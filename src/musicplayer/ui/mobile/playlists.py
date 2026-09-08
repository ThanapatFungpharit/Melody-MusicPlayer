from __future__ import annotations

import flet as ft

from musicplayer.ui.components.common import _empty_state, _track_credit, _track_title
from musicplayer.ui.mobile.controls import (
    heading,
    icon_button,
    more,
    scroll_page,
    track_row,
)


def _playlists_view(app) -> ft.Control:
    if app.selected_playlist_id and app.manager.has_playlist(app.selected_playlist_id):
        return _playlist_detail(app, app.manager.get_playlist(app.selected_playlist_id))
    app.selected_playlist_id = None
    playlists = app.manager.list_playlists()
    return scroll_page(
        [
            heading(
                "Playlists",
                "Collections made by you",
                more(
                    app,
                    "Playlist actions",
                    [
                        ft.PopupMenuItem(
                            content="Import YouTube playlist",
                            icon=ft.Icons.PLAYLIST_ADD_ROUNDED,
                            on_click=lambda _: app._import_playlist_dialog(),
                        )
                    ],
                ),
            ),
            ft.Button(
                "New playlist",
                icon=ft.Icons.ADD_ROUNDED,
                height=48,
                on_click=lambda _: app._create_playlist_dialog(),
            ),
            *[_playlist_card(app, p) for p in playlists],
            *(
                []
                if playlists
                else [
                    _empty_state(
                        ft.Icons.ALBUM_ROUNDED,
                        "Create a playlist or import one from YouTube.",
                    )
                ]
            ),
        ]
    )


def _playlist_card(app, playlist) -> ft.Control:
    artwork = (
        app.library.details(playlist.track_ids[0]).thumbnail
        if playlist.track_ids
        else ""
    )
    return track_row(
        playlist.name,
        f"{len(playlist.track_ids)} tracks",
        artwork,
        lambda _: app._open_playlist(str(playlist.id)),
        icon_button(
            ft.Icons.PLAY_ARROW_ROUNDED,
            f"Play {playlist.name}",
            lambda _: app._play_playlist(playlist),
        ),
    )


def _playlist_detail(app, playlist) -> ft.Control:
    tracks = app.manager.playlist_tracks(playlist.id)
    others = tuple(p for p in app.manager.list_playlists() if p.id != playlist.id)
    ids = tuple(str(t.id) for t in tracks)
    actions = [
        *(
            [
                ft.PopupMenuItem(
                    content="Add tracks",
                    icon=ft.Icons.PLAYLIST_ADD_ROUNDED,
                    on_click=lambda _: app._bulk_add_tracks_dialog(playlist),
                )
            ]
            if tracks
            else []
        ),
        *(
            [
                ft.PopupMenuItem(
                    content="Add playlist to queue",
                    icon=ft.Icons.ADD_TO_QUEUE_ROUNDED,
                    on_click=lambda _: app._queue_collection(tracks),
                )
            ]
            if tracks
            else []
        ),
        ft.PopupMenuItem(
            content="Rename playlist",
            icon=ft.Icons.EDIT_ROUNDED,
            on_click=lambda _: app._rename_playlist_dialog(playlist),
        ),
        ft.PopupMenuItem(
            content="Delete playlist",
            icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
            on_click=lambda _: app._delete_playlist_dialog(playlist),
        ),
    ]
    return scroll_page(
        [
            ft.Row(
                [
                    icon_button(
                        ft.Icons.ARROW_BACK_ROUNDED,
                        "All playlists",
                        lambda _: app._close_playlist(),
                    ),
                    ft.Text(
                        playlist.name,
                        size=24,
                        weight=ft.FontWeight.BOLD,
                        max_lines=2,
                        expand=True,
                    ),
                    more(app, playlist.name, actions),
                ],
                spacing=4,
            ),
            ft.Text(f"{len(tracks)} tracks · Reorder from a track’s actions", size=12),
            ft.Row(
                [
                    ft.Button(
                        "Play",
                        icon=ft.Icons.PLAY_ARROW_ROUNDED,
                        height=48,
                        expand=True,
                        disabled=not tracks,
                        on_click=lambda _: app._play_playlist(playlist),
                    ),
                    ft.OutlinedButton(
                        "Shuffle",
                        icon=ft.Icons.SHUFFLE_ROUNDED,
                        height=48,
                        disabled=not tracks,
                        on_click=lambda _: app._play_playlist(playlist, shuffle=True),
                    ),
                ]
            ),
            *[
                _playlist_track_row(app, playlist, track, i, others, ids)
                for i, track in enumerate(tracks)
            ],
            *(
                []
                if tracks
                else [
                    ft.Button(
                        "Add tracks",
                        icon=ft.Icons.ADD_ROUNDED,
                        height=48,
                        on_click=lambda _: app._bulk_add_tracks_dialog(playlist),
                    )
                ]
            ),
        ]
    )


def _playlist_track_row(
    app, playlist, track, index, other_playlists, playlist_track_ids
) -> ft.Control:
    details = app.library.details(track.id)
    menu = app._playlist_track_menu(
        playlist, track, index, other_playlists, playlist_track_ids
    )
    menu[0:0] = [
        ft.PopupMenuItem(
            content="Move up",
            icon=ft.Icons.ARROW_UPWARD_ROUNDED,
            disabled=index == 0,
            on_click=lambda _: app._reorder_playlist(
                str(playlist.id), index, index - 1
            ),
        ),
        ft.PopupMenuItem(
            content="Move down",
            icon=ft.Icons.ARROW_DOWNWARD_ROUNDED,
            disabled=index == len(playlist_track_ids) - 1,
            on_click=lambda _: app._reorder_playlist(
                str(playlist.id), index, index + 2
            ),
        ),
    ]
    return track_row(
        _track_title(track),
        _track_credit(details),
        details.thumbnail,
        lambda _: app.playback.play_tracks(playlist_track_ids, start_index=index),
        more(app, _track_title(track), menu),
    )
