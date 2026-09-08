"""Desktop presentation primitives, using the shared adaptive color scheme."""

from __future__ import annotations

import flet as ft


def heading(title: str, subtitle: str, *, expand: bool = False) -> ft.Column:
    return ft.Column(
        [
            ft.Text(title, size=30, weight=ft.FontWeight.BOLD, max_lines=2),
            ft.Text(subtitle, size=13, color=ft.Colors.ON_SURFACE_VARIANT),
        ],
        spacing=6,
        expand=expand,
    )


def surface(
    content: ft.Control,
    *,
    padding: int = 18,
    expand: bool | int | None = None,
    key: ft.KeyValue | None = None,
) -> ft.Container:
    return ft.Container(
        content,
        padding=padding,
        border_radius=18,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        expand=expand,
        key=key,
    )


def hover_surface(event) -> None:
    event.control.bgcolor = (
        ft.Colors.SURFACE_CONTAINER_HIGH
        if event.data is True or event.data == "true"
        else ft.Colors.SURFACE_CONTAINER_LOW
    )
    event.control.update()


def reorder_handle(*, current: bool = False) -> ft.ReorderableDragHandle:
    return ft.ReorderableDragHandle(
        ft.Container(
            ft.Icon(
                ft.Icons.VOLUME_UP_ROUNDED
                if current
                else ft.Icons.DRAG_INDICATOR_ROUNDED,
                color=ft.Colors.PRIMARY if current else ft.Colors.ON_SURFACE_VARIANT,
                size=20,
            ),
            width=28,
            height=44,
            alignment=ft.Alignment.CENTER,
        ),
        mouse_cursor=ft.MouseCursor.GRAB,
        tooltip="Current track • Drag to reorder" if current else "Drag to reorder",
    )


def pill(label: str, icon=None) -> ft.Container:
    return ft.Container(
        ft.Row(
            [
                *([ft.Icon(icon, size=16, color=ft.Colors.PRIMARY)] if icon else []),
                ft.Text(label, size=12, weight=ft.FontWeight.W_500),
            ],
            spacing=8,
            tight=True,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border_radius=20,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
    )
