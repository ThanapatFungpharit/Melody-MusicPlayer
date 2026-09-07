from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yt_dlp import CookieLoadError

from musicplayer.application.models import AppSettings, DownloadRecord, TrackDetails
from musicplayer.application.providers import (
    ProviderError,
    ProviderRegistry,
    YtDlpProvider,
    _results_from_info,
    is_youtube_url,
)
from musicplayer.application.store import ApplicationStore


class StoreTests(unittest.TestCase):
    def test_settings_and_track_details_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = ApplicationStore(path)
            settings = AppSettings(
                volume=37,
                theme="light",
                cookie_file=str(Path(directory) / "cookies.txt"),
            )
            store.save_settings(settings)
            store.save_track_details(
                "track", TrackDetails(uploader="Nina's label", favorite=True)
            )

            restored = ApplicationStore(path)

            self.assertEqual(restored.settings.volume, 37)
            self.assertEqual(restored.settings.theme, "light")
            self.assertEqual(restored.track_details("track").uploader, "Nina's label")
            self.assertTrue(restored.track_details("track").favorite)
            self.assertEqual(
                restored.settings.cookie_file, str(Path(directory) / "cookies.txt")
            )

    def test_legacy_artist_credit_migrates_to_uploader_without_album(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "track_details": {
                            "track": {
                                "artist": "Legacy channel",
                                "album": "Unverified",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = ApplicationStore(path)

            details = store.track_details("track")
            persisted = store.get("track_details")["track"]

            self.assertEqual(details.uploader, "Legacy channel")
            self.assertFalse(hasattr(details, "album"))
            self.assertNotIn("artist", persisted)
            self.assertNotIn("album", persisted)
            self.assertEqual(store.get("version"), 3)

    def test_legacy_browser_cookie_settings_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "settings": {
                            "use_browser_cookies": True,
                            "cookie_browser": "chrome",
                            "cookie_profile": "Profile 1",
                        },
                    }
                ),
                encoding="utf-8",
            )

            store = ApplicationStore(path)

            self.assertEqual(store.settings.cookie_file, "")
            self.assertNotIn("use_browser_cookies", store.get("settings"))
            self.assertNotIn("cookie_browser", store.get("settings"))
            self.assertNotIn("cookie_profile", store.get("settings"))
            self.assertEqual(store.get("version"), 3)

    def test_legacy_download_artist_migrates_to_uploader(self) -> None:
        record = DownloadRecord.from_dict(
            {"id": "download", "url": "https://example.test", "artist": "Channel"}
        )
        self.assertEqual(record.uploader, "Channel")

    def test_recent_values_are_unique_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            store.add_recent("search_history", "one")
            store.add_recent("search_history", "two")
            store.add_recent("search_history", "one")
            self.assertEqual(store.get("search_history"), ["one", "two"])

    def test_bulk_favorites_use_one_atomic_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            with patch.object(store, "_save_locked", wraps=store._save_locked) as save:
                changed = store.favorite_tracks(("one", "two", "one"))

            self.assertEqual(changed, 2)
            self.assertEqual(save.call_count, 1)
            self.assertTrue(store.track_details("one").favorite)
            self.assertTrue(store.track_details("two").favorite)
            self.assertEqual(store.favorite_track_ids(), frozenset({"one", "two"}))

    def test_readding_first_recent_value_skips_redundant_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            store.add_recent("search_history", "same")
            with patch.object(store, "_save_locked", wraps=store._save_locked) as save:
                store.add_recent("search_history", "same")

            save.assert_not_called()

    def test_failed_atomic_write_restores_in_memory_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            store.set("custom", {"value": "before"})

            with (
                patch.object(store, "_save_locked", side_effect=OSError("disk full")),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                store.set("custom", {"value": "after"})

            self.assertEqual(store.get("custom"), {"value": "before"})

    def test_record_play_commits_metadata_and_histories_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            with patch.object(store, "_save_locked", wraps=store._save_locked) as save:
                store.record_play(
                    "track",
                    played_at=123.5,
                    playback={"queue": ["track"], "current_index": 0},
                )

            details = store.track_details("track")
            self.assertEqual(save.call_count, 1)
            self.assertEqual(details.play_count, 1)
            self.assertEqual(details.last_played, 123.5)
            self.assertEqual(store.get("recent_tracks"), ["track"])
            self.assertEqual(store.get("playback_history"), ["track"])
            self.assertEqual(store.get("playback")["queue"], ["track"])

    def test_failed_play_record_restores_metadata_and_histories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            store.record_play("track", played_at=1.0)

            with (
                patch.object(store, "_save_locked", side_effect=OSError("disk full")),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                store.record_play("other", played_at=2.0)

            self.assertEqual(store.track_details("other").play_count, 0)
            self.assertEqual(store.get("recent_tracks"), ["track"])
            self.assertEqual(store.get("playback_history"), ["track"])

    def test_clear_library_data_preserves_unrelated_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            settings = AppSettings(theme="light")
            store.save_settings(settings)
            store.save_track_details("track", TrackDetails(favorite=True))
            store.set("recent_tracks", ["track"])
            store.set("playback_history", ["track"])
            store.set("search_history", ["keep this search"])
            store.set("downloads", [{"id": "keep-this-download"}])
            store.set(
                "playback",
                {
                    "queue": ["track"],
                    "current_index": 0,
                    "position_ms": 1200,
                    "shuffle": True,
                    "repeat": "playlist",
                },
            )

            store.clear_library_data()
            store.clear_library_data()

            self.assertEqual(store.get("track_details"), {})
            self.assertEqual(store.get("recent_tracks"), [])
            self.assertEqual(store.get("playback_history"), [])
            self.assertEqual(store.get("playback")["queue"], [])
            self.assertEqual(store.get("playback")["current_index"], -1)
            self.assertEqual(store.get("playback")["position_ms"], 0)
            self.assertTrue(store.get("playback")["shuffle"])
            self.assertEqual(store.get("playback")["repeat"], "playlist")
            self.assertEqual(store.settings.theme, "light")
            self.assertEqual(store.get("search_history"), ["keep this search"])
            self.assertEqual(store.get("downloads"), [{"id": "keep-this-download"}])

    def test_recent_history_respects_small_limits(self) -> None:
        for limit, expected in ((-1, []), (0, []), (1, ["new"]), (2, ["new", "old"])):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as directory:
                store = ApplicationStore(Path(directory) / "state.json")
                store.set("search_history", ["old", "older"])
                store.add_recent("search_history", "new", limit=limit)
                self.assertEqual(store.get("search_history"), expected)
                self.assertEqual(
                    ApplicationStore(store.path).get("search_history"), expected
                )

    def test_all_store_mutations_roll_back_when_replacement_fails(self) -> None:
        mutations = {
            "settings": lambda store: store.save_settings(AppSettings(volume=42)),
            "new metadata": lambda store: store.save_track_details(
                "new", TrackDetails()
            ),
            "existing metadata": lambda store: store.save_track_details(
                "track", TrackDetails(uploader="updated")
            ),
            "metadata deletion": lambda store: store.remove_track_details("track"),
            "library clearing": lambda store: store.clear_library_data(),
            "new value": lambda store: store.set("new", {"nested": [1]}),
            "existing value": lambda store: store.set("custom", None),
            "recent history": lambda store: store.add_recent("search_history", "new"),
            "new history": lambda store: store.add_recent("new_history", "new"),
            "play recording": lambda store: store.record_play(
                "track", played_at=2.0, playback={"queue": ["track"], "position_ms": 20}
            ),
            "new play recording": lambda store: store.record_play("new", played_at=2.0),
            "downloads": lambda store: store.save_downloads(
                [DownloadRecord(id="new", url="https://youtu.be/new", title="New")]
            ),
            "bulk favorites": lambda store: store.favorite_tracks(["track", "new"]),
        }
        for name, mutate in mutations.items():
            with (
                self.subTest(mutation=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                store = ApplicationStore(Path(directory) / "state.json")
                store.record_play("track", played_at=1.0)
                store.set("custom", {"nested": [0]})
                store.add_recent("search_history", "old")
                before = store.path.read_bytes()
                expected = json.loads(before)

                with (
                    patch(
                        "musicplayer.application.store.os.replace",
                        side_effect=OSError("disk full"),
                    ),
                    self.assertRaisesRegex(OSError, "disk full"),
                ):
                    mutate(store)

                self.assertEqual(store.path.read_bytes(), before)
                self.assertEqual(store._data, expected)
                self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_unchanged_mutations_skip_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            details = TrackDetails(favorite=True)
            store.save_track_details("track", details)
            store.set("custom", {"nested": [1]})
            store.add_recent("search_history", "same")
            with patch.object(store, "_save_locked") as save:
                store.save_settings(store.settings)
                store.save_track_details("track", details)
                store.remove_track_details("missing")
                store.set("custom", {"nested": [1]})
                store.add_recent("search_history", "same")
                store.save_downloads([])
                self.assertEqual(store.favorite_tracks(["track", "track"]), 0)
            save.assert_not_called()

    def test_store_values_are_isolated_from_callers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            value = {"nested": ["original"]}
            store.set("custom", value)
            value["nested"].append("external")
            fetched = store.get("custom")
            fetched["nested"].clear()
            self.assertEqual(store.get("custom"), {"nested": ["original"]})

            playback = {"queue": ["track"]}
            store.record_play("track", played_at=1, playback=playback)
            playback["queue"].clear()
            self.assertEqual(store.get("playback"), {"queue": ["track"]})

    def test_null_can_be_saved_under_a_new_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            store.set("nullable", None)
            restored = ApplicationStore(store.path)
            self.assertIsNone(restored.get("nullable", "missing"))

    def test_download_history_retains_only_latest_records_from_an_iterator(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            records = [
                DownloadRecord(id=str(i), url="", title=str(i)) for i in range(300)
            ]
            # Discarded records should never need serialization.
            with patch.object(
                records[0], "to_dict", side_effect=AssertionError("discarded")
            ):
                store.save_downloads(iter(records))
            restored = ApplicationStore(store.path).downloads()
            self.assertEqual(
                [record.id for record in restored], [str(i) for i in range(50, 300)]
            )

    def test_failed_favorite_input_iteration_does_not_partially_update_state(
        self,
    ) -> None:
        def track_ids():
            yield "track"
            raise ValueError("incomplete input")

        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStore(Path(directory) / "state.json")
            with self.assertRaisesRegex(ValueError, "incomplete input"):
                store.favorite_tracks(track_ids())
            self.assertEqual(store.get("track_details"), {})
            self.assertFalse(store.path.exists())


class ProviderMappingTests(unittest.TestCase):
    def test_result_limit_does_not_consume_unused_lazy_entries(self) -> None:
        def entries():
            yield None
            yield {"id": "one", "url": "one"}
            raise AssertionError("Consumed beyond the requested result window")

        results = _results_from_info({"entries": entries()}, "YouTube", limit=1)
        self.assertEqual([result.id for result in results], ["one"])
        self.assertEqual(
            _results_from_info({"entries": entries()}, "YouTube", limit=0), []
        )

    def test_stream_resolution_uses_full_metadata_and_shared_overrides(self) -> None:
        provider = YtDlpProvider(
            yt_dlp_options={"socket_timeout": 30, "cookiefile": "cookies.txt"}
        )
        with patch("musicplayer.application.providers.YoutubeDL") as youtube_dl:
            extractor = youtube_dl.return_value.__enter__.return_value
            extractor.extract_info.return_value = {
                "entries": [None, {"url": "https://media.test/audio"}]
            }
            self.assertEqual(
                provider.resolve_stream("https://youtu.be/one"),
                "https://media.test/audio",
            )

        options = youtube_dl.call_args.args[0]
        self.assertEqual(options["format"], "bestaudio/best")
        self.assertFalse(options["extract_flat"])
        self.assertTrue(options["noplaylist"])
        self.assertTrue(options["skip_download"])
        self.assertEqual(options["socket_timeout"], 30)
        self.assertEqual(options["cookiefile"], "cookies.txt")
        self.assertIn("ffmpeg_location", options)

    def test_all_provider_operations_translate_errors(self) -> None:
        provider = YtDlpProvider()
        operations = (
            lambda: provider.search("music"),
            lambda: provider.load_playlist("https://www.youtube.com/playlist?list=one"),
            lambda: provider.resolve_stream("https://youtu.be/one"),
        )
        for operation in operations:
            with (
                self.subTest(operation=operation),
                patch(
                    "musicplayer.application.providers.YoutubeDL",
                    side_effect=TimeoutError("timed out"),
                ),
                self.assertRaisesRegex(ProviderError, "YouTube took too long"),
            ):
                operation()

    def test_stream_resolution_rejects_missing_audio_url(self) -> None:
        with patch("musicplayer.application.providers.YoutubeDL") as youtube_dl:
            youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {}
            with self.assertRaisesRegex(ProviderError, "playable audio stream"):
                YtDlpProvider().resolve_stream("https://youtu.be/one")

    def test_default_registry_exposes_only_youtube(self) -> None:
        registry = ProviderRegistry()

        self.assertEqual(registry.get("youtube").name, "YouTube")
        self.assertTrue(is_youtube_url("https://www.youtube.com/watch?v=video"))
        self.assertTrue(is_youtube_url("https://youtu.be/video"))
        self.assertFalse(is_youtube_url("https://youtube.com.example.test/video"))

        with self.assertRaisesRegex(ProviderError, "not available"):
            registry.get("web")

        with self.assertRaisesRegex(ProviderError, "YouTube URL"):
            registry.search("youtube", "https://example.test/video")

    def test_uploaded_cookie_file_is_passed_to_yt_dlp_search(self) -> None:
        provider = YtDlpProvider(yt_dlp_options={"cookiefile": "/private/cookies.txt"})
        with patch("musicplayer.application.providers.YoutubeDL") as youtube_dl:
            youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
                "entries": []
            }
            provider.search("focus music")

        options = youtube_dl.call_args.args[0]
        self.assertEqual(options["cookiefile"], "/private/cookies.txt")
        self.assertIn("ffmpeg_location", options)

    def test_search_offset_requests_the_next_result_window(self) -> None:
        provider = YtDlpProvider()
        with patch("musicplayer.application.providers.YoutubeDL") as youtube_dl:
            extractor = youtube_dl.return_value.__enter__.return_value
            extractor.extract_info.return_value = {"entries": []}

            provider.search("focus music", limit=25, offset=24)

        options = youtube_dl.call_args.args[0]
        self.assertFalse(options["noplaylist"])
        self.assertEqual(options["playliststart"], 25)
        self.assertEqual(options["playlistend"], 49)
        extractor.extract_info.assert_called_once_with(
            "ytsearch49:focus music", download=False
        )

    def test_playlist_url_offset_requests_the_next_track_window(self) -> None:
        provider = YtDlpProvider()
        playlist_url = "https://www.youtube.com/playlist?list=playlist-id"
        with patch("musicplayer.application.providers.YoutubeDL") as youtube_dl:
            extractor = youtube_dl.return_value.__enter__.return_value
            extractor.extract_info.return_value = {
                "_type": "playlist",
                "entries": [],
            }

            provider.search(playlist_url, limit=25, offset=24)

        options = youtube_dl.call_args.args[0]
        self.assertFalse(options["noplaylist"])
        self.assertEqual(options["playliststart"], 25)
        self.assertEqual(options["playlistend"], 49)
        extractor.extract_info.assert_called_once_with(playlist_url, download=False)

    def test_playlist_import_loads_every_flat_entry_without_downloading(self) -> None:
        provider = YtDlpProvider()
        playlist_url = "https://www.youtube.com/playlist?list=playlist-id"
        with patch("musicplayer.application.providers.YoutubeDL") as youtube_dl:
            extractor = youtube_dl.return_value.__enter__.return_value
            extractor.extract_info.return_value = {
                "_type": "playlist",
                "title": "Imported mix",
                "entries": [
                    {"id": "one", "title": "First", "url": "one"},
                    {"id": "two", "title": "Second", "url": "two"},
                ],
            }

            playlist = provider.load_playlist(playlist_url)

        options = youtube_dl.call_args.args[0]
        self.assertTrue(options["skip_download"])
        self.assertFalse(options["noplaylist"])
        self.assertNotIn("playlistend", options)
        self.assertEqual(playlist.title, "Imported mix")
        self.assertEqual([track.id for track in playlist.tracks], ["one", "two"])

    def test_playlist_import_requires_a_specific_playlist_url(self) -> None:
        provider = YtDlpProvider()

        with self.assertRaisesRegex(ProviderError, "playlist URL"):
            provider.load_playlist("https://youtu.be/video-id")

    def test_search_retries_anonymously_when_cookie_file_cannot_load(self) -> None:
        provider = YtDlpProvider(yt_dlp_options={"cookiefile": "/private/cookies.txt"})
        with patch("musicplayer.application.providers.YoutubeDL") as youtube_dl:
            authenticated = youtube_dl.return_value.__enter__.return_value
            authenticated.extract_info.side_effect = [
                CookieLoadError("failed to load cookies"),
                {"entries": []},
            ]
            provider.search("focus music")

        self.assertEqual(youtube_dl.call_count, 2)
        self.assertIn("cookiefile", youtube_dl.call_args_list[0].args[0])
        self.assertNotIn("cookiefile", youtube_dl.call_args_list[1].args[0])

    def test_search_entries_map_to_typed_results(self) -> None:
        info = {
            "_type": "playlist",
            "id": "demo",
            "extractor": "youtube:search",
            "extractor_key": "YoutubeSearch",
            "original_url": "ytsearch10:demo",
            "entries": [
                {
                    "id": "video-id",
                    "title": "A song",
                    "channel": "A publisher channel",
                    "duration": 125,
                    "url": "video-id",
                    "thumbnails": [
                        {
                            "url": "//i.ytimg.test/small.jpg",
                            "width": 120,
                            "height": 90,
                        },
                        {
                            "url": "https://i.ytimg.test/large.jpg",
                            "width": 480,
                            "height": 360,
                        },
                    ],
                }
            ],
        }
        result = _results_from_info(info, "YouTube", limit=10)[0]
        self.assertEqual(result.title, "A song")
        self.assertEqual(result.uploader, "A publisher channel")
        self.assertEqual(result.url, "https://www.youtube.com/watch?v=video-id")
        self.assertEqual(result.thumbnail, "https://i.ytimg.test/large.jpg")
        self.assertFalse(result.is_playlist)

    def test_invalid_duration_metadata_does_not_break_result_mapping(self) -> None:
        info = {
            "id": "video-id",
            "title": "A song",
            "url": "https://www.youtube.com/watch?v=video-id",
            "duration": "unknown",
        }

        result = _results_from_info(info, "YouTube", limit=10)[0]

        self.assertEqual(result.duration, 0.0)

    def test_direct_thumbnail_is_preferred_over_thumbnail_candidates(self) -> None:
        info = {
            "id": "video-id",
            "title": "A song",
            "url": "https://example.test/watch",
            "thumbnail": "https://example.test/direct.jpg",
            "thumbnails": [
                {
                    "url": "https://example.test/candidate.jpg",
                    "width": 1920,
                    "height": 1080,
                }
            ],
        }

        result = _results_from_info(info, "YouTube", limit=10)[0]

        self.assertEqual(result.thumbnail, "https://example.test/direct.jpg")

    def test_explicit_playlist_loads_its_tracks(self) -> None:
        info = {
            "_type": "playlist",
            "id": "playlist-id",
            "title": "Focus playlist",
            "channel": "Playlist channel",
            "webpage_url": "https://www.youtube.com/playlist?list=playlist-id",
            "entries": [
                {"id": "one", "title": "First", "url": "one"},
                {"id": "two", "title": "Second", "url": "two"},
            ],
        }
        results = _results_from_info(info, "YouTube", limit=10)

        self.assertEqual([result.title for result in results], ["First", "Second"])
        self.assertEqual(
            [result.url for result in results],
            [
                "https://www.youtube.com/watch?v=one",
                "https://www.youtube.com/watch?v=two",
            ],
        )
        self.assertTrue(all(not result.is_playlist for result in results))
        self.assertTrue(
            all(result.playlist_title == "Focus playlist" for result in results)
        )
        self.assertTrue(
            all(result.uploader == "Playlist channel" for result in results)
        )

    def test_empty_explicit_playlist_loads_as_an_empty_result(self) -> None:
        info = {
            "_type": "playlist",
            "id": "empty-playlist",
            "title": "Empty playlist",
            "entries": [],
        }

        self.assertEqual(_results_from_info(info, "YouTube", limit=10), [])


if __name__ == "__main__":
    unittest.main()
