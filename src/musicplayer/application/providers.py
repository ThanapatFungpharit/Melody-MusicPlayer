from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import parse_qs, urlsplit

from yt_dlp import CookieLoadError, YoutubeDL

from musicplayer.platform_runtime import yt_dlp_binary_options
from musicplayer.runtime_environment import public_error_message

from .models import AppSettings, SearchResult
from .yt_dlp_settings import yt_dlp_options

logger = logging.getLogger(__name__)

YOUTUBE_PROVIDER_ID = "youtube"
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemotePlaylist:
    title: str
    tracks: tuple[SearchResult, ...]


def _is_url(value: str) -> bool:
    return urlsplit(value.strip()).scheme.casefold() in {"http", "https"}


def is_youtube_url(value: str) -> bool:
    """Return whether *value* is an HTTP(S) URL hosted by YouTube."""
    parsed = urlsplit(value.strip())
    return (
        parsed.scheme.casefold() in {"http", "https"}
        and (parsed.hostname or "").casefold() in _YOUTUBE_HOSTS
    )


def is_youtube_playlist_url(value: str) -> bool:
    """Return whether *value* identifies a specific YouTube playlist."""
    parsed = urlsplit(value.strip())
    return is_youtube_url(value) and bool(parse_qs(parsed.query).get("list"))


@dataclass(frozen=True)
class YtDlpProvider:
    """Search/metadata adapter; downloads remain owned by core.Downloader."""

    id: str = YOUTUBE_PROVIDER_ID
    name: str = "YouTube"
    yt_dlp_options: dict[str, Any] | None = None

    def accepts(self, query: str) -> bool:
        if not _is_url(query):
            return True
        return is_youtube_url(query)

    def search(
        self, query: str, *, limit: int = 20, offset: int = 0
    ) -> list[SearchResult]:
        term = query.strip()
        if not term:
            return []
        result_limit = max(1, int(limit))
        result_offset = max(0, int(offset))
        is_url = _is_url(term)
        target = term if is_url else f"ytsearch{result_offset + result_limit}:{term}"
        options: dict[str, Any] = {
            **yt_dlp_binary_options(),
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": False,
            "extract_flat": "in_playlist",
            "playliststart": result_offset + 1,
            "playlistend": result_offset + result_limit,
            "socket_timeout": 12,
            **(self.yt_dlp_options or {}),
        }
        try:
            info = _extract_info(target, options)
        except Exception as error:
            raise ProviderError(_friendly_provider_error(error)) from error
        return _results_from_info(info, self.name, limit=result_limit)

    def resolve_stream(self, url: str) -> str:
        return _resolve_stream(url, self.yt_dlp_options)

    def load_playlist(self, url: str) -> RemotePlaylist:
        source = url.strip()
        if not is_youtube_playlist_url(source):
            raise ProviderError("Paste a complete YouTube playlist URL.")
        options: dict[str, Any] = {
            **yt_dlp_binary_options(),
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": False,
            "extract_flat": "in_playlist",
            "socket_timeout": 12,
            **(self.yt_dlp_options or {}),
        }
        try:
            info = _extract_info(source, options)
        except Exception as error:
            raise ProviderError(_friendly_provider_error(error)) from error
        if not info or not _is_explicit_playlist_info(info):
            raise ProviderError("That URL did not resolve to a YouTube playlist.")

        entries = [entry for entry in (info.get("entries") or []) if entry]
        normalized_info = {**info, "entries": entries}
        tracks = tuple(
            result
            for result in _results_from_info(
                normalized_info,
                self.name,
                limit=max(1, len(entries)),
            )
            if result.url and is_youtube_url(result.url)
        )
        return RemotePlaylist(
            title=str(info.get("title") or "Imported YouTube playlist").strip(),
            tracks=tracks,
        )


class ProviderRegistry:
    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
    ) -> None:
        shared_options = yt_dlp_options(settings) if settings else {}
        self._youtube = YtDlpProvider(yt_dlp_options=shared_options)

    def get(self, provider_id: str) -> YtDlpProvider:
        if provider_id != YOUTUBE_PROVIDER_ID:
            raise ProviderError(f"Source '{provider_id}' is not available.")
        return self._youtube

    def search(
        self,
        provider_id: str,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[SearchResult]:
        provider = self.get(provider_id)
        if not provider.accepts(query):
            raise ProviderError("Enter a search term or paste a YouTube URL.")
        return [
            replace(result, provider_id=provider_id)
            for result in provider.search(query, limit=limit, offset=offset)
        ]

    def load_playlist(self, url: str) -> RemotePlaylist:
        return self._youtube.load_playlist(url)


def _is_search_container(info: dict[str, Any]) -> bool:
    extractor_identity = " ".join(
        str(info.get(key) or "") for key in ("extractor", "extractor_key")
    ).casefold()
    original_url = str(info.get("original_url") or "").casefold()
    return "search" in extractor_identity or original_url.startswith("ytsearch")


def _is_explicit_playlist_info(info: dict[str, Any]) -> bool:
    return info.get("_type") == "playlist" and not _is_search_container(info)


def _results_from_info(
    info: dict[str, Any] | None, source: str, *, limit: int
) -> list[SearchResult]:
    if not info:
        return []
    entries = info.get("entries")
    is_explicit_playlist = _is_explicit_playlist_info(info)
    playlist_title = (
        str(info.get("title") or "Playlist") if is_explicit_playlist else ""
    )
    playlist_uploader = str(info.get("uploader") or info.get("channel") or "")
    raw_entries = entries if is_explicit_playlist else (entries or [info])
    results: list[SearchResult] = []
    for entry in raw_entries or []:
        if not entry or len(results) >= limit:
            continue
        url = str(entry.get("webpage_url") or entry.get("url") or "")
        if url and not _is_url(url) and source.casefold().startswith("youtube"):
            url = f"https://www.youtube.com/watch?v={url}"
        results.append(
            SearchResult(
                id=str(entry.get("id") or url),
                title=str(entry.get("title") or "Untitled track"),
                uploader=str(
                    entry.get("uploader")
                    or entry.get("channel")
                    or playlist_uploader
                    or "Unknown uploader"
                ),
                duration=_number(entry.get("duration")),
                thumbnail=_thumbnail_url(entry),
                url=url,
                source=str(entry.get("extractor_key") or source),
                playlist_title=str(entry.get("playlist_title") or playlist_title),
            )
        )
    return results


def _thumbnail_url(info: dict[str, Any]) -> str:
    """Choose a usable thumbnail from full or flat yt-dlp metadata."""
    direct = str(info.get("thumbnail") or "").strip()
    if direct:
        return _absolute_thumbnail_url(direct)

    candidates = [
        candidate
        for candidate in info.get("thumbnails") or []
        if isinstance(candidate, dict) and str(candidate.get("url") or "").strip()
    ]
    if not candidates:
        return ""

    def quality(candidate: dict[str, Any]) -> tuple[float, float]:
        width = _number(candidate.get("width"))
        height = _number(candidate.get("height"))
        preference = _number(candidate.get("preference"))
        return preference, width * height

    best = max(candidates, key=quality)
    return _absolute_thumbnail_url(str(best["url"]).strip())


def _absolute_thumbnail_url(url: str) -> str:
    return f"https:{url}" if url.startswith("//") else url


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _friendly_provider_error(error: Exception) -> str:
    message = str(error).strip()
    lowered = message.casefold()
    if "unsupported url" in lowered:
        return "Only YouTube URLs are supported."
    if "timed out" in lowered or "timeout" in lowered:
        return "YouTube took too long to respond. Check your connection and retry."
    if "unable to download" in lowered or "network" in lowered:
        return "YouTube could not be reached. Check your connection and retry."
    if "private" in lowered or "sign in" in lowered:
        return "This media is private or requires sign-in."
    if "failed to load cookies" in lowered or "cookies database" in lowered:
        return (
            "The uploaded cookie file could not be loaded. Upload a valid "
            "Netscape cookies.txt file in Settings, or remove it for anonymous access."
        )
    return public_error_message(message or "YouTube could not be searched.")


def _resolve_stream(url: str, yt_dlp_options: dict[str, Any] | None = None) -> str:
    options: dict[str, Any] = {
        **yt_dlp_binary_options(),
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "bestaudio/best",
        "noplaylist": True,
        "socket_timeout": 12,
        **(yt_dlp_options or {}),
    }
    try:
        info = _extract_info(url, options)
    except Exception as error:
        raise ProviderError(_friendly_provider_error(error)) from error
    if info and info.get("entries"):
        info = next((entry for entry in info["entries"] if entry), None)
    stream = str((info or {}).get("url") or "")
    if not stream:
        raise ProviderError("This result does not expose a playable audio stream.")
    return stream


def _extract_info(target: str, options: dict[str, Any]) -> dict[str, Any] | None:
    try:
        with YoutubeDL(options) as downloader:
            return downloader.extract_info(target, download=False)
    except CookieLoadError:
        if "cookiefile" not in options:
            raise
        logger.warning("Cookie file could not be loaded; retrying yt-dlp anonymously")
        fallback = {key: value for key, value in options.items() if key != "cookiefile"}
        with YoutubeDL(fallback) as downloader:
            return downloader.extract_info(target, download=False)
