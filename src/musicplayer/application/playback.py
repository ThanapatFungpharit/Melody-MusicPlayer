from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Protocol

from musicplayer.core.library import MusicManager

from .library_service import LibraryService
from .models import AppSettings
from .playback_persistence import PlaybackPersistence
from .queue import PlaybackQueue
from .store import ApplicationStore


class AudioBackend(Protocol):
    def load(self, source: str | bytes) -> None: ...
    def play(self, position_ms: int = 0) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def seek(self, position_ms: int) -> None: ...
    def set_volume(self, value: float) -> None: ...


class BackgroundExecutor(Protocol):
    def submit(
        self, function: Callable[..., str | bytes], /, *args: object
    ) -> Future[str | bytes]: ...


def _serialized[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    @wraps(method)
    def invoke(*args: P.args, **kwargs: P.kwargs) -> R:
        controller = args[0]
        assert isinstance(controller, PlaybackController)
        with controller._load_lock:
            return method(*args, **kwargs)

    return invoke


@dataclass(frozen=True)
class PlaybackSnapshot:
    current_track_id: str | None
    queue: tuple[str, ...]
    playing: bool
    loading: bool
    position_ms: int
    duration_ms: int
    generation: int


class PlaybackController:
    """Coordinates persistent queue semantics with a platform audio backend."""

    def __init__(
        self,
        manager: MusicManager,
        library: LibraryService,
        store: ApplicationStore,
        backend: AudioBackend,
        *,
        io_executor: BackgroundExecutor | None = None,
        persistence: PlaybackPersistence | None = None,
        dispatch: Callable[..., None] | None = None,
        available_track_ids: set[str] | None = None,
        on_change: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.manager = manager
        self.library = library
        self.store = store
        self.backend = backend
        self.io_executor = io_executor
        self.persistence = persistence
        self.dispatch = dispatch
        self.on_change = on_change
        self.on_error = on_error
        settings = store.settings
        restored = store.get("playback", {}) if settings.resume_session else {}
        persisted = restored if isinstance(restored, dict) else {}
        self.queue = PlaybackQueue.from_dict(persisted)
        original_items = list(self.queue.items)
        original_index = self.queue.current_index

        available_items: list[str] = []
        items_before_original = 0
        current_was_available = False

        for index, item in enumerate(original_items):
            is_available = (
                item in available_track_ids
                if available_track_ids is not None
                else self.manager.has_track(item)
                and self.manager.track_path(item).is_file()
            )
            if is_available:
                available_items.append(item)
                if index < original_index:
                    items_before_original += 1
            if index == original_index:
                current_was_available = is_available

        self.queue.items = available_items

        if not self.queue.items:
            self.queue.current_index = -1
        elif current_was_available:
            self.queue.current_index = items_before_original
        else:
            self.queue.current_index = min(
                items_before_original, len(self.queue.items) - 1
            )
        self.position_ms = (
            int(persisted.get("position_ms", 0)) if current_was_available else 0
        )
        self.volume = settings.volume
        self.muted = settings.muted
        self.previous_volume = settings.previous_volume
        self._playing = False
        self.duration_ms = 0
        self.external_title = ""
        self.external_uploader = ""
        self.external_thumbnail = ""
        self._source_loaded = False
        self._load_lock = RLock()
        self._load_generation = 0
        self._load_future: Future[str | bytes] | None = None
        self._preload_generation = 0
        self._preload_track_id: str | None = None
        self._preload_future: Future[str | bytes] | None = None
        self._preloaded_source: str | bytes | None = None
        self._closed = False
        self._native_loading = False
        self._loading = False
        self._load_autoplay = False
        self._load_position = 0
        self._last_persisted_position = 0.0
        self._last_backend_error_at: float | None = None
        self._pending_play_record_id: str | None = None
        self._loaded_track_id: str | None = None
        self._media_session_active = False
        self.backend.set_volume(0.0 if self.muted else self.volume / 100)
        if self.queue.items != original_items:
            self._persist()

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def snapshot(self) -> PlaybackSnapshot:
        with self._load_lock:
            return PlaybackSnapshot(
                self.current_track_id,
                tuple(self.queue.items),
                self._playing,
                self._loading or self._native_loading,
                self.position_ms,
                self.duration_ms,
                self._load_generation,
            )

    @property
    def current_track_id(self) -> str | None:
        return self.queue.current

    @property
    def media_session_active(self) -> bool:
        return self._media_session_active

    @_serialized
    def play_tracks(
        self,
        track_ids: Sequence[str],
        *,
        start_index: int = 0,
        shuffle: bool | None = None,
    ) -> None:
        selected_index = (
            max(0, min(start_index, len(track_ids) - 1)) if track_ids else -1
        )
        selected = str(track_ids[selected_index]) if selected_index >= 0 else None
        available: list[str] = []
        available_index: int | None = None
        for item in track_ids:
            normalized = str(item)
            if not self.manager.has_track(normalized):
                continue
            if available_index is None and normalized == selected:
                available_index = len(available)
            available.append(normalized)

        if not available:
            self._error("There are no available tracks to play.")
            return
        if available_index is None:
            available_index = min(max(0, selected_index), len(available) - 1)
        self.queue.replace(
            available,
            start_index=available_index,
            shuffle=shuffle,
        )
        self._cancel_preload()
        self._load_current(autoplay=True)

    @_serialized
    def play_stream(
        self,
        source: str,
        *,
        title: str,
        uploader: str = "",
        thumbnail: str = "",
    ) -> None:
        """Play an ephemeral search result without adding it to the library."""
        self._cancel_pending_load()
        self._cancel_preload()
        self.external_title = title
        self.external_uploader = uploader
        self.external_thumbnail = thumbnail
        self._source_loaded = True
        self._loaded_track_id = None
        self._native_loading = True
        self.position_ms = 0
        self.duration_ms = 0
        self._playing = True
        self._media_session_active = True
        try:
            self.backend.load(source)
            if self._source_loaded:
                self.backend.play()
        except (OSError, ValueError, RuntimeError) as error:
            self.on_backend_error(str(error))
        self._notify()

    @_serialized
    def play_track(self, track_id: str) -> None:
        track_id = str(track_id)
        if track_id == self.current_track_id and self._loading:
            return
        try:
            self.queue.select(self.queue.items.index(track_id))
        except ValueError:
            self.queue.replace([track_id])
        self._cancel_preload(keep_track_id=track_id)
        self._load_current(autoplay=True)

    @_serialized
    def toggle(self) -> None:
        if not self.current_track_id and not self.external_title:
            tracks = self.manager.list_tracks()
            if tracks:
                self.queue.replace([str(item.id) for item in tracks])
                self._load_current(autoplay=True)
            else:
                self._error("Your library is empty. Download a track first.")
            return
        if self._loading:
            # Loading is already off the UI thread. A toggle changes only the
            # desired state applied when those bytes become available.
            self._load_autoplay = not self._playing
            self._playing = self._load_autoplay
            if self._source_loaded:
                if self._playing:
                    self.backend.resume()
                else:
                    self.backend.pause()
            if self._playing:
                self._media_session_active = True
            self._persist()
            self._notify()
            return
        if self._native_loading:
            self._playing = not self._playing
            if self._playing:
                self._media_session_active = True
                self.backend.resume()
            else:
                self.backend.pause()
            self._notify()
            return
        if self._playing:
            self.backend.pause()
            self._playing = False
        else:
            if not self._source_loaded:
                saved_position = self.position_ms
                if not self._load_current(autoplay=True, position_ms=saved_position):
                    return
                if self._loading:
                    return
            else:
                self.backend.resume()
            self._playing = True
            self._media_session_active = True
        self._persist()
        self._notify()

    @_serialized
    def next(self, *, automatic: bool = False) -> None:
        was_external = bool(self.external_title)
        source_was_loaded = self._source_loaded
        was_playing = self._playing
        self._clear_external()
        if self.queue.next(automatic=automatic):
            self._load_current(autoplay=True)
        else:
            self._cancel_preload()
            if source_was_loaded:
                if was_playing:
                    self.backend.pause()
                self.backend.seek(0)
            self._source_loaded = source_was_loaded and not was_external
            self._playing = False
            self.position_ms = 0
            if was_external:
                self.duration_ms = 0
            self._persist()
            self._notify("track")

    @_serialized
    def previous(self) -> None:
        if self.external_title:
            self.seek(0)
            return
        if self.position_ms > 5_000:
            self.seek(0)
        elif self.queue.previous():
            self._cancel_preload(keep_track_id=self.current_track_id)
            self._load_current(autoplay=True)

    @_serialized
    def stop(self) -> None:
        """Terminate playback while preserving a library queue selection.

        Pause deliberately keeps the media session visible. Stop is the
        explicit destructive transport boundary that releases the source and
        removes system controls.
        """
        self._cancel_pending_load(discard_source=True)
        self._cancel_preload()
        if self._source_loaded or self._playing:
            self.backend.pause()
            self.backend.seek(0)
        self._source_loaded = False
        self._loaded_track_id = None
        self._playing = False
        self._native_loading = False
        self._media_session_active = False
        self.position_ms = 0
        self.duration_ms = 0
        self._pending_play_record_id = None
        if self.external_title:
            self.external_title = ""
            self.external_uploader = ""
            self.external_thumbnail = ""
        self._persist()
        self._notify("state")

    @_serialized
    def seek(self, position_ms: int) -> None:
        self.position_ms = max(
            0, min(int(position_ms), self.duration_ms or int(position_ms))
        )
        if self._loading:
            self._load_position = self.position_ms
            self._persist()
            self._notify()
            return
        self.backend.seek(self.position_ms)
        self._persist()
        self._notify()

    @_serialized
    def add_next(self, track_id: str) -> None:
        self.queue.add_next(track_id)
        self._refresh_preload_prediction()
        self._persist()
        self._notify("queue")

    @_serialized
    def add_last(self, track_id: str) -> None:
        self.queue.add_last(track_id)
        self._refresh_preload_prediction()
        self._persist()
        self._notify("queue")

    @_serialized
    def add_next_many(self, track_ids: Iterable[str]) -> int:
        """Insert several tracks next with one persistence/UI update cycle."""
        items = [str(track_id) for track_id in track_ids]
        for track_id in reversed(items):
            self.queue.add_next(track_id)
        if items:
            self._refresh_preload_prediction()
            self._persist()
            self._notify("queue")
        return len(items)

    @_serialized
    def add_last_many(self, track_ids: Iterable[str]) -> int:
        """Append several tracks with one persistence/UI update cycle."""
        items = [str(track_id) for track_id in track_ids]
        for track_id in items:
            self.queue.add_last(track_id)
        if items:
            self._refresh_preload_prediction()
            self._persist()
            self._notify("queue")
        return len(items)

    @_serialized
    def remove_queue_item(self, index: int) -> None:
        was_current = index == self.queue.current_index
        self.queue.remove_at(index)
        self._cancel_preload()
        if was_current and not self.external_title:
            if self.queue.current:
                self._load_current(autoplay=self._playing)
            else:
                self._cancel_pending_load()
                if self._source_loaded:
                    self.backend.pause()
                self._source_loaded = False
                self._loaded_track_id = None
                self._playing = False
                self.position_ms = 0
                self.duration_ms = 0
        else:
            self._schedule_preload()
        self._persist()
        self._notify("queue")

    @_serialized
    def move_queue_item(self, index: int, offset: int) -> None:
        target = index + offset
        if 0 <= target < len(self.queue.items):
            self.queue.move(index, target)
            self._refresh_preload_prediction()
            self._persist()
            self._notify("queue")

    @_serialized
    def reorder_queue(self, old_index: int | None, new_index: int | None) -> None:
        """Apply a direct drag-and-drop reorder and persist it immediately."""
        if old_index is None or new_index is None:
            return
        if not (0 <= old_index < len(self.queue.items)):
            return
        if new_index > old_index:
            new_index -= 1
        target = max(0, min(new_index, len(self.queue.items) - 1))
        self.queue.move(old_index, target)
        self._refresh_preload_prediction()
        self._persist()
        self._notify("queue")

    @_serialized
    def clear_queue(self) -> None:
        self.queue.clear(keep_current=True)
        self._refresh_preload_prediction()
        self._persist()
        self._notify("queue")

    @_serialized
    def clear_library_state(self) -> None:
        """Drop queued library tracks and stop a loaded library source."""
        library_source_active = bool(
            self.current_track_id or (self._source_loaded and not self.external_title)
        )
        if library_source_active:
            self._cancel_pending_load()
            if self._source_loaded or self._playing:
                self.backend.pause()
            self._source_loaded = False
            self._loaded_track_id = None
            self._playing = False
            self.position_ms = 0
            self.duration_ms = 0
        self.queue.clear()
        self._cancel_preload()
        self._persist()
        self._notify("queue")

    @_serialized
    def toggle_shuffle(self) -> bool:
        self.queue.set_shuffle(not self.queue.shuffle)
        self._refresh_preload_prediction()
        self._persist()
        self._notify("queue")
        return self.queue.shuffle

    @_serialized
    def cycle_repeat(self):
        mode = self.queue.cycle_repeat()
        self._refresh_preload_prediction()
        self._persist()
        self._notify("queue")
        return mode

    @_serialized
    def set_volume(self, value: int, *, persist: bool = True) -> None:
        """Apply volume immediately and optionally persist the settled value.

        Slider previews pass ``persist=False`` so dragging remains responsive;
        the final pointer-up event persists the selected value atomically.
        """
        value = max(0, min(100, int(value)))
        self.volume = value
        if value > 0:
            self.previous_volume = value
            self.muted = False
        else:
            self.muted = True
        self.backend.set_volume(0.0 if self.muted else value / 100)
        if persist:
            self._save_volume()
        self._notify("volume")

    @_serialized
    def adjust_volume(self, delta: int) -> None:
        self.set_volume(self.volume + delta)

    @_serialized
    def toggle_mute(self) -> None:
        if self.muted:
            self.muted = False
            self.volume = max(1, self.previous_volume)
        else:
            if self.volume > 0:
                self.previous_volume = self.volume
            self.muted = True
        self.backend.set_volume(0.0 if self.muted else self.volume / 100)
        self._save_volume()
        self._notify("volume")

    @_serialized
    def apply_settings(self, settings: AppSettings) -> None:
        """Apply live playback settings without changing queue/session state."""
        self.volume = settings.volume
        self.previous_volume = settings.previous_volume
        self.muted = settings.muted
        self.backend.set_volume(0.0 if self.muted else self.volume / 100)
        self._notify("volume")

    @_serialized
    def on_position(self, position_ms: int) -> None:
        if self._loading or self._native_loading:
            return
        self.position_ms = max(0, int(position_ms))
        now = time.monotonic()
        if now - self._last_persisted_position >= 5:
            self._persist()
            self._last_persisted_position = now
        self._notify("progress")

    @_serialized
    def on_duration(self, duration_ms: int) -> None:
        if self._loading or self._native_loading:
            return
        self.duration_ms = max(0, int(duration_ms))
        self._notify("progress")

    @_serialized
    def on_playing(self, playing: bool) -> None:
        if self._loading or self._native_loading or not self._source_loaded:
            return
        self._playing = playing
        if playing:
            self._media_session_active = True
        track_id = self.current_track_id
        if (
            playing
            and track_id is not None
            and track_id == self._pending_play_record_id
        ):
            self._pending_play_record_id = None
            if self.persistence is not None:
                self.persistence.record_play(track_id)
            else:
                self.library.record_play(
                    track_id,
                    playback=self.queue.to_dict(position_ms=self.position_ms),
                )
        self._notify()

    @_serialized
    def on_completed(self) -> None:
        if self._loading or self._native_loading or not self._source_loaded:
            return
        self.next(automatic=True)

    @_serialized
    def on_backend_error(self, message: str) -> None:
        """Reconcile optimistic controller state after a native audio failure."""
        # A failed native operation can happen while a managed source is still
        # being read. Invalidate that worker before repairing controller state,
        # otherwise its late completion can resurrect a track the backend has
        # already rejected.
        self._cancel_pending_load(discard_source=True)
        self._cancel_preload()
        self._playing = False
        self._source_loaded = False
        self._loaded_track_id = None
        self._loading = False
        self._pending_play_record_id = None
        self._persist()
        self._notify()
        now = time.monotonic()
        if (
            self._last_backend_error_at is None
            or now - self._last_backend_error_at >= 5
        ):
            self._last_backend_error_at = now
            self._error(message)

    def _load_current(self, *, autoplay: bool, position_ms: int = 0) -> bool:
        track_id = self.current_track_id
        if not track_id:
            return False
        # Keep the current native source playing while the replacement is
        # validated or read. The backend swaps only after the new source is
        # ready, avoiding a silent gap and service teardown on the command path.
        self._clear_external()
        if autoplay:
            self._media_session_active = True
        position = max(0, int(position_ms))
        if self.io_executor is not None:
            preloaded = self._take_preloaded_source(track_id)
            if preloaded is not None:
                try:
                    self._activate_source(
                        preloaded,
                        track_id,
                        autoplay=autoplay,
                        position_ms=position,
                    )
                except (OSError, ValueError, RuntimeError) as error:
                    self._playing = False
                    self._source_loaded = False
                    self._loaded_track_id = None
                    self._error(f"This track is unavailable: {error}")
                    self._persist()
                    self._notify("track")
                    return False
                self._notify("track")
                return True
            return self._load_current_in_background(
                track_id, autoplay=autoplay, position_ms=position
            )
        try:
            path = self.manager.track_path(track_id)
            if not path.is_file():
                raise FileNotFoundError(path)
        except (OSError, KeyError, ValueError) as error:
            self._playing = False
            self._error(f"This track is unavailable: {error}")
            self._persist()
            self._notify()
            return False

        try:
            source = (
                _audio_source(path)
                if getattr(self.backend, "supports_file_sources", False)
                else _audio_bytes(path)
            )
            self._activate_source(
                source,
                track_id,
                autoplay=autoplay,
                position_ms=position,
            )
        except (OSError, ValueError, RuntimeError) as error:
            self._playing = False
            self._error(f"This track is unavailable: {error}")
            self._persist()
            loaded = False
        else:
            loaded = True
        self._notify("track")
        return loaded

    def _load_current_in_background(
        self,
        track_id: str,
        *,
        autoplay: bool,
        position_ms: int,
    ) -> bool:
        """Read managed audio off the UI thread with stale-load suppression."""
        with self._load_lock:
            self._cancel_pending_load()
            self._load_generation += 1
            generation = self._load_generation
            self.position_ms = position_ms
            self.duration_ms = 0
            self._playing = autoplay
            self._load_autoplay = autoplay
            self._load_position = position_ms
            self._loading = True
            try:
                assert self.io_executor is not None
                future = self._take_preload_future(track_id)
                if future is None:
                    future = self.io_executor.submit(self._prepare_track, track_id)
            except RuntimeError as error:
                self._loading = False
                self._playing = False
                self._error(str(error))
                self._persist()
                self._notify()
                return False
            self._load_future = future
            future.add_done_callback(
                lambda completed: self._source_ready_callback(
                    completed, generation, track_id
                )
            )
        self._notify("track")
        return True

    def _prepare_track(self, track_id: str) -> str | bytes:
        path = self.manager.track_path(track_id)
        if getattr(self.backend, "supports_file_sources", False):
            return _audio_source(path)
        return _audio_bytes(path)

    def _source_ready_callback(
        self, future: Future[str | bytes], generation: int, track_id: str
    ) -> None:
        if self.dispatch is not None:
            self.dispatch(self._background_source_ready, future, generation, track_id)
        else:
            self._background_source_ready(future, generation, track_id)

    def _background_source_ready(
        self,
        future: Future[str | bytes],
        generation: int,
        track_id: str,
    ) -> None:
        with self._load_lock:
            if self._closed or generation != self._load_generation or not self._loading:
                return
            self._load_future = None
            self._loading = False
            try:
                source = future.result()
                self._activate_source(
                    source,
                    track_id,
                    autoplay=self._load_autoplay,
                    position_ms=self._load_position,
                )
            except CancelledError:
                return
            except (OSError, KeyError, ValueError, RuntimeError) as error:
                if self._source_loaded:
                    self.backend.pause()
                discard = getattr(self.backend, "discard_source", None)
                if discard is not None:
                    discard()
                self._playing = False
                self._source_loaded = False
                self._loaded_track_id = None
                self._error(f"This track is unavailable: {error}")
                self._persist()
        self._notify("loading")

    def _activate_source(
        self,
        source: str | bytes,
        track_id: str,
        *,
        autoplay: bool,
        position_ms: int,
    ) -> None:
        self._source_loaded = True
        self._loaded_track_id = track_id
        self._native_loading = True
        self.position_ms = position_ms
        self.duration_ms = 0
        self._playing = autoplay
        if autoplay:
            self._media_session_active = True
        self.backend.load(source)
        if not self._source_loaded:
            return
        if autoplay:
            self._pending_play_record_id = track_id
            self.backend.play(position_ms)
        else:
            self._pending_play_record_id = None
        self._persist()

    def _save_volume(self) -> None:
        if self.persistence is not None:
            self.persistence.save(
                volume={
                    "volume": self.volume,
                    "previous_volume": self.previous_volume,
                    "muted": self.muted,
                }
            )
            return
        settings = self.store.settings
        settings.volume = self.volume
        settings.previous_volume = self.previous_volume
        settings.muted = self.muted
        self.store.save_settings(settings)

    def _clear_external(self) -> None:
        self._cancel_pending_load()
        self.external_title = ""
        self.external_uploader = ""
        self.external_thumbnail = ""

    def _cancel_pending_load(self, *, discard_source: bool = False) -> None:
        with self._load_lock:
            self._load_generation += 1
            future = self._load_future
            native_was_loading = self._native_loading
            self._load_future = None
            self._loading = False
            self._native_loading = False
            self._pending_play_record_id = None
        if future is not None:
            future.cancel()
        discard = getattr(self.backend, "discard_source", None)
        if discard_source and discard is not None and not self._closed:
            discard()
        elif native_was_loading and not self._closed:
            cancel_native = getattr(self.backend, "cancel_load", None)
            if cancel_native is not None:
                cancel_native()

    def _take_preloaded_source(self, track_id: str) -> str | bytes | None:
        with self._load_lock:
            if self._preload_track_id != track_id or self._preloaded_source is None:
                self._cancel_preload(keep_track_id=track_id)
                return None
            source = self._preloaded_source
            self._preload_generation += 1
            self._preload_track_id = None
            self._preload_future = None
            self._preloaded_source = None
            return source

    def _take_preload_future(self, track_id: str) -> Future[str | bytes] | None:
        with self._load_lock:
            if self._preload_track_id != track_id or self._preload_future is None:
                return None
            future = self._preload_future
            self._preload_generation += 1
            self._preload_track_id = None
            self._preload_future = None
            self._preloaded_source = None
            return future

    def _schedule_preload(self) -> None:
        if (
            self._closed
            or self.io_executor is None
            or not self._source_loaded
            or self._native_loading
        ):
            return
        track_id = self.queue.peek_next(automatic=True)
        if not track_id or track_id == self._loaded_track_id:
            self._cancel_preload()
            return
        if self._preload_track_id == track_id and (
            self._preload_future is not None or self._preloaded_source is not None
        ):
            return
        self._cancel_preload()
        self._preload_generation += 1
        generation = self._preload_generation
        self._preload_track_id = track_id
        try:
            future = self.io_executor.submit(self._prepare_track, track_id)
        except RuntimeError:
            self._preload_track_id = None
            return
        self._preload_future = future
        future.add_done_callback(
            lambda completed: self._preload_ready_callback(
                completed, generation, track_id
            )
        )

    def _preload_ready_callback(
        self, future: Future[str | bytes], generation: int, track_id: str
    ) -> None:
        if self.dispatch is not None:
            self.dispatch(self._background_preload_ready, future, generation, track_id)
        else:
            self._background_preload_ready(future, generation, track_id)

    def _background_preload_ready(
        self, future: Future[str | bytes], generation: int, track_id: str
    ) -> None:
        with self._load_lock:
            if (
                self._closed
                or generation != self._preload_generation
                or track_id != self._preload_track_id
                or self.queue.peek_next(automatic=True) != track_id
            ):
                return
            self._preload_future = None
            try:
                source = future.result()
            except (CancelledError, OSError, KeyError, ValueError, RuntimeError):
                self._preload_track_id = None
                return
            self._preloaded_source = source
        preload = getattr(self.backend, "preload", None)
        if preload is not None:
            try:
                preload(source)
            except (OSError, ValueError, RuntimeError):
                # Python preparation is still reusable even if a platform does
                # not have enough native resources for speculative decoding.
                pass

    def _refresh_preload_prediction(self) -> None:
        self._cancel_preload()
        self._schedule_preload()

    def _cancel_preload(self, *, keep_track_id: str | None = None) -> None:
        with self._load_lock:
            if keep_track_id is not None and self._preload_track_id == keep_track_id:
                return
            had_preload = (
                self._preload_track_id is not None
                or self._preload_future is not None
                or self._preloaded_source is not None
            )
            self._preload_generation += 1
            future = self._preload_future
            self._preload_future = None
            self._preload_track_id = None
            self._preloaded_source = None
        if future is not None:
            future.cancel()
        cancel = getattr(self.backend, "cancel_preload", None)
        if had_preload and cancel is not None and not self._closed:
            try:
                cancel()
            except (OSError, ValueError, RuntimeError):
                pass

    def _persist(self) -> None:
        state = self.queue.to_dict(position_ms=self.position_ms)
        if self.persistence is not None:
            self.persistence.save(playback=state)
        else:
            self.store.set("playback", state)

    @_serialized
    def on_loaded(self) -> None:
        self._native_loading = False
        self._schedule_preload()
        self._notify("loading")

    @_serialized
    def close(self) -> None:
        self._persist()
        self._closed = True
        self._cancel_pending_load()
        self._cancel_preload()

    @_serialized
    def begin_pending_track(self, title: str) -> int:
        """Reserve playback intent before resolving or downloading a selection."""
        self._cancel_pending_load()
        self._cancel_preload()
        self.backend.pause()
        self._source_loaded = False
        self._loaded_track_id = None
        self.external_title = title
        self.external_uploader = ""
        self.external_thumbnail = ""
        self._loading = True
        self._playing = self._load_autoplay = True
        self._media_session_active = True
        self.position_ms = self.duration_ms = 0
        self._notify()
        return self._load_generation

    @_serialized
    def is_current_request(self, generation: int) -> bool:
        return not self._closed and generation == self._load_generation

    def _notify(self, change: str = "state") -> None:
        """Publish a scoped UI invalidation instead of forcing a full redraw."""
        if self.on_change:
            self.on_change(change)

    def _error(self, message: str) -> None:
        if self.on_error:
            self.on_error(message)


def _audio_source(path: Path) -> str:
    """Validate on a worker; the native decoder reads the file itself.

    Never send a whole song through the UI protocol. Use a native path, not a
    file URI (which Flet interprets as a network URL on Windows).
    """
    with path.open("rb") as source:
        if not source.read(1):
            raise ValueError("Audio file is empty")
    return str(path)


def _audio_bytes(path: Path) -> bytes:
    source = path.read_bytes()
    if not source:
        raise ValueError("Audio file is empty")
    return source
