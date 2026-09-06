from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import UUID

from musicplayer.application.downloads import (
    DownloadCoordinator,
    DuplicateDownloadError,
    _download_options,
    _friendly_download_error,
    extract_download_urls,
)
from musicplayer.application.models import AppSettings, DownloadRecord, TrackDetails
from musicplayer.application.store import ApplicationStore
from musicplayer.core.concurrency import WorkerQueueFull
from musicplayer.core.downloader import (
    Downloader,
    DownloadProgress,
    DownloadResult,
    DownloadStatus,
    DownloadTask,
)
from musicplayer.core.library import MusicManager
from musicplayer.platform_runtime import current_architecture, current_platform


class StubDownloader(Downloader):
    def _run_yt_dlp(self, job, temporary_directory: str) -> None:
        Path(temporary_directory, "downloaded.mp3").write_bytes(b"audio")


class _DownloadStore:
    def __init__(self) -> None:
        self.records: list[DownloadRecord] = []
        self.save_count = 0

    def downloads(self) -> list[DownloadRecord]:
        return list(self.records)

    def save_downloads(self, records: list[DownloadRecord]) -> None:
        self.save_count += 1
        self.records = list(records)


class _Manager:
    @staticmethod
    def find_track_by_source(url: str) -> None:
        return None


class DownloaderTests(unittest.TestCase):
    def test_downloader_pool_is_lazy_and_rejects_excess_pending_work(self) -> None:
        started = Event()
        release = Event()

        class BlockingDownloader(Downloader):
            def _run_yt_dlp(self, job, temporary_directory: str) -> None:
                started.set()
                release.wait(timeout=2)
                Path(temporary_directory, "downloaded.mp3").write_bytes(b"audio")

        with tempfile.TemporaryDirectory() as directory:
            downloader = BlockingDownloader(directory, max_workers=1, max_pending=1)
            self.assertFalse(downloader._executor.is_active)
            first = downloader.start("https://example.test/one")
            self.assertTrue(started.wait(timeout=1))
            second = downloader.start("https://example.test/two")
            with self.assertRaises(WorkerQueueFull):
                downloader.start("https://example.test/three")
            release.set()
            downloader._jobs[first.id].future.result(timeout=2)  # ty: ignore[unresolved-attribute]
            downloader._jobs[second.id].future.result(timeout=2)  # ty: ignore[unresolved-attribute]
            downloader.shutdown()

    def test_completed_job_metadata_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            downloader = StubDownloader(
                directory,
                max_workers=1,
                max_pending=1,
                max_retained_jobs=2,
            )
            tasks = []
            for index in range(3):
                task = downloader.start(f"https://example.test/{index}")
                downloader._jobs[task.id].future.result(timeout=2)  # ty: ignore[unresolved-attribute]
                tasks.append(task)

            self.assertEqual(len(downloader._jobs), 2)
            with self.assertRaises(ValueError):
                downloader.result(tasks[0])
            downloader.shutdown()

    def test_download_options_use_uploaded_cookie_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cookie_file = Path(directory) / "cookies.txt"
            cookie_file.write_text("cookie data", encoding="utf-8")
            settings = AppSettings(cookie_file=str(cookie_file))

            self.assertEqual(
                _download_options(settings)["cookiefile"], str(cookie_file)
            )

    def test_download_options_use_bundled_ffmpeg(self) -> None:
        options = _download_options(AppSettings())
        location = Path(options["ffmpeg_location"])  # ty: ignore[invalid-argument-type]

        self.assertEqual(location.name, current_architecture())
        self.assertEqual(location.parent.name, current_platform())

    def test_download_options_are_anonymous_without_an_upload(self) -> None:
        self.assertNotIn("cookiefile", _download_options(AppSettings()))

    def test_missing_encoder_has_an_actionable_error(self) -> None:
        error = _friendly_download_error(
            "audio conversion failed: Error opening output files: Encoder not found"
        )

        self.assertIn("audio encoder", error)
        self.assertIn("Restart Melody", error)

    def test_background_download_returns_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            downloader = StubDownloader(directory, max_workers=1)
            task = downloader.start("https://example.test/audio")
            job = downloader._jobs[task.id]
            job.future.result(timeout=3)  # ty: ignore[unresolved-attribute]
            result = downloader.result(task)
            downloader.shutdown()

            self.assertEqual(result.status, DownloadStatus.COMPLETED)  # ty: ignore[unresolved-attribute]
            self.assertEqual(len(result.files), 1)  # ty: ignore[unresolved-attribute]
            self.assertTrue(result.files[0].exists())  # ty: ignore[unresolved-attribute]

    def test_core_downloader_always_uses_bundled_media_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            downloader = Downloader(directory, max_workers=1)
            try:
                location = Path(downloader._options["ffmpeg_location"])  # ty: ignore[invalid-argument-type]
                self.assertEqual(location.name, current_architecture())
                self.assertEqual(location.parent.name, current_platform())
            finally:
                downloader.shutdown()


class BatchDownloadTests(unittest.TestCase):
    def test_completed_download_reuses_content_duplicate_and_removes_extra_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            existing_path = music / "existing.mp3"
            existing_path.write_bytes(b"same audio")
            duplicate_path = music / "new-download.mp3"
            duplicate_path.write_bytes(b"same audio")
            manager = MusicManager(root / "library.mmdb", music)
            existing_id = manager.add_track(existing_path, title="Existing")
            store = ApplicationStore(root / "state.json")
            store.save_track_details(str(existing_id), TrackDetails(favorite=True))
            settings = AppSettings(download_directory=str(music))
            task = DownloadTask(UUID(int=1))
            result = DownloadResult(
                task,
                "https://youtu.be/new-source",
                DownloadStatus.COMPLETED,
                (duplicate_path,),
            )
            metadata = {
                "url": result.url,
                "title": "Downloaded title",
                "source": "YouTube",
            }
            with patch("musicplayer.application.downloads.Downloader"):
                coordinator = DownloadCoordinator(manager, store, settings)

            coordinator._handle_complete(result, metadata)  # ty: ignore[invalid-argument-type]

            record = coordinator.list()[0]
            self.assertEqual(record.status, "completed")
            self.assertEqual(record.track_ids, [str(existing_id)])
            self.assertEqual(len(manager.list_tracks()), 1)
            self.assertTrue(existing_path.exists())
            self.assertFalse(duplicate_path.exists())
            self.assertEqual(manager.find_track_by_source(result.url).id, existing_id)  # ty: ignore[unresolved-attribute]
            self.assertTrue(store.track_details(str(existing_id)).favorite)

    def test_progress_callbacks_are_throttled_between_status_changes(self) -> None:
        store = _DownloadStore()
        notifications: list[DownloadRecord] = []
        task = DownloadTask(UUID(int=1))
        progress = DownloadProgress(
            task,
            "https://youtu.be/song",
            DownloadStatus.DOWNLOADING,
            progress=0.25,
        )
        metadata = {
            "url": progress.url,
            "title": "Song",
            "source": "YouTube",
        }
        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(download_directory=directory)
            with patch("musicplayer.application.downloads.Downloader"):
                coordinator = DownloadCoordinator(
                    _Manager(), store, settings, on_change=notifications.append  # ty: ignore[invalid-argument-type]
                )
            with patch(
                "musicplayer.application.downloads.time.monotonic",
                side_effect=(10.0, 10.1, 11.1),
            ):
                coordinator._handle_progress(progress, metadata)  # ty: ignore[invalid-argument-type]
                coordinator._handle_progress(progress, metadata)  # ty: ignore[invalid-argument-type]
                coordinator._handle_progress(progress, metadata)  # ty: ignore[invalid-argument-type]

        self.assertEqual(store.save_count, 2)
        self.assertEqual(len(notifications), 2)

    def test_pasted_url_lists_are_extracted_and_deduplicated(self) -> None:
        urls = extract_download_urls(
            """
            - https://youtu.be/song-one,
            2. https://www.youtube.com/watch?v=song-two
            duplicate: https://youtu.be/song-one
            """
        )

        self.assertEqual(
            urls,
            (
                "https://youtu.be/song-one",
                "https://www.youtube.com/watch?v=song-two",
            ),
        )

    def test_batch_schedules_an_independent_record_for_each_url(self) -> None:
        store = _DownloadStore()
        attempts: list[str] = []

        def start(url, *, on_progress, on_complete):
            attempts.append(url)
            task = DownloadTask(UUID(int=len(attempts)))
            on_progress(DownloadProgress(task, url, DownloadStatus.QUEUED))
            return task

        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(download_directory=directory)
            with patch("musicplayer.application.downloads.Downloader") as downloader:
                downloader.return_value.start.side_effect = start
                coordinator = DownloadCoordinator(_Manager(), store, settings)  # ty: ignore[invalid-argument-type]
                batch = coordinator.start_urls(
                    (
                        "https://youtu.be/one",
                        "https://youtu.be/two",
                        "https://youtu.be/three",
                    )
                )

        records = coordinator.list()
        self.assertEqual(batch.size, 3)
        self.assertEqual(len(batch.tasks), 3)
        self.assertEqual(len({record.batch_id for record in records}), 1)
        self.assertEqual([record.batch_position for record in records], [1, 2, 3])
        self.assertTrue(all(record.batch_size == 3 for record in records))
        self.assertTrue(all(record.status == "queued" for record in records))

    def test_batch_continues_after_one_song_cannot_be_scheduled(self) -> None:
        store = _DownloadStore()
        attempts: list[str] = []

        def start(url, *, on_progress, on_complete):
            attempts.append(url)
            if url.endswith("/bad"):
                raise OSError("could not schedule this song")
            task = DownloadTask(UUID(int=len(attempts)))
            on_progress(
                DownloadProgress(
                    task,
                    url,
                    DownloadStatus.DOWNLOADING,
                    media_title=f"Resolved {len(attempts)}",
                )
            )
            return task

        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(download_directory=directory)
            with patch("musicplayer.application.downloads.Downloader") as downloader:
                downloader.return_value.start.side_effect = start
                coordinator = DownloadCoordinator(_Manager(), store, settings)  # ty: ignore[invalid-argument-type]
                batch = coordinator.start_urls(
                    (
                        "https://youtu.be/one",
                        "https://youtu.be/bad",
                        "https://youtu.be/three",
                    )
                )

        records_by_url = {record.url: record for record in coordinator.list()}
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(batch.tasks), 2)
        self.assertEqual(records_by_url["https://youtu.be/bad"].status, "failed")
        self.assertIn(
            "could not schedule",
            records_by_url["https://youtu.be/bad"].error,
        )
        self.assertEqual(records_by_url["https://youtu.be/three"].status, "downloading")
        self.assertEqual(records_by_url["https://youtu.be/three"].title, "Resolved 3")

    def test_direct_and_batch_downloads_reject_non_youtube_urls(self) -> None:
        store = _DownloadStore()
        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(download_directory=directory)
            with patch("musicplayer.application.downloads.Downloader") as downloader:
                coordinator = DownloadCoordinator(_Manager(), store, settings)  # ty: ignore[invalid-argument-type]

                with self.assertRaisesRegex(ValueError, "Only YouTube"):
                    coordinator.start_url("https://example.test/song")
                with self.assertRaisesRegex(ValueError, "Only YouTube"):
                    coordinator.start_urls(
                        ("https://youtu.be/allowed", "https://example.test/blocked")
                    )

        downloader.return_value.start.assert_not_called()
        self.assertEqual(store.records, [])

    def test_equivalent_youtube_url_cannot_start_a_duplicate_active_download(
        self,
    ) -> None:
        store = _DownloadStore()

        def start(url, *, on_progress, on_complete):
            task = DownloadTask(UUID(int=1))
            on_progress(DownloadProgress(task, url, DownloadStatus.QUEUED))
            return task

        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(download_directory=directory)
            with patch("musicplayer.application.downloads.Downloader") as downloader:
                downloader.return_value.start.side_effect = start
                coordinator = DownloadCoordinator(_Manager(), store, settings)  # ty: ignore[invalid-argument-type]
                coordinator.start_url("https://youtu.be/video-id")

                self.assertTrue(
                    coordinator.is_source_active(
                        "https://www.youtube.com/watch?v=video-id"
                    )
                )
                with self.assertRaisesRegex(DuplicateDownloadError, "downloading"):
                    coordinator.start_url("https://www.youtube.com/watch?v=video-id")

        downloader.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
