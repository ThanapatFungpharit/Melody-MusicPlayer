from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from musicplayer.core.downloader import (
    Downloader,
    DownloadProgress,
    DownloadResult,
    DownloadStatus,
    DownloadTask,
)
from musicplayer.core.library import MusicManager
from musicplayer.core.library.utils import source_key
from musicplayer.runtime_environment import public_error_message

from .models import AppSettings, DownloadRecord, SearchResult, TrackDetails
from .providers import is_youtube_url
from .store import ApplicationStore
from .yt_dlp_settings import yt_dlp_options

logger = logging.getLogger(__name__)
_DOWNLOAD_URL_PATTERN = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)


def extract_download_urls(value: str) -> tuple[str, ...]:
    """Extract unique HTTP(S) URLs from lines, bullets, or pasted prose."""
    urls: list[str] = []
    seen: set[str] = set()
    for match in _DOWNLOAD_URL_PATTERN.findall(value):
        url = match.rstrip(".,;")
        if url and url not in seen:
            urls.append(url)
            seen.add(url)
    return tuple(urls)


class DuplicateDownloadError(ValueError):
    pass


@dataclass(frozen=True)
class DownloadBatch:
    """A group of independently scheduled song downloads."""

    id: str
    tasks: tuple[DownloadTask, ...]
    size: int


class DownloadCoordinator:
    """Turns core downloader jobs into persistent, library-aware operations.

    Worker callbacks can arrive many times per second. UI notification and
    durable-history intervals cap the work caused by those callbacks while
    terminal state changes are still written and published immediately.
    """

    PROGRESS_NOTIFY_INTERVAL = 0.2
    PROGRESS_PERSIST_INTERVAL = 1.0
    MAX_RETAINED_RECORDS = 250

    def __init__(
        self,
        manager: MusicManager,
        store: ApplicationStore,
        settings: AppSettings,
        *,
        on_change: Callable[[DownloadRecord], None] | None = None,
    ) -> None:
        self.manager = manager
        self.store = store
        self.settings = settings
        self.on_change = on_change
        self._lock = threading.RLock()
        self._last_progress_notify: dict[str, float] = {}
        self._last_progress_persist: dict[str, float] = {}
        self._records = {record.id: record for record in store.downloads()}
        recovered = False
        for record in self._records.values():
            if record.status in {"queued", "downloading", "processing"}:
                recovered = True
                record.status = "failed"
                record.error = (
                    "The application closed before this download finished. Retry it."
                )
        if recovered:
            self._persist()
        self._downloader = Downloader(
            settings.download_directory,
            max_workers=settings.concurrent_downloads,
            audio_extensions=frozenset({f".{settings.audio_format.casefold()}"}),
            options=_download_options(settings),
        )

    def list(self) -> tuple[DownloadRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._records.values(),
                    key=lambda item: (item.created_at, -item.batch_position),
                    reverse=True,
                )
            )

    def is_source_active(self, url: str) -> bool:
        """Return whether an equivalent source is already being downloaded."""
        with self._lock:
            return self._active_record_for_source_locked(url) is not None

    def start(self, result: SearchResult) -> DownloadTask:
        if not result.url:
            raise ValueError("This result does not provide a downloadable URL.")
        if not result.is_playlist and self.manager.find_track_by_source(result.url):
            raise DuplicateDownloadError("This track is already in your library.")
        metadata = {
            "url": result.url,
            "title": result.title,
            "uploader": result.uploader,
            "thumbnail": result.thumbnail,
            "source": result.source,
            "kind": result.kind,
            "duration": result.duration,
        }
        return self._start(metadata)

    def start_url(self, url: str, *, title: str = "New download") -> DownloadTask:
        metadata = {
            "url": url.strip(),
            "title": title,
            "uploader": "",
            "thumbnail": "",
            "source": "YouTube",
            "kind": "track",
            "duration": 0.0,
            "resolve_metadata": True,
        }
        return self._start(metadata)

    def start_urls(self, urls: Iterable[str]) -> DownloadBatch:
        """Schedule a URL collection as one batch without coupling its failures.

        Every URL remains an independent downloader task, so the configured worker
        limit still controls concurrency and one terminal failure cannot cancel the
        remaining songs.
        """
        sources: list[str] = []
        seen: set[str] = set()
        for value in urls:
            source = str(value).strip()
            if source and source not in seen:
                sources.append(source)
                seen.add(source)
        if not sources:
            raise ValueError("At least one YouTube URL is required.")
        if any(not is_youtube_url(source) for source in sources):
            raise ValueError("Only YouTube URLs are supported.")

        batch_id = str(uuid4())
        batch_created_at = time.time()
        tasks: list[DownloadTask] = []
        for position, source in enumerate(sources, start=1):
            metadata: dict[str, object] = {
                "url": source,
                "title": f"Song {position} of {len(sources)}",
                "uploader": "",
                "thumbnail": "",
                "source": "YouTube",
                "kind": "track",
                "duration": 0.0,
                "resolve_metadata": True,
                "batch_id": batch_id,
                "batch_position": position,
                "batch_size": len(sources),
                "created_at": batch_created_at,
            }
            try:
                tasks.append(self._start(metadata))
            except Exception as error:
                # A scheduling error is recorded for this song, then the rest of
                # the batch is still submitted.
                logger.exception(
                    "Batch download could not be scheduled: url=%s", source
                )
                self._record_start_failure(metadata, error)
        return DownloadBatch(batch_id, tuple(tasks), len(sources))

    def _start(self, metadata: dict[str, object]) -> DownloadTask:
        url = str(metadata["url"])
        if not url:
            raise ValueError("A YouTube URL is required.")
        if not is_youtube_url(url):
            raise ValueError("Only YouTube URLs are supported.")

        def progress_callback(progress: DownloadProgress) -> None:
            self._handle_progress(progress, metadata)

        def complete_callback(result: DownloadResult) -> None:
            self._handle_complete(result, metadata)

        with self._lock:
            if self._active_record_for_source_locked(url):
                raise DuplicateDownloadError("This track is already downloading.")
            return self._downloader.start(
                url, on_progress=progress_callback, on_complete=complete_callback
            )

    def cancel(self, record_id: str) -> bool:
        return self._downloader.cancel(DownloadTask(_uuid(record_id)))

    def retry(self, record_id: str) -> DownloadTask:
        with self._lock:
            try:
                original = self._records[record_id]
            except KeyError as error:
                raise ValueError("Download history entry was not found.") from error
            metadata = {
                "url": original.url,
                "title": original.title,
                "uploader": original.uploader,
                "thumbnail": original.thumbnail,
                "source": original.source,
                "kind": original.kind,
                "duration": 0.0,
            }
        return self._start(metadata)

    def clear_finished(self) -> None:
        with self._lock:
            active = {"queued", "downloading", "processing"}
            self._records = {
                key: value
                for key, value in self._records.items()
                if value.status in active
            }
            self._persist()

    def shutdown(self) -> None:
        self._downloader.shutdown(wait=False, cancel_pending=True)

    def _handle_progress(
        self, progress: DownloadProgress, metadata: dict[str, object]
    ) -> None:
        with self._lock:
            record = self._record(progress.task, metadata)
            previous_status = record.status
            record.status = progress.status.value
            record.progress = progress.progress
            record.filename = Path(progress.filename).name if progress.filename else ""
            record.downloaded_bytes = progress.downloaded_bytes
            record.total_bytes = progress.total_bytes
            record.error = _friendly_download_error(progress.error)
            if metadata.get("resolve_metadata"):
                record.title = progress.media_title or record.title
                record.uploader = progress.uploader or record.uploader
                record.thumbnail = progress.thumbnail or record.thumbnail
            now = time.monotonic()
            status_changed = record.status != previous_status
            persisted_at = self._last_progress_persist.get(record.id, 0.0)
            notified_at = self._last_progress_notify.get(record.id, 0.0)
            should_persist = (
                status_changed or now - persisted_at >= self.PROGRESS_PERSIST_INTERVAL
            )
            should_notify = (
                status_changed or now - notified_at >= self.PROGRESS_NOTIFY_INTERVAL
            )
            if should_persist:
                self._persist()
                self._last_progress_persist[record.id] = now
            if should_notify:
                self._last_progress_notify[record.id] = now
        if should_notify:
            self._notify(record)

    def _handle_complete(
        self, result: DownloadResult, metadata: dict[str, object]
    ) -> None:
        with self._lock:
            record = self._record(result.task, metadata)
        if result.status is DownloadStatus.COMPLETED:
            imported: list[str] = []
            try:
                for path in result.files:
                    source = str(metadata["url"])
                    existing = self.manager.find_track_by_source(
                        source
                    ) or self.manager.find_track_by_content(path)
                    if existing is not None:
                        track_id = existing.id
                        existing_path = self.manager.track_path(existing.id)
                        if path.resolve() != existing_path.resolve():
                            path.unlink()
                        if not existing.source:
                            self.manager.update_track(existing.id, source=source)
                        logger.info(
                            "Downloaded duplicate reused existing library track: "
                            "source=%s track_id=%s",
                            source,
                            track_id,
                        )
                    else:
                        track_id = self.manager.add_track(
                            path,
                            source=str(metadata["url"]),
                            title=str(metadata.get("title") or path.stem),
                        )
                        details = TrackDetails(
                            uploader=record.uploader,
                            duration=float(str(metadata.get("duration") or 0)),
                            thumbnail=record.thumbnail,
                            source_name=str(metadata.get("source") or "YouTube"),
                        )
                        self.store.save_track_details(str(track_id), details)
                    imported.append(str(track_id))
            except Exception as error:
                logger.exception(
                    "Downloaded media could not be imported into the library"
                )
                record.status = "failed"
                record.error = f"Downloaded, but could not add to the library: {error}"
            else:
                record.status = "completed"
                record.progress = 1.0
                record.track_ids = imported
                record.completed_at = time.time()
        else:
            record.status = result.status.value
            record.error = _friendly_download_error(result.error)
        with self._lock:
            self._prune_records_locked()
            self._persist()
            self._last_progress_notify.pop(record.id, None)
            self._last_progress_persist.pop(record.id, None)
        self._notify(record)

    def _record(
        self, task: DownloadTask, metadata: dict[str, object]
    ) -> DownloadRecord:
        key = str(task.id)
        record = self._records.get(key)
        if record is None:
            record = DownloadRecord(
                id=key,
                url=str(metadata["url"]),
                title=str(metadata["title"]),
                uploader=str(metadata.get("uploader") or ""),
                thumbnail=str(metadata.get("thumbnail") or ""),
                source=str(metadata.get("source") or "YouTube"),
                kind=str(metadata.get("kind") or "track"),
                created_at=float(str(metadata.get("created_at") or time.time())),
                batch_id=str(metadata.get("batch_id") or ""),
                batch_position=int(str(metadata.get("batch_position") or 0)),
                batch_size=int(str(metadata.get("batch_size") or 0)),
            )
            self._records[key] = record
            self._prune_records_locked()
        return record

    def _record_start_failure(
        self, metadata: dict[str, object], error: Exception
    ) -> None:
        record = DownloadRecord(
            id=str(uuid4()),
            url=str(metadata["url"]),
            title=str(metadata["title"]),
            uploader=str(metadata.get("uploader") or ""),
            thumbnail=str(metadata.get("thumbnail") or ""),
            source=str(metadata.get("source") or "YouTube"),
            kind=str(metadata.get("kind") or "track"),
            status="failed",
            error=_friendly_download_error(str(error)),
            created_at=float(str(metadata.get("created_at") or time.time())),
            completed_at=time.time(),
            batch_id=str(metadata.get("batch_id") or ""),
            batch_position=int(str(metadata.get("batch_position") or 0)),
            batch_size=int(str(metadata.get("batch_size") or 0)),
        )
        with self._lock:
            self._records[record.id] = record
            self._prune_records_locked()
            self._persist()
        self._notify(record)

    def _persist(self) -> None:
        self.store.save_downloads(list(self._records.values()))

    def _prune_records_locked(self) -> None:
        """Retain bounded history while never evicting an active operation."""
        excess = len(self._records) - self.MAX_RETAINED_RECORDS
        if excess <= 0:
            return
        active = {"queued", "downloading", "processing"}
        finished = sorted(
            (
                record
                for record in self._records.values()
                if record.status not in active
            ),
            key=lambda record: record.created_at,
        )
        for record in finished[:excess]:
            self._records.pop(record.id, None)

    def _notify(self, record: DownloadRecord) -> None:
        if self.on_change:
            try:
                self.on_change(record)
            except Exception:
                # A disconnected window must never fail the download worker.
                logger.debug("Download listener failed", exc_info=True)

    def _active_record_for_source_locked(self, url: str) -> DownloadRecord | None:
        identity = source_key(url)
        active = {"queued", "downloading", "processing"}
        return next(
            (
                record
                for record in self._records.values()
                if record.status in active and source_key(record.url) == identity
            ),
            None,
        )


def _uuid(value: str):
    from uuid import UUID

    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError("Invalid download identifier.") from error


def _download_options(settings: AppSettings) -> dict[str, object]:
    quality = "0" if settings.audio_quality == "best" else settings.audio_quality
    return {
        "format": "bestaudio/best",
        "quiet": True,
        "noprogress": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": settings.audio_format,
                "preferredquality": quality,
            },
            {"key": "EmbedThumbnail"},
            {"key": "FFmpegMetadata"},
        ],
        **yt_dlp_options(settings),
    }


def _friendly_download_error(message: str) -> str:
    if not message:
        return ""
    lowered = message.casefold()
    if "encoder not found" in lowered or "unknown encoder" in lowered:
        return (
            "Melody's bundled FFmpeg is missing the selected audio encoder. "
            "Restart Melody to refresh its media tools, then retry."
        )
    if (
        "ffmpeg" in lowered and ("not found" in lowered or "not installed" in lowered)
    ) or "ffmpeg-location" in lowered:
        return (
            "Melody's bundled FFmpeg tools are unavailable. "
            "Reinstall the application and retry."
        )
    if "unsupported url" in lowered:
        return "Only YouTube URLs are supported."
    if "private" in lowered or "sign in" in lowered:
        return "This media is private or requires sign-in."
    if "failed to load cookies" in lowered or "cookies database" in lowered:
        return (
            "The uploaded cookie file could not be loaded. Upload a valid "
            "Netscape cookies.txt file in Settings, or remove it for anonymous access."
        )
    if "timed out" in lowered or "network" in lowered:
        return "The network request failed. Check your connection and retry."
    return public_error_message(message)
