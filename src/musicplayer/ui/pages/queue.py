from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from musicplayer.ui.components.common import (
    _artwork,
    _empty_state,
    _page_header,
    _track_credit,
    _track_title,
)

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class QueuePage(_Base):
    """Playback queue page UI."""

    def _queue_view(self) -> ft.Control:
        queue_rows: list[ft.Control] = []
        for index, track_id in enumerate(self.playback.queue.items):
            if not self.manager.has_track(track_id):
                continue
            track = self.manager.get_track(track_id)
            details = self.library.details(track_id)
            current = index == self.playback.queue.current_index
            queue_rows.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.VOLUME_UP_ROUNDED
                                if current
                                else ft.Icons.DRAG_INDICATOR_ROUNDED,
                                color=ft.Colors.PRIMARY
                                if current
                                else ft.Colors.ON_SURFACE_VARIANT,
                                width=28,
                            ),
                            _artwork(details.thumbnail, 48),
                            ft.Column(
                                [
                                    ft.Text(
                                        _track_title(track),
                                        weight=ft.FontWeight.BOLD
                                        if current
                                        else ft.FontWeight.W_500,
                                        max_lines=1,
                                    ),
                                    ft.Text(
                                        _track_credit(details),
                                        size=12,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=3,
                                expand=True,
                            ),
                            ft.Text(
                                "Now playing" if current else "Up next",
                                size=11,
                                color=ft.Colors.PRIMARY
                                if current
                                else ft.Colors.ON_SURFACE_VARIANT,
                            ),
                            ft.IconButton(
                                ft.Icons.CLOSE_ROUNDED,
                                tooltip="Remove from queue",
                                on_click=lambda _, pos=index: (
                                    self.playback.remove_queue_item(pos)
                                ),
                            ),
                        ]
                    ),
                    padding=10,
                    border_radius=14,
                    bgcolor=ft.Colors.PRIMARY_CONTAINER
                    if current
                    else ft.Colors.SURFACE_CONTAINER,
                    key=f"queue:{index}:{track_id}",
                )
            )
        if queue_rows:
            rows: ft.Control = ft.ReorderableListView(
                controls=queue_rows,
                spacing=7,
                show_default_drag_handles=True,
                mouse_cursor=ft.MouseCursor.GRAB,
                on_reorder=lambda event: self.playback.reorder_queue(
                    event.old_index, event.new_index
                ),
                expand=True,
            )
        else:
            rows = _empty_state(
                ft.Icons.QUEUE_MUSIC_ROUNDED,
                "Your temporary playback queue is empty.",
                expand=True,
            )
        return ft.Column(
            [
                ft.Row(
                    [
                        _page_header(
                            "Playback queue",
                            "Temporary play order; your playlists remain unchanged.",
                            expand=True,
                        ),
                        ft.TextButton(
                            "Clear upcoming",
                            icon=ft.Icons.CLEAR_ALL_ROUNDED,
                            on_click=lambda _: self.playback.clear_queue(),
                            disabled=len(self.playback.queue.items) <= 1,
                        ),
                    ]
                ),
                ft.Row(
                    [
                        ft.TextButton(
                            "Shuffle on"
                            if self.playback.queue.shuffle
                            else "Shuffle off",
                            icon=ft.Icons.SHUFFLE_ROUNDED,
                            on_click=lambda _: self.playback.toggle_shuffle(),
                        ),
                        ft.TextButton(
                            f"Repeat {self.playback.queue.repeat.value}",
                            icon=ft.Icons.REPEAT_ROUNDED,
                            on_click=lambda _: self.playback.cycle_repeat(),
                        ),
                    ]
                ),
                ft.Divider(height=1),
                rows,
            ],
            spacing=16,
            expand=True,
        )
