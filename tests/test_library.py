from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from musicplayer.core.library import MusicManager


class MusicManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.music = self.root / "music"
        self.music.mkdir()
        self.manager = MusicManager(self.root / "library.mmdb", self.music)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_track(self, name: str, source: str = ""):
        path = self.music / name
        path.write_bytes(f"audio:{name}".encode())
        return self.manager.add_track(path, source=source, title=Path(name).stem)

    def test_tracks_and_playlist_order_survive_restart(self) -> None:
        first = self.add_track("first.mp3")
        second = self.add_track("second.mp3")
        playlist = self.manager.create_playlist("Focus")
        self.manager.add_to_playlist(playlist, first)
        self.manager.add_to_playlist(playlist, second)
        self.manager.reorder_playlist(playlist, [second, first])

        reloaded = MusicManager(self.root / "library.mmdb", self.music)

        self.assertEqual(
            [track.id for track in reloaded.playlist_tracks(playlist)], [second, first]
        )

    def test_source_duplicate_normalizes_youtube_urls(self) -> None:
        track_id = self.add_track(
            "song.mp3", "https://www.youtube.com/watch?v=abc123&utm_source=test"
        )
        found = self.manager.find_track_by_source("https://youtu.be/abc123")
        self.assertEqual(found.id, track_id)  # ty: ignore[unresolved-attribute]

    def test_move_between_playlists_is_atomic(self) -> None:
        track_id = self.add_track("song.mp3")
        source = self.manager.create_playlist("Source")
        target = self.manager.create_playlist("Target")
        self.manager.add_to_playlist(source, track_id)
        self.manager.move_track_between_playlists(source, target, track_id)
        self.assertEqual(self.manager.playlist_tracks(source), ())
        self.assertEqual(self.manager.playlist_tracks(target)[0].id, track_id)

    def test_bulk_add_to_playlist_is_ordered_and_duplicate_safe(self) -> None:
        first = self.add_track("first.mp3")
        second = self.add_track("second.mp3")
        third = self.add_track("third.mp3")
        playlist = self.manager.create_playlist("Bulk")
        self.manager.add_to_playlist(playlist, first)

        added = self.manager.add_tracks_to_playlist(
            playlist, [first, second, second, third]
        )
        repeated = self.manager.add_tracks_to_playlist(playlist, [second, third])

        self.assertEqual(added, 2)
        self.assertEqual(repeated, 0)
        self.assertEqual(
            [track.id for track in self.manager.playlist_tracks(playlist)],
            [first, second, third],
        )

    def test_delete_track_keeps_file(self) -> None:
        track_id = self.add_track("safe.mp3")
        path = self.manager.track_path(track_id)
        self.manager.delete_track(track_id)
        self.assertTrue(path.exists())

    def test_clear_library_removes_tracks_and_playlists_but_keeps_files(self) -> None:
        track_id = self.add_track("kept.mp3")
        path = self.manager.track_path(track_id)
        playlist_id = self.manager.create_playlist("Temporary")
        self.manager.add_to_playlist(playlist_id, track_id)

        self.assertEqual(self.manager.clear_library(), (1, 1))

        self.assertEqual(self.manager.list_tracks(), ())
        self.assertEqual(self.manager.list_playlists(), ())
        self.assertTrue(path.exists())
        self.assertEqual(self.manager.clear_library(), (0, 0))
        reloaded = MusicManager(self.root / "library.mmdb", self.music)
        self.assertEqual(reloaded.list_tracks(), ())
        self.assertEqual(reloaded.list_playlists(), ())

    def test_clear_playlists_preserves_library_tracks(self) -> None:
        track_id = self.add_track("library-track.mp3")
        self.manager.create_playlist("Temporary")

        self.assertEqual(self.manager.clear_playlists(), 1)

        self.assertTrue(self.manager.has_track(track_id))
        self.assertEqual(self.manager.list_playlists(), ())
        self.assertEqual(self.manager.clear_playlists(), 0)
        reloaded = MusicManager(self.root / "library.mmdb", self.music)
        self.assertTrue(reloaded.has_track(track_id))
        self.assertEqual(reloaded.list_playlists(), ())

    def test_find_track_by_content_detects_local_duplicate(self) -> None:
        track_id = self.add_track("same.mp3")
        outside = self.root / "outside.mp3"
        outside.write_bytes(b"audio:same.mp3")
        self.assertEqual(self.manager.find_track_by_content(outside).id, track_id)  # ty: ignore[unresolved-attribute]

    def test_lookup_indexes_follow_source_updates_and_deletion(self) -> None:
        track_id = self.add_track("indexed.mp3", "https://example.test/old")
        same_content = self.root / "same-content.mp3"
        same_content.write_bytes(b"audio:indexed.mp3")

        self.manager.update_track(track_id, source="https://example.test/new")

        self.assertIsNone(self.manager.find_track_by_source("https://example.test/old"))
        self.assertEqual(
            self.manager.find_track_by_source("https://example.test/new").id,  # ty: ignore[unresolved-attribute]
            track_id,
        )
        self.assertEqual(self.manager.find_track_by_content(same_content).id, track_id)  # ty: ignore[unresolved-attribute]

        self.manager.delete_track(track_id)

        self.assertIsNone(self.manager.find_track_by_content(same_content))


if __name__ == "__main__":
    unittest.main()
