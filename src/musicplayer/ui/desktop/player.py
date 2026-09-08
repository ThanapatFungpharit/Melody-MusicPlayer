from __future__ import annotations

import flet as ft

from musicplayer.ui.components.player_bar import PlaybackBindings


class DesktopPlayer(PlaybackBindings):
    """Persistent, precise transport and volume controls across the window."""

    @staticmethod
    def button(icon, label, action) -> ft.IconButton:
        return ft.IconButton(
            icon,
            tooltip=label,
            on_click=action,
            width=36,
            height=36,
            icon_size=21,
        )

    def __init__(self, app) -> None:
        super().__init__(app)
        self.volume = ft.Slider(
            min=0,
            max=100,
            divisions=100,
            value=app.playback.volume,
            width=105,
            label="{value}%",
            on_change=lambda e: app.playback.set_volume(
                int(e.control.value), persist=False
            ),
            on_change_end=lambda e: app.playback.set_volume(int(e.control.value)),
        )
        self.volume_label = ft.Text(width=36, size=11)
        self.art.width = self.art.height = 56
        self.title.size = 14
        self.position.color = self.duration.color = ft.Colors.ON_SURFACE_VARIANT
        self.position.width = self.duration.width = 38
        self.duration.text_align = ft.TextAlign.RIGHT
        self.seek.height = 24
        self.seek.padding = ft.Padding.symmetric(horizontal=8)
        self.volume.padding = ft.Padding.symmetric(horizontal=8)
        self.volume.height = 28
        self.play.width = self.play.height = 46
        self.play.icon_size = 30
        self.play.bgcolor = ft.Colors.PRIMARY
        self.play.icon_color = ft.Colors.ON_PRIMARY
        self.queue_button = self.button(
            ft.Icons.QUEUE_MUSIC_ROUNDED,
            "Queue (Ctrl+Q)",
            lambda _: app._open_queue_panel(),
        )
        self.root = ft.Container(
            ft.Row(
                [
                    ft.Row(
                        [
                            self.art,
                            ft.Column(
                                [self.title, self.credit], spacing=2, expand=True
                            ),
                            self.favorite,
                        ],
                        expand=3,
                    ),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    self.shuffle,
                                    self.previous,
                                    self.play,
                                    self.next,
                                    self.repeat,
                                ],
                                spacing=8,
                                alignment=ft.MainAxisAlignment.CENTER,
                            ),
                            ft.Row(
                                [self.position, self.seek, self.duration], spacing=4
                            ),
                        ],
                        spacing=6,
                        expand=4,
                    ),
                    ft.Row(
                        [
                            self.mute,
                            self.volume,
                            self.volume_label,
                            self.queue_button,
                        ],
                        spacing=0,
                        alignment=ft.MainAxisAlignment.END,
                        expand=3,
                    ),
                ],
                spacing=18,
            ),
            height=106,
            padding=ft.Padding.symmetric(horizontal=20, vertical=12),
            margin=12,
            border_radius=22,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
        )

    def resize(self, width: float) -> None:
        self.volume.width = 105 if width >= 1280 else 80
        self.volume_label.visible = width >= 1280
        self.favorite.visible = width >= 1000

    def refresh_volume(self) -> None:
        super().refresh_volume()
        self.volume.value = self.app.playback.volume
        self.volume_label.value = f"{self.app.playback.volume}%"

    def volume_controls(self) -> list[ft.Control]:
        return [self.mute, self.volume, self.volume_label]
