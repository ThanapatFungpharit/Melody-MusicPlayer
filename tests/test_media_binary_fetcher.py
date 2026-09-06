from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.fetch_media_binaries import ARCHIVES, _extract_programs


class MediaBinaryFetcherTests(unittest.TestCase):
    def test_windows_archives_include_yt_dlp_audio_encoders(self) -> None:
        for architecture in ("x86_64", "arm64"):
            archive = ARCHIVES[("windows", architecture)]

            self.assertIn("BtbN/FFmpeg-Builds", archive.release_root)
            self.assertIn("gpl", archive.filename)
            self.assertEqual(len(archive.sha256), 64)

    def test_android_release_manifest_is_pinned(self) -> None:
        arm64 = ARCHIVES[("android", "arm64")]
        x86_64 = ARCHIVES[("android", "x86_64")]

        self.assertEqual(arm64.version, "build-264")
        self.assertEqual(x86_64.version, "build-264")
        self.assertEqual(len(arm64.sha256), 64)
        self.assertEqual(len(x86_64.sha256), 64)
        self.assertIn("android-arm64-v8a", arm64.filename)
        self.assertIn("android-x86_64", x86_64.filename)

    def test_nested_android_archive_extracts_both_programs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested_archive = root / "ffmpeg.tar.xz"
            with tarfile.open(nested_archive, mode="w:xz") as package:
                for name, contents in (
                    ("bin/ffmpeg", b"android-ffmpeg"),
                    ("bin/ffprobe", b"android-ffprobe"),
                ):
                    member = tarfile.TarInfo(name)
                    member.size = len(contents)
                    package.addfile(member, io.BytesIO(contents))

            outer_archive = root / "android.zip"
            with zipfile.ZipFile(outer_archive, mode="w") as package:
                package.write(nested_archive, "ffmpeg.tar.xz")

            extracted = _extract_programs(outer_archive, root / "output")

            self.assertEqual(extracted["ffmpeg"].read_bytes(), b"android-ffmpeg")
            self.assertEqual(extracted["ffprobe"].read_bytes(), b"android-ffprobe")


if __name__ == "__main__":
    unittest.main()
