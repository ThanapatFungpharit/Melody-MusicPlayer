from __future__ import annotations

import inspect
from functools import partial

import flet as ft

from musicplayer.ui.components.common import _artwork
from musicplayer.ui.mobile.insets import bottom_sheet
from musicplayer.ui.theme import TOUCH_TARGET


def icon_button(icon, label, action) -> ft.IconButton:
    return ft.IconButton(
        icon, tooltip=label, on_click=action, width=TOUCH_TARGET, height=TOUCH_TARGET
    )


def heading(title: str, subtitle: str = "", *actions: ft.Control) -> ft.Control:
    return ft.Column(
        [
            ft.Row(
                [
                    ft.Text(
                        title,
                        size=24,
                        weight=ft.FontWeight.BOLD,
                        expand=True,
                        max_lines=2,
                    ),
                    *actions,
                ],
                spacing=4,
            ),
            *(
                [ft.Text(subtitle, color=ft.Colors.ON_SURFACE_VARIANT, size=13)]
                if subtitle
                else []
            ),
        ],
        spacing=4,
    )


def action_sheet(app, title: str, items: list[ft.PopupMenuItem]) -> None:
    async def choose(event, item) -> None:
        app.page.pop_dialog()
        if item.on_click:
            result = item.on_click(event)
            if inspect.isawaitable(result):
                await result

    body = ft.Column(
        [
            heading(
                title,
                "",
                icon_button(
                    ft.Icons.CLOSE_ROUNDED,
                    "Close actions",
                    lambda _: app.page.pop_dialog(),
                ),
            ),
            *[
                ft.ListTile(
                    title=item.content
                    if isinstance(item.content, ft.Control)
                    else ft.Text(item.content or "", max_lines=2),
                    leading=item.icon
                    if isinstance(item.icon, ft.Control)
                    else ft.Icon(item.icon)
                    if item.icon
                    else None,
                    min_height=52,
                    disabled=item.disabled,
                    on_click=partial(choose, item=item),
                )
                for item in items
            ],
        ],
        spacing=2,
        scroll=ft.ScrollMode.AUTO,
        tight=True,
    )
    app.page.show_dialog(
        bottom_sheet(
            ft.Container(body, padding=16),
            max_height=max(160, float(app.page.height or 700) * 0.85),
        )
    )


def more(app, title: str, items: list[ft.PopupMenuItem]) -> ft.IconButton:
    return icon_button(
        ft.Icons.MORE_HORIZ_ROUNDED,
        f"Actions for {title}",
        lambda _: action_sheet(app, title, items),
    )


def track_row(
    title: str,
    credit: str,
    artwork: str | bytes,
    play,
    trailing: ft.Control,
    *,
    selected: bool = False,
    cache=None,
) -> ft.Control:
    return ft.Container(
        ft.Row(
            [
                ft.Container(
                    ft.Row(
                        [
                            _artwork(artwork, 48, cache=cache),
                            ft.Column(
                                [
                                    ft.Text(
                                        title,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                        weight=ft.FontWeight.W_600,
                                    ),
                                    ft.Text(
                                        credit,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                        size=12,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=3,
                                expand=True,
                            ),
                        ],
                        spacing=12,
                    ),
                    on_click=play,
                    expand=True,
                    padding=ft.Padding.symmetric(vertical=8),
                    tooltip=f"Play {title}",
                ),
                trailing,
            ],
            spacing=4,
        ),
        padding=ft.Padding.only(left=8, right=4),
        border_radius=12,
        bgcolor=ft.Colors.PRIMARY_CONTAINER
        if selected
        else ft.Colors.SURFACE_CONTAINER_LOW,
    )


def scroll_page(controls: list[ft.Control]) -> ft.Column:
    return ft.Column(
        controls,
        spacing=14,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
