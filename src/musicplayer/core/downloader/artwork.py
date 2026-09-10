"""Read cover artwork back from completed audio containers.

Mutagen is imported inside the feature call so application startup does not pay
for media-tag parsing. The downloader uses this read-back as proof that yt-dlp
burned the thumbnail into the output before the staging files are committed.
"""

from __future__ import annotations

import base64
import binascii
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ARTWORK_BYTES = 8 * 1024 * 1024


def read_embedded_artwork(path: str | Path) -> bytes:
    """Return a front-cover payload from a supported audio file, if present."""
    from mutagen import MutagenError

    media_path = Path(path)
    try:
        candidates = _read_candidates(media_path)
    except (MutagenError, OSError, EOFError, TypeError, ValueError) as error:
        # Mutagen exposes format-specific exception subclasses and Python's
        # binary decoders can also reject damaged tag data. Treat either as a
        # failed verification; the caller decides whether artwork was required.
        logger.debug(
            "Could not read embedded artwork: path=%s error=%s",
            media_path,
            error,
        )
        return b""
    usable = [data for data in candidates if 0 < len(data) <= _MAX_ARTWORK_BYTES]
    return max(usable, key=len, default=b"")


def _read_candidates(path: Path) -> list[bytes]:
    extension = path.suffix.casefold()
    if extension == ".mp3":
        from mutagen.id3 import ID3

        frames = ID3(path).getall("APIC")
        front_covers = [
            bytes(frame.data) for frame in frames if frame.type == 3 and frame.data
        ]
        return front_covers or [bytes(frame.data) for frame in frames if frame.data]
    if extension in {".m4a", ".mp4", ".m4v", ".mov"}:
        from mutagen.mp4 import MP4

        media = MP4(path)
        covers = (media.tags or {}).get("covr", [])
        return [bytes(cover) for cover in covers if cover]
    if extension == ".flac":
        from mutagen.flac import FLAC

        pictures = FLAC(path).pictures
        front_covers = [
            bytes(picture.data)
            for picture in pictures
            if picture.type == 3 and picture.data
        ]
        return front_covers or [
            bytes(picture.data) for picture in pictures if picture.data
        ]
    if extension in {".ogg", ".opus"}:
        from mutagen.flac import Picture
        from mutagen.oggopus import OggOpus
        from mutagen.oggvorbis import OggVorbis

        media: Any = OggOpus(path) if extension == ".opus" else OggVorbis(path)
        result: list[tuple[int, bytes]] = []
        for encoded in media.get("metadata_block_picture", []):
            try:
                picture = Picture(base64.b64decode(encoded, validate=True))
            except (binascii.Error, TypeError, ValueError):
                continue
            if picture.data:
                result.append((picture.type, bytes(picture.data)))
        front_covers = [data for picture_type, data in result if picture_type == 3]
        return front_covers or [data for _, data in result]
    return []


__all__ = ["read_embedded_artwork"]
