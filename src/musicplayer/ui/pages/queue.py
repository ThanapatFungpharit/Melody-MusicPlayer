from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class QueuePage(_Base):
    """Playback queue page UI."""

    def _queue_view(self) -> ft.Control:
        return self.views._queue_view(self)
