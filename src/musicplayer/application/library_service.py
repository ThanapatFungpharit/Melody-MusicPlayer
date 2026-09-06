from __future__ import annotations

import shutil
import time
from pathlib import Path
from uuid import UUID

from musicplayer.core.library import MusicManager
from musicplayer.core.library.models import Playlist, Track

from .models import TrackDetails
from .store import ApplicationStore


class LibraryService:
    def __init__(self, manager: MusicManager, store: ApplicationStore) -> None:
        self.manager = manager
        self.store = store

    def details(self, track_id: UUID | str) -> TrackDetails:
        return self.store.track_details(str(track_id))

    def update_details(self, track_id: UUID | str, **changes: object) -> TrackDetails:
        details = self.details(track_id)
        for name, value in changes.items():
            if not hasattr(details, name):
                raise ValueError(f"Unknown track metadata field: {name}")
            setattr(details, name, value)
        self.store.save_track_details(str(track_id), details)
        return details

    def toggle_favorite(self, track_id: UUID | str) -> bool:
        details = self.details(track_id)
        details.favorite = not details.favorite
        self.store.save_track_details(str(track_id), details)
        return details.favorite

    def record_play(
        self, track_id: UUID | str, *, playback: dict[str, object] | None = None
    ) -> None:
        """Record playback activity and an optional session snapshot together."""
        self.store.record_play(str(track_id), played_at=time.time(), playback=playback)

    def delete_track(self, track_id: UUID | str, *, delete_file: bool = False) -> None:
        path = self.manager.track_path(track_id)
        self.manager.delete_track(track_id)
        self.store.remove_track_details(str(track_id))
        if delete_file:
            path.unlink(missing_ok=True)

    def rename_track(self, track_id: UUID | str, title: str) -> None:
        self.manager.rename_track(track_id, title)

    def import_local_file(self, source: str | Path) -> UUID:
        """Copy an existing audio file into the managed folder and register it."""
        source_path = Path(source).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        supported = {".mp3", ".m4a", ".opus", ".wav", ".flac", ".ogg", ".aac"}
        if source_path.suffix.casefold() not in supported:
            raise ValueError(
                f"Unsupported audio format: {source_path.suffix or 'unknown'}"
            )
        existing = self.manager.find_track_by_content(source_path)
        if existing:
            raise ValueError(
                f"“{existing.title or existing.filename}” is already in the library"
            )
        destination = self._available_destination(source_path.name)
        shutil.copy2(source_path, destination)
        try:
            track_id = self.manager.add_track(
                destination, source=source_path.as_uri(), title=source_path.stem
            )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        self.store.save_track_details(
            str(track_id),
            TrackDetails(source_name="Local file"),
        )
        return track_id

    def _available_destination(self, name: str) -> Path:
        requested = Path(name)
        destination = self.manager.music_folder / requested.name
        counter = 1
        while destination.exists():
            counter += 1
            destination = (
                self.manager.music_folder
                / f"{requested.stem} ({counter}){requested.suffix}"
            )
        return destination

    def tracks(self, *, query: str = "", sort: str = "recent") -> tuple[Track, ...]:
        tracks = list(self.manager.search_tracks(query))
        if sort == "title":
            tracks.sort(
                key=lambda item: (item.title or Path(item.filename).stem).casefold()
            )
        elif sort == "oldest":
            tracks.sort(key=lambda item: item.added_at)
        return tuple(tracks)

    def playlists(self) -> tuple[Playlist, ...]:
        return self.manager.list_playlists()
