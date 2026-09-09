from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from musicplayer.core.library import MusicManager
from musicplayer.core.library.models import Playlist, Track
from musicplayer.core.storage import atomic_copy

from .models import TrackDetails
from .store import ApplicationStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibraryReconciliation:
    missing_track_ids: tuple[str, ...]
    unregistered_files: tuple[str, ...]


class LibraryService:
    def __init__(self, manager: MusicManager, store: ApplicationStore) -> None:
        self.manager = manager
        self.store = store

    def details(self, track_id: UUID | str) -> TrackDetails:
        return self.store.track_details(str(track_id))

    def reconcile(self) -> LibraryReconciliation:
        """Repair stale secondary metadata and report ambiguous media safely.

        Missing track records remain available for source-based repair. Files
        without a download receipt are never adopted or deleted automatically.
        """
        with self.manager.mutation():
            tracks = self.manager.list_tracks()
            self.store.reconcile_track_references({str(track.id) for track in tracks})
            paths = {self.manager.track_path(track.id).resolve() for track in tracks}
            missing = tuple(
                str(track.id)
                for track in tracks
                if not self.manager.track_path(track.id).is_file()
            )
            unregistered = tuple(
                str(path)
                for path in self.manager.music_folder.iterdir()
                if path.is_file()
                and not path.name.startswith(".")
                and path.suffix.lower()
                in {".mp3", ".m4a", ".opus", ".wav", ".flac", ".ogg", ".aac"}
                and path.resolve() not in paths
            )
        if missing or unregistered:
            logger.warning(
                "Library reconciliation: %d missing tracks, %d unregistered files preserved",
                len(missing),
                len(unregistered),
            )
        return LibraryReconciliation(missing, unregistered)

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

    def favorite_tracks(self, track_ids: Iterable[UUID | str]) -> int:
        """Favorite several tracks with a single durable store update."""
        return self.store.favorite_tracks(str(track_id) for track_id in track_ids)

    def record_play(
        self, track_id: UUID | str, *, playback: dict[str, object] | None = None
    ) -> None:
        """Record playback activity and an optional session snapshot together."""
        self.store.record_play(str(track_id), played_at=time.time(), playback=playback)

    def delete_track(self, track_id: UUID | str, *, delete_file: bool = False) -> None:
        path = self.manager.track_path(track_id)
        self.manager.delete_track(track_id)
        self.store.remove_track_details(str(track_id))
        if delete_file and not self.manager.is_path_referenced(path):
            path.unlink(missing_ok=True)

    def delete_track_and_file(self, track_id: UUID | str) -> None:
        """Remove a library record and its owned media as one user action."""
        self.delete_track(track_id, delete_file=True)

    def rename_track(self, track_id: UUID | str, title: str) -> None:
        self.manager.rename_track(track_id, title)

    def import_local_file(self, source: str | Path) -> UUID:
        with self.manager.mutation():
            return self._import_local_file(source)

    def _import_local_file(self, source: str | Path) -> UUID:
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
        atomic_copy(source_path, destination)
        try:
            track_id = self.manager.add_track(
                destination, source=source_path.as_uri(), title=source_path.stem
            )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        try:
            self.store.save_track_details(
                str(track_id),
                TrackDetails(source_name="Local file"),
            )
        except Exception:
            # Registration and enriched metadata live in separate atomic
            # stores. Compensate if the second commit fails so callers never
            # receive an error while a half-imported track remains visible.
            self._rollback_local_import(track_id, destination)
            raise
        return track_id

    def _rollback_local_import(self, track_id: UUID, destination: Path) -> None:
        try:
            self.manager.delete_track(track_id)
        except Exception:
            # Keep the copied file when registration rollback fails; the
            # surviving track still references it and can be repaired.
            logger.exception(
                "Could not roll back a local track after metadata save failure: "
                "track_id=%s",
                track_id,
            )
            return
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Could not remove a copied file after import rollback: path=%s",
                destination,
                exc_info=True,
            )

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
        matching_tracks = self.manager.search_tracks(query)
        if sort == "recent":
            return matching_tracks
        tracks = list(matching_tracks)
        if sort == "title":
            tracks.sort(
                key=lambda item: (item.title or Path(item.filename).stem).casefold()
            )
        if sort == "oldest":
            tracks.sort(key=lambda item: item.added_at)
        return tuple(tracks)

    def playlists(self) -> tuple[Playlist, ...]:
        return self.manager.list_playlists()
