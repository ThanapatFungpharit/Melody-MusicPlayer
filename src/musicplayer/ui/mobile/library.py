from __future__ import annotations

import flet as ft

from musicplayer.ui.components.common import _track_credit, _track_title
from musicplayer.ui.mobile.controls import (
    heading,
    icon_button,
    more,
    scroll_page,
    track_row,
)


def _library_view(app) -> ft.Control:
    app.library_mode = "tracks"
    app.library_query = ft.TextField(
        value=app.library_query_text,
        hint_text="Find in your library",
        prefix_icon=ft.Icons.SEARCH_ROUNDED,
        on_change=app._library_query_changed,
        expand=True,
    )
    app.library_list = ft.Column(spacing=6)
    app._refresh_library_list(update=False)
    has_visible_tracks = bool(app._visible_library_tracks())
    actions = [
        ft.PopupMenuItem(
            content="Add local audio files",
            icon=ft.Icons.LIBRARY_ADD_ROUNDED,
            on_click=app._pick_local_files,
        ),
        ft.PopupMenuItem(
            content="Add these tracks to queue",
            icon=ft.Icons.ADD_TO_QUEUE_ROUNDED,
            disabled=not has_visible_tracks,
            on_click=lambda _: app._queue_visible_library(),
        ),
    ]
    sort_actions = [
        ft.PopupMenuItem(
            content=label, on_click=lambda _, value=key: app._set_library_sort(value)
        )
        for key, label in (
            ("recent", "Recently added"),
            ("oldest", "Oldest first"),
            ("title", "Title"),
        )
    ]
    shuffle_button = icon_button(
        ft.Icons.SHUFFLE_ROUNDED,
        "Shuffle visible tracks",
        lambda _: app._play_visible_library(shuffle=True),
    )
    shuffle_button.disabled = not has_visible_tracks
    return scroll_page(
        [
            heading(
                "Library",
                "Music saved on this device",
                more(app, "Library actions", actions),
            ),
            ft.Row(
                [app.library_query, more(app, "Sort tracks", sort_actions)], spacing=4
            ),
            ft.Row(
                [
                    ft.Button(
                        "Play all",
                        icon=ft.Icons.PLAY_ARROW_ROUNDED,
                        height=48,
                        expand=True,
                        disabled=not has_visible_tracks,
                        on_click=lambda _: app._play_visible_library(),
                    ),
                    shuffle_button,
                ]
            ),
            app.library_list,
        ]
    )


def _library_track_row(app, track, index, playlists=None) -> ft.Control:
    details = app.library.details(track.id)
    title = _track_title(track)
    return track_row(
        title,
        _track_credit(details),
        details.thumbnail,
        lambda _: app.playback.play_track(str(track.id)),
        more(app, title, app._library_track_menu(track, index, playlists)),
        cache=getattr(app, "thumbnails", None),
    )
