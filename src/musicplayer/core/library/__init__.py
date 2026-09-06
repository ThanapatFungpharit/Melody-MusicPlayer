from __future__ import annotations

import logging
import os
import stat
import struct
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

from .config import HEADER, MAGIC, SHA256_BYTES, TRACK_FIXED, U32, UUID_ONLY, VERSION
from .models import IntegrityProblem, Playlist, Track
from .utils import _as_uuid, _pack_utf8_string, _read_utf8_string, _sha256, source_key

logger = logging.getLogger(__name__)


class MusicManager:
    def __init__(self, metadata_path: str | Path, music_folder: str | Path) -> None:
        logger.debug(
            "Initializing MusicManager: metadata_path=%s music_folder=%s",
            metadata_path,
            music_folder,
        )
        self.metadata_path = Path(metadata_path)
        self.music_folder = Path(music_folder)
        try:
            self.music_folder.mkdir(parents=True, exist_ok=True)
            self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception(
                "Failed to create MusicManager storage directories: metadata_path=%s music_folder=%s",
                self.metadata_path,
                self.music_folder,
            )
            raise

        self._music_folder = self.music_folder.resolve()
        self._lock = threading.RLock()
        self._tracks: dict[UUID, Track] = {}
        self._playlists: dict[UUID, Playlist] = {}
        self._source_index: dict[str, dict[UUID, None]] = {}
        self._content_index: dict[str, dict[UUID, None]] = {}
        self._newest_track_ids: tuple[UUID, ...] | None = None
        try:
            self._load()
        except Exception:
            logger.exception(
                "MusicManager initialization failed while loading metadata: path=%s",
                self.metadata_path,
            )
            raise
        logger.info(
            "MusicManager initialized: metadata_path=%s music_folder=%s tracks=%d playlists=%d",
            self.metadata_path,
            self.music_folder,
            len(self._tracks),
            len(self._playlists),
        )

    # -- persistence -------------------------------------------------
    def _load(self) -> None:
        logger.debug("Loading music metadata: path=%s", self.metadata_path)
        try:
            data = self.metadata_path.read_bytes()
        except FileNotFoundError:
            logger.info(
                "No existing music metadata to load: path=%s", self.metadata_path
            )
            return
        except OSError:
            logger.exception(
                "Failed to read music metadata file: path=%s", self.metadata_path
            )
            raise
        if not data:
            logger.info(
                "No existing music metadata to load: path=%s (empty file)",
                self.metadata_path,
            )
            return
        logger.debug(
            "Read music metadata: path=%s byte_count=%d", self.metadata_path, len(data)
        )
        version, track_count = self._read_metadataHEADER(data)
        logger.info(
            "Parsing music metadata: path=%s version=%d track_count=%d",
            self.metadata_path,
            version,
            track_count,
        )
        try:
            tracks, playlists, offset = self._parse_metadata_records(
                data, version, track_count
            )
        except (struct.error, UnicodeDecodeError, ValueError) as exc:
            logger.exception(
                "Invalid music metadata while parsing: path=%s", self.metadata_path
            )
            raise ValueError(
                f"{self.metadata_path} contains invalid music metadata"
            ) from exc

        if offset != len(data):
            logger.error(
                "Music metadata has trailing bytes: path=%s parsed_offset=%d total_bytes=%d",
                self.metadata_path,
                offset,
                len(data),
            )
            raise ValueError(f"{self.metadata_path} contains trailing music metadata")

        self._discard_missing_playlist_tracks(playlists, tracks)

        self._tracks = tracks
        self._playlists = playlists
        logger.info(
            "Music metadata loaded: path=%s tracks=%d playlists=%d",
            self.metadata_path,
            len(tracks),
            len(playlists),
        )
        if version < VERSION:
            logger.info(
                "Upgrading legacy music metadata by capturing file hashes: path=%s version=%d targetVERSION=%d",
                self.metadata_path,
                version,
                VERSION,
            )
            self._capture_legacy_hashes()
            self._save()
        self._rebuild_track_indexes()

    def _read_metadataHEADER(self, data: bytes) -> tuple[int, int]:
        if len(data) < HEADER.size:
            logger.error(
                "Music metadata header is truncated: path=%s byte_count=%d",
                self.metadata_path,
                len(data),
            )
            raise ValueError(f"{self.metadata_path} is not a valid MusicManager file")

        magic, version, track_count = HEADER.unpack_from(data)
        if magic != MAGIC:
            logger.error(
                "Music metadata magic mismatch: path=%s actual MAGIC=%r",
                self.metadata_path,
                magic,
            )
            raise ValueError(f"{self.metadata_path} is not a valid MusicManager file")
        if version not in (1, 2, VERSION):
            logger.error(
                "Unsupported music metadata version: path=%s version=%d",
                self.metadata_path,
                version,
            )
            raise ValueError(f"Unsupported metadata version: {version}")
        return version, track_count

    def _parse_metadata_records(
        self, data: bytes, version: int, track_count: int
    ) -> tuple[dict[UUID, Track], dict[UUID, Playlist], int]:
        tracks, offset = self._read_tracks(data, HEADER.size, track_count, version)
        playlists, offset = self._read_playlists(data, offset)
        return tracks, playlists, offset

    def _read_tracks(
        self, data: bytes, offset: int, count: int, version: int
    ) -> tuple[dict[UUID, Track], int]:
        tracks: dict[UUID, Track] = {}
        for _ in range(count):
            if offset + TRACK_FIXED.size > len(data):
                raise ValueError("Truncated music metadata")
            raw_id, added_at = TRACK_FIXED.unpack_from(data, offset)
            offset += TRACK_FIXED.size
            filename, offset = _read_utf8_string(data, offset)
            source, offset = _read_utf8_string(data, offset)
            title, offset = self._read_track_title(data, offset, version)
            content_hash, offset = self._read_track_hash(data, offset, version)
            track_id = UUID(bytes=raw_id)
            tracks[track_id] = Track(
                track_id, filename, source, title, added_at, content_hash
            )
        return tracks, offset

    @staticmethod
    def _read_track_title(data: bytes, offset: int, version: int) -> tuple[str, int]:
        if version < 2:
            return "", offset
        return _read_utf8_string(data, offset)

    @staticmethod
    def _read_track_hash(data: bytes, offset: int, version: int) -> tuple[str, int]:
        if version < 3:
            # This durable placeholder allows integrity checks to report an
            # unavailable legacy file without trusting its current contents.
            return "0" * (SHA256_BYTES * 2), offset
        if offset + SHA256_BYTES > len(data):
            raise ValueError("Truncated music metadata")
        return data[offset : offset + SHA256_BYTES].hex(), offset + SHA256_BYTES

    def _read_playlists(
        self, data: bytes, offset: int
    ) -> tuple[dict[UUID, Playlist], int]:
        if offset + U32.size > len(data):
            raise ValueError("Truncated music metadata")
        (count,) = U32.unpack_from(data, offset)
        offset += U32.size

        playlists: dict[UUID, Playlist] = {}
        for _ in range(count):
            if offset + UUID_ONLY.size > len(data):
                raise ValueError("Truncated music metadata")
            (raw_id,) = UUID_ONLY.unpack_from(data, offset)
            offset += UUID_ONLY.size
            name, offset = _read_utf8_string(data, offset)
            track_ids, offset = self._read_playlist_track_ids(data, offset)
            playlist_id = UUID(bytes=raw_id)
            playlists[playlist_id] = Playlist(playlist_id, name, track_ids)
        return playlists, offset

    @staticmethod
    def _read_playlist_track_ids(data: bytes, offset: int) -> tuple[list[UUID], int]:
        if offset + U32.size > len(data):
            raise ValueError("Truncated music metadata")
        (count,) = U32.unpack_from(data, offset)
        offset += U32.size
        track_ids: list[UUID] = []
        for _ in range(count):
            if offset + UUID_ONLY.size > len(data):
                raise ValueError("Truncated music metadata")
            (raw_id,) = UUID_ONLY.unpack_from(data, offset)
            offset += UUID_ONLY.size
            track_ids.append(UUID(bytes=raw_id))
        return track_ids, offset

    @staticmethod
    def _discard_missing_playlist_tracks(
        playlists: dict[UUID, Playlist], tracks: dict[UUID, Track]
    ) -> None:
        for playlist in playlists.values():
            playlist.track_ids = [
                track_id for track_id in playlist.track_ids if track_id in tracks
            ]

    def _capture_legacy_hashes(self) -> None:
        logger.debug(
            "Capturing hashes for legacy tracks: track_count=%d", len(self._tracks)
        )
        for track in self._tracks.values():
            try:
                path = self._path_for_filename(track.filename)
                if path.is_file():
                    track.content_hash = _sha256(path)
                    logger.debug(
                        "Captured legacy track hash: track_id=%s path=%s",
                        track.id,
                        path,
                    )
                else:
                    logger.warning(
                        "Legacy track file is unavailable; retaining zero hash: track_id=%s path=%s",
                        track.id,
                        path,
                    )
            except (OSError, ValueError):
                # A later integrity check reports files that cannot be read.
                logger.exception(
                    "Could not capture legacy track hash; integrity check will report it: "
                    "track_id=%s filename=%s",
                    track.id,
                    track.filename,
                )
                continue

    def _save(self) -> None:
        logger.debug(
            "Saving music metadata atomically: path=%s tracks=%d playlists=%d",
            self.metadata_path,
            len(self._tracks),
            len(self._playlists),
        )
        data = self._serialize_metadata()
        self._write_metadata(data)

    def _serialize_metadata(self) -> bytes:
        parts: list[bytes] = [HEADER.pack(MAGIC, VERSION, len(self._tracks))]
        for track in self._tracks.values():
            parts.append(TRACK_FIXED.pack(track.id.bytes, track.added_at))
            parts.extend(
                _pack_utf8_string(value)
                for value in (track.filename, track.source, track.title)
            )
            parts.append(self._content_hash_bytes(track))

        parts.append(U32.pack(len(self._playlists)))
        for playlist in self._playlists.values():
            parts.append(UUID_ONLY.pack(playlist.id.bytes))
            parts.extend(
                (_pack_utf8_string(playlist.name), U32.pack(len(playlist.track_ids)))
            )
            parts.extend(
                UUID_ONLY.pack(track_id.bytes) for track_id in playlist.track_ids
            )
        return b"".join(parts)

    @staticmethod
    def _content_hash_bytes(track: Track) -> bytes:
        try:
            digest = bytes.fromhex(track.content_hash)
        except ValueError as exc:
            raise ValueError(f"Invalid content hash for track {track.id}") from exc
        if len(digest) != SHA256_BYTES:
            raise ValueError(f"Invalid content hash for track {track.id}")
        return digest

    def _write_metadata(self, data: bytes) -> None:
        try:
            fd, temporary_name = tempfile.mkstemp(
                dir=self.metadata_path.parent, prefix=".mmgr-", suffix=".tmp"
            )
        except OSError:
            logger.exception(
                "Failed to create temporary metadata file: path=%s", self.metadata_path
            )
            raise
        logger.debug(
            "Writing metadata through temporary file: temporary_path=%s", temporary_name
        )
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            logger.debug(
                "Replacing metadata atomically: temporary_path=%s target_path=%s",
                temporary_name,
                self.metadata_path,
            )
            os.replace(temporary_name, self.metadata_path)
            logger.info(
                "Music metadata saved successfully: path=%s byte_count=%d",
                self.metadata_path,
                len(data),
            )
        except Exception:
            logger.exception(
                "Failed to save music metadata atomically: path=%s temporary_path=%s",
                self.metadata_path,
                temporary_name,
            )
            try:
                os.unlink(temporary_name)
            except OSError:
                logger.warning(
                    "Could not remove failed metadata temporary file: path=%s",
                    temporary_name,
                    exc_info=True,
                )
            raise

    # -- tracks ------------------------------------------------------
    def add_track(
        self, filename: str | Path, *, source: str = "", title: str = ""
    ) -> UUID:
        """Record an existing file in ``music_folder`` and capture its hash.

        The caller, normally a downloader, is responsible for putting the file
        there. This method never copies, moves, or modifies that file.
        """
        logger.debug(
            "add_track entered: filename=%s source=%s title=%r", filename, source, title
        )
        relative_filename = self._relative_filename(filename)
        path = self._music_folder / relative_filename
        try:
            file_status = path.stat()
        except OSError:
            logger.error(
                "Cannot add track because its file does not exist: path=%s", path
            )
            raise FileNotFoundError(path)
        if not stat.S_ISREG(file_status.st_mode):
            raise FileNotFoundError(path)
        if file_status.st_size <= 0:
            raise ValueError("Track file is empty")
        digest = _sha256(path)
        track_title = title.strip()

        with self._lock:
            track_id = uuid4()
            track = Track(
                track_id, relative_filename, source, track_title, time.time(), digest
            )
            previous_newest_track_ids = self._newest_track_ids
            self._tracks[track_id] = track
            self._index_track(track)
            self._newest_track_ids = None
            try:
                self._save()
            except Exception:
                self._tracks.pop(track_id, None)
                self._unindex_track(track)
                self._newest_track_ids = previous_newest_track_ids
                raise
            logger.info(
                "Track added: track_id=%s filename=%s source=%s title=%r",
                track_id,
                relative_filename,
                source,
                track_title,
            )
            return track_id

    def replace_track_file(self, track_id: UUID | str, filename: str | Path) -> None:
        """Point an existing track at a verified replacement file.

        The track identity and playlist membership remain unchanged. This is
        used when a new download repairs a missing, unreadable, or modified
        managed file for an already-known source.
        """
        relative_filename = self._relative_filename(filename)
        path = self._music_folder / relative_filename
        try:
            file_status = path.stat()
        except OSError:
            raise FileNotFoundError(path)
        if not stat.S_ISREG(file_status.st_mode):
            raise FileNotFoundError(path)
        if file_status.st_size <= 0:
            raise ValueError("Track file is empty")
        digest = _sha256(path)

        with self._lock:
            track = self._get_track(track_id)
            previous_filename = track.filename
            previous_hash = track.content_hash
            if previous_filename == relative_filename and previous_hash == digest:
                return
            self._remove_index_entry(self._content_index, previous_hash, track.id)
            track.filename = relative_filename
            track.content_hash = digest
            self._content_index.setdefault(digest, {})[track.id] = None
            try:
                self._save()
            except Exception:
                self._remove_index_entry(self._content_index, digest, track.id)
                track.filename = previous_filename
                track.content_hash = previous_hash
                if previous_hash:
                    self._content_index.setdefault(previous_hash, {})[track.id] = None
                raise
            logger.info(
                "Track file replaced: track_id=%s filename=%s",
                track.id,
                relative_filename,
            )

    def update_track(
        self,
        track_id: UUID | str,
        *,
        title: str | None = None,
        source: str | None = None,
    ) -> None:
        logger.debug(
            "update_track entered: track_id=%s title=%r source=%r",
            track_id,
            title,
            source,
        )
        normalized_title = title.strip() if title is not None else None
        if normalized_title is not None and not normalized_title:
            logger.warning("Refusing to set empty track title: track_id=%s", track_id)
            raise ValueError("Song title cannot be empty")
        with self._lock:
            track = self._get_track(track_id)
            previous_title = track.title
            previous_source = track.source
            title_changed = (
                normalized_title is not None and normalized_title != track.title
            )
            source_changed = source is not None and source != track.source
            if not title_changed and not source_changed:
                return
            if title_changed:
                track.title = normalized_title
            if source_changed:
                self._unindex_source(track)
                track.source = source
                self._index_source(track)
            try:
                self._save()
            except Exception:
                if source_changed:
                    self._unindex_source(track)
                    track.source = previous_source
                    self._index_source(track)
                track.title = previous_title
                raise
            logger.info(
                "Track metadata updated: track_id=%s title=%r source=%r",
                track.id,
                track.title,
                track.source,
            )

    def rename_track(self, track_id: UUID | str, title: str) -> None:
        logger.debug("rename_track entered: track_id=%s title=%r", track_id, title)
        self.update_track(track_id, title=title)

    def delete_track(self, track_id: UUID | str) -> None:
        logger.debug("delete_track entered: track_id=%s", track_id)
        with self._lock:
            track_uuid = _as_uuid(track_id)
            track = self._get_track(track_uuid)
            removed_references: list[tuple[Playlist, int]] = []
            for playlist in self._playlists.values():
                try:
                    index = playlist.track_ids.index(track_uuid)
                except ValueError:
                    continue
                playlist.track_ids.pop(index)
                removed_references.append((playlist, index))
            previous_tracks = self._tracks
            self._tracks = dict(self._tracks)
            del self._tracks[track_uuid]
            previous_newest_track_ids = self._newest_track_ids
            self._newest_track_ids = None
            try:
                self._save()
            except Exception:
                self._tracks = previous_tracks
                self._newest_track_ids = previous_newest_track_ids
                for playlist, index in removed_references:
                    playlist.track_ids.insert(index, track_uuid)
                raise
            self._unindex_track(track)
            logger.info(
                "Track record deleted; source file was left untouched: track_id=%s removed_playlist_references=%d",
                track_uuid,
                len(removed_references),
            )

    def clear_library(self) -> tuple[int, int]:
        """Remove every track and playlist record without deleting audio files."""
        logger.debug("clear_library entered")
        with self._lock:
            track_count = len(self._tracks)
            playlist_count = len(self._playlists)
            if not track_count and not playlist_count:
                return 0, 0

            previous_tracks = self._tracks
            previous_playlists = self._playlists
            previous_source_index = self._source_index
            previous_content_index = self._content_index
            previous_newest_track_ids = self._newest_track_ids
            self._tracks = {}
            self._playlists = {}
            self._source_index = {}
            self._content_index = {}
            self._newest_track_ids = None
            try:
                self._save()
            except Exception:
                self._tracks = previous_tracks
                self._playlists = previous_playlists
                self._source_index = previous_source_index
                self._content_index = previous_content_index
                self._newest_track_ids = previous_newest_track_ids
                raise

            logger.info(
                "Library records cleared; source files were left untouched: "
                "track_count=%d playlist_count=%d",
                track_count,
                playlist_count,
            )
            return track_count, playlist_count

    def list_tracks(self) -> tuple[Track, ...]:
        logger.debug("Listing tracks")
        with self._lock:
            tracks = tuple(replace(track) for track in self._tracks_newest_first())
        logger.debug("Listed tracks: count=%d", len(tracks))
        return tracks

    def counts(self) -> tuple[int, int]:
        """Return track and playlist counts without copying either collection."""
        with self._lock:
            return len(self._tracks), len(self._playlists)

    def get_track(self, track_id: UUID | str) -> Track:
        logger.debug("Getting track: track_id=%s", track_id)
        with self._lock:
            track = replace(self._get_track(track_id))
        logger.debug(
            "Retrieved track: track_id=%s filename=%s", track.id, track.filename
        )
        return track

    def has_track(self, track_id: UUID | str) -> bool:
        logger.debug("Checking track existence: track_id=%s", track_id)
        try:
            normalized = _as_uuid(track_id)
        except (TypeError, ValueError):
            result = False
        else:
            with self._lock:
                result = normalized in self._tracks
        logger.debug("Track existence result: track_id=%s exists=%s", track_id, result)
        return result

    def search_tracks(self, query: str) -> tuple[Track, ...]:
        logger.debug("Searching tracks: query=%r", query)
        needle = query.strip().casefold()
        if not needle:
            logger.debug("Search query is empty; returning entire track list")
            return self.list_tracks()
        with self._lock:
            results = tuple(
                replace(track)
                for track in self._tracks_newest_first()
                if needle in track.title.casefold()
                or needle in track.filename.casefold()
                or needle in track.source.casefold()
            )
        logger.debug(
            "Track search completed: query=%r result_count=%d", query, len(results)
        )
        return results

    def find_track_by_source(self, source: str) -> Track | None:
        logger.debug("Finding track by source: source=%s", source)
        needle = source_key(source)
        if not needle:
            logger.debug(
                "Source has no identity key; no track can match: source=%r", source
            )
            return None
        with self._lock:
            matches = self._source_index.get(needle)
            if matches:
                track = self._tracks[next(iter(matches))]
                logger.info(
                    "Found track by source: source=%s track_id=%s", source, track.id
                )
                return replace(track)
        logger.debug(
            "No track found for source: source=%s identity_key=%s", source, needle
        )
        return None

    def find_track_by_content(self, path: str | Path) -> Track | None:
        """Return the track with the same bytes as ``path``, if one exists."""
        digest = _sha256(Path(path))
        with self._lock:
            matches = self._content_index.get(digest)
            if matches:
                return replace(self._tracks[next(iter(matches))])
        return None

    def track_path(self, track_id: UUID | str) -> Path:
        logger.debug("Resolving track path: track_id=%s", track_id)
        with self._lock:
            path = self._path_for_filename(self._get_track(track_id).filename)
        logger.debug("Resolved track path: track_id=%s path=%s", track_id, path)
        return path

    # -- integrity ---------------------------------------------------
    def check_track_integrity(self, track_id: UUID | str) -> IntegrityProblem | None:
        logger.debug("Checking track integrity: track_id=%s", track_id)
        with self._lock:
            track = replace(self._get_track(track_id))
        problem = self._integrity_problem(track)
        logger.info(
            "Track integrity check completed: track_id=%s result=%s",
            track_id,
            problem.kind if problem else "intact",
        )
        return problem

    def check_library_integrity(self) -> tuple[IntegrityProblem, ...]:
        """Report every missing, unreadable, or modified library file.

        Checks are strictly observational: no files or metadata are repaired,
        replaced, deleted, or re-downloaded.
        """
        logger.debug("Checking library integrity")
        with self._lock:
            tracks = tuple(replace(track) for track in self._tracks.values())
        problems = tuple(
            problem
            for track in tracks
            if (problem := self._integrity_problem(track)) is not None
        )
        logger.info(
            "Library integrity check completed: problem_count=%d", len(problems)
        )
        return problems

    def _integrity_problem(self, track: Track) -> IntegrityProblem | None:
        logger.debug(
            "Inspecting track integrity: track_id=%s filename=%s",
            track.id,
            track.filename,
        )
        try:
            path = self._path_for_filename(track.filename)
        except ValueError as exc:
            logger.warning(
                "Track has invalid library path: track_id=%s filename=%s error=%s",
                track.id,
                track.filename,
                exc,
            )
            return IntegrityProblem(track.id, track.filename, "invalid_path", str(exc))
        try:
            file_status = path.stat()
        except FileNotFoundError:
            logger.warning("Track file is missing: track_id=%s path=%s", track.id, path)
            return IntegrityProblem(
                track.id, track.filename, "missing", f"Missing file: {path}"
            )
        except OSError as exc:
            return IntegrityProblem(
                track.id, track.filename, "unreadable", f"Cannot read {path}: {exc}"
            )
        if not stat.S_ISREG(file_status.st_mode):
            logger.warning(
                "Track path is not a regular file: track_id=%s path=%s", track.id, path
            )
            return IntegrityProblem(
                track.id,
                track.filename,
                "missing",
                f"Expected a file but found: {path}",
            )
        if file_status.st_size <= 0:
            return IntegrityProblem(
                track.id,
                track.filename,
                "empty",
                f"Audio file is empty: {path}",
            )
        try:
            actual_hash = _sha256(path)
        except OSError as exc:
            logger.exception(
                "Track file cannot be read for integrity check: track_id=%s path=%s",
                track.id,
                path,
            )
            return IntegrityProblem(
                track.id, track.filename, "unreadable", f"Cannot read {path}: {exc}"
            )
        if actual_hash != track.content_hash:
            logger.warning(
                "Track content hash mismatch: track_id=%s path=%s expected_hash=%s actual_hash=%s",
                track.id,
                path,
                track.content_hash,
                actual_hash,
            )
            return IntegrityProblem(
                track.id,
                track.filename,
                "modified",
                f"File content differs from recorded SHA-256: {path}",
                track.content_hash,
                actual_hash,
            )
        logger.debug("Track integrity verified: track_id=%s path=%s", track.id, path)
        return None

    # -- playlists ---------------------------------------------------
    def list_playlists(self) -> tuple[Playlist, ...]:
        logger.debug("Listing playlists")
        with self._lock:
            playlists = tuple(
                self._copy_playlist(playlist) for playlist in self._playlists.values()
            )
        logger.debug("Listed playlists: count=%d", len(playlists))
        return playlists

    def get_playlist(self, playlist_id: UUID | str) -> Playlist:
        logger.debug("Getting playlist: playlist_id=%s", playlist_id)
        with self._lock:
            playlist = self._get_playlist(playlist_id)
            result = self._copy_playlist(playlist)
        logger.debug(
            "Retrieved playlist: playlist_id=%s name=%r track_count=%d",
            result.id,
            result.name,
            len(result.track_ids),
        )
        return result

    def has_playlist(self, playlist_id: UUID | str) -> bool:
        logger.debug("Checking playlist existence: playlist_id=%s", playlist_id)
        try:
            normalized = _as_uuid(playlist_id)
        except (TypeError, ValueError):
            result = False
        else:
            with self._lock:
                result = normalized in self._playlists
        logger.debug(
            "Playlist existence result: playlist_id=%s exists=%s", playlist_id, result
        )
        return result

    def create_playlist(self, name: str) -> UUID:
        logger.debug("create_playlist entered: name=%r", name)
        name = name.strip()
        if not name:
            logger.warning("Refusing to create playlist with an empty name")
            raise ValueError("Playlist name cannot be empty")
        with self._lock:
            playlist_id = uuid4()
            self._playlists[playlist_id] = Playlist(playlist_id, name)
            try:
                self._save()
            except Exception:
                self._playlists.pop(playlist_id, None)
                raise
            logger.info("Playlist created: playlist_id=%s name=%r", playlist_id, name)
            return playlist_id

    def rename_playlist(self, playlist_id: UUID | str, name: str) -> None:
        logger.debug(
            "rename_playlist entered: playlist_id=%s name=%r", playlist_id, name
        )
        name = name.strip()
        if not name:
            logger.warning(
                "Refusing to rename playlist to an empty name: playlist_id=%s",
                playlist_id,
            )
            raise ValueError("Playlist name cannot be empty")
        with self._lock:
            playlist = self._get_playlist(playlist_id)
            previous_name = playlist.name
            if previous_name == name:
                return
            playlist.name = name
            try:
                self._save()
            except Exception:
                playlist.name = previous_name
                raise
            logger.info("Playlist renamed: playlist_id=%s name=%r", playlist_id, name)

    def delete_playlist(self, playlist_id: UUID | str) -> None:
        logger.debug("delete_playlist entered: playlist_id=%s", playlist_id)
        with self._lock:
            playlist_uuid = _as_uuid(playlist_id)
            if playlist_uuid not in self._playlists:
                logger.warning(
                    "Cannot delete missing playlist: playlist_id=%s", playlist_uuid
                )
                raise KeyError(f"No playlist with id {playlist_uuid}")
            previous_playlists = self._playlists
            self._playlists = dict(self._playlists)
            del self._playlists[playlist_uuid]
            try:
                self._save()
            except Exception:
                self._playlists = previous_playlists
                raise
            logger.info("Playlist deleted: playlist_id=%s", playlist_uuid)

    def clear_playlists(self) -> int:
        """Delete every playlist while preserving all library tracks."""
        logger.debug("clear_playlists entered")
        with self._lock:
            playlist_count = len(self._playlists)
            if not playlist_count:
                return 0

            previous_playlists = self._playlists
            self._playlists = {}
            try:
                self._save()
            except Exception:
                self._playlists = previous_playlists
                raise

            logger.info("All playlists deleted: playlist_count=%d", playlist_count)
            return playlist_count

    def playlist_tracks(self, playlist_id: UUID | str) -> tuple[Track, ...]:
        logger.debug("Listing tracks for playlist: playlist_id=%s", playlist_id)
        with self._lock:
            playlist = self._get_playlist(playlist_id)
            tracks = tuple(
                replace(self._tracks[item])
                for item in playlist.track_ids
                if item in self._tracks
            )
        logger.debug(
            "Listed playlist tracks: playlist_id=%s track_count=%d",
            playlist_id,
            len(tracks),
        )
        return tracks

    def playlist_track_count(self, playlist_id: UUID | str) -> int:
        logger.debug("Counting tracks in playlist: playlist_id=%s", playlist_id)
        with self._lock:
            count = len(self._get_playlist(playlist_id).track_ids)
        logger.debug(
            "Counted playlist tracks: playlist_id=%s track_count=%d", playlist_id, count
        )
        return count

    def add_to_playlist(self, playlist_id: UUID | str, track_id: UUID | str) -> None:
        logger.debug(
            "add_to_playlist entered: playlist_id=%s track_id=%s", playlist_id, track_id
        )
        with self._lock:
            playlist = self._get_playlist(playlist_id)
            track = self._get_track(track_id)
            if track.id not in playlist.track_ids:
                playlist.track_ids.append(track.id)
                try:
                    self._save()
                except Exception:
                    playlist.track_ids.pop()
                    raise
                logger.info(
                    "Track added to playlist: playlist_id=%s track_id=%s",
                    playlist.id,
                    track.id,
                )
            else:
                logger.debug(
                    "Track already belongs to playlist; no metadata change made: playlist_id=%s track_id=%s",
                    playlist.id,
                    track.id,
                )

    def add_tracks_to_playlist(
        self,
        playlist_id: UUID | str,
        track_ids: Sequence[UUID | str],
    ) -> int:
        """Add several existing tracks with one atomic metadata write."""
        with self._lock:
            playlist = self._get_playlist(playlist_id)
            existing = set(playlist.track_ids)
            additions: list[UUID] = []
            for track_id in track_ids:
                normalized_id = self._get_track(track_id).id
                if normalized_id not in existing:
                    additions.append(normalized_id)
                    existing.add(normalized_id)
            if not additions:
                return 0

            previous = list(playlist.track_ids)
            playlist.track_ids.extend(additions)
            try:
                self._save()
            except Exception:
                playlist.track_ids = previous
                raise
            logger.info(
                "Tracks added to playlist: playlist_id=%s track_count=%d",
                playlist.id,
                len(additions),
            )
            return len(additions)

    def merge_tracks_into_playlist(
        self,
        playlist_id: UUID | str,
        ordered_track_ids: Sequence[UUID | str],
    ) -> int:
        """Place known tracks first in the requested order with one write.

        Existing tracks not included in ``ordered_track_ids`` are retained
        after the ordered prefix. This supports incremental imports without a
        separate add, reload, and reorder persistence cycle.
        """
        with self._lock:
            playlist = self._get_playlist(playlist_id)
            ordered: list[UUID] = []
            ordered_set: set[UUID] = set()
            for track_id in ordered_track_ids:
                normalized = self._get_track(track_id).id
                if normalized not in ordered_set:
                    ordered.append(normalized)
                    ordered_set.add(normalized)

            previous = playlist.track_ids
            previous_set = set(previous)
            merged = ordered + [
                track_id for track_id in previous if track_id not in ordered_set
            ]
            if merged == previous:
                return 0
            playlist.track_ids = merged
            try:
                self._save()
            except Exception:
                playlist.track_ids = previous
                raise
            return len(ordered_set - previous_set)

    def remove_from_playlist(
        self, playlist_id: UUID | str, track_id: UUID | str
    ) -> None:
        logger.debug(
            "remove_from_playlist entered: playlist_id=%s track_id=%s",
            playlist_id,
            track_id,
        )
        with self._lock:
            playlist = self._get_playlist(playlist_id)
            track_uuid = _as_uuid(track_id)
            try:
                index = playlist.track_ids.index(track_uuid)
            except ValueError:
                logger.warning(
                    "Cannot remove track absent from playlist: playlist_id=%s track_id=%s",
                    playlist.id,
                    track_uuid,
                )
                raise KeyError(
                    f"Track {track_uuid} is not in playlist {playlist.id}"
                ) from None
            playlist.track_ids.pop(index)
            try:
                self._save()
            except Exception:
                playlist.track_ids.insert(index, track_uuid)
                raise
            logger.info(
                "Track removed from playlist: playlist_id=%s track_id=%s",
                playlist.id,
                track_uuid,
            )

    def move_playlist_track(
        self, playlist_id: UUID | str, track_id: UUID | str, offset: int
    ) -> None:
        logger.debug(
            "move_playlist_track entered: playlist_id=%s track_id=%s offset=%d",
            playlist_id,
            track_id,
            offset,
        )
        if offset not in (-1, 1):
            logger.warning("Invalid playlist movement offset: offset=%d", offset)
            raise ValueError("offset must be -1 or 1")
        with self._lock:
            playlist = self._get_playlist(playlist_id)
            track_uuid = _as_uuid(track_id)
            try:
                index = playlist.track_ids.index(track_uuid)
            except ValueError:
                logger.warning(
                    "Cannot move track absent from playlist: playlist_id=%s track_id=%s",
                    playlist.id,
                    track_uuid,
                )
                raise KeyError(
                    f"Track {track_uuid} is not in playlist {playlist.id}"
                ) from None
            new_index = index + offset
            if 0 <= new_index < len(playlist.track_ids):
                playlist.track_ids[index], playlist.track_ids[new_index] = (
                    playlist.track_ids[new_index],
                    playlist.track_ids[index],
                )
                try:
                    self._save()
                except Exception:
                    playlist.track_ids[index], playlist.track_ids[new_index] = (
                        playlist.track_ids[new_index],
                        playlist.track_ids[index],
                    )
                    raise
                logger.info(
                    "Moved track within playlist: playlist_id=%s track_id=%s old_index=%d new_index=%d",
                    playlist.id,
                    track_uuid,
                    index,
                    new_index,
                )
            else:
                logger.debug(
                    "Playlist move skipped because target index is out of bounds: playlist_id=%s "
                    "track_id=%s current_index=%d requested_offset=%d track_count=%d",
                    playlist.id,
                    track_uuid,
                    index,
                    offset,
                    len(playlist.track_ids),
                )

    def reorder_playlist(
        self, playlist_id: UUID | str, ordered_track_ids: Sequence[UUID | str]
    ) -> None:
        """Persist an explicit playlist order.

        Every existing track must be supplied exactly once.  This strict
        contract prevents a drag-and-drop UI bug from silently dropping or
        duplicating tracks.
        """
        with self._lock:
            playlist = self._get_playlist(playlist_id)
            normalized = [_as_uuid(track_id) for track_id in ordered_track_ids]
            normalized_set = set(normalized)
            if len(normalized) != len(normalized_set):
                raise ValueError("Playlist order contains duplicate tracks")
            if normalized_set != set(playlist.track_ids):
                raise ValueError("Playlist order must contain every existing track")
            if normalized == playlist.track_ids:
                return
            previous = playlist.track_ids
            playlist.track_ids = normalized
            try:
                self._save()
            except Exception:
                playlist.track_ids = previous
                raise

    def copy_playlist_track(
        self,
        source_playlist_id: UUID | str,
        target_playlist_id: UUID | str,
        track_id: UUID | str,
    ) -> None:
        """Copy a track between playlists, preserving source membership."""
        with self._lock:
            source = self._get_playlist(source_playlist_id)
            target = self._get_playlist(target_playlist_id)
            track = self._get_track(track_id)
            if track.id not in source.track_ids:
                raise KeyError(f"Track {track.id} is not in playlist {source.id}")
            if track.id not in target.track_ids:
                target.track_ids.append(track.id)
                try:
                    self._save()
                except Exception:
                    target.track_ids.pop()
                    raise

    def move_track_between_playlists(
        self,
        source_playlist_id: UUID | str,
        target_playlist_id: UUID | str,
        track_id: UUID | str,
    ) -> None:
        """Move a track between playlists as one atomic metadata update."""
        with self._lock:
            source = self._get_playlist(source_playlist_id)
            target = self._get_playlist(target_playlist_id)
            track = self._get_track(track_id)
            if source.id == target.id:
                return
            try:
                source_index = source.track_ids.index(track.id)
            except ValueError:
                raise KeyError(f"Track {track.id} is not in playlist {source.id}")
            source.track_ids.pop(source_index)
            added_to_target = track.id not in target.track_ids
            if added_to_target:
                target.track_ids.append(track.id)
            try:
                self._save()
            except Exception:
                source.track_ids.insert(source_index, track.id)
                if added_to_target:
                    target.track_ids.pop()
                raise

    # -- internal helpers -------------------------------------------
    def _relative_filename(self, filename: str | Path) -> str:
        """Return a validated track filename relative to the music folder."""
        logger.debug("Resolving track filename: filename=%s", filename)
        candidate = Path(filename)
        path = (
            candidate.resolve()
            if candidate.is_absolute()
            else (self._music_folder / candidate).resolve()
        )
        try:
            relative = path.relative_to(self._music_folder)
        except ValueError:
            logger.warning(
                "Rejected track path outside music folder: filename=%s resolved_path=%s music_folder=%s",
                filename,
                path,
                self._music_folder,
            )
            raise ValueError(
                "Track files must be inside the configured music folder"
            ) from None
        result = str(relative)
        logger.debug(
            "Resolved track filename: filename=%s relative_filename=%s",
            filename,
            result,
        )
        return result

    def _path_for_filename(self, filename: str) -> Path:
        path = self._music_folder / self._relative_filename(filename)
        logger.debug(
            "Resolved music path for filename: filename=%s path=%s", filename, path
        )
        return path

    def _tracks_newest_first(self) -> Iterator[Track]:
        """Return tracks in display order, sorting only after membership changes."""
        if self._newest_track_ids is None:
            self._newest_track_ids = tuple(
                sorted(
                    self._tracks,
                    key=lambda track_id: self._tracks[track_id].added_at,
                    reverse=True,
                )
            )
        return (self._tracks[track_id] for track_id in self._newest_track_ids)

    def _rebuild_track_indexes(self) -> None:
        """Build O(1) source/content lookup tables after loading metadata."""
        self._source_index.clear()
        self._content_index.clear()
        for track in self._tracks.values():
            self._index_track(track)
        self._newest_track_ids = None

    def _index_track(self, track: Track) -> None:
        self._index_source(track)
        if track.content_hash:
            self._content_index.setdefault(track.content_hash, {})[track.id] = None

    def _unindex_track(self, track: Track) -> None:
        self._unindex_source(track)
        self._remove_index_entry(self._content_index, track.content_hash, track.id)

    def _index_source(self, track: Track) -> None:
        key = source_key(track.source)
        if key:
            self._source_index.setdefault(key, {})[track.id] = None

    def _unindex_source(self, track: Track) -> None:
        self._remove_index_entry(self._source_index, source_key(track.source), track.id)

    @staticmethod
    def _remove_index_entry(
        index: dict[str, dict[UUID, None]], key: str, track_id: UUID
    ) -> None:
        matches = index.get(key)
        if not matches:
            return
        matches.pop(track_id, None)
        if not matches:
            index.pop(key, None)

    @staticmethod
    def _copy_playlist(playlist: Playlist) -> Playlist:
        return Playlist(playlist.id, playlist.name, list(playlist.track_ids))

    def _get_track(self, track_id: UUID | str) -> Track:
        track_uuid = _as_uuid(track_id)
        try:
            return self._tracks[track_uuid]
        except KeyError:
            logger.warning("Track lookup failed: track_id=%s", track_uuid)
            raise KeyError(f"No track with id {track_uuid}") from None

    def _get_playlist(self, playlist_id: UUID | str) -> Playlist:
        playlist_uuid = _as_uuid(playlist_id)
        try:
            return self._playlists[playlist_uuid]
        except KeyError:
            logger.warning("Playlist lookup failed: playlist_id=%s", playlist_uuid)
            raise KeyError(f"No playlist with id {playlist_uuid}") from None
