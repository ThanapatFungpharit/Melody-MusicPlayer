"""Small, immutable values exposed by the download service."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .errors import DownloadFailure


class DownloadStatus(str, enum.Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class DownloadTask:
    """An opaque identifier for one download started by :class:`Downloader`."""

    id: UUID


@dataclass(frozen=True)
class DownloadProgress:
    """A point-in-time, read-only view of a download."""

    task: DownloadTask
    url: str
    status: DownloadStatus
    progress: float = 0.0
    filename: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error: str = ""
    media_title: str = ""
    uploader: str = ""
    thumbnail: str = ""


@dataclass(frozen=True)
class DownloadResult:
    """The terminal outcome of one download.

    ``artwork`` is read back from the completed media container. It is exposed
    so presentation layers can build disposable caches without making those
    caches the authoritative copy of the cover.
    """

    task: DownloadTask
    url: str
    status: DownloadStatus
    files: tuple[Path, ...] = ()
    error: str = ""
    failure: DownloadFailure | None = None
    artwork: bytes = b""
