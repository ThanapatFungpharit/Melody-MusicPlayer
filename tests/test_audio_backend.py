from __future__ import annotations

import asyncio
import inspect
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any

from musicplayer.app import MusicPlayerApp
from musicplayer.ui.audio_backend import FletAudioBackend


class FakePage:
    def __init__(self) -> None:
        self.services: list[Any] = []
        self.scheduled: list[tuple[Any, tuple[Any, ...]]] = []
        self.futures: list[Future[Any]] = []

    def update(self, *_: Any) -> None:
        pass

    def run_task(self, handler: Any, *args: Any) -> Future[Any]:
        self.scheduled.append((handler, args))
        future: Future[Any] = Future()
        self.futures.append(future)
        return future


class FletAudioBackendTests(unittest.TestCase):
    def test_phone_gain_uses_system_volume_while_desktop_keeps_precise_gain(self):
        for mobile, expected in ((True, 1.0), (False, 0.25)):
            backend = FletAudioBackend(FakePage(), use_device_volume=mobile)  # ty: ignore[invalid-argument-type]
            backend.set_volume(0.25)
            self.assertEqual(backend._volume, expected)
            backend.set_volume(0)
            self.assertEqual(backend._volume, 0)
            backend.set_volume(0.25)
            self.assertEqual(backend._volume, expected)

    def test_play_waits_for_loaded_event_and_operations_use_page_loop(self) -> None:
        page = FakePage()
        backend = FletAudioBackend(page)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

        backend.load(b"audio bytes")
        backend.play(1250)

        # Service creation itself is marshalled to the page loop because a
        # source may have become ready on a worker thread.
        self.assertEqual(len(page.scheduled), 1)
        mount_handler, mount_args = page.scheduled[0]
        asyncio.run(mount_handler(*mount_args))
        # Mounting schedules the load watchdog; play remains pending until the
        # native client confirms that its player is ready.
        self.assertEqual(len(page.scheduled), 2)
        backend._loaded_event(1)
        self.assertTrue(page.futures[1].cancelled())
        backend.pause()
        backend.resume()
        backend.seek(2500)
        backend.close()

        self.assertEqual(len(page.scheduled), 6)
        self.assertTrue(
            all(inspect.iscoroutinefunction(handler) for handler, _ in page.scheduled)
        )
        play_operation = page.scheduled[2][1]
        seek_operation = page.scheduled[5][1]
        self.assertEqual(play_operation[1], (1250,))
        self.assertEqual(seek_operation[1], (2500,))

    def test_media_session_updates_are_throttled_to_five_seconds(self) -> None:
        page = FakePage()
        backend = FletAudioBackend(page)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

        backend.sync_media_session(
            title="Track",
            artist="Artist",
            artwork_uri="https://example.test/cover.jpg",
            duration_ms=180_000,
            position_ms=1_000,
            playing=True,
        )
        backend.sync_media_session(
            title="Track",
            artist="Artist",
            artwork_uri="https://example.test/cover.jpg",
            duration_ms=180_000,
            position_ms=4_999,
            playing=True,
        )
        backend.sync_media_session(
            title="Track",
            artist="Artist",
            artwork_uri="https://example.test/cover.jpg",
            duration_ms=180_000,
            position_ms=5_000,
            playing=True,
        )

        self.assertEqual(len(page.scheduled), 2)
        first_payload = page.scheduled[0][1][1]
        second_payload = page.scheduled[1][1][1]
        self.assertEqual(first_payload["position_ms"], 1_000)
        self.assertEqual(second_payload["position_ms"], 5_000)

    def test_native_media_action_is_forwarded_to_controller_callback(self) -> None:
        page = FakePage()
        backend = FletAudioBackend(page)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        received: list[tuple[str, int | None]] = []
        backend.bind(
            on_position=lambda _: None,
            on_duration=lambda _: None,
            on_playing=lambda _: None,
            on_completed=lambda: None,
            on_media_action=lambda action, position: received.append(
                (action, position)
            ),
        )

        event = SimpleNamespace(
            action="seekTo",
            seek_position_ms=42_000,
        )
        backend._media_action(event)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

        self.assertEqual(received, [("seekTo", 42_000)])

    def test_every_audio_failure_is_forwarded_for_state_repair(self) -> None:
        page = FakePage()
        backend = FletAudioBackend(page)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        errors: list[str] = []
        backend.bind(
            on_position=lambda _: None,
            on_duration=lambda _: None,
            on_playing=lambda _: None,
            on_completed=lambda: None,
            on_error=errors.append,
        )

        backend._report_error("first")
        backend._report_error("second")

        self.assertEqual(errors, ["first", "second"])

    def test_native_media_actions_map_to_idempotent_playback_commands(self) -> None:
        class FakePlayback:
            def __init__(self) -> None:
                self.playing = False
                self.position_ms = 30_000
                self.calls: list[tuple[str, int | None]] = []

            def toggle(self) -> None:
                self.playing = not self.playing
                self.calls.append(("toggle", None))

            def next(self) -> None:
                self.calls.append(("next", None))

            def previous(self) -> None:
                self.calls.append(("previous", None))

            def seek(self, position_ms: int) -> None:
                self.position_ms = position_ms
                self.calls.append(("seek", position_ms))

        playback = FakePlayback()
        app = object.__new__(MusicPlayerApp)
        app.playback = playback  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

        app._media_action("play", None)
        app._media_action("play", None)
        app._media_action("pause", None)
        app._media_action("pause", None)
        app._media_action("skipToNext", None)
        app._media_action("skipToPrevious", None)
        app._media_action("seekTo", 42_000)
        app._media_action("rewind", None)
        app._media_action("fastForward", None)
        playback.playing = True
        app._media_action("stop", None)

        self.assertEqual(
            playback.calls,
            [
                ("toggle", None),
                ("toggle", None),
                ("next", None),
                ("previous", None),
                ("seek", 42_000),
                ("seek", 32_000),
                ("seek", 42_000),
                ("toggle", None),
                ("seek", 0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
