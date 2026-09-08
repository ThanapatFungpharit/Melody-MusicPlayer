from __future__ import annotations

import flet as ft

from musicplayer.ui.components.navigation import NAVIGATION
from musicplayer.ui.mobile.insets import safe_area
from musicplayer.ui.mobile.player import MobilePlayer


class MobileShell:
    def __init__(self, app) -> None:
        self.app = app
        self.panel_history: list[tuple[str, ft.View, ft.Container]] = []

    def build(self) -> None:
        app = self.app
        app.content = ft.Container(
            expand=True, padding=ft.Padding.symmetric(horizontal=16, vertical=12)
        )
        app.player = MobilePlayer(app)
        app.player_bar = app.player.root
        self.downloads = ft.IconButton(
            ft.Icons.DOWNLOAD_ROUNDED,
            tooltip="Downloads",
            width=48,
            height=48,
            on_click=lambda _: app._open_downloads_panel(),
        )
        self.bottom_nav = ft.NavigationBar(
            selected_index=0,
            height=64,
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
            destinations=[
                ft.NavigationBarDestination(icon=o, selected_icon=f, label=n)
                for n, f, o in NAVIGATION
            ],
            on_change=lambda e: app.navigate(int(e.control.selected_index)),
        )
        header = ft.Row(
            [
                ft.Icon(ft.Icons.GRAPHIC_EQ_ROUNDED, color=ft.Colors.PRIMARY),
                ft.Text("Melody", size=18, weight=ft.FontWeight.BOLD, expand=True),
                self.downloads,
                ft.IconButton(
                    ft.Icons.SETTINGS_ROUNDED,
                    tooltip="Settings",
                    width=48,
                    height=48,
                    on_click=lambda _: app._open_settings_panel(),
                ),
            ],
            spacing=4,
        )
        self.root_view = ft.View(
            route="/",
            padding=0,
            spacing=0,
            controls=[
                safe_area(
                    ft.Column(
                        [
                            ft.Container(
                                header, padding=ft.Padding.symmetric(horizontal=16)
                            ),
                            app.content,
                            app.player.root,
                            self.bottom_nav,
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    expand=True,
                )
            ],
        )
        app.page.views.clear()
        app.page.views.append(self.root_view)
        app.page.on_view_pop = self._back
        self.root_view.on_confirm_pop = self._confirm_back
        app.page.update()

    def select(self, index: int) -> None:
        self.bottom_nav.selected_index = index
        self.root_view.can_pop = (
            not (self.app.selected_playlist_id and index == 3) and index == 0
        )

    async def _confirm_back(self, event) -> None:
        if self.app.selected_playlist_id and self.app.selected_navigation == 3:
            self.app._close_playlist()
        elif self.app.selected_navigation != 0:
            self.app.navigate(0)
        await event.control.confirm_pop(False)

    def _back(self, _) -> None:
        self.close_panel()

    def open_panel(self, name: str) -> None:
        if self.app.active_panel == name:
            return
        # Queue can be opened from Now Playing, so back returns to the player.
        content = (
            self.app.player.now_playing()
            if name == "player"
            else self.app._panel_view(name)
        )
        body = ft.Container(content, padding=16, expand=True)
        view = ft.View(
            route=f"/{name}",
            padding=0,
            controls=[safe_area(body, expand=True)],
        )
        self.panel_history.append((name, view, body))
        self.app.active_panel = name
        self.app.page.views.append(view)
        self.app.page.update()

    def close_panel(self) -> None:
        if self.panel_history:
            _, view, _ = self.panel_history.pop()
            if view in self.app.page.views:
                self.app.page.views.remove(view)
        self.app.active_panel = (
            self.panel_history[-1][0] if self.panel_history else None
        )
        self.app.page.update()

    def refresh_panel(self) -> None:
        if self.panel_history:
            name, _, body = self.panel_history[-1]
            if name != "player":
                body.content = self.app._panel_view(name)
            self.app.page.update()

    def refresh_downloads(self) -> None:
        active = self.app.downloads.active_count()
        self.downloads.badge = str(active) if active else None
        self.downloads.tooltip = (
            f"Downloads, {active} active" if active else "Downloads"
        )
        self.app.page.update()

    def resize(self, width: float, height: float) -> None:
        # Flex and scrolling handle orientation without recreating forms or state.
        self.app.page.update()

    def keyboard(self, event) -> None:
        pass

    def context_header(self, title, subtitle, *actions):
        from musicplayer.ui.mobile.controls import heading, icon_button

        return heading(
            title,
            subtitle,
            *actions,
            icon_button(
                ft.Icons.ARROW_BACK_ROUNDED, "Back", lambda _: self.close_panel()
            ),
        )
