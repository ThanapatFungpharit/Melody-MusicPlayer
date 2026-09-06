from __future__ import annotations

from typing import TYPE_CHECKING, Any

import flet as ft

from musicplayer.ui.theme import accent_color

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class NavigationShell(_Base):
    """Persistent navigation and layout shared by every page."""

    SYSTEM_BOTTOM_PADDING = 16
    NAVIGATION = (
        ("Home", ft.Icons.HOME_ROUNDED, ft.Icons.HOME_OUTLINED),
        ("Search", ft.Icons.SEARCH_ROUNDED, ft.Icons.SEARCH_ROUNDED),
        ("Library", ft.Icons.LIBRARY_MUSIC_ROUNDED, ft.Icons.LIBRARY_MUSIC_OUTLINED),
        ("Playlists", ft.Icons.ALBUM_ROUNDED, ft.Icons.ALBUM_OUTLINED),
    )

    def _build_shell(self) -> None:
        accent = self._accent_hex()
        self.content = ft.Container(
            expand=True,
            padding=self._content_padding(),
        )
        self.rail = ft.NavigationRail(
            selected_index=0,
            extended=True,
            min_width=88,
            min_extended_width=210,
            group_alignment=-0.75,
            indicator_color=ft.Colors.PRIMARY_CONTAINER,
            leading=ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            ft.Icon(
                                ft.Icons.GRAPHIC_EQ_ROUNDED,
                                color=ft.Colors.WHITE,
                                size=24,
                            ),
                            width=42,
                            height=42,
                            border_radius=13,
                            bgcolor=accent,
                            alignment=ft.Alignment.CENTER,
                            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
                        ),
                        ft.Text("Melody", size=20, weight=ft.FontWeight.BOLD),
                    ],
                    spacing=12,
                ),
                padding=ft.Padding.only(left=16, top=16, right=12, bottom=24),
                on_hover=self._logo_hover,
                animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT),
            ),
            destinations=[
                ft.NavigationRailDestination(
                    icon=outline, selected_icon=filled, label=label
                )
                for label, filled, outline in self.NAVIGATION
            ],
            trailing=ft.Column(
                [
                    ft.Container(expand=True),
                    ft.IconButton(
                        ft.Icons.DOWNLOAD_ROUNDED,
                        tooltip="Downloads (Ctrl+D)",
                        on_click=lambda _: self._open_downloads_panel(),
                    ),
                    ft.IconButton(
                        ft.Icons.SETTINGS_ROUNDED,
                        tooltip="Settings (Ctrl+,)",
                        on_click=lambda _: self._open_settings_panel(),
                    ),
                ],
                spacing=4,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            pin_trailing_to_bottom=True,
            on_change=lambda event: self.navigate(int(event.control.selected_index)),
        )
        from typing import cast
        trailing = cast(ft.Column, self.rail.trailing)
        self.download_button = cast(ft.IconButton, trailing.controls[1])
        self._refresh_download_badge()
        self.mobile_drawer = ft.NavigationDrawer(
            controls=[
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Container(
                                ft.Icon(
                                    ft.Icons.GRAPHIC_EQ_ROUNDED,
                                    color=ft.Colors.WHITE,
                                    size=24,
                                ),
                                width=42,
                                height=42,
                                border_radius=13,
                                bgcolor=accent,
                                alignment=ft.Alignment.CENTER,
                            ),
                            ft.Text("Melody", size=20, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=12,
                    ),
                    padding=ft.Padding.only(left=20, top=24, right=16, bottom=20),
                ),
                ft.Divider(height=1),
                *[
                    ft.NavigationDrawerDestination(
                        icon=outline, selected_icon=filled, label=label
                    )
                    for label, filled, outline in self.NAVIGATION
                ],
                ft.Divider(height=1),
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.DOWNLOAD_ROUNDED),
                    title=ft.Text("Downloads"),
                    on_click=self._open_downloads_from_drawer,
                ),
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.SETTINGS_ROUNDED),
                    title=ft.Text("Settings"),
                    on_click=self._open_settings_from_drawer,
                ),
            ],
            selected_index=0,
            width=304,
            on_change=self._navigate_from_drawer,
        )
        self.page.drawer = self.mobile_drawer
        # -- mobile bottom navigation bar --------------------------------
        self.bottom_nav = ft.NavigationBar(
            selected_index=0,
            destinations=[
                ft.NavigationBarDestination(
                    icon=outline, selected_icon=filled, label=label
                )
                for label, filled, outline in self.NAVIGATION
            ],
            on_change=lambda event: self.navigate(int(event.control.selected_index)),
            height=68,
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
        )
        # -- mobile top header (slimmed down, secondary actions only) ----
        self.mobile_menu_button = ft.IconButton(
            ft.Icons.MENU_ROUNDED,
            tooltip="More options",
            width=44,
            height=44,
            icon_size=24,
            on_click=self._open_mobile_drawer,
        )
        self.mobile_header = ft.Container(
            ft.Row(
                [
                    ft.Container(
                        ft.Icon(
                            ft.Icons.GRAPHIC_EQ_ROUNDED,
                            color=ft.Colors.WHITE,
                            size=16,
                        ),
                        width=30,
                        height=30,
                        border_radius=9,
                        bgcolor=accent,
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Text("Melody", size=18, weight=ft.FontWeight.BOLD, expand=True),
                    ft.IconButton(
                        ft.Icons.DOWNLOAD_ROUNDED,
                        tooltip="Downloads",
                        width=44,
                        height=44,
                        icon_size=22,
                        on_click=lambda _: self._open_downloads_panel(),
                    ),
                    ft.IconButton(
                        ft.Icons.QUEUE_MUSIC_ROUNDED,
                        tooltip="Open queue",
                        width=44,
                        height=44,
                        icon_size=22,
                        on_click=lambda _: self._open_queue_panel(),
                    ),
                    self.mobile_menu_button,
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
            visible=self.compact_layout,
        )
        self.player_bar = self._build_player_bar()
        self.rail_holder = ft.Container(
            self.rail,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border.only(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
            visible=not self.compact_layout,
        )
        # Bottom nav only visible in compact mode
        self.bottom_nav.visible = self.compact_layout
        body = ft.Row(
            [
                self.rail_holder,
                ft.Column(
                    [
                        self.mobile_header,
                        self.content,
                        self.player_bar,
                        self.bottom_nav,
                    ],
                    spacing=0,
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.system_safe_area = self._system_safe_area(body)
        self.page.add(self.system_safe_area)
        self.navigate(0)
        self._apply_player_layout()
        self._refresh_player()

    @classmethod
    def _system_safe_area(cls, content: ft.Control) -> ft.SafeArea:
        """Keep the shell above desktop taskbars and mobile system actions."""
        return ft.SafeArea(
            content=content,
            expand=True,
            avoid_intrusions_left=False,
            avoid_intrusions_top=False,
            avoid_intrusions_right=False,
            avoid_intrusions_bottom=True,
            maintain_bottom_view_padding=True,
            minimum_padding=ft.Padding.only(bottom=cls.SYSTEM_BOTTOM_PADDING),
        )

    def _logo_hover(self, event: Any) -> None:
        """Subtle opacity pulse on desktop logo hover."""
        leading = self.rail.leading
        if leading is None:
            return
        if hasattr(event, "data") and event.data == "true":
            leading.opacity = 0.8
        else:
            leading.opacity = 1.0
        try:
            self.page.update(leading)
        except Exception:
            pass

    def navigate(self, index: int) -> None:
        self.selected_navigation = max(0, min(index, len(self.NAVIGATION) - 1))
        self.rail.selected_index = self.selected_navigation
        if hasattr(self, "mobile_drawer"):
            self.mobile_drawer.selected_index = self.selected_navigation
        if hasattr(self, "bottom_nav"):
            self.bottom_nav.selected_index = self.selected_navigation
        builders = (
            self._home_view,
            self._search_view,
            self._library_view,
            self._playlists_view,
        )
        self.content.content = builders[self.selected_navigation]()
        controls_to_update = [self.rail, self.content]
        if hasattr(self, "bottom_nav"):
            controls_to_update.append(self.bottom_nav)
        self.page.update(*controls_to_update)

    async def _navigate_from_drawer(self, event: Any) -> None:
        self.navigate(int(event.control.selected_index))
        await self._close_mobile_drawer()

    async def _open_mobile_drawer(self, _: Any = None) -> None:
        if not self.compact_layout:
            return
        # Flet 0.86 resolves the drawer through page.drawer; show_drawer takes
        # no positional drawer argument and is asynchronous.
        await self.page.show_drawer()

    async def _close_mobile_drawer(self) -> None:
        await self.page.close_drawer()

    async def _open_downloads_from_drawer(self, _: Any) -> None:
        await self._close_mobile_drawer()
        self._open_downloads_panel()

    async def _open_settings_from_drawer(self, _: Any) -> None:
        await self._close_mobile_drawer()
        self._open_settings_panel()

    # -- persistent player -----------------------------------------
