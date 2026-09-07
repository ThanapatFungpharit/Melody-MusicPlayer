from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from musicplayer.core.downloader import (
    Downloader,
    DownloadProgress,
    DownloadResult,
    DownloadStatus,
    DownloadTask,
)
from musicplayer.core.library import MusicManager
from musicplayer.core.library.models import Track
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
    ACTIVE_STATUSES = frozenset({"queued", "downloading", "processing"})

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
        self._last_progress_notify = 0.0
        self._last_progress_persist = 0.0
        self._records = {record.id: record for record in store.downloads()}
        self._ordered_records: tuple[DownloadRecord, ...] | None = None
        self._active_records_by_source: dict[str, str] = {}
        recovered = False
        for record in self._records.values():
            if record.status in self.ACTIVE_STATUSES:
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
            if self._ordered_records is None:
                self._ordered_records = tuple(
                    sorted(
                        self._records.values(),
                        key=lambda item: (item.created_at, -item.batch_position),
                        reverse=True,
                    )
                )
            return self._ordered_records

    def get(self, record_id: str) -> DownloadRecord | None:
        """Return a history record by identifier without sorting all records."""
        with self._lock:
            return self._records.get(str(record_id))

    def active_count(self) -> int:
        """Return the number of downloads still owned by the core service."""
        with self._lock:
            return len(self._active_records_by_source)

    def is_source_active(self, url: str) -> bool:
        """Return whether an equivalent source is already being downloaded."""
        with self._lock:
            return self._active_record_for_source_locked(url) is not None

    def start(self, result: SearchResult) -> DownloadTask:
        if not result.url:
            raise ValueError("This result does not provide a downloadable URL.")
        if not result.is_playlist:
            existing = self.manager.find_track_by_source(result.url)
            if (
                existing is not None
                and self.manager.check_track_integrity(existing.id) is None
            ):
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
        return self._start(_url_metadata(url.strip(), title=title))

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
            if not source or source in seen:
                continue
            if not is_youtube_url(source):
                raise ValueError("Only YouTube URLs are supported.")
            sources.append(source)
            seen.add(source)
        if not sources:
            raise ValueError("At least one YouTube URL is required.")

        batch_id = str(uuid4())
        batch_created_at = time.time()
        tasks: list[DownloadTask] = []
        for position, source in enumerate(sources, start=1):
            metadata: dict[str, object] = {
                **_url_metadata(source, title=f"Song {position} of {len(sources)}"),
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
            self._records = {
                key: value
                for key, value in self._records.items()
                if value.status in self.ACTIVE_STATUSES
            }
            self._ordered_records = None
            self._persist()

    def shutdown(self) -> None:
        self._downloader.shutdown(wait=False, cancel_pending=True)

    def _handle_progress(
        self, progress: DownloadProgress, metadata: dict[str, object]
    ) -> None:
        with self._lock:
            record = self._record(progress.task, metadata)
            previous_status = record.status
            record.status = (
                DownloadStatus.PROCESSING.value
                if progress.status is DownloadStatus.COMPLETED
                else progress.status.value
            )
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
            should_persist = (
                status_changed
                or now - self._last_progress_persist >= self.PROGRESS_PERSIST_INTERVAL
            )
            should_notify = (
                status_changed
                or now - self._last_progress_notify >= self.PROGRESS_NOTIFY_INTERVAL
            )
            if should_persist:
                self._persist()
                self._last_progress_persist = now
            if should_notify:
                self._last_progress_notify = now
        if should_notify:
            self._notify(record)

    def _handle_complete(
        self, result: DownloadResult, metadata: dict[str, object]
    ) -> None:
        with self._lock:
            record = self._record(result.task, metadata)
            uploader = record.uploader
            thumbnail = record.thumbnail
        final_status = result.status.value
        final_error = _friendly_download_error(result.error)
        imported: list[str] | None = None
        stored_filename: str | None = None
        completed_at: float | None = None
        if result.status is DownloadStatus.COMPLETED:
            imported = []
            stored_paths: list[Path] = []
            try:
                for path in result.files:
                    track_id, stored_path = self._import_downloaded_file(
                        path, metadata, uploader=uploader, thumbnail=thumbnail
                    )
                    imported.append(str(track_id))
                    stored_paths.append(stored_path)
            except Exception as error:
                logger.exception(
                    "Downloaded media could not be imported into the library"
                )
                final_status = "failed"
                final_error = f"Downloaded, but could not add to the library: {error}"
                imported = None
            else:
                if stored_paths:
                    stored_filename = stored_paths[0].name
                completed_at = time.time()
        with self._lock:
            record.status = final_status
            record.error = final_error
            if imported is not None:
                assert completed_at is not None
                record.progress = 1.0
                record.track_ids = imported
                if stored_filename is not None:
                    record.filename = stored_filename
                record.completed_at = completed_at
            identity = source_key(record.url)
            if self._active_records_by_source.get(identity) == record.id:
                self._active_records_by_source.pop(identity, None)
            self._prune_records_locked()
            self._persist()
        self._notify(record)

    def _import_downloaded_file(
        self,
        path: Path,
        metadata: dict[str, object],
        *,
        uploader: str,
        thumbnail: str,
    ) -> tuple[UUID, Path]:
        """Register audio or reuse its library identity, preserving existing metadata."""
        source = str(metadata["url"])
        existing = self.manager.find_track_by_source(
            source
        ) or self.manager.find_track_by_content(path)
        if existing is not None:
            return existing.id, self._reuse_library_track(existing, path, source)

        track_id = self.manager.add_track(
            path,
            source=source,
            title=str(metadata.get("title") or path.stem),
        )
        details = TrackDetails(
            uploader=uploader,
            duration=float(str(metadata.get("duration") or 0)),
            thumbnail=thumbnail,
            source_name=str(metadata.get("source") or "YouTube"),
        )
        self.store.save_track_details(str(track_id), details)
        return track_id, self.manager.track_path(track_id)

    def _reuse_library_track(self, track: Track, path: Path, source: str) -> Path:
        """Keep intact audio, or repair its file without replacing the track record."""
        problem = self.manager.check_track_integrity(track.id)
        if problem is None:
            stored_path = self.manager.track_path(track.id)
            if path.resolve() != stored_path.resolve():
                path.unlink()
        else:
            self.manager.replace_track_file(track.id, path)
            stored_path = self.manager.track_path(track.id)
        if not track.source:
            self.manager.update_track(track.id, source=source)
        logger.info(
            "Downloaded media reused or repaired a library track: "
            "source=%s track_id=%s integrity=%s",
            source,
            track.id,
            problem.kind if problem else "intact",
        )
        return stored_path

    def _record(
        self, task: DownloadTask, metadata: dict[str, object]
    ) -> DownloadRecord:
        key = str(task.id)
        record = self._records.get(key)
        if record is None:
            record = _record_from_metadata(key, metadata)
            self._records[key] = record
            identity = source_key(record.url)
            if identity:
                self._active_records_by_source[identity] = record.id
            self._ordered_records = None
            self._prune_records_locked()
        return record

    def _record_start_failure(
        self, metadata: dict[str, object], error: Exception
    ) -> None:
        record = _record_from_metadata(str(uuid4()), metadata)
        record.status = "failed"
        record.error = _friendly_download_error(str(error))
        record.completed_at = time.time()
        with self._lock:
            self._records[record.id] = record
            self._ordered_records = None
            self._prune_records_locked()
            self._persist()
        self._notify(record)

    def _persist(self) -> None:
        self.store.save_downloads(self._records.values())

    def _prune_records_locked(self) -> None:
        """Retain bounded history while never evicting an active operation."""
        excess = len(self._records) - self.MAX_RETAINED_RECORDS
        if excess <= 0:
            return
        finished = sorted(
            (
                record
                for record in self._records.values()
                if record.status not in self.ACTIVE_STATUSES
            ),
            key=lambda record: record.created_at,
        )
        for record in finished[:excess]:
            self._records.pop(record.id, None)
        self._ordered_records = None

    def _notify(self, record: DownloadRecord) -> None:
        if self.on_change:
            try:
                self.on_change(record)
            except Exception:
                # A disconnected window must never fail the download worker.
                logger.debug("Download listener failed", exc_info=True)

    def _active_record_for_source_locked(self, url: str) -> DownloadRecord | None:
        identity = source_key(url)
        record_id = self._active_records_by_source.get(identity)
        return self._records.get(record_id) if record_id is not None else None


def _url_metadata(url: str, *, title: str) -> dict[str, object]:
    """Metadata shared by direct URLs and individual songs in a batch."""
    return {
        "url": url,
        "title": title,
        "uploader": "",
        "thumbnail": "",
        "source": "YouTube",
        "kind": "track",
        "duration": 0.0,
        "resolve_metadata": True,
    }


def _record_from_metadata(
    record_id: str, metadata: dict[str, object]
) -> DownloadRecord:
    return DownloadRecord(
        id=record_id,
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


def _uuid(value: str) -> UUID:
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
