"""Stable downloader failures and explicit user recovery actions."""

from __future__ import annotations

import errno
from dataclasses import dataclass
from enum import Enum


class DownloadErrorKind(str, Enum):
    NETWORK = "network"
    AUTHENTICATION = "authentication"
    UNAVAILABLE = "unavailable"
    EXTRACTOR = "extractor"
    RATE_LIMIT = "rate_limit"
    DISK = "disk"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DownloadFailure:
    kind: DownloadErrorKind
    message: str
    recovery: str


def classify_download_error(error: Exception | str) -> DownloadFailure:
    chain: list[BaseException] = []
    current = error if isinstance(error, Exception) else None
    while current is not None and all(current is not item for item in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__
    message = " ".join([str(error), *(str(item) for item in chain)]).casefold()
    names = {type(item).__name__ for item in chain}
    codes = {str(getattr(item, "status", getattr(item, "code", ""))) for item in chain}
    if "CookieLoadError" in names or any(
        word in message
        for word in (
            "cookie",
            "sign in",
            "login required",
            "authentication",
            "private video",
        )
    ):
        return DownloadFailure(
            DownloadErrorKind.AUTHENTICATION,
            "This media requires a valid session. Upload fresh Netscape cookies.txt in Settings, or remove cookies for public media.",
            "refresh_cookies",
        )
    if "429" in codes or any(
        word in message for word in ("429", "too many requests", "rate limit")
    ):
        return DownloadFailure(
            DownloadErrorKind.RATE_LIMIT,
            "The provider is limiting requests. Wait before retrying.",
            "retry_later",
        )
    if any(
        isinstance(item, OSError)
        and item.errno
        in {errno.ENOSPC, errno.EDQUOT, errno.EACCES, errno.EROFS, errno.EIO}
        for item in chain
    ) or any(
        word in message
        for word in (
            "no space left",
            "disk full",
            "permission denied",
            "read-only file system",
        )
    ):
        return DownloadFailure(
            DownloadErrorKind.DISK,
            "The file could not be written. Check free space and folder permissions, then retry.",
            "repair_storage",
        )
    if any(word in message for word in ("unsupported url", "unsupported scheme")):
        return DownloadFailure(
            DownloadErrorKind.UNSUPPORTED,
            "This URL is unsupported. Use a YouTube media URL.",
            "change_url",
        )
    if any(
        word in message
        for word in (
            "video unavailable",
            "not available",
            "removed",
            "deleted",
            "copyright",
            "geo restricted",
        )
    ):
        return DownloadFailure(
            DownloadErrorKind.UNAVAILABLE,
            "This media is unavailable. Choose another source.",
            "change_media",
        )
    if any(
        word in message
        for word in ("unable to extract", "extractor", "signature extraction", "nsig")
    ):
        return DownloadFailure(
            DownloadErrorKind.EXTRACTOR,
            "The provider's extractor failed. Restart Melody to check for a compatible downloader update, then retry.",
            "update_downloader",
        )
    if any(isinstance(item, (TimeoutError, ConnectionError)) for item in chain) or any(
        word in message
        for word in (
            "timed out",
            "network",
            "connection",
            "name resolution",
            "urlopen error",
            "http error 5",
        )
    ):
        return DownloadFailure(
            DownloadErrorKind.NETWORK,
            "The network request failed. Check your connection and retry.",
            "retry",
        )
    if "_DownloadCancelled" in names:
        return DownloadFailure(
            DownloadErrorKind.CANCELLED, "Download cancelled.", "none"
        )
    return DownloadFailure(
        DownloadErrorKind.UNKNOWN,
        "The download failed. Retry or try another source.",
        "retry",
    )
