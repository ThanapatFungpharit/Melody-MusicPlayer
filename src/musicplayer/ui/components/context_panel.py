from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import flet as ft

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class ContextPanel(_Base):
    """Shared bottom-sheet host for queue, downloads, and settings pages."""

    def _open_queue_panel(self) -> None:
        self._show_context_panel("queue", self._queue_view())

    def _open_downloads_panel(self) -> None:
        self._show_context_panel("downloads", self._downloads_view())

    def _open_settings_panel(self) -> None:
        self._show_context_panel("settings", self._settings_view())

    def _show_context_panel(self, name: str, content: ft.Control) -> None:
        self.active_panel = name
        panel_width = self._context_panel_width()
        self.context_panel_body = ft.Container(
            content=content,
            width=panel_width,
            height=self._context_panel_height(),
            padding=self._context_panel_padding(),
        )
        self.context_sheet = ft.BottomSheet(
            content=self.context_panel_body,
            draggable=True,
            show_drag_handle=True,
            dismissible=True,
            fullscreen=True,
            scrollable=self.compact_layout,
            use_safe_area=True,
            maintain_bottom_view_insets_padding=True,
            size_constraints=ft.BoxConstraints(
                min_width=panel_width,
                max_width=panel_width,
            ),
            on_dismiss=lambda _: self._context_panel_dismissed(),
        )
        self.page.show_dialog(self.context_sheet)

    def _context_panel_height(self, height: float | None = None) -> float:
        available = float(height or self.page.height or self.page.window.height or 900)
        # On compact / mobile, maximise usable space; on desktop, keep a
        # comfortable margin.  A 280px floor prevents the sheet from being
        # too short on very small devices.
        if self.compact_layout:
            return max(280, available - 48)
        return max(280, min(900, available - 42))

    def _context_panel_width(self, width: float | None = None) -> float:
        return float(width or self.page.width or self.page.window.width or 1540)

    def _context_panel_padding(self, width: float | None = None) -> ft.Padding:
        available = float(width or self.page.width or self.page.window.width or 1540)
        if self.compact_layout:
            horizontal = 16
        else:
            horizontal = 40 if available >= 1280 else (28 if available >= 900 else 20)
        return ft.Padding.only(
            left=horizontal,
            top=12 if self.compact_layout else 8,
            right=horizontal,
            bottom=16 if self.compact_layout else 24,
        )

    def _close_context_panel(self) -> None:
        try:
            self.page.pop_dialog()
        finally:
            self._context_panel_dismissed()

    def _context_panel_dismissed(self) -> None:
        self.active_panel = None
        self.context_sheet = None

    def _refresh_context_panel(self) -> None:
        if not self.context_sheet or not self.active_panel:
            return
        if self.active_panel == "queue":
            content = self._queue_view()
        elif self.active_panel == "downloads":
            content = self._downloads_view()
        elif self.active_panel == "settings":
            content = self._settings_view()
        else:
            return
        self.context_panel_body.content = content
        try:
            self.page.update(self.context_panel_body)
        except Exception:
            logger.debug("Context panel refresh skipped", exc_info=True)

    def _refresh_download_badge(self) -> None:
        if not hasattr(self, "download_button"):
            return
        active = self.downloads.active_count()
        self.download_button.badge = str(active) if active else None
        self.download_button.tooltip = (
            f"Downloads — {active} active (Ctrl+D)" if active else "Downloads (Ctrl+D)"
        )
        try:
            self.page.update(self.download_button)
        except Exception:
            logger.debug("Download indicator refresh skipped", exc_info=True)
