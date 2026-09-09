import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from musicplayer.application.library_service import LibraryService
from musicplayer.application.playback import PlaybackController
from musicplayer.application.store import ApplicationStore
from musicplayer.core.downloader import Downloader, DownloadStatus
from musicplayer.core.library import MusicManager
from tests.unit.test_playback import DeferredExecutor, FakeAudioBackend


class StateConcurrencyTests(unittest.TestCase):
    def test_cancellation_wins_before_terminal_completion(self):
        arrived, release = Event(), Event()

        class RacingDownloader(Downloader):
            def _run_yt_dlp(self, job, temporary_directory):
                (Path(temporary_directory) / "song.mp3").write_bytes(b"audio")

            def _finish(self, job, status, **kwargs):
                if status is DownloadStatus.COMPLETED:
                    arrived.set()
                    if not release.wait(5):
                        raise RuntimeError("Test completion barrier timed out")
                super()._finish(job, status, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            results = []
            downloader = RacingDownloader(directory, max_workers=1)
            try:
                task = downloader.start(
                    "https://youtu.be/test", on_complete=results.append
                )
                self.assertTrue(arrived.wait(5))
                self.assertTrue(downloader.cancel(task))
            finally:
                release.set()
                downloader.shutdown()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, DownloadStatus.CANCELLED)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_queue_commands_commit_serially(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MusicManager(root / "library.mmdb", root)
            store = ApplicationStore(root / "state.json")
            controller = PlaybackController(
                manager, LibraryService(manager, store), store, FakeAudioBackend()
            )
            with ThreadPoolExecutor(max_workers=4) as workers:
                list(workers.map(controller.add_last, map(str, range(40))))
            self.assertEqual(len(controller.snapshot.queue), 40)
            self.assertEqual(
                list(controller.snapshot.queue), store.get("playback")["queue"]
            )
            with self.assertRaises(AttributeError):
                controller.playing = True  # ty: ignore[invalid-assignment]

    def test_native_events_during_replacement_load_cannot_overwrite_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MusicManager(root / "library.mmdb", root)
            store = ApplicationStore(root / "state.json")
            file = root / "song.mp3"
            file.write_bytes(b"audio")
            track_id = manager.add_track(file)
            executor = DeferredExecutor()
            controller = PlaybackController(
                manager,
                LibraryService(manager, store),
                store,
                FakeAudioBackend(),
                io_executor=executor,
            )
            controller.play_track(str(track_id))
            controller.on_playing(False)
            controller.on_position(9000)
            controller.on_completed()
            self.assertTrue(controller.snapshot.loading)
            self.assertTrue(controller.playing)
            self.assertEqual(controller.snapshot.position_ms, 0)
            self.assertEqual(controller.current_track_id, str(track_id))
