from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock

from musicplayer.app import MusicPlayerApp


class _DrawerPage:
    def __init__(self) -> None:
        self.show_count = 0
        self.close_count = 0

    async def show_drawer(self) -> None:
        self.show_count += 1

    async def close_drawer(self) -> None:
        self.close_count += 1


class NavigationTests(unittest.TestCase):
    def _app(self) -> MusicPlayerApp:
        app = MusicPlayerApp.__new__(MusicPlayerApp)
        app.page = _DrawerPage()  # ty: ignore[invalid-assignment]
        app.compact_layout = True
        return app

    def test_mobile_drawer_uses_current_no_argument_async_api(self) -> None:
        app = self._app()

        asyncio.run(app._open_mobile_drawer())
        asyncio.run(app._close_mobile_drawer())

        self.assertEqual(app.page.show_count, 1)  # ty: ignore[unresolved-attribute]
        self.assertEqual(app.page.close_count, 1)  # ty: ignore[unresolved-attribute]

    def test_mobile_drawer_is_not_opened_for_desktop_layout(self) -> None:
        app = self._app()
        app.compact_layout = False

        asyncio.run(app._open_mobile_drawer())

        self.assertEqual(app.page.show_count, 0)  # ty: ignore[unresolved-attribute]

    def test_drawer_selection_navigates_then_closes(self) -> None:
        app = self._app()
        app.navigate = Mock()
        event = type(
            "DrawerEvent", (), {"control": type("Control", (), {"selected_index": 2})()}
        )()

        asyncio.run(app._navigate_from_drawer(event))

        app.navigate.assert_called_once_with(2)
        self.assertEqual(app.page.close_count, 1)  # ty: ignore[unresolved-attribute]

    def test_drawer_panel_shortcuts_close_before_opening(self) -> None:
        app = self._app()
        app._open_downloads_panel = Mock()
        app._open_settings_panel = Mock()

        asyncio.run(app._open_downloads_from_drawer(None))
        asyncio.run(app._open_settings_from_drawer(None))

        self.assertEqual(app.page.close_count, 2)  # ty: ignore[unresolved-attribute]
        app._open_downloads_panel.assert_called_once_with()
        app._open_settings_panel.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
