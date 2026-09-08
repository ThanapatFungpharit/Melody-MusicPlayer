from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import flet as ft
from ui_support import TestPage, make_app, walk

from musicplayer.ui.desktop.player import DesktopPlayer
from musicplayer.ui.mobile.player import MobilePlayer
from musicplayer.ui.presentation import PresentationKind, presentation_kind


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.apps = []

    def tearDown(self):
        for app in self.apps:
            app._close(None)
        self.temporary.cleanup()

    def app(self, *, mobile=True, width=390, height=844, populated=True):
        page = TestPage(
            width,
            height,
            ft.PagePlatform.ANDROID if mobile else ft.PagePlatform.WINDOWS,
        )
        app = make_app(
            page, self.root / str(len(self.apps)), mobile=mobile, populated=populated
        )
        self.apps.append(app)
        return app

    def test_platform_selection_is_not_a_width_breakpoint(self):
        self.assertEqual(
            presentation_kind(ft.PagePlatform.IOS), PresentationKind.MOBILE
        )
        self.assertEqual(
            presentation_kind(ft.PagePlatform.WINDOWS), PresentationKind.DESKTOP
        )
        self.assertEqual(
            presentation_kind("linux", native_mobile=True), PresentationKind.MOBILE
        )
        app = self.app(mobile=True, width=900, height=390)
        asyncio.run(app._page_resized(SimpleNamespace(width=1200, height=800)))
        self.assertIsInstance(app.player, MobilePlayer)
        desktop = self.app(mobile=False, width=1040)
        asyncio.run(desktop._page_resized(SimpleNamespace(width=720, height=700)))
        self.assertIsInstance(desktop.player, DesktopPlayer)

    def test_mobile_navigation_uses_all_edge_safe_areas(self):
        app = self.app()
        controls = list(walk(app.page.views[0]))
        self.assertEqual(sum(isinstance(c, ft.NavigationBar) for c in controls), 1)
        self.assertFalse(
            any(
                isinstance(c, (ft.NavigationRail, ft.NavigationDrawer))
                for c in controls
            )
        )
        safe = next(c for c in controls if isinstance(c, ft.SafeArea))
        for edge in ("left", "top", "right", "bottom"):
            self.assertTrue(getattr(safe, f"avoid_intrusions_{edge}"))
        self.assertTrue(safe.maintain_bottom_view_padding)

        for panel in ("player", "queue", "downloads", "settings"):
            app.shell.open_panel(panel)
            view_controls = list(walk(app.page.views[-1]))
            panel_safe = next(c for c in view_controls if isinstance(c, ft.SafeArea))
            self.assertTrue(panel_safe.avoid_intrusions_top)
            self.assertTrue(panel_safe.avoid_intrusions_bottom)
            self.assertTrue(panel_safe.maintain_bottom_view_padding)
            app.shell.close_panel()

    def test_now_playing_queue_and_native_back_preserve_playback(self):
        app = self.app()
        before = app.playback.queue.to_dict(position_ms=app.playback.position_ms)
        app.shell.open_panel("player")
        app._open_queue_panel()
        self.assertEqual(len(app.page.views), 3)
        app.page.on_view_pop(None)
        self.assertEqual(app.active_panel, "player")
        app.page.on_view_pop(None)
        self.assertIsNone(app.active_panel)
        self.assertEqual(
            before, app.playback.queue.to_dict(position_ms=app.playback.position_ms)
        )

    def test_phone_back_returns_from_playlist_and_then_to_home(self):
        app = self.app()
        app._open_playlist(str(app.manager.list_playlists()[0].id))
        event = SimpleNamespace(control=SimpleNamespace(confirm_pop=AsyncMock()))
        asyncio.run(app.shell._confirm_back(event))
        self.assertIsNone(app.selected_playlist_id)
        self.assertEqual(app.selected_navigation, 3)
        asyncio.run(app.shell._confirm_back(event))
        self.assertEqual(app.selected_navigation, 0)

    def test_desktop_queue_docks_and_settings_uses_workspace(self):
        app = self.app(mobile=False, width=1540)
        app._open_queue_panel()
        self.assertTrue(app.content.visible)
        self.assertEqual(app.shell.panel.width, 430)
        app._open_settings_panel()
        self.assertFalse(app.content.visible)
        self.assertTrue(app.shell.panel.expand)
        app._close_context_panel()
        self.assertTrue(app.content.visible)

    def test_search_and_library_drafts_survive_navigation(self):
        for mobile in (True, False):
            app = self.app(mobile=mobile, width=390 if mobile else 1540)
            app.navigate(1)
            app.search_query.value = "Unsubmitted search"
            app._search_draft_changed(SimpleNamespace(control=app.search_query))
            app.navigate(2)
            app.library_query.value = "Ocean"
            app._library_query_changed(SimpleNamespace(control=app.library_query))
            app._set_library_sort("title")
            app.navigate(0)
            app.navigate(1)
            self.assertEqual(app.search_query.value, "Unsubmitted search")
            app.navigate(2)
            self.assertEqual(app.library_query.value, "Ocean")
            self.assertEqual(app.library_sort_key, "title")

    def test_desktop_sidebar_actions_preserve_queue_and_restore_navigation(self):
        app = self.app(mobile=False, width=1540)
        queue = app.playback.queue.to_dict(position_ms=app.playback.position_ms)
        for index, button in enumerate(app.shell.nav_buttons):
            button.on_click(None)
            self.assertEqual(app.selected_navigation, index)
            self.assertIsNone(app.active_panel)
        for name, button in app.shell.utility_buttons.items():
            button.on_click(None)
            self.assertEqual(app.active_panel, name)
            self.assertFalse(app.content.visible)
            app.shell.close_panel()
            self.assertEqual(app.selected_navigation, 3)
            self.assertTrue(app.content.visible)
        app.player.queue_button.on_click(None)
        self.assertEqual(app.active_panel, "queue")
        self.assertTrue(app.content.visible)
        app.shell.nav_buttons[2].on_click(None)
        self.assertEqual(app.selected_navigation, 2)
        self.assertEqual(app.active_panel, "queue")
        self.assertEqual(
            app.playback.queue.to_dict(position_ms=app.playback.position_ms), queue
        )

    def test_desktop_resize_keeps_settings_and_seek_drafts(self):
        app = self.app(mobile=False, width=1540)
        app.shell.utility_buttons["settings"].on_click(None)
        field = app.settings_path
        field.value = "An unsaved music folder"
        app.player.seeking = True
        app.player.seek.value = 45000
        for width in (1040, 1540):
            app.page.width = width
            asyncio.run(app._page_resized(SimpleNamespace(width=width, height=700)))
            self.assertIs(app.settings_path, field)
            self.assertEqual(field.value, "An unsaved music folder")
            self.assertEqual(app.player.seek.value, 45000)
            self.assertFalse(app.content.visible)
            self.assertEqual(app.shell.nav_labels[0].visible, width == 1540)
        app.shell.nav_buttons[1].on_click(None)
        self.assertEqual(app.selected_navigation, 1)
        self.assertIsNone(app.active_panel)

    def test_desktop_custom_handles_keep_queue_and_playlist_reordering(self):
        app = self.app(mobile=False, width=1540)
        app._open_queue_panel()
        queue_view = next(
            c for c in walk(app.shell.panel) if isinstance(c, ft.ReorderableListView)
        )
        before = list(app.playback.queue.items)
        self.assertFalse(queue_view.show_default_drag_handles)
        self.assertTrue(
            all(
                any(isinstance(c, ft.ReorderableDragHandle) for c in walk(row))
                for row in queue_view.controls
            )
        )
        queue_view.on_reorder(SimpleNamespace(old_index=0, new_index=2))
        self.assertEqual(app.playback.queue.items, [before[1], before[0], before[2]])
        app.shell.close_panel()
        playlist = app.manager.list_playlists()[0]
        before = list(playlist.track_ids)
        app._open_playlist(str(playlist.id))
        playlist_view = next(
            c for c in walk(app.content) if isinstance(c, ft.ReorderableListView)
        )
        self.assertFalse(playlist_view.show_default_drag_handles)
        self.assertTrue(
            all(
                any(isinstance(c, ft.ReorderableDragHandle) for c in walk(row))
                for row in playlist_view.controls
            )
        )
        playlist_view.on_reorder(SimpleNamespace(old_index=0, new_index=2))
        self.assertEqual(
            list(app.manager.get_playlist(playlist.id).track_ids),
            [before[1], before[0], before[2]],
        )

    def test_orientation_keeps_settings_draft_and_cookie_selection(self):
        app = self.app()
        app._open_settings_panel()
        field = app.settings_theme
        field.value = "light"
        app._pending_cookie_name = "cookies.txt"
        asyncio.run(app._page_resized(SimpleNamespace(width=844, height=390)))
        self.assertIs(app.settings_theme, field)
        self.assertEqual(field.value, "light")
        self.assertEqual(app._pending_cookie_name, "cookies.txt")

    def test_mobile_player_actions_share_playback_state_without_volume_slider(self):
        app = self.app()
        app.shell.open_panel("player")
        player = app.player
        desktop = DesktopPlayer(app)
        player.shuffle.on_click(None)
        player.repeat.on_click(None)
        player.mute.on_click(None)
        desktop.refresh()
        self.assertEqual(player.shuffle.icon_color, desktop.shuffle.icon_color)
        self.assertEqual(player.repeat.tooltip, desktop.repeat.tooltip)
        self.assertEqual(player.mute.icon, desktop.mute.icon)
        self.assertEqual(
            sum(isinstance(c, ft.Slider) for c in walk(player.full_player)), 1
        )
        self.assertEqual(sum(isinstance(c, ft.Slider) for c in walk(desktop.root)), 2)
        player.shuffle.on_click(None)
        original_ids = list(app.playback.queue.items)
        player.next.on_click(None)
        self.assertEqual(app.playback.current_track_id, original_ids[1])
        player.previous.on_click(None)
        self.assertEqual(app.playback.current_track_id, original_ids[0])

    def test_progress_does_not_replace_screen_or_override_a_seek_drag(self):
        app = self.app()
        app.shell.open_panel("player")
        app.playback.duration_ms = 180000
        app.player.seek.value = 60000
        app.player.seeking = True
        view = app.page.views[-1]
        app.playback.on_position(3000)
        self.assertEqual(app.player.seek.value, 60000)
        self.assertIs(app.page.views[-1], view)
        self.assertNotIn(app.content, app.page.updated[-1])
        app.player._seek_end(SimpleNamespace(control=SimpleNamespace(value=60000)))
        self.assertEqual(app.playback.position_ms, 60000)

    def test_mobile_actions_close_sheet_then_reorder_or_await_file_picker(self):
        from musicplayer.ui.mobile.controls import action_sheet

        app = self.app()
        queue = list(app.playback.queue.items)
        action_sheet(
            app,
            "Track",
            [
                ft.PopupMenuItem(
                    content="Move down",
                    on_click=lambda _: app.playback.reorder_queue(0, 2),
                )
            ],
        )
        sheet = app.page.dialogs[-1]
        self.assertTrue(sheet.use_safe_area)
        self.assertTrue(sheet.scrollable)
        self.assertTrue(sheet.maintain_bottom_view_insets_padding)
        tile = next(c for c in walk(app.page.dialogs[-1]) if isinstance(c, ft.ListTile))
        asyncio.run(tile.on_click(None))
        self.assertFalse(app.page.dialogs)
        self.assertEqual(app.playback.queue.items, [queue[1], queue[0], queue[2]])
        pick = AsyncMock()
        action_sheet(
            app, "Library", [ft.PopupMenuItem(content="Add files", on_click=pick)]
        )
        tile = next(c for c in walk(app.page.dialogs[-1]) if isinstance(c, ft.ListTile))
        asyncio.run(tile.on_click(None))
        pick.assert_awaited_once()

    def test_mobile_search_ignores_desktop_grid_preference(self):
        app = self.app()
        app.search_view_mode = "grid"
        app.navigate(1)
        self.assertEqual(len(app.search_list.controls), 1)
        self.assertFalse(
            any(isinstance(c, ft.PopupMenuButton) for c in walk(app.search_list))
        )

    def test_all_screens_build_for_phone_orientations_and_desktop(self):
        for mobile, width, height in (
            (True, 320, 568),
            (True, 390, 844),
            (True, 844, 390),
            (False, 1040, 700),
            (False, 1540, 960),
        ):
            for populated in (False, True):
                with self.subTest(mobile=mobile, width=width, populated=populated):
                    app = self.app(
                        mobile=mobile, width=width, height=height, populated=populated
                    )
                    for index in range(4):
                        app.navigate(index)
                        controls = list(walk(app.content.content))
                        if mobile:
                            self.assertFalse(
                                any(
                                    isinstance(
                                        c, (ft.PopupMenuButton, ft.ReorderableListView)
                                    )
                                    for c in controls
                                )
                            )
                            for c in controls:
                                if isinstance(c, ft.IconButton):
                                    self.assertGreaterEqual(c.width or 0, 48)
                                    self.assertGreaterEqual(c.height or 0, 48)
                    for panel in ("queue", "downloads", "settings"):
                        app.shell.open_panel(panel)
                        app._close_context_panel()


if __name__ == "__main__":
    unittest.main()
