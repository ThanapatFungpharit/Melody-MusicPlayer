from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import flet as ft

from musicplayer.application.downloads import (
    DuplicateDownloadError,
    extract_download_urls,
)
from musicplayer.application.models import SearchResult
from musicplayer.application.providers import YOUTUBE_PROVIDER_ID, ProviderError
from musicplayer.core.library.models import Playlist
from musicplayer.ui.components.common import (
    _empty_state,
    _format_duration,
)

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

        def start_batch(_: object) -> None:
            urls = extract_download_urls(urls_field.value or "")
            if len(urls) < 2:
                validation.value = "Paste at least two complete YouTube URLs."
                self.page.update(validation)
                return
            try:
                batch = self.downloads.start_urls(urls)
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

    def _start_url_batch(self, urls: tuple[str, ...]) -> None:
        try:
            batch = self.downloads.start_urls(urls)
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
        if self.search_busy:
            return
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
        if not self._submit_background(self._perform_search, query, page, remember):
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

    def _perform_search(
        self,
        query: str,
        page: int,
        remember: bool,
    ) -> None:
        try:
            offset = (page - 1) * self.SEARCH_PAGE_SIZE
            fetched = self.providers.search(
                YOUTUBE_PROVIDER_ID,
                query,
                limit=self.SEARCH_PAGE_SIZE + 1,
                offset=offset,
            )
        except ProviderError as error:
            self.search_results = []
            self.search_has_next = False
            self.search_playlist_title = ""
            self.search_status.value = str(error)
            self.search_list.controls = [
                _empty_state(ft.Icons.CLOUD_OFF_ROUNDED, str(error))
            ]
        except Exception:
            self.search_results = []
            self.search_has_next = False
            self.search_playlist_title = ""
            logger.exception("Unexpected provider search failure")
            self.search_status.value = (
                "YouTube search failed. Check your connection and try again."
            )
            self.search_list.controls = [
                _empty_state(ft.Icons.ERROR_OUTLINE_ROUNDED, self.search_status.value)
            ]
        else:
            results = fetched[: self.SEARCH_PAGE_SIZE]
            self.search_results = results
            self.search_page = page
            self.search_has_next = len(fetched) > self.SEARCH_PAGE_SIZE
            self.search_playlist_title = next(
                (item.playlist_title for item in results if item.playlist_title),
                "",
            )
            if remember:
                self.store.add_recent("search_history", query, limit=20)
            self._render_search_results(update=False)
            self._update_search_navigation()
        finally:
            self.search_busy = False
            self.search_button.disabled = False
            self._update_search_navigation(update_status=False)
            try:
                self.page.update(
                    self.search_button,
                    self.search_status,
                    self.search_list,
                    self.search_pagination,
                )
            except Exception:
                logger.debug(
                    "Search result update skipped after window disconnect",
                    exc_info=True,
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
        """Resolve the canonical intact local track for a search result."""
        if result.is_playlist:
            return None
        resolver = getattr(getattr(self, "downloads", None), "available_track", None)
        if resolver is not None:
            return resolver(result.url)
        existing = self.manager.find_track_by_source(result.url)
        if existing is None:
            return None
        try:
            return (
                existing
                if self.manager.check_track_integrity(existing.id) is None
                else None
            )
        except (KeyError, OSError, ValueError):
            return None

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
    ) -> None:
        active_check = getattr(self.downloads, "is_source_active", None)
        was_active = bool(active_check and active_check(result.url))
        try:
            requester = getattr(self.downloads, "request", None)
            task = (
                requester(result)
                if requester is not None
                else self.downloads.start(result)
            )
        except DuplicateDownloadError as error:
            existing = self._available_search_track(result)
            if existing and after:
                self._apply_track_action([str(existing.id)], after, playlist_id)
            else:
                self._show_message(str(error))
        except (ValueError, OSError, RuntimeError) as error:
            self._show_error(str(error))
        else:
            if task is None:
                existing = self._available_search_track(result)
                if existing and after:
                    self._apply_track_action([str(existing.id)], after, playlist_id)
                elif existing:
                    self._show_message("This track is already in your library.")
                return
            if after:
                self.pending_download_actions[str(task.id)] = (after, playlist_id)
                record = self.downloads.get(str(task.id))
                if record and record.status in {"completed", "failed", "cancelled"}:
                    self._download_changed(record)
            noun = "playlist" if result.is_playlist else "track"
            follow_up = {
                "next": " It will play next when ready.",
                "queue": " It will join the queue when ready.",
                "playlist": " It will be added to the playlist when ready.",
            }.get(after, "")
            prefix = "Download already in progress for" if was_active else "Downloading"
            self._show_message(f"{prefix} {noun}.{follow_up}")

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
            self.manager.add_tracks_to_playlist(playlist_id, available)
            playlist = self.manager.get_playlist(playlist_id)
            message = f"Added to {playlist.name}."
        elif action == "favorite":
            self.library.favorite_tracks(available)
            message = "Added to favorites."
        elif action == "play":
            self.playback.play_tracks(available)
            message = "Playing now."
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
        if not result.is_playlist:
            existing = self._available_search_track(result)
            if existing is not None:
                self.playback.play_track(str(existing.id))
                return
            self._download_result(result, after="play")
            return

        # A playlist search result is a container, not a single library track.
        # Keep the useful lightweight preview interaction, and make that intent
        # explicit in the platform-specific label.
        provider_id = result.provider_id
        if self._submit_background(self._resolve_and_play, provider_id, result):
            self._show_message(f"Previewing “{result.title}”…")

    def _resolve_and_play(self, provider_id: str, result: SearchResult) -> None:
        try:
            stream = self.providers.get(provider_id).resolve_stream(result.url)
            self.playback.play_stream(
                stream,
                title=result.title,
                uploader=result.uploader,
                thumbnail=result.thumbnail,
            )
        except ProviderError as error:
            self._show_error(str(error))

    def _search_draft_changed(self, event) -> None:
        self.search_draft = event.control.value or ""
