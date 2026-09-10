"""Durable, offline-first thumbnail storage.

The authoritative artwork is embedded in each downloaded media file. The UI
also renders a URL-keyed copy of those verified embedded bytes directly, which
works on desktop and mobile without repeatedly parsing the media container.
Native media sessions receive a file URI for the same derived cache entry.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit

from musicplayer.core.storage import sync_directory

logger = logging.getLogger(__name__)

_MAX_THUMBNAIL_BYTES = 8 * 1024 * 1024
_MEMORY_CACHE_ENTRIES = 64


class ThumbnailCache:
    """Store thumbnail bytes by source URL and prefer them for every read."""

    def __init__(
        self,
        directory: str | Path,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._key_locks: dict[str, tuple[threading.Lock, int]] = {}
        self._memory: OrderedDict[str, bytes] = OrderedDict()

    def cached_path(self, url: str) -> Path | None:
        """Return the durable entry for a remote URL without touching network."""
        key = _remote_key(url)
        if key is None:
            return None
        path = self.directory / f"{key}.thumb"
        try:
            return path if path.is_file() and path.stat().st_size > 0 else None
        except OSError:
            return None

    def image_source(self, source: str | bytes) -> str | bytes:
        """Resolve to cached bytes when possible, otherwise preserve *source*."""
        if isinstance(source, bytes) or not source:
            return source
        path = self.cached_path(source)
        if path is None:
            return source
        key = path.stem
        with self._lock:
            cached = self._memory.get(key)
            if cached is not None:
                self._memory.move_to_end(key)
                return cached
        try:
            data = path.read_bytes()
            _validate_image(data)
        except (OSError, ValueError):
            logger.warning("Ignoring invalid cached thumbnail: path=%s", path)
            return source
        self._remember(key, data)
        return data

    def artwork_uri(self, source: str) -> str:
        """Resolve artwork for an operating-system media session."""
        path = self.cached_path(source)
        return path.resolve().as_uri() if path is not None else source

    def store_bytes(self, url: str, data: bytes) -> bool:
        """Atomically retain image bytes already downloaded with the media."""
        key = _remote_key(url)
        if key is None or not data:
            return False
        try:
            _validate_image(data)
        except ValueError:
            logger.warning("Downloaded thumbnail has an unsupported image format")
            return False
        key_lock = self._key_lock(key)
        try:
            with key_lock:
                # Replace even a non-empty entry: a previous process or manual
                # disk edit may have left corrupt cache bytes at this key.
                self._write(key, data)
                self._remember(key, data)
            return True
        except OSError:
            logger.warning("Could not cache thumbnail: url=%s", url, exc_info=True)
            return False
        finally:
            self._release_key_lock(key, key_lock)

    def _write(self, key: str, data: bytes) -> None:
        if len(data) > _MAX_THUMBNAIL_BYTES:
            raise ValueError("Thumbnail exceeds the size limit")
        destination = self.directory / f"{key}.thumb"
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.directory, prefix=f".{key}-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, destination)
            sync_directory(self.directory)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise

    def _remember(self, key: str, data: bytes) -> None:
        with self._lock:
            self._memory[key] = data
            self._memory.move_to_end(key)
            while len(self._memory) > _MEMORY_CACHE_ENTRIES:
                self._memory.popitem(last=False)

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            entry = self._key_locks.get(key)
            if entry is None:
                key_lock = threading.Lock()
                self._key_locks[key] = (key_lock, 1)
                return key_lock
            key_lock, references = entry
            self._key_locks[key] = (key_lock, references + 1)
            return key_lock

    def _release_key_lock(self, key: str, key_lock: threading.Lock) -> None:
        with self._lock:
            entry = self._key_locks.get(key)
            if entry is None or entry[0] is not key_lock:
                return
            references = entry[1] - 1
            if references == 0:
                self._key_locks.pop(key, None)
            else:
                self._key_locks[key] = (key_lock, references)


def _remote_key(url: str) -> str | None:
    parsed = urlsplit(str(url).strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return None
    return hashlib.sha256(str(url).strip().encode("utf-8")).hexdigest()


def _validate_image(data: bytes) -> None:
    if not data or len(data) > _MAX_THUMBNAIL_BYTES:
        raise ValueError("Thumbnail is empty or exceeds the size limit")
    supported = (
        data.startswith(b"\xff\xd8\xff"),
        data.startswith(b"\x89PNG\r\n\x1a\n"),
        data.startswith((b"GIF87a", b"GIF89a")),
        len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        data.startswith(b"BM"),
    )
    if not any(supported):
        raise ValueError("Unsupported thumbnail format")


__all__ = ["ThumbnailCache"]
