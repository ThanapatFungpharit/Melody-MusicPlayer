"""Platform paths and bundled media-tool discovery.

This module is deliberately dependency-free within :mod:`musicplayer`.  Core
and application services both use it during import, so importing either layer
from here would invert the dependency direction and create initialization
cycles.
"""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class UnsupportedPlatformError(RuntimeError):
    """Raised when Melody has no bundled tools for the current runtime."""


class MediaBinaryError(RuntimeError):
    """Raised when a required bundled media program is unavailable."""


_PLATFORM_NAMES = {
    "win32": "windows",
    "windows": "windows",
    "cygwin": "windows",
    "linux": "linux",
    "linux2": "linux",
    "darwin": "macos",
    "macos": "macos",
    "android": "android",
}
_ARCHITECTURE_NAMES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "x64": "x86_64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "arm64-v8a": "arm64",
    "armeabi-v7a": "arm",
    "armv7l": "arm",
    "arm": "arm",
    "i686": "x86",
    "i386": "x86",
    "x86": "x86",
    "riscv64": "riscv64",
}


def current_platform(
    platform_name: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the normalized platform name used by bundled resources."""
    environment = os.environ if environ is None else environ
    detected = platform_name
    if detected is None:
        # CPython reports ``linux`` inside an Android Flet application.  Flet's
        # explicit runtime marker must therefore take precedence over
        # ``sys.platform``.
        detected = environment.get("FLET_PLATFORM") or sys.platform
    key = detected.strip().casefold()
    try:
        return _PLATFORM_NAMES[key]
    except KeyError:
        raise UnsupportedPlatformError(
            f"Unsupported platform: {detected!r}. "
            "Melody supports Windows, Linux, macOS, and Android."
        ) from None


def current_architecture(
    architecture: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the normalized CPU architecture used by bundled resources."""
    environment = os.environ if environ is None else environ
    detected = architecture
    if detected is None:
        detected = (
            environment.get("ANDROID_ABI")
            or environment.get("PROCESSOR_ARCHITECTURE")
            or platform.machine()
        )
    key = detected.strip().casefold()
    try:
        return _ARCHITECTURE_NAMES[key]
    except KeyError:
        raise UnsupportedPlatformError(
            f"Unsupported architecture: {detected!r}. "
            "Melody supports x86_64, arm64, arm (armeabi-v7a), x86, and riscv64."
        ) from None


@dataclass(frozen=True)
class MediaBinaryBundle:
    """Resolved FFmpeg and FFprobe programs for one target runtime."""

    platform: str
    architecture: str
    directory: Path
    ffmpeg: Path
    ffprobe: Path

    def yt_dlp_options(self) -> dict[str, str]:
        """Return yt-dlp's FFmpeg location option for this bundle.

        Desktop bundles use conventional executable names, so their containing
        directory is sufficient.  Android installs the programs under native
        library names; passing the exact ``libffmpeg.so`` path lets yt-dlp
        derive the matching ``libffprobe.so`` path.
        """
        location = self.ffmpeg if self.platform == "android" else self.directory
        return {"ffmpeg_location": str(location)}


def media_binary_bundle(
    *,
    platform_name: str | None = None,
    architecture: str | None = None,
    binary_root: str | Path | None = None,
    validate: bool = False,
    environ: Mapping[str, str] | None = None,
) -> MediaBinaryBundle:
    """Resolve Melody's packaged FFmpeg/FFprobe pair.

    Validation is opt-in so settings and service objects can be constructed
    before a desktop development bundle has been fetched.  Actual downloads
    still receive the bundle path and report a useful yt-dlp error if the
    installation is incomplete.
    """
    environment = os.environ if environ is None else environ
    normalized_platform = current_platform(platform_name, environ=environment)
    normalized_architecture = current_architecture(architecture, environ=environment)

    if normalized_platform == "android":
        native_directory = environment.get("ANDROID_NATIVE_LIBRARY_DIR")
        if native_directory:
            directory = Path(native_directory)
        elif binary_root is not None:
            directory = (
                Path(binary_root) / normalized_platform / normalized_architecture
            )
        else:
            raise MediaBinaryError(
                "Android's native library directory is unavailable. "
                "ANDROID_NATIVE_LIBRARY_DIR must identify the installed media tools."
            )
        ffmpeg = directory / "libffmpeg.so"
        ffprobe = directory / "libffprobe.so"
    else:
        root = (
            Path(binary_root)
            if binary_root is not None
            else Path(__file__).resolve().parent / "bin"
        )
        directory = root / normalized_platform / normalized_architecture
        suffix = ".exe" if normalized_platform == "windows" else ""
        ffmpeg = directory / f"ffmpeg{suffix}"
        ffprobe = directory / f"ffprobe{suffix}"

    bundle = MediaBinaryBundle(
        normalized_platform,
        normalized_architecture,
        directory,
        ffmpeg,
        ffprobe,
    )
    if validate:
        _validate_media_bundle(bundle)
    return bundle


def yt_dlp_binary_options() -> dict[str, str]:
    """Return the bundled media-tool options shared by every yt-dlp caller."""
    return media_binary_bundle().yt_dlp_options()


def application_data_directory(
    *,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | Path | None = None,
    create: bool = True,
) -> Path:
    """Return Melody's native per-user application-data directory."""
    environment = os.environ if environ is None else environ
    normalized_platform = current_platform(platform_name, environ=environment)
    home_directory = Path(home) if home is not None else Path.home()

    if normalized_platform == "windows":
        local_data = environment.get("LOCALAPPDATA")
        root = Path(local_data) if local_data else home_directory / "AppData" / "Local"
        result = root / "MelodyPlayer"
    elif normalized_platform == "macos":
        result = home_directory / "Library" / "Application Support" / "MelodyPlayer"
    elif normalized_platform == "android":
        app_data = environment.get("FLET_APP_STORAGE_DATA")
        result = Path(app_data) if app_data else home_directory / ".melody"
    else:
        xdg_data = environment.get("XDG_DATA_HOME")
        root = Path(xdg_data) if xdg_data else home_directory / ".local" / "share"
        result = root / "MelodyPlayer"

    if create:
        result.mkdir(parents=True, exist_ok=True)
    return result


def default_music_directory(
    *,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | Path | None = None,
) -> Path:
    """Return the default writable directory for downloaded audio."""
    environment = os.environ if environ is None else environ
    normalized_platform = current_platform(platform_name, environ=environment)
    if normalized_platform == "android":
        return (
            application_data_directory(
                platform_name=normalized_platform,
                environ=environment,
                home=home,
                create=False,
            )
            / "Music"
        )
    home_directory = Path(home) if home is not None else Path.home()
    return home_directory / "Music" / "Melody"


def _validate_media_bundle(bundle: MediaBinaryBundle) -> None:
    for program in (bundle.ffmpeg, bundle.ffprobe):
        if not program.is_file():
            raise MediaBinaryError(
                f"Bundled media program is missing: {program}. "
                "Fetch or reinstall Melody's FFmpeg bundle."
            )
        if bundle.platform != "windows" and not os.access(program, os.X_OK):
            raise MediaBinaryError(
                f"Bundled media program is not executable: {program}."
            )


__all__ = [
    "MediaBinaryBundle",
    "MediaBinaryError",
    "UnsupportedPlatformError",
    "application_data_directory",
    "current_architecture",
    "current_platform",
    "default_music_directory",
    "media_binary_bundle",
    "yt_dlp_binary_options",
]
