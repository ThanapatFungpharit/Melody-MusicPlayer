from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft

from musicplayer.app import MusicPlayerApp
from musicplayer.application.models import AppSettings
from musicplayer.ui.theme import card


class ThemeTests(unittest.TestCase):
    def test_card_forwards_control_key(self) -> None:
        result = card(ft.Text("Track"), key="playlist:0:track")

        self.assertEqual(result.key, "playlist:0:track")

    def test_theme_selector_previews_mode_and_keeps_draft_when_settings_refresh(
        self,
    ) -> None:
        app = MusicPlayerApp.__new__(MusicPlayerApp)
        app.page = SimpleNamespace(  # ty: ignore[invalid-assignment]
            theme_mode=None,
            update=Mock(),
        )
        app.settings = AppSettings(theme="dark", accent_color="violet")
        app._refresh_context_panel = Mock()
        app._reapply_accent = Mock()

        app._build_appearance_settings()
        app.settings_theme.value = "light"
        app._theme_changed(SimpleNamespace(control=app.settings_theme))

        self.assertEqual(app.page.theme_mode, ft.ThemeMode.LIGHT)
        self.assertEqual(app._theme_draft, "light")
        app._build_appearance_settings()
        self.assertEqual(app.settings_theme.value, "light")

    def test_accent_selection_survives_panel_rebuild_and_is_applied(self) -> None:
        app = MusicPlayerApp.__new__(MusicPlayerApp)
        app.page = SimpleNamespace(update=Mock())  # ty: ignore[invalid-assignment]
        app.settings = AppSettings(accent_color="violet")
        app._refresh_context_panel = Mock()
        app._reapply_accent = Mock()

        app._build_appearance_settings()
        app._select_accent("ocean")
        app._build_appearance_settings()

        self.assertEqual(app._accent_draft, "ocean")
        self.assertEqual(app.settings_theme.value, "dark")
        self.assertEqual(app._selected_accent, "ocean")
        app._reapply_accent.assert_called_once()


if __name__ == "__main__":
    unittest.main()
