from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Event
from typing import cast
from unittest.mock import Mock, patch
from uuid import UUID

from mutagen.id3 import APIC, ID3, PictureType

from musicplayer.application.downloads import (
    DownloadCoordinator,
    DuplicateDownloadError,
    _download_options,
    _friendly_download_error,
    extract_download_urls,
)
from musicplayer.application.models import (
    AppSettings,
    DownloadRecord,
    SearchResult,
    TrackDetails,
)
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
from musicplayer.core.library.utils import source_key
from musicplayer.platform_runtime import current_architecture, current_platform


class StubDownloader(Downloader):
    def _run_yt_dlp(self, job, temporary_directory: str) -> None:
        Path(temporary_directory, "downloaded.mp3").write_bytes(b"audio")


class EmptyDownloader(Downloader):
    def _run_yt_dlp(self, job, temporary_directory: str) -> None:
        Path(temporary_directory, "downloaded.mp3").write_bytes(b"")


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

    def test_every_selectable_format_burns_thumbnail_after_metadata(self) -> None:
        for audio_format in ("mp3", "m4a", "opus"):
            with self.subTest(audio_format=audio_format):
                options = _download_options(AppSettings(audio_format=audio_format))
                postprocessors = cast(
                    list[dict[str, object]], options["postprocessors"]
                )

                self.assertTrue(options["writethumbnail"])
                self.assertTrue(options["addmetadata"])
                self.assertEqual(
                    [processor["key"] for processor in postprocessors],
                    ["FFmpegExtractAudio", "FFmpegMetadata", "EmbedThumbnail"],
                )
                self.assertTrue(postprocessors[-1]["already_have_thumbnail"])

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

    def test_background_download_hands_off_thumbnail_bytes(self) -> None:
        class ArtworkDownloader(StubDownloader):
            def _run_yt_dlp(self, job, temporary_directory: str) -> None:
                tags = ID3()
                tags.add(
                    APIC(
                        mime="image/jpeg",
                        type=PictureType.COVER_FRONT,
                        desc="Cover",
                        data=b"\xff\xd8\xff\xe0embedded cover",
                    )
                )
                tags.save(Path(temporary_directory, "downloaded.mp3"))
                Path(temporary_directory, "cover.jpg").write_bytes(
                    b"\xff\xd8\xff\xe0downloaded sidecar"
                )

        with tempfile.TemporaryDirectory() as directory:
            downloader = ArtworkDownloader(directory, max_workers=1)
            task = downloader.start("https://example.test/audio")
            job = downloader._jobs[task.id]
            job.future.result(timeout=3)  # ty: ignore[unresolved-attribute]
            result = downloader.result(task)
            downloader.shutdown()

            assert result is not None
            self.assertEqual(result.artwork, b"\xff\xd8\xff\xe0embedded cover")
            self.assertEqual(list(Path(directory).glob("*.jpg")), [])

    def test_download_fails_if_thumbnail_was_not_burned_into_media(self) -> None:
        class MissingEmbeddedArtworkDownloader(StubDownloader):
            def _run_yt_dlp(self, job, temporary_directory: str) -> None:
                super()._run_yt_dlp(job, temporary_directory)
                Path(temporary_directory, "cover.jpg").write_bytes(
                    b"\xff\xd8\xff\xe0downloaded sidecar"
                )

        with tempfile.TemporaryDirectory() as directory:
            downloader = MissingEmbeddedArtworkDownloader(directory, max_workers=1)
            task = downloader.start("https://example.test/audio")
            job = downloader._jobs[task.id]
            job.future.result(timeout=3)  # ty: ignore[unresolved-attribute]
            result = downloader.result(task)
            downloader.shutdown()

            assert result is not None
            self.assertEqual(result.status, DownloadStatus.FAILED)
            self.assertIn("not embedded in the media file", result.error)
            assert result.failure is not None
            self.assertEqual(result.failure.kind.value, "postprocessing")
            self.assertIn("Cover artwork", result.failure.message)

    def test_empty_audio_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            downloader = EmptyDownloader(directory, max_workers=1)
            task = downloader.start("https://example.test/empty")
            job = downloader._jobs[task.id]
            job.future.result(timeout=3)  # ty: ignore[unresolved-attribute]
            result = downloader.result(task)
            downloader.shutdown()

            self.assertEqual(result.status, DownloadStatus.FAILED)  # ty: ignore[unresolved-attribute]
            self.assertIn("empty audio file", result.error)  # ty: ignore[unresolved-attribute]

    def test_failed_cleanup_does_not_mask_a_terminal_download_result(self) -> None:
        output = Path("locked-output.mp3")
        with (
            patch.object(Path, "unlink", side_effect=PermissionError("locked")),
            self.assertLogs("musicplayer.core.downloader", level="WARNING"),
        ):
            Downloader._remove_files((output,))

    def test_core_downloader_accepts_injected_media_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            downloader = Downloader(
                directory, max_workers=1, options=_download_options(AppSettings())
            )
            try:
                location = Path(downloader._options["ffmpeg_location"])  # ty: ignore[invalid-argument-type]
                self.assertEqual(location.name, current_architecture())
                self.assertEqual(location.parent.name, current_platform())
            finally:
                downloader.shutdown()


class DownloadImportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.music = self.root / "music"
        self.manager = MusicManager(self.root / "library.mmdb", self.music)
        self.store = ApplicationStore(self.root / "state.json")
        self.notifications: list[DownloadRecord] = []
        with patch("musicplayer.application.downloads.Downloader"):
            self.coordinator = DownloadCoordinator(
                self.manager,
                self.store,
                AppSettings(download_directory=str(self.music)),
                on_change=self.notifications.append,
            )
        self.task = DownloadTask(UUID(int=1))
        self.url = "https://youtu.be/imported"
        self.metadata: dict[str, object] = {
            "url": self.url,
            "title": "Downloaded song",
            "source": "YouTube",
            "duration": 123.5,
            "resolve_metadata": True,
        }

    def complete(self, path: Path) -> DownloadRecord:
        self.coordinator._handle_complete(
            DownloadResult(self.task, self.url, DownloadStatus.COMPLETED, (path,)),
            self.metadata,
        )
        return self.coordinator.list()[0]

    def test_new_track_uses_metadata_resolved_during_progress(self) -> None:
        path = self.music / "downloaded.mp3"
        path.write_bytes(b"new audio")
        self.coordinator._handle_progress(
            DownloadProgress(
                self.task,
                self.url,
                DownloadStatus.PROCESSING,
                uploader="Resolved uploader",
                thumbnail="https://example.test/artwork.jpg",
            ),
            self.metadata,
        )

        record = self.complete(path)

        self.assertEqual(record.status, "completed")
        self.assertEqual(record.progress, 1.0)
        self.assertEqual(record.filename, path.name)
        self.assertGreater(record.completed_at, 0)
        track = self.manager.get_track(record.track_ids[0])
        self.assertEqual(track.title, "Downloaded song")
        details = self.store.track_details(str(track.id))
        self.assertEqual(details.uploader, "Resolved uploader")
        self.assertEqual(details.thumbnail, "https://example.test/artwork.jpg")
        self.assertEqual(details.duration, 123.5)
        self.assertEqual(self.coordinator.active_count(), 0)
        self.assertEqual(self.store.downloads()[0].track_ids, [str(track.id)])

    def test_completion_caches_thumbnail_without_rewriting_its_source_url(
        self,
    ) -> None:
        path = self.music / "downloaded.mp3"
        path.write_bytes(b"new audio")
        thumbnail = "https://example.test/artwork.jpg"
        cache = Mock()
        cache.store_bytes.return_value = True
        self.coordinator.thumbnails = cache
        self.coordinator._handle_progress(
            DownloadProgress(
                self.task,
                self.url,
                DownloadStatus.PROCESSING,
                thumbnail=thumbnail,
            ),
            self.metadata,
        )

        self.coordinator._handle_complete(
            DownloadResult(
                self.task,
                self.url,
                DownloadStatus.COMPLETED,
                (path,),
                artwork=b"\xff\xd8\xff\xe0cached cover",
            ),
            self.metadata,
        )

        cache.store_bytes.assert_called_once_with(
            thumbnail, b"\xff\xd8\xff\xe0cached cover"
        )
        record = self.coordinator.list()[0]
        self.assertEqual(record.thumbnail, thumbnail)
        self.assertEqual(
            self.store.track_details(record.track_ids[0]).thumbnail, thumbnail
        )

    def test_source_match_takes_priority_over_content_match(self) -> None:
        source_path = self.music / "source.mp3"
        source_path.write_bytes(b"original source audio")
        source_id = self.manager.add_track(source_path, source=self.url)
        content_path = self.music / "content.mp3"
        content_path.write_bytes(b"matching downloaded audio")
        self.manager.add_track(content_path)
        download_path = self.music / "downloaded.mp3"
        download_path.write_bytes(content_path.read_bytes())

        record = self.complete(download_path)

        self.assertEqual(record.track_ids, [str(source_id)])
        self.assertEqual(record.filename, source_path.name)
        self.assertEqual(source_path.read_bytes(), b"original source audio")
        self.assertTrue(content_path.exists())
        self.assertFalse(download_path.exists())
        self.assertEqual(len(self.manager.list_tracks()), 2)

    def test_completion_keeps_file_that_is_already_managed(self) -> None:
        path = self.music / "managed.mp3"
        path.write_bytes(b"managed audio")
        track_id = self.manager.add_track(path, source=self.url)

        record = self.complete(path)

        self.assertEqual(record.status, "completed")
        self.assertEqual(record.track_ids, [str(track_id)])
        self.assertEqual(path.read_bytes(), b"managed audio")
        self.assertEqual(len(self.manager.list_tracks()), 1)

    def test_import_failure_is_persisted_and_releases_active_source(self) -> None:
        path = self.music / "downloaded.mp3"
        path.write_bytes(b"downloaded audio")
        self.coordinator._handle_progress(
            DownloadProgress(self.task, self.url, DownloadStatus.PROCESSING),
            self.metadata,
        )
        self.assertTrue(self.coordinator.is_source_active(self.url))
        self.notifications.clear()

        with (
            patch.object(
                self.store, "save_track_details", side_effect=OSError("disk full")
            ),
            self.assertLogs("musicplayer.application.downloads", level="ERROR"),
        ):
            record = self.complete(path)

        self.assertEqual(record.status, "failed")
        self.assertEqual(
            record.error, "Downloaded, but could not add to the library: disk full"
        )
        self.assertEqual(record.track_ids, [])
        self.assertEqual(record.completed_at, 0)
        self.assertFalse(self.coordinator.is_source_active(self.url))
        self.assertEqual(self.notifications, [record])
        self.assertEqual(self.store.downloads()[0].status, "failed")
        # A failed enriched-metadata write leaves the registered audio intact.
        self.assertEqual(len(self.manager.list_tracks()), 1)
        self.assertTrue(path.exists())

    def test_failed_and_cancelled_results_do_not_import_audio(self) -> None:
        path = self.music / "downloaded.mp3"
        path.write_bytes(b"downloaded audio")
        for status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            with self.subTest(status=status):
                self.coordinator._handle_complete(
                    DownloadResult(self.task, self.url, status, (path,)),
                    self.metadata,
                )

                record = self.coordinator.list()[0]
                self.assertEqual(record.status, status.value)
                self.assertEqual(record.track_ids, [])
                self.assertEqual(record.completed_at, 0)
                self.assertEqual(self.manager.list_tracks(), ())
                self.assertTrue(path.exists())


class BatchDownloadTests(unittest.TestCase):
    def test_known_source_blocks_only_when_its_managed_file_is_intact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            path = music / "known.mp3"
            path.write_bytes(b"audio")
            source = "https://youtu.be/known-source"
            manager = MusicManager(root / "library.mmdb", music)
            manager.add_track(path, source=source)
            store = ApplicationStore(root / "state.json")
            settings = AppSettings(download_directory=str(music))
            result = SearchResult(
                id="known-source",
                title="Known",
                uploader="Uploader",
                duration=1,
                thumbnail="",
                url=source,
                source="YouTube",
            )
            with patch("musicplayer.application.downloads.Downloader") as downloader:
                coordinator = DownloadCoordinator(manager, store, settings)
                with self.assertRaisesRegex(DuplicateDownloadError, "library"):
                    coordinator.start(result)

                path.unlink()
                task = DownloadTask(UUID(int=1))
                downloader.return_value.start.return_value = task
                self.assertEqual(coordinator.start(result), task)

    def test_redownload_repairs_broken_source_record_without_losing_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            existing_path = music / "old.mp3"
            existing_path.write_bytes(b"original audio")
            replacement_path = music / "redownloaded.mp3"
            replacement_path.write_bytes(b"fresh audio")
            manager = MusicManager(root / "library.mmdb", music)
            source = "https://youtu.be/repair-source"
            existing_id = manager.add_track(existing_path, source=source)
            playlist_id = manager.create_playlist("Kept")
            manager.add_to_playlist(playlist_id, existing_id)
            store = ApplicationStore(root / "state.json")
            store.save_track_details(str(existing_id), TrackDetails(favorite=True))
            existing_path.write_bytes(b"modified and no longer trusted")
            task = DownloadTask(UUID(int=1))
            result = DownloadResult(
                task,
                source,
                DownloadStatus.COMPLETED,
                (replacement_path,),
            )
            with patch("musicplayer.application.downloads.Downloader"):
                coordinator = DownloadCoordinator(
                    manager,
                    store,
                    AppSettings(download_directory=str(music)),
                )

            coordinator._handle_complete(
                result,
                {"url": source, "title": "Replacement", "source": "YouTube"},
            )

            record = coordinator.list()[0]
            self.assertEqual(record.status, "completed")
            self.assertEqual(record.track_ids, [str(existing_id)])
            self.assertEqual(record.filename, replacement_path.name)
            self.assertTrue(manager.track_path(existing_id).samefile(replacement_path))
            self.assertIsNone(manager.check_track_integrity(existing_id))
            self.assertEqual(
                manager.playlist_tracks(playlist_id)[0].id,
                existing_id,
            )
            self.assertTrue(store.track_details(str(existing_id)).favorite)

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
        metadata: dict[str, object] = {
            "url": progress.url,
            "title": "Song",
            "source": "YouTube",
        }
        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(download_directory=directory)
            with patch("musicplayer.application.downloads.Downloader"):
                coordinator = DownloadCoordinator(
                    cast(MusicManager, _Manager()),
                    cast(ApplicationStore, store),
                    settings,
                    on_change=notifications.append,
                )
            with patch(
                "musicplayer.application.downloads.time.monotonic",
                side_effect=(10.0, 10.1, 11.1),
            ):
                coordinator._handle_progress(progress, metadata)
                coordinator._handle_progress(progress, metadata)
                coordinator._handle_progress(progress, metadata)

        self.assertEqual(store.save_count, 2)
        self.assertEqual(len(notifications), 2)

    def test_terminal_progress_remains_active_until_library_import_finishes(
        self,
    ) -> None:
        store = _DownloadStore()
        task = DownloadTask(UUID(int=1))
        progress = DownloadProgress(
            task,
            "https://youtu.be/song",
            DownloadStatus.COMPLETED,
            progress=1.0,
        )
        metadata: dict[str, object] = {
            "url": progress.url,
            "title": "Song",
            "source": "YouTube",
        }
        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(download_directory=directory)
            with patch("musicplayer.application.downloads.Downloader"):
                coordinator = DownloadCoordinator(
                    cast(MusicManager, _Manager()),
                    cast(ApplicationStore, store),
                    settings,
                )
            coordinator._handle_progress(progress, metadata)

        self.assertEqual(coordinator.list()[0].status, "processing")
        self.assertEqual(coordinator.active_count(), 1)

    def test_steady_progress_is_throttled_across_all_active_jobs(self) -> None:
        store = _DownloadStore()
        notifications: list[DownloadRecord] = []
        first = DownloadProgress(
            DownloadTask(UUID(int=1)),
            "https://youtu.be/one",
            DownloadStatus.DOWNLOADING,
            progress=0.1,
        )
        second = DownloadProgress(
            DownloadTask(UUID(int=2)),
            "https://youtu.be/two",
            DownloadStatus.DOWNLOADING,
            progress=0.1,
        )

        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(download_directory=directory)
            with patch("musicplayer.application.downloads.Downloader"):
                coordinator = DownloadCoordinator(
                    cast(MusicManager, _Manager()),
                    cast(ApplicationStore, store),
                    settings,
                    on_change=notifications.append,
                )
            with patch(
                "musicplayer.application.downloads.time.monotonic",
                side_effect=(10.0, 10.0, 10.1, 10.1, 11.1, 11.1),
            ):
                for progress in (first, second, first, second, first, second):
                    coordinator._handle_progress(
                        progress,
                        {
                            "url": progress.url,
                            "title": "Song",
                            "source": "YouTube",
                        },
                    )

        # Both initial status transitions are immediate. The four steady
        # updates share one later persistence/notification allowance.
        self.assertEqual(store.save_count, 3)
        self.assertEqual(len(notifications), 3)

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
                with patch(
                    "musicplayer.application.downloads.source_key",
                    wraps=source_key,
                ) as normalize:
                    self.assertTrue(
                        coordinator.is_source_active(
                            "https://www.youtube.com/watch?v=video-id"
                        )
                    )
                self.assertEqual(normalize.call_count, 1)
                with self.assertRaisesRegex(DuplicateDownloadError, "downloading"):
                    coordinator.start_url("https://www.youtube.com/watch?v=video-id")

        downloader.return_value.start.assert_called_once()

    def test_request_ownership_is_atomic_with_active_source_deduplication(
        self,
    ) -> None:
        store = _DownloadStore()

        def start(url, *, on_progress, on_complete):
            task = DownloadTask(UUID(int=1))
            on_progress(DownloadProgress(task, url, DownloadStatus.QUEUED))
            return task

        result = SearchResult(
            id="video-id",
            title="Song",
            uploader="Uploader",
            duration=1,
            thumbnail="",
            url="https://youtu.be/video-id",
            source="YouTube",
        )
        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(download_directory=directory)
            with patch("musicplayer.application.downloads.Downloader") as downloader:
                downloader.return_value.start.side_effect = start
                coordinator = DownloadCoordinator(_Manager(), store, settings)  # ty: ignore[invalid-argument-type]

                first, first_owned = coordinator.request_with_ownership(result)
                joined, joined_owned = coordinator.request_with_ownership(result)

        self.assertEqual(first, joined)
        self.assertTrue(first_owned)
        self.assertFalse(joined_owned)
        downloader.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
