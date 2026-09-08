from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from musicplayer.ui.components.common import (
    _artwork,
    _empty_state,
    _track_credit,
    _track_title,
)
from musicplayer.ui.desktop.controls import reorder_handle

if TYPE_CHECKING:
    from musicplayer.app import MusicPlayerApp


def _queue_view(app: MusicPlayerApp) -> ft.Control:
    queue_rows: list[ft.Control] = []
    for index, track_id in enumerate(app.playback.queue.items):
        try:
            track = app.manager.get_track(track_id)
        except (KeyError, TypeError, ValueError):
            continue
        details = app.library.details(track_id)
        current = index == app.playback.queue.current_index
        queue_rows.append(
            ft.Container(
                ft.Row(
                    [
                        reorder_handle(current=current),
                        _artwork(details.thumbnail, 48),
                        ft.Column(
                            [
                                ft.Text(
                                    _track_title(track),
                                    weight=ft.FontWeight.BOLD
                                    if current
                                    else ft.FontWeight.W_500,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    size=14,
                                ),
                                ft.Text(
                                    _track_credit(details),
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    size=12,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                            ],
                            spacing=3,
                            expand=True,
                        ),
                        ft.IconButton(
                            ft.Icons.PLAY_ARROW_ROUNDED,
                            tooltip="Play from here",
                            on_click=lambda _, ids=tuple(app.playback.queue.items), pos=index: (
                                app.playback.play_tracks(ids, start_index=pos)
                            ),
                        ),
                        ft.IconButton(
                            ft.Icons.CLOSE_ROUNDED,
                            tooltip="Remove from queue",
                            on_click=lambda _, pos=index: (
                                app.playback.remove_queue_item(pos)
                            ),
                        ),
                    ]
                ),
                padding=10,
                margin=ft.Margin.only(bottom=6),
                border_radius=14,
                bgcolor=ft.Colors.PRIMARY_CONTAINER
                if current
                else ft.Colors.SURFACE_CONTAINER_LOW,
                key=f"queue:{index}:{track_id}",
            )
        )
    if queue_rows:
        rows: ft.Control = ft.ReorderableListView(
            controls=queue_rows,
            show_default_drag_handles=False,
            mouse_cursor=ft.MouseCursor.GRAB,
            on_reorder=lambda event: app.playback.reorder_queue(
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
            app._context_header("Queue", f"{len(queue_rows)} tracks • Drag to reorder"),
            ft.TextButton(
                "Clear upcoming",
                icon=ft.Icons.CLEAR_ALL_ROUNDED,
                on_click=lambda _: app.playback.clear_queue(),
                disabled=len(app.playback.queue.items) <= 1,
            ),
            ft.Row(
                [
                    ft.TextButton(
                        "Shuffle on" if app.playback.queue.shuffle else "Shuffle off",
                        icon=ft.Icons.SHUFFLE_ROUNDED,
                        on_click=lambda _: app.playback.toggle_shuffle(),
                    ),
                    ft.TextButton(
                        f"Repeat {app.playback.queue.repeat.value}",
                        icon=ft.Icons.REPEAT_ROUNDED,
                        on_click=lambda _: app.playback.cycle_repeat(),
                    ),
                ]
            ),
            rows,
        ],
        spacing=16,
        expand=True,
    )
