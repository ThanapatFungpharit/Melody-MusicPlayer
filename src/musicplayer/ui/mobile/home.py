from __future__ import annotations

import flet as ft

from musicplayer.ui.components.common import _empty_state, _time_greeting
from musicplayer.ui.mobile.controls import heading, scroll_page


def _home_view(app) -> ft.Control:
    tracks, recent, favorites = app._home_collections()
    sections = [
        heading(_time_greeting(), "Your music, ready when you are."),
        ft.Button(
            "Shuffle your library",
            icon=ft.Icons.SHUFFLE_ROUNDED,
            height=52,
            disabled=not tracks,
            on_click=lambda _: app._play_collection(tracks, shuffle=True),
        ),
    ]
    for title, items, message in (
        ("Recently played", recent, "Play a track to start your listening history."),
        (
            "Favorites",
            favorites,
            "Favorite tracks from their action menu to find them here.",
        ),
    ):
        sections.append(heading(title))
        sections.extend(
            [app._library_track_row(track, i) for i, track in enumerate(items)]
        )
        if not items:
            sections.append(_empty_state(ft.Icons.MUSIC_NOTE_ROUNDED, message))
    return scroll_page(sections)
