from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

import flet as ft

from musicplayer.application.contracts import Playlist
from musicplayer.application.downloads import (
    extract_download_urls,
)
from musicplayer.application.models import SearchResult
from musicplayer.application.providers import YOUTUBE_PROVIDER_ID
from musicplayer.ui.components.common import (
    _empty_state,
    _format_duration,
)
from musicplayer.ui.tasks import page_action, run_io

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from musicplayer.ui._app_protocol import AppProtocol

    _Base = AppProtocol
else:
    _Base = object


class SearchPage(_Base):
    """Search page UI and provider-search workflow."""

    SEARCH_PAGE_SIZE = 24

    def _initialize_search_page(self) -> None:
        self._search_local_tracks = {}
        self.search_draft = ""
        self.search_results: list[SearchResult] = []
        self.search_busy = False
        self.search_page = 1
        self.search_has_next = False
        self.search_query_text = ""
        self.search_playlist_title = ""
        self.search_view_mode = "list"

    def _search_view(self) -> ft.Control:
        return self.views._search_view(self)

    def _start_search(self) -> None:
        query = self.search_query.value.strip()
        if not query:
            self._show_error("Enter a search term or YouTube URL.")
            return
        urls = extract_download_urls(query)
        if len(urls) > 1:
            self.search_query_text = query
            self._start_url_batch(urls)
            return
        self.search_query_text = query
        self._begin_search(query, page=1, remember=True)

    def _batch_download_dialog(self) -> None:
        urls_field = ft.TextField(
            label="YouTube URLs",
            hint_text="Paste one YouTube video or playlist URL per line",
            multiline=True,
            min_lines=7,
            max_lines=12,
            autofocus=True,
        )
        validation = ft.Text("", color=ft.Colors.ERROR, size=12)

        async def start_batch(_: object) -> None:
            urls = extract_download_urls(urls_field.value or "")
            if len(urls) < 2:
                validation.value = "Paste at least two complete YouTube URLs."
                self.page.update(validation)
                return
            try:
                batch = await self.tasks.io(self.downloads.start_urls, urls)
            except (ValueError, OSError, RuntimeError) as error:
                validation.value = str(error)
                self.page.update(validation)
                return
            self.page.pop_dialog()
            self._show_message(self._batch_download_message(batch))
            self._open_downloads_panel()

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Batch download"),
                content=ft.Column(
                    [
                        ft.Text(
                            "Add multiple YouTube URLs. Melody will queue them together and show progress for each song.",
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        urls_field,
                        validation,
                    ],
                    tight=True,
                    spacing=12,
                ),
                scrollable=True,
                inset_padding=ft.Padding.symmetric(horizontal=16, vertical=24),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                    ft.Button(
                        "Start batch",
                        icon=ft.Icons.DOWNLOAD_ROUNDED,
                        on_click=start_batch,
                    ),
                ],
            )
        )

    @page_action
    async def _start_url_batch(self, urls: tuple[str, ...]) -> None:
        try:
            batch = await self.tasks.io(self.downloads.start_urls, urls)
        except (ValueError, OSError, RuntimeError) as error:
            self._show_error(str(error))
            return
        self._show_message(self._batch_download_message(batch))
        self._open_downloads_panel()

    @staticmethod
    def _batch_download_message(batch) -> str:
        scheduled = len(batch.tasks)
        if scheduled == batch.size:
            return f"Started a batch of {batch.size} songs. Each song will continue independently."
        if scheduled:
            return (
                f"Queued {scheduled} of {batch.size} songs. Existing files and active "
                "downloads were reused."
            )
        return "All requested songs are already in the library or downloading."

    def _load_search_page(self, page: int) -> None:
        if self.search_busy or page < 1:
            return
        if page > self.search_page and not self.search_has_next:
            return
        if not self.search_query_text:
            return
        self._begin_search(
            self.search_query_text,
            page=page,
            remember=False,
        )

    def _begin_search(
        self,
        query: str,
        *,
        page: int,
        remember: bool,
    ) -> None:
        self.search_busy = True
        self.search_button.disabled = True
        self.search_previous_button.disabled = True
        self.search_first_button.disabled = True
        self.search_next_button.disabled = True
        self.search_status.value = f"Searching page {page}…"
        self.search_list.controls = [
            ft.Container(ft.ProgressRing(), alignment=ft.Alignment.CENTER, padding=40)
        ]
        self.page.update(
            self.search_button,
            self.search_status,
            self.search_list,
            self.search_pagination,
        )
        if not run_io(
            self,
            partial(self._fetch_search, query, page, remember),
            partial(self._finish_search, page),
            key="search",
            replace=True,
            failed=self._search_failed,
        ):
            self.search_busy = False
            self.search_button.disabled = False
            self.search_status.value = "Background workers are busy. Retry shortly."
            self._update_search_navigation(update_status=False)
            self.page.update(
                self.search_button,
                self.search_status,
                self.search_list,
                self.search_pagination,
            )

    def _fetch_search(self, query: str, page: int, remember: bool):
        offset = (page - 1) * self.SEARCH_PAGE_SIZE
        fetched = self.providers.search(
            YOUTUBE_PROVIDER_ID, query, limit=self.SEARCH_PAGE_SIZE + 1, offset=offset
        )
        local = {
            item.url: self.downloads.available_track(item.url)
            for item in fetched
            if not item.is_playlist
        }
        if remember:
            self.store.add_recent("search_history", query, limit=20)
        return fetched, local

    def _finish_search(self, page: int, value) -> None:
        fetched, self._search_local_tracks = value
        self.search_results = fetched[: self.SEARCH_PAGE_SIZE]
        self.search_page = page
        self.search_has_next = len(fetched) > self.SEARCH_PAGE_SIZE
        self.search_playlist_title = next(
            (
                item.playlist_title
                for item in self.search_results
                if item.playlist_title
            ),
            "",
        )
        self.search_busy = False
        self.search_button.disabled = False
        self._render_search_results(update=False)
        self._update_search_navigation()
        self.page.update(
            self.search_button,
            self.search_status,
            self.search_list,
            self.search_pagination,
        )

    def _search_failed(self, message: str) -> None:
        self.search_busy = False
        self.search_button.disabled = False
        self.search_results = []
        self.search_has_next = False
        self.search_status.value = message
        self.search_list.controls = [_empty_state(ft.Icons.CLOUD_OFF_ROUNDED, message)]
        self._update_search_navigation(update_status=False)
        self.page.update(
            self.search_button,
            self.search_status,
            self.search_list,
            self.search_pagination,
        )

    def _render_search_results(self, *, update: bool = True) -> None:
        if not self.search_results:
            self.search_list.controls = [
                _empty_state(
                    ft.Icons.SEARCH_OFF_ROUNDED, "No matching music was found."
                )
            ]
        else:
            self.search_list.controls = self.views._search_results(self)
        if update:
            self.page.update(self.search_list)

    def _set_search_view(self, selected: list[str]) -> None:
        self.search_view_mode = selected[0] if selected else "list"
        self._render_search_results()

    def _update_search_navigation(self, *, update_status: bool = True) -> None:
        result_count = len(self.search_results)
        result_word = "result" if result_count == 1 else "results"
        if update_status:
            if result_count:
                playlist = (
                    f"{self.search_playlist_title} • "
                    if self.search_playlist_title
                    else ""
                )
                self.search_status.value = (
                    f"{playlist}Page {self.search_page} • {result_count} {result_word}"
                )
            else:
                self.search_status.value = "No matching music was found."
        self.search_page_label.value = f"Page {self.search_page}"
        self.search_pagination.visible = (
            bool(self.search_results) or self.search_page > 1
        )
        self.search_first_button.disabled = self.search_busy or self.search_page <= 1
        self.search_previous_button.disabled = self.search_busy or self.search_page <= 1
        self.search_next_button.disabled = self.search_busy or not self.search_has_next

    def _search_result_row(
        self, result: SearchResult, playlists: tuple[Playlist, ...]
    ) -> ft.Control:
        return self.views._search_result_row(self, result, playlists)

    def _search_result_card(
        self, result: SearchResult, playlists: tuple[Playlist, ...]
    ) -> ft.Control:
        return self.views._search_result_card(self, result, playlists)

    def _available_search_track(self, result: SearchResult):
        """Read the result-page cache; rendering must never hash audio files."""
        return self._search_local_tracks.get(result.url)

    def _search_result_menu(
        self,
        result: SearchResult,
        playlists: tuple[Playlist, ...],
        *,
        include_primary_actions: bool = False,
    ) -> list[ft.PopupMenuItem]:
        menu_items: list[ft.PopupMenuItem] = []
        local_track = self._available_search_track(result)
        if include_primary_actions and local_track is None:
            menu_items.append(
                ft.PopupMenuItem(
                    content=("Download playlist" if result.is_playlist else "Download"),
                    icon=ft.Icons.DOWNLOAD_ROUNDED,
                    on_click=lambda _, item=result: self._download_result(item),
                )
            )
        known_track = local_track or (
            None
            if result.is_playlist
            else self.manager.find_track_by_source(result.url)
        )
        favorite = bool(known_track and self.library.details(known_track.id).favorite)
        menu_items.extend(
            [
                ft.PopupMenuItem(
                    content="Play next",
                    icon=ft.Icons.QUEUE_PLAY_NEXT_ROUNDED,
                    on_click=lambda _, item=result: self._act_on_search_result(
                        item, "next"
                    ),
                ),
                ft.PopupMenuItem(
                    content="Add to queue",
                    icon=ft.Icons.ADD_TO_QUEUE_ROUNDED,
                    on_click=lambda _, item=result: self._act_on_search_result(
                        item, "queue"
                    ),
                ),
                ft.PopupMenuItem(
                    content="Remove favorite" if favorite else "Favorite",
                    icon=ft.Icons.FAVORITE_BORDER_ROUNDED,
                    on_click=lambda _, item=result: self._act_on_search_result(
                        item, "favorite"
                    ),
                ),
            ]
        )
        menu_items.extend(
            ft.PopupMenuItem(
                content=f"Add to {playlist.name}",
                icon=ft.Icons.PLAYLIST_ADD_ROUNDED,
                on_click=lambda _, item=result, pid=str(playlist.id): (
                    self._act_on_search_result(item, "playlist", pid)
                ),
            )
            for playlist in playlists
        )
        menu_items.append(
            ft.PopupMenuItem(
                content="View details",
                icon=ft.Icons.INFO_OUTLINE_ROUNDED,
                on_click=lambda _, item=result: self._search_result_details(item),
            )
        )
        return menu_items

    def _download_result(
        self,
        result: SearchResult,
        *,
        after: str = "",
        playlist_id: str | None = None,
        request_generation: int | None = None,
    ) -> None:
        def prepare():
            was_active = self.downloads.is_source_active(result.url)
            task = self.downloads.request(result)
            existing = (
                self.downloads.available_track(result.url) if task is None else None
            )
            return was_active, task, existing

        def finish(value):
            was_active, task, existing = value
            relevant = after != "play" or (
                request_generation is not None
                and self.playback.is_current_request(request_generation)
            )
            if existing:
                self._search_local_tracks[result.url] = existing
                if after and relevant:
                    self._apply_track_action([str(existing.id)], after, playlist_id)
                elif not after:
                    self._show_message("This track is already in your library.")
                return
            if task is None:
                return
            if after and relevant:
                self.pending_download_actions[str(task.id)] = (after, playlist_id)
                if request_generation is not None:
                    self._download_play_requests[str(task.id)] = request_generation
                record = self.downloads.get(str(task.id))
                if record and record.status in {"completed", "failed", "cancelled"}:
                    self._download_changed(record)
            self._show_message(
                "Download already in progress." if was_active else "Downloading…"
            )

        def failed(message):
            if after == "play" and request_generation is not None:
                if not self.playback.is_current_request(request_generation):
                    return
                self.playback.on_backend_error(message)
            else:
                self._show_error(message)

        run_io(
            self,
            prepare,
            finish,
            key=("download", result.url, after),
            replace=True,
            failed=failed,
        )

    def _act_on_search_result(
        self, result: SearchResult, action: str, playlist_id: str | None = None
    ) -> None:
        existing = self._available_search_track(result)
        if existing is None and action == "favorite" and not result.is_playlist:
            # Favoriting is metadata-only, so it remains useful for a known
            # record even while its managed file is awaiting repair.
            existing = self.manager.find_track_by_source(result.url)
        if existing:
            if action == "favorite":
                self._toggle_favorite(str(existing.id))
            else:
                self._apply_track_action([str(existing.id)], action, playlist_id)
            return
        self._download_result(result, after=action, playlist_id=playlist_id)

    def _apply_track_action(
        self, track_ids: list[str], action: str, playlist_id: str | None = None
    ) -> None:
        available = [
            track_id for track_id in track_ids if self.manager.has_track(track_id)
        ]
        if not available:
            return
        if action == "next":
            self.playback.add_next_many(available)
            message = "Ready to play next."
        elif action == "queue":
            self.playback.add_last_many(available)
            message = "Added to the queue."
        elif action == "playlist" and playlist_id:
            run_io(
                self,
                lambda: self.manager.add_tracks_to_playlist(playlist_id, available),
                lambda _: self._show_message("Added to playlist."),
            )
            return
        elif action == "favorite":
            run_io(
                self,
                lambda: self.library.favorite_tracks(available),
                lambda _: self._show_message("Added to favorites."),
            )
            return
        elif action == "play":
            autoplay = self.playback.playing if self.playback.snapshot.loading else True
            self.playback.play_tracks(available)
            if not autoplay:
                self.playback.toggle()
            message = "Preparing track…"
        else:
            return
        self._show_message(message)

    def _search_result_details(self, result: SearchResult) -> None:
        detail_rows = [
            ("Uploader / channel", result.uploader or "Unknown uploader"),
            ("Source", result.source),
            ("Type", "Playlist" if result.is_playlist else "Track"),
        ]
        if result.duration:
            detail_rows.append(("Duration", _format_duration(result.duration)))
        if result.entry_count:
            detail_rows.append(("Tracks", str(result.entry_count)))
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(result.title),
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(
                                    label,
                                    width=130,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                                ft.Text(value, selectable=True, expand=True),
                            ]
                        )
                        for label, value in detail_rows
                    ]
                    + [ft.Text(result.url, size=11, selectable=True)],
                    tight=True,
                ),
                scrollable=True,
                inset_padding=ft.Padding.symmetric(horizontal=16, vertical=24),
                actions=[
                    ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog())
                ],
            )
        )

    def _play_search_result(self, result: SearchResult) -> None:
        if (
            self.playback.snapshot.loading
            and self.playback.external_title == result.title
        ):
            return
        generation = self.playback.begin_pending_track(result.title)
        if not result.is_playlist:
            self._download_result(result, after="play", request_generation=generation)
            return

        def finish(stream):
            if self.playback.is_current_request(generation):
                autoplay = self.playback.playing
                self.playback.play_stream(
                    stream,
                    title=result.title,
                    uploader=result.uploader,
                    thumbnail=result.thumbnail,
                )
                if not autoplay:
                    self.playback.toggle()

        def failed(message):
            if self.playback.is_current_request(generation):
                self.playback.on_backend_error(message)

        run_io(
            self,
            lambda: self.providers.get(result.provider_id).resolve_stream(result.url),
            finish,
            key="preview",
            replace=True,
            failed=failed,
        )

    def _search_draft_changed(self, event) -> None:
        self.search_draft = event.control.value or ""
