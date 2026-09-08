from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import flet as ft

from musicplayer.app import MusicPlayerApp
from musicplayer.application.models import SearchResult, TrackDetails
from musicplayer.application.providers import RemotePlaylist
from musicplayer.core.downloader import DownloadTask
from musicplayer.core.library import MusicManager
from musicplayer.core.library.utils import source_key


class _Page:
    width = 1540
    height = 960

    def __init__(self) -> None:
        self.dialogs: list[ft.Control] = []
        self.pop_count = 0
        self.update_count = 0

    def show_dialog(self, dialog: ft.Control) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.pop_count += 1

    def update(self, *_: ft.Control) -> None:
        self.update_count += 1


class _Library:
    def details(self, _: object) -> TrackDetails:
        return TrackDetails(uploader="Local artist")


class _Downloads:
    def __init__(self) -> None:
        self.active: set[str] = set()
        self.started: list[SearchResult] = []
        self.cancelled: list[str] = []

    def is_source_active(self, url: str) -> bool:
        return source_key(url) in self.active

    def start(self, result: SearchResult) -> DownloadTask:
        self.started.append(result)
        self.active.add(source_key(result.url))
        return DownloadTask(UUID(int=len(self.started)))

    def cancel(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        if self.started:
            self.active.discard(source_key(self.started[-1].url))
        return True

    def complete(self, url: str) -> None:
        self.active.discard(source_key(url))


class PlaylistFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.music = self.root / "music"
        self.music.mkdir()
        self.manager = MusicManager(self.root / "library.mmdb", self.music)
        self.app = MusicPlayerApp.__new__(MusicPlayerApp)
        self.app.page = _Page()  # ty: ignore[invalid-assignment]
        self.app.manager = self.manager
        self.app.library = _Library()  # ty: ignore[invalid-assignment]
        self.app.downloads = _Downloads()  # ty: ignore[invalid-assignment]
        self.app.selected_navigation = 3
        self.app.navigate = Mock()
        self.app._show_message = Mock()
        self.app._show_error = Mock()
        self.app._initialize_playlists_page()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _add_track(self, filename: str, *, source: str = "") -> str:
        path = self.music / filename
        path.write_bytes(f"audio:{filename}".encode())
        return str(self.manager.add_track(path, source=source, title=path.stem))

    @staticmethod
    def _result(video_id: str, title: str) -> SearchResult:
        return SearchResult(
            id=video_id,
            title=title,
            uploader="Channel",
            duration=120,
            thumbnail="",
            url=f"https://www.youtube.com/watch?v={video_id}",
            source="YouTube",
        )

    def test_bulk_dialog_selects_and_adds_multiple_library_tracks(self) -> None:
        first = self._add_track("first.mp3")
        second = self._add_track("second.mp3")
        third = self._add_track("third.mp3")
        playlist_id = self.manager.create_playlist("Road trip")
        self.manager.add_to_playlist(playlist_id, first)

        self.app._bulk_add_tracks_dialog(self.manager.get_playlist(playlist_id))

        dialog = self.app.page.dialogs[-1]  # ty: ignore[unresolved-attribute]
        select_all = dialog.content.controls[1]
        track_list = dialog.content.controls[3]
        add_button = dialog.actions[-1]
        self.assertEqual(select_all.label, "Select all")
        self.assertEqual(len(track_list.controls), 2)
        self.assertTrue(
            all(isinstance(item, ft.Checkbox) for item in track_list.controls)
        )
        self.assertTrue(add_button.disabled)

        select_all.value = True
        select_all.on_change(None)
        self.assertTrue(all(item.value for item in track_list.controls))
        self.assertFalse(add_button.disabled)
        add_button.on_click(None)

        self.assertEqual(
            {str(track.id) for track in self.manager.playlist_tracks(playlist_id)},
            {first, second, third},
        )
        self.assertEqual(self.app.page.pop_count, 1)  # ty: ignore[unresolved-attribute]
        self.app._show_message.assert_called_once_with("Added 2 tracks to “Road trip”.")  # ty: ignore[unresolved-attribute]

        self.app._bulk_add_tracks_dialog(self.manager.get_playlist(playlist_id))
        repeated_dialog = self.app.page.dialogs[-1]  # ty: ignore[unresolved-attribute]
        self.assertTrue(repeated_dialog.content.controls[1].disabled)
        self.assertTrue(repeated_dialog.actions[-1].disabled)

    def test_import_dialog_explains_deduplication_and_validates_the_url(self) -> None:
        self.app._import_playlist_dialog()

        dialog = self.app.page.dialogs[-1]  # ty: ignore[unresolved-attribute]
        explanation = dialog.content.controls[0]
        field = dialog.content.controls[1]
        self.assertIn("reuse tracks", explanation.value)
        self.assertIn("download only the missing", explanation.value)

        field.value = "https://www.youtube.com/watch?v=single-video"
        dialog.actions[-1].on_click(None)

        self.assertEqual(field.error, "Paste a complete YouTube playlist URL.")
        self.assertEqual(self.app.page.pop_count, 0)  # ty: ignore[unresolved-attribute]

    def test_playlist_import_reuses_local_tracks_and_downloads_only_missing(
        self,
    ) -> None:
        existing = self._add_track(
            "existing.mp3", source="https://youtu.be/already-local"
        )
        missing = self._result("missing", "Missing song")
        remote = RemotePlaylist(
            "Imported mix",
            (
                self._result("already-local", "Existing song"),
                missing,
                self._result("missing", "Duplicate playlist entry"),
            ),
        )

        self.app._begin_playlist_import(remote)

        playlist_id = self.app.selected_playlist_id
        self.assertIsNotNone(playlist_id)
        self.assertEqual(
            [str(track.id) for track in self.manager.playlist_tracks(playlist_id)],  # ty: ignore[invalid-argument-type]
            [existing],
        )
        self.assertEqual([item.id for item in self.app.downloads.started], ["missing"])  # ty: ignore[unresolved-attribute]
        self.assertIsNotNone(self.app.playlist_import_session)

        downloaded_path = self.music / "missing.mp3"
        downloaded_path.write_bytes(b"new audio")
        downloaded = str(
            self.manager.add_track(
                downloaded_path,
                source=missing.url,
                title=missing.title,
            )
        )
        self.app.downloads.complete(missing.url)  # ty: ignore[unresolved-attribute]
        self.app._continue_playlist_import()

        self.assertIsNone(self.app.playlist_import_session)
        self.assertEqual(
            [str(track.id) for track in self.manager.playlist_tracks(playlist_id)],  # ty: ignore[invalid-argument-type]
            [existing, downloaded],
        )
        self.assertEqual(len(self.manager.list_tracks()), 2)
        self.assertEqual(len(list(self.music.glob("*.mp3"))), 2)
        self.app._show_error.assert_not_called()  # ty: ignore[unresolved-attribute]

        # Reimporting creates another playlist that references the same records;
        # it does not schedule downloads or create duplicate library entries.
        self.app._begin_playlist_import(remote)
        repeated_id = self.app.selected_playlist_id
        self.assertEqual(len(self.app.downloads.started), 1)  # ty: ignore[unresolved-attribute]
        self.assertEqual(len(self.manager.list_tracks()), 2)
        self.assertEqual(
            [str(track.id) for track in self.manager.playlist_tracks(repeated_id)],  # ty: ignore[invalid-argument-type]
            [existing, downloaded],
        )

    def test_import_accepts_a_download_record_that_reused_local_file_content(
        self,
    ) -> None:
        existing = self._add_track("content-match.mp3")
        result = self._result("same-content", "Same audio")

        self.app._begin_playlist_import(RemotePlaylist("Content match", (result,)))
        playlist_id = self.app.selected_playlist_id
        self.assertEqual(len(self.app.downloads.started), 1)  # ty: ignore[unresolved-attribute]
        self.app.downloads.complete(result.url)  # ty: ignore[unresolved-attribute]

        self.app._continue_playlist_import(result.url, (existing,))

        self.assertIsNone(self.app.playlist_import_session)
        self.assertEqual(
            [str(track.id) for track in self.manager.playlist_tracks(playlist_id)],  # ty: ignore[invalid-argument-type]
            [existing],
        )
        self.assertEqual(len(self.manager.list_tracks()), 1)

    def test_deleting_an_importing_playlist_cancels_its_owned_download(
        self,
    ) -> None:
        result = self._result("still-downloading", "Pending song")
        self.app._begin_playlist_import(RemotePlaylist("Temporary", (result,)))
        playlist_id = self.app.selected_playlist_id
        playlist = self.manager.get_playlist(playlist_id)  # ty: ignore[invalid-argument-type]

        self.app._delete_playlist_dialog(playlist)
        self.app.page.dialogs[-1].actions[-1].on_click(None)  # ty: ignore[unresolved-attribute]

        self.assertFalse(self.manager.has_playlist(playlist_id))  # ty: ignore[invalid-argument-type]
        self.assertIsNone(self.app.playlist_import_session)
        self.assertFalse(self.app.downloads.is_source_active(result.url))
        self.assertEqual(
            self.app.downloads.cancelled,  # ty: ignore[unresolved-attribute]
            ["00000000-0000-0000-0000-000000000001"],
        )
        self.assertEqual(len(self.manager.list_tracks()), 0)


if __name__ == "__main__":
    unittest.main()
