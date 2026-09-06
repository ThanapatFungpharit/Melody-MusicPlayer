from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import flet as ft

from musicplayer.core.library.models import Playlist, Track
from musicplayer.ui.components.common import (
    _artwork,
    _empty_state,
    _format_duration,
    _page_header,
    _track_credit,
    _track_title,
)

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class LibraryPage(_Base):
    """Library page UI and local-track workflows."""

    def _initialize_library_page(self) -> None:
        self.library_mode = "tracks"

    def _library_view(self) -> ft.Control:
        self.library_query = ft.TextField(
            hint_text="Filter your library",
            prefix_icon=ft.Icons.SEARCH_ROUNDED,
            border_radius=14,
            on_change=lambda _: self._refresh_library_list(),
        )
        self.library_sort = ft.Dropdown(
            value="recent",
            options=[
                ft.DropdownOption(key="recent", text="Recently added"),
                ft.DropdownOption(key="oldest", text="Oldest added"),
                ft.DropdownOption(key="title", text="Title"),
            ],
            label="Sort by",
            on_select=lambda _: self._refresh_library_list(),
        )
        self.library_query.col = {"xs": 12, "md": 8}
        self.library_sort.col = {"xs": 12, "md": 4}
        self.library_list = ft.Column(spacing=7)
        self._refresh_library_list(update=False)
        library_header = _page_header(
            "Library",
            "Downloaded and local music, ready to play and organize.",
        )
        library_header.col = {"xs": 12, "lg": 7}
        library_actions = ft.Container(
            ft.Row(
                [
                    ft.TextButton(
                        "Add files",
                        icon=ft.Icons.LIBRARY_ADD_ROUNDED,
                        on_click=self._pick_local_files,
                    ),
                    ft.Button(
                        "Play",
                        icon=ft.Icons.PLAY_ARROW_ROUNDED,
                        on_click=lambda _: self._play_visible_library(),
                    ),
                    ft.IconButton(
                        ft.Icons.SHUFFLE_ROUNDED,
                        tooltip="Shuffle visible tracks",
                        on_click=lambda _: self._play_visible_library(shuffle=True),
                    ),
                    ft.IconButton(
                        ft.Icons.ADD_TO_QUEUE_ROUNDED,
                        tooltip="Add visible tracks to queue",
                        on_click=lambda _: self._queue_visible_library(),
                    ),
                ],
                spacing=6,
                scroll=ft.ScrollMode.AUTO,
                alignment=ft.MainAxisAlignment.END,
            ),
            alignment=ft.Alignment.CENTER_RIGHT,
            col={"xs": 12, "lg": 5},
        )
        library_sections = ft.SegmentedButton(
            segments=[
                ft.Segment("tracks", icon=ft.Icons.MUSIC_NOTE_ROUNDED, label="Tracks"),
                ft.Segment(
                    "playlists", icon=ft.Icons.QUEUE_MUSIC_ROUNDED, label="Playlists"
                ),
            ],
            selected=[self.library_mode],
            show_selected_icon=False,
            on_change=lambda event: self._set_library_mode(event.control.selected),
        )
        return ft.Column(
            [
                self._responsive_grid(
                    [library_header, library_actions],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                library_sections,
                self._responsive_grid(
                    [
                        self.library_query,
                        self.library_sort,
                    ],
                    spacing=12,
                ),
                ft.Divider(height=1),
                ft.Column([self.library_list], scroll=ft.ScrollMode.AUTO, expand=True),
            ],
            spacing=16,
            expand=True,
        )

    def _refresh_library_list(self, *, update: bool = True) -> None:
        if self.library_mode not in {"tracks", "playlists"}:
            self.library_mode = "tracks"
        query = getattr(self, "library_query", None)
        sort = getattr(self, "library_sort", None)
        if self.library_mode == "tracks":
            tracks = self.library.tracks(
                query=query.value if query else "",
                sort=(sort.value or "recent") if sort else "recent",
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
            search_term = (query.value or "").casefold() if query else ""
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
            query=self.library_query.value, sort=self.library_sort.value or "recent"
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
        if paths and self._submit_background(self._import_local_files, paths):
            self._show_message(
                f"Importing {len(paths)} local file{'s' if len(paths) != 1 else ''}…"
            )

    def _import_local_files(self, paths: list[str]) -> None:
        imported = 0
        failures: list[str] = []
        for path in paths:
            try:
                self.library.import_local_file(path)
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
        self,
        track: Track,
        index: int,
        playlists: tuple[Playlist, ...] | None = None,
    ) -> ft.Control:
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
                    content="Remove download…",
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    on_click=lambda _, item=track: self._delete_track_dialog(item),
                ),
            ]
        )
        compact = self.compact_layout
        leading: list[ft.Control] = []
        if not compact:
            leading.append(
                ft.Text(
                    str(index + 1),
                    width=30,
                    text_align=ft.TextAlign.CENTER,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            )
        trailing: list[ft.Control] = (
            [
                ft.IconButton(
                    ft.Icons.PLAY_ARROW_ROUNDED,
                    tooltip="Play",
                    on_click=lambda _, item=str(track.id): self.playback.play_track(
                        item
                    ),
                ),
                ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ_ROUNDED, items=menu_items),
            ]
            if compact
            else [
                ft.Text(
                    _format_duration(details.duration),
                    width=50,
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.IconButton(
                    ft.Icons.FAVORITE_ROUNDED
                    if details.favorite
                    else ft.Icons.FAVORITE_BORDER_ROUNDED,
                    icon_color=ft.Colors.PINK_400 if details.favorite else None,
                    tooltip="Favorite",
                    on_click=lambda _, item=str(track.id): self._toggle_favorite(item),
                ),
                ft.IconButton(
                    ft.Icons.PLAY_ARROW_ROUNDED,
                    tooltip="Play",
                    on_click=lambda _, item=str(track.id): self.playback.play_track(
                        item
                    ),
                ),
                ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ_ROUNDED, items=menu_items),
            ]
        )
        return ft.Container(
            ft.Row(
                [
                    *leading,
                    _artwork(details.thumbnail, 50),
                    ft.Column(
                        [
                            ft.Text(
                                _track_title(track),
                                weight=ft.FontWeight.W_600,
                                max_lines=1,
                            ),
                            ft.Text(
                                _track_credit(details),
                                size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                                max_lines=1,
                            ),
                        ],
                        spacing=3,
                        expand=True,
                    ),
                    *trailing,
                ],
                spacing=10,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border_radius=14,
            bgcolor=ft.Colors.SURFACE_CONTAINER
            if index % 2 == 0
            else ft.Colors.SURFACE,
            on_click=lambda _, item=str(track.id): self.playback.play_track(item),
        )

    def _toggle_favorite(self, track_id: str) -> None:
        self.library.toggle_favorite(track_id)
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

        def save(_: Any) -> None:
            try:
                self.library.rename_track(track.id, field.value)
            except ValueError as error:
                self._show_error(str(error))
                return
            self.page.pop_dialog()
            self._refresh_library_list()
            self._refresh_player()
            if self.playback.current_track_id == str(track.id):
                self._sync_system_media()

        self.page.show_dialog(
            ft.AlertDialog(
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
                scrollable=True,
                inset_padding=ft.Padding.symmetric(horizontal=16, vertical=24),
                actions=[
                    ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())
                ],
            )
        )

    def _delete_track_dialog(self, track: Track) -> None:
        choice = ft.Checkbox(label="Also delete the downloaded audio file", value=False)

        def confirm(_: Any) -> None:
            try:
                self.library.delete_track(track.id, delete_file=bool(choice.value))
            except OSError as error:
                self._show_error(
                    f"The library entry was removed, but the file could not be deleted: {error}"
                )
            if not self.manager.has_track(track.id):
                for index in reversed(
                    [
                        index
                        for index, track_id in enumerate(self.playback.queue.items)
                        if track_id == str(track.id)
                    ]
                ):
                    self.playback.remove_queue_item(index)
            self.page.pop_dialog()
            self.navigate(2)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Remove track?"),
                content=ft.Column(
                    [
                        ft.Text(
                            f"“{_track_title(track)}” will be removed from the library and every playlist."
                        ),
                        choice,
                    ],
                    tight=True,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    ft.Button(
                        "Remove", icon=ft.Icons.DELETE_OUTLINE_ROUNDED, on_click=confirm
                    ),
                ],
            )
        )
