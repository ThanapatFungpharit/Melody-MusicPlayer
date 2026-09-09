import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from musicplayer.application.downloads import DownloadCoordinator
from musicplayer.application.library_service import LibraryService
from musicplayer.application.models import AppSettings, TrackDetails
from musicplayer.application.store import ApplicationStore
from musicplayer.core import storage
from musicplayer.core.downloader.receipts import (
    file_digest,
    receipt_path,
    write_receipt,
)
from musicplayer.core.library import MusicManager
from musicplayer.core.storage import atomic_copy, exclusive_file_lock


class StateRecoveryTests(unittest.TestCase):
    def test_directory_flush_failure_keeps_memory_and_committed_state_in_agreement(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            settings = store.settings
            settings.volume = 37

            def unsupported_barrier(path):
                with (
                    patch.object(storage.os, "name", "posix"),
                    patch.object(
                        storage.os,
                        "open",
                        side_effect=OSError("directory fsync unavailable"),
                    ),
                ):
                    storage.sync_directory(path)

            with (
                patch(
                    "musicplayer.application.store.sync_directory",
                    side_effect=unsupported_barrier,
                ),
                self.assertLogs("musicplayer.core.storage", level="WARNING"),
            ):
                store.save_settings(settings)
            self.assertEqual(store.settings.volume, 37)
            self.assertEqual(ApplicationStore(store.path).settings.volume, 37)

    def test_handoff_repairs_registration_without_secondary_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "song.mp3"
            file.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", root)
            track_id = manager.add_track(file, source="https://youtu.be/abc")
            task_id = uuid4()
            write_receipt(
                root,
                task_id,
                "https://youtu.be/abc",
                [{"filename": file.name, "sha256": file_digest(file)}],
            )
            store = ApplicationStore(root / "state.json")
            coordinator = DownloadCoordinator(
                manager, store, AppSettings(download_directory=str(root))
            )
            coordinator.shutdown()
            self.assertEqual(len(manager.list_tracks()), 1)
            self.assertEqual(store.track_details(str(track_id)).source_name, "YouTube")
            self.assertFalse(receipt_path(root, task_id).exists())

    def test_completed_bytes_are_registered_after_process_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            file = music / "song.mp3"
            file.write_bytes(b"verified completed audio")
            task_id = uuid4()
            write_receipt(
                music,
                task_id,
                "https://youtu.be/abc",
                [{"filename": file.name, "sha256": file_digest(file)}],
            )
            manager = MusicManager(root / "library.mmdb", music)
            store = ApplicationStore(root / "state.json")
            coordinator = DownloadCoordinator(
                manager, store, AppSettings(download_directory=str(music))
            )
            self.addCleanup(coordinator.shutdown)
            self.assertEqual(len(manager.list_tracks()), 1)
            record = coordinator.get(str(task_id))
            assert record is not None
            self.assertEqual(record.status, "completed")
            self.assertFalse(receipt_path(music, task_id).exists())
            self.assertEqual(file.read_bytes(), b"verified completed audio")

    def test_corrupt_receipt_file_is_preserved_without_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "song.mp3"
            file.write_bytes(b"damaged")
            task_id = uuid4()
            write_receipt(
                root,
                task_id,
                "https://youtu.be/abc",
                [{"filename": file.name, "sha256": "0" * 64}],
            )
            manager = MusicManager(root / "library.mmdb", root)
            coordinator = DownloadCoordinator(
                manager,
                ApplicationStore(root / "state.json"),
                AppSettings(download_directory=str(root)),
            )
            self.addCleanup(coordinator.shutdown)
            self.assertEqual(manager.list_tracks(), ())
            self.assertTrue(file.exists())
            self.assertTrue(receipt_path(root, task_id).exists())

    def test_reconciliation_removes_stale_metadata_and_preserves_orphans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MusicManager(root / "library.mmdb", root)
            store = ApplicationStore(root / "state.json")
            store.save_track_details("missing-record", TrackDetails(favorite=True))
            orphan = root / "orphan.mp3"
            orphan.write_bytes(b"keep me")
            report = LibraryService(manager, store).reconcile()
            self.assertEqual(report.unregistered_files, (str(orphan),))
            self.assertEqual(store.favorite_track_ids(), frozenset())
            self.assertTrue(orphan.exists())

    def test_failed_atomic_copy_does_not_expose_partial_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = (
                Path(directory) / "source",
                Path(directory) / "destination",
            )
            source.write_bytes(b"source")
            destination.write_bytes(b"original")

            def fail_copy(input_file, output, length):
                output.write(b"partial")
                raise OSError("disk full")

            with (
                patch(
                    "musicplayer.core.storage.shutil.copyfileobj", side_effect=fail_copy
                ),
                self.assertRaises(OSError),
            ):
                atomic_copy(source, destination)
            self.assertEqual(destination.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_lock_content_is_not_used_as_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            path.write_text(json.dumps({"old_process": 1}))
            with (
                exclusive_file_lock(path),
                self.assertRaises(OSError),
                exclusive_file_lock(path),
            ):
                self.fail("Second owner acquired the same lock")
            with exclusive_file_lock(path):
                pass
