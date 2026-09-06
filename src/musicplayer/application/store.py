from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import AppSettings, DownloadRecord, TrackDetails


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
        if not self.path.exists() or self.path.stat().st_size == 0:
            return self._defaults()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
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
            value = settings.to_dict()
            if self._data["settings"] == value:
                return
            self._data["settings"] = value
            self._save_locked()

    def track_details(self, track_id: str) -> TrackDetails:
        with self._lock:
            raw = self._data["track_details"].get(str(track_id), {})
            # TrackDetails owns the values it receives; no mutable value is
            # shared with the store by this conversion.
            return TrackDetails.from_dict(raw)

    def save_track_details(self, track_id: str, details: TrackDetails) -> None:
        with self._lock:
            key = str(track_id)
            value = details.to_dict()
            if self._data["track_details"].get(key) == value:
                return
            self._data["track_details"][key] = value
            self._save_locked()

    def remove_track_details(self, track_id: str) -> None:
        with self._lock:
            self._data["track_details"].pop(str(track_id), None)
            self._save_locked()

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
            if all(self._data.get(key) == value for key, value in replacements.items()):
                return
            self._data.update(replacements)
            self._save_locked()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return deepcopy(self._data.get(key, default))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            copied = deepcopy(value)
            if self._data.get(key) == copied:
                return
            self._data[key] = copied
            self._save_locked()

    def add_recent(self, key: str, value: str, *, limit: int = 50) -> None:
        with self._lock:
            self._add_recent_locked(key, value, limit=limit)
            self._save_locked()

    def record_play(
        self,
        track_id: str,
        *,
        played_at: float,
        playback: dict[str, Any] | None = None,
    ) -> None:
        """Update play metadata, histories, and optional session state atomically.

        Playback used to save the details, recent list, and history list
        separately. Combining them with the current queue snapshot keeps the
        same crash-safe replace semantics while reducing a track start to one
        serialization, fsync, and file replacement.
        """
        key = str(track_id)
        with self._lock:
            details = TrackDetails.from_dict(self._data["track_details"].get(key, {}))
            details.play_count += 1
            details.last_played = float(played_at)
            self._data["track_details"][key] = details.to_dict()
            self._add_recent_locked("recent_tracks", key, limit=20)
            self._add_recent_locked("playback_history", key, limit=100)
            if playback is not None:
                self._data["playback"] = deepcopy(playback)
            self._save_locked()

    def downloads(self) -> list[DownloadRecord]:
        return [DownloadRecord.from_dict(item) for item in self.get("downloads", [])]

    def save_downloads(self, records: list[DownloadRecord]) -> None:
        self.set("downloads", [record.to_dict() for record in records[-250:]])

    def _add_recent_locked(self, key: str, value: str, *, limit: int) -> None:
        items = self._data.get(key, [])
        # Histories are intentionally small and ordered. Avoid allocating an
        # unbounded intermediate list before applying the configured cap.
        recent = [value]
        recent.extend(item for item in items if item != value)
        self._data[key] = recent[:limit]

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
