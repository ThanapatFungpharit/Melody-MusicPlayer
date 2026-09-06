from __future__ import annotations

import enum
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from musicplayer.platform_runtime import default_music_directory


class RepeatMode(str, enum.Enum):
    OFF = "off"
    TRACK = "track"
    PLAYLIST = "playlist"


@dataclass
class AppSettings:
    download_directory: str = field(
        default_factory=lambda: str(default_music_directory())
    )
    volume: int = 5
    previous_volume: int = 5
    muted: bool = False
    theme: str = "dark"
    audio_format: str = "mp3"
    audio_quality: str = "best"
    concurrent_downloads: int = 4
    cookie_file: str = ""
    resume_session: bool = True
    notifications: bool = True
    accent_color: str = "violet"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppSettings:
        defaults = cls()
        values = {
            name: data.get(name, getattr(defaults, name))
            for name in cls.__dataclass_fields__
        }
        values["volume"] = max(0, min(100, int(values["volume"])))
        values["previous_volume"] = max(1, min(100, int(values["previous_volume"])))
        values["concurrent_downloads"] = max(
            1, min(8, int(values["concurrent_downloads"]))
        )
        values["cookie_file"] = str(values["cookie_file"] or "")
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrackDetails:
    uploader: str = ""
    duration: float = 0.0
    thumbnail: str = ""
    favorite: bool = False
    play_count: int = 0
    last_played: float = 0.0
    source_name: str = "Local"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackDetails:
        defaults = cls()
        values = {
            name: data.get(name, getattr(defaults, name))
            for name in cls.__dataclass_fields__
        }
        # Before version 0.2, yt-dlp's uploader/channel value was incorrectly
        # stored as an artist and albums were accepted without a reliable source.
        values["uploader"] = data.get("uploader", data.get("artist", ""))
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchResult:
    id: str
    title: str
    uploader: str
    duration: float
    thumbnail: str
    url: str
    source: str
    provider_id: str = "youtube"
    kind: str = "track"
    playlist_title: str = ""
    entry_count: int = 0

    @property
    def is_playlist(self) -> bool:
        return self.kind == "playlist"


@dataclass
class DownloadRecord:
    id: str
    url: str
    title: str
    uploader: str = ""
    thumbnail: str = ""
    source: str = "YouTube"
    kind: str = "track"
    status: str = "queued"
    progress: float = 0.0
    filename: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    track_ids: list[str] = field(default_factory=list)
    batch_id: str = ""
    batch_position: int = 0
    batch_size: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DownloadRecord:
        defaults = cls(id="", url="", title="")
        values = {
            name: data.get(name, getattr(defaults, name))
            for name in cls.__dataclass_fields__
        }
        values["uploader"] = data.get("uploader", data.get("artist", ""))
        values["track_ids"] = list(values["track_ids"] or [])
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
