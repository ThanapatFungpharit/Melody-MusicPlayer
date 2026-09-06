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


class ProviderMappingTests(unittest.TestCase):
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
