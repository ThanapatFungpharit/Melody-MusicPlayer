from __future__ import annotations

from typing import TYPE_CHECKING, Any

import flet as ft

from musicplayer.application.models import DownloadRecord
from musicplayer.ui.tasks import page_action

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class DownloadsPage(_Base):
    """Downloads page UI and download-history actions."""

    def _downloads_view(self) -> ft.Control:
        return self.views._downloads_view(self)

    def _batch_overview(self, records: list[DownloadRecord]) -> ft.Control:
        return self.views._batch_overview(self, records)

    def _download_metric(
        self, label: str, value: str, icon: Any, color: Any
    ) -> ft.Control:
        return self.views._download_metric(self, label, value, icon, color)

    def _download_row(self, record: DownloadRecord) -> ft.Control:
        return self.views._download_row(self, record)

    @page_action
    async def _cancel_download(self, record_id: str) -> None:
        try:
            await self.tasks.io(self.downloads.cancel, record_id)
        except ValueError as error:
            self._show_error(str(error))

    @page_action
    async def _retry_download(self, record_id: str) -> None:
        try:
            await self.tasks.io(self.downloads.retry, record_id)
        except (ValueError, OSError) as error:
            self._show_error(str(error))

    @page_action
    async def _clear_downloads(self) -> None:
        await self.tasks.io(self.downloads.clear_finished)
        self._refresh_download_badge()
        if self.active_panel == "downloads":
            self._refresh_context_panel()
