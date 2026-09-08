"""Build Android APK/AAB artifacts with executable FFmpeg native payloads."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from tools.fetch_media_binaries import fetch_bundle
from tools.production_build import (
    platform_flet_arguments,
    production_process_environment,
    validate_android_release_signing,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANDROID_BINARY_ROOT = PROJECT_ROOT / "build" / "android-native"
ANDROID_ABIS = {
    "arm64": "arm64-v8a",
    "arm": "armeabi-v7a",
    "x86_64": "x86_64",
}


def stage_android_native_libraries(
    flutter_dir: Path,
    architectures: list[str],
    *,
    binary_root: Path = ANDROID_BINARY_ROOT,
) -> None:
    """Place CLI executables where Android installs executable native files.

    Android 10 and newer cannot execute programs from writable application
    storage. Naming the position-independent executables as native libraries
    and enabling legacy JNI packaging makes the installer place them in the
    app's read-only ``nativeLibraryDir`` instead.
    """
    jni_root = flutter_dir / "android" / "app" / "src" / "main" / "jniLibs"
    for architecture in architectures:
        abi = ANDROID_ABIS[architecture]
        source = binary_root / "android" / architecture
        destination = jni_root / abi
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "ffmpeg", destination / "libffmpeg.so")
        shutil.copy2(source / "ffprobe", destination / "libffprobe.so")


def _run_flet_build(
    target: str, architectures: list[str], flet_arguments: list[str]
) -> None:
    # ``flet-cli`` is present in the isolated release/build environment. Keep
    # these imports local so the application runtime and UI smoke tests do not
    # need the packaging tool installed.
    from flet_cli import cli
    from flet_cli.commands.build import Command

    original_run_flutter = Command.run_flutter

    def run_flutter(self: Any) -> None:
        stage_android_native_libraries(self.flutter_dir, architectures)
        original_run_flutter(self)

    Command.run_flutter = run_flutter
    previous_argv = sys.argv
    try:
        sys.argv = [
            "flet",
            "build",
            target,
            str(PROJECT_ROOT),
            "--arch",
            *(ANDROID_ABIS[item] for item in architectures),
            "--android-legacy-packaging",
            *flet_arguments,
        ]
        cli.main()
    finally:
        Command.run_flutter = original_run_flutter
        sys.argv = previous_argv


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("apk", "aab"))
    parser.add_argument(
        "--architecture",
        choices=tuple(ANDROID_ABIS),
        action="append",
        dest="architectures",
    )
    arguments, flet_arguments = parser.parse_known_args()
    architectures = arguments.architectures or ["arm64", "arm", "x86_64"]
    if flet_arguments[:1] == ["--"]:
        flet_arguments = flet_arguments[1:]
    flet_arguments = platform_flet_arguments(
        flet_arguments,
        target=arguments.target,
        project_file=PROJECT_ROOT / "pyproject.toml",
    )

    validate_android_release_signing(os.environ)
    with production_process_environment():
        for architecture in architectures:
            fetch_bundle("android", architecture, binary_root=ANDROID_BINARY_ROOT)
        _run_flet_build(arguments.target, architectures, flet_arguments)


if __name__ == "__main__":
    main()
