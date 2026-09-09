from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, cast

import flet as ft
import flet_audio as fa
from flet_background_audio import BackgroundAudioSession, MediaSessionActionEvent

logger = logging.getLogger(__name__)
AudioSource = str | bytes


@dataclass
class ManagedAudioErrorEvent(ft.Event["ManagedAudio"]):
    message: str


@ft.control("ManagedAudio")
class ManagedAudio(fa.Audio):
    on_error: ft.EventHandler[ManagedAudioErrorEvent] | None = None


class FletAudioBackend:
    """Lifecycle-safe adapter around Flet's asynchronous audio service.

    A new service is mounted for every source. Playback and seeking are held
    until the native client confirms ``on_loaded``; RPCs then run serially on
    the page loop. This avoids racing a source update with ``play()`` and keeps
    Flet's 30-second RPC failures from escaping its Future callback.
    """

    _OPERATION_TIMEOUT_SECONDS = 12
    supports_file_sources = True

    def __init__(self, page: ft.Page, *, use_device_volume: bool = False) -> None:
        self.page = page
        self._use_device_volume = use_device_volume
        self.audio: fa.Audio | None = None
        self.on_position: Callable[[int], None] | None = None
        self.on_loaded: Callable[[], None] | None = None
        self.on_duration: Callable[[int], None] | None = None
        self.on_playing: Callable[[bool], None] | None = None
        self.on_completed: Callable[[], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.on_media_action: Callable[[str, int | None], None] | None = None
        self._volume = 1.0
        self._generation = 0
        self._completed_generation = -1
        self._loaded = False
        self._closing = False
        self._pending_play_position: int | None = None
        self._pending_seek_position: int | None = None
        self._load_watchdog: Future[Any] | None = None
        self._operation_lock = asyncio.Lock()
        self._operations: set[Future[Any]] = set()
        self._media_operation_lock = asyncio.Lock()
        self._media_generation = 0
        self._media_snapshot: tuple[object, ...] | None = None
        self._media_pending_snapshot: tuple[object, ...] | None = None
        self._media_session_active = False
        self.media_session = BackgroundAudioSession(on_action=self._media_action)
        self.page.services.append(self.media_session)

    def bind(
        self,
        *,
        on_position: Callable[[int], None],
        on_loaded: Callable[[], None] | None = None,
        on_duration: Callable[[int], None],
        on_playing: Callable[[bool], None],
        on_completed: Callable[[], None],
        on_error: Callable[[str], None] | None = None,
        on_media_action: Callable[[str, int | None], None] | None = None,
    ) -> None:
        self.on_position = on_position
        self.on_loaded = on_loaded
        self.on_duration = on_duration
        self.on_playing = on_playing
        self.on_completed = on_completed
        self.on_error = on_error
        self.on_media_action = on_media_action

    def sync_media_session(
        self,
        *,
        title: str,
        artist: str = "",
        album: str = "",
        artwork_uri: str = "",
        duration_ms: int = 0,
        position_ms: int = 0,
        playing: bool = False,
        has_next: bool = True,
        has_previous: bool = True,
        repeat_mode: str = "off",
        shuffle: bool = False,
    ) -> None:
        """Publish a throttled, coherent snapshot to native system controls.

        Flet already sends one position event per second. The native media
        panel extrapolates a playing timeline, so a five-second correction is
        sufficient and avoids a second high-frequency platform-channel stream
        while the display is off.
        """
        clean_title = title.strip()
        duration = max(0, int(duration_ms))
        position = max(0, int(position_ms))
        is_playing = bool(playing)
        can_skip_next = bool(has_next)
        can_skip_previous = bool(has_previous)
        is_shuffled = bool(shuffle)
        position_marker = position // 5_000 if is_playing else position
        snapshot: tuple[object, ...] = (
            clean_title,
            artist,
            album,
            artwork_uri,
            duration,
            position_marker,
            is_playing,
            can_skip_next,
            can_skip_previous,
            repeat_mode,
            is_shuffled,
        )
        if (
            snapshot == self._media_snapshot
            or snapshot == self._media_pending_snapshot
            or self._closing
        ):
            return
        # Keep the last successfully published snapshot separate from the
        # latest queued one. A method-channel failure must remain retryable;
        # recording it as already synchronized would permanently strand stale
        # lock-screen controls until another playback event happens.
        self._media_pending_snapshot = snapshot
        self._media_generation += 1
        generation = self._media_generation
        payload = {
            "title": clean_title,
            "artist": artist,
            "album": album,
            "artwork_uri": artwork_uri,
            "duration_ms": duration,
            "position_ms": position,
            "playing": is_playing,
            "has_next": can_skip_next,
            "has_previous": can_skip_previous,
            "repeat_mode": repeat_mode,
            "shuffle": is_shuffled,
        }
        try:
            self.page.run_task(
                self._run_media_sync,
                generation,
                cast(Any, payload),
                snapshot,
            )
        except Exception:
            if (
                generation == self._media_generation
                and self._media_pending_snapshot == snapshot
            ):
                self._media_pending_snapshot = None
            logger.debug(
                "Media-session update skipped after session disposal", exc_info=True
            )

    def load(self, source: AudioSource) -> None:
        if self._closing:
            return
        self._cancel_load_watchdog()
        self._generation += 1
        self._cancel_operations()
        generation = self._generation
        self._loaded = False
        self._pending_play_position = None
        self._pending_seek_position = None

        # Sources can become ready on a download or file-I/O worker. Flet page
        # patches are event-loop-affine: patching ``page.services`` from that
        # worker updates Python state but can leave the native Audio service
        # unmounted, with no loaded/state/error event ever arriving. Always
        # marshal service replacement to the page loop.
        try:
            self.page.run_task(self._mount_source, source, generation)
        except Exception:
            logger.exception("Audio source could not be scheduled for mounting")
            self._report_error("The audio player could not start loading this track.")

    async def _mount_source(self, source: AudioSource, generation: int) -> None:
        if self._closing or generation != self._generation:
            return
        previous = self.audio
        try:
            if previous is not None:
                try:
                    self.page.services.remove(previous)
                except ValueError:
                    pass

            # The native bridge selects DeviceFileSource directly. Flet's generic
            # resolver calls existsSync on the Flutter UI isolate for local paths.
            audio_type = ManagedAudio if isinstance(source, str) else fa.Audio
            self.audio = audio_type(
                src=source,
                autoplay=False,
                volume=self._volume,
                release_mode=fa.ReleaseMode.STOP,
                on_loaded=lambda _event: self._loaded_event(generation),
                on_position_change=lambda event: self._position_changed(
                    event, generation
                ),
                on_duration_change=lambda event: self._duration_changed(
                    event, generation
                ),
                on_state_change=lambda event: self._state_changed(event, generation),
            )
            if isinstance(self.audio, ManagedAudio):
                self.audio.on_error = lambda event: self._source_error(
                    event.message, generation
                )
            self.page.services.append(self.audio)
            self.page.update()
            self._load_watchdog = self.page.run_task(self._watch_load, generation)
        except Exception:
            logger.exception("Audio source could not be mounted")
            if generation != self._generation or self._closing:
                return
            self._loaded = False
            self._cancel_load_watchdog()
            self._pending_play_position = None
            self._pending_seek_position = None
            if self.audio is not None:
                try:
                    self.page.services.remove(self.audio)
                except ValueError:
                    pass
                self.audio = None
            if self.on_playing:
                self.on_playing(False)
            self._report_error("The audio player could not load this track.")

    def play(self, position_ms: int = 0) -> None:
        position = max(0, int(position_ms))
        if not self._loaded or self.audio is None:
            self._pending_play_position = position
            return
        self._schedule(self.audio.play, position, generation=self._generation)

    def pause(self) -> None:
        if not self._loaded or self.audio is None:
            self._pending_play_position = None
            return
        self._schedule(self.audio.pause, generation=self._generation)

    def resume(self) -> None:
        if not self._loaded or self.audio is None:
            self._pending_play_position = self._pending_seek_position or 0
            return
        self._schedule(self.audio.resume, generation=self._generation)

    def seek(self, position_ms: int) -> None:
        position = max(0, int(position_ms))
        if not self._loaded or self.audio is None:
            self._pending_seek_position = position
            if self._pending_play_position is not None:
                self._pending_play_position = position
            return
        self._schedule(self.audio.seek, position, generation=self._generation)

    def close(self) -> None:
        # The native service is disposed with the page. Starting an RPC during
        # on_close races the session teardown and can itself produce
        # "Session closed" errors, so shutdown is deliberately local-only.
        self._closing = True
        self._generation += 1
        self._media_generation += 1
        self._media_pending_snapshot = None
        self._cancel_load_watchdog()
        self._cancel_operations()
        self._pending_play_position = None
        self._pending_seek_position = None

    def refresh_state(self) -> None:
        """Reconcile the Python UI with the native player after app resume.

        Position events are intentionally sparse while the app is backgrounded.
        Querying the native player when the page becomes visible prevents the
        slider and system-session timeline from jumping back to their last
        foreground value.
        """
        if self._closing or not self._loaded or self.audio is None:
            return
        try:
            self.page.run_task(self._refresh_state, self._generation)
        except Exception:
            logger.debug("Audio state refresh could not be scheduled", exc_info=True)

    async def _refresh_state(self, generation: int) -> None:
        if self._closing or generation != self._generation or self.audio is None:
            return
        try:
            async with self._operation_lock:
                if (
                    self._closing
                    or generation != self._generation
                    or self.audio is None
                ):
                    return
                position = await asyncio.wait_for(
                    self.audio.get_current_position(),
                    timeout=self._OPERATION_TIMEOUT_SECONDS,
                )
                duration = await asyncio.wait_for(
                    self.audio.get_duration(),
                    timeout=self._OPERATION_TIMEOUT_SECONDS,
                )
        except asyncio.CancelledError:
            return
        except Exception:
            # Resume reconciliation is best effort. The normal event stream and
            # the native session remain usable if a platform channel is briefly
            # unavailable while the app is being restored.
            logger.debug("Native audio state refresh failed", exc_info=True)
            return

        if self._closing or generation != self._generation:
            return
        if position is not None and self.on_position:
            self.on_position(int(position.in_milliseconds))
        if duration is not None and self.on_duration:
            self.on_duration(int(duration.in_milliseconds))

    def set_volume(self, value: float) -> None:
        gain = max(0.0, min(1.0, value))
        # Phones use system media volume. Preserve the shared app-volume setting
        # for desktop, but do not attenuate it behind an unavailable phone slider.
        self._volume = float(gain > 0) if self._use_device_volume else gain
        if self.audio is None:
            return
        self.audio.volume = self._volume
        try:
            self.page.update(cast(ft.Control, self.audio))
        except Exception:
            logger.debug(
                "Audio volume update deferred until service mount", exc_info=True
            )

    def _loaded_event(self, generation: int) -> None:
        if generation != self._generation or self._closing or self.audio is None:
            return
        self._loaded = True
        self._cancel_load_watchdog()
        if self.on_loaded:
            self.on_loaded()
        pending_play = self._pending_play_position
        pending_seek = self._pending_seek_position
        self._pending_play_position = None
        self._pending_seek_position = None
        if pending_play is not None:
            self._schedule(self.audio.play, pending_play, generation=generation)
        elif pending_seek is not None:
            self._schedule(self.audio.seek, pending_seek, generation=generation)

    def _schedule(
        self,
        operation: Callable[..., Awaitable[Any]],
        *args: Any,
        generation: int,
    ) -> None:
        if self._closing or generation != self._generation:
            return
        try:
            future = self.page.run_task(
                self._run_operation, operation, args, generation
            )
            self._operations.add(future)
            future.add_done_callback(self._operations.discard)
        except Exception:
            logger.exception("Audio operation could not be scheduled")
            self._report_error(
                "The audio player did not respond. The playback service may "
                "have disconnected."
            )

    async def _run_operation(
        self,
        operation: Callable[..., Awaitable[Any]],
        args: tuple[Any, ...],
        generation: int,
    ) -> None:
        if self._closing or generation != self._generation:
            return
        try:
            async with self._operation_lock:
                if self._closing or generation != self._generation:
                    return
                await asyncio.wait_for(
                    operation(*args), timeout=self._OPERATION_TIMEOUT_SECONDS
                )
        except asyncio.CancelledError:
            return
        except Exception:
            # This coroutine is the final boundary around Flet/native RPCs.
            # Extension and method-channel failures are not restricted to the
            # built-in timeout/OSError hierarchy.
            logger.exception("Audio operation failed")
            if self._closing or generation != self._generation:
                return
            self._report_error(
                "The audio player did not respond. The track may be unsupported or "
                "the playback service may have disconnected."
            )

    async def _watch_load(self, generation: int) -> None:
        await asyncio.sleep(self._OPERATION_TIMEOUT_SECONDS)
        if generation == self._generation and not self._loaded and not self._closing:
            if self.on_playing:
                self.on_playing(False)
            self._report_error(
                "The track could not be loaded by the audio service. The file may be "
                "corrupted or encoded in an unsupported format."
            )

    def _cancel_load_watchdog(self) -> None:
        watchdog = self._load_watchdog
        self._load_watchdog = None
        if watchdog is not None:
            watchdog.cancel()

    def _cancel_operations(self) -> None:
        for future in tuple(self._operations):
            future.cancel()
        self._operations.clear()
        # Obsolete RPCs cannot hold the next player's transport lock hostage.
        self._operation_lock = asyncio.Lock()

    def _source_error(self, message: str, generation: int) -> None:
        if generation == self._generation and not self._closing:
            self._cancel_load_watchdog()
            self._report_error(message)

    def discard_source(self) -> None:
        """Invalidate callbacks immediately, then dispose on the page loop."""
        self._generation += 1
        self._loaded = False
        self._pending_play_position = self._pending_seek_position = None
        self._cancel_load_watchdog()
        self._cancel_operations()
        self.page.run_task(self._unmount_source, self._generation)

    async def _unmount_source(self, generation: int) -> None:
        if generation != self._generation or self._closing:
            return
        if self.audio is not None:
            try:
                self.page.services.remove(self.audio)
            except ValueError:
                pass
            self.audio = None
            self.page.update()

    async def _run_media_sync(
        self,
        generation: int,
        payload: dict[str, object],
        snapshot: tuple[object, ...],
    ) -> None:
        if self._closing or generation != self._media_generation:
            return
        try:
            async with self._media_operation_lock:
                if self._closing or generation != self._media_generation:
                    return
                if payload["title"]:
                    # Activation happens inside the native sync call. Mark the
                    # session as potentially active before entering it so a
                    # partial platform-channel failure is still followed by a
                    # best-effort deactivate when the track is cleared.
                    self._media_session_active = True
                    await asyncio.wait_for(
                        self.media_session.sync(**cast(Any, payload)),
                        timeout=self._OPERATION_TIMEOUT_SECONDS,
                    )
                elif self._media_session_active:
                    await asyncio.wait_for(
                        self.media_session.deactivate(),
                        timeout=self._OPERATION_TIMEOUT_SECONDS,
                    )
                    self._media_session_active = False
                # Commit only after the native operation has completed. This is
                # the success marker used to coalesce future progress updates.
                if generation == self._media_generation:
                    self._media_snapshot = snapshot
                    if self._media_pending_snapshot == snapshot:
                        self._media_pending_snapshot = None
        except asyncio.CancelledError:
            return
        except Exception:
            if (
                generation == self._media_generation
                and self._media_pending_snapshot == snapshot
            ):
                self._media_pending_snapshot = None
            # System controls are an enhancement; their failure must never stop
            # the actual audio path or surface a misleading playback error.
            logger.warning("Media-session synchronization failed", exc_info=True)

    def _report_error(self, message: str) -> None:
        # Every failure must reach the controller so its optimistic loaded and
        # playing flags can be repaired. User-notification coalescing belongs
        # above this transport boundary; suppressing this callback used to
        # leave a second failed source falsely marked as playable.
        if self.on_error:
            self.on_error(message)

    def _position_changed(
        self, event: fa.AudioPositionChangeEvent, generation: int
    ) -> None:
        if generation == self._generation and self.on_position:
            self.on_position(event.position)

    def _duration_changed(
        self, event: fa.AudioDurationChangeEvent, generation: int
    ) -> None:
        if generation == self._generation and self.on_duration:
            self.on_duration(int(event.duration.in_milliseconds))

    def _state_changed(self, event: fa.AudioStateChangeEvent, generation: int) -> None:
        if generation != self._generation:
            return
        if event.state is fa.AudioState.COMPLETED:
            if self._completed_generation == generation:
                return
            self._completed_generation = generation
            if self.on_completed:
                self.on_completed()
        elif self.on_playing:
            self.on_playing(event.state is fa.AudioState.PLAYING)

    def _media_action(self, event: MediaSessionActionEvent) -> None:
        if self._closing or self.on_media_action is None:
            return
        self.on_media_action(event.action, event.seek_position_ms)
