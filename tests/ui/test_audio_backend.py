from __future__ import annotations

import asyncio
import inspect
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast

from musicplayer.app import MusicPlayerApp
from musicplayer.ui.audio_backend import FletAudioBackend


class FakePage:
    def __init__(self) -> None:
        self.services: list[Any] = []
        self.scheduled: list[tuple[Any, tuple[Any, ...]]] = []
        self.futures: list[Future[Any]] = []
        self.update_count = 0

    def update(self, *_: Any) -> None:
        self.update_count += 1

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

        # The long-lived service receives an async source-load RPC and a load
        # watchdog; play remains pending until native preparation completes.
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

    def test_track_switch_keeps_services_mounted_and_avoids_page_update(self) -> None:
        page = FakePage()
        backend = FletAudioBackend(page)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        mounted_services = tuple(page.services)

        backend.load(b"first")
        backend._loaded_event(1)
        backend.load(b"second")
        backend.pause()

        self.assertEqual(tuple(page.services), mounted_services)
        self.assertEqual(len(page.services), 2)
        self.assertEqual(page.update_count, 0)
        self.assertIs(
            page.scheduled[-1][0].__func__, FletAudioBackend._run_transport_operation
        )

    def test_obsolete_native_generation_events_are_rejected(self) -> None:
        page = FakePage()
        backend = FletAudioBackend(page)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        positions: list[int] = []
        backend.bind(
            on_position=positions.append,
            on_duration=lambda _: None,
            on_playing=lambda _: None,
            on_completed=lambda: None,
        )
        backend.load("first.mp3")
        backend.load("second.mp3")
        assert backend.audio is not None
        position_handler = backend.audio.on_position_change
        assert position_handler is not None

        cast(Any, position_handler)(SimpleNamespace(position=1_000, generation=1))
        self.assertEqual(positions, [])
        cast(Any, position_handler)(SimpleNamespace(position=2_000, generation=2))
        self.assertEqual(positions, [2_000])

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

    def test_failed_media_session_update_can_be_retried(self) -> None:
        class FailingSession:
            async def sync(self, **_: object) -> None:
                raise ValueError("native session is not ready")

            async def deactivate(self) -> None:
                pass

        page = FakePage()
        backend = FletAudioBackend(page)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        backend.media_session = FailingSession()  # ty: ignore[invalid-assignment]

        backend.sync_media_session(title="Track", playing=True)
        generation, payload, snapshot = page.scheduled[0][1]
        asyncio.run(backend._run_media_sync(generation, payload, snapshot))

        self.assertIsNone(backend._media_snapshot)
        self.assertIsNone(backend._media_pending_snapshot)

        # The same state must be schedulable again after a transient native
        # failure; otherwise the lock screen can remain stale indefinitely.
        backend.sync_media_session(title="Track", playing=True)
        self.assertEqual(len(page.scheduled), 2)

    def test_media_session_snapshot_commits_only_after_success(self) -> None:
        class WorkingSession:
            async def sync(self, **_: object) -> None:
                pass

            async def deactivate(self) -> None:
                pass

        page = FakePage()
        backend = FletAudioBackend(page)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        backend.media_session = WorkingSession()  # ty: ignore[invalid-assignment]

        backend.sync_media_session(title="Track", playing=False)
        generation, payload, snapshot = page.scheduled[0][1]
        asyncio.run(backend._run_media_sync(generation, payload, snapshot))

        backend.sync_media_session(title="Track", playing=False)
        self.assertEqual(len(page.scheduled), 1)

        backend.invalidate_media_session()
        backend.sync_media_session(title="Track", playing=False)
        self.assertEqual(len(page.scheduled), 2)
        self.assertTrue(page.scheduled[-1][1][1]["reattach"])

    def test_pause_keeps_media_session_metadata_until_explicit_stop(self) -> None:
        class SessionBackend:
            def __init__(self) -> None:
                self.payloads: list[dict[str, object]] = []

            def sync_media_session(self, **payload: object) -> None:
                self.payloads.append(payload)

        queue = SimpleNamespace(
            items=["queued-track"],
            peek_next=lambda **_: None,
            repeat=SimpleNamespace(value="off"),
            shuffle=False,
        )
        playback = SimpleNamespace(
            external_title="Preview",
            external_uploader="Artist",
            external_thumbnail="",
            current_track_id=None,
            media_session_active=True,
            duration_ms=10_000,
            position_ms=2_000,
            playing=False,
            snapshot=SimpleNamespace(loading=False),
            queue=queue,
        )
        app = object.__new__(MusicPlayerApp)
        app.playback = playback  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
        session_backend = SessionBackend()
        app.backend = session_backend  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
        app._system_media_key = None
        app._system_media_metadata = ("", "", "", "")

        app._sync_system_media()
        self.assertEqual(session_backend.payloads[-1]["title"], "Preview")
        self.assertFalse(session_backend.payloads[-1]["playing"])
        self.assertTrue(session_backend.payloads[-1]["keep_alive"])

        playback.media_session_active = False
        app._sync_system_media(refresh_metadata=False)
        self.assertEqual(session_backend.payloads[-1]["title"], "")
        self.assertFalse(session_backend.payloads[-1]["keep_alive"])

    def test_refresh_state_publishes_native_position_and_duration(self) -> None:
        class NativeAudio:
            async def get_current_position(self) -> SimpleNamespace:
                return SimpleNamespace(in_milliseconds=12_345)

            async def get_duration(self) -> SimpleNamespace:
                return SimpleNamespace(in_milliseconds=180_000)

        page = FakePage()
        backend = FletAudioBackend(page)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        backend.audio = NativeAudio()  # ty: ignore[invalid-assignment]
        backend._loaded = True
        positions: list[int] = []
        durations: list[int] = []
        backend.bind(
            on_position=positions.append,
            on_duration=durations.append,
            on_playing=lambda _: None,
            on_completed=lambda: None,
        )

        asyncio.run(backend._refresh_state(backend._generation))

        self.assertEqual(positions, [12_345])
        self.assertEqual(durations, [180_000])

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

            def stop(self) -> None:
                self.playing = False
                self.position_ms = 0
                self.calls.append(("stop", None))

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
                ("stop", None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
