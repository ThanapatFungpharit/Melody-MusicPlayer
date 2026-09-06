from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import flet as ft

from musicplayer.app import MusicPlayerApp
from musicplayer.application.models import AppSettings

COOKIE_FILE = b"""# Netscape HTTP Cookie File
.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret
"""


class _Page:
    width = 1000
    height = 800
    window = type("Window", (), {"width": 1000, "height": 800})()
    theme_mode: ft.ThemeMode | None = None

    def update(self, *_: ft.Control) -> None:
        pass


class _Providers:
    pass


class _Manager:
    def list_tracks(self) -> tuple[object, ...]:
        return ()


class _Store:
    def __init__(self) -> None:
        self.saved: AppSettings | None = None

    def save_settings(self, settings: AppSettings) -> None:
        self.saved = settings


class _FilePicker:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.arguments: dict[str, object] = {}

    async def pick_files(self, **arguments: object) -> list[ft.FilePickerFile]:
        self.arguments = arguments
        return [
            ft.FilePickerFile(
                id=1,
                name="cookies.txt",
                size=len(self.content),
                bytes=self.content,
            )
        ]


class SettingsCookieUiTests(unittest.TestCase):
    def _app(self, directory: str, content: bytes) -> MusicPlayerApp:
        app = MusicPlayerApp.__new__(MusicPlayerApp)
        app.page = _Page()  # ty: ignore[invalid-assignment]
        app.data_directory = Path(directory) / "app-data"
        app.settings = AppSettings(download_directory=directory)
        app.providers = _Providers()  # ty: ignore[invalid-assignment]
        app.manager = _Manager()  # ty: ignore[invalid-assignment]
        app.store = _Store()  # ty: ignore[invalid-assignment]
        app.file_picker = _FilePicker(content)  # ty: ignore[invalid-assignment]
        app._show_error = Mock()
        app._show_message = Mock()
        app._settings_view()
        return app

    def test_valid_upload_is_selected_with_data_and_saved_privately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._app(directory, COOKIE_FILE)

            asyncio.run(app._pick_cookie_file(None))
            app._save_settings()

            cookie_path = app.data_directory / "cookies.txt"
            self.assertTrue(app.file_picker.arguments["with_data"])  # ty: ignore[unresolved-attribute]
            self.assertEqual(app.file_picker.arguments["allow_multiple"], False)  # ty: ignore[unresolved-attribute]
            self.assertTrue(cookie_path.is_file())
            self.assertEqual(app.settings.cookie_file, str(cookie_path))
            self.assertIs(app.store.saved, app.settings)  # ty: ignore[unresolved-attribute]
            app._show_error.assert_not_called()  # ty: ignore[unresolved-attribute]

    def test_invalid_upload_reports_error_and_is_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._app(directory, b'{"cookies": []}')

            asyncio.run(app._pick_cookie_file(None))

            app._show_error.assert_called_once()  # ty: ignore[unresolved-attribute]
            self.assertIsNone(app._pending_cookie_file)
            self.assertFalse((app.data_directory / "cookies.txt").exists())


if __name__ == "__main__":
    unittest.main()
