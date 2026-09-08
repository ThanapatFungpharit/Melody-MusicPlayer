from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import flet as ft

from musicplayer.application.downloads import DownloadCoordinator
from musicplayer.application.library_service import LibraryService
from musicplayer.application.models import DownloadRecord
from musicplayer.application.playback import PlaybackController
from musicplayer.application.providers import ProviderRegistry
from musicplayer.application.store import ApplicationStore
from musicplayer.core.concurrency import LazyBoundedExecutor, WorkerQueueFull
from musicplayer.core.library import MusicManager
from musicplayer.platform_runtime import (
    UnsupportedPlatformError,
    application_data_directory,
    current_platform,
)
from musicplayer.runtime_environment import public_error_message
from musicplayer.ui.audio_backend import FletAudioBackend
from musicplayer.ui.components import (
    ContextPanel,
    NavigationShell,
    PlayerBar,
    SharedUIComponents,
)
from musicplayer.ui.components.common import (
    _artwork,
    _download_detail,
    _empty_state,
    _format_bytes,
    _format_duration,
    _page_header,
    _theme_mode,
    _time_greeting,
    _track_credit,
    _track_title,
)
from musicplayer.ui.pages import (
    DownloadsPage,
    HomePage,
    LibraryPage,
    PlaylistsPage,
    QueuePage,
    SearchPage,
    SettingsPage,
)
from musicplayer.ui.presentation import PresentationKind, presentation_kind
from musicplayer.ui.theme import accent_color, build_theme

logger = logging.getLogger(__name__)

__all__ = [
    "MusicPlayerApp",
    "_artwork",
    "_download_detail",
    "_empty_state",
    "_format_bytes",
    "_format_duration",
    "_page_header",
    "_theme_mode",
    "_time_greeting",
    "_track_credit",
    "_track_title",
    "main",
]


class MusicPlayerApp(
    NavigationShell,
    PlayerBar,
    ContextPanel,
    SharedUIComponents,
    HomePage,
    SearchPage,
    LibraryPage,
    PlaylistsPage,
    QueuePage,
    DownloadsPage,
    SettingsPage,
):
    """Application composition root for Melody's page and component modules."""

    @property
    def views(self):
        # Release bundles contain only the presentation for their native target.
        # Keep the other package out of the import graph, including at startup.
        if (
            getattr(self, "presentation", PresentationKind.DESKTOP)
            is PresentationKind.MOBILE
        ):
            from musicplayer.ui import mobile

            return mobile
        from musicplayer.ui import desktop

        return desktop

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.data_directory = _application_data_directory()
        self.store = ApplicationStore(self.data_directory / "state.json")
        self.settings = self.store.settings
        self.manager = MusicManager(
            self.data_directory / "library.mmdb", self.settings.download_directory
        )
        self.library = LibraryService(self.manager, self.store)
        self.providers = ProviderRegistry(settings=self.settings)
        # Search, stream resolution, and imports share a bounded I/O pool that
        # exists only while at least one background operation is in flight.
        self.workers = LazyBoundedExecutor(
            max_workers=2,
            max_pending=2,
            thread_name_prefix="melody-io",
        )
        self._system_media_key: tuple[str, ...] | None = None
        self._system_media_metadata = ("", "", "", "")
        self.file_picker = ft.FilePicker()
        page.services.append(self.file_picker)
        self.backend = FletAudioBackend(
            page, use_device_volume=self._is_mobile_platform()
        )
        self.playback = PlaybackController(
            self.manager,
            self.library,
            self.store,
            self.backend,
            io_executor=self.workers,
            on_change=self._playback_changed,
            on_error=self._show_error,
        )
        self.backend.bind(
            on_position=self.playback.on_position,
            on_duration=self.playback.on_duration,
            on_playing=self.playback.on_playing,
            on_completed=self.playback.on_completed,
            on_error=self.playback.on_backend_error,
            on_media_action=self._media_action,
        )
        self.downloads = DownloadCoordinator(
            self.manager,
            self.store,
            self.settings,
            on_change=self._download_changed,
        )
        self.selected_navigation = 0
        self._initialize_search_page()
        self._initialize_library_page()
        self._initialize_playlists_page()
        self.active_panel: str | None = None
        self.pending_download_actions: dict[str, tuple[str, str | None]] = {}
        self._last_announced_track: str | None = None
        self.presentation = presentation_kind(
            page.platform, native_mobile=self._is_mobile_platform()
        )
        if self.presentation is PresentationKind.MOBILE:
            from musicplayer.ui.mobile.shell import MobileShell

            self.shell = MobileShell(self)
        else:
            from musicplayer.ui.desktop.shell import DesktopShell

            self.shell = DesktopShell(self)
        self._configure_page()
        self._build_shell()

    def _configure_page(self) -> None:
        self.page.title = "Melody — Music player"
        self.page.padding = 0
        self.page.spacing = 0
        self._reapply_accent()
        self.page.theme_mode = _theme_mode(self.settings.theme)
        # Window constraints are meaningful on desktop only. Applying the
        # desktop minimum size to an Android/iOS window makes the app render
        # beyond the device viewport.
        if not self._is_mobile_platform():
            self.page.window.width = 1540
            self.page.window.height = 960
            self.page.window.min_width = 1040
            self.page.window.min_height = 700
        self.page.on_keyboard_event = self._keyboard_event
        self.page.on_resize = self._page_resized
        self.page.on_close = self._close

    def _playback_changed(self, change: str = "state") -> None:
        if change != "volume":
            self._sync_system_media(refresh_metadata=change != "progress")
        if change == "progress":
            self._refresh_player_progress()
            return
        if change == "volume":
            self._refresh_player_volume()
            return
        self._refresh_player()
        current = self.playback.current_track_id or (
            f"stream:{self.playback.external_title}"
            if self.playback.external_title
            else None
        )
        if (
            self.settings.notifications
            and self.playback.playing
            and current
            and current != self._last_announced_track
        ):
            self._last_announced_track = current
            self._show_message(f"Now playing: {self.player.title.value}")
        if self.active_panel == "queue":
            self._refresh_context_panel()

    def _sync_system_media(self, *, refresh_metadata: bool = True) -> None:
        """Keep native lock-screen/notification controls aligned with playback."""
        if self.playback.external_title:
            media_key = (
                "stream",
                self.playback.external_title,
                self.playback.external_uploader,
                self.playback.external_thumbnail,
            )
        elif self.playback.current_track_id:
            media_key = ("track", self.playback.current_track_id)
        else:
            media_key = None

        if refresh_metadata or media_key != self._system_media_key:
            title = ""
            artist = ""
            album = ""
            artwork_uri = ""
            if self.playback.external_title:
                title = self.playback.external_title
                artist = self.playback.external_uploader
                artwork_uri = self.playback.external_thumbnail
            elif self.playback.current_track_id:
                try:
                    track = self.manager.get_track(self.playback.current_track_id)
                    details = self.library.details(self.playback.current_track_id)
                except (KeyError, OSError, ValueError):
                    logger.debug(
                        "Could not resolve system media metadata", exc_info=True
                    )
                else:
                    title = track.title or Path(track.filename).stem
                    artist = details.uploader
                    artwork_uri = details.thumbnail
                    # Melody does not currently persist album tags. The source
                    # name is still useful context in media panels without
                    # pretending it is an album title.
                    album = details.source_name
            self._system_media_key = media_key
            self._system_media_metadata = (title, artist, album, artwork_uri)
        else:
            title, artist, album, artwork_uri = self._system_media_metadata

        has_media = bool(title)
        self.backend.sync_media_session(
            title=title,
            artist=artist,
            album=album,
            artwork_uri=artwork_uri,
            duration_ms=self.playback.duration_ms,
            position_ms=self.playback.position_ms,
            playing=self.playback.playing,
            has_next=has_media,
            has_previous=has_media,
            repeat_mode=self.playback.queue.repeat.value,
            shuffle=self.playback.queue.shuffle,
        )

    def _media_action(self, action: str, seek_position_ms: int | None) -> None:
        """Apply an idempotent command received from native media controls."""
        command = action.casefold()
        if command == "play":
            if not self.playback.playing:
                self.playback.toggle()
        elif command == "pause":
            if self.playback.playing:
                self.playback.toggle()
        elif command == "stop":
            if self.playback.playing:
                self.playback.toggle()
            self.playback.seek(0)
        elif command == "skiptonext":
            self.playback.next()
        elif command == "skiptoprevious":
            self.playback.previous()
        elif command == "seekto" and seek_position_ms is not None:
            self.playback.seek(seek_position_ms)
        elif command == "rewind":
            self.playback.seek(self.playback.position_ms - 10_000)
        elif command == "fastforward":
            self.playback.seek(self.playback.position_ms + 10_000)

    def _download_changed(self, record: DownloadRecord) -> None:
        self._refresh_download_badge()
        pending = self.pending_download_actions.get(record.id)
        if pending and record.status in {"completed", "failed", "cancelled"}:
            self.pending_download_actions.pop(record.id, None)
            if record.status == "completed":
                self._apply_track_action(record.track_ids, *pending)
            else:
                self._show_error(
                    f"Couldn’t finish the requested action for {record.title}."
                )
        if (
            record.status in {"completed", "failed", "cancelled"}
            and self.playlist_import_session is not None
        ):
            self._continue_playlist_import(
                record.url,
                tuple(record.track_ids) if record.status == "completed" else (),
            )
        if self.selected_navigation == 2 and hasattr(self, "library_list"):
            self._refresh_library_list()
        if self.active_panel == "downloads":
            self._refresh_context_panel()

    def _keyboard_event(self, event: ft.KeyboardEvent) -> None:
        key = event.key.casefold()
        if key in {"media play pause", "mediaplaypause"}:
            self.playback.toggle()
        elif key in {"media track next", "medianexttrack"}:
            self.playback.next()
        elif key in {"media track previous", "mediaprevioustrack"}:
            self.playback.previous()
        else:
            self.shell.keyboard(event)

    async def _page_resized(self, event: Any) -> None:
        self.shell.resize(
            float(getattr(event, "width", 0) or self.page.width or 1540),
            float(getattr(event, "height", 0) or self.page.height or 960),
        )

    def _is_mobile_platform(self) -> bool:
        platform = str(getattr(self.page, "platform", "")).casefold()
        if "android" in platform or "ios" in platform:
            return True
        try:
            return current_platform() == "android"
        except UnsupportedPlatformError:
            return False

    def _accent_hex(self) -> str:
        """Resolve the current accent-color hex from settings."""
        return accent_color(getattr(self, "_accent_draft", self.settings.accent_color))

    def _reapply_accent(self) -> None:
        self.page.theme = build_theme(self._accent_hex())
        self.page.dark_theme = build_theme(self._accent_hex())

    def _close(self, _: Any) -> None:
        self._cancel_playlist_import_retry()
        self.backend.close()
        self.downloads.shutdown()
        self.workers.shutdown(wait=False, cancel_pending=True)
        self.store.set(
            "playback",
            self.playback.queue.to_dict(position_ms=self.playback.position_ms),
        )

    def _submit_background(
        self, operation: Callable[..., object], /, *args: object
    ) -> bool:
        """Submit bounded blocking I/O without allocating workers while idle."""
        try:
            self.workers.submit(operation, *args)
        except WorkerQueueFull as error:
            self._show_error(str(error))
            return False
        except RuntimeError:
            logger.debug("Background work rejected during application shutdown")
            return False
        return True

    def _show_message(self, message: str, *, error: bool = False) -> None:
        try:
            self.page.show_dialog(
                ft.SnackBar(
                    ft.Text(message),
                    show_close_icon=True,
                    bgcolor=ft.Colors.ERROR_CONTAINER if error else None,
                )
            )
        except Exception:
            logger.debug("Could not show notification", exc_info=True)

    def _show_error(self, message: str) -> None:
        self._show_message(public_error_message(message), error=True)


def main(page: ft.Page) -> None:
    try:
        MusicPlayerApp(page)
    except Exception:  # noqa: BLE001 - final UI boundary must stay user-safe
        logger.error("Melody failed to start")
        page.title = "Melody — Startup error"
        page.padding = 32
        page.add(
            ft.Column(
                [
                    ft.Icon(
                        ft.Icons.ERROR_OUTLINE_ROUNDED, size=52, color=ft.Colors.ERROR
                    ),
                    ft.Text(
                        "Melody couldn’t start", size=28, weight=ft.FontWeight.BOLD
                    ),
                    ft.Text(
                        "Restart Melody and check that the configured music folder "
                        "and app data location are available."
                    ),
                ],
                spacing=14,
            )
        )


def _application_data_directory() -> Path:
    return application_data_directory()
