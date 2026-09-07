from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import deque
from collections.abc import Iterable
from copy import deepcopy
from itertools import chain, islice
from pathlib import Path
from typing import Any

from .models import AppSettings, DownloadRecord, TrackDetails

_MISSING = object()


class ApplicationStore:
    """Thread-safe JSON persistence for state outside MusicManager's domain.

    MusicManager continues to own tracks and playlists. This store contains UI
    preferences, playback/session state, enriched metadata, favorites, history,
    and durable download history.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            contents = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._defaults()
        except OSError as error:
            raise ValueError(f"Could not read application settings: {error}") from error
        if not contents:
            return self._defaults()
        try:
            raw = json.loads(contents)
        except json.JSONDecodeError as error:
            raise ValueError(f"Could not read application settings: {error}") from error
        if not isinstance(raw, dict):
            raise TypeError("Application settings must contain a JSON object")
        merged = self._defaults()
        merged.update(raw)
        return self._migrate(merged)

    @staticmethod
    def _migrate(data: dict[str, Any]) -> dict[str, Any]:
        if int(data.get("version", 1)) < 2:
            for details in data.get("track_details", {}).values():
                if not isinstance(details, dict):
                    continue
                if "uploader" not in details and "artist" in details:
                    details["uploader"] = details["artist"]
                details.pop("artist", None)
                details.pop("album", None)
            for record in data.get("downloads", []):
                if not isinstance(record, dict):
                    continue
                if "uploader" not in record and "artist" in record:
                    record["uploader"] = record["artist"]
                record.pop("artist", None)
            data["version"] = 2
        if int(data.get("version", 1)) < 3:
            settings = data.get("settings")
            if isinstance(settings, dict):
                # Browser database extraction was removed in version 3. A user
                # must explicitly upload a cookie file before authenticated
                # yt-dlp requests are enabled again.
                settings.pop("use_browser_cookies", None)
                settings.pop("cookie_browser", None)
                settings.pop("cookie_profile", None)
                settings.setdefault("cookie_file", "")
            data["version"] = 3
        return data

    @staticmethod
    def _defaults() -> dict[str, Any]:
        return {
            "version": 3,
            "settings": AppSettings().to_dict(),
            "track_details": {},
            "recent_tracks": [],
            "playback_history": [],
            "search_history": [],
            "downloads": [],
            "playback": {
                "queue": [],
                "current_index": -1,
                "position_ms": 0,
                "shuffle": False,
                "repeat": "off",
            },
        }

    @property
    def settings(self) -> AppSettings:
        with self._lock:
            # from_dict constructs a new settings object and only reads the
            # stored primitives, so copying the mapping first is redundant.
            return AppSettings.from_dict(self._data["settings"])

    def save_settings(self, settings: AppSettings) -> None:
        with self._lock:
            self._commit_locked({"settings": settings.to_dict()})

    def track_details(self, track_id: str) -> TrackDetails:
        with self._lock:
            raw = self._data["track_details"].get(str(track_id), {})
            # TrackDetails owns the values it receives; no mutable value is
            # shared with the store by this conversion.
            return TrackDetails.from_dict(raw)

    def favorite_track_ids(self) -> frozenset[str]:
        """Return favorite identifiers with one lock acquisition and no aliases."""
        with self._lock:
            return frozenset(
                str(track_id)
                for track_id, details in self._data["track_details"].items()
                if isinstance(details, dict) and bool(details.get("favorite"))
            )

    def save_track_details(self, track_id: str, details: TrackDetails) -> None:
        with self._lock:
            self._commit_locked({}, track_details={str(track_id): details.to_dict()})

    def remove_track_details(self, track_id: str) -> None:
        with self._lock:
            self._commit_locked({}, track_details={str(track_id): _MISSING})

    def clear_library_data(self) -> None:
        """Clear state that refers to library tracks, preserving other app data."""
        with self._lock:
            playback = self._data.get("playback", {})
            if isinstance(playback, dict):
                cleared_playback = {
                    **playback,
                    "queue": [],
                    "current_index": -1,
                    "position_ms": 0,
                }
            else:
                cleared_playback = self._defaults()["playback"]
            replacements = {
                "track_details": {},
                "recent_tracks": [],
                "playback_history": [],
                "playback": cleared_playback,
            }
            self._commit_locked(replacements)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return deepcopy(self._data.get(key, default))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._commit_locked({key: deepcopy(value)})

    def add_recent(self, key: str, value: str, *, limit: int = 50) -> None:
        with self._lock:
            self._commit_locked(
                {key: self._recent_items_locked(key, value, limit=limit)}
            )

    def record_play(
        self,
        track_id: str,
        *,
        played_at: float,
        playback: dict[str, Any] | None = None,
    ) -> None:
        """Update play metadata, histories, and optional session state atomically."""
        key = str(track_id)
        with self._lock:
            details = TrackDetails.from_dict(self._data["track_details"].get(key, {}))
            details.play_count += 1
            details.last_played = float(played_at)
            changes = {
                "recent_tracks": self._recent_items_locked(
                    "recent_tracks", key, limit=20
                ),
                "playback_history": self._recent_items_locked(
                    "playback_history", key, limit=100
                ),
            }
            if playback is not None:
                changes["playback"] = deepcopy(playback)
            self._commit_locked(changes, track_details={key: details.to_dict()})

    def downloads(self) -> list[DownloadRecord]:
        with self._lock:
            return [
                DownloadRecord.from_dict(item)
                for item in self._data.get("downloads", [])
            ]

    def save_downloads(self, records: Iterable[DownloadRecord]) -> None:
        value = [record.to_dict() for record in deque(records, maxlen=250)]
        with self._lock:
            self._commit_locked({"downloads": value})

    def favorite_tracks(self, track_ids: Iterable[str]) -> int:
        """Mark several tracks as favorites with one atomic state write."""
        with self._lock:
            changes = {}
            for key in dict.fromkeys(str(track_id) for track_id in track_ids):
                details = TrackDetails.from_dict(
                    self._data["track_details"].get(key, {})
                )
                if details.favorite:
                    continue
                details.favorite = True
                changes[key] = details.to_dict()
            self._commit_locked({}, track_details=changes)
            return len(changes)

    def _recent_items_locked(self, key: str, value: str, *, limit: int) -> list[str]:
        remaining = (item for item in self._data.get(key, []) if item != value)
        return list(islice(chain((value,), remaining), max(0, limit)))

    def _commit_locked(
        self,
        changes: dict[str, Any],
        *,
        track_details: dict[str, Any] | None = None,
    ) -> None:
        """Persist replacements once, restoring changed entries on failure.

        Callers hold the lock and supply owned values, never in-place mutations.
        Snapshot only touched entries so updating a track needs no library copy.
        _MISSING represents deletion as well as an absent previous value.
        """
        updates = [(self._data, changes)]
        if track_details:
            updates.append((self._data["track_details"], track_details))
        previous = []
        for target, replacements in updates:
            for key, value in replacements.items():
                old_value = target.get(key, _MISSING)
                if old_value == value:
                    continue
                previous.append((target, key, old_value))
                if value is _MISSING:
                    target.pop(key, None)
                else:
                    target[key] = value
        if not previous:
            return
        try:
            self._save_locked()
        except Exception:
            for target, key, value in reversed(previous):
                if value is _MISSING:
                    target.pop(key, None)
                else:
                    target[key] = value
            raise

    def _save_locked(self) -> None:
        # Compact JSON materially reduces bytes encoded and flushed for hot
        # state updates. Human readability is provided through typed APIs and
        # documentation rather than whitespace in this private state file.
        payload = json.dumps(self._data, ensure_ascii=False, separators=(",", ":"))
        fd, temporary_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=".melody-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
