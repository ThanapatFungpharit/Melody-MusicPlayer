"""Utility destinations share workflows, not a modal presentation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class ContextPanel(_Base):
    def _open_queue_panel(self) -> None:
        self.shell.open_panel("queue")

    def _open_downloads_panel(self) -> None:
        self.shell.open_panel("downloads")

    def _open_settings_panel(self) -> None:
        self.shell.open_panel("settings")

    def _panel_view(self, name: str):
        return {
            "queue": self._queue_view,
            "downloads": self._downloads_view,
            "settings": self._settings_view,
        }[name]()

    def _close_context_panel(self) -> None:
        self.shell.close_panel()

    def _refresh_context_panel(self) -> None:
        if self.active_panel:
            self.shell.refresh_panel()

    def _refresh_download_badge(self) -> None:
        if hasattr(self, "shell"):
            self.shell.refresh_downloads()
