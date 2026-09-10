from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from musicplayer.app import AppResources
from musicplayer.application.library_service import LibraryService
from musicplayer.application.models import AppSettings, DownloadRecord
from musicplayer.application.store import ApplicationStore
from musicplayer.core.library import MusicManager


class StartupTests(unittest.TestCase):
    def test_launch_imports_do_not_load_the_network_updater(self) -> None:
        script = (
            "import sys; import main; import musicplayer.__main__; "
            "assert 'musicplayer.yt_dlp_updater' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_resource_load_uses_availability_without_reconciliation_or_hashing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music = root / "music"
            music.mkdir()
            audio = music / "song.mp3"
            audio.write_bytes(b"audio")
            manager = MusicManager(root / "library.mmdb", music)
            track_id = manager.add_track(audio, source="https://youtu.be/song")
            store = ApplicationStore(root / "state.json")
            store.save_settings(AppSettings(download_directory=str(music)))
            store.save_downloads(
                [
                    DownloadRecord(
                        id="00000000-0000-0000-0000-000000000001",
                        url="https://youtu.be/song",
                        title="Song",
                        status="completed",
                        track_ids=[str(track_id)],
                    )
                ]
            )

            with (
                patch("musicplayer.app._application_data_directory", return_value=root),
                patch.object(
                    LibraryService,
                    "reconcile",
                    side_effect=AssertionError("startup reconciliation is forbidden"),
                ),
                patch.object(
                    MusicManager,
                    "check_track_integrity",
                    side_effect=AssertionError("startup hashing is forbidden"),
                ),
            ):
                resources = AppResources.load()

            self.assertEqual(resources.available_track_ids, {str(track_id)})
            self.assertEqual(resources.downloads.list()[0].status, "completed")
            resources.downloads.shutdown(wait=False)


if __name__ == "__main__":
    unittest.main()
