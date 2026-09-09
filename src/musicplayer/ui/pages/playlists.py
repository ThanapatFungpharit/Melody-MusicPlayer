from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING, Any

import flet as ft

from musicplayer.application.contracts import (
    Playlist,
    Track,
    WorkerQueueFull,
    source_key,
)
from musicplayer.application.models import SearchResult
from musicplayer.application.providers import (
    RemotePlaylist,
    is_youtube_playlist_url,
)
from musicplayer.ui.components.common import (
    _track_credit,
    _track_title,
)
from musicplayer.ui.tasks import page_action

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
        self.playlist_import_retry = None
        self._playlist_import_generation = 0
        self._import_wakeup = asyncio.Event()

    def _playlists_view(self) -> ft.Control:
        return self.views._playlists_view(self)

    def _open_playlist(self, playlist_id: str) -> None:
        self.selected_playlist_id = playlist_id
        self.navigate(3)

    def _playlist_detail(self, playlist: Playlist) -> ft.Control:
        return self.views._playlist_detail(self, playlist)

    def _playlist_track_row(
        self,
        playlist: Playlist,
        track: Track,
        index: int,
        other_playlists: tuple[Playlist, ...],
        playlist_track_ids: tuple[str, ...],
    ) -> ft.Control:
        return self.views._playlist_track_row(
            self, playlist, track, index, other_playlists, playlist_track_ids
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

        async def create(_: Any) -> None:
            try:
                playlist_id = await self.tasks.io(
                    self.manager.create_playlist, field.value
                )
            except ValueError as error:
                self._show_error(str(error))
                return
            self.page.pop_dialog()
            self.selected_playlist_id = str(playlist_id)
            self.navigate(3)

        self.page.show_dialog(
            ft.AlertDialog(
                scrollable=True,
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
            width=min(560, max(160, float(self.page.width or 360) - 80)),
        )

        def begin(_: Any) -> None:
            url = (field.value or "").strip()
            if not is_youtube_playlist_url(url):
                field.error = "Paste a complete YouTube playlist URL."
                self.page.update(field)
                return
            self.playlist_import_loading = True
            import_button.disabled = True
            self._load_playlist_import(url)
            if True:
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
                scrollable=True,
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

    @page_action
    async def _load_playlist_import(self, url: str) -> None:
        generation = self._playlist_import_generation
        try:
            remote = await self.tasks.io(self.providers.load_playlist, url)
            if generation == self._playlist_import_generation:
                await self._import_playlist(remote, generation)
        finally:
            self.playlist_import_loading = False

    @page_action
    async def _begin_playlist_import(self, remote: RemotePlaylist) -> None:
        await self._import_playlist(remote, self._playlist_import_generation)

    async def _import_playlist(self, remote: RemotePlaylist, generation: int) -> None:
        if self.playlist_import_session is not None:
            self._show_error("A playlist import is already in progress.")
            return
        # Lightweight page fixtures and older integrations provide only the
        # original ``start`` API. Preserve that handoff while the production
        # coordinator uses cancellable request/get semantics below.
        if not hasattr(self.downloads, "request"):
            await self._legacy_import_playlist(remote)
            return
        self.playlist_import_loading = True
        name = remote.title.strip() or "Imported YouTube playlist"
        unique = {source_key(item.url): item for item in remote.tracks if item.url}
        playlist_id = str(await self.tasks.io(self.manager.create_playlist, name))
        if generation != self._playlist_import_generation:
            return
        session = _PlaylistImportSession(
            playlist_id, name, deque(unique.items()), tuple(unique)
        )
        self.playlist_import_session = session
        self.selected_playlist_id = playlist_id
        self._show_message(f"Importing {session.total} tracks into “{name}”…")
        try:
            while session.pending and self.playlist_import_session is session:
                key, result = session.pending[0]
                existing = await self.tasks.io(
                    self.downloads.available_track, result.url
                )
                if self.playlist_import_session is not session:
                    return
                if existing is not None:
                    session.resolved[key] = str(existing.id)
                    session.reused += 1
                else:
                    try:
                        was_active = self.downloads.is_source_active(result.url)
                        task = await self.tasks.io(self.downloads.request, result)
                    except WorkerQueueFull:
                        await asyncio.sleep(0.2)
                        continue
                    except (OSError, RuntimeError, ValueError):
                        session.failed += 1
                        session.pending.popleft()
                        continue
                    if self.playlist_import_session is not session:
                        if task is not None and not was_active:
                            await self.tasks.io(self.downloads.cancel, str(task.id))
                        return
                    if task is None:
                        continue
                    session.waiting_key = key
                    session.waiting_owned = not was_active
                    session.waiting_task_id = str(task.id)
                    while self.playlist_import_session is session:
                        self._import_wakeup.clear()
                        record = self.downloads.get(str(task.id))
                        if record and record.status in {
                            "completed",
                            "failed",
                            "cancelled",
                        }:
                            if record.status == "completed" and record.track_ids:
                                session.resolved[key] = record.track_ids[0]
                                session.downloaded += 1
                            else:
                                session.failed += 1
                            break
                        await self._import_wakeup.wait()
                    if self.playlist_import_session is not session:
                        return
                session.waiting_key = session.waiting_task_id = None
                session.waiting_owned = False
                session.pending.popleft()
                order = [
                    session.resolved[item]
                    for item in session.order
                    if item in session.resolved
                ]
                await self.tasks.io(
                    self.manager.merge_tracks_into_playlist, playlist_id, order
                )
                if (
                    self.playlist_import_session is session
                    and self.selected_navigation == 3
                ):
                    self.navigate(3)
            self._finish_playlist_import(session)
        finally:
            self.playlist_import_loading = False
            if self.playlist_import_session is session:
                self.playlist_import_session = None

    async def _legacy_import_playlist(self, remote: RemotePlaylist) -> None:
        unique: list[tuple[str, SearchResult]] = []
        seen: set[str] = set()
        for result in remote.tracks:
            key = source_key(result.url)
            if result.url and key and key not in seen:
                seen.add(key)
                unique.append((key, result))
        name = remote.title.strip() or "Imported YouTube playlist"
        playlist_id = str(await self.tasks.io(self.manager.create_playlist, name))
        session = _PlaylistImportSession(
            playlist_id, name, deque(unique), tuple(key for key, _ in unique)
        )
        self.playlist_import_session = session
        self.selected_playlist_id = playlist_id
        self._legacy_advance(session)

    def _legacy_advance(self, session: _PlaylistImportSession) -> None:
        while session.pending:
            key, result = session.pending[0]
            existing = self.manager.find_track_by_source(result.url)
            if existing is not None:
                session.resolved[key] = str(existing.id)
                session.reused += 1
                session.pending.popleft()
                continue
            if session.waiting_key == key:
                return
            task = self.downloads.start(result)
            session.waiting_key = key
            session.waiting_owned = True
            session.waiting_task_id = str(task.id)
            break
        self.manager.merge_tracks_into_playlist(
            session.playlist_id,
            [session.resolved[key] for key in session.order if key in session.resolved],
        )
        if not session.pending:
            self._finish_playlist_import(session)

    def _continue_playlist_import(
        self, completed_url: str = "", completed_track_ids: tuple[str, ...] = ()
    ) -> None:
        session = self.playlist_import_session
        if session is not None and not hasattr(self.downloads, "request"):
            key, result = session.pending[0] if session.pending else ("", None)
            if completed_url:
                key = source_key(completed_url)
                result = next(
                    (item for item_key, item in session.pending if item_key == key),
                    result,
                )
            completed_id = next(
                (
                    track_id
                    for track_id in completed_track_ids
                    if self.manager.has_track(track_id)
                ),
                None,
            )
            if completed_id and result is not None:
                session.resolved[key] = completed_id
                session.downloaded += 1
                session.pending.popleft()
                session.waiting_key = session.waiting_task_id = None
                session.waiting_owned = False
                self._legacy_advance(session)
                return
            if result is not None:
                existing = self.manager.find_track_by_source(result.url)
                if existing is not None:
                    session.resolved[key] = str(existing.id)
                    session.downloaded += 1
                    session.pending.popleft()
                    session.waiting_key = session.waiting_task_id = None
                    session.waiting_owned = False
                    self._legacy_advance(session)
            return
        self._import_wakeup.set()

    def _cancel_playlist_import_retry(self) -> None:
        self._playlist_import_generation += 1
        self._import_wakeup.set()

    def _abandon_playlist_import(self, playlist_id: str | None = None) -> bool:
        session = getattr(self, "playlist_import_session", None)
        if playlist_id is not None and (
            session is None or session.playlist_id != playlist_id
        ):
            return False
        self._playlist_import_generation = (
            getattr(self, "_playlist_import_generation", 0) + 1
        )
        self.playlist_import_session = None
        wakeup = getattr(self, "_import_wakeup", None)
        if wakeup is not None:
            wakeup.set()
        if session is not None and session.waiting_owned and session.waiting_task_id:
            self.tasks.submit(
                lambda: self.downloads.cancel(session.waiting_task_id), lambda _: None
            )
        return session is not None

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

        async def save(_: Any) -> None:
            try:
                await self.tasks.io(
                    self.manager.rename_playlist, playlist.id, field.value
                )
            except ValueError as error:
                self._show_error(str(error))
                return
            self.page.pop_dialog()
            self.navigate(3)

        self.page.show_dialog(
            ft.AlertDialog(
                scrollable=True,
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
            async def commit() -> None:
                await self.tasks.io(self.manager.delete_playlist, playlist.id)
                self._abandon_playlist_import(str(playlist.id))
                self.page.pop_dialog()
                self.selected_playlist_id = None
                self.navigate(3)

            self.tasks.start(lambda: commit())

        self.page.show_dialog(
            ft.AlertDialog(
                scrollable=True,
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

    @page_action
    async def _add_to_playlist(self, playlist_id: str, track_id: str) -> None:
        await self.tasks.io(self.manager.add_to_playlist, playlist_id, track_id)
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

            async def commit() -> None:
                try:
                    added = await self.tasks.io(
                        self.manager.add_tracks_to_playlist, playlist.id, track_ids
                    )
                except (KeyError, ValueError) as error:
                    self._show_error(str(error))
                    return
                self.page.pop_dialog()
                self.navigate(3)
                self._show_message(
                    f"Added {added} track{'s' if added != 1 else ''} to “{playlist.name}”."
                )

            self.tasks.start(lambda: commit())

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
                height=min(340, max(96, float(self.page.height or 640) * 0.35)),
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
                scrollable=True,
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
                    width=min(560, max(160, float(self.page.width or 360) - 80)),
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    add_button,
                ],
            )
        )

    @page_action
    async def _remove_from_playlist(self, playlist_id: str, track_id: str) -> None:
        await self.tasks.io(self.manager.remove_from_playlist, playlist_id, track_id)
        self.navigate(3)

    @page_action
    async def _reorder_playlist(
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
        await self.tasks.io(self.manager.reorder_playlist, playlist_id, ordered)
        self.navigate(3)

    @page_action
    async def _transfer_playlist_track(
        self, source: str, target: str, track_id: str, *, move: bool
    ) -> None:
        if move:
            await self.tasks.io(
                self.manager.move_track_between_playlists, source, target, track_id
            )
        else:
            await self.tasks.io(
                self.manager.copy_playlist_track, source, target, track_id
            )
        self.navigate(3)

    def _playlist_track_menu(
        self,
        playlist: Playlist,
        track: Track,
        index: int,
        other_playlists: tuple[Playlist, ...],
        playlist_track_ids: tuple[str, ...],
    ) -> list[ft.PopupMenuItem]:
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
        return menu
