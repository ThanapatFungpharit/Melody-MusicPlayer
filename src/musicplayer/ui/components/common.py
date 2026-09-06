from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import flet as ft

from musicplayer.application.models import DownloadRecord, TrackDetails
from musicplayer.core.library.models import Playlist, Track
from musicplayer.ui.theme import accent_color

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class SharedUIComponents(_Base):
    """Reusable controls and collection actions shared by multiple pages."""

    GRID_COLUMNS = 12
    GRID_GUTTER: ClassVar[dict[str, int]] = {"xs": 12, "md": 16, "xl": 18}

    def _play_collection(
        self, tracks: list[Track] | tuple[Track, ...], *, shuffle: bool = False
    ) -> None:
        ids = [str(track.id) for track in tracks]
        if shuffle:
            import random

            random.shuffle(ids)
        self.playback.play_tracks(ids)

    def _queue_collection(self, tracks: list[Track] | tuple[Track, ...]) -> None:
        items = list(tracks)
        for track in items:
            self.playback.add_last(str(track.id))
        if items:
            self._show_message(
                f"Added {len(items)} track{'s' if len(items) != 1 else ''} to the queue."
            )

    def _playlist_card(self, playlist: Playlist) -> ft.Control:
        tracks = self.manager.playlist_tracks(playlist.id)
        artwork = self.library.details(tracks[0].id).thumbnail if tracks else ""
        compact = self._is_compact()
        accent = self._accent_hex()
        art_size = 120 if compact else 170
        card_widget = ft.Container(
            ft.Column(
                [
                    ft.Stack(
                        [
                            _artwork(artwork, art_size, playlist=True),
                            ft.Container(
                                ft.IconButton(
                                    ft.Icons.PLAY_ARROW_ROUNDED,
                                    bgcolor=accent,
                                    icon_color=ft.Colors.WHITE,
                                    icon_size=20 if compact else 24,
                                    on_click=lambda _: self._play_playlist(playlist),
                                ),
                                right=8,
                                bottom=8,
                            ),
                        ],
                        width=art_size,
                        height=art_size,
                    ),
                    ft.Text(
                        playlist.name,
                        size=15 if compact else 17,
                        weight=ft.FontWeight.BOLD,
                        max_lines=1,
                    ),
                    ft.Text(
                        f"{len(tracks)} track{'s' if len(tracks) != 1 else ''}",
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        size=12,
                    ),
                ],
                spacing=7,
            ),
            padding=10 if compact else 12,
            border_radius=18,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            on_click=lambda _, item=str(playlist.id): self._open_playlist(item),
        )
        return card_widget

    def _context_header(
        self, title: str, subtitle: str, *actions: ft.Control
    ) -> ft.Control:
        heading = ft.Column(
            [
                ft.Text(title, size=34, weight=ft.FontWeight.BOLD),
                ft.Text(
                    subtitle,
                    size=14,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=4,
        )
        heading.col = {"xs": 12, "sm": 8, "lg": 9}
        header_actions = ft.Container(
            ft.Row(
                [
                    *actions,
                    ft.IconButton(
                        ft.Icons.CLOSE_ROUNDED,
                        tooltip="Close",
                        icon_size=24,
                        width=48,
                        height=48,
                        on_click=lambda _: self._close_context_panel(),
                    ),
                ],
                spacing=12,
                alignment=ft.MainAxisAlignment.END,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            alignment=ft.Alignment.CENTER_RIGHT,
        )
        header_actions.col = {"xs": 12, "sm": 4, "lg": 3}
        return self._responsive_grid(
            [heading, header_actions],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    @classmethod
    def _responsive_grid(
        cls,
        controls: list[ft.Control],
        *,
        spacing: int | dict[str, int] | None = None,
        run_spacing: int | dict[str, int] | None = None,
        scroll: ft.ScrollMode | None = None,
        expand: bool | int | None = None,
        vertical_alignment: ft.CrossAxisAlignment = ft.CrossAxisAlignment.START,
    ) -> ft.ResponsiveRow:
        gutter: Any = spacing if spacing is not None else cls.GRID_GUTTER
        run: Any = run_spacing if run_spacing is not None else gutter
        return ft.ResponsiveRow(
            controls,
            columns=cls.GRID_COLUMNS,
            spacing=gutter,
            run_spacing=run,
            vertical_alignment=vertical_alignment,
            scroll=scroll,
            expand=expand,
        )


def _theme_mode(value: str) -> ft.ThemeMode:
    return {"dark": ft.ThemeMode.DARK, "light": ft.ThemeMode.LIGHT}.get(
        value, ft.ThemeMode.SYSTEM
    )


def _page_header(title: str, subtitle: str, *, expand: bool = False) -> ft.Control:
    return ft.Column(
        [
            ft.Column(
                [
                    ft.Text(title, size=30, weight=ft.FontWeight.BOLD),
                    ft.Container(
                        width=40,
                        height=3,
                        border_radius=2,
                        bgcolor=ft.Colors.PRIMARY,
                    ),
                ],
                spacing=6,
            ),
            ft.Text(subtitle, color=ft.Colors.ON_SURFACE_VARIANT),
        ],
        spacing=3,
        expand=expand,
    )


def _empty_state(
    icon: Any,
    message: str,
    *,
    expand: bool | int | None = None,
) -> ft.Control:
    return ft.Container(
        ft.Column(
            [
                ft.Container(
                    ft.Icon(icon, size=36, color=ft.Colors.ON_SURFACE_VARIANT),
                    width=72,
                    height=72,
                    border_radius=36,
                    bgcolor=ft.Colors.SURFACE_CONTAINER,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Text(
                    message,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    text_align=ft.TextAlign.CENTER,
                    size=14,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=14,
        ),
        padding=40,
        alignment=ft.Alignment.CENTER,
        border_radius=20,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        expand=expand,
    )


def _artwork(url: str, size: int, *, playlist: bool = False) -> ft.Control:
    radius = 14 if size >= 100 else 12
    if url:
        return ft.Image(
            src=url,
            width=size,
            height=size,
            fit=ft.BoxFit.COVER,
            border_radius=radius,
            error_content=ft.Icon(ft.Icons.BROKEN_IMAGE_OUTLINED),
        )
    return ft.Container(
        ft.Icon(
            ft.Icons.ALBUM_ROUNDED if playlist else ft.Icons.MUSIC_NOTE_ROUNDED,
            size=max(22, size * 0.38),
            color=ft.Colors.PRIMARY,
        ),
        width=size,
        height=size,
        border_radius=radius,
        bgcolor=ft.Colors.PRIMARY_CONTAINER,
        alignment=ft.Alignment.CENTER,
    )


def _track_title(track: Track) -> str:
    return track.title.strip() or Path(track.filename).stem


def _track_credit(details: TrackDetails) -> str:
    return details.uploader.strip() or details.source_name.strip() or "Unknown source"


def _format_duration(seconds: float) -> str:
    if not seconds:
        return "—"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _download_detail(record: DownloadRecord) -> str:
    if record.status == "completed":
        return f"Added {len(record.track_ids)} track{'s' if len(record.track_ids) != 1 else ''} to your library"
    if record.status == "processing":
        return "Converting audio and embedding metadata…"
    if record.total_bytes:
        return f"{_format_bytes(record.downloaded_bytes)} of {_format_bytes(record.total_bytes)} • {record.progress:.0%}"
    if record.batch_id and record.url:
        return record.url
    return record.filename or record.source


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _time_greeting() -> str:
    from datetime import datetime

    hour = datetime.now().astimezone().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"
