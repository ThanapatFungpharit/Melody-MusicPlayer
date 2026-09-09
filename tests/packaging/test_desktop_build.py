from __future__ import annotations

import os
import unittest
from unittest import mock

from tools import build_desktop


class DesktopBuildTests(unittest.TestCase):
    def test_macos_architectures_match_python_native_app_and_media_tools(self) -> None:
        # serious_python's Darwin targets use x86_64, while Flutter otherwise
        # defaults a release app to universal. Both must match the media bundle.
        for architectures in (["x86_64"], ["arm64"], ["x86_64", "arm64"]):
            argv = ["build_desktop.py", "macos"]
            for architecture in architectures:
                argv.extend(["--architecture", architecture])
            argv.extend(["--", "--yes"])
            with (
                self.subTest(architectures=architectures),
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("sys.argv", argv),
                mock.patch.object(build_desktop, "fetch_bundle") as fetch,
                mock.patch.object(build_desktop.subprocess, "run") as run,
            ):
                build_desktop.main()

            self.assertEqual(
                fetch.call_args_list,
                [mock.call("macos", architecture) for architecture in architectures],
            )
            run.assert_called_once()
            command = run.call_args.args[0]
            start = command.index("--arch") + 1
            self.assertEqual(command[start : start + len(architectures)], architectures)
            self.assertNotIn("x64", command)
            self.assertEqual(
                run.call_args.kwargs["env"]["FLUTTER_XCODE_ARCHS"],
                " ".join(architectures),
            )
            self.assertTrue(run.call_args.kwargs["check"])
            self.assertIn("--yes", command)
            self.assertIn("--compile-packages", command)

    def test_macos_default_architecture_overrides_inherited_xcode_architecture(
        self,
    ) -> None:
        with (
            mock.patch.dict(
                os.environ, {"FLUTTER_XCODE_ARCHS": "x86_64 arm64"}, clear=True
            ),
            mock.patch("sys.argv", ["build_desktop.py", "macos", "--", "--yes"]),
            mock.patch.object(
                build_desktop, "current_architecture", return_value="arm64"
            ),
            mock.patch.object(build_desktop, "fetch_bundle") as fetch,
            mock.patch.object(build_desktop.subprocess, "run") as run,
        ):
            build_desktop.main()
            self.assertEqual(os.environ["FLUTTER_XCODE_ARCHS"], "x86_64 arm64")

        fetch.assert_called_once_with("macos", "arm64")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--arch") + 1], "arm64")
        self.assertEqual(run.call_args.kwargs["env"]["FLUTTER_XCODE_ARCHS"], "arm64")

    def test_other_desktop_targets_do_not_receive_macos_settings(self) -> None:
        for target in ("windows", "linux"):
            with (
                self.subTest(target=target),
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch(
                    "sys.argv",
                    ["build_desktop.py", target, "--architecture", "x86_64"],
                ),
                mock.patch.object(build_desktop, "fetch_bundle") as fetch,
                mock.patch.object(build_desktop.subprocess, "run") as run,
            ):
                build_desktop.main()

            fetch.assert_called_once_with(target, "x86_64")
            self.assertNotIn("--arch", run.call_args.args[0])
            self.assertNotIn("FLUTTER_XCODE_ARCHS", run.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
