"""Asynchronous audio downloading, independent of music-library storage."""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any
from uuid import UUID, uuid4

from musicplayer.core.concurrency import LazyBoundedExecutor
from musicplayer.core.storage import sync_directory

from .config import OPTS
from .errors import classify_download_error
from .models import DownloadProgress, DownloadResult, DownloadStatus, DownloadTask
from .receipts import file_digest, receipt_path, write_receipt

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[DownloadProgress], None]
CompletionCallback = Callable[[DownloadResult], None]


class _DownloadCancelled(Exception):
    """Internal control-flow exception used to stop a running yt-dlp job."""


@dataclass
class _Job:
    task: DownloadTask
    progress: DownloadProgress
    cancelled: Event
    on_progress: ProgressCallback | None
    on_complete: CompletionCallback | None
    future: Future[None] | None = None
    result: DownloadResult | None = None


class Downloader:
    """Download audio files in background threads.

    The service owns only download staging files. Callers decide whether and
    where successful files are stored, using the result supplied on completion.
    Its bounded pool is created by the first download, retires when idle, and
    rejects submissions above ``max_workers + max_pending`` without blocking.
    At most ``max_retained_jobs`` terminal task results remain addressable.
    """

    def __init__(
        self,
        output_dir: str | Path = "downloads",
        *,
        max_workers: int = 4,
        max_pending: int | None = None,
        max_retained_jobs: int = 250,
        audio_extensions: frozenset[str] | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._audio_extensions = frozenset(
            extension.casefold()
            for extension in (audio_extensions or frozenset({".mp3"}))
        )
        # Keep the proven defaults in ``core.downloader.config`` while allowing
        # the application layer to apply user-selected codec and quality
        # settings without duplicating the downloader implementation.
        self._options = {**OPTS, **(options or {})}
        if max_retained_jobs < 1:
            raise ValueError("max_retained_jobs must be at least 1")
        pending_limit = max_workers if max_pending is None else max_pending
        self._executor = LazyBoundedExecutor(
            max_workers=max_workers,
            max_pending=pending_limit,
            thread_name_prefix="music-download",
        )
        self._lock = Lock()
        self._jobs: dict[UUID, _Job] = {}
        self._terminal_jobs: deque[UUID] = deque()
        self._max_retained_jobs = max_retained_jobs
        self._reserved_names: set[Path] = set()

    def start(
        self,
        url: str,
        *,
        on_progress: ProgressCallback | None = None,
        on_complete: CompletionCallback | None = None,
    ) -> DownloadTask:
        """Start a download and return immediately with its task handle."""
        source = url.strip()
        if not source:
            raise ValueError("A download URL is required")

        task = DownloadTask(uuid4())
        progress = DownloadProgress(task, source, DownloadStatus.QUEUED)
        job = _Job(task, progress, Event(), on_progress, on_complete)
        with self._lock:
            self._jobs[task.id] = job
            try:
                job.future = self._executor.submit(self._run, task.id)
            except Exception:
                self._jobs.pop(task.id, None)
                raise
        self._notify_progress(job)
        return task

    def progress(self, task: DownloadTask | UUID) -> DownloadProgress:
        """Return the latest progress snapshot for ``task``."""
        with self._lock:
            return self._job(task).progress

    def result(self, task: DownloadTask | UUID) -> DownloadResult | None:
        """Return a terminal result, or ``None`` while the task is active."""
        with self._lock:
            return self._job(task).result

    def cancel(self, task: DownloadTask | UUID) -> bool:
        """Request cancellation. Returns ``False`` for a terminal task."""
        with self._lock:
            job = self._job(task)
            if job.result is not None:
                return False
            job.cancelled.set()
            cancelled_before_start = job.future is not None and job.future.cancel()
        if cancelled_before_start:
            self._finish(job, DownloadStatus.CANCELLED)
        return True

    def shutdown(self, *, wait: bool = True, cancel_pending: bool = False) -> None:
        """Stop accepting work and optionally cancel queued jobs."""
        if cancel_pending:
            with self._lock:
                tasks = tuple(
                    job.task for job in self._jobs.values() if job.result is None
                )
            for task in tasks:
                self.cancel(task)
        self._executor.shutdown(wait=wait, cancel_pending=cancel_pending)

    def _run(self, task_id: UUID) -> None:
        with self._lock:
            job = self._jobs[task_id]
        files: tuple[Path, ...] = ()
        try:
            self._raise_if_cancelled(job)
            self._update(job, status=DownloadStatus.DOWNLOADING)
            files = self._download_one(job)
            self._raise_if_cancelled(job)
        except _DownloadCancelled:
            self._remove_files(files)
            self._finish(job, DownloadStatus.CANCELLED)
        except Exception as error:
            logger.exception("Download failed: url=%s", job.progress.url)
            self._remove_files(files)
            self._finish(job, DownloadStatus.FAILED, error=str(error), exception=error)
        else:
            self._finish(job, DownloadStatus.COMPLETED, files=files)

    def _download_one(self, job: _Job) -> tuple[Path, ...]:
        with tempfile.TemporaryDirectory(
            dir=self._output_dir, prefix=".melody-download-"
        ) as temporary_directory:
            temporary_path = Path(temporary_directory)
            self._run_yt_dlp(job, temporary_directory)
            self._raise_if_cancelled(job)
            files = self._move_downloaded_files(job, temporary_path)
        if not files:
            raise RuntimeError("The download did not produce an audio file")
        return tuple(files)

    def _run_yt_dlp(self, job: _Job, temporary_directory: str) -> None:
        # Imported only after the startup updater has selected and activated a
        # wheel.  Keeping this out of module import time protects all entry
        # points, including source-tree launches and test discovery.
        from yt_dlp import YoutubeDL

        options: dict[str, Any] = {
            **self._options,
            "paths": {"home": temporary_directory},
            "progress_hooks": [self._progress_hook(job)],
        }
        with YoutubeDL(options) as downloader:
            downloader.download([job.progress.url])

    def _move_downloaded_files(
        self, job: _Job, temporary_directory: Path
    ) -> list[Path]:
        files: list[Path] = []
        entries: list[dict[str, str]] = []
        try:
            for source in temporary_directory.iterdir():
                self._raise_if_cancelled(job)
                if source.suffix.casefold() not in self._audio_extensions:
                    continue
                file_status = source.stat()
                if not stat.S_ISREG(file_status.st_mode):
                    continue
                if file_status.st_size <= 0:
                    raise RuntimeError(
                        f"The download produced an empty audio file: {source.name}"
                    )
                destination = self._reserve_destination(source.name)
                try:
                    entries.append(
                        {"filename": destination.name, "sha256": file_digest(source)}
                    )
                    write_receipt(
                        self._output_dir, job.task.id, job.progress.url, entries
                    )
                    with source.open("rb+") as stream:
                        os.fsync(stream.fileno())
                    os.replace(source, destination)
                    sync_directory(destination.parent)
                finally:
                    # Once moved, destination.exists() provides collision
                    # protection. Failed moves must not leak reservations.
                    with self._lock:
                        self._reserved_names.discard(destination)
                files.append(destination)
        except Exception:
            self._remove_files(tuple(files))
            raise
        return files

    def _progress_hook(self, job: _Job) -> Callable[[dict[str, Any]], None]:
        def hook(data: dict[str, Any]) -> None:
            self._raise_if_cancelled(job)
            info = data.get("info_dict") or {}
            hook_status = data.get("status", "")
            status = (
                DownloadStatus.PROCESSING
                if hook_status == "finished"
                else DownloadStatus.DOWNLOADING
            )
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(
                data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            )
            self._update(
                job,
                status=status,
                progress=min(max(downloaded / total, 0.0), 1.0) if total else 0.0,
                filename=str(data.get("filename") or ""),
                downloaded_bytes=downloaded,
                total_bytes=total,
                media_title=str(info.get("title") or ""),
                uploader=str(
                    info.get("uploader")
                    or info.get("channel")
                    or info.get("creator")
                    or ""
                ),
                thumbnail=str(info.get("thumbnail") or ""),
            )

        return hook

    def _update(self, job: _Job, **changes: Any) -> None:
        with self._lock:
            job.progress = DownloadProgress(
                task=job.progress.task,
                url=job.progress.url,
                status=changes.get("status", job.progress.status),
                progress=changes.get("progress", job.progress.progress),
                filename=changes.get("filename", job.progress.filename),
                downloaded_bytes=changes.get(
                    "downloaded_bytes", job.progress.downloaded_bytes
                ),
                total_bytes=changes.get("total_bytes", job.progress.total_bytes),
                error=changes.get("error", job.progress.error),
                media_title=changes.get("media_title", job.progress.media_title),
                uploader=changes.get("uploader", job.progress.uploader),
                thumbnail=changes.get("thumbnail", job.progress.thumbnail),
            )
        self._notify_progress(job)

    def _finish(
        self,
        job: _Job,
        status: DownloadStatus,
        *,
        files: tuple[Path, ...] = (),
        error: str = "",
        exception: Exception | None = None,
    ) -> None:
        with self._lock:
            if job.result is not None:
                return
            # Cancellation and successful completion have one linearization
            # point. A request accepted first owns cleanup and terminal status.
            if job.cancelled.is_set():
                status = DownloadStatus.CANCELLED
                self._remove_files(files)
                files, error = (), ""
            if status is not DownloadStatus.COMPLETED:
                self._remove_files((receipt_path(self._output_dir, job.task.id),))
            job.progress = DownloadProgress(
                task=job.progress.task,
                url=job.progress.url,
                status=status,
                progress=1.0
                if status is DownloadStatus.COMPLETED
                else job.progress.progress,
                filename=job.progress.filename,
                downloaded_bytes=job.progress.downloaded_bytes,
                total_bytes=job.progress.total_bytes,
                error=error,
                media_title=job.progress.media_title,
                uploader=job.progress.uploader,
                thumbnail=job.progress.thumbnail,
            )
            job.result = DownloadResult(
                job.task,
                job.progress.url,
                status,
                files,
                error,
                classify_download_error(exception or error)
                if status is DownloadStatus.FAILED
                else None,
            )
            self._terminal_jobs.append(job.task.id)
            self._prune_terminal_jobs_locked()
        self._notify_progress(job)
        if job.on_complete:
            try:
                job.on_complete(job.result)
            except Exception:
                logger.exception(
                    "Download completion callback failed: url=%s", job.progress.url
                )

    def _reserve_destination(self, name: str) -> Path:
        requested = Path(name)
        counter = 1
        with self._lock:
            destination = self._output_dir / requested.name
            while destination.exists() or destination in self._reserved_names:
                counter += 1
                destination = (
                    self._output_dir / f"{requested.stem} ({counter}){requested.suffix}"
                )
            self._reserved_names.add(destination)
        return destination

    @staticmethod
    def _remove_files(files: tuple[Path, ...]) -> None:
        for path in files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Cleanup is best-effort. A locked output must not prevent the
                # task from publishing its real cancelled/failed outcome.
                logger.warning(
                    "Could not remove incomplete download output: path=%s",
                    path,
                    exc_info=True,
                )

    @staticmethod
    def _raise_if_cancelled(job: _Job) -> None:
        if job.cancelled.is_set():
            raise _DownloadCancelled

    def _job(self, task: DownloadTask | UUID) -> _Job:
        task_id = task.id if isinstance(task, DownloadTask) else task
        try:
            return self._jobs[task_id]
        except KeyError as error:
            raise ValueError("Unknown download task") from error

    def _prune_terminal_jobs_locked(self) -> None:
        """Bound retained result metadata without touching active jobs."""
        while len(self._terminal_jobs) > self._max_retained_jobs:
            self._jobs.pop(self._terminal_jobs.popleft(), None)

    @staticmethod
    def _notify_progress(job: _Job) -> None:
        if job.on_progress:
            try:
                job.on_progress(job.progress)
            except Exception:
                logger.exception(
                    "Download progress callback failed: url=%s", job.progress.url
                )


__all__ = [
    "DownloadProgress",
    "DownloadResult",
    "DownloadStatus",
    "DownloadTask",
    "Downloader",
]
