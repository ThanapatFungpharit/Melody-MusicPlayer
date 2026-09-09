"""Shared playback bindings; layouts live in mobile/player and desktop/player."""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from musicplayer.application.models import RepeatMode
from musicplayer.ui.components.common import (
    _artwork,
    _format_duration,
    _track_credit,
    _track_title,
)

if TYPE_CHECKING:
    from musicplayer.app import MusicPlayerApp


class PlaybackBindings:
    def __init__(self, app: MusicPlayerApp) -> None:
        self.app = app
        self.title = ft.Text(
            "Nothing playing",
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            weight=ft.FontWeight.W_600,
        )
        self.credit = ft.Text(
            "Choose music to start listening",
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.art = ft.Container(width=48, height=48)
        self.play = self.button(
            ft.Icons.PLAY_ARROW_ROUNDED, "Play", lambda _: app.playback.toggle()
        )
        self.previous = self.button(
            ft.Icons.SKIP_PREVIOUS_ROUNDED,
            "Previous track",
            lambda _: app.playback.previous(),
        )
        self.next = self.button(
            ft.Icons.SKIP_NEXT_ROUNDED, "Next track", lambda _: app.playback.next()
        )
        self.favorite = self.button(
            ft.Icons.FAVORITE_BORDER_ROUNDED, "Favorite", app._toggle_current_favorite
        )
        self.shuffle = self.button(
            ft.Icons.SHUFFLE_ROUNDED,
            "Shuffle off",
            lambda _: app.playback.toggle_shuffle(),
        )
        self.repeat = self.button(
            ft.Icons.REPEAT_ROUNDED, "Repeat off", lambda _: app.playback.cycle_repeat()
        )
        self.mute = self.button(
            ft.Icons.VOLUME_UP_ROUNDED, "Mute", lambda _: app.playback.toggle_mute()
        )
        self.position = ft.Text("0:00", size=11)
        self.duration = ft.Text("0:00", size=11)
        self.seeking = False
        self.seek = ft.Slider(
            min=0,
            max=1,
            value=0,
            expand=True,
            on_change_start=self._seek_start,
            on_change_end=self._seek_end,
        )
        self.root = ft.Container()

    @staticmethod
    def button(icon, label, action) -> ft.IconButton:
        return ft.IconButton(icon, tooltip=label, on_click=action)

    def _seek_start(self, _) -> None:
        self.seeking = True

    def _seek_end(self, event) -> None:
        self.seeking = False
        self.app.playback.seek(int(event.control.value or 0))

    def refresh(self, change: str = "state") -> None:
        playback = self.app.playback
        if change == "progress":
            self.refresh_progress()
        elif change == "volume":
            self.refresh_volume()
        else:
            thumbnail = playback.external_thumbnail
            title = playback.external_title or "Nothing playing"
            credit = playback.external_uploader or "Choose music to start listening"
            favorite = False
            self.favorite.disabled = True
            if playback.current_track_id and not playback.external_title:
                try:
                    track = self.app.manager.get_track(playback.current_track_id)
                    details = self.app.library.details(track.id)
                except (KeyError, TypeError, ValueError):
                    pass
                else:
                    title, credit, thumbnail = (
                        _track_title(track),
                        _track_credit(details),
                        details.thumbnail,
                    )
                    favorite = details.favorite
                    self.favorite.disabled = False
            if playback.snapshot.loading:
                credit = "Preparing track…"
            self.title.value, self.credit.value = title, credit
            self.art.content = _artwork(thumbnail, int(self.art.width or 48))
            self.favorite.icon = (
                ft.Icons.FAVORITE_ROUNDED
                if favorite
                else ft.Icons.FAVORITE_BORDER_ROUNDED
            )
            self.favorite.icon_color = ft.Colors.PINK_400 if favorite else None
            self.favorite.tooltip = "Remove favorite" if favorite else "Favorite"
            self.play.icon = (
                ft.Icons.PAUSE_ROUNDED
                if playback.playing
                else ft.Icons.PLAY_ARROW_ROUNDED
            )
            self.play.tooltip = "Pause" if playback.playing else "Play"
            has_media = bool(playback.current_track_id or playback.external_title)
            for control in (self.play, self.previous, self.next):
                control.disabled = not has_media
            self.shuffle.icon_color = (
                ft.Colors.PRIMARY if playback.queue.shuffle else None
            )
            self.shuffle.tooltip = (
                "Shuffle on" if playback.queue.shuffle else "Shuffle off"
            )
            repeat = playback.queue.repeat
            self.repeat.icon = (
                ft.Icons.REPEAT_ONE_ROUNDED
                if repeat is RepeatMode.TRACK
                else ft.Icons.REPEAT_ROUNDED
            )
            self.repeat.icon_color = (
                ft.Colors.PRIMARY if repeat is not RepeatMode.OFF else None
            )
            self.repeat.tooltip = {
                RepeatMode.OFF: "Repeat off",
                RepeatMode.TRACK: "Repeat track",
                RepeatMode.PLAYLIST: "Repeat queue",
            }[repeat]
            self.refresh_progress()
            self.refresh_volume()

    def refresh_progress(self) -> None:
        playback = self.app.playback
        if not self.seeking:
            self.seek.max = max(1, playback.duration_ms)
            self.seek.value = min(playback.position_ms, self.seek.max)
        self.seek.disabled = playback.duration_ms <= 0
        self.position.value = (
            _format_duration(playback.position_ms / 1000)
            if playback.position_ms
            else "0:00"
        )
        self.duration.value = (
            "Loading…"
            if playback.snapshot.loading
            else _format_duration(playback.duration_ms / 1000)
        )

    def refresh_volume(self) -> None:
        muted = self.app.playback.muted or self.app.playback.volume == 0
        self.mute.icon = (
            ft.Icons.VOLUME_OFF_ROUNDED if muted else ft.Icons.VOLUME_UP_ROUNDED
        )
        self.mute.tooltip = "Unmute" if muted else "Mute"

    def progress_controls(self) -> list[ft.Control]:
        return [self.seek, self.position, self.duration]

    def volume_controls(self) -> list[ft.Control]:
        return [self.mute]

    def state_controls(self) -> list[ft.Control]:
        return [self.root]


class PlayerBar:
    """Application event bridge with no platform layout decisions."""

    if TYPE_CHECKING:
        player: PlaybackBindings
        page: ft.Page

    def _refresh_player(self) -> None:
        self.player.refresh()
        self.page.update(*self.player.state_controls())

    def _refresh_player_progress(self) -> None:
        self.player.refresh("progress")
        self.page.update(*self.player.progress_controls())

    def _refresh_player_volume(self) -> None:
        self.player.refresh("volume")
        controls = self.player.volume_controls()
        if controls:
            self.page.update(*controls)
