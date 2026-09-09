from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock
from uuid import UUID

import flet as ft

from musicplayer.app import MusicPlayerApp
from musicplayer.application.models import AppSettings, DownloadRecord, SearchResult
from musicplayer.core.library.models import Playlist
from musicplayer.ui.desktop.shell import DesktopShell


class _Page:
    width = 1540
    height = 960
    window = type("Window", (), {"width": 1540, "height": 960})()

    def update(self, *controls: ft.Control) -> None:
        pass


class _Providers:
    pass


class _Downloads:
    def __init__(self, records: tuple[DownloadRecord, ...]) -> None:
        self.records = records

    def list(self) -> tuple[DownloadRecord, ...]:
        return self.records


class _PlaylistManager:
    def __init__(self, playlists: tuple[Playlist, ...]) -> None:
        self.playlists = playlists

    def list_playlists(self) -> tuple[Playlist, ...]:
        return self.playlists

    def has_playlist(self, playlist_id: str) -> bool:
        return False

    def playlist_tracks(self, playlist_id: UUID) -> tuple[object, ...]:
        return ()

    def list_tracks(self) -> tuple[object, ...]:
        return ()

    def find_track_by_source(self, _: str) -> None:
        return None


class _Library:
    def tracks(self, **_: object) -> tuple[object, ...]:
        return ()


class _Store:
    def get(self, _: str, default: object = None) -> object:
        return default

    def favorite_track_ids(self) -> frozenset[str]:
        return frozenset()


class AppLayoutTests(unittest.TestCase):
    def _app(self) -> MusicPlayerApp:
        app = MusicPlayerApp.__new__(MusicPlayerApp)
        app.shell = DesktopShell(app)
        app.page = _Page()  # ty: ignore[invalid-assignment]
        app.settings = AppSettings()
        app.providers = _Providers()  # ty: ignore[invalid-assignment]
        return app

    def test_progress_media_sync_reuses_resolved_track_metadata(self) -> None:
        app = self._app()
        app.playback = SimpleNamespace(  # ty: ignore[invalid-assignment]
            external_title="",
            external_uploader="",
            external_thumbnail="",
            current_track_id="track",
            duration_ms=3000,
            position_ms=1000,
            playing=True,
            queue=SimpleNamespace(repeat=SimpleNamespace(value="none"), shuffle=False),
        )
        app.manager = Mock()
        app.manager.get_track.return_value = SimpleNamespace(
            title="Cached title", filename="cached.mp3"
        )
        app.library = Mock()
        app.library.details.return_value = SimpleNamespace(
            uploader="Artist", thumbnail="artwork", source_name="YouTube"
        )
        app.backend = Mock()
        app._system_media_key = None
        app._system_media_metadata = ("", "", "", "")

        app._sync_system_media()
        app.playback.position_ms = 2000
        app._sync_system_media(refresh_metadata=False)

        app.manager.get_track.assert_called_once_with("track")
        app.library.details.assert_called_once_with("track")
        self.assertEqual(app.backend.sync_media_session.call_count, 2)
        self.assertEqual(
            app.backend.sync_media_session.call_args_list[-1].kwargs["position_ms"],
            2000,
        )

    def test_foreground_resume_reconciles_native_playback_state(self) -> None:
        app = self._app()
        app.backend = Mock()
        app._sync_system_media = Mock()

        app._app_lifecycle_changed(
            cast(
                ft.AppLifecycleStateChangeEvent,
                SimpleNamespace(state=ft.AppLifecycleState.PAUSE),
            )
        )
        app.backend.refresh_state.assert_not_called()
        app._sync_system_media.assert_not_called()

        app._app_lifecycle_changed(
            cast(
                ft.AppLifecycleStateChangeEvent,
                SimpleNamespace(state=ft.AppLifecycleState.RESUME),
            )
        )

        app.backend.refresh_state.assert_called_once_with()
        app._sync_system_media.assert_called_once_with(refresh_metadata=False)

    def test_settings_uses_responsive_sections_and_fixed_footer(self) -> None:
        app = self._app()

        view = app._settings_view()
        header = view.controls[0]  # ty: ignore[unresolved-attribute]
        sections = view.controls[1]  # ty: ignore[unresolved-attribute]
        footer = view.controls[-1]  # ty: ignore[unresolved-attribute]

        self.assertIsInstance(header, ft.Row)
        self.assertIsInstance(sections, ft.ResponsiveRow)
        self.assertEqual(sections.columns, 12)
        self.assertEqual(len(sections.controls), 4)
        self.assertEqual(sections.controls[0].col, {"xs": 12, "lg": 6, "xl": 4})
        self.assertEqual(sections.controls[2].col, {"xs": 12, "lg": 12, "xl": 4})
        youtube_access_header = sections.controls[2].content.controls[0]
        self.assertEqual(
            youtube_access_header.controls[1].controls[0].value,
            "YouTube access",
        )
        self.assertFalse(self._contains_control(sections.controls[2], ft.Checkbox))
        self.assertEqual(sections.controls[3].col, {"xs": 12})
        reset_everything = sections.controls[3].content.controls[-1]
        self.assertEqual(reset_everything.bgcolor, ft.Colors.ERROR_CONTAINER)
        reset_button = reset_everything.content.controls[-1].content
        self.assertEqual(reset_button.bgcolor, ft.Colors.ERROR)
        self.assertTrue(sections.expand)
        self.assertIsInstance(footer, ft.ResponsiveRow)
        self.assertEqual(footer.columns, 12)
        self.assertEqual(
            footer.controls[-1].content.controls[-1].content,
            "Save settings",
        )

    def test_downloads_use_summary_and_responsive_activity_cards(self) -> None:
        app = self._app()
        app.downloads = _Downloads(  # ty: ignore[invalid-assignment]
            (
                DownloadRecord(
                    id="active",
                    url="https://example.test/active",
                    title="Active track",
                    uploader="Channel",
                    status="downloading",
                ),
                DownloadRecord(
                    id="done",
                    url="https://example.test/done",
                    title="Completed track",
                    status="completed",
                ),
            )
        )

        view = app._downloads_view()
        summary = view.controls[1]  # ty: ignore[unresolved-attribute]
        activity = view.controls[-1]  # ty: ignore[unresolved-attribute]

        self.assertIsInstance(summary, ft.ResponsiveRow)
        self.assertEqual(summary.columns, 12)
        self.assertEqual(len(summary.controls), 4)
        self.assertEqual(
            summary.controls[0].col,
            {"xs": 12, "sm": 6, "lg": 3},
        )
        self.assertIsInstance(activity, ft.ResponsiveRow)
        self.assertEqual(len(activity.controls), 2)
        self.assertEqual(activity.controls[0].col, {"xs": 12, "lg": 6})

    def test_downloads_show_batch_summary_and_per_song_context(self) -> None:
        app = self._app()
        app.downloads = _Downloads(  # ty: ignore[invalid-assignment]
            (
                DownloadRecord(
                    id="batch-active",
                    url="https://example.test/active",
                    title="Active track",
                    status="downloading",
                    progress=0.5,
                    batch_id="batch",
                    batch_position=1,
                    batch_size=2,
                ),
                DownloadRecord(
                    id="batch-done",
                    url="https://example.test/done",
                    title="Completed track",
                    status="completed",
                    progress=1.0,
                    batch_id="batch",
                    batch_position=2,
                    batch_size=2,
                ),
            )
        )

        view = app._downloads_view()
        batch_heading = view.controls[2]  # ty: ignore[unresolved-attribute]
        batch_grid = view.controls[3]  # ty: ignore[unresolved-attribute]
        activity = view.controls[-1]  # ty: ignore[unresolved-attribute]
        first_song_credit = activity.controls[0].content.controls[0].controls[1]

        self.assertEqual(batch_heading.value, "Recent batches")
        self.assertEqual(len(batch_grid.controls), 1)
        self.assertEqual(batch_grid.controls[0].col, {"xs": 12, "lg": 6})
        self.assertIn("Batch song 1 of 2", first_song_credit.controls[1].value)

    def test_playlists_empty_state_uses_a_bounded_flex_child(self) -> None:
        app = self._app()
        app.manager = _PlaylistManager(())  # ty: ignore[invalid-assignment]
        app.library = object()  # ty: ignore[invalid-assignment]
        app._initialize_playlists_page()

        view = app._playlists_view()
        body = view.controls[1]  # ty: ignore[unresolved-attribute]

        self.assertIsInstance(body, ft.Container)
        self.assertTrue(body.expand)
        self.assertIsInstance(body.content, ft.Container)
        self.assertIsNone(body.content.expand)
        self.assertEqual(
            body.content.content.controls[1].value,
            "Create a playlist or import one from YouTube to get started.",
        )
        actions = view.controls[0].controls[1].content  # ty: ignore[unresolved-attribute]
        self.assertEqual(actions.controls[0].content, "Import playlist")
        self.assertEqual(actions.controls[1].content, "New playlist")

    def test_playlists_with_items_keep_the_wrapping_card_grid(self) -> None:
        app = self._app()
        app.manager = _PlaylistManager(  # ty: ignore[invalid-assignment]
            (Playlist(id=UUID(int=1), name="Existing playlist"),)
        )
        app.library = object()  # ty: ignore[invalid-assignment]
        app._initialize_playlists_page()

        view = app._playlists_view()
        body = view.controls[1]  # ty: ignore[unresolved-attribute]

        self.assertIsInstance(body, ft.Row)
        self.assertTrue(body.wrap)
        self.assertTrue(body.expand)
        self.assertEqual(len(body.controls), 1)

    def test_all_responsive_page_wraps_have_non_expanding_direct_children(self) -> None:
        app = self._app()
        app.manager = _PlaylistManager(  # ty: ignore[invalid-assignment]
            (Playlist(id=UUID(int=1), name="Existing playlist"),)
        )
        app.library = _Library()  # ty: ignore[invalid-assignment]
        app.store = _Store()  # ty: ignore[invalid-assignment]
        app._initialize_search_page()
        app._initialize_library_page()
        app._initialize_playlists_page()
        app.search_results = [
            SearchResult(
                id="result",
                title="Result",
                uploader="Uploader",
                duration=1,
                thumbnail="",
                url="https://example.test/result",
                source="YouTube",
            )
        ]
        app.search_view_mode = "grid"

        views = (
            app._home_view(),
            app._search_view(),
            app._library_view(),
            app._playlists_view(),
        )

        for view in views:
            self._assert_wrapping_rows_are_flex_safe(view)

        app.manager = _PlaylistManager(())  # ty: ignore[invalid-assignment]
        self._assert_wrapping_rows_are_flex_safe(app._playlists_view())

    def test_search_has_no_source_selector_and_uses_youtube(self) -> None:
        app = self._app()
        app.manager = _PlaylistManager(())  # ty: ignore[invalid-assignment]
        app.store = _Store()  # ty: ignore[invalid-assignment]
        app._initialize_search_page()

        view = app._search_view()
        search_controls = view.controls[1].controls  # ty: ignore[unresolved-attribute]

        self.assertEqual(len(search_controls), 3)
        self.assertFalse(
            any(isinstance(control, ft.Dropdown) for control in search_controls)
        )
        self.assertEqual(app.search_query.col, {"xs": 12, "md": 8})
        self.assertIn("YouTube", view.controls[0].controls[1].value)  # ty: ignore[unresolved-attribute]

    def test_loaded_playlist_title_is_shown_above_its_tracks(self) -> None:
        app = self._app()
        app.manager = _PlaylistManager(())  # ty: ignore[invalid-assignment]
        app.store = _Store()  # ty: ignore[invalid-assignment]
        app._initialize_search_page()
        app.search_results = [
            SearchResult(
                id="track",
                title="Playlist track",
                uploader="Channel",
                duration=120,
                thumbnail="",
                url="https://youtu.be/track",
                source="YouTube",
                playlist_title="Focus playlist",
            )
        ]
        app.search_playlist_title = "Focus playlist"

        app._search_view()

        self.assertIn("Focus playlist", app.search_status.value)
        self.assertIn("1 result", app.search_status.value)

    def test_empty_state_does_not_emit_expansion_metadata(self) -> None:
        from musicplayer.ui.components.common import _empty_state

        empty = _empty_state(ft.Icons.INFO_OUTLINE_ROUNDED, "Nothing here")

        self.assertIsNone(empty.expand)

    def _assert_wrapping_rows_are_flex_safe(self, control: ft.Control) -> None:
        if isinstance(control, ft.Row) and control.wrap:
            for child in control.controls:
                self.assertIsNone(
                    child.expand,
                    f"{type(child).__name__} expands directly inside a wrapping Row",
                )
        children: list[ft.Control] = []
        controls = getattr(control, "controls", None)
        if controls:
            children.extend(controls)
        content = getattr(control, "content", None)
        if isinstance(content, ft.Control):
            children.append(content)
        for child in children:
            self._assert_wrapping_rows_are_flex_safe(child)

    def _contains_control(
        self, control: ft.Control, control_type: type[ft.Control]
    ) -> bool:
        if isinstance(control, control_type):
            return True
        children = list(getattr(control, "controls", None) or [])
        content = getattr(control, "content", None)
        if isinstance(content, ft.Control):
            children.append(content)
        return any(self._contains_control(child, control_type) for child in children)


if __name__ == "__main__":
    unittest.main()
