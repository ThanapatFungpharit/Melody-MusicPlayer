from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterable
from copy import deepcopy
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
            value = settings.to_dict()
            if self._data["settings"] == value:
                return
            previous = self._data["settings"]
            self._data["settings"] = value
            try:
                self._save_locked()
            except Exception:
                self._data["settings"] = previous
                raise

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
            key = str(track_id)
            value = details.to_dict()
            if self._data["track_details"].get(key) == value:
                return
            details_by_track = self._data["track_details"]
            previous = details_by_track.get(key, _MISSING)
            details_by_track[key] = value
            try:
                self._save_locked()
            except Exception:
                if previous is _MISSING:
                    details_by_track.pop(key, None)
                else:
                    details_by_track[key] = previous
                raise

    def remove_track_details(self, track_id: str) -> None:
        with self._lock:
            details_by_track = self._data["track_details"]
            key = str(track_id)
            if key not in details_by_track:
                return
            previous = details_by_track.pop(key)
            try:
                self._save_locked()
            except Exception:
                details_by_track[key] = previous
                raise

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
            previous = {key: self._data.get(key, _MISSING) for key in replacements}
            self._data.update(replacements)
            try:
                self._save_locked()
            except Exception:
                for key, value in previous.items():
                    if value is _MISSING:
                        self._data.pop(key, None)
                    else:
                        self._data[key] = value
                raise

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return deepcopy(self._data.get(key, default))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            copied = deepcopy(value)
            if self._data.get(key) == copied:
                return
            previous = self._data.get(key, _MISSING)
            self._data[key] = copied
            try:
                self._save_locked()
            except Exception:
                if previous is _MISSING:
                    self._data.pop(key, None)
                else:
                    self._data[key] = previous
                raise

    def add_recent(self, key: str, value: str, *, limit: int = 50) -> None:
        with self._lock:
            previous = self._data.get(key, _MISSING)
            if not self._add_recent_locked(key, value, limit=limit):
                return
            try:
                self._save_locked()
            except Exception:
                if previous is _MISSING:
                    self._data.pop(key, None)
                else:
                    self._data[key] = previous
                raise

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
            fields = ("recent_tracks", "playback_history", "playback")
            previous = {name: self._data.get(name, _MISSING) for name in fields}
            details_by_track = self._data["track_details"]
            previous_details = details_by_track.get(key, _MISSING)
            details = TrackDetails.from_dict(details_by_track.get(key, {}))
            details.play_count += 1
            details.last_played = float(played_at)
            details_by_track[key] = details.to_dict()
            self._add_recent_locked("recent_tracks", key, limit=20)
            self._add_recent_locked("playback_history", key, limit=100)
            if playback is not None:
                self._data["playback"] = deepcopy(playback)
            try:
                self._save_locked()
            except Exception:
                if previous_details is _MISSING:
                    details_by_track.pop(key, None)
                else:
                    details_by_track[key] = previous_details
                for name, value in previous.items():
                    if value is _MISSING:
                        self._data.pop(name, None)
                    else:
                        self._data[name] = value
                raise

    def downloads(self) -> list[DownloadRecord]:
        with self._lock:
            return [
                DownloadRecord.from_dict(item)
                for item in self._data.get("downloads", [])
            ]

    def save_downloads(self, records: Iterable[DownloadRecord]) -> None:
        value = [record.to_dict() for record in records]
        if len(value) > 250:
            value = value[-250:]
        with self._lock:
            if self._data.get("downloads") == value:
                return
            previous = self._data.get("downloads", _MISSING)
            self._data["downloads"] = value
            try:
                self._save_locked()
            except Exception:
                if previous is _MISSING:
                    self._data.pop("downloads", None)
                else:
                    self._data["downloads"] = previous
                raise

    def favorite_tracks(self, track_ids: Iterable[str]) -> int:
        """Mark several tracks as favorites with one atomic state write."""
        changed = 0
        with self._lock:
            previous = self._data["track_details"]
            details_by_track = previous
            for track_id in track_ids:
                key = str(track_id)
                details = TrackDetails.from_dict(details_by_track.get(key, {}))
                if details.favorite:
                    continue
                if details_by_track is previous:
                    details_by_track = dict(previous)
                details.favorite = True
                details_by_track[key] = details.to_dict()
                changed += 1
            if changed:
                self._data["track_details"] = details_by_track
                try:
                    self._save_locked()
                except Exception:
                    self._data["track_details"] = previous
                    raise
        return changed

    def _add_recent_locked(self, key: str, value: str, *, limit: int) -> bool:
        items = self._data.get(key, [])
        recent: list[str] = []
        if limit > 0:
            recent.append(value)
            for item in items:
                if item != value:
                    recent.append(item)
                    if len(recent) >= limit:
                        break
        if recent == items:
            return False
        self._data[key] = recent
        return True

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
