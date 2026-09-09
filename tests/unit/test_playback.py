from __future__ import annotations

import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from musicplayer.application.library_service import LibraryService
from musicplayer.application.playback import PlaybackController
from musicplayer.application.store import ApplicationStore
from musicplayer.core.library import MusicManager


class FakeAudioBackend:
    def __init__(self) -> None:
        self.source = ""
        self.played_at: list[int] = []
        self.volume = 0.0
        self.pause_count = 0
        self.seeked_to: list[int] = []

    def load(self, source: str | bytes) -> None:
        self.source = source

    def play(self, position_ms: int = 0) -> None:
        self.played_at.append(position_ms)

    def pause(self) -> None:
        self.pause_count += 1

    def resume(self) -> None:
        self.played_at.append(-1)

    def seek(self, position_ms: int) -> None:
        self.seeked_to.append(position_ms)

    def set_volume(self, value: float) -> None:
        self.volume = value


class DeferredExecutor:
    def __init__(self) -> None:
        self.future: Future[str | bytes] | None = None
        self.futures: list[Future[str | bytes]] = []
        self.function = None
        self.args: tuple[object, ...] = ()

    def submit(self, function, /, *args) -> Future[str | bytes]:
        self.function = function
        self.args = args
        self.future = Future()
        self.futures.append(self.future)
        return self.future


class PlaybackControllerTests(unittest.TestCase):
    def test_bulk_queue_updates_persist_and_notify_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            manager = MusicManager(root / "library.mmdb", music)
            store = ApplicationStore(root / "state.json")
            controller = PlaybackController(
                manager, LibraryService(manager, store), store, FakeAudioBackend()
            )
            controller.queue.replace(["current"])

            with (
                patch.object(controller, "_persist") as persist,
                patch.object(controller, "_notify") as notify,
            ):
                controller.add_next_many(("next-one", "next-two"))
                controller.add_last_many(("last-one", "last-two"))

            self.assertEqual(
                controller.queue.items,
                ["current", "next-one", "next-two", "last-one", "last-two"],
            )
            self.assertEqual(persist.call_count, 2)
            self.assertEqual(notify.call_count, 2)

    def test_audio_file_read_is_deferred_to_the_io_executor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            path = music / "song.mp3"
            path.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", music)
            track_id = manager.add_track(path, title="Song")
            store = ApplicationStore(root / "state.json")
            backend = FakeAudioBackend()
            executor = DeferredExecutor()
            controller = PlaybackController(
                manager,
                LibraryService(manager, store),
                store,
                backend,
                io_executor=executor,
            )

            controller.play_track(str(track_id))

            self.assertEqual(backend.source, "")
            self.assertIsNotNone(executor.function)
            controller.seek(123)
            executor.future.set_result(b"audio")  # ty: ignore[unresolved-attribute]
            self.assertEqual(backend.source, b"audio")
            self.assertEqual(backend.played_at, [123])
            self.assertEqual(backend.seeked_to, [])

    def test_stale_background_audio_read_cannot_replace_newer_track(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            manager = MusicManager(root / "library.mmdb", music)
            ids = []
            for name in ("one.mp3", "two.mp3"):
                path = music / name
                path.write_bytes(name.encode())
                ids.append(str(manager.add_track(path, title=name)))
            store = ApplicationStore(root / "state.json")
            backend = FakeAudioBackend()
            executor = DeferredExecutor()
            controller = PlaybackController(
                manager,
                LibraryService(manager, store),
                store,
                backend,
                io_executor=executor,
            )

            controller.play_track(ids[0])
            first = executor.futures[0]
            first.set_running_or_notify_cancel()
            controller.play_track(ids[1])
            second = executor.futures[1]

            first.set_result(b"stale")
            self.assertEqual(backend.source, "")
            second.set_result(b"current")
            self.assertEqual(backend.source, b"current")

    def test_play_tracks_keeps_requested_start_after_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            manager = MusicManager(root / "library.mmdb", music)
            ids = []
            for name in ("one.mp3", "two.mp3"):
                path = music / name
                path.write_bytes(name.encode())
                ids.append(str(manager.add_track(path, title=name)))
            store = ApplicationStore(root / "state.json")
            controller = PlaybackController(
                manager, LibraryService(manager, store), store, FakeAudioBackend()
            )

            controller.play_tracks(["not-a-uuid", ids[0], ids[1]], start_index=1)

            self.assertEqual(controller.current_track_id, ids[0])

    def test_next_at_queue_end_stops_and_rewinds_backend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            path = music / "song.mp3"
            path.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", music)
            track_id = manager.add_track(path, title="Song")
            store = ApplicationStore(root / "state.json")
            backend = FakeAudioBackend()
            controller = PlaybackController(
                manager, LibraryService(manager, store), store, backend
            )
            controller.play_tracks([str(track_id)])

            controller.next()

            self.assertFalse(controller.playing)
            self.assertEqual(controller.position_ms, 0)
            self.assertEqual(backend.pause_count, 1)
            self.assertEqual(backend.seeked_to, [0])

    def test_clear_library_state_stops_audio_and_empties_persistent_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            path = music / "song.mp3"
            path.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", music)
            track_id = manager.add_track(path, title="Song")
            store = ApplicationStore(root / "state.json")
            backend = FakeAudioBackend()
            controller = PlaybackController(
                manager, LibraryService(manager, store), store, backend
            )
            controller.play_tracks([str(track_id)])
            controller.position_ms = 400

            controller.clear_library_state()
            controller.clear_library_state()

            self.assertEqual(controller.queue.items, [])
            self.assertEqual(controller.queue.current_index, -1)
            self.assertFalse(controller.playing)
            self.assertEqual(controller.position_ms, 0)
            self.assertEqual(controller.duration_ms, 0)
            self.assertEqual(backend.pause_count, 1)
            self.assertEqual(store.get("playback")["queue"], [])

    def test_toggle_does_not_play_after_current_file_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            path = music / "song.mp3"
            path.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", music)
            track_id = manager.add_track(path, title="Song")
            store = ApplicationStore(root / "state.json")
            store.set(
                "playback",
                {
                    "queue": [str(track_id)],
                    "current_index": 0,
                    "position_ms": 1234,
                },
            )
            backend = FakeAudioBackend()
            controller = PlaybackController(
                manager, LibraryService(manager, store), store, backend
            )
            path.unlink()

            controller.toggle()

            self.assertFalse(controller.playing)
            self.assertEqual(backend.played_at, [])

    def test_restored_queue_discards_invalid_tracks_and_preserves_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            path = music / "song.mp3"
            path.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", music)
            track_id = manager.add_track(path, title="Song")
            store = ApplicationStore(root / "state.json")
            store.set(
                "playback",
                {
                    "queue": ["not-a-uuid", str(track_id)],
                    "current_index": 1,
                    "position_ms": 3456,
                    "shuffle": False,
                    "repeat": "off",
                },
            )

            controller = PlaybackController(
                manager, LibraryService(manager, store), store, FakeAudioBackend()
            )

            self.assertEqual(controller.queue.items, [str(track_id)])
            self.assertEqual(controller.current_track_id, str(track_id))
            self.assertEqual(controller.position_ms, 3456)

    def test_removing_only_current_item_stops_playback_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            path = music / "song.mp3"
            path.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", music)
            track_id = manager.add_track(path, title="Song")
            store = ApplicationStore(root / "state.json")
            backend = FakeAudioBackend()
            controller = PlaybackController(
                manager, LibraryService(manager, store), store, backend
            )
            controller.play_tracks([str(track_id)])

            controller.remove_queue_item(0)

            self.assertEqual(controller.queue.items, [])
            self.assertFalse(controller.playing)
            self.assertEqual(controller.position_ms, 0)
            self.assertEqual(backend.pause_count, 1)

    def test_drag_reorder_persists_and_preserves_current_track(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            manager = MusicManager(root / "library.mmdb", music)
            ids = []
            for name in ("one.mp3", "two.mp3", "three.mp3"):
                path = music / name
                path.write_bytes(name.encode())
                ids.append(str(manager.add_track(path, title=name)))
            store = ApplicationStore(root / "state.json")
            controller = PlaybackController(
                manager, LibraryService(manager, store), store, FakeAudioBackend()
            )
            controller.queue.replace(ids, start_index=1)

            controller.reorder_queue(0, 3)

            self.assertEqual(controller.queue.items, [ids[1], ids[2], ids[0]])
            self.assertEqual(controller.current_track_id, ids[1])
            self.assertEqual(store.get("playback")["queue"], controller.queue.items)

    def test_restored_queue_loads_source_before_resuming(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            path = music / "song.mp3"
            path.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", music)
            track_id = manager.add_track(path, title="Song")
            store = ApplicationStore(root / "state.json")
            settings = store.settings
            settings.download_directory = str(music)
            settings.resume_session = True
            store.save_settings(settings)
            store.set(
                "playback",
                {
                    "queue": [str(track_id)],
                    "current_index": 0,
                    "position_ms": 3456,
                    "shuffle": False,
                    "repeat": "off",
                },
            )
            backend = FakeAudioBackend()
            controller = PlaybackController(
                manager, LibraryService(manager, store), store, backend
            )

            controller.toggle()

            self.assertEqual(backend.source, b"audio")
            self.assertEqual(backend.played_at, [3456])

    def test_mute_restores_previous_exact_volume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MusicManager(root / "library.mmdb", root / "music")
            store = ApplicationStore(root / "state.json")
            backend = FakeAudioBackend()
            controller = PlaybackController(
                manager, LibraryService(manager, store), store, backend
            )
            controller.set_volume(41)
            controller.toggle_mute()
            self.assertEqual(backend.volume, 0.0)
            controller.toggle_mute()
            self.assertEqual(controller.volume, 41)
            self.assertEqual(backend.volume, 0.41)

    def test_volume_preview_skips_persistence_until_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MusicManager(root / "library.mmdb", root / "music")
            store = ApplicationStore(root / "state.json")
            changes: list[str] = []
            controller = PlaybackController(
                manager,
                LibraryService(manager, store),
                store,
                FakeAudioBackend(),
                on_change=changes.append,
            )

            with patch.object(store, "save_settings") as save_settings:
                controller.set_volume(35, persist=False)
                controller.set_volume(35)

            self.assertEqual(save_settings.call_count, 1)
            self.assertEqual(changes, ["volume", "volume"])

    def test_position_event_requests_only_a_progress_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MusicManager(root / "library.mmdb", root / "music")
            store = ApplicationStore(root / "state.json")
            changes: list[str] = []
            controller = PlaybackController(
                manager,
                LibraryService(manager, store),
                store,
                FakeAudioBackend(),
                on_change=changes.append,
            )

            controller._last_persisted_position = time.monotonic()
            controller.on_position(500)

            self.assertEqual(changes, ["progress"])

    def test_backend_failure_repairs_loaded_state_and_next_toggle_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            path = music / "song.mp3"
            path.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", music)
            track_id = manager.add_track(path, title="Song")
            store = ApplicationStore(root / "state.json")
            backend = FakeAudioBackend()
            errors: list[str] = []
            controller = PlaybackController(
                manager,
                LibraryService(manager, store),
                store,
                backend,
                on_error=errors.append,
            )
            controller.play_track(str(track_id))

            controller.on_backend_error("native load failed")

            self.assertFalse(controller.playing)
            self.assertFalse(controller._source_loaded)
            self.assertEqual(errors, ["native load failed"])
            self.assertEqual(store.track_details(str(track_id)).play_count, 0)
            controller.toggle()
            self.assertEqual(backend.played_at, [0, 0])
            controller.on_playing(True)
            self.assertEqual(store.track_details(str(track_id)).play_count, 1)

    def test_backend_failure_cancels_a_pending_background_source_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            path = music / "song.mp3"
            path.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", music)
            track_id = manager.add_track(path, title="Song")
            store = ApplicationStore(root / "state.json")
            executor = DeferredExecutor()
            controller = PlaybackController(
                manager,
                LibraryService(manager, store),
                store,
                FakeAudioBackend(),
                io_executor=executor,
            )

            controller.play_track(str(track_id))
            pending = executor.future
            controller.on_backend_error("native load failed")

            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertTrue(pending.cancelled())
            self.assertFalse(controller._loading)
            self.assertFalse(controller._source_loaded)


if __name__ == "__main__":
    unittest.main()
