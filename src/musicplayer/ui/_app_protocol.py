"""Typing-only protocol describing the combined MusicPlayerApp interface.

Every page and component mixin in Melody is composed into a single concrete
class (``MusicPlayerApp``).  The mixins freely reference attributes that live
on sibling mixins or on the root class.  At runtime the composition works
because Python's MRO finds the attribute; but static type checkers cannot
see across unrelated classes.

This module declares a ``Protocol`` that lists every attribute and method
the mixins expect on ``self``.  Each mixin class conditionally inherits from
the protocol (under ``TYPE_CHECKING``) so that the checker can verify
attribute access without affecting the runtime class hierarchy.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import flet as ft

if TYPE_CHECKING:
    from musicplayer.application.downloads import DownloadCoordinator
    from musicplayer.application.library_service import LibraryService
    from musicplayer.application.models import AppSettings
    from musicplayer.application.playback import PlaybackController
    from musicplayer.application.providers import ProviderRegistry
    from musicplayer.application.store import ApplicationStore
    from musicplayer.core.concurrency import LazyBoundedExecutor
    from musicplayer.core.library import MusicManager


class AppProtocol(Protocol):
    """Structural interface that every Melody mixin may assume on ``self``."""

    # -- core services ---------------------------------------------------
    page: ft.Page
    settings: AppSettings
    store: ApplicationStore
    manager: MusicManager
    library: LibraryService
    playback: PlaybackController
    providers: ProviderRegistry
    downloads: DownloadCoordinator
    workers: LazyBoundedExecutor
    file_picker: ft.FilePicker
    data_directory: Path

    # -- layout state ----------------------------------------------------
    compact_layout: bool
    selected_navigation: int
    active_panel: str | None
    context_sheet: ft.BottomSheet | None
    pending_download_actions: dict[str, tuple[str, str | None]]
    COMPACT_BREAKPOINT: int

    # -- navigation / shell controls -------------------------------------
    content: ft.Container
    rail: ft.NavigationRail
    rail_holder: ft.Container
    mobile_header: ft.Container
    mobile_drawer: ft.NavigationDrawer
    bottom_nav: ft.NavigationBar
    mobile_menu_button: ft.IconButton
    download_button: ft.IconButton
    system_safe_area: ft.SafeArea
    player_bar: ft.Container

    # -- player bar controls ---------------------------------------------
    player_art: ft.Container
    player_title: ft.Text
    player_credit: ft.Text
    favorite_button: ft.IconButton
    shuffle_button: ft.IconButton
    play_button: ft.IconButton
    repeat_button: ft.IconButton
    position_label: ft.Text
    duration_label: ft.Text
    seek_slider: ft.Slider
    volume_button: ft.IconButton
    volume_slider: ft.Slider
    volume_label: ft.Text
    volume_down_button: ft.IconButton
    volume_up_button: ft.IconButton
    queue_button: ft.IconButton
    player_info: ft.Row
    skip_previous_button: ft.IconButton
    skip_next_button: ft.IconButton
    transport_row: ft.Row
    seek_row: ft.Row
    player_controls: ft.Column
    player_volume: ft.Row
    player_layout: ft.Row

    # -- search page state -----------------------------------------------
    search_query: ft.TextField
    search_provider: ft.Dropdown
    search_results_area: ft.Column
    search_loading: bool

    # -- library page state ----------------------------------------------
    library_mode: str
    library_query: ft.TextField
    library_sort: ft.Dropdown
    library_list: ft.Column

    # -- playlists page state --------------------------------------------
    selected_playlist_id: str | None
    playlist_import_session: Any
    playlist_import_loading: bool
    playlist_import_lock: Any
    playlist_import_retry: Any

    # -- context panel state ---------------------------------------------
    context_panel_body: ft.Container

    # -- settings page state ---------------------------------------------
    _selected_accent: str
    accent_picker: ft.Control
    _pending_cookie_file: Any
    _pending_cookie_name: str
    _remove_cookie_requested: bool
    applied_settings: AppSettings
    settings_path: ft.TextField
    settings_format: ft.Dropdown
    settings_quality: ft.Dropdown
    settings_concurrency: ft.Slider
    settings_concurrency_value: ft.Text
    settings_theme: ft.Dropdown
    settings_resume: ft.Switch
    settings_notifications: ft.Switch
    settings_cookie_status: ft.Text
    settings_cookie_upload: ft.Button
    settings_cookie_remove: ft.TextButton
    _last_announced_track: str | None

    # -- navigation methods ----------------------------------------------
    def navigate(self, index: int) -> None: ...
    def _build_shell(self) -> None: ...

    # -- player methods --------------------------------------------------
    def _build_player_bar(self) -> ft.Container: ...
    def _apply_player_layout(self) -> None: ...
    def _refresh_player(self) -> None: ...
    def _refresh_player_progress(self) -> None: ...
    def _refresh_player_volume(self) -> None: ...
    def _sync_system_media(self, *, refresh_metadata: bool = ...) -> None: ...
    def _set_player_progress(self) -> None: ...
    def _set_player_volume(self) -> None: ...

    # -- context panel methods -------------------------------------------
    def _open_queue_panel(self) -> None: ...
    def _open_downloads_panel(self) -> None: ...
    def _open_settings_panel(self) -> None: ...
    def _close_context_panel(self) -> None: ...
    def _refresh_context_panel(self) -> None: ...
    def _refresh_download_badge(self) -> None: ...

    # -- page view builders ----------------------------------------------
    def _home_view(self) -> ft.Control: ...
    def _search_view(self) -> ft.Control: ...
    def _library_view(self) -> ft.Control: ...
    def _playlists_view(self) -> ft.Control: ...
    def _queue_view(self) -> ft.Control: ...
    def _downloads_view(self) -> ft.Control: ...
    def _settings_view(self) -> ft.Control: ...

    # -- collection helpers (SharedUIComponents) -------------------------
    def _play_collection(
        self,
        tracks: Any,
        *,
        shuffle: bool = False,
    ) -> None: ...
    def _queue_collection(self, tracks: Any) -> None: ...
    def _playlist_card(self, playlist: Any) -> ft.Control: ...
    def _context_header(
        self, title: str, subtitle: str, *actions: ft.Control
    ) -> ft.Control: ...

    @classmethod
    def _responsive_grid(
        cls,
        controls: list[ft.Control],
        *,
        spacing: int | dict[str, int] | None = ...,
        run_spacing: int | dict[str, int] | None = ...,
        scroll: ft.ScrollMode | None = ...,
        expand: bool | int | None = ...,
        vertical_alignment: ft.CrossAxisAlignment = ...,
    ) -> ft.ResponsiveRow: ...

    # -- layout helpers --------------------------------------------------
    def _is_compact(self, width: float | None = ...) -> bool: ...
    def _accent_hex(self) -> str: ...
    def _reapply_accent(self) -> None: ...
    def _content_padding(self, width: float | None = ...) -> ft.Padding: ...
    def _is_mobile_platform(self) -> bool: ...

    # -- messaging / background ------------------------------------------
    def _show_message(self, message: str) -> None: ...
    def _show_error(self, message: str) -> None: ...
    def _submit_background(self, fn: Any, /, *args: Any) -> bool: ...

    # -- library helpers -------------------------------------------------
    def _toggle_favorite(self, track_id: str) -> None: ...
    def _toggle_current_favorite(self, event: Any) -> None: ...
    def _track_details_dialog(self, track: Any) -> None: ...
    def _add_to_playlist(self, playlist_id: str, track_id: str) -> None: ...
    def _refresh_library_list(self, *, update: bool = ...) -> None: ...

    # -- playlist page helpers -------------------------------------------
    def _open_playlist(self, playlist_id: str) -> None: ...
    def _play_playlist(self, playlist: Any, *, shuffle: bool = ...) -> None: ...
    def _initialize_search_page(self) -> None: ...
    def _initialize_library_page(self) -> None: ...
    def _initialize_playlists_page(self) -> None: ...
    def _continue_playlist_import(
        self,
        completed_url: str = ...,
        completed_track_ids: tuple[str, ...] = ...,
    ) -> None: ...
    def _abandon_playlist_import(self, playlist_id: str | None = ...) -> bool: ...

    # -- download helpers ------------------------------------------------
    def _download_changed(self, record: Any) -> None: ...

    # -- close drawer helpers --------------------------------------------
    async def _close_mobile_drawer(self) -> None: ...
