from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import CancelledError, Future
from pathlib import Path
from threading import RLock
from typing import Protocol

from musicplayer.core.library import MusicManager

from .library_service import LibraryService
from .models import AppSettings
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
        self, function: Callable[..., bytes], /, *args: object
    ) -> Future[bytes]: ...


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
        on_change: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.manager = manager
        self.library = library
        self.store = store
        self.backend = backend
        self.io_executor = io_executor
        self.on_change = on_change
        self.on_error = on_error
        settings = store.settings
        restored = store.get("playback", {}) if settings.resume_session else {}
        persisted = restored if isinstance(restored, dict) else {}
        self.queue = PlaybackQueue.from_dict(persisted)
        original_items = list(self.queue.items)
        original_index = self.queue.current_index
        available = [self.manager.has_track(item) for item in original_items]
        self.queue.items = [
            item
            for item, is_available in zip(original_items, available)
            if is_available
        ]
        current_was_available = (
            0 <= original_index < len(available) and available[original_index]
        )
        if not self.queue.items:
            self.queue.current_index = -1
        elif current_was_available:
            self.queue.current_index = sum(available[:original_index])
        else:
            self.queue.current_index = min(
                sum(available[: max(0, original_index)]), len(self.queue.items) - 1
            )
        self.position_ms = (
            int(persisted.get("position_ms", 0)) if current_was_available else 0
        )
        self.volume = settings.volume
        self.muted = settings.muted
        self.previous_volume = settings.previous_volume
        self.playing = False
        self.duration_ms = 0
        self.external_title = ""
        self.external_uploader = ""
        self.external_thumbnail = ""
        self._source_loaded = False
        self._load_lock = RLock()
        self._load_generation = 0
        self._load_future: Future[bytes] | None = None
        self._loading = False
        self._load_autoplay = False
        self._load_position = 0
        self._last_persisted_position = 0.0
        self.backend.set_volume(0.0 if self.muted else self.volume / 100)
        if self.queue.items != original_items:
            self._persist()

    @property
    def current_track_id(self) -> str | None:
        return self.queue.current

    def play_tracks(self, track_ids: list[str], *, start_index: int = 0) -> None:
        requested = [str(item) for item in track_ids]
        selected_index = (
            max(0, min(start_index, len(requested) - 1)) if requested else -1
        )
        selected = requested[selected_index] if selected_index >= 0 else None
        available = [item for item in requested if self.manager.has_track(item)]
        if not available:
            self._error("There are no available tracks to play.")
            return
        available_index = (
            available.index(selected)
            if selected in available
            else min(max(0, selected_index), len(available) - 1)
        )
        self.queue.replace(available, start_index=available_index)
        self._load_current(autoplay=True)

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
        self.external_title = title
        self.external_uploader = uploader
        self.external_thumbnail = thumbnail
        self.backend.load(source)
        self._source_loaded = True
        self.backend.play()
        self.position_ms = 0
        self.duration_ms = 0
        self.playing = True
        self._notify()

    def play_track(self, track_id: str) -> None:
        track_id = str(track_id)
        if track_id in self.queue.items:
            self.queue.current_index = self.queue.items.index(track_id)
        else:
            self.queue.replace([track_id])
        self._load_current(autoplay=True)

    def toggle(self) -> None:
        if not self.current_track_id and not self.external_title:
            tracks = self.manager.list_tracks()
            if tracks:
                self.play_tracks([str(item.id) for item in tracks])
            else:
                self._error("Your library is empty. Download a track first.")
            return
        if self._loading:
            # Loading is already off the UI thread. A toggle changes only the
            # desired state applied when those bytes become available.
            self._load_autoplay = not self.playing
            self.playing = self._load_autoplay
            self._persist()
            self._notify()
            return
        if self.playing:
            self.backend.pause()
            self.playing = False
        else:
            if not self._source_loaded:
                saved_position = self.position_ms
                if not self._load_current(autoplay=True, position_ms=saved_position):
                    return
                if self._loading:
                    return
            else:
                self.backend.resume()
            self.playing = True
        self._persist()
        self._notify()

    def next(self, *, automatic: bool = False) -> None:
        was_external = bool(self.external_title)
        source_was_loaded = self._source_loaded
        was_playing = self.playing
        paused_for_background_load = False
        if self.io_executor is not None and source_was_loaded and was_playing:
            self.backend.pause()
            paused_for_background_load = True
        self._clear_external()
        if self.queue.next(automatic=automatic):
            self._load_current(autoplay=True)
        else:
            if source_was_loaded:
                if was_playing and not paused_for_background_load:
                    self.backend.pause()
                self.backend.seek(0)
            self._source_loaded = source_was_loaded and not was_external
            self.playing = False
            self.position_ms = 0
            if was_external:
                self.duration_ms = 0
            self._persist()
            self._notify()

    def previous(self) -> None:
        if self.external_title:
            self.seek(0)
            return
        if self.position_ms > 5_000:
            self.seek(0)
        elif self.queue.previous():
            self._load_current(autoplay=True)

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

    def add_next(self, track_id: str) -> None:
        self.queue.add_next(track_id)
        self._persist()
        self._notify()

    def add_last(self, track_id: str) -> None:
        self.queue.add_last(track_id)
        self._persist()
        self._notify()

    def remove_queue_item(self, index: int) -> None:
        was_current = index == self.queue.current_index
        self.queue.remove_at(index)
        if was_current and not self.external_title:
            if self.queue.current:
                self._load_current(autoplay=self.playing)
            else:
                self._cancel_pending_load()
                if self._source_loaded:
                    self.backend.pause()
                self._source_loaded = False
                self.playing = False
                self.position_ms = 0
                self.duration_ms = 0
        self._persist()
        self._notify()

    def move_queue_item(self, index: int, offset: int) -> None:
        target = index + offset
        if 0 <= target < len(self.queue.items):
            self.queue.move(index, target)
            self._persist()
            self._notify()

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
        self._persist()
        self._notify()

    def clear_queue(self) -> None:
        self.queue.clear(keep_current=True)
        self._persist()
        self._notify()

    def clear_library_state(self) -> None:
        """Drop queued library tracks and stop a loaded library source."""
        library_source_active = bool(
            self.current_track_id or (self._source_loaded and not self.external_title)
        )
        if library_source_active:
            self._cancel_pending_load()
            if self._source_loaded or self.playing:
                self.backend.pause()
            self._source_loaded = False
            self.playing = False
            self.position_ms = 0
            self.duration_ms = 0
        self.queue.clear()
        self._persist()
        self._notify()

    def toggle_shuffle(self) -> bool:
        self.queue.shuffle = not self.queue.shuffle
        self._persist()
        self._notify()
        return self.queue.shuffle

    def cycle_repeat(self):
        mode = self.queue.cycle_repeat()
        self._persist()
        self._notify()
        return mode

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

    def adjust_volume(self, delta: int) -> None:
        self.set_volume(self.volume + delta)

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

    def apply_settings(self, settings: AppSettings) -> None:
        """Apply live playback settings without changing queue/session state."""
        self.volume = settings.volume
        self.previous_volume = settings.previous_volume
        self.muted = settings.muted
        self.backend.set_volume(0.0 if self.muted else self.volume / 100)
        self._notify("volume")

    def on_position(self, position_ms: int) -> None:
        self.position_ms = max(0, int(position_ms))
        now = time.monotonic()
        if now - self._last_persisted_position >= 5:
            self._persist()
            self._last_persisted_position = now
        self._notify("progress")

    def on_duration(self, duration_ms: int) -> None:
        self.duration_ms = max(0, int(duration_ms))
        self._notify("progress")

    def on_playing(self, playing: bool) -> None:
        self.playing = playing
        self._notify()

    def on_completed(self) -> None:
        self.next(automatic=True)

    def _load_current(self, *, autoplay: bool, position_ms: int = 0) -> bool:
        track_id = self.current_track_id
        if not track_id:
            return False
        if self.io_executor is not None and self._source_loaded:
            # Stop the previous service before its replacement is read; the
            # potentially large read itself remains off the UI thread.
            self.backend.pause()
        self._clear_external()
        try:
            path = self.manager.track_path(track_id)
            if not path.is_file():
                raise FileNotFoundError(path)
        except (OSError, KeyError, ValueError) as error:
            self.playing = False
            self._error(f"This track is unavailable: {error}")
            self._persist()
            self._notify()
            return False

        position = max(0, int(position_ms))
        if self.io_executor is not None:
            return self._load_current_in_background(
                path, track_id, autoplay=autoplay, position_ms=position
            )

        try:
            self._activate_source(
                _audio_source(path),
                track_id,
                autoplay=autoplay,
                position_ms=position,
            )
        except (OSError, ValueError, RuntimeError) as error:
            self.playing = False
            self._error(f"This track is unavailable: {error}")
            self._persist()
            loaded = False
        else:
            loaded = True
        self._notify()
        return loaded

    def _load_current_in_background(
        self,
        path: Path,
        track_id: str,
        *,
        autoplay: bool,
        position_ms: int,
    ) -> bool:
        """Read managed audio off the UI thread with stale-load suppression."""
        with self._load_lock:
            self._load_generation += 1
            generation = self._load_generation
            self.position_ms = position_ms
            self.duration_ms = 0
            self.playing = autoplay
            self._load_autoplay = autoplay
            self._load_position = position_ms
            self._loading = True
            try:
                assert self.io_executor is not None
                future = self.io_executor.submit(_audio_source, path)
            except RuntimeError as error:
                self._loading = False
                self.playing = False
                self._error(str(error))
                self._persist()
                self._notify()
                return False
            self._load_future = future
            future.add_done_callback(
                lambda completed: self._background_source_ready(
                    completed, generation, track_id
                )
            )
        self._notify()
        return True

    def _background_source_ready(
        self,
        future: Future[bytes],
        generation: int,
        track_id: str,
    ) -> None:
        with self._load_lock:
            if generation != self._load_generation or not self._loading:
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
            except (OSError, ValueError, RuntimeError) as error:
                self.playing = False
                self._error(f"This track is unavailable: {error}")
                self._persist()
        self._notify()

    def _activate_source(
        self,
        source: bytes,
        track_id: str,
        *,
        autoplay: bool,
        position_ms: int,
    ) -> None:
        self.backend.load(source)
        self._source_loaded = True
        self.position_ms = position_ms
        self.duration_ms = 0
        self.playing = autoplay
        if autoplay:
            self.backend.play(position_ms)
        self.library.record_play(
            track_id,
            playback=self.queue.to_dict(position_ms=position_ms),
        )

    def _save_volume(self) -> None:
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
        self._source_loaded = False

    def _cancel_pending_load(self) -> None:
        with self._load_lock:
            self._load_generation += 1
            future = self._load_future
            self._load_future = None
            self._loading = False
        if future is not None:
            future.cancel()

    def _persist(self) -> None:
        self.store.set("playback", self.queue.to_dict(position_ms=self.position_ms))

    def _notify(self, change: str = "state") -> None:
        """Publish a scoped UI invalidation instead of forcing a full redraw."""
        if self.on_change:
            self.on_change(change)

    def _error(self, message: str) -> None:
        if self.on_error:
            self.on_error(message)


def _audio_source(path: Path) -> bytes:
    """Use the audio service's documented raw-byte source for managed files.

    A ``file://`` URI is interpreted as a URL by the Windows audioplayers
    plugin and can leave its method channel waiting indefinitely. Raw bytes are
    portable and also work for filenames containing spaces or non-ASCII text.
    """
    return path.read_bytes()
