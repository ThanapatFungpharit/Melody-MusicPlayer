"""Small, bounded worker pools for blocking application I/O.

The standard :class:`~concurrent.futures.ThreadPoolExecutor` creates worker
threads lazily, but its submission queue is unbounded and its threads remain
allocated until explicit shutdown. ``LazyBoundedExecutor`` adds admission
control and retires the pool whenever its final task completes.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import BoundedSemaphore, RLock
from typing import Any, TypeVar

ResultT = TypeVar("ResultT")


class WorkerQueueFull(RuntimeError):
    """Raised when a bounded executor cannot accept another task immediately."""


class LazyBoundedExecutor:
    """Run blocking callables in a bounded pool that exists only while busy.

    ``max_workers`` limits simultaneous calls and ``max_pending`` limits calls
    waiting for a worker. Submission never blocks: overload is reported with
    :class:`WorkerQueueFull`, allowing an interactive caller to apply explicit
    backpressure rather than growing an unbounded queue.

    The underlying pool is instantiated by the first accepted submission and
    shut down after the last accepted future completes. A later submission can
    create a fresh pool, so idle instances retain only their lock/semaphore.
    """

    def __init__(
        self,
        *,
        max_workers: int,
        max_pending: int = 0,
        thread_name_prefix: str = "bounded-worker",
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if max_pending < 0:
            raise ValueError("max_pending cannot be negative")
        self._max_workers = int(max_workers)
        self._capacity = self._max_workers + int(max_pending)
        self._thread_name_prefix = thread_name_prefix
        self._permits = BoundedSemaphore(self._capacity)
        self._lock = RLock()
        self._executor: ThreadPoolExecutor | None = None
        self._in_flight = 0
        self._shutdown = False

    @property
    def in_flight(self) -> int:
        """Return the number of running and queued tasks."""
        with self._lock:
            return self._in_flight

    @property
    def is_active(self) -> bool:
        """Return whether an underlying pool is currently allocated."""
        with self._lock:
            return self._executor is not None

    def submit(
        self, function: Callable[..., ResultT], /, *args: Any, **kwargs: Any
    ) -> Future[ResultT]:
        """Submit without blocking, rejecting work beyond the configured cap."""
        if not self._permits.acquire(blocking=False):
            raise WorkerQueueFull(
                f"Background work limit reached ({self._capacity} tasks); retry shortly."
            )

        executor_to_retire: ThreadPoolExecutor | None = None
        try:
            with self._lock:
                if self._shutdown:
                    raise RuntimeError("Executor has been shut down")
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=self._max_workers,
                        thread_name_prefix=self._thread_name_prefix,
                    )
                executor = self._executor
                self._in_flight += 1
                try:
                    future = executor.submit(function, *args, **kwargs)
                except Exception:
                    self._in_flight -= 1
                    if self._in_flight == 0:
                        self._executor = None
                        executor_to_retire = executor
                    raise
                future.add_done_callback(self._task_finished)
                return future
        except Exception:
            self._permits.release()
            if executor_to_retire is not None:
                executor_to_retire.shutdown(wait=False, cancel_futures=True)
            raise

    def shutdown(self, *, wait: bool = True, cancel_pending: bool = False) -> None:
        """Reject new submissions and release any currently allocated pool."""
        with self._lock:
            self._shutdown = True
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=cancel_pending)

    def _task_finished(self, _: Future[Any]) -> None:
        executor_to_retire: ThreadPoolExecutor | None = None
        with self._lock:
            self._in_flight -= 1
            if self._in_flight == 0 and not self._shutdown:
                executor_to_retire = self._executor
                self._executor = None
        self._permits.release()
        if executor_to_retire is not None:
            # wait=False is safe from a worker callback and places a sentinel
            # that lets every idle worker exit without blocking completion.
            executor_to_retire.shutdown(wait=False, cancel_futures=False)
