from __future__ import annotations

import flet as ft

from musicplayer.ui.components.navigation import NAVIGATION
from musicplayer.ui.desktop.controls import heading
from musicplayer.ui.desktop.player import DesktopPlayer


class DesktopShell:
    def __init__(self, app) -> None:
        self.app = app

    def build(self) -> None:
        app = self.app
        app.content = ft.Container(
            expand=True, padding=28, bgcolor=ft.Colors.SURFACE, border_radius=24
        )
        app.player = DesktopPlayer(app)
        app.player_bar = app.player.root
        self.nav_buttons = []
        self.nav_labels = []
        self.nav_icons = []
        self.nav_rows = []
        for index, (label, filled, outlined) in enumerate(NAVIGATION):
            self.nav_buttons.append(
                self._navigation_button(
                    label, outlined, lambda _, i=index: app.navigate(i)
                )
            )
        downloads = self._navigation_button(
            "Downloads",
            ft.Icons.DOWNLOAD_ROUNDED,
            lambda _: app._open_downloads_panel(),
            "Downloads (Ctrl+D)",
        )
        self.downloads = self.nav_icons[-1]
        settings = self._navigation_button(
            "Settings",
            ft.Icons.SETTINGS_OUTLINED,
            lambda _: app._open_settings_panel(),
            "Settings (Ctrl+,)",
        )
        self.utility_buttons = {"downloads": downloads, "settings": settings}
        self.brand_label = ft.Text("Melody", weight=ft.FontWeight.BOLD, size=22)
        self.section_label = ft.Text(
            "YOUR MUSIC",
            size=10,
            weight=ft.FontWeight.W_600,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.shortcut = ft.Text(
            "Ctrl L to find your next track",
            size=11,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.brand = ft.Row(
            [
                ft.Container(
                    ft.Icon(
                        ft.Icons.GRAPHIC_EQ_ROUNDED, color=ft.Colors.PRIMARY, size=26
                    ),
                    width=42,
                    height=42,
                    border_radius=14,
                    bgcolor=ft.Colors.PRIMARY_CONTAINER,
                    alignment=ft.Alignment.CENTER,
                ),
                self.brand_label,
            ],
            spacing=12,
        )
        self.rail = ft.Container(
            ft.Column(
                [
                    ft.Container(
                        self.brand, padding=ft.Padding.only(top=12, bottom=28)
                    ),
                    ft.Container(
                        self.section_label, padding=ft.Padding.only(left=14, bottom=8)
                    ),
                    *self.nav_buttons,
                    ft.Container(expand=True),
                    downloads,
                    settings,
                    ft.Container(
                        self.shortcut,
                        padding=ft.Padding.only(left=14, top=14, bottom=12),
                    ),
                ],
                spacing=6,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            width=208,
            padding=ft.Padding.symmetric(horizontal=12),
        )
        self.panel = ft.Container(
            visible=False,
            padding=28,
            bgcolor=ft.Colors.SURFACE,
            border_radius=24,
        )
        self.workspace = ft.Row(
            [app.content, self.panel],
            spacing=12,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        app.page.add(
            ft.Container(
                ft.Column(
                    [
                        ft.Container(
                            ft.Row(
                                [self.rail, self.workspace],
                                spacing=0,
                                expand=True,
                                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                            ),
                            padding=ft.Padding.only(top=12, right=12),
                            expand=True,
                        ),
                        app.player.root,
                    ],
                    spacing=0,
                    expand=True,
                ),
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                expand=True,
            )
        )
        self.resize(float(app.page.width or 1540), float(app.page.height or 960))

    def select(self, index: int) -> None:
        if self.app.active_panel != "queue":
            self.close_panel(update=False)
        self._refresh_navigation()

    def _navigation_button(self, label, icon, action, tooltip=None):
        symbol = ft.Icon(icon, size=22)
        text = ft.Text(label, size=14, weight=ft.FontWeight.W_500, expand=True)
        row = ft.Row([symbol, text], spacing=12)
        self.nav_icons.append(symbol)
        self.nav_labels.append(text)
        self.nav_rows.append(row)
        return ft.TextButton(
            row,
            height=48,
            tooltip=tooltip or label,
            on_click=action,
            style=self._navigation_style(False),
        )

    @staticmethod
    def _navigation_style(selected):
        return ft.ButtonStyle(
            bgcolor=ft.Colors.PRIMARY_CONTAINER if selected else ft.Colors.TRANSPARENT,
            color=ft.Colors.ON_PRIMARY_CONTAINER
            if selected
            else ft.Colors.ON_SURFACE_VARIANT,
            shape=ft.RoundedRectangleBorder(radius=14),
            padding=ft.Padding.symmetric(horizontal=14),
            alignment=ft.Alignment.CENTER_LEFT,
        )

    def _refresh_navigation(self):
        for index, button in enumerate(self.nav_buttons):
            selected = (
                index == self.app.selected_navigation
                and self.app.active_panel not in self.utility_buttons
            )
            button.style = self._navigation_style(selected)
            self.nav_icons[index].icon = NAVIGATION[index][1 if selected else 2]
            self.nav_labels[index].weight = (
                ft.FontWeight.W_600 if selected else ft.FontWeight.W_500
            )
        for name, button in self.utility_buttons.items():
            button.style = self._navigation_style(self.app.active_panel == name)
        self.app.player.queue_button.bgcolor = (
            ft.Colors.PRIMARY_CONTAINER if self.app.active_panel == "queue" else None
        )

    def open_panel(self, name: str) -> None:
        if self.app.active_panel == name:
            return
        self.app.active_panel = name
        self.panel.content = self.app._panel_view(name)
        self.panel.visible = True
        self._refresh_navigation()
        self.resize(
            float(self.app.page.width or 1540), float(self.app.page.height or 960)
        )

    def close_panel(self, *, update=True) -> None:
        self.app.active_panel = None
        self.panel.visible = False
        self.panel.content = None
        self.app.content.visible = True
        self._refresh_navigation()
        if update:
            self.app.page.update()

    def refresh_panel(self) -> None:
        self.panel.content = self.app._panel_view(self.app.active_panel)
        self.app.page.update()

    def refresh_downloads(self) -> None:
        active = self.app.downloads.active_count()
        self.downloads.badge = str(active) if active else None
        self.utility_buttons["downloads"].tooltip = (
            f"Downloads, {active} active (Ctrl+D)" if active else "Downloads (Ctrl+D)"
        )
        self.app.page.update()

    def resize(self, width: float, height: float) -> None:
        extended = width >= 1180
        self.rail.width = 208 if extended else 80
        for label in [
            self.brand_label,
            self.section_label,
            self.shortcut,
            *self.nav_labels,
        ]:
            label.visible = extended
        self.brand.alignment = (
            ft.MainAxisAlignment.START if extended else ft.MainAxisAlignment.CENTER
        )
        for row in self.nav_rows:
            row.alignment = (
                ft.MainAxisAlignment.START if extended else ft.MainAxisAlignment.CENTER
            )
        docked = self.app.active_panel == "queue" and width >= 1400
        self.panel.width = 430 if docked else None
        self.panel.expand = not docked
        self.panel.padding = 20 if docked else 28
        self.app.content.visible = not self.app.active_panel or docked
        self.app.player.resize(width)
        self.app.page.update()

    def keyboard(self, event) -> None:
        app = self.app
        key = event.key.casefold()
        modifier = event.ctrl or event.meta
        if key == "escape" and app.active_panel:
            app._close_context_panel()
        elif modifier and key == "l":
            app.navigate(1)
            app.page.run_task(app.search_query.focus)
        elif modifier and key == "q":
            app._open_queue_panel()
        elif modifier and key == "d":
            app._open_downloads_panel()
        elif modifier and key == ",":
            app._open_settings_panel()
        elif modifier and key == " ":
            app.playback.toggle()

    def context_header(self, title, subtitle, *actions):
        return ft.Row(
            [
                heading(title, subtitle, expand=True),
                *actions,
                ft.IconButton(
                    ft.Icons.CLOSE_ROUNDED,
                    tooltip="Close (Esc)",
                    on_click=lambda _: self.close_panel(),
                ),
            ],
            spacing=12,
        )
