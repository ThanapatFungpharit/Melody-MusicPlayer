from __future__ import annotations

from typing import TYPE_CHECKING, Any

import flet as ft

from musicplayer.application.models import DownloadRecord
from musicplayer.ui.components.common import _artwork, _download_detail, _empty_state
from musicplayer.ui.desktop.controls import surface as card

if TYPE_CHECKING:
    from musicplayer.app import MusicPlayerApp


def _downloads_view(app: MusicPlayerApp) -> ft.Control:
    records = app.downloads.list()
    batch_groups: dict[str, list[DownloadRecord]] = {}
    active = 0
    completed = 0
    issues = 0
    for record in records:
        if record.batch_id:
            batch_groups.setdefault(record.batch_id, []).append(record)
        if record.status in {"queued", "downloading", "processing"}:
            active += 1
        elif record.status == "completed":
            completed += 1
        elif record.status in {"failed", "cancelled"}:
            issues += 1
    finished = completed + issues
    summary = app._responsive_grid(
        [
            app._download_metric(
                "Active", str(active), ft.Icons.DOWNLOADING_ROUNDED, ft.Colors.PRIMARY
            ),
            app._download_metric(
                "Completed",
                str(completed),
                ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED,
                ft.Colors.GREEN_400,
            ),
            app._download_metric(
                "Needs attention",
                str(issues),
                ft.Icons.ERROR_OUTLINE_ROUNDED,
                ft.Colors.ERROR,
            ),
            app._download_metric(
                "History",
                str(len(records)),
                ft.Icons.HISTORY_ROUNDED,
                ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
    )
    if records:
        body: ft.Control = app._responsive_grid(
            [app._download_row(record) for record in records],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
    else:
        body = ft.Container(
            _empty_state(
                ft.Icons.DOWNLOAD_DONE_ROUNDED,
                "Your download history is empty. New downloads will appear here with live progress and actions.",
            ),
            expand=True,
            alignment=ft.Alignment.CENTER,
        )
    controls: list[ft.Control] = [
        app._context_header(
            "Downloads",
            "Save it here. Take it with you.",
            ft.TextButton(
                "Clear download history",
                icon=ft.Icons.CLEANING_SERVICES_ROUNDED,
                height=46,
                disabled=finished == 0,
                on_click=lambda _: app._clear_downloads(),
            ),
        ),
        summary,
    ]
    if batch_groups:
        controls.extend(
            [
                ft.Text("Recent batches", size=18, weight=ft.FontWeight.BOLD),
                app._responsive_grid(
                    [
                        app._batch_overview(items)
                        for items in list(batch_groups.values())[:3]
                    ]
                ),
            ]
        )
    controls.extend(
        [
            app._responsive_grid(
                [
                    ft.Text(
                        "Download activity",
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        col={"xs": 8, "md": 9},
                    ),
                    ft.Container(
                        ft.Text(
                            f"{len(records)} item{('s' if len(records) != 1 else '')}",
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        alignment=ft.Alignment.CENTER_RIGHT,
                        col={"xs": 4, "md": 3},
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            body,
        ]
    )
    return ft.Column(controls, spacing=18, expand=True)


def _batch_overview(app: MusicPlayerApp, records: list[DownloadRecord]) -> ft.Control:
    total = records[0].batch_size or len(records)
    completed = 0
    failed = 0
    for record in records:
        if record.status == "completed":
            completed += 1
        elif record.status in {"failed", "cancelled"}:
            failed += 1
    finished = completed + failed
    active = total - finished
    status = (
        f"{active} active • {completed} completed • {failed} failed"
        if active
        else f"Finished • {completed} completed • {failed} failed"
    )
    overview = card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.DOWNLOAD_ROUNDED,
                            color=ft.Colors.ERROR if failed else ft.Colors.PRIMARY,
                        ),
                        ft.Text(
                            f"Batch download • {finished} of {total} finished",
                            weight=ft.FontWeight.BOLD,
                            expand=True,
                        ),
                    ]
                ),
                ft.Text(status, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.ProgressBar(
                    value=min(finished / total, 1.0) if total else 0,
                    color=ft.Colors.ERROR
                    if failed and (not active)
                    else ft.Colors.PRIMARY,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                    border_radius=4,
                    height=6,
                ),
            ],
            spacing=10,
        ),
        padding=16,
    )
    overview.col = {"xs": 12, "lg": 6}
    return overview


def _download_metric(
    app: MusicPlayerApp, label: str, value: str, icon: Any, color: Any
) -> ft.Control:
    metric = card(
        ft.Row(
            [
                ft.Container(
                    ft.Icon(icon, color=color, size=24),
                    width=46,
                    height=46,
                    border_radius=14,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Column(
                    [
                        ft.Text(value, size=24, weight=ft.FontWeight.BOLD),
                        ft.Text(label, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                    spacing=0,
                ),
            ],
            spacing=12,
        ),
        padding=16,
    )
    metric.col = {"xs": 12, "sm": 6, "lg": 3}
    return metric


def _download_row(app: MusicPlayerApp, record: DownloadRecord) -> ft.Control:
    active = record.status in {"queued", "downloading", "processing"}
    completed = record.status == "completed"
    playable_ids = tuple(
        track_id for track_id in record.track_ids if app.manager.has_track(track_id)
    )
    status_color = (
        ft.Colors.GREEN_400
        if completed
        else ft.Colors.ERROR
        if record.status == "failed"
        else ft.Colors.PRIMARY
    )
    detail = record.error or _download_detail(record)
    actions: list[ft.Control] = []
    if active:
        actions.append(
            ft.IconButton(
                ft.Icons.CANCEL_OUTLINED,
                tooltip="Cancel",
                width=44,
                height=44,
                on_click=lambda _, item=record.id: app._cancel_download(item),
            )
        )
    elif record.status in {"failed", "cancelled"}:
        actions.append(
            ft.Button(
                "Retry",
                icon=ft.Icons.REFRESH_ROUNDED,
                height=44,
                on_click=lambda _, item=record.id: app._retry_download(item),
            )
        )
    elif completed and playable_ids:
        actions.append(
            ft.IconButton(
                ft.Icons.PLAY_ARROW_ROUNDED,
                tooltip="Play",
                width=44,
                height=44,
                on_click=lambda _, ids=playable_ids: app.playback.play_tracks(ids),
            )
        )
    status = record.status.replace("_", " ").title()
    status_chip = ft.Container(
        ft.Text(status, size=11, color=status_color, weight=ft.FontWeight.BOLD),
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        border_radius=14,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
    )
    credit = record.uploader or record.source
    if record.batch_size > 1:
        credit = f"Batch song {record.batch_position} of {record.batch_size} • {credit}"
    detail_column = ft.Column(
        [
            ft.Text(
                record.title,
                size=14,
                weight=ft.FontWeight.W_600,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            ft.Text(credit, size=12, color=ft.Colors.ON_SURFACE_VARIANT, max_lines=1),
            ft.Text(
                detail,
                size=12,
                color=ft.Colors.ERROR if record.error else ft.Colors.ON_SURFACE_VARIANT,
                max_lines=2,
            ),
        ],
        spacing=4,
        expand=True,
    )
    header: ft.Control
    header = ft.Row(
        [
            _artwork(record.thumbnail, 56, playlist=record.kind == "playlist"),
            detail_column,
            ft.Column(
                [status_chip, ft.Row(actions, spacing=4)],
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.END,
            ),
        ],
        spacing=14,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )
    item = card(
        ft.Column(
            [
                header,
                ft.ProgressBar(
                    value=record.progress if active or completed else 0,
                    color=status_color,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                    border_radius=4,
                    height=6,
                ),
            ],
            spacing=12,
        ),
        padding=16,
    )
    item.col = {"xs": 12, "lg": 6}
    return item
