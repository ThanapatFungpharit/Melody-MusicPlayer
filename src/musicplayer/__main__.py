from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from .core.runtime_gate import (
    MEDIA_BINARY_PREPARATION,
    configure_runtime_preparation,
)
from .platform_runtime import (
    MediaBinaryBundle,
    MediaBinaryError,
    current_architecture,
    current_platform,
    media_binary_bundle,
)

logger = logging.getLogger(__name__)

_REQUIRED_AUDIO_ENCODERS = {
    "libmp3lame": "MP3",
    "aac": "M4A/AAC",
    "libopus": "Opus",
}


def _source_checkout_root() -> Path | None:
    """Return the project root when running from an editable source checkout."""
    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        if (parent / "pyproject.toml").is_file() and (
            parent / "tools" / "fetch_media_binaries.py"
        ).is_file():
            return parent
    return None


def validate_development_audio_encoders(bundle: MediaBinaryBundle) -> None:
    """Ensure the bundled FFmpeg can encode every format exposed in Settings."""
    try:
        result = subprocess.run(
            [str(bundle.ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MediaBinaryError(
            f"Could not inspect bundled FFmpeg audio encoders: {error}"
        ) from error

    if result.returncode != 0:
        details = result.stderr.strip() or f"exit code {result.returncode}"
        raise MediaBinaryError(
            f"Could not inspect bundled FFmpeg audio encoders: {details}"
        )

    encoders = {
        fields[1]
        for line in result.stdout.splitlines()
        if len(fields := line.split()) >= 2 and fields[0].startswith("A")
    }
    missing = {
        encoder: label
        for encoder, label in _REQUIRED_AUDIO_ENCODERS.items()
        if encoder not in encoders
    }
    if missing:
        formatted = ", ".join(
            f"{label} ({encoder})" for encoder, label in missing.items()
        )
        raise MediaBinaryError(
            "Bundled FFmpeg is missing audio encoders required by Settings: "
            f"{formatted}. Delete the cached media bundle and restart Melody "
            "to download the pinned codec-complete build."
        )


def prepare_development_media_binaries(
    project_root: Path | None = None,
) -> MediaBinaryBundle | None:
    """Fetch the verified desktop media bundle before a source-tree launch.

    Flet release builds stage their binaries through the platform build wrappers,
    and Android must load installed native libraries. This startup preparation is
    therefore deliberately limited to editable desktop source checkouts such as
    ``uv run musicplayer``.
    """
    root = project_root or _source_checkout_root()
    if root is None:
        return None

    platform_name = current_platform()
    if platform_name == "android":
        return None
    architecture = current_architecture()
    binary_root = root / "src" / "musicplayer" / "bin"

    root_string = str(root)
    added_to_path = root_string not in sys.path
    if added_to_path:
        sys.path.insert(0, root_string)
    try:
        from tools.fetch_media_binaries import fetch_bundle
    finally:
        if added_to_path:
            sys.path.remove(root_string)

    logger.info(
        "Preparing bundled media tools: platform=%s architecture=%s",
        platform_name,
        architecture,
    )
    fetch_bundle(platform_name, architecture, binary_root=binary_root)
    bundle = media_binary_bundle(binary_root=binary_root, validate=True)
    if platform_name == "windows":
        # The Windows manifest is pinned to a codec-complete build. Keep the
        # gate scoped to that manifest until the other desktop archives have
        # equivalent codec guarantees.
        validate_development_audio_encoders(bundle)
    logger.info(
        "Bundled media tools ready: ffmpeg=%s ffprobe=%s",
        bundle.ffmpeg,
        bundle.ffprobe,
    )
    return bundle


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # A source checkout may need to fetch its pinned media tools. Register the
    # operation now; the first download starts it, while launch stays local.
    configure_runtime_preparation(
        prepare_development_media_binaries,
        key=MEDIA_BINARY_PREPARATION,
    )

    import flet as ft

    from .app import main as app_main

    ft.run(app_main)


if __name__ == "__main__":
    main()
