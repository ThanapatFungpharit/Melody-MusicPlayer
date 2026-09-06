from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import flet as ft

from musicplayer.application.models import RepeatMode
from musicplayer.ui.components.common import (
    _artwork,
    _format_duration,
    _track_credit,
    _track_title,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class PlayerBar(_Base):
    """Persistent playback controls displayed beneath every page."""

    def _build_player_bar(self) -> ft.Container:
        self.player_art = ft.Container(
            ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, color=ft.Colors.PRIMARY, size=28),
            width=54,
            height=54,
            bgcolor=ft.Colors.PRIMARY_CONTAINER,
            border_radius=14,
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            on_click=lambda _: self._open_queue_panel(),
            tooltip="Open queue",
        )
        self.player_title = ft.Text(
            "Nothing playing", weight=ft.FontWeight.W_600, max_lines=1
        )
        self.player_credit = ft.Text(
            "Choose something from your library",
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
            max_lines=1,
        )
        self.favorite_button = ft.IconButton(
            ft.Icons.FAVORITE_BORDER_ROUNDED,
            tooltip="Like this track",
            on_click=self._toggle_current_favorite,
            disabled=True,
        )
        self.shuffle_button = ft.IconButton(
            ft.Icons.SHUFFLE_ROUNDED,
            tooltip="Shuffle",
            on_click=lambda _: self.playback.toggle_shuffle(),
        )
        self.play_button = ft.IconButton(
            ft.Icons.PLAY_ARROW_ROUNDED,
            icon_size=34,
            tooltip="Play / pause (Ctrl+Space)",
            style=ft.ButtonStyle(bgcolor=ft.Colors.PRIMARY_CONTAINER),
            on_click=lambda _: self.playback.toggle(),
        )
        self.repeat_button = ft.IconButton(
            ft.Icons.REPEAT_ROUNDED,
            tooltip="Repeat off",
            on_click=lambda _: self.playback.cycle_repeat(),
        )
        self.position_label = ft.Text(
            "0:00", size=11, color=ft.Colors.ON_SURFACE_VARIANT
        )
        self.duration_label = ft.Text(
            "0:00", size=11, color=ft.Colors.ON_SURFACE_VARIANT
        )
        self.seek_slider = ft.Slider(
            min=0,
            max=1,
            value=0,
            expand=True,
            on_change_end=lambda event: self.playback.seek(
                int(float(event.control.value))
            ),
        )
        self.volume_button = ft.IconButton(
            ft.Icons.VOLUME_UP_ROUNDED,
            tooltip="Mute / unmute",
            on_click=lambda _: self.playback.toggle_mute(),
        )
        self.volume_slider = ft.Slider(
            min=0,
            max=100,
            divisions=100,
            value=self.playback.volume,
            width=120,
            label="{value}%",
            on_change=lambda event: self.playback.set_volume(
                int(float(event.control.value)), persist=False
            ),
            on_change_end=lambda event: self.playback.set_volume(
                int(float(event.control.value))
            ),
        )
        self.volume_label = ft.Text(f"{self.playback.volume}%", width=38, size=12)
        self.player_info = ft.Row(
            [
                self.player_art,
                ft.Column(
                    [self.player_title, self.player_credit], spacing=2, expand=True
                ),
                self.favorite_button,
            ],
            width=330,
        )
        self.skip_previous_button = ft.IconButton(
            ft.Icons.SKIP_PREVIOUS_ROUNDED,
            tooltip="Previous",
            on_click=lambda _: self.playback.previous(),
        )
        self.skip_next_button = ft.IconButton(
            ft.Icons.SKIP_NEXT_ROUNDED,
            tooltip="Next",
            on_click=lambda _: self.playback.next(),
        )
        self.transport_row = ft.Row(
            [
                self.shuffle_button,
                self.skip_previous_button,
                self.play_button,
                self.skip_next_button,
                self.repeat_button,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=2,
        )
        self.seek_row = ft.Row(
            [self.position_label, self.seek_slider, self.duration_label],
            spacing=8,
        )
        self.player_controls = ft.Column(
            [self.transport_row, self.seek_row],
            spacing=0,
            expand=True,
        )
        self.volume_down_button = ft.IconButton(
            ft.Icons.REMOVE_ROUNDED,
            icon_size=18,
            tooltip="Volume −1%",
            on_click=lambda _: self.playback.adjust_volume(-1),
        )
        self.volume_up_button = ft.IconButton(
            ft.Icons.ADD_ROUNDED,
            icon_size=18,
            tooltip="Volume +1%",
            on_click=lambda _: self.playback.adjust_volume(1),
        )
        self.queue_button = ft.IconButton(
            ft.Icons.QUEUE_MUSIC_ROUNDED,
            tooltip="Open queue (Ctrl+Q)",
            on_click=lambda _: self._open_queue_panel(),
        )
        self.player_volume = ft.Row(
            [
                self.volume_button,
                self.volume_down_button,
                self.volume_slider,
                self.volume_up_button,
                self.volume_label,
                self.queue_button,
            ],
            spacing=0,
            width=350,
            alignment=ft.MainAxisAlignment.END,
        )
        self.player_layout = ft.Row(
            [self.player_info, self.player_controls, self.player_volume], spacing=24
        )
        return ft.Container(
            self.player_layout,
            padding=ft.Padding.symmetric(horizontal=22, vertical=10),
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.Border.only(top=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
            height=104,
        )

    def _apply_player_layout(self) -> None:
        """Keep persistent playback controls reachable without horizontal scroll.

        In compact (mobile) mode the player bar collapses to a slim strip:
        a mini now-playing row (art + title + play) and a seek bar beneath
        it.  Secondary controls like volume, shuffle, repeat, previous,
        and next are hidden — users access them via the queue panel.
        """
        compact = self.compact_layout
        if compact:
            # -- compact mobile layout: Column of two rows ---------------
            compact_art_size = 44
            self.player_art.width = compact_art_size
            self.player_art.height = compact_art_size
            self.player_art.border_radius = 12
            self.player_title.size = 14
            self.player_credit.size = 11

            # Build a mini now-playing strip — tappable to open queue
            compact_info = ft.Container(
                ft.Row(
                    [
                        self.player_art,
                        ft.Column(
                            [self.player_title, self.player_credit],
                            spacing=1,
                            expand=True,
                        ),
                        self.play_button,
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                on_click=lambda _: self._open_queue_panel(),
            )
            self.play_button.icon_size = 30
            self.play_button.width = 48
            self.play_button.height = 48

            # Seek bar row
            compact_seek = ft.Row(
                [self.position_label, self.seek_slider, self.duration_label],
                spacing=6,
            )

            # Hide everything else
            self.player_volume.visible = False
            self.transport_row.visible = False
            self.favorite_button.visible = False
            self.queue_button.visible = False

            self.player_bar.content = ft.Column(
                [compact_info, compact_seek],
                spacing=2,
            )
            self.player_bar.height = 96
            self.player_bar.padding = ft.Padding.only(
                left=14,
                right=14,
                top=6,
                bottom=4,
            )
        else:
            # -- desktop / wide layout -----------------------------------
            self.player_art.width = 54
            self.player_art.height = 54
            self.player_art.border_radius = 14
            self.player_title.size = 14
            self.player_credit.size = 12
            self.play_button.icon_size = 34
            self.play_button.width = None
            self.play_button.height = None

            self.player_info.width = 330
            self.player_info.expand = None
            self.player_controls.expand = True
            self.player_volume.width = 350
            self.player_volume.expand = None
            self.player_volume.visible = True
            self.transport_row.visible = True
            self.favorite_button.visible = True
            self.queue_button.visible = True
            self.volume_down_button.visible = True
            self.volume_up_button.visible = True
            self.volume_label.visible = True
            self.volume_slider.width = 120

            self.player_layout.wrap = False
            self.player_layout.spacing = 24
            self.player_layout.run_spacing = 0

            self.player_bar.content = self.player_layout
            self.player_bar.height = 104
            self.player_bar.padding = ft.Padding.symmetric(
                horizontal=22,
                vertical=10,
            )

    def _refresh_player(self) -> None:
        current_id = self.playback.current_track_id
        if self.playback.external_title:
            title = self.playback.external_title
            credit = self.playback.external_uploader or "Streaming preview"
            thumbnail = self.playback.external_thumbnail
            self.favorite_button.disabled = True
            self.favorite_button.icon = ft.Icons.FAVORITE_BORDER_ROUNDED
            self.favorite_button.icon_color = None
        elif current_id:
            try:
                track = self.manager.get_track(current_id)
            except (KeyError, TypeError, ValueError):
                title = "Nothing playing"
                credit = "Choose something from your library"
                thumbnail = ""
                self.favorite_button.disabled = True
                self.favorite_button.icon = ft.Icons.FAVORITE_BORDER_ROUNDED
                self.favorite_button.icon_color = None
            else:
                details = self.library.details(current_id)
                title = _track_title(track)
                credit = _track_credit(details)
                thumbnail = details.thumbnail
                self.favorite_button.disabled = False
                self.favorite_button.icon = (
                    ft.Icons.FAVORITE_ROUNDED
                    if details.favorite
                    else ft.Icons.FAVORITE_BORDER_ROUNDED
                )
                self.favorite_button.icon_color = (
                    ft.Colors.PINK_400 if details.favorite else None
                )
        else:
            title = "Nothing playing"
            credit = "Choose something from your library"
            thumbnail = ""
            self.favorite_button.disabled = True
            self.favorite_button.icon = ft.Icons.FAVORITE_BORDER_ROUNDED
            self.favorite_button.icon_color = None
        self.player_title.value = title
        self.player_credit.value = credit
        self.player_art.content = _artwork(thumbnail, 54)
        self.play_button.icon = (
            ft.Icons.PAUSE_ROUNDED
            if self.playback.playing
            else ft.Icons.PLAY_ARROW_ROUNDED
        )
        self.shuffle_button.icon_color = (
            ft.Colors.PRIMARY if self.playback.queue.shuffle else None
        )
        repeat = self.playback.queue.repeat
        self.repeat_button.icon = (
            ft.Icons.REPEAT_ONE_ROUNDED
            if repeat is RepeatMode.TRACK
            else ft.Icons.REPEAT_ROUNDED
        )
        self.repeat_button.icon_color = (
            ft.Colors.PRIMARY if repeat is not RepeatMode.OFF else None
        )
        self.repeat_button.tooltip = {
            RepeatMode.OFF: "Repeat off",
            RepeatMode.TRACK: "Repeat track",
            RepeatMode.PLAYLIST: "Repeat queue",
        }[repeat]
        self._set_player_progress()
        self._set_player_volume()

        try:
            self.page.update(self.player_bar)
        except Exception:
            logger.debug(
                "Player refresh skipped because the window is unavailable",
                exc_info=True,
            )

    def _set_player_progress(self) -> None:
        maximum = max(1, self.playback.duration_ms)
        self.seek_slider.max = maximum
        self.seek_slider.value = min(self.playback.position_ms, maximum)
        self.position_label.value = _format_duration(self.playback.position_ms / 1000)
        self.duration_label.value = _format_duration(self.playback.duration_ms / 1000)

    def _set_player_volume(self) -> None:
        self.volume_slider.value = self.playback.volume
        self.volume_label.value = f"{self.playback.volume}%"
        self.volume_button.icon = (
            ft.Icons.VOLUME_OFF_ROUNDED
            if self.playback.muted
            else (
                ft.Icons.VOLUME_DOWN_ROUNDED
                if self.playback.volume < 50
                else ft.Icons.VOLUME_UP_ROUNDED
            )
        )

    def _refresh_player_progress(self) -> None:
        """Update only the high-frequency seek controls."""
        self._set_player_progress()
        try:
            self.page.update(self.seek_slider, self.position_label, self.duration_label)
        except Exception:
            logger.debug(
                "Player progress refresh skipped because the window is unavailable",
                exc_info=True,
            )

    def _refresh_player_volume(self) -> None:
        """Update only volume controls while a slider drag is in progress."""
        self._set_player_volume()
        try:
            self.page.update(self.volume_button, self.volume_slider, self.volume_label)
        except Exception:
            logger.debug(
                "Player volume refresh skipped because the window is unavailable",
                exc_info=True,
            )
