from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import flet as ft
import flet_audio as fa
from flet_background_audio import BackgroundAudioSession, MediaSessionActionEvent

logger = logging.getLogger(__name__)
AudioSource = str | bytes


class FletAudioBackend:
    """Lifecycle-safe adapter around Flet's asynchronous audio service.

    A new service is mounted for every source. Playback and seeking are held
    until the native client confirms ``on_loaded``; RPCs then run serially on
    the page loop. This avoids racing a source update with ``play()`` and keeps
    Flet's 30-second RPC failures from escaping its Future callback.
    """

    _OPERATION_TIMEOUT_SECONDS = 12

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.audio: fa.Audio | None = None
        self.on_position: Callable[[int], None] | None = None
        self.on_duration: Callable[[int], None] | None = None
        self.on_playing: Callable[[bool], None] | None = None
        self.on_completed: Callable[[], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.on_media_action: Callable[[str, int | None], None] | None = None
        self._volume = 1.0
        self._generation = 0
        self._loaded = False
        self._closing = False
        self._pending_play_position: int | None = None
        self._pending_seek_position: int | None = None
        self._operation_lock = asyncio.Lock()
        self._media_operation_lock = asyncio.Lock()
        self._media_generation = 0
        self._media_snapshot: tuple[object, ...] | None = None
        self._media_session_active = False
        self._last_error_at = 0.0
        self.media_session = BackgroundAudioSession(on_action=self._media_action)
        self.page.services.append(self.media_session)

    def bind(
        self,
        *,
        on_position: Callable[[int], None],
        on_duration: Callable[[int], None],
        on_playing: Callable[[bool], None],
        on_completed: Callable[[], None],
        on_error: Callable[[str], None] | None = None,
        on_media_action: Callable[[str, int | None], None] | None = None,
    ) -> None:
        self.on_position = on_position
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
        position = max(0, int(position_ms))
        position_marker = position // 5_000 if playing else position
        snapshot: tuple[object, ...] = (
            clean_title,
            artist,
            album,
            artwork_uri,
            max(0, int(duration_ms)),
            position_marker,
            bool(playing),
            bool(has_next),
            bool(has_previous),
            repeat_mode,
            bool(shuffle),
        )
        if snapshot == self._media_snapshot or self._closing:
            return
        self._media_snapshot = snapshot
        self._media_generation += 1
        generation = self._media_generation
        payload = {
            "title": clean_title,
            "artist": artist,
            "album": album,
            "artwork_uri": artwork_uri,
            "duration_ms": max(0, int(duration_ms)),
            "position_ms": position,
            "playing": bool(playing),
            "has_next": bool(has_next),
            "has_previous": bool(has_previous),
            "repeat_mode": repeat_mode,
            "shuffle": bool(shuffle),
        }
        from typing import cast
        try:
            self.page.run_task(self._run_media_sync, generation, cast(Any, payload))
        except Exception:
            logger.debug(
                "Media-session update skipped after session disposal", exc_info=True
            )

    def load(self, source: AudioSource) -> None:
        if self._closing:
            return
        self._generation += 1
        generation = self._generation
        self._loaded = False
        self._pending_play_position = None
        self._pending_seek_position = None

        previous = self.audio
        if previous is not None:
            try:
                self.page.services.remove(previous)
            except ValueError:
                pass

        self.audio = fa.Audio(
            src=source,
            autoplay=False,
            volume=self._volume,
            release_mode=fa.ReleaseMode.STOP,
            on_loaded=lambda _event: self._loaded_event(generation),
            on_position_change=lambda event: self._position_changed(event, generation),
            on_duration_change=lambda event: self._duration_changed(event, generation),
            on_state_change=lambda event: self._state_changed(event, generation),
        )
        self.page.services.append(self.audio)
        self.page.update()
        try:
            self.page.run_task(self._watch_load, generation)
        except Exception:
            logger.debug(
                "Audio load watcher skipped after session disposal", exc_info=True
            )

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
        self._pending_play_position = None
        self._pending_seek_position = None

    def set_volume(self, value: float) -> None:
        self._volume = max(0.0, min(1.0, value))
        if self.audio is None:
            return
        self.audio.volume = self._volume
        try:
            from typing import cast
            self.page.update(cast(ft.Control, self.audio))
        except Exception:
            logger.debug(
                "Audio volume update deferred until service mount", exc_info=True
            )

    def _loaded_event(self, generation: int) -> None:
        if generation != self._generation or self._closing or self.audio is None:
            return
        self._loaded = True
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
            self.page.run_task(self._run_operation, operation, args, generation)
        except Exception:
            logger.debug(
                "Audio operation skipped after session disposal", exc_info=True
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
        except (TimeoutError, RuntimeError, OSError) as error:
            logger.warning("Audio operation failed: %s", error)
            self._report_error(
                "The audio player did not respond. The track may be unsupported or "
                "the playback service may have disconnected."
            )
        except asyncio.CancelledError:
            return

    async def _watch_load(self, generation: int) -> None:
        await asyncio.sleep(self._OPERATION_TIMEOUT_SECONDS)
        if generation == self._generation and not self._loaded and not self._closing:
            if self.on_playing:
                self.on_playing(False)
            self._report_error(
                "The track could not be loaded by the audio service. The file may be "
                "corrupted or encoded in an unsupported format."
            )

    async def _run_media_sync(
        self, generation: int, payload: dict[str, object]
    ) -> None:
        if self._closing or generation != self._media_generation:
            return
        try:
            async with self._media_operation_lock:
                if self._closing or generation != self._media_generation:
                    return
                if payload["title"]:
                    from typing import cast
                    await asyncio.wait_for(
                        self.media_session.sync(**cast(Any, payload)),
                        timeout=self._OPERATION_TIMEOUT_SECONDS,
                    )
                    self._media_session_active = True
                elif self._media_session_active:
                    await asyncio.wait_for(
                        self.media_session.deactivate(),
                        timeout=self._OPERATION_TIMEOUT_SECONDS,
                    )
                    self._media_session_active = False
        except (TimeoutError, RuntimeError, OSError) as error:
            # System controls are an enhancement; their failure must never stop
            # the actual audio path or surface a misleading playback error.
            logger.warning("Media-session synchronization failed: %s", error)
        except asyncio.CancelledError:
            return

    def _report_error(self, message: str) -> None:
        # A queued burst of obsolete seeks should produce one actionable error,
        # not a stack-trace or notification storm.
        now = time.monotonic()
        if now - self._last_error_at < 5:
            return
        self._last_error_at = now
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
            if self.on_completed:
                self.on_completed()
        elif self.on_playing:
            self.on_playing(event.state is fa.AudioState.PLAYING)

    def _media_action(self, event: MediaSessionActionEvent) -> None:
        if self._closing or self.on_media_action is None:
            return
        self.on_media_action(event.action, event.seek_position_ms)
