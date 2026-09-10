from __future__ import annotations

import tempfile
import unittest
import wave
from io import BytesIO
from pathlib import Path
from threading import Event

from musicplayer.application.downloads import DownloadCoordinator
from musicplayer.application.library_service import LibraryService
from musicplayer.application.models import AppSettings
from musicplayer.application.playback import PlaybackController
from musicplayer.application.store import ApplicationStore
from musicplayer.core.concurrency import LazyBoundedExecutor
from musicplayer.core.downloader import Downloader
from musicplayer.core.library import MusicManager


class ValidWaveDownloader(Downloader):
    """Deterministic stand-in for yt-dlp's successfully transcoded output."""

    def _run_yt_dlp(self, job, temporary_directory: str) -> None:
        path = Path(temporary_directory) / "downloaded.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(8_000)
            audio.writeframes(b"\x00\x00\x00\x00" * 2_000)


class FlowAudioBackend:
    def __init__(self) -> None:
        self.source: str | bytes = ""
        self.loaded = Event()
        self.played_at: list[int] = []

    def load(self, source: str | bytes) -> None:
        self.source = source
        self.loaded.set()

    def play(self, position_ms: int = 0) -> None:
        self.played_at.append(position_ms)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def seek(self, position_ms: int) -> None:
        pass

    def set_volume(self, value: float) -> None:
        pass


class CleanDownloadPlaybackFlowTests(unittest.TestCase):
    def test_clean_state_download_register_load_and_play(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            store_path = root / "state.json"
            library_path = root / "library.mmdb"
            self.assertFalse(store_path.exists())
            self.assertFalse(library_path.exists())

            settings = AppSettings(
                download_directory=str(music),
                audio_format="wav",
                concurrent_downloads=1,
            )
            store = ApplicationStore(store_path)
            store.save_settings(settings)
            manager = MusicManager(library_path, music)
            coordinator = DownloadCoordinator(manager, store, settings)
            coordinator._downloader.shutdown()
            coordinator._downloader = ValidWaveDownloader(
                music,
                max_workers=1,
                audio_extensions=frozenset({".wav"}),
            )
            io_executor = LazyBoundedExecutor(max_workers=1, max_pending=1)
            try:
                task = coordinator.start_url("https://youtu.be/clean-flow")
                job = coordinator._downloader._jobs[task.id]
                job.future.result(timeout=5)  # ty: ignore[unresolved-attribute]

                record = coordinator.list()[0]
                self.assertEqual(record.status, "completed")
                self.assertEqual(len(record.track_ids), 1)
                track_id = record.track_ids[0]
                track = manager.get_track(track_id)
                path = manager.track_path(track_id)
                self.assertEqual(path, music.resolve() / track.filename)
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 44)
                self.assertIsNone(manager.check_track_integrity(track_id))
                with wave.open(str(path), "rb") as audio:
                    self.assertEqual(audio.getnchannels(), 2)
                    self.assertEqual(audio.getframerate(), 8_000)
                    self.assertEqual(audio.getnframes(), 2_000)

                backend = FlowAudioBackend()
                playback = PlaybackController(
                    manager,
                    LibraryService(manager, store),
                    store,
                    backend,
                    io_executor=io_executor,
                )
                playback.play_track(track_id)
                self.assertTrue(backend.loaded.wait(timeout=5))
                self.assertIsInstance(backend.source, bytes)
                with wave.open(BytesIO(backend.source), "rb") as audio:  # ty: ignore[invalid-argument-type]
                    self.assertEqual(audio.getnframes(), 2_000)
                self.assertEqual(backend.played_at, [0])
                self.assertEqual(playback.current_track_id, track_id)
                playback.on_loaded()
                playback.on_playing(True)
                self.assertEqual(store.track_details(track_id).play_count, 1)
            finally:
                coordinator.shutdown()
                io_executor.shutdown(wait=False, cancel_pending=True)


if __name__ == "__main__":
    unittest.main()
