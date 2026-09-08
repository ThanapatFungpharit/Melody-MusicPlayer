from __future__ import annotations

import flet as ft

from musicplayer.ui.mobile.controls import heading, icon_button, scroll_page


def _settings_view(app) -> ft.Control:
    # Field validation, cookie upload, reset confirmation and saving are shared.
    appearance = app._build_appearance_settings()
    download = app._build_download_settings()
    youtube = app._build_youtube_settings()
    data = ft.Column(
        [
            ft.Text(
                "Audio files stay on your device. Each action asks you to confirm.",
                size=12,
            ),
            *[
                ft.OutlinedButton(
                    label,
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    height=48,
                    on_click=lambda _, selected=action: app._confirm_data_action(
                        selected
                    ),
                )
                for label, action in (
                    ("Clear library and playlists", "library"),
                    ("Clear playlists", "playlists"),
                    ("Reset settings", "settings"),
                    ("Reset everything", "everything"),
                )
            ],
        ],
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        spacing=12,
    )
    app.settings_path.read_only = True
    app.settings_path.helper_text = "Managed music folder on this device"
    return scroll_page(
        [
            heading(
                "Settings",
                "",
                icon_button(
                    ft.Icons.ARROW_BACK_ROUNDED,
                    "Back",
                    lambda _: app._close_context_panel(),
                ),
            ),
            appearance,
            ft.ExpansionTile(
                title=ft.Text("Download defaults"),
                controls=[download],
                maintain_state=True,
            ),
            ft.ExpansionTile(
                title=ft.Text("YouTube access"), controls=[youtube], maintain_state=True
            ),
            ft.ExpansionTile(
                title=ft.Text("Data management"), controls=[data], maintain_state=True
            ),
            ft.Button(
                "Save settings",
                icon=ft.Icons.SAVE_ROUNDED,
                height=52,
                on_click=lambda _: app._save_settings(),
            ),
            ft.TextButton(
                "Cancel changes", height=48, on_click=lambda _: app._cancel_settings()
            ),
        ]
    )
