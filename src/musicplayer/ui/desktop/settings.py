from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

if TYPE_CHECKING:
    from musicplayer.app import MusicPlayerApp


def _settings_view(app: MusicPlayerApp) -> ft.Control:
    download = app._build_download_settings()
    appearance = app._build_appearance_settings()
    youtube_access = app._build_youtube_settings()
    data_management = app._build_data_management_settings()
    footer = app._build_settings_footer()
    for section in (appearance, download, youtube_access, data_management):
        section.bgcolor = ft.Colors.SURFACE_CONTAINER_LOW
        section.border = None
        section.border_radius = 20
    return ft.Column(
        [
            app._context_header(
                "Settings",
                "Make Melody feel like you.",
            ),
            app._responsive_grid(
                [appearance, download, youtube_access, data_management],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
            ft.Divider(height=1),
            footer,
        ],
        spacing=16,
        expand=True,
    )
