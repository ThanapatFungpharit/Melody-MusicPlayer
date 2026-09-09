from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from musicplayer.application.contracts import Playlist
from musicplayer.application.models import SearchResult
from musicplayer.ui.components.common import (
    _artwork,
    _empty_state,
    _format_duration,
)
from musicplayer.ui.desktop.controls import heading as _page_header
from musicplayer.ui.desktop.controls import surface as card

if TYPE_CHECKING:
    from musicplayer.app import MusicPlayerApp


def _search_view(app: MusicPlayerApp) -> ft.Control:
    app.search_query = ft.TextField(
        value=app.search_draft,
        on_change=app._search_draft_changed,
        hint_text="Search YouTube or paste a link",
        prefix_icon=ft.Icons.SEARCH_ROUNDED,
        border_radius=16,
        border_color=ft.Colors.TRANSPARENT,
        filled=True,
        fill_color=ft.Colors.SURFACE_CONTAINER_LOW,
        text_size=14,
        height=50,
        expand=True,
        on_submit=lambda _: app._start_search(),
    )
    app.search_button = ft.Button(
        "Search",
        bgcolor=ft.Colors.PRIMARY,
        color=ft.Colors.ON_PRIMARY,
        elevation=0,
        height=46,
        disabled=app.search_busy,
        icon=ft.Icons.SEARCH_ROUNDED,
        on_click=lambda _: app._start_search(),
    )
    app.batch_download_button = ft.Button(
        "Batch URLs",
        height=46,
        style=ft.ButtonStyle(
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            color=ft.Colors.ON_SURFACE,
            elevation=0,
            padding=ft.Padding.symmetric(horizontal=10),
            text_style=ft.TextStyle(size=13),
        ),
        icon=ft.Icons.DOWNLOAD_ROUNDED,
        on_click=lambda _: app._batch_download_dialog(),
    )
    app.search_query.col = {"xs": 12, "md": 8}
    app.search_button.col = {"xs": 12, "md": 2}
    app.batch_download_button.col = {"xs": 12, "md": 2}
    app.search_status = ft.Text(
        "Songs, channels, and playlists. Find something you love.",
        size=13,
        color=ft.Colors.ON_SURFACE_VARIANT,
        col={"xs": 12, "md": 7},
    )
    app.search_view_selector = ft.SegmentedButton(
        segments=[
            ft.Segment("list", icon=ft.Icons.VIEW_LIST_ROUNDED, label="Track list"),
            ft.Segment("grid", icon=ft.Icons.GRID_VIEW_ROUNDED, label="Grid"),
        ],
        selected=[app.search_view_mode],
        show_selected_icon=False,
        on_change=lambda event: app._set_search_view(event.control.selected),
        col={"xs": 12, "md": 5},
    )
    app.search_previous_button = ft.IconButton(
        ft.Icons.CHEVRON_LEFT_ROUNDED,
        tooltip="Previous page",
        disabled=app.search_page <= 1,
        on_click=lambda _: app._load_search_page(app.search_page - 1),
    )
    app.search_first_button = ft.TextButton(
        "First",
        disabled=app.search_page <= 1,
        on_click=lambda _: app._load_search_page(1),
    )
    app.search_page_label = ft.Text(
        f"Page {app.search_page}", weight=ft.FontWeight.W_600
    )
    app.search_next_button = ft.IconButton(
        ft.Icons.CHEVRON_RIGHT_ROUNDED,
        tooltip="Next page",
        disabled=not app.search_has_next,
        on_click=lambda _: app._load_search_page(app.search_page + 1),
    )
    app.search_pagination = ft.Row(
        [
            app.search_first_button,
            app.search_previous_button,
            app.search_page_label,
            app.search_next_button,
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=6,
        visible=bool(app.search_results) or app.search_page > 1,
    )
    app.search_list = ft.Column(spacing=10)
    if app.search_results:
        app._render_search_results(update=False)
        app._update_search_navigation()
    else:
        app.search_list.controls = [
            _empty_state(
                ft.Icons.TRAVEL_EXPLORE_ROUNDED,
                "Start with a song, channel, playlist, or YouTube URL.",
            )
        ]
    return ft.Column(
        [
            _page_header("Search", "Find music on YouTube."),
            app._responsive_grid(
                [app.search_query, app.search_button, app.batch_download_button],
                spacing=12,
            ),
            app._responsive_grid(
                [app.search_status, app.search_view_selector],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Column([app.search_list], scroll=ft.ScrollMode.AUTO, expand=True),
            app.search_pagination,
        ],
        spacing=20,
        expand=True,
    )


def _search_result_row(
    app: MusicPlayerApp, result: SearchResult, playlists: tuple[Playlist, ...]
) -> ft.Control:
    subtitle = f"{result.uploader}  •  {result.source}"
    if result.is_playlist:
        subtitle += f"  •  {result.entry_count or 'Multiple'} tracks"
    elif result.duration:
        subtitle += f"  •  {_format_duration(result.duration)}"
    local_track = app._available_search_track(result)
    play_label = "Preview playlist" if result.is_playlist else "Play"
    menu_items = app._search_result_menu(result, playlists)
    actions: list[ft.Control] = [
        ft.IconButton(
            ft.Icons.PLAY_ARROW_ROUNDED,
            tooltip=play_label,
            on_click=lambda _, item=result: app._play_search_result(item),
        ),
        (
            ft.Icon(
                ft.Icons.DOWNLOAD_DONE_ROUNDED,
                color=ft.Colors.GREEN_400,
                tooltip="Already in library",
            )
            if local_track
            else ft.IconButton(
                ft.Icons.DOWNLOAD_ROUNDED,
                tooltip="Download",
                on_click=lambda _, item=result: app._download_result(item),
            )
        ),
        ft.PopupMenuButton(
            icon=ft.Icons.MORE_HORIZ_ROUNDED, tooltip="More actions", items=menu_items
        ),
    ]
    return card(
        ft.Row(
            [
                _artwork(result.thumbnail, 52, playlist=result.is_playlist),
                ft.Column(
                    [
                        ft.Text(
                            result.title,
                            weight=ft.FontWeight.W_600,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            size=14,
                        ),
                        ft.Text(
                            subtitle,
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                    spacing=5,
                    expand=True,
                ),
                *actions,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=12,
    )


def _search_result_card(
    app: MusicPlayerApp, result: SearchResult, playlists: tuple[Playlist, ...]
) -> ft.Control:
    local_track = app._available_search_track(result)
    detail = result.uploader or "Unknown uploader"
    if result.is_playlist:
        detail += f" • {result.entry_count or 'Multiple'} tracks"
    elif result.duration:
        detail += f" • {_format_duration(result.duration)}"
    art_size = 180
    card_width = 204
    return ft.Container(
        card(
            ft.Column(
                [
                    _artwork(result.thumbnail, art_size, playlist=result.is_playlist),
                    ft.Text(
                        result.title,
                        weight=ft.FontWeight.BOLD,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        height=44,
                        size=14,
                    ),
                    ft.Text(
                        detail, size=12, color=ft.Colors.ON_SURFACE_VARIANT, max_lines=1
                    ),
                    ft.Row(
                        [
                            ft.IconButton(
                                ft.Icons.PLAY_ARROW_ROUNDED,
                                tooltip=(
                                    "Preview playlist" if result.is_playlist else "Play"
                                ),
                                icon_size=24,
                                on_click=lambda _, item=result: app._play_search_result(
                                    item
                                ),
                            ),
                            (
                                ft.Icon(
                                    ft.Icons.DOWNLOAD_DONE_ROUNDED,
                                    color=ft.Colors.GREEN_400,
                                    tooltip="Already in library",
                                )
                                if local_track
                                else ft.IconButton(
                                    ft.Icons.DOWNLOAD_ROUNDED,
                                    tooltip="Download",
                                    icon_size=24,
                                    on_click=lambda _, item=result: (
                                        app._download_result(item)
                                    ),
                                )
                            ),
                            ft.Container(expand=True),
                            ft.PopupMenuButton(
                                icon=ft.Icons.MORE_HORIZ_ROUNDED,
                                tooltip="More actions",
                                items=app._search_result_menu(
                                    result, playlists, include_primary_actions=False
                                ),
                            ),
                        ]
                    ),
                ],
                spacing=8,
            ),
            padding=12,
        ),
        width=card_width,
    )


def _search_results(app: MusicPlayerApp) -> list[ft.Control]:
    playlists = app.manager.list_playlists()
    if app.search_view_mode == "grid":
        return [
            ft.Row(
                [
                    _search_result_card(app, item, playlists)
                    for item in app.search_results
                ],
                wrap=True,
                spacing=14,
                run_spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        ]
    return [_search_result_row(app, item, playlists) for item in app.search_results]
