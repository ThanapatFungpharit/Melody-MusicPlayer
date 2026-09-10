from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from musicplayer.application.thumbnails import ThumbnailCache

JPEG = b"\xff\xd8\xff\xe0" + b"offline-artwork"


class ThumbnailCacheTests(unittest.TestCase):
    def test_downloaded_bytes_are_loaded_offline_without_an_opener(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = ThumbnailCache(directory)
            url = "https://i.ytimg.com/vi/song/hqdefault.jpg"

            self.assertTrue(cache.store_bytes(url, JPEG))
            self.assertEqual(cache.image_source(url), JPEG)
            self.assertTrue(cache.artwork_uri(url).startswith("file:"))

            reopened = ThumbnailCache(directory)
            self.assertEqual(reopened.image_source(url), JPEG)

    def test_invalid_or_non_remote_artwork_is_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = ThumbnailCache(directory)

            self.assertFalse(
                cache.store_bytes("https://example.test/not-image", b"not an image")
            )
            self.assertFalse(cache.store_bytes("file:///cover.jpg", JPEG))
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
