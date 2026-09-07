"""Prepare native dependencies and build a Flet desktop application."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from musicplayer.platform_runtime import current_architecture

if __package__:
    from tools.fetch_media_binaries import fetch_bundle
    from tools.production_build import (
        hardened_flet_arguments,
        production_build_environment,
    )
else:
    from fetch_media_binaries import fetch_bundle  # ty: ignore[unresolved-import]
    from production_build import (  # ty: ignore[unresolved-import]
        hardened_flet_arguments,
        production_build_environment,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("windows", "linux", "macos"))
    parser.add_argument(
        "--architecture",
        choices=("x86_64", "arm64"),
        action="append",
        dest="architectures",
    )
    arguments, flet_arguments = parser.parse_known_args()
    architectures = arguments.architectures or [current_architecture()]

    extra = flet_arguments
    if extra[:1] == ["--"]:
        extra = extra[1:]
    extra = hardened_flet_arguments(extra)
    build_environment = production_build_environment()

    for architecture in architectures:
        fetch_bundle(arguments.target, architecture)

    command = [
        sys.executable,
        "-m",
        "flet_cli.cli",
        "build",
        arguments.target,
        str(PROJECT_ROOT),
    ]
    if arguments.target == "macos":
        # Flet forwards these names to serious_python, whose Darwin package
        # targets are x86_64 and arm64 (not Flutter's x64 alias).
        command.extend(("--arch", *architectures))
        # Flutter defaults release builds to universal binaries. Match the
        # native app to the Python packages and FFmpeg resources prepared above.
        build_environment["FLUTTER_XCODE_ARCHS"] = " ".join(architectures)
    command.extend(extra)
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
        env=build_environment,
    )


if __name__ == "__main__":
    main()
