import hashlib
import logging
from pathlib import Path
from uuid import UUID

from musicplayer.core.media import source_key

__all__ = ["source_key"]

from .config import U32

logger = logging.getLogger(__name__)


def _as_uuid(value: UUID | str) -> UUID:
    result = value if isinstance(value, UUID) else UUID(str(value))
    logger.debug("Converted value to UUID: input=%r result=%s", value, result)
    return result


def _read_utf8_string(data: bytes, offset: int) -> tuple[str, int]:
    logger.debug(
        "Reading UTF-8 field from metadata: offset=%d data_length=%d", offset, len(data)
    )
    if offset + U32.size > len(data):
        logger.error(
            "Metadata is truncated before UTF-8 field length: offset=%d", offset
        )
        raise ValueError("Truncated music metadata")
    (length,) = U32.unpack_from(data, offset)
    offset += U32.size
    end = offset + length
    if end > len(data):
        logger.error(
            "Metadata is truncated inside UTF-8 field: offset=%d length=%d data_length=%d",
            offset,
            length,
            len(data),
        )
        raise ValueError("Truncated music metadata")
    try:
        value = data[offset:end].decode("utf-8")
    except UnicodeDecodeError:
        logger.exception(
            "Invalid UTF-8 metadata field: offset=%d length=%d", offset, length
        )
        raise
    logger.debug(
        "Read UTF-8 metadata field: offset=%d length=%d next_offset=%d",
        offset,
        length,
        end,
    )
    return value, end


def _pack_utf8_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return U32.pack(len(encoded)) + encoded


def _sha256(path: Path) -> str:
    logger.debug("Calculating SHA-256: path=%s", path)
    digest = hashlib.sha256()
    bytes_read = 0
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1 << 20), b""):
                bytes_read += len(chunk)
                digest.update(chunk)
    except OSError:
        logger.exception("SHA-256 calculation failed while reading file: path=%s", path)
        raise
    result = digest.hexdigest()
    logger.debug(
        "Calculated SHA-256: path=%s bytes_read=%d digest=%s", path, bytes_read, result
    )
    return result
