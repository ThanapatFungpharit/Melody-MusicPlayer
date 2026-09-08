from __future__ import annotations

import flet as ft

from musicplayer.ui.components.common import _empty_state, _track_title
from musicplayer.ui.mobile.controls import (
    heading,
    icon_button,
    more,
    scroll_page,
    track_row,
)


def _queue_view(app) -> ft.Control:
    queue = app.playback.queue
    rows = []
    for index, track_id in enumerate(queue.items):
        try:
            track = app.manager.get_track(track_id)
            details = app.library.details(track_id)
        except (KeyError, TypeError, ValueError):
            continue
        current = index == queue.current_index
        actions = [
            ft.PopupMenuItem(
                content="Move up",
                icon=ft.Icons.ARROW_UPWARD_ROUNDED,
                disabled=index == 0,
                on_click=lambda _, i=index: app.playback.reorder_queue(i, i - 1),
            ),
            ft.PopupMenuItem(
                content="Move down",
                icon=ft.Icons.ARROW_DOWNWARD_ROUNDED,
                disabled=index == len(queue.items) - 1,
                on_click=lambda _, i=index: app.playback.reorder_queue(i, i + 2),
            ),
            ft.PopupMenuItem(
                content="Remove from queue",
                icon=ft.Icons.REMOVE_CIRCLE_OUTLINE_ROUNDED,
                on_click=lambda _, i=index: app.playback.remove_queue_item(i),
            ),
        ]
        rows.append(
            track_row(
                _track_title(track),
                "Now playing"
                if current
                else f"{index + 1} · {details.uploader or 'Queued track'}",
                details.thumbnail,
                lambda _, i=index: app.playback.play_tracks(
                    tuple(queue.items), start_index=i
                ),
                more(app, _track_title(track), actions),
                selected=current,
            )
        )
    return scroll_page(
        [
            heading(
                "Queue",
                "Temporary play order",
                icon_button(
                    ft.Icons.ARROW_BACK_ROUNDED,
                    "Back",
                    lambda _: app._close_context_panel(),
                ),
            ),
            ft.TextButton(
                "Clear upcoming",
                height=48,
                icon=ft.Icons.CLEAR_ALL_ROUNDED,
                disabled=len(queue.items) <= 1,
                on_click=lambda _: app.playback.clear_queue(),
            ),
            *rows,
            *(
                []
                if rows
                else [
                    _empty_state(
                        ft.Icons.QUEUE_MUSIC_ROUNDED,
                        "Play music or add tracks to start your queue.",
                    )
                ]
            ),
        ]
    )
