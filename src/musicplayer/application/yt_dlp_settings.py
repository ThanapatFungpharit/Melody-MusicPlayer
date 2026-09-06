from __future__ import annotations

from pathlib import Path
from typing import Any

from musicplayer.platform_runtime import yt_dlp_binary_options

from .models import AppSettings


def cookie_options(settings: AppSettings) -> dict[str, Any]:
    """Return yt-dlp options for a manually uploaded cookie file.

    The upload workflow validates and copies the file into app-private storage,
    so this path works identically on desktop and Android and never asks yt-dlp
    to inspect a browser database.
    """
    if not settings.cookie_file:
        return {}
    cookie_file = Path(settings.cookie_file).expanduser()
    if not cookie_file.is_file():
        return {}
    return {"cookiefile": str(cookie_file)}


def yt_dlp_options(settings: AppSettings) -> dict[str, Any]:
    """Return all shared yt-dlp runtime options for Melody.

    Keeping cookie-file and native-binary configuration here prevents
    search, preview, and download code paths from drifting apart.
    """
    return {**yt_dlp_binary_options(), **cookie_options(settings)}
