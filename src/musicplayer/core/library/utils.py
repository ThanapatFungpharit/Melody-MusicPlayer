import hashlib
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit
from uuid import UUID

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


def source_key(source: str) -> str:
    parsed = urlsplit(source.strip())
    host = parsed.netloc.casefold().removeprefix("www.")
    query = parse_qs(parsed.query)
    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"} and query.get("v"):
        key = f"youtube:{query['v'][0]}"
        logger.debug(
            "Normalized YouTube source identity: source=%s key=%s", source, key
        )
        return key
    if host == "youtu.be":
        key = f"youtube:{parsed.path.strip('/')}"
        logger.debug(
            "Normalized shortened YouTube source identity: source=%s key=%s",
            source,
            key,
        )
        return key
    filtered_query = "&".join(
        f"{name}={value}"
        for name in sorted(query)
        if not name.casefold().startswith("utm_")
        for value in query[name]
    )
    key = urlunsplit(
        (parsed.scheme.casefold(), host, parsed.path.rstrip("/"), filtered_query, "")
    )
    logger.debug("Normalized source identity: source=%s key=%s", source, key)
    return key


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
