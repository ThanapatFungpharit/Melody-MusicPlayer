from __future__ import annotations

import flet as ft

from musicplayer.ui.components.common import _download_detail, _empty_state
from musicplayer.ui.mobile.controls import heading, icon_button, more, scroll_page
from musicplayer.ui.theme import card


def _downloads_view(app) -> ft.Control:
    records = app.downloads.list()
    active = [r for r in records if r.status in {"queued", "downloading", "processing"}]
    issues = [r for r in records if r.status in {"failed", "cancelled"}]
    completed = [r for r in records if r.status == "completed"]
    sections = [
        heading(
            "Downloads",
            f"{len(active)} active · {len(issues)} need attention",
            icon_button(
                ft.Icons.ARROW_BACK_ROUNDED,
                "Back",
                lambda _: app._close_context_panel(),
            ),
        ),
        more(
            app,
            "Download actions",
            [
                ft.PopupMenuItem(
                    content="Clear download history",
                    icon=ft.Icons.CLEANING_SERVICES_ROUNDED,
                    disabled=not issues and not completed,
                    on_click=lambda _: app._clear_downloads(),
                ),
            ],
        ),
    ]
    for label, items in (
        ("Needs attention", issues),
        ("In progress", active),
        ("Completed", completed),
    ):
        if items:
            sections.append(ft.Text(label, size=18, weight=ft.FontWeight.BOLD))
            sections.extend(_download_row(app, record) for record in items)
    if not records:
        sections.append(
            _empty_state(
                ft.Icons.DOWNLOAD_DONE_ROUNDED,
                "Download songs from Search to listen offline.",
            )
        )
    return scroll_page(sections)


def _download_row(app, record) -> ft.Control:
    active = record.status in {"queued", "downloading", "processing"}
    playable_ids = tuple(
        track_id for track_id in record.track_ids if app.manager.has_track(track_id)
    )
    actions = []
    if active:
        actions.append(
            ft.TextButton(
                "Cancel",
                icon=ft.Icons.CANCEL_OUTLINED,
                height=48,
                on_click=lambda _: app._cancel_download(record.id),
            )
        )
    elif record.status in {"failed", "cancelled"}:
        actions.append(
            ft.Button(
                "Retry",
                icon=ft.Icons.REFRESH_ROUNDED,
                height=48,
                on_click=lambda _: app._retry_download(record.id),
            )
        )
    elif playable_ids:
        actions.append(
            ft.TextButton(
                "Play",
                icon=ft.Icons.PLAY_ARROW_ROUNDED,
                height=48,
                on_click=lambda _: app.playback.play_tracks(playable_ids),
            )
        )
    return card(
        ft.Column(
            [
                ft.Text(
                    record.title,
                    weight=ft.FontWeight.BOLD,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    f"Song {record.batch_position} of {record.batch_size}"
                    if record.batch_size > 1
                    else record.uploader or record.source,
                    size=12,
                ),
                ft.Text(
                    record.error or _download_detail(record),
                    size=12,
                    color=ft.Colors.ERROR
                    if record.error
                    else ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.ProgressBar(
                    value=record.progress
                    if active or record.status == "completed"
                    else 0,
                    height=4,
                ),
                ft.Row(
                    [ft.Text(record.status.title(), size=12, expand=True), *actions]
                ),
            ],
            spacing=8,
        ),
        padding=14,
    )
