import errno
import tomllib
import unittest
from pathlib import Path

from packaging.requirements import Requirement

from musicplayer import __version__
from musicplayer.application.cookie_files import CookieFileError, validate_cookie_file
from musicplayer.core.downloader.errors import (
    DownloadErrorKind,
    classify_download_error,
)
from musicplayer.core.media import source_key
from tools.production_build import platform_flet_arguments
from tools.release_version import build_number, release_version, validate_tag

ROOT = Path(__file__).resolve().parents[2]


class ContractTests(unittest.TestCase):
    def test_versions_and_dependency_family_agree(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        plugin = tomllib.loads(
            (ROOT / "packages/flet_background_audio/pyproject.toml").read_text()
        )
        requirements = {
            r.name: str(r.specifier)
            for r in map(Requirement, project["project"]["dependencies"])
        }
        self.assertEqual(
            project["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "musicplayer.__version__",
        )
        self.assertNotIn("version", project["project"])
        self.assertEqual(release_version(), __version__)
        family = {requirements[name] for name in ("flet", "flet-audio")}
        self.assertEqual(len(family), 1)
        self.assertEqual(
            Requirement(plugin["project"]["dependencies"][0]).specifier,
            Requirement("flet" + requirements["flet"]).specifier,
        )
        self.assertEqual(
            requirements["flet-background-audio"], "==" + plugin["project"]["version"]
        )
        self.assertTrue(requirements["yt-dlp"].startswith("=="))
        lock = tomllib.loads((ROOT / "uv.lock").read_text())
        locked = {
            package["name"]: package.get("version") for package in lock["package"]
        }
        for name in ("flet", "flet-audio", "flet-desktop", "flet-cli"):
            self.assertEqual("==" + locked[name], requirements["flet"])
        self.assertEqual("==" + locked["yt-dlp"], requirements["yt-dlp"])

    def test_tag_and_native_metadata_use_authoritative_version(self):
        validate_tag(__version__, f"refs/tags/v{__version__}")
        with self.assertRaises(ValueError):
            validate_tag(__version__, "refs/tags/v999.0.0")
        args = platform_flet_arguments(
            [], target="windows", project_file=ROOT / "pyproject.toml"
        )
        self.assertEqual(args[args.index("--build-version") + 1], __version__)
        self.assertEqual(
            args[args.index("--build-number") + 1], str(build_number(__version__))
        )
        with self.assertRaises(ValueError):
            platform_flet_arguments(
                ["--build-version=9.0.0"],
                target="windows",
                project_file=ROOT / "pyproject.toml",
            )

    def test_youtube_variants_have_one_identity(self):
        for url in (
            "https://www.youtube.com/watch?v=abc123&t=20",
            "https://youtu.be/abc123?si=share",
            "https://m.youtube.com/shorts/abc123",
            "https://youtube.com/live/abc123",
            "https://youtube-nocookie.com/embed/abc123",
        ):
            self.assertEqual(source_key(url), "youtube:abc123")
        self.assertNotEqual(
            source_key("https://youtube.com.evil.test/watch?v=abc123"), "youtube:abc123"
        )

    def test_failure_categories_and_recovery(self):
        cases = [
            (TimeoutError(), DownloadErrorKind.NETWORK, "retry"),
            (OSError(errno.ENOSPC, "full"), DownloadErrorKind.DISK, "repair_storage"),
            ("HTTP Error 429", DownloadErrorKind.RATE_LIMIT, "retry_later"),
            ("expired cookies", DownloadErrorKind.AUTHENTICATION, "refresh_cookies"),
            ("unsupported URL", DownloadErrorKind.UNSUPPORTED, "change_url"),
            ("video unavailable", DownloadErrorKind.UNAVAILABLE, "change_media"),
            (
                "Unable to extract signature",
                DownloadErrorKind.EXTRACTOR,
                "update_downloader",
            ),
        ]
        for error, kind, action in cases:
            with self.subTest(kind=kind):
                failure = classify_download_error(error)
                self.assertEqual((failure.kind, failure.recovery), (kind, action))

    def test_completely_expired_cookies_are_rejected(self):
        with self.assertRaisesRegex(CookieFileError, "expired"):
            validate_cookie_file(
                b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1\tSID\tsecret\n"
            )
