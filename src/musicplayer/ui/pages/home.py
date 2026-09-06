from __future__ import annotations

from typing import TYPE_CHECKING, Any

import flet as ft

from musicplayer.core.library.models import Track
from musicplayer.ui.components.common import (
    _artwork,
    _empty_state,
    _time_greeting,
    _track_credit,
    _track_title,
)
from musicplayer.ui.theme import card

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class HomePage(_Base):
    """Home page UI."""

    def _home_view(self) -> ft.Control:
        tracks = self.manager.list_tracks()
        recent_ids = self.store.get("recent_tracks", [])
        recent: list[Track] = []
        for track_id in recent_ids:
            try:
                recent.append(self.manager.get_track(track_id))
            except (KeyError, TypeError, ValueError):
                continue
            if len(recent) == 6:
                break
        favorite_ids = self.store.favorite_track_ids()
        favorites: list[Track] = []
        for track in tracks:
            if str(track.id) in favorite_ids:
                favorites.append(track)
                if len(favorites) == 6:
                    break
        greeting = _time_greeting()
        heading = ft.Column(
            [
                ft.Text(greeting, size=30, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Your music, ready when you are.",
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=3,
            col={"xs": 12, "md": 9},
        )
        track_count = ft.Container(
            ft.Row(
                [
                    ft.Icon(ft.Icons.LIBRARY_MUSIC_ROUNDED, size=18),
                    ft.Text(f"{len(tracks)} tracks"),
                ]
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=9),
            border_radius=20,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            col={"xs": 12, "md": 3},
        )
        header = self._responsive_grid(
            [heading, track_count],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        collection_summary = ft.Column(
            [
                ft.Text("Your collection", weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Play, shuffle, or queue your library without leaving Home.",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=3,
            col={"xs": 12, "md": 8},
        )
        collection_actions = ft.Container(
            ft.Row(
                [
                    ft.Button(
                        "Play",
                        icon=ft.Icons.PLAY_ARROW_ROUNDED,
                        disabled=not tracks,
                        on_click=lambda _: self._play_collection(tracks),
                    ),
                    ft.IconButton(
                        ft.Icons.SHUFFLE_ROUNDED,
                        tooltip="Shuffle library",
                        disabled=not tracks,
                        on_click=lambda _: self._play_collection(tracks, shuffle=True),
                    ),
                    ft.IconButton(
                        ft.Icons.ADD_TO_QUEUE_ROUNDED,
                        tooltip="Add library to queue",
                        disabled=not tracks,
                        on_click=lambda _: self._queue_collection(tracks),
                    ),
                ],
                spacing=6,
                alignment=ft.MainAxisAlignment.END,
            ),
            alignment=ft.Alignment.CENTER_RIGHT,
            col={"xs": 12, "md": 4},
        )
        quick_actions = card(
            self._responsive_grid(
                [collection_summary, collection_actions],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=14,
        )
        sections: list[ft.Control] = [header, quick_actions]
        sections.extend(
            self._track_shelf(
                "Recently played", recent, "Your listening history will appear here."
            )
        )
        sections.extend(
            self._track_shelf("Favorites", favorites, "Like tracks to keep them close.")
        )
        return ft.Column(
            [ft.Column(sections, spacing=22, scroll=ft.ScrollMode.AUTO, expand=True)],
            expand=True,
        )

    def _track_shelf(
        self, title: str, tracks: list[Track], empty: str
    ) -> list[ft.Control]:
        heading = ft.Row(
            [
                ft.Text(title, size=20, weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                ft.TextButton("View all", on_click=lambda _: self.navigate(2)),
            ]
        )
        if not tracks:
            return [heading, _empty_state(ft.Icons.MUSIC_NOTE_ROUNDED, empty)]
        compact = self.compact_layout
        cards = [self._track_card(track) for track in tracks]
        if compact:
            # On mobile, wrap cards so they reflow within the viewport.
            return [
                heading,
                ft.Row(
                    cards,
                    wrap=True,
                    spacing=10,
                    run_spacing=10,
                ),
            ]
        return [
            heading,
            ft.Row(
                cards,
                scroll=ft.ScrollMode.AUTO,
                spacing=14,
            ),
        ]

    def _track_card(self, track: Track) -> ft.Control:
        details = self.library.details(track.id)
        compact = self.compact_layout
        accent = self._accent_hex()
        art_size = 110 if compact else 142
        return ft.Container(
            ft.Column(
                [
                    ft.Stack(
                        [
                            _artwork(details.thumbnail, art_size),
                            ft.Container(
                                ft.IconButton(
                                    ft.Icons.PLAY_ARROW_ROUNDED,
                                    icon_color=ft.Colors.WHITE,
                                    icon_size=20 if compact else 24,
                                    bgcolor=accent,
                                    on_click=lambda _, item=str(track.id): (
                                        self.playback.play_track(item)
                                    ),
                                ),
                                right=6,
                                bottom=6,
                            ),
                        ],
                        width=art_size,
                        height=art_size,
                    ),
                    ft.Text(
                        _track_title(track),
                        weight=ft.FontWeight.W_600,
                        max_lines=1,
                        width=art_size,
                        size=13 if compact else 14,
                    ),
                    ft.Text(
                        _track_credit(details),
                        size=11 if compact else 12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        max_lines=1,
                        width=art_size,
                    ),
                ],
                spacing=5 if compact else 6,
            ),
            padding=8 if compact else 10,
            border_radius=14 if compact else 16,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            on_click=lambda _, item=str(track.id): self.playback.play_track(item),
            on_hover=None if compact else self._card_hover,
            animate_scale=None
            if compact
            else ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            scale=1,
        )

    @staticmethod
    def _card_hover(event: Any) -> None:
        """Subtle scale-up on desktop card hover."""
        container = event.control
        if hasattr(event, "data") and event.data == "true":
            container.scale = 1.03
            container.border = ft.Border.all(1, ft.Colors.PRIMARY)
        else:
            container.scale = 1.0
            container.border = ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
        container.update()
