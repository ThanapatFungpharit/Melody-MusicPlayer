from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import flet as ft

from musicplayer.application.cookie_files import (
    COOKIE_FILE_NAME,
    CookieFileError,
    ValidatedCookieFile,
    install_cookie_file,
    validate_cookie_file,
)
from musicplayer.application.models import AppSettings
from musicplayer.application.providers import ProviderRegistry
from musicplayer.ui.components.common import _theme_mode
from musicplayer.ui.theme import THEME_PALETTES, card

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class SettingsPage(_Base):
    """Settings page UI and persistence workflow."""

    def _settings_view(self) -> ft.Control:
        download = self._build_download_settings()
        appearance = self._build_appearance_settings()
        youtube_access = self._build_youtube_settings()
        data_management = self._build_data_management_settings()
        footer = self._build_settings_footer()
        return ft.Column(
            [
                self._context_header(
                    "Settings",
                    "Tune playback, downloads, YouTube access, personalization, and appearance.",
                ),
                self._responsive_grid(
                    [appearance, download, youtube_access, data_management],
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ),
                ft.Divider(height=1),
                footer,
            ],
            spacing=16,
            expand=True,
        )

    def _build_download_settings(self) -> ft.Container:
        self.settings_path = ft.TextField(
            value=self.settings.download_directory,
            label="Music & download folder",
            prefix_icon=ft.Icons.FOLDER_ROUNDED,
            height=56,
            border_radius=12,
            text_size=14,
        )
        self.settings_format = ft.Dropdown(
            value=self.settings.audio_format,
            label="Audio format",
            height=56,
            border_radius=12,
            options=[
                ft.DropdownOption(key="mp3", text="MP3"),
                ft.DropdownOption(key="m4a", text="M4A"),
                ft.DropdownOption(key="opus", text="Opus"),
            ],
        )
        self.settings_quality = ft.Dropdown(
            value=self.settings.audio_quality,
            label="Audio quality",
            height=56,
            border_radius=12,
            options=[
                ft.DropdownOption(key="best", text="Best available"),
                ft.DropdownOption(key="320", text="320 kbps"),
                ft.DropdownOption(key="256", text="256 kbps"),
                ft.DropdownOption(key="192", text="192 kbps"),
                ft.DropdownOption(key="128", text="128 kbps"),
            ],
        )
        self.settings_concurrency = ft.Slider(
            min=1,
            max=8,
            divisions=7,
            value=self.settings.concurrent_downloads,
            label="{value} simultaneous",
            on_change=self._settings_concurrency_changed,
        )
        self.settings_concurrency_value = ft.Text(
            f"{self.settings.concurrent_downloads} workers",
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.PRIMARY,
        )
        for control in (self.settings_format, self.settings_quality):
            control.col = {"xs": 12, "md": 6}
        return _settings_card(
            [
                self._settings_section_header(
                    ft.Icons.DOWNLOAD_FOR_OFFLINE_ROUNDED,
                    "Download defaults",
                    "Control where audio is saved and how new downloads are processed.",
                ),
                self.settings_path,
                self._responsive_grid(
                    [self.settings_format, self.settings_quality],
                    spacing=12,
                    run_spacing=12,
                ),
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text(
                                    "Concurrent downloads",
                                    weight=ft.FontWeight.BOLD,
                                ),
                                ft.Text(
                                    "Balance speed against network and CPU usage.",
                                    size=12,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        self.settings_concurrency_value,
                    ]
                ),
                self.settings_concurrency,
                ft.Container(
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.RESTART_ALT_ROUNDED,
                                size=20,
                                color=ft.Colors.PRIMARY,
                            ),
                            ft.Text(
                                "Download changes apply after restarting Melody.",
                                size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                                expand=True,
                            ),
                        ],
                        spacing=10,
                    ),
                    padding=12,
                    border_radius=12,
                    bgcolor=ft.Colors.PRIMARY_CONTAINER,
                ),
            ],
            columns={"xs": 12, "lg": 6, "xl": 4},
        )

    def _build_appearance_settings(self) -> ft.Container:
        self.settings_theme = ft.Dropdown(
            value=self.settings.theme,
            label="Theme",
            height=56,
            border_radius=12,
            options=[
                ft.DropdownOption(key="dark", text="Dark"),
                ft.DropdownOption(key="light", text="Light"),
                ft.DropdownOption(key="system", text="Use system setting"),
            ],
        )
        self.settings_resume = ft.Switch(
            label="Resume queue and playback state", value=self.settings.resume_session
        )
        self.settings_notifications = ft.Switch(
            label="Notify when tracks change", value=self.settings.notifications
        )
        # -- accent color picker ----------------------------------------
        self._selected_accent = self.settings.accent_color
        self.accent_picker = self._build_accent_picker()
        return _settings_card(
            [
                self._settings_section_header(
                    ft.Icons.TUNE_ROUNDED,
                    "Appearance & session",
                    "Choose how Melody looks and restores your workspace.",
                ),
                self.settings_theme,
                ft.Text(
                    "Accent color",
                    weight=ft.FontWeight.BOLD,
                ),
                self.accent_picker,
                ft.Divider(height=18),
                self.settings_resume,
                self.settings_notifications,
            ],
            columns={"xs": 12, "lg": 6, "xl": 4},
        )

    def _build_youtube_settings(self) -> ft.Container:
        self._pending_cookie_file: ValidatedCookieFile | None = None
        self._pending_cookie_name = ""
        self._remove_cookie_requested = False
        configured_cookie = bool(
            self.settings.cookie_file and Path(self.settings.cookie_file).is_file()
        )
        cookie_missing = bool(self.settings.cookie_file) and not configured_cookie
        if configured_cookie:
            cookie_status = f"Using {Path(self.settings.cookie_file).name}"
        elif cookie_missing:
            cookie_status = "The saved cookie file is missing. Upload a replacement."
        else:
            cookie_status = "No cookie file uploaded. YouTube requests are anonymous."
        self.settings_cookie_status = ft.Text(
            cookie_status,
            size=12,
            color=(ft.Colors.ERROR if cookie_missing else ft.Colors.ON_SURFACE_VARIANT),
            expand=True,
        )
        self.settings_cookie_upload = ft.Button(
            "Replace file" if configured_cookie else "Upload cookie file",
            icon=ft.Icons.UPLOAD_FILE_ROUNDED,
            on_click=self._pick_cookie_file,
        )
        self.settings_cookie_remove = ft.TextButton(
            "Remove",
            icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
            disabled=not configured_cookie,
            on_click=self._remove_cookie_file,
        )
        return _settings_card(
            [
                self._settings_section_header(
                    ft.Icons.SMART_DISPLAY_ROUNDED,
                    "YouTube access",
                    "YouTube is Melody’s default and only music source.",
                ),
                ft.Text(
                    "YouTube cookies",
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Container(
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.COOKIE_OUTLINED,
                                size=20,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                            self.settings_cookie_status,
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    padding=12,
                    border_radius=12,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                ),
                ft.Row(
                    [
                        self.settings_cookie_upload,
                        self.settings_cookie_remove,
                    ],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                ft.Text(
                    "Upload a UTF-8 Netscape cookies.txt export. Melody validates it and copies it to private app storage; it never reads browser cookie databases. The same flow works on desktop and Android.",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            columns={"xs": 12, "lg": 12, "xl": 4},
        )

    def _build_data_management_settings(self) -> ft.Container:
        return _settings_card(
            [
                self._settings_section_header(
                    ft.Icons.DELETE_SWEEP_ROUNDED,
                    "Reset / Data Management",
                    "Remove only the local data you choose. Every action asks for confirmation.",
                ),
                self._responsive_grid(
                    [
                        self._data_management_action(
                            "Clear Library",
                            "Remove every library track and all playlists. Audio files stay on disk.",
                            "Clear library",
                            ft.Icons.LIBRARY_MUSIC_ROUNDED,
                            "library",
                        ),
                        self._data_management_action(
                            "Clear Playlists",
                            "Delete user-created playlists while keeping every library track.",
                            "Clear playlists",
                            ft.Icons.QUEUE_MUSIC_ROUNDED,
                            "playlists",
                        ),
                        self._data_management_action(
                            "Reset Settings",
                            "Restore appearance, playback, download, and session settings to defaults.",
                            "Reset settings",
                            ft.Icons.RESTART_ALT_ROUNDED,
                            "settings",
                        ),
                    ],
                    spacing=12,
                    run_spacing=12,
                ),
                ft.Container(
                    self._responsive_grid(
                        [
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Icon(
                                                ft.Icons.WARNING_AMBER_ROUNDED,
                                                color=ft.Colors.ERROR,
                                            ),
                                            ft.Text(
                                                "Reset Everything",
                                                size=18,
                                                weight=ft.FontWeight.BOLD,
                                                color=ft.Colors.ERROR,
                                            ),
                                        ],
                                        spacing=8,
                                    ),
                                    ft.Text(
                                        "Clear the library and playlists, empty the playback queue, and restore all settings. Audio files, download history, and search history are preserved.",
                                        size=12,
                                        color=ft.Colors.ON_ERROR_CONTAINER,
                                    ),
                                ],
                                spacing=5,
                                col={"xs": 12, "md": 8},
                            ),
                            ft.Container(
                                ft.Button(
                                    "Reset everything",
                                    icon=ft.Icons.DELETE_FOREVER_ROUNDED,
                                    color=ft.Colors.ON_ERROR,
                                    bgcolor=ft.Colors.ERROR,
                                    height=52,
                                    on_click=lambda _: self._confirm_data_action(
                                        "everything"
                                    ),
                                ),
                                alignment=ft.Alignment.CENTER_RIGHT,
                                col={"xs": 12, "md": 4},
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=16,
                    border_radius=14,
                    bgcolor=ft.Colors.ERROR_CONTAINER,
                    border=ft.Border.all(1, ft.Colors.ERROR),
                ),
            ],
            columns={"xs": 12},
        )

    def _build_settings_footer(self) -> ft.ResponsiveRow:
        footer_copy = ft.Text(
            "Changes are saved locally on this device.",
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        footer_copy.col = {"xs": 12, "md": 5}
        footer_actions = ft.Container(
            ft.Row(
                [
                    ft.TextButton(
                        "Cancel",
                        height=48,
                        on_click=lambda _: self._cancel_settings(),
                    ),
                    ft.Button(
                        "Save settings",
                        icon=ft.Icons.SAVE_ROUNDED,
                        width=180,
                        height=48,
                        on_click=lambda _: self._save_settings(),
                    ),
                ],
                spacing=12,
                alignment=ft.MainAxisAlignment.END,
            ),
            alignment=ft.Alignment.CENTER_RIGHT,
        )
        footer_actions.col = {"xs": 12, "md": 7}
        return self._responsive_grid(
            [footer_copy, footer_actions],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _data_management_action(
        self,
        title: str,
        description: str,
        button_label: str,
        icon: Any,
        action: str,
    ) -> ft.Control:
        result = ft.Container(
            ft.Column(
                [
                    ft.Text(title, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        description,
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        expand=True,
                    ),
                    ft.OutlinedButton(
                        button_label,
                        icon=icon,
                        icon_color=ft.Colors.ERROR,
                        style=ft.ButtonStyle(color=ft.Colors.ERROR),
                        on_click=lambda _, selected=action: self._confirm_data_action(
                            selected
                        ),
                    ),
                ],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=14,
            border_radius=14,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
        )
        result.col = {"xs": 12, "lg": 4}
        return result

    def _confirm_data_action(self, action: str) -> None:
        track_count, playlist_count = self.manager.counts()
        track_word = "track" if track_count == 1 else "tracks"
        playlist_word = "playlist" if playlist_count == 1 else "playlists"
        details = {
            "library": (
                "Clear library?",
                (
                    f"Remove {track_count} library {track_word} and {playlist_count} "
                    f"{playlist_word}. All playlists, the playback queue, favorites, "
                    "and track-linked listening history will be cleared. Downloaded "
                    "and imported audio files stay on disk. Settings, download "
                    "history, and search history are not changed."
                ),
                "Clear library",
                False,
            ),
            "playlists": (
                "Clear playlists?",
                (
                    f"Delete {playlist_count} user-created {playlist_word}. Library "
                    "tracks, audio files, settings, the playback queue, and histories "
                    "are not changed."
                ),
                "Clear playlists",
                False,
            ),
            "settings": (
                "Reset settings?",
                (
                    "Restore appearance, volume, session, download, cookie, "
                    "and notification settings to their defaults. Library tracks, "
                    "playlists, audio files, download history, and search history are "
                    "not changed. Any cookie file copied into Melody’s private app "
                    "storage will be removed; external files are never deleted."
                ),
                "Reset settings",
                False,
            ),
            "everything": (
                "Reset everything?",
                (
                    f"Remove {track_count} library {track_word} and {playlist_count} "
                    f"{playlist_word}, clear the playback queue and track-linked "
                    "history, and restore every setting to its default. Audio files, "
                    "download history, and search history remain on this device. This "
                    "cannot be undone inside Melody."
                ),
                "Reset everything",
                True,
            ),
        }
        if action not in details:
            raise ValueError(f"Unknown data-management action: {action}")
        title, description, confirm_label, prominent = details[action]

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                icon=ft.Icon(
                    ft.Icons.WARNING_AMBER_ROUNDED,
                    color=ft.Colors.ERROR,
                    size=32,
                ),
                title=ft.Text(title),
                content=ft.Text(description),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    ft.Button(
                        confirm_label,
                        icon=(
                            ft.Icons.DELETE_FOREVER_ROUNDED
                            if prominent
                            else ft.Icons.DELETE_OUTLINE_ROUNDED
                        ),
                        color=ft.Colors.ON_ERROR if prominent else ft.Colors.ERROR,
                        bgcolor=ft.Colors.ERROR if prominent else None,
                        on_click=lambda _: self._run_data_action(action),
                    ),
                ],
            )
        )

    def _run_data_action(self, action: str) -> None:
        try:
            message = {
                "library": self._clear_library,
                "playlists": self._clear_playlists,
                "settings": self._reset_settings,
                "everything": self._reset_everything,
            }[action]()
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.page.pop_dialog()
            self._refresh_after_data_action(action)
            self._show_error(f"The reset could not be completed: {error}")
            return

        self.page.pop_dialog()
        self._refresh_after_data_action(action)
        self._show_message(message)

    def _clear_library(self) -> str:
        track_count, playlist_count = self.manager.clear_library()
        self._abandon_playlist_import()
        self.playback.clear_library_state()
        self.store.clear_library_data()
        self.selected_playlist_id = None
        if not track_count and not playlist_count:
            return "The library and playlists are already empty."
        return (
            f"Cleared {track_count} library track"
            f"{'s' if track_count != 1 else ''} and {playlist_count} playlist"
            f"{'s' if playlist_count != 1 else ''}. Audio files were kept."
        )

    def _clear_playlists(self) -> str:
        playlist_count = self.manager.clear_playlists()
        self._abandon_playlist_import()
        self.selected_playlist_id = None
        if not playlist_count:
            return "There are no playlists to clear."
        return (
            f"Deleted {playlist_count} playlist"
            f"{'s' if playlist_count != 1 else ''}. Library tracks were kept."
        )

    def _reset_settings(self) -> str:
        already_default = self._restore_default_settings()
        if already_default:
            return "Settings already use their default values."
        return "Settings restored to defaults. Download engine changes apply after restart."

    def _reset_everything(self) -> str:
        track_count, playlist_count = self.manager.clear_library()
        self._abandon_playlist_import()
        self.playback.clear_library_state()
        self.store.clear_library_data()
        self.selected_playlist_id = None
        already_default = self._restore_default_settings()
        if not track_count and not playlist_count and already_default:
            return "The library, playlists, and settings are already reset."
        return (
            "Library, playlists, and settings were reset. Audio files and unrelated "
            "history were kept. Download engine changes apply after restart."
        )

    def _restore_default_settings(self) -> bool:
        defaults = AppSettings()
        already_default = self.settings == defaults
        configured_cookie = self.settings.cookie_file
        managed_cookie = Path(self.data_directory) / COOKIE_FILE_NAME
        if (
            configured_cookie
            and Path(configured_cookie).resolve() == managed_cookie.resolve()
        ):
            managed_cookie.unlink(missing_ok=True)

        for name in defaults.__dataclass_fields__:
            setattr(self.settings, name, getattr(defaults, name))
        self.store.save_settings(self.settings)
        self.playback.apply_settings(self.settings)
        self.providers = ProviderRegistry(settings=self.settings)
        self.page.theme_mode = _theme_mode(self.settings.theme)
        self._pending_cookie_file = None
        self._pending_cookie_name = ""
        self._remove_cookie_requested = False
        return already_default

    def _refresh_after_data_action(self, action: str) -> None:
        if action in {"library", "playlists", "everything"}:
            self.selected_playlist_id = None
            if hasattr(self, "content") and hasattr(self, "rail"):
                self.navigate(self.selected_navigation)
        if hasattr(self, "player_bar"):
            self._refresh_player()
        if getattr(self, "active_panel", None) and getattr(self, "context_sheet", None):
            self._refresh_context_panel()
        else:
            self.page.update()

    @staticmethod
    def _settings_section_header(icon: Any, title: str, subtitle: str) -> ft.Control:
        return ft.Row(
            [
                ft.Container(
                    ft.Icon(icon, color=ft.Colors.PRIMARY, size=24),
                    width=46,
                    height=46,
                    border_radius=14,
                    bgcolor=ft.Colors.PRIMARY_CONTAINER,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Column(
                    [
                        ft.Text(title, size=18, weight=ft.FontWeight.BOLD),
                        ft.Text(
                            subtitle,
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _settings_concurrency_changed(self, event: Any) -> None:
        self.settings_concurrency_value.value = f"{int(event.control.value)} workers"
        self.page.update(self.settings_concurrency_value)

    def _build_accent_picker(self) -> ft.Control:
        """Build a row of clickable accent-color swatches."""
        swatches: list[ft.Control] = []
        for name, hex_color in THEME_PALETTES.items():
            selected = name == self._selected_accent
            swatch = ft.Container(
                ft.Column(
                    [
                        ft.Container(
                            width=40,
                            height=40,
                            border_radius=20,
                            bgcolor=hex_color,
                            border=ft.Border.all(
                                3 if selected else 1,
                                ft.Colors.ON_SURFACE
                                if selected
                                else ft.Colors.OUTLINE_VARIANT,
                            ),
                            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
                        ),
                        ft.Text(
                            name.capitalize(),
                            size=11,
                            text_align=ft.TextAlign.CENTER,
                            weight=ft.FontWeight.BOLD
                            if selected
                            else ft.FontWeight.NORMAL,
                        ),
                    ],
                    spacing=4,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                on_click=lambda _, n=name: self._select_accent(n),
                padding=4,
            )
            swatches.append(swatch)
        return ft.Row(swatches, spacing=8, scroll=ft.ScrollMode.AUTO)

    def _select_accent(self, name: str) -> None:
        """Update the visually selected accent palette."""
        self._selected_accent = name
        self.accent_picker = self._build_accent_picker()
        self._refresh_context_panel()

    async def _pick_cookie_file(self, _: Any) -> None:
        try:
            files = await self.file_picker.pick_files(
                dialog_title="Upload YouTube cookies",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["txt"],
                allow_multiple=False,
                with_data=True,
            )
        except (ft.FletException, OSError, RuntimeError) as error:
            self._show_error(f"The cookie file picker could not be opened: {error}")
            return
        if not files:
            return

        selected = files[0]
        try:
            content = selected.bytes
            if content is None and selected.path:
                content = Path(selected.path).read_bytes()
            if content is None:
                raise CookieFileError("The selected cookie file could not be read.")
            validated = validate_cookie_file(bytes(content))
        except (CookieFileError, OSError) as error:
            self._show_error(str(error))
            return

        self._pending_cookie_file = validated
        self._pending_cookie_name = selected.name
        self._remove_cookie_requested = False
        self.settings_cookie_status.value = (
            f"Ready to save {selected.name} ({validated.cookie_count} "
            f"cookie{'s' if validated.cookie_count != 1 else ''})."
        )
        self.settings_cookie_status.color = ft.Colors.PRIMARY
        self.settings_cookie_upload.content = "Choose another file"
        self.settings_cookie_remove.disabled = False
        self.page.update(
            self.settings_cookie_status,
            self.settings_cookie_upload,
            self.settings_cookie_remove,
        )

    def _remove_cookie_file(self, _: Any) -> None:
        self._pending_cookie_file = None
        self._pending_cookie_name = ""
        self._remove_cookie_requested = True
        self.settings_cookie_status.value = (
            "The cookie file will be removed when settings are saved."
        )
        self.settings_cookie_status.color = ft.Colors.ON_SURFACE_VARIANT
        self.settings_cookie_upload.content = "Upload cookie file"
        self.settings_cookie_remove.disabled = True
        self.page.update(
            self.settings_cookie_status,
            self.settings_cookie_upload,
            self.settings_cookie_remove,
        )

    def _cancel_settings(self) -> None:
        self._pending_cookie_file = None
        self._pending_cookie_name = ""
        self._remove_cookie_requested = False
        self._close_context_panel()

    def _save_settings(self) -> None:
        folder = Path(self.settings_path.value).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._show_error(f"The download folder cannot be used: {error}")
            return
        if (
            folder.resolve() != Path(self.settings.download_directory).resolve()
            and self.manager.counts()[0]
        ):
            self._show_error(
                "To protect existing files, the music folder can only be changed "
                "while the library is empty. Remove or migrate your library first."
            )
            return
        cookie_changed = bool(
            self._pending_cookie_file is not None or self._remove_cookie_requested
        )
        cookie_file = self.settings.cookie_file
        try:
            if self._pending_cookie_file is not None:
                destination = Path(self.data_directory) / COOKIE_FILE_NAME
                cookie_file = str(
                    install_cookie_file(self._pending_cookie_file, destination)
                )
            elif self._remove_cookie_requested:
                configured = (
                    Path(self.settings.cookie_file)
                    if self.settings.cookie_file
                    else None
                )
                managed = Path(self.data_directory) / COOKIE_FILE_NAME
                if configured and configured.resolve() == managed.resolve():
                    configured.unlink(missing_ok=True)
                cookie_file = ""
        except OSError as error:
            self._show_error(f"The cookie file could not be saved: {error}")
            return

        restart_required = any(
            (
                str(folder) != self.settings.download_directory,
                self.settings_format.value != self.settings.audio_format,
                self.settings_quality.value != self.settings.audio_quality,
                int(self.settings_concurrency.value or 0)
                != self.settings.concurrent_downloads,
                cookie_changed,
            )
        )
        self.settings.download_directory = str(folder)
        self.settings.audio_format = self.settings_format.value or "mp3"
        self.settings.audio_quality = self.settings_quality.value or "best"
        self.settings.concurrent_downloads = int(self.settings_concurrency.value or 0)
        self.settings.theme = self.settings_theme.value or "system"
        self.settings.accent_color = self._selected_accent
        self.settings.cookie_file = cookie_file
        self.settings.resume_session = bool(self.settings_resume.value)
        self.settings.notifications = bool(self.settings_notifications.value)
        self.store.save_settings(self.settings)
        self._pending_cookie_file = None
        self._pending_cookie_name = ""
        self._remove_cookie_requested = False
        self.providers = ProviderRegistry(settings=self.settings)
        self.page.theme_mode = _theme_mode(self.settings.theme)
        self._reapply_accent()
        self.page.update()
        self._show_message(
            "Settings saved. Restart to apply download changes."
            if restart_required
            else "Settings saved."
        )


def _settings_card(
    controls: list[ft.Control], *, columns: ft.ResponsiveNumber
) -> ft.Container:
    section = card(
        ft.Column(
            controls,
            spacing=16,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        padding=22,
    )
    section.col = columns
    return section
