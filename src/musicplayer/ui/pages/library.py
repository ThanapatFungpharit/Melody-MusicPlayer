from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import flet as ft

from musicplayer.application.contracts import Playlist, Track
from musicplayer.ui.components.common import (
    _empty_state,
    _format_duration,
    _track_title,
)
from musicplayer.ui.tasks import page_action

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class LibraryPage(_Base):
    """Library page UI and local-track workflows."""

    def _initialize_library_page(self) -> None:
        self.library_mode = "tracks"
        self.library_query_text = ""
        self.library_sort_key = "recent"

    def _library_view(self) -> ft.Control:
        return self.views._library_view(self)

    def _refresh_library_list(self, *, update: bool = True) -> None:
        if self.library_mode not in {"tracks", "playlists"}:
            self.library_mode = "tracks"
        if self.library_mode == "tracks":
            tracks = self.library.tracks(
                query=self.library_query_text,
                sort=self.library_sort_key,
            )
            # Playlist menus share one immutable snapshot for the whole render;
            # fetching and copying the same collection once per row made large
            # libraries increasingly expensive to display.
            playlists = self.manager.list_playlists()
            controls = [
                self._library_track_row(track, index, playlists)
                for index, track in enumerate(tracks)
            ]
        elif self.library_mode == "playlists":
            search_term = self.library_query_text.casefold()
            controls: list[ft.Control] = [
                self._playlist_card(playlist)
                for playlist in self.manager.list_playlists()
                if not search_term or search_term in playlist.name.casefold()
            ]
            if controls:
                controls = [ft.Row(controls, wrap=True, spacing=14, run_spacing=14)]
        self.library_list.controls = controls
        if not controls:
            self.library_list.controls = [
                _empty_state(
                    ft.Icons.LIBRARY_MUSIC_ROUNDED,
                    "Nothing matches this view. Search for music or add local files.",
                )
            ]
        if update:
            self.page.update(self.library_list)

    def _set_library_mode(self, selected: list[str]) -> None:
        self.library_mode = selected[0] if selected else "tracks"
        self._refresh_library_list()

    def _visible_library_tracks(self) -> tuple[Track, ...]:
        return self.library.tracks(
            query=self.library_query_text, sort=self.library_sort_key
        )

    def _play_visible_library(self, *, shuffle: bool = False) -> None:
        self._play_collection(self._visible_library_tracks(), shuffle=shuffle)

    def _queue_visible_library(self) -> None:
        self._queue_collection(self._visible_library_tracks())

    async def _pick_local_files(self, _: Any) -> None:
        files = await self.file_picker.pick_files(
            dialog_title="Add music to Melody",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["mp3", "m4a", "opus", "wav", "flac", "ogg", "aac"],
            allow_multiple=True,
        )
        paths = [item.path for item in (files or []) if item.path]
        if paths:
            self._import_local_files(paths)
            self._show_message(
                f"Importing {len(paths)} local file{'s' if len(paths) != 1 else ''}…"
            )

    @page_action
    async def _import_local_files(self, paths: list[str]) -> None:
        imported = 0
        failures: list[str] = []
        for path in paths:
            try:
                await self.tasks.io(self.library.import_local_file, path)
            except (OSError, ValueError) as error:
                failures.append(f"{Path(path).name}: {error}")
            else:
                imported += 1
        if self.selected_navigation == 2:
            self._refresh_library_list()
        if failures:
            self._show_error(
                f"Imported {imported} file{'s' if imported != 1 else ''}. "
                + " • ".join(failures[:3])
            )
        else:
            self._show_message(
                f"Added {imported} local file{'s' if imported != 1 else ''} to the library."
            )

    def _library_track_row(
        self, track: Track, index: int, playlists: tuple[Playlist, ...] | None = None
    ) -> ft.Control:
        return self.views._library_track_row(self, track, index, playlists)

    @page_action
    async def _toggle_favorite(self, track_id: str) -> None:
        await self.tasks.io(self.library.toggle_favorite, track_id)
        if self.selected_navigation == 2:
            self._refresh_library_list()
        elif self.selected_navigation == 0:
            self.navigate(0)
        elif self.selected_navigation == 3:
            self.navigate(3)
        self._refresh_player()

    def _toggle_current_favorite(self, event: Any) -> None:
        if self.playback.current_track_id:
            self._toggle_favorite(self.playback.current_track_id)

    def _rename_track_dialog(self, track: Track) -> None:
        field = ft.TextField(
            value=_track_title(track), label="Track title", autofocus=True
        )

        async def save(_: Any) -> None:
            try:
                await self.tasks.io(self.library.rename_track, track.id, field.value)
            except ValueError as error:
                self._show_error(str(error))
                return
            self.page.pop_dialog()
            if self.selected_navigation == 2:
                self._refresh_library_list()
            else:
                self.navigate(self.selected_navigation)
            self._refresh_player()
            if self.playback.current_track_id == str(track.id):
                self._sync_system_media()

        self.page.show_dialog(
            ft.AlertDialog(
                scrollable=True,
                modal=True,
                title=ft.Text("Edit track title"),
                content=field,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    ft.Button("Save", on_click=save),
                ],
            )
        )

    def _track_details_dialog(self, track: Track) -> None:
        details = self.library.details(track.id)
        try:
            path = str(self.manager.track_path(track.id))
        except (KeyError, ValueError):
            path = "Unavailable"
        rows = [
            ("Title", _track_title(track)),
            ("Uploader / channel", details.uploader or "—"),
            ("Duration", _format_duration(details.duration)),
            ("Source", details.source_name or track.source or "Local"),
            ("File", path),
        ]
        self.page.show_dialog(
            ft.AlertDialog(
                scrollable=True,
                modal=True,
                title=ft.Text("Track details"),
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(
                                    label,
                                    width=130,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                                ft.Text(
                                    value, selectable=True, expand=True, max_lines=3
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        )
                        for label, value in rows
                    ],
                    tight=True,
                ),
                inset_padding=ft.Padding.symmetric(horizontal=16, vertical=24),
                actions=[
                    ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())
                ],
            )
        )

    def _delete_track_dialog(self, track: Track) -> None:
        async def confirm(_: Any) -> None:
            queue_indexes = [
                index
                for index, track_id in enumerate(self.playback.queue.items)
                if track_id == str(track.id)
            ]
            # Detach the file from the active player before deleting it. The
            # queue controller pauses or advances the backend as needed.
            for index in reversed(queue_indexes):
                self.playback.remove_queue_item(index)
            try:
                await self.tasks.io(self.library.delete_track_and_file, track.id)
            except OSError as error:
                self._show_error(f"The track could not be deleted completely: {error}")
            self.page.pop_dialog()
            self.navigate(2)

        self.page.show_dialog(
            ft.AlertDialog(
                scrollable=True,
                modal=True,
                title=ft.Text("Delete track?"),
                content=ft.Column(
                    [
                        ft.Text(
                            f"“{_track_title(track)}” will be deleted from the library, every playlist, and managed local storage."
                        ),
                    ],
                    tight=True,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    ft.Button(
                        "Delete track",
                        icon=ft.Icons.DELETE_FOREVER_ROUNDED,
                        on_click=confirm,
                    ),
                ],
            )
        )

    def _library_track_menu(
        self, track: Track, index: int, playlists: tuple[Playlist, ...] | None = None
    ) -> list[ft.PopupMenuItem]:
        details = self.library.details(track.id)
        playlists = (
            playlists if playlists is not None else self.manager.list_playlists()
        )
        menu_items: list[ft.PopupMenuItem] = [
            ft.PopupMenuItem(
                content="Play next",
                icon=ft.Icons.QUEUE_PLAY_NEXT_ROUNDED,
                on_click=lambda _, item=str(track.id): self.playback.add_next(item),
            ),
            ft.PopupMenuItem(
                content="Add to queue",
                icon=ft.Icons.ADD_TO_QUEUE_ROUNDED,
                on_click=lambda _, item=str(track.id): self.playback.add_last(item),
            ),
        ]
        menu_items.extend(
            ft.PopupMenuItem(
                content=f"Add to {playlist.name}",
                icon=ft.Icons.PLAYLIST_ADD_ROUNDED,
                on_click=lambda _, pid=str(playlist.id), tid=str(track.id): (
                    self._add_to_playlist(pid, tid)
                ),
            )
            for playlist in playlists
        )
        menu_items.extend(
            [
                ft.PopupMenuItem(
                    content="Remove favorite" if details.favorite else "Favorite",
                    icon=ft.Icons.FAVORITE_BORDER_ROUNDED,
                    on_click=lambda _, item=str(track.id): self._toggle_favorite(item),
                ),
                ft.PopupMenuItem(
                    content="Edit title",
                    icon=ft.Icons.EDIT_ROUNDED,
                    on_click=lambda _, item=track: self._rename_track_dialog(item),
                ),
                ft.PopupMenuItem(
                    content="View details",
                    icon=ft.Icons.INFO_OUTLINE_ROUNDED,
                    on_click=lambda _, item=track: self._track_details_dialog(item),
                ),
                ft.PopupMenuItem(
                    content="Delete track and file…",
                    icon=ft.Icons.DELETE_FOREVER_ROUNDED,
                    on_click=lambda _, item=track: self._delete_track_dialog(item),
                ),
            ]
        )
        return menu_items

    def _library_query_changed(self, event) -> None:
        self.library_query_text = event.control.value or ""
        self._refresh_library_list()

    def _library_sort_changed(self, event) -> None:
        self._set_library_sort(event.control.value or "recent")

    def _set_library_sort(self, value: str) -> None:
        self.library_sort_key = value
        sort_control = getattr(self, "library_sort", None)
        if sort_control is not None:
            sort_control.value = value
        self._refresh_library_list()
