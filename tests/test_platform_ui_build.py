"""Test the import graph and inventory that native UI bundles actually ship."""

from __future__ import annotations

import compileall
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import build_android, build_desktop
from tools.production_build import (
    TARGET_PRESENTATIONS,
    platform_flet_arguments,
    required_ui_modules,
)

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "pyproject.toml"

# Import the real application from the compiled staging tree. The fixture creates
# temporary storage and a silent backend, so every page can build without a GUI.
_COMPILED_SMOKE = """
import importlib.util
import sys
from pathlib import Path

import flet as ft

stage, tests, presentation, data = sys.argv[1:]
sys.path[:0] = [stage, tests]
from ui_support import TestPage, make_app
import musicplayer.app
assert Path(musicplayer.app.__file__).resolve().is_relative_to(Path(stage).resolve())
assert musicplayer.app.__file__.endswith('.pyc')
opposite = 'desktop' if presentation == 'mobile' else 'mobile'
assert importlib.util.find_spec('musicplayer.ui.' + opposite) is None
for populated in (False, True):
    platform = ft.PagePlatform.ANDROID if presentation == 'mobile' else ft.PagePlatform.WINDOWS
    app = make_app(TestPage(width=1040, height=700, platform=platform), Path(data) / str(populated),
                   mobile=presentation == 'mobile', populated=populated)
    try:
        assert app.presentation.value == presentation
        assert app.views.__name__ == 'musicplayer.ui.' + presentation
        for index in range(4):
            app.navigate(index)
        if populated:
            app._open_playlist(str(app.manager.list_playlists()[0].id))
        for panel in ('queue', 'downloads', 'settings'):
            app.shell.open_panel(panel)
            app._close_context_panel()
        if presentation == 'mobile':
            app.shell.open_panel('player')
            app._close_context_panel()
        assert not any(name.startswith('musicplayer.ui.' + opposite) for name in sys.modules)
    finally:
        app._close(None)
"""


class PlatformUIBuildTests(unittest.TestCase):
    def test_each_compiled_presentation_runs_without_the_other_package(self):
        for presentation in ("desktop", "mobile"):
            with (
                self.subTest(presentation=presentation),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                stage = root / "app"
                opposite = "mobile" if presentation == "desktop" else "desktop"
                shutil.copytree(
                    ROOT / "src",
                    stage,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", ".flet", "bin", opposite
                    ),
                )
                self.assertTrue(
                    compileall.compile_dir(stage, quiet=1, legacy=True, optimize=2)
                )
                for source in stage.rglob("*.py"):
                    source.unlink()
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        _COMPILED_SMOKE,
                        str(stage),
                        str(ROOT / "tests"),
                        presentation,
                        str(root / "data"),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=root,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exclusions_keep_shared_defaults_and_custom_cli_patterns(self):
        for target, presentation in TARGET_PRESENTATIONS.items():
            with self.subTest(target=target):
                arguments = platform_flet_arguments(
                    ["--yes", "--exclude", "custom-assets"],
                    target=target,
                    project_file=PROJECT,
                )
                opposite = "mobile" if presentation == "desktop" else "desktop"
                self.assertIn("custom-assets", arguments)
                self.assertIn(".flet", arguments)
                self.assertIn("__pycache__", arguments)
                self.assertIn("musicplayer/__main__.py", arguments)
                self.assertIn("musicplayer\\__main__.py", arguments)
                self.assertIn(f"musicplayer/ui/{opposite}", arguments)
                self.assertIn(f"musicplayer\\ui\\{opposite}", arguments)
                self.assertNotIn(f"musicplayer/ui/{presentation}", arguments)
                self.assertIn("--compile-app", arguments)

    def test_required_inventory_contains_shared_pages_and_platform_helpers(self):
        for presentation in ("desktop", "mobile"):
            modules = required_ui_modules(PROJECT, presentation)
            for name in (
                "shell",
                "player",
                "home",
                "search",
                "library",
                "playlists",
                "queue",
                "downloads",
                "settings",
                "controls",
            ):
                self.assertIn(f"ui/{presentation}/{name}.pyc", modules)
            self.assertIn("ui/pages/settings.pyc", modules)
            self.assertIn("ui/components/player_bar.pyc", modules)
            self.assertIn("ui/presentation.pyc", modules)
            self.assertNotIn(
                f"ui/{'mobile' if presentation == 'desktop' else 'desktop'}/shell.pyc",
                modules,
            )
        self.assertIn("ui/mobile/insets.pyc", required_ui_modules(PROJECT, "mobile"))

    def test_desktop_wrappers_pass_platform_exclusions_to_flet(self):
        for target in ("windows", "linux", "macos"):
            with (
                self.subTest(target=target),
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch(
                    "sys.argv", ["build_desktop.py", target, "--architecture", "x86_64"]
                ),
                mock.patch.object(build_desktop, "fetch_bundle"),
                mock.patch.object(build_desktop.subprocess, "run") as run,
            ):
                build_desktop.main()
            self.assertIn("musicplayer/ui/mobile", run.call_args.args[0])
            self.assertIn("musicplayer/__main__.py", run.call_args.args[0])

    def test_android_wrappers_pass_mobile_exclusions_to_flet(self):
        for target in ("apk", "aab"):
            with (
                self.subTest(target=target),
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("sys.argv", ["build_android.py", target, "--", "--yes"]),
                mock.patch.object(build_android, "validate_android_release_signing"),
                mock.patch.object(build_android, "fetch_bundle"),
                mock.patch.object(build_android, "_run_flet_build") as run,
            ):
                build_android.main()
            self.assertEqual(run.call_args.args[0], target)
            self.assertIn("musicplayer/ui/desktop", run.call_args.args[2])
            self.assertIn("musicplayer/__main__.py", run.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
