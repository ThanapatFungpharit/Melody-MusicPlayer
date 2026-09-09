from __future__ import annotations

from typing import TYPE_CHECKING, Any

import flet as ft

from musicplayer.application.contracts import Track
from musicplayer.ui.components.common import (
    _artwork,
    _empty_state,
    _time_greeting,
    _track_credit,
    _track_title,
)
from musicplayer.ui.desktop.controls import heading, hover_surface, pill

if TYPE_CHECKING:
    from musicplayer.app import MusicPlayerApp


def _home_view(app: MusicPlayerApp) -> ft.Control:
    tracks, recent, favorites = app._home_collections()
    intro = heading(_time_greeting(), "Your music, ready when you are.")
    intro.col = {"xs": 12, "md": 9}
    count = ft.Container(
        pill(f"{len(tracks)} tracks", ft.Icons.LIBRARY_MUSIC_ROUNDED),
        alignment=ft.Alignment.CENTER_RIGHT,
        col={"xs": 12, "md": 3},
    )
    hero = ft.Container(
        ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(
                            "YOUR COLLECTION",
                            size=11,
                            weight=ft.FontWeight.W_600,
                            color=ft.Colors.ON_PRIMARY_CONTAINER,
                        ),
                        ft.Text(
                            "Good music. All yours.",
                            size=32,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.ON_PRIMARY_CONTAINER,
                        ),
                        ft.Text(
                            "An old favorite or something new. Just press play."
                            if tracks
                            else "Make room for your favorites. Add music to get started.",
                            size=13,
                            color=ft.Colors.ON_PRIMARY_CONTAINER,
                        ),
                        ft.Container(
                            ft.Row(
                                [
                                    ft.Button(
                                        "Shuffle your library"
                                        if tracks
                                        else "Find your music",
                                        icon=ft.Icons.SHUFFLE_ROUNDED
                                        if tracks
                                        else ft.Icons.SEARCH_ROUNDED,
                                        height=44,
                                        bgcolor=ft.Colors.PRIMARY,
                                        color=ft.Colors.ON_PRIMARY,
                                        elevation=0,
                                        on_click=lambda _: (
                                            app._play_collection(tracks, shuffle=True)
                                            if tracks
                                            else app.navigate(1)
                                        ),
                                    ),
                                    ft.TextButton(
                                        "Play all",
                                        icon=ft.Icons.PLAY_ARROW_ROUNDED,
                                        disabled=not tracks,
                                        on_click=lambda _: app._play_collection(tracks),
                                    ),
                                    ft.IconButton(
                                        ft.Icons.ADD_TO_QUEUE_ROUNDED,
                                        tooltip="Add library to queue",
                                        disabled=not tracks,
                                        on_click=lambda _: app._queue_collection(
                                            tracks
                                        ),
                                    ),
                                ],
                                spacing=8,
                                wrap=True,
                            ),
                            padding=ft.Padding.only(top=12),
                        ),
                    ],
                    spacing=8,
                    expand=True,
                ),
                ft.Container(
                    ft.Icon(
                        ft.Icons.GRAPHIC_EQ_ROUNDED, size=86, color=ft.Colors.PRIMARY
                    ),
                    width=126,
                    height=126,
                    border_radius=32,
                    bgcolor=ft.Colors.with_opacity(0.35, ft.Colors.SURFACE),
                    alignment=ft.Alignment.CENTER,
                ),
            ],
            spacing=24,
        ),
        padding=28,
        border_radius=24,
        gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_LEFT,
            end=ft.Alignment.BOTTOM_RIGHT,
            colors=[ft.Colors.PRIMARY_CONTAINER, ft.Colors.SURFACE_CONTAINER_LOW],
        ),
    )
    sections: list[ft.Control] = [
        app._responsive_grid(
            [intro, count], vertical_alignment=ft.CrossAxisAlignment.CENTER
        ),
        hero,
    ]
    sections.extend(
        app._track_shelf(
            "Recently played", recent, "Play a track to start your listening history."
        )
    )
    sections.extend(
        app._track_shelf("Favorites", favorites, "Favorite a track to keep it close.")
    )
    return ft.Column(
        sections,
        spacing=24,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )


def _track_shelf(
    app: MusicPlayerApp, title: str, tracks: list[Track], empty: str
) -> list[ft.Control]:
    section_heading = ft.Row(
        [
            ft.Text(title, size=20, weight=ft.FontWeight.BOLD, expand=True),
            ft.TextButton(
                "Open library",
                icon=ft.Icons.ARROW_FORWARD_ROUNDED,
                on_click=lambda _: app.navigate(2),
            ),
        ]
    )
    if not tracks:
        return [section_heading, _empty_state(ft.Icons.MUSIC_NOTE_ROUNDED, empty)]
    return [
        section_heading,
        ft.Row(
            [app._track_card(track) for track in tracks],
            scroll=ft.ScrollMode.AUTO,
            spacing=16,
        ),
    ]


def _track_card(app: MusicPlayerApp, track: Track) -> ft.Control:
    details = app.library.details(track.id)
    title = _track_title(track)
    art_size = 156
    return ft.Container(
        ft.Column(
            [
                ft.Stack(
                    [
                        _artwork(details.thumbnail, art_size),
                        ft.Container(
                            ft.IconButton(
                                ft.Icons.PLAY_ARROW_ROUNDED,
                                icon_color=ft.Colors.ON_PRIMARY,
                                bgcolor=ft.Colors.PRIMARY,
                                icon_size=24,
                                width=40,
                                height=40,
                                tooltip=f"Play {title}",
                                on_click=lambda _: app.playback.play_track(
                                    str(track.id)
                                ),
                            ),
                            right=8,
                            bottom=8,
                        ),
                    ],
                    width=art_size,
                    height=art_size,
                ),
                ft.Text(
                    title,
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
            spacing=8,
        ),
        width=180,
        padding=12,
        border_radius=20,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        on_hover=app._card_hover,
        animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
    )


def _card_hover(app: MusicPlayerApp, event: Any) -> None:
    hover_surface(event)
