from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import flet as ft

from musicplayer.application.downloads import DownloadCoordinator
from musicplayer.application.library_service import LibraryService
from musicplayer.application.models import DownloadRecord
from musicplayer.application.playback import PlaybackController
from musicplayer.application.playback_persistence import PlaybackPersistence
from musicplayer.application.providers import ProviderRegistry
from musicplayer.application.store import ApplicationStore
from musicplayer.application.thumbnails import ThumbnailCache
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
from musicplayer.ui.tasks import PageTasks
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

    def __getattr__(self, name: str) -> Any:
        # Small, synchronous fixtures and embedders sometimes construct the
        # composition root with ``__new__`` to exercise one page workflow. Keep
        # those callers on the same I/O boundary without requiring a full app
        # startup; normal pages initialize ``tasks`` eagerly in ``__init__``.
        if name == "tasks" and "page" in self.__dict__:
            tasks = PageTasks(
                self.page,
                LazyBoundedExecutor(
                    max_workers=1,
                    max_pending=1,
                    thread_name_prefix="melody-fixture",
                ),
                getattr(self, "_show_error", lambda _: None),
            )
            self.__dict__[name] = tasks
            return tasks
        raise AttributeError(name)

    def __init__(self, page: ft.Page, resources: AppResources | None = None) -> None:
        self.page = page
        resources = resources or AppResources.load()
        self.data_directory = resources.directory
        self.store = resources.store
        self.settings = resources.store.settings
        self.manager = resources.manager
        self.library = resources.library
        self.providers = resources.providers
        self.thumbnails = resources.thumbnails
        # Search, stream resolution, and imports share a bounded I/O pool that
        # exists only while at least one background operation is in flight.
        self.workers = LazyBoundedExecutor(
            max_workers=2,
            max_pending=2,
            thread_name_prefix="melody-io",
        )
        self.tasks = PageTasks(page, self.workers, self._show_error)
        self.playback_workers = LazyBoundedExecutor(
            max_workers=2, max_pending=1, thread_name_prefix="melody-playback"
        )
        self.persistence = PlaybackPersistence(
            self.store, lambda message: self.tasks.dispatch(self._show_error, message)
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
            io_executor=self.playback_workers,
            persistence=self.persistence,
            dispatch=self.tasks.dispatch,
            available_track_ids=resources.available_track_ids,
            on_change=self._playback_changed,
            on_error=self._show_error,
        )
        self.backend.bind(
            on_loaded=self.playback.on_loaded,
            on_position=self.playback.on_position,
            on_duration=self.playback.on_duration,
            on_playing=self.playback.on_playing,
            on_completed=self.playback.on_completed,
            on_error=self.playback.on_backend_error,
            on_media_action=self._media_action,
        )
        self.downloads = resources.downloads
        self.downloads.on_change = lambda record: self.tasks.dispatch(
            self._download_changed, record
        )
        self.selected_navigation = 0
        self._initialize_search_page()
        self._initialize_library_page()
        self._initialize_playlists_page()
        self.active_panel: str | None = None
        self.pending_download_actions: dict[str, tuple[str, str | None]] = {}
        self._download_play_requests: dict[str, int] = {}
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
        self.page.on_app_lifecycle_state_change = self._app_lifecycle_changed
        self.page.on_close = self._close

    def _app_lifecycle_changed(self, event: ft.AppLifecycleStateChangeEvent) -> None:
        """Refresh native playback state after the page returns to the foreground."""
        if event.state not in (ft.AppLifecycleState.SHOW, ft.AppLifecycleState.RESUME):
            return
        self.backend.refresh_state()
        self.backend.invalidate_media_session()
        # Refresh the session immediately with the current controller state;
        # the native position query above will publish a precise correction as
        # soon as it completes.
        self._sync_system_media(refresh_metadata=False)

    def _playback_changed(self, change: str = "state") -> None:
        if change != "volume":
            self._sync_system_media(refresh_metadata=change == "track")
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
        if self.active_panel == "queue" and change in {"queue", "track"}:
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
                cache = getattr(self, "thumbnails", None)
                artwork_uri = (
                    cache.artwork_uri(self.playback.external_thumbnail)
                    if cache is not None
                    else self.playback.external_thumbnail
                )
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
                    cache = getattr(self, "thumbnails", None)
                    artwork_uri = (
                        cache.artwork_uri(details.thumbnail)
                        if cache is not None
                        else details.thumbnail
                    )
                    # Melody does not currently persist album tags. The source
                    # name is still useful context in media panels without
                    # pretending it is an album title.
                    album = details.source_name
            self._system_media_key = media_key
            self._system_media_metadata = (title, artist, album, artwork_uri)
        else:
            title, artist, album, artwork_uri = self._system_media_metadata

        has_media = bool(title) and self.playback.media_session_active
        self.backend.sync_media_session(
            title=title if has_media else "",
            artist=artist,
            album=album,
            artwork_uri=artwork_uri,
            duration_ms=self.playback.duration_ms,
            position_ms=self.playback.position_ms,
            playing=self.playback.playing,
            loading=self.playback.snapshot.loading,
            has_next=has_media
            and self.playback.queue.peek_next(automatic=True) is not None,
            has_previous=has_media,
            repeat_mode=self.playback.queue.repeat.value,
            shuffle=self.playback.queue.shuffle,
            # A paused Android MediaSessionService is otherwise allowed to
            # disappear with its notification when the app task is removed.
            # Preserve it while Melody still owns a resumable queue; explicit
            # Stop clears has_media and remains the teardown boundary.
            keep_alive=has_media and bool(getattr(self.playback.queue, "items", ())),
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
            self.playback.stop()
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
            generation = self._download_play_requests.pop(record.id, None)
            relevant = pending[0] != "play" or (
                generation is not None and self.playback.is_current_request(generation)
            )
            if relevant:
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
        if (
            record.status in {"completed", "failed", "cancelled"}
            and self.selected_navigation == 2
            and hasattr(self, "library_list")
        ):
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
        self.playback.close()
        self.backend.close()
        self.tasks.close()
        self.persistence.close(wait=True)
        self.playback_workers.shutdown(wait=True, cancel_pending=True)
        # Closing a window must not wait for a slow network read or postprocess.
        # Running jobs observe cancellation at their next yt-dlp callback.
        self.downloads.shutdown(wait=False)
        self.workers.shutdown(wait=True, cancel_pending=True)

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


@dataclass
class AppResources:
    directory: Path
    store: ApplicationStore
    manager: MusicManager
    library: LibraryService
    providers: ProviderRegistry
    thumbnails: ThumbnailCache
    downloads: DownloadCoordinator
    available_track_ids: set[str]

    @classmethod
    def load(cls) -> AppResources:
        """Load only state required to render and resume the local application."""
        directory = _application_data_directory()
        store = ApplicationStore(directory / "state.json")
        settings = store.settings
        manager = MusicManager(directory / "library.mmdb", settings.download_directory)
        library = LibraryService(manager, store)
        providers = ProviderRegistry(settings=settings)
        thumbnails = ThumbnailCache(directory / "thumbnails")
        downloads = DownloadCoordinator(manager, store, settings, thumbnails=thumbnails)
        available = {
            str(track.id)
            for track in manager.list_tracks()
            if manager.track_path(track.id).is_file()
        }
        return cls(
            directory,
            store,
            manager,
            library,
            providers,
            thumbnails,
            downloads,
            available,
        )


async def main(page: ft.Page) -> None:
    page.title = "Melody — Loading library"
    loading = ft.Column(
        [ft.ProgressRing(), ft.Text("Loading your library…")],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True,
    )
    page.add(loading)
    try:
        resources = await asyncio.to_thread(AppResources.load)
        page.controls.clear()
        MusicPlayerApp(page, resources)
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
