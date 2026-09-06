from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from threading import RLock, Timer
from typing import TYPE_CHECKING, Any

import flet as ft

from musicplayer.application.downloads import DuplicateDownloadError
from musicplayer.application.models import SearchResult
from musicplayer.application.providers import (
    ProviderError,
    RemotePlaylist,
    is_youtube_playlist_url,
)
from musicplayer.core.concurrency import WorkerQueueFull
from musicplayer.core.library.models import Playlist, Track
from musicplayer.core.library.utils import source_key
from musicplayer.ui.components.common import (
    _artwork,
    _empty_state,
    _format_duration,
    _page_header,
    _track_credit,
    _track_title,
)
from musicplayer.ui.theme import card

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _PlaylistBase = AppProtocol
else:
    _PlaylistBase = object


@dataclass
class _PlaylistImportSession:
    playlist_id: str
    name: str
    pending: deque[tuple[str, SearchResult]]
    order: tuple[str, ...]
    resolved: dict[str, str] = field(default_factory=dict)
    waiting_key: str | None = None
    waiting_owned: bool = False
    waiting_task_id: str | None = None
    reused: int = 0
    downloaded: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return len(self.order)


class PlaylistsPage(_PlaylistBase):
    """Playlists page UI and playlist-management workflows."""

    def _initialize_playlists_page(self) -> None:
        self.selected_playlist_id: str | None = None
        self.playlist_import_session: _PlaylistImportSession | None = None
        self.playlist_import_loading = False
        self.playlist_import_lock = RLock()
        self.playlist_import_retry: Timer | None = None

    def _playlists_view(self) -> ft.Control:
        if self.selected_playlist_id and self.manager.has_playlist(
            self.selected_playlist_id
        ):
            return self._playlist_detail(
                self.manager.get_playlist(self.selected_playlist_id)
            )
        self.selected_playlist_id = None
        playlists = self.manager.list_playlists()
        if playlists:
            body: ft.Control = ft.Row(
                [self._playlist_card(playlist) for playlist in playlists],
                wrap=True,
                spacing=14,
                run_spacing=14,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            )
        else:
            body = ft.Container(
                _empty_state(
                    ft.Icons.QUEUE_MUSIC_ROUNDED,
                    "Create a playlist or import one from YouTube to get started.",
                ),
                alignment=ft.Alignment.CENTER,
                expand=True,
            )
        playlists_header = _page_header(
            "Playlists",
            "Persistent collections, separate from your temporary queue.",
        )
        playlists_header.col = {"xs": 12, "md": 7}
        playlist_actions = ft.Container(
            ft.Row(
                [
                    ft.OutlinedButton(
                        "Import playlist",
                        icon=ft.Icons.PLAYLIST_ADD_ROUNDED,
                        on_click=lambda _: self._import_playlist_dialog(),
                    ),
                    ft.Button(
                        "New playlist",
                        icon=ft.Icons.ADD_ROUNDED,
                        on_click=lambda _: self._create_playlist_dialog(),
                    ),
                ],
                spacing=8,
                scroll=ft.ScrollMode.AUTO,
                alignment=ft.MainAxisAlignment.END,
            ),
            alignment=ft.Alignment.CENTER_RIGHT,
            col={"xs": 12, "md": 5},
        )
        return ft.Column(
            [
                self._responsive_grid(
                    [playlists_header, playlist_actions],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                body,
            ],
            spacing=20,
            expand=True,
        )

    def _open_playlist(self, playlist_id: str) -> None:
        self.selected_playlist_id = playlist_id
        self.navigate(3)

    def _playlist_detail(self, playlist: Playlist) -> ft.Control:
        tracks = self.manager.playlist_tracks(playlist.id)
        track_ids = tuple(str(track.id) for track in tracks)
        other_playlists = tuple(
            item for item in self.manager.list_playlists() if item.id != playlist.id
        )
        compact = self.compact_layout
        bulk_add_button: ft.Control
        if compact:
            bulk_add_button = ft.IconButton(
                ft.Icons.PLAYLIST_ADD_ROUNDED,
                tooltip="Add to playlist",
                on_click=lambda _: self._bulk_add_tracks_dialog(playlist),
            )
            play_button: ft.Control = ft.IconButton(
                ft.Icons.PLAY_ARROW_ROUNDED,
                tooltip="Play playlist",
                on_click=lambda _: self._play_playlist(playlist),
            )
        else:
            bulk_add_button = ft.OutlinedButton(
                "Add to playlist",
                icon=ft.Icons.PLAYLIST_ADD_ROUNDED,
                on_click=lambda _: self._bulk_add_tracks_dialog(playlist),
            )
            play_button = ft.Button(
                "Play",
                icon=ft.Icons.PLAY_ARROW_ROUNDED,
                on_click=lambda _: self._play_playlist(playlist),
            )
        rows: ft.Control
        if tracks:
            rows = ft.ReorderableListView(
                controls=[
                    self._playlist_track_row(
                        playlist,
                        track,
                        index,
                        other_playlists,
                        track_ids,
                    )
                    for index, track in enumerate(tracks)
                ],
                spacing=6,
                show_default_drag_handles=True,
                mouse_cursor=ft.MouseCursor.GRAB,
                on_reorder=lambda event, pid=str(playlist.id): self._reorder_playlist(
                    pid, event.old_index, event.new_index
                ),
                expand=True,
            )
        else:
            rows = _empty_state(
                ft.Icons.PLAYLIST_ADD_ROUNDED,
                "This playlist is empty. Add tracks from Search or Library.",
                expand=True,
            )
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.IconButton(
                            ft.Icons.ARROW_BACK_ROUNDED,
                            tooltip="All playlists",
                            on_click=lambda _: self._close_playlist(),
                        ),
                        ft.Column(
                            [
                                ft.Text(
                                    playlist.name, size=30, weight=ft.FontWeight.BOLD
                                ),
                                ft.Text(
                                    f"{len(tracks)} track{'s' if len(tracks) != 1 else ''}",
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        bulk_add_button,
                        play_button,
                        ft.IconButton(
                            ft.Icons.SHUFFLE_ROUNDED,
                            tooltip="Shuffle playlist",
                            on_click=lambda _: self._play_playlist(
                                playlist, shuffle=True
                            ),
                        ),
                        ft.IconButton(
                            ft.Icons.ADD_TO_QUEUE_ROUNDED,
                            tooltip="Add playlist to queue",
                            on_click=lambda _: self._queue_collection(tracks),
                        ),
                        ft.PopupMenuButton(
                            icon=ft.Icons.MORE_HORIZ_ROUNDED,
                            items=[
                                ft.PopupMenuItem(
                                    content="Rename playlist",
                                    icon=ft.Icons.EDIT_ROUNDED,
                                    on_click=lambda _: self._rename_playlist_dialog(
                                        playlist
                                    ),
                                ),
                                ft.PopupMenuItem(
                                    content="Delete playlist",
                                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                                    on_click=lambda _: self._delete_playlist_dialog(
                                        playlist
                                    ),
                                ),
                            ],
                        ),
                    ],
                    scroll=ft.ScrollMode.AUTO,
                ),
                ft.Divider(height=1),
                rows,
            ],
            spacing=16,
            expand=True,
        )

    def _playlist_track_row(
        self,
        playlist: Playlist,
        track: Track,
        index: int,
        other_playlists: tuple[Playlist, ...],
        playlist_track_ids: tuple[str, ...],
    ) -> ft.Control:
        details = self.library.details(track.id)
        menu: list[ft.PopupMenuItem] = [
            ft.PopupMenuItem(
                content="Play next",
                icon=ft.Icons.QUEUE_PLAY_NEXT_ROUNDED,
                on_click=lambda _, tid=str(track.id): self.playback.add_next(tid),
            ),
            ft.PopupMenuItem(
                content="Add to queue",
                icon=ft.Icons.ADD_TO_QUEUE_ROUNDED,
                on_click=lambda _, tid=str(track.id): self.playback.add_last(tid),
            ),
            ft.PopupMenuItem(
                content="Favorite" if not details.favorite else "Remove favorite",
                icon=ft.Icons.FAVORITE_BORDER_ROUNDED,
                on_click=lambda _, tid=str(track.id): self._toggle_favorite(tid),
            ),
            ft.PopupMenuItem(
                content="View details",
                icon=ft.Icons.INFO_OUTLINE_ROUNDED,
                on_click=lambda _, item=track: self._track_details_dialog(item),
            ),
        ]
        for target in other_playlists:
            menu.extend(
                [
                    ft.PopupMenuItem(
                        content=f"Copy to {target.name}",
                        icon=ft.Icons.CONTENT_COPY_ROUNDED,
                        on_click=lambda _, source=str(playlist.id), destination=str(target.id), tid=str(track.id): (
                            self._transfer_playlist_track(
                                source, destination, tid, move=False
                            )
                        ),
                    ),
                    ft.PopupMenuItem(
                        content=f"Move to {target.name}",
                        icon=ft.Icons.DRIVE_FILE_MOVE_OUTLINE,
                        on_click=lambda _, source=str(playlist.id), destination=str(target.id), tid=str(track.id): (
                            self._transfer_playlist_track(
                                source, destination, tid, move=True
                            )
                        ),
                    ),
                ]
            )
        menu.append(
            ft.PopupMenuItem(
                content="Remove from playlist",
                icon=ft.Icons.REMOVE_CIRCLE_OUTLINE_ROUNDED,
                on_click=lambda _, pid=str(playlist.id), tid=str(track.id): (
                    self._remove_from_playlist(pid, tid)
                ),
            )
        )
        compact = self.compact_layout
        leading: list[ft.Control] = []
        if not compact:
            leading.extend(
                [
                    ft.Icon(
                        ft.Icons.DRAG_INDICATOR_ROUNDED,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Text(
                        str(index + 1),
                        width=28,
                        text_align=ft.TextAlign.CENTER,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ]
            )
        trailing: list[ft.Control] = []
        if not compact:
            trailing.append(
                ft.Text(
                    _format_duration(details.duration),
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            )
        trailing.extend(
            [
                ft.IconButton(
                    ft.Icons.PLAY_ARROW_ROUNDED,
                    tooltip="Play from here",
                    on_click=lambda _, ids=playlist_track_ids, start=index: (
                        self.playback.play_tracks(ids, start_index=start)
                    ),
                ),
                ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ_ROUNDED, items=menu),
            ]
        )
        return card(
            ft.Row(
                [
                    *leading,
                    _artwork(details.thumbnail, 48),
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
                            ),
                        ],
                        spacing=3,
                        expand=True,
                    ),
                    *trailing,
                ]
            ),
            padding=10,
            key=f"{playlist.id}:{index}:{track.id}",
        )

    def _close_playlist(self) -> None:
        self.selected_playlist_id = None
        self.navigate(3)

    def _play_playlist(self, playlist: Playlist, *, shuffle: bool = False) -> None:
        ids = [str(track_id) for track_id in playlist.track_ids]
        if shuffle:
            random.shuffle(ids)
        self.playback.play_tracks(ids)

    def _create_playlist_dialog(self) -> None:
        field = ft.TextField(label="Playlist name", autofocus=True)

        def create(_: Any) -> None:
            try:
                playlist_id = self.manager.create_playlist(field.value)
            except ValueError as error:
                self._show_error(str(error))
                return
            self.page.pop_dialog()
            self.selected_playlist_id = str(playlist_id)
            self.navigate(3)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("New playlist"),
                content=field,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    ft.Button("Create", on_click=create),
                ],
            )
        )

    def _import_playlist_dialog(self) -> None:
        if self.playlist_import_loading or self.playlist_import_session is not None:
            self._show_error("A playlist import is already in progress.")
            return

        field = ft.TextField(
            label="YouTube playlist URL",
            hint_text="https://www.youtube.com/playlist?list=…",
            autofocus=True,
            width=None if self.compact_layout else 560,
        )

        def begin(_: Any) -> None:
            url = (field.value or "").strip()
            if not is_youtube_playlist_url(url):
                field.error = "Paste a complete YouTube playlist URL."
                self.page.update(field)
                return
            self.playlist_import_loading = True
            import_button.disabled = True
            if self._submit_background(self._load_playlist_import, url):
                self.page.pop_dialog()
                self._show_message("Loading the YouTube playlist…")
                return
            self.playlist_import_loading = False
            import_button.disabled = False
            self.page.update(import_button)

        import_button = ft.Button(
            "Import playlist",
            icon=ft.Icons.DOWNLOAD_ROUNDED,
            on_click=begin,
        )
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Import playlist"),
                content=ft.Column(
                    [
                        ft.Text(
                            "Paste a YouTube playlist link. Melody will reuse tracks "
                            "already in your library and download only the missing ones."
                        ),
                        field,
                    ],
                    spacing=14,
                    tight=True,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    import_button,
                ],
            )
        )

    def _load_playlist_import(self, url: str) -> None:
        try:
            remote = self.providers.load_playlist(url)
            self._begin_playlist_import(remote)
        except (ProviderError, OSError, RuntimeError, ValueError) as error:
            self._show_error(f"Couldn’t import that playlist: {error}")
        finally:
            self.playlist_import_loading = False

    def _begin_playlist_import(self, remote: RemotePlaylist) -> None:
        with self.playlist_import_lock:
            if self.playlist_import_session is not None:
                self._show_error("A playlist import is already in progress.")
                return

            unique: list[tuple[str, SearchResult]] = []
            seen: set[str] = set()
            for result in remote.tracks:
                key = source_key(result.url)
                if not result.url or not key or key in seen:
                    continue
                seen.add(key)
                unique.append((key, result))

            name = remote.title.strip() or "Imported YouTube playlist"
            playlist_id = str(self.manager.create_playlist(name))
            resolved: dict[str, str] = {}
            pending: deque[tuple[str, SearchResult]] = deque()
            reused = 0
            for key, result in unique:
                existing = self.manager.find_track_by_source(result.url)
                if existing is None:
                    pending.append((key, result))
                    continue
                resolved[key] = str(existing.id)
                reused += 1

            session = _PlaylistImportSession(
                playlist_id=playlist_id,
                name=name,
                pending=pending,
                order=tuple(key for key, _ in unique),
                resolved=resolved,
                reused=reused,
            )
            self.playlist_import_session = session
            self.selected_playlist_id = playlist_id
            self._sync_imported_playlist(session)
            if self.selected_navigation == 3:
                self.navigate(3)
            if pending:
                self._show_message(
                    f"Importing {session.total} tracks into “{name}”: "
                    f"reusing {reused}, downloading {len(pending)} missing."
                )
            self._continue_playlist_import()

    def _continue_playlist_import(
        self,
        completed_url: str = "",
        completed_track_ids: tuple[str, ...] = (),
    ) -> None:
        with self.playlist_import_lock:
            session = self.playlist_import_session
            if session is None:
                return
            if not self.manager.has_playlist(session.playlist_id):
                self._abandon_playlist_import(session.playlist_id)
                self._show_error(
                    f"Import of “{session.name}” stopped because its playlist was deleted."
                )
                return

            completed_track_id = next(
                (
                    track_id
                    for track_id in completed_track_ids
                    if self.manager.has_track(track_id)
                ),
                None,
            )
            if (
                completed_url
                and completed_track_id
                and session.pending
                and session.pending[0][0] == source_key(completed_url)
            ):
                key, _ = session.pending.popleft()
                session.resolved[key] = completed_track_id
                session.downloaded += 1
                session.waiting_key = None
                session.waiting_owned = False
                session.waiting_task_id = None
                self._sync_imported_playlist(session)

            while session.pending:
                key, result = session.pending[0]
                existing = self.manager.find_track_by_source(result.url)
                if existing is not None:
                    session.resolved[key] = str(existing.id)
                    session.pending.popleft()
                    if session.waiting_key == key:
                        session.downloaded += 1
                    else:
                        session.reused += 1
                    session.waiting_key = None
                    session.waiting_owned = False
                    session.waiting_task_id = None
                    self._sync_imported_playlist(session)
                    continue

                if session.waiting_key == key:
                    if self.downloads.is_source_active(result.url):
                        return
                    if session.waiting_owned:
                        session.failed += 1
                        session.pending.popleft()
                    session.waiting_key = None
                    session.waiting_owned = False
                    session.waiting_task_id = None
                    continue

                if self.downloads.is_source_active(result.url):
                    session.waiting_key = key
                    session.waiting_owned = False
                    return

                session.waiting_key = key
                session.waiting_owned = True
                try:
                    task = self.downloads.start(result)
                    session.waiting_task_id = str(task.id)
                except WorkerQueueFull:
                    session.waiting_key = None
                    session.waiting_owned = False
                    session.waiting_task_id = None
                    self._schedule_playlist_import_retry()
                    return
                except DuplicateDownloadError:
                    if self.downloads.is_source_active(result.url):
                        session.waiting_owned = False
                        return
                    session.failed += 1
                    session.pending.popleft()
                    session.waiting_key = None
                    session.waiting_owned = False
                    session.waiting_task_id = None
                    continue
                except (OSError, RuntimeError, ValueError):
                    session.failed += 1
                    session.pending.popleft()
                    session.waiting_key = None
                    session.waiting_owned = False
                    session.waiting_task_id = None
                    continue
                return

            self._finish_playlist_import(session)

    def _schedule_playlist_import_retry(self) -> None:
        if self.playlist_import_retry is not None:
            return

        def retry() -> None:
            with self.playlist_import_lock:
                self.playlist_import_retry = None
            self._continue_playlist_import()

        timer = Timer(0.1, retry)
        timer.daemon = True
        self.playlist_import_retry = timer
        timer.start()

    def _cancel_playlist_import_retry(self) -> None:
        timer = self.playlist_import_retry
        self.playlist_import_retry = None
        if timer is not None:
            timer.cancel()

    def _abandon_playlist_import(self, playlist_id: str | None = None) -> bool:
        lock = getattr(self, "playlist_import_lock", None)
        if lock is None:
            return False
        with lock:
            session = self.playlist_import_session
            if session is None or (
                playlist_id is not None and session.playlist_id != playlist_id
            ):
                return False
            owned_task_id = session.waiting_task_id if session.waiting_owned else None
            self.playlist_import_session = None
            self._cancel_playlist_import_retry()
            if owned_task_id is not None:
                try:
                    self.downloads.cancel(owned_task_id)
                except (KeyError, RuntimeError, ValueError):
                    pass
            return True

    def _sync_imported_playlist(self, session: _PlaylistImportSession) -> None:
        imported_order = [
            session.resolved[key] for key in session.order if key in session.resolved
        ]
        self.manager.merge_tracks_into_playlist(session.playlist_id, imported_order)

    def _finish_playlist_import(self, session: _PlaylistImportSession) -> None:
        if self.playlist_import_session is not session:
            return
        self.playlist_import_session = None
        self._cancel_playlist_import_retry()
        imported = session.total - session.failed
        if self.selected_navigation == 3:
            self.navigate(3)
        summary = (
            f"Imported {imported} of {session.total} tracks into “{session.name}”: "
            f"reused {session.reused}, downloaded {session.downloaded}."
        )
        if session.failed:
            self._show_error(f"{summary} {session.failed} could not be downloaded.")
        else:
            self._show_message(summary)

    def _rename_playlist_dialog(self, playlist: Playlist) -> None:
        field = ft.TextField(label="Playlist name", value=playlist.name, autofocus=True)

        def save(_: Any) -> None:
            try:
                self.manager.rename_playlist(playlist.id, field.value)
            except ValueError as error:
                self._show_error(str(error))
                return
            self.page.pop_dialog()
            self.navigate(3)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Rename playlist"),
                content=field,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    ft.Button("Save", on_click=save),
                ],
            )
        )

    def _delete_playlist_dialog(self, playlist: Playlist) -> None:
        def confirm(_: Any) -> None:
            self.manager.delete_playlist(playlist.id)
            self._abandon_playlist_import(str(playlist.id))
            self.page.pop_dialog()
            self.selected_playlist_id = None
            self.navigate(3)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Delete playlist?"),
                content=ft.Text(
                    f"Delete “{playlist.name}”? Your downloaded tracks will stay in the library."
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    ft.Button("Delete", on_click=confirm),
                ],
            )
        )

    def _add_to_playlist(self, playlist_id: str, track_id: str) -> None:
        self.manager.add_to_playlist(playlist_id, track_id)
        self._show_message("Added to playlist.")

    def _bulk_add_tracks_dialog(self, playlist: Playlist) -> None:
        member_ids = {str(track_id) for track_id in playlist.track_ids}
        candidates = [
            track
            for track in self.manager.list_tracks()
            if str(track.id) not in member_ids
        ]
        checkboxes = [
            ft.Checkbox(
                label=(
                    f"{_track_title(track)} — "
                    f"{_track_credit(self.library.details(track.id))}"
                ),
                value=False,
            )
            for track in candidates
        ]
        select_all = ft.Checkbox(label="Select all", value=False)
        selection = ft.Text(
            "No tracks selected",
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )

        def update_selection() -> None:
            selected = sum(bool(checkbox.value) for checkbox in checkboxes)
            select_all.value = bool(checkboxes) and selected == len(checkboxes)
            add_button.disabled = selected == 0
            selection.value = (
                f"{selected} track{'s' if selected != 1 else ''} selected"
                if selected
                else "No tracks selected"
            )
            self.page.update(select_all, selection, add_button, *checkboxes)

        def toggle_all(_: Any) -> None:
            checked = bool(select_all.value)
            for checkbox in checkboxes:
                checkbox.value = checked
            update_selection()

        def add_selected(_: Any) -> None:
            track_ids = [
                str(track.id)
                for track, checkbox in zip(candidates, checkboxes, strict=True)
                if checkbox.value
            ]
            if not track_ids:
                return
            try:
                added = self.manager.add_tracks_to_playlist(playlist.id, track_ids)
            except (KeyError, ValueError) as error:
                self._show_error(str(error))
                return
            self.page.pop_dialog()
            self.navigate(3)
            self._show_message(
                f"Added {added} track{'s' if added != 1 else ''} to “{playlist.name}”."
            )

        select_all.on_change = toggle_all
        for checkbox in checkboxes:
            checkbox.on_change = lambda _: update_selection()
        add_button = ft.Button(
            "Add selected",
            icon=ft.Icons.PLAYLIST_ADD_ROUNDED,
            disabled=True,
            on_click=add_selected,
        )
        track_list: ft.Control
        if checkboxes:
            from typing import cast

            track_list = ft.Column(
                cast(list[ft.Control], checkboxes),
                spacing=2,
                height=340,
                scroll=ft.ScrollMode.AUTO,
            )
        else:
            select_all.disabled = True
            track_list = ft.Container(
                ft.Text(
                    "Every library track is already in this playlist. Add music to "
                    "your library, then return here.",
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    text_align=ft.TextAlign.CENTER,
                ),
                padding=ft.Padding.symmetric(vertical=32, horizontal=12),
                alignment=ft.Alignment.CENTER,
            )

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(f"Add tracks to “{playlist.name}”"),
                content=ft.Column(
                    [
                        ft.Text(
                            "Choose multiple tracks from your library and add them in one action."
                        ),
                        select_all,
                        ft.Divider(height=1),
                        track_list,
                        selection,
                    ],
                    spacing=10,
                    tight=True,
                    width=None if self.compact_layout else 560,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    add_button,
                ],
            )
        )

    def _remove_from_playlist(self, playlist_id: str, track_id: str) -> None:
        self.manager.remove_from_playlist(playlist_id, track_id)
        self.navigate(3)

    def _reorder_playlist(
        self, playlist_id: str, old_index: int | None, new_index: int | None
    ) -> None:
        if old_index is None or new_index is None:
            return
        playlist = self.manager.get_playlist(playlist_id)
        ordered = [str(track_id) for track_id in playlist.track_ids]
        item = ordered.pop(old_index)
        if new_index > old_index:
            new_index -= 1
        ordered.insert(max(0, min(new_index, len(ordered))), item)
        self.manager.reorder_playlist(playlist_id, ordered)
        self.navigate(3)

    def _transfer_playlist_track(
        self, source: str, target: str, track_id: str, *, move: bool
    ) -> None:
        if move:
            self.manager.move_track_between_playlists(source, target, track_id)
        else:
            self.manager.copy_playlist_track(source, target, track_id)
        self.navigate(3)
