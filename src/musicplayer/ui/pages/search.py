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
from musicplayer.ui.components.common import (
    _artwork,
    _empty_state,
    _format_duration,
    _page_header,
)
from musicplayer.ui.theme import card

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
        self.search_results: list[SearchResult] = []
        self.search_busy = False
        self.search_page = 1
        self.search_has_next = False
        self.search_query_text = ""
        self.search_playlist_title = ""
        self.search_view_mode = "list"

    def _search_view(self) -> ft.Control:
        self.search_query = ft.TextField(
            value=self.search_query_text,
            hint_text="Search YouTube songs, channels, or playlists — or paste a YouTube URL",
            prefix_icon=ft.Icons.SEARCH_ROUNDED,
            border_radius=16,
            expand=True,
            on_submit=lambda _: self._start_search(),
        )
        self.search_button = ft.Button(
            "Search",
            icon=ft.Icons.SEARCH_ROUNDED,
            on_click=lambda _: self._start_search(),
        )
        self.batch_download_button = ft.Button(
            "Batch URLs",
            icon=ft.Icons.DOWNLOAD_ROUNDED,
            on_click=lambda _: self._batch_download_dialog(),
        )
        self.search_query.col = {"xs": 12, "md": 8}
        self.search_button.col = {"xs": 12, "md": 2}
        self.batch_download_button.col = {"xs": 12, "md": 2}
        self.search_status = ft.Text(
            "Search YouTube or inspect a YouTube video or playlist URL.",
            color=ft.Colors.ON_SURFACE_VARIANT,
            col={"xs": 12, "md": 7},
        )
        self.search_view_selector = ft.SegmentedButton(
            segments=[
                ft.Segment("list", icon=ft.Icons.VIEW_LIST_ROUNDED, label="Track list"),
                ft.Segment("grid", icon=ft.Icons.GRID_VIEW_ROUNDED, label="Grid"),
            ],
            selected=[self.search_view_mode],
            show_selected_icon=False,
            on_change=lambda event: self._set_search_view(event.control.selected),
            col={"xs": 12, "md": 5},
        )
        self.search_previous_button = ft.IconButton(
            ft.Icons.CHEVRON_LEFT_ROUNDED,
            tooltip="Previous page",
            disabled=self.search_page <= 1,
            on_click=lambda _: self._load_search_page(self.search_page - 1),
        )
        self.search_first_button = ft.TextButton(
            "First",
            disabled=self.search_page <= 1,
            on_click=lambda _: self._load_search_page(1),
        )
        self.search_page_label = ft.Text(
            f"Page {self.search_page}", weight=ft.FontWeight.W_600
        )
        self.search_next_button = ft.IconButton(
            ft.Icons.CHEVRON_RIGHT_ROUNDED,
            tooltip="Next page",
            disabled=not self.search_has_next,
            on_click=lambda _: self._load_search_page(self.search_page + 1),
        )
        self.search_pagination = ft.Row(
            [
                self.search_first_button,
                self.search_previous_button,
                self.search_page_label,
                self.search_next_button,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=6,
            visible=bool(self.search_results) or self.search_page > 1,
        )
        self.search_list = ft.Column(spacing=10)
        if self.search_results:
            self._render_search_results(update=False)
            self._update_search_navigation()
        else:
            self.search_list.controls = [
                _empty_state(
                    ft.Icons.TRAVEL_EXPLORE_ROUNDED,
                    "Start with a song, channel, playlist, or YouTube URL.",
                )
            ]
        return ft.Column(
            [
                _page_header("Search", "Find music on YouTube."),
                self._responsive_grid(
                    [
                        self.search_query,
                        self.search_button,
                        self.batch_download_button,
                    ],
                    spacing=12,
                ),
                self._responsive_grid(
                    [self.search_status, self.search_view_selector],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Divider(height=1),
                ft.Column([self.search_list], scroll=ft.ScrollMode.AUTO, expand=True),
                self.search_pagination,
            ],
            spacing=16,
            expand=True,
        )

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
            self._show_message(
                f"Started a batch of {batch.size} songs. Each song will continue independently."
            )
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
        self._show_message(
            f"Started a batch of {batch.size} songs. Each song will continue independently."
        )
        self._open_downloads_panel()

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
        elif self.search_view_mode == "grid":
            self.search_list.controls = [
                ft.Row(
                    [self._search_result_card(item) for item in self.search_results],
                    wrap=True,
                    spacing=14,
                    run_spacing=14,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
            ]
        else:
            self.search_list.controls = [
                self._search_result_row(item) for item in self.search_results
            ]
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

    def _search_result_row(self, result: SearchResult) -> ft.Control:
        subtitle = f"{result.uploader}  •  {result.source}"
        if result.is_playlist:
            subtitle += f"  •  {result.entry_count or 'Multiple'} tracks"
        elif result.duration:
            subtitle += f"  •  {_format_duration(result.duration)}"
        local_track = (
            None
            if result.is_playlist
            else self.manager.find_track_by_source(result.url)
        )
        menu_items = self._search_result_menu(result)
        compact = self._is_compact()
        actions: list[ft.Control] = (
            [
                ft.PopupMenuButton(
                    icon=ft.Icons.MORE_HORIZ_ROUNDED,
                    tooltip="Actions",
                    items=menu_items,
                )
            ]
            if compact
            else [
                ft.IconButton(
                    ft.Icons.PLAY_ARROW_ROUNDED,
                    tooltip="Play now",
                    on_click=lambda _, item=result: self._play_search_result(item),
                ),
                ft.IconButton(
                    ft.Icons.DOWNLOAD_DONE_ROUNDED
                    if local_track
                    else ft.Icons.DOWNLOAD_ROUNDED,
                    tooltip="Already in library" if local_track else "Download",
                    disabled=bool(local_track),
                    on_click=lambda _, item=result: self._download_result(item),
                ),
                ft.PopupMenuButton(
                    icon=ft.Icons.MORE_HORIZ_ROUNDED,
                    tooltip="More actions",
                    items=menu_items,
                ),
            ]
        )
        return card(
            ft.Row(
                [
                    _artwork(result.thumbnail, 64, playlist=result.is_playlist),
                    ft.Column(
                        [
                            ft.Text(
                                result.title, weight=ft.FontWeight.W_600, max_lines=1
                            ),
                            ft.Text(
                                subtitle,
                                size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                                max_lines=1,
                            ),
                        ],
                        spacing=5,
                        expand=True,
                    ),
                    *actions,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=12,
        )

    def _search_result_card(self, result: SearchResult) -> ft.Control:
        local_track = (
            None
            if result.is_playlist
            else self.manager.find_track_by_source(result.url)
        )
        detail = result.uploader or "Unknown uploader"
        if result.is_playlist:
            detail += f" • {result.entry_count or 'Multiple'} tracks"
        elif result.duration:
            detail += f" • {_format_duration(result.duration)}"
        compact = self._is_compact()
        art_size = 150 if compact else 220
        card_width = 180 if compact else 260
        return ft.Container(
            card(
                ft.Column(
                    [
                        _artwork(result.thumbnail, art_size, playlist=result.is_playlist),
                        ft.Text(
                            result.title,
                            weight=ft.FontWeight.BOLD,
                            max_lines=2,
                            height=40 if compact else 44,
                            size=13 if compact else 14,
                        ),
                        ft.Text(
                            detail,
                            size=11 if compact else 12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            max_lines=1,
                        ),
                        ft.Row(
                            [
                                ft.IconButton(
                                    ft.Icons.PLAY_ARROW_ROUNDED,
                                    tooltip="Play now",
                                    icon_size=20 if compact else 24,
                                    on_click=lambda _, item=result: (
                                        self._play_search_result(item)
                                    ),
                                ),
                                ft.IconButton(
                                    ft.Icons.DOWNLOAD_DONE_ROUNDED
                                    if local_track
                                    else ft.Icons.DOWNLOAD_ROUNDED,
                                    tooltip=(
                                        "Already in library"
                                        if local_track
                                        else "Download"
                                    ),
                                    icon_size=20 if compact else 24,
                                    disabled=bool(local_track),
                                    on_click=lambda _, item=result: (
                                        self._download_result(item)
                                    ),
                                ),
                                ft.Container(expand=True),
                                ft.PopupMenuButton(
                                    icon=ft.Icons.MORE_HORIZ_ROUNDED,
                                    tooltip="More actions",
                                    items=self._search_result_menu(
                                        result, include_primary_actions=False
                                    ),
                                ),
                            ]
                        ),
                    ],
                    spacing=6 if compact else 8,
                ),
                padding=10 if compact else 12,
            ),
            width=card_width,
        )

    def _search_result_menu(
        self, result: SearchResult, *, include_primary_actions: bool | None = None
    ) -> list[ft.PopupMenuItem]:
        menu_items: list[ft.PopupMenuItem] = []
        show_primary_actions = (
            self._is_compact()
            if include_primary_actions is None
            else include_primary_actions
        )
        if show_primary_actions:
            menu_items.extend(
                [
                    ft.PopupMenuItem(
                        content="Play now",
                        icon=ft.Icons.PLAY_ARROW_ROUNDED,
                        on_click=lambda _, item=result: self._play_search_result(item),
                    ),
                    ft.PopupMenuItem(
                        content="Download",
                        icon=ft.Icons.DOWNLOAD_ROUNDED,
                        on_click=lambda _, item=result: self._download_result(item),
                    ),
                ]
            )
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
                    content="Favorite",
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
            for playlist in self.manager.list_playlists()
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
        try:
            task = self.downloads.start(result)
        except DuplicateDownloadError as error:
            existing = self.manager.find_track_by_source(result.url)
            if existing and after:
                self._apply_track_action([str(existing.id)], after, playlist_id)
            else:
                self._show_message(str(error))
        except (ValueError, OSError, RuntimeError) as error:
            self._show_error(str(error))
        else:
            if after:
                self.pending_download_actions[str(task.id)] = (after, playlist_id)
                record = next(
                    (item for item in self.downloads.list() if item.id == str(task.id)),
                    None,
                )
                if record and record.status in {"completed", "failed", "cancelled"}:
                    self._download_changed(record)
            noun = "playlist" if result.is_playlist else "track"
            follow_up = {
                "next": " It will play next when ready.",
                "queue": " It will join the queue when ready.",
                "playlist": " It will be added to the playlist when ready.",
            }.get(after, "")
            self._show_message(f"Downloading {noun}.{follow_up}")

    def _act_on_search_result(
        self, result: SearchResult, action: str, playlist_id: str | None = None
    ) -> None:
        existing = (
            None
            if result.is_playlist
            else self.manager.find_track_by_source(result.url)
        )
        if existing:
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
            for track_id in reversed(available):
                self.playback.add_next(track_id)
            message = "Ready to play next."
        elif action == "queue":
            for track_id in available:
                self.playback.add_last(track_id)
            message = "Added to the queue."
        elif action == "playlist" and playlist_id:
            for track_id in available:
                self.manager.add_to_playlist(playlist_id, track_id)
            playlist = self.manager.get_playlist(playlist_id)
            message = f"Added to {playlist.name}."
        elif action == "favorite":
            for track_id in available:
                if not self.library.details(track_id).favorite:
                    self.library.toggle_favorite(track_id)
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
        provider_id = result.provider_id
        if self._submit_background(self._resolve_and_play, provider_id, result):
            self._show_message(f"Preparing “{result.title}”…")

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
