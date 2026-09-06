from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import flet as ft

from musicplayer.app import MusicPlayerApp
from musicplayer.application.models import AppSettings, TrackDetails
from musicplayer.application.store import ApplicationStore
from musicplayer.core.library import MusicManager


class _Page:
    theme_mode: ft.ThemeMode | None = None

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


class _Playback:
    def __init__(self) -> None:
        self.library_clear_count = 0
        self.applied_settings: AppSettings | None = None

    def clear_library_state(self) -> None:
        self.library_clear_count += 1

    def apply_settings(self, settings: AppSettings) -> None:
        self.applied_settings = settings


class DataManagementUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.music = self.root / "music"
        self.music.mkdir()
        self.data = self.root / "data"
        self.data.mkdir()
        self.manager = MusicManager(self.data / "library.mmdb", self.music)
        self.store = ApplicationStore(self.data / "state.json")
        self.app = MusicPlayerApp.__new__(MusicPlayerApp)
        self.app.page = _Page()  # ty: ignore[invalid-assignment]
        self.app.data_directory = self.data
        self.app.manager = self.manager
        self.app.store = self.store
        self.app.settings = self.store.settings
        self.app.playback = _Playback()  # ty: ignore[invalid-assignment]
        self.app.selected_playlist_id = None
        self.app.active_panel = None
        self.app.context_sheet = None
        self.app._show_message = Mock()
        self.app._show_error = Mock()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _add_track_and_playlist(self) -> tuple[str, Path]:
        path = self.music / "song.mp3"
        path.write_bytes(b"audio")
        track_id = self.manager.add_track(path, title="Song")
        playlist_id = self.manager.create_playlist("Focus")
        self.manager.add_to_playlist(playlist_id, track_id)
        return str(track_id), path

    def test_every_data_action_opens_a_modal_confirmation_before_changes(self) -> None:
        track_id, _ = self._add_track_and_playlist()
        expected_titles = {
            "library": "Clear library?",
            "playlists": "Clear playlists?",
            "settings": "Reset settings?",
            "everything": "Reset everything?",
        }

        for action, expected_title in expected_titles.items():
            self.app._confirm_data_action(action)
            dialog = self.app.page.dialogs[-1]  # ty: ignore[unresolved-attribute]

            self.assertIsInstance(dialog, ft.AlertDialog)
            self.assertTrue(dialog.modal)
            self.assertEqual(dialog.title.value, expected_title)
            self.assertEqual(len(dialog.actions), 2)
            self.assertTrue(self.manager.has_track(track_id))
            self.assertEqual(len(self.manager.list_playlists()), 1)
            dialog.actions[0].on_click(None)

        self.assertEqual(self.app.page.pop_count, 4)  # ty: ignore[unresolved-attribute]

    def test_reset_settings_removes_only_managed_cookie_and_applies_defaults(
        self,
    ) -> None:
        track_id, path = self._add_track_and_playlist()
        managed_cookie = self.data / "cookies.txt"
        managed_cookie.write_text("private cookie", encoding="utf-8")
        self.app.settings.theme = "light"
        self.app.settings.volume = 12
        self.app.settings.cookie_file = str(managed_cookie)
        self.store.save_settings(self.app.settings)
        self.store.set("search_history", ["keep search"])
        self.store.set("downloads", [{"id": "keep download"}])

        self.app._confirm_data_action("settings")
        self.app.page.dialogs[-1].actions[-1].on_click(None)  # ty: ignore[unresolved-attribute]

        self.assertEqual(self.app.settings, AppSettings())
        self.assertEqual(self.store.settings, AppSettings())
        self.assertIs(self.app.playback.applied_settings, self.app.settings)  # ty: ignore[unresolved-attribute]
        self.assertFalse(managed_cookie.exists())
        self.assertTrue(self.manager.has_track(track_id))
        self.assertEqual(len(self.manager.list_playlists()), 1)
        self.assertTrue(path.exists())
        self.assertEqual(self.store.get("search_history"), ["keep search"])
        self.assertEqual(self.store.get("downloads"), [{"id": "keep download"}])

        external_cookie = self.root / "external-cookies.txt"
        external_cookie.write_text("user-owned cookie", encoding="utf-8")
        self.app.settings.cookie_file = str(external_cookie)
        self.store.save_settings(self.app.settings)
        self.app._confirm_data_action("settings")
        self.app.page.dialogs[-1].actions[-1].on_click(None)  # ty: ignore[unresolved-attribute]

        self.assertTrue(external_cookie.exists())
        self.assertEqual(self.app.settings, AppSettings())
        self.app._show_error.assert_not_called()  # ty: ignore[unresolved-attribute]

    def test_reset_everything_clears_scoped_data_and_keeps_unrelated_data(self) -> None:
        track_id, path = self._add_track_and_playlist()
        self.store.save_track_details(track_id, TrackDetails(favorite=True))
        self.store.set("recent_tracks", [track_id])
        self.store.set("playback_history", [track_id])
        self.store.set("search_history", ["keep search"])
        self.store.set("downloads", [{"id": "keep download"}])
        self.app.settings.theme = "light"
        self.store.save_settings(self.app.settings)

        self.app._confirm_data_action("everything")
        dialog = self.app.page.dialogs[-1]  # ty: ignore[unresolved-attribute]
        self.assertEqual(dialog.actions[-1].bgcolor, ft.Colors.ERROR)
        dialog.actions[-1].on_click(None)

        self.assertEqual(self.manager.list_tracks(), ())
        self.assertEqual(self.manager.list_playlists(), ())
        self.assertEqual(self.store.get("track_details"), {})
        self.assertEqual(self.store.get("recent_tracks"), [])
        self.assertEqual(self.store.get("playback_history"), [])
        self.assertEqual(self.store.settings, AppSettings())
        self.assertEqual(self.store.get("search_history"), ["keep search"])
        self.assertEqual(self.store.get("downloads"), [{"id": "keep download"}])
        self.assertTrue(path.exists())
        self.assertEqual(self.app.playback.library_clear_count, 1)  # ty: ignore[unresolved-attribute]
        self.app._show_error.assert_not_called()  # ty: ignore[unresolved-attribute]


if __name__ == "__main__":
    unittest.main()
