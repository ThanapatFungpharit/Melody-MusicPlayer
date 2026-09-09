from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from musicplayer.application.contracts import Playlist, Track
from musicplayer.ui.components.common import (
    _artwork,
    _empty_state,
    _format_duration,
    _track_credit,
    _track_title,
)
from musicplayer.ui.desktop.controls import (
    heading as _page_header,
)
from musicplayer.ui.desktop.controls import (
    hover_surface,
    reorder_handle,
)
from musicplayer.ui.desktop.controls import (
    surface as card,
)

if TYPE_CHECKING:
    from musicplayer.app import MusicPlayerApp


def _playlists_view(app: MusicPlayerApp) -> ft.Control:
    if app.selected_playlist_id and app.manager.has_playlist(app.selected_playlist_id):
        return app._playlist_detail(app.manager.get_playlist(app.selected_playlist_id))
    app.selected_playlist_id = None
    playlists = app.manager.list_playlists()
    if playlists:
        body: ft.Control = ft.Row(
            [app._playlist_card(playlist) for playlist in playlists],
            wrap=True,
            spacing=14,
            run_spacing=18,
            vertical_alignment=ft.CrossAxisAlignment.START,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
    else:
        body = ft.Container(
            _empty_state(
                ft.Icons.QUEUE_MUSIC_ROUNDED,
                "Create a playlist or import one from YouTube to get started.",
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )
    playlists_header = _page_header(
        "Playlists", "A collection for every mood and moment."
    )
    playlists_header.col = {"xs": 12, "md": 7}
    playlist_actions = ft.Container(
        ft.Row(
            [
                ft.OutlinedButton(
                    "Import playlist",
                    icon=ft.Icons.PLAYLIST_ADD_ROUNDED,
                    on_click=lambda _: app._import_playlist_dialog(),
                ),
                ft.Button(
                    "New playlist",
                    bgcolor=ft.Colors.PRIMARY,
                    color=ft.Colors.ON_PRIMARY,
                    elevation=0,
                    icon=ft.Icons.ADD_ROUNDED,
                    on_click=lambda _: app._create_playlist_dialog(),
                ),
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
            alignment=ft.MainAxisAlignment.END,
        ),
        alignment=ft.Alignment.CENTER_RIGHT,
        col={"xs": 12, "md": 5},
    )
    return ft.Column(
        [
            app._responsive_grid(
                [playlists_header, playlist_actions],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            body,
        ],
        spacing=20,
        expand=True,
    )


def _playlist_detail(app: MusicPlayerApp, playlist: Playlist) -> ft.Control:
    tracks = app.manager.playlist_tracks(playlist.id)
    track_ids = tuple(str(track.id) for track in tracks)
    other_playlists = tuple(
        item for item in app.manager.list_playlists() if item.id != playlist.id
    )
    bulk_add_button: ft.Control
    bulk_add_button = ft.OutlinedButton(
        "Add to playlist",
        icon=ft.Icons.PLAYLIST_ADD_ROUNDED,
        on_click=lambda _: app._bulk_add_tracks_dialog(playlist),
    )
    play_button = ft.Button(
        "Play",
        bgcolor=ft.Colors.PRIMARY,
        color=ft.Colors.ON_PRIMARY,
        elevation=0,
        icon=ft.Icons.PLAY_ARROW_ROUNDED,
        disabled=not tracks,
        on_click=lambda _: app._play_playlist(playlist),
    )
    rows: ft.Control
    if tracks:
        rows = ft.ReorderableListView(
            controls=[
                app._playlist_track_row(
                    playlist, track, index, other_playlists, track_ids
                )
                for index, track in enumerate(tracks)
            ],
            show_default_drag_handles=False,
            mouse_cursor=ft.MouseCursor.GRAB,
            on_reorder=lambda event, pid=str(playlist.id): app._reorder_playlist(
                pid, event.old_index, event.new_index
            ),
            expand=True,
        )
    else:
        rows = _empty_state(
            ft.Icons.PLAYLIST_ADD_ROUNDED,
            "This playlist is empty. Add tracks from Search or Library.",
            expand=True,
        )
    return ft.Column(
        [
            ft.Row(
                [
                    ft.IconButton(
                        ft.Icons.ARROW_BACK_ROUNDED,
                        tooltip="All playlists",
                        on_click=lambda _: app._close_playlist(),
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                playlist.name,
                                size=28,
                                weight=ft.FontWeight.BOLD,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                f"{len(tracks)} track{('s' if len(tracks) != 1 else '')}",
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    bulk_add_button,
                    play_button,
                    ft.IconButton(
                        ft.Icons.SHUFFLE_ROUNDED,
                        tooltip="Shuffle playlist",
                        disabled=not tracks,
                        on_click=lambda _: app._play_playlist(playlist, shuffle=True),
                    ),
                    ft.IconButton(
                        ft.Icons.ADD_TO_QUEUE_ROUNDED,
                        tooltip="Add playlist to queue",
                        disabled=not tracks,
                        on_click=lambda _: app._queue_collection(tracks),
                    ),
                    ft.PopupMenuButton(
                        icon=ft.Icons.MORE_HORIZ_ROUNDED,
                        items=[
                            ft.PopupMenuItem(
                                content="Rename playlist",
                                icon=ft.Icons.EDIT_ROUNDED,
                                on_click=lambda _: app._rename_playlist_dialog(
                                    playlist
                                ),
                            ),
                            ft.PopupMenuItem(
                                content="Delete playlist",
                                icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                                on_click=lambda _: app._delete_playlist_dialog(
                                    playlist
                                ),
                            ),
                        ],
                    ),
                ],
            ),
            ft.Divider(height=1),
            rows,
        ],
        spacing=16,
        expand=True,
    )


def _playlist_track_row(
    app: MusicPlayerApp,
    playlist: Playlist,
    track: Track,
    index: int,
    other_playlists: tuple[Playlist, ...],
    playlist_track_ids: tuple[str, ...],
) -> ft.Control:
    details = app.library.details(track.id)
    menu = app._playlist_track_menu(
        playlist, track, index, other_playlists, playlist_track_ids
    )
    leading: list[ft.Control] = []
    leading.extend(
        [
            reorder_handle(),
            ft.Text(
                str(index + 1),
                width=28,
                text_align=ft.TextAlign.CENTER,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
    )
    trailing: list[ft.Control] = []
    trailing.append(
        ft.Text(
            _format_duration(details.duration),
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
    )
    trailing.extend(
        [
            ft.IconButton(
                ft.Icons.PLAY_ARROW_ROUNDED,
                tooltip="Play from here",
                on_click=lambda _, ids=playlist_track_ids, start=index: (
                    app.playback.play_tracks(ids, start_index=start)
                ),
            ),
            ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ_ROUNDED, items=menu),
        ]
    )
    row = card(
        ft.Row(
            [
                *leading,
                _artwork(details.thumbnail, 48),
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
            ]
        ),
        padding=10,
        key=f"{playlist.id}:{index}:{track.id}",
    )
    row.margin = ft.Margin.only(bottom=6)
    return row


def _playlist_card(app: MusicPlayerApp, playlist: Playlist) -> ft.Control:
    track_count = len(playlist.track_ids)
    artwork = (
        app.library.details(playlist.track_ids[0]).thumbnail
        if playlist.track_ids
        else ""
    )
    art_size = 180
    card_widget = ft.Container(
        ft.Column(
            [
                ft.Stack(
                    [
                        _artwork(artwork, art_size, playlist=True),
                        ft.Container(
                            ft.IconButton(
                                ft.Icons.PLAY_ARROW_ROUNDED,
                                bgcolor=ft.Colors.PRIMARY,
                                icon_color=ft.Colors.ON_PRIMARY,
                                icon_size=24,
                                width=40,
                                height=40,
                                tooltip=f"Play {playlist.name}",
                                disabled=not track_count,
                                on_click=lambda _: app._play_playlist(playlist),
                            ),
                            right=8,
                            bottom=8,
                        ),
                    ],
                    width=art_size,
                    height=art_size,
                ),
                ft.Text(
                    playlist.name,
                    size=15,
                    weight=ft.FontWeight.W_600,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    f"{track_count} track{('s' if track_count != 1 else '')}",
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    size=12,
                ),
            ],
            spacing=7,
        ),
        padding=12,
        width=204,
        border_radius=20,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        on_hover=hover_surface,
        animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        on_click=lambda _, item=str(playlist.id): app._open_playlist(item),
    )
    return card_widget
