"""Shared destination dispatch. Each shell owns its navigation controls."""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object

NAVIGATION = (
    ("Home", ft.Icons.HOME_ROUNDED, ft.Icons.HOME_OUTLINED),
    ("Search", ft.Icons.SEARCH_ROUNDED, ft.Icons.SEARCH_ROUNDED),
    ("Library", ft.Icons.LIBRARY_MUSIC_ROUNDED, ft.Icons.LIBRARY_MUSIC_OUTLINED),
    ("Playlists", ft.Icons.ALBUM_ROUNDED, ft.Icons.ALBUM_OUTLINED),
)


class NavigationShell(_Base):
    def _build_shell(self) -> None:
        self.shell.build()
        self.navigate(self.selected_navigation)
        self._refresh_player()
        self._refresh_download_badge()

    def navigate(self, index: int) -> None:
        self.selected_navigation = max(0, min(index, len(NAVIGATION) - 1))
        builders = (
            self._home_view,
            self._search_view,
            self._library_view,
            self._playlists_view,
        )
        self.content.content = builders[self.selected_navigation]()
        self.shell.select(self.selected_navigation)
        self.page.update()
