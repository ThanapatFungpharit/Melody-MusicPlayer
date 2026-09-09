"""Coalesced playback writes, independent of transport and provider workers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Lock

from musicplayer.core.concurrency import LazyBoundedExecutor

from .store import ApplicationStore


class PlaybackPersistence:
    def __init__(self, store: ApplicationStore, on_error: Callable[[str], None]):
        self.store = store
        self.on_error = on_error
        self._lock = Lock()
        self._worker = LazyBoundedExecutor(
            max_workers=1, max_pending=1, thread_name_prefix="melody-state"
        )
        self._pending: dict[str, object] = {}
        self._plays: dict[str, int] = {}
        self._running = False
        self._closed = False

    def save(self, **changes: object) -> None:
        with self._lock:
            if self._closed:
                return
            self._pending.update(changes)
            self._start_locked()

    def record_play(self, track_id: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._plays[track_id] = self._plays.get(track_id, 0) + 1
            self._start_locked()

    def _start_locked(self) -> None:
        if not self._running:
            self._running = True
            self._worker.submit(self._drain)

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._pending and not self._plays:
                    self._running = False
                    return
                changes, self._pending = self._pending, {}
                plays, self._plays = self._plays, {}
            try:
                self.store.save_playback_updates(changes, plays)
            except Exception:
                logging.getLogger(__name__).exception("Playback state write failed")
                # Do not retry indefinitely or interfere with native playback.
                # The next transport change will save a fresh session snapshot.
                self.on_error("Playback continues, but its session could not be saved.")

    def flush(self) -> None:
        """Worker-only barrier for shutdown tests and destructive data actions."""
        self._worker.submit(lambda: None).result()

    def close(self, *, wait: bool = False) -> None:
        with self._lock:
            self._closed = True
        # Accepted writes drain, including the last position. Never join on UI.
        self._worker.shutdown(wait=wait)
