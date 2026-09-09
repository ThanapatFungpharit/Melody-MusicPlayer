from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from musicplayer.application.contracts import Playlist, Track
from musicplayer.ui.components.common import (
    _artwork,
    _format_duration,
    _track_credit,
    _track_title,
)
from musicplayer.ui.desktop.controls import heading as _page_header
from musicplayer.ui.desktop.controls import hover_surface

if TYPE_CHECKING:
    from musicplayer.app import MusicPlayerApp


def _library_view(app: MusicPlayerApp) -> ft.Control:
    app.library_query = ft.TextField(
        value=app.library_query_text,
        hint_text="Find in your library",
        prefix_icon=ft.Icons.SEARCH_ROUNDED,
        border_radius=14,
        border_color=ft.Colors.TRANSPARENT,
        filled=True,
        fill_color=ft.Colors.SURFACE_CONTAINER_LOW,
        text_size=14,
        height=48,
        on_change=app._library_query_changed,
    )
    app.library_sort = ft.Dropdown(
        value=app.library_sort_key,
        options=[
            ft.DropdownOption(key="recent", text="Recently added"),
            ft.DropdownOption(key="oldest", text="Oldest added"),
            ft.DropdownOption(key="title", text="Title"),
        ],
        border_radius=14,
        border_color=ft.Colors.TRANSPARENT,
        filled=True,
        fill_color=ft.Colors.SURFACE_CONTAINER_LOW,
        text_size=14,
        height=48,
        on_select=app._library_sort_changed,
    )
    app.library_query.col = {"xs": 12, "md": 8}
    app.library_sort.col = {"xs": 12, "md": 4}
    app.library_list = ft.Column(spacing=4)
    app._refresh_library_list(update=False)
    has_visible_tracks = bool(app._visible_library_tracks())
    library_header = _page_header(
        "Library", "Your favorites, downloads, and local discoveries."
    )
    library_header.col = {"xs": 12, "lg": 7}
    library_actions = ft.Container(
        ft.Row(
            [
                ft.TextButton(
                    "Add files",
                    icon=ft.Icons.LIBRARY_ADD_ROUNDED,
                    on_click=app._pick_local_files,
                ),
                ft.Button(
                    "Play all",
                    bgcolor=ft.Colors.PRIMARY,
                    color=ft.Colors.ON_PRIMARY,
                    elevation=0,
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                    height=44,
                    disabled=not has_visible_tracks,
                    on_click=lambda _: app._play_visible_library(),
                ),
                ft.IconButton(
                    ft.Icons.SHUFFLE_ROUNDED,
                    tooltip="Shuffle visible tracks",
                    disabled=not has_visible_tracks,
                    on_click=lambda _: app._play_visible_library(shuffle=True),
                ),
                ft.IconButton(
                    ft.Icons.ADD_TO_QUEUE_ROUNDED,
                    tooltip="Add visible tracks to queue",
                    disabled=not has_visible_tracks,
                    on_click=lambda _: app._queue_visible_library(),
                ),
            ],
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
            alignment=ft.MainAxisAlignment.END,
        ),
        alignment=ft.Alignment.CENTER_RIGHT,
        col={"xs": 12, "lg": 5},
    )
    library_sections = ft.SegmentedButton(
        segments=[
            ft.Segment("tracks", icon=ft.Icons.MUSIC_NOTE_ROUNDED, label="Tracks"),
            ft.Segment(
                "playlists", icon=ft.Icons.QUEUE_MUSIC_ROUNDED, label="Playlists"
            ),
        ],
        selected=[app.library_mode],
        show_selected_icon=False,
        on_change=lambda event: app._set_library_mode(event.control.selected),
    )
    return ft.Column(
        [
            app._responsive_grid(
                [library_header, library_actions],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            library_sections,
            app._responsive_grid([app.library_query, app.library_sort], spacing=12),
            ft.Column([app.library_list], scroll=ft.ScrollMode.AUTO, expand=True),
        ],
        spacing=20,
        expand=True,
    )


def _library_track_row(
    app: MusicPlayerApp,
    track: Track,
    index: int,
    playlists: tuple[Playlist, ...] | None = None,
) -> ft.Control:
    details = app.library.details(track.id)
    menu_items = app._library_track_menu(track, index, playlists)
    leading: list[ft.Control] = []
    leading.append(
        ft.Text(
            str(index + 1),
            width=30,
            text_align=ft.TextAlign.CENTER,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
    )
    trailing: list[ft.Control] = [
        ft.Text(
            _format_duration(details.duration),
            width=50,
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        ),
        ft.IconButton(
            ft.Icons.FAVORITE_ROUNDED
            if details.favorite
            else ft.Icons.FAVORITE_BORDER_ROUNDED,
            icon_color=ft.Colors.PINK_400 if details.favorite else None,
            tooltip="Remove favorite" if details.favorite else "Favorite",
            on_click=lambda _, item=str(track.id): app._toggle_favorite(item),
        ),
        ft.IconButton(
            ft.Icons.PLAY_ARROW_ROUNDED,
            tooltip="Play",
            on_click=lambda _, item=str(track.id): app.playback.play_track(item),
        ),
        ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ_ROUNDED, items=menu_items),
    ]
    return ft.Container(
        ft.Row(
            [
                *leading,
                _artwork(details.thumbnail, 44),
                ft.Column(
                    [
                        ft.Text(
                            _track_title(track),
                            weight=ft.FontWeight.W_600,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            size=14,
                        ),
                        ft.Text(
                            _track_credit(details),
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                    spacing=3,
                    expand=True,
                ),
                *trailing,
            ],
            spacing=10,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border_radius=12,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        on_hover=hover_surface,
        animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
    )
