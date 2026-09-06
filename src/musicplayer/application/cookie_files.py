from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

COOKIE_FILE_NAME = "cookies.txt"
MAX_COOKIE_FILE_SIZE = 10 * 1024 * 1024
_NETSCAPE_HEADER = re.compile(r"^#(?: Netscape)? HTTP Cookie File\b", re.IGNORECASE)


class CookieFileError(ValueError):
    """Raised when an uploaded cookie file is unsafe or unsupported."""


@dataclass(frozen=True)
class ValidatedCookieFile:
    """Normalized Netscape cookie-file contents ready for private storage."""

    content: bytes
    cookie_count: int


def validate_cookie_file(content: bytes) -> ValidatedCookieFile:
    """Validate and normalize a Netscape/Mozilla ``cookies.txt`` export.

    yt-dlp consumes the Netscape text format. Browser-extension JSON exports,
    browser databases, and arbitrary text files are rejected before anything
    is persisted or passed to yt-dlp.
    """
    if not content:
        raise CookieFileError("The selected cookie file is empty.")
    if len(content) > MAX_COOKIE_FILE_SIZE:
        raise CookieFileError("The selected cookie file is larger than 10 MB.")
    if b"\x00" in content:
        raise CookieFileError(
            "Unsupported cookie format. Upload a Netscape cookies.txt file."
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CookieFileError(
            "Cookie files must be UTF-8 text in Netscape cookies.txt format."
        ) from error

    lines = text.splitlines()
    if not lines or not _NETSCAPE_HEADER.match(lines[0].strip()):
        raise CookieFileError(
            "Unsupported cookie format. Upload a Netscape cookies.txt file."
        )

    cookie_count = 0
    for line_number, original_line in enumerate(lines[1:], start=2):
        line = original_line
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif not line.strip() or line.lstrip().startswith(("#", "$")):
            continue

        fields = line.split("\t")
        if len(fields) != 7:
            raise CookieFileError(
                f"Invalid Netscape cookie record on line {line_number}."
            )
        domain, include_subdomains, path, secure, expires, name, value = fields
        if not domain or not path or (not name and not value):
            raise CookieFileError(
                f"Incomplete Netscape cookie record on line {line_number}."
            )
        if include_subdomains not in {"TRUE", "FALSE"} or secure not in {
            "TRUE",
            "FALSE",
        }:
            raise CookieFileError(
                f"Invalid cookie flags on line {line_number}; expected TRUE or FALSE."
            )
        if (include_subdomains == "TRUE") != domain.startswith("."):
            raise CookieFileError(f"Invalid cookie domain flags on line {line_number}.")
        if expires:
            try:
                int(expires)
            except ValueError as error:
                raise CookieFileError(
                    f"Invalid cookie expiry value on line {line_number}."
                ) from error
        cookie_count += 1

    if not cookie_count:
        raise CookieFileError("The selected file does not contain any cookies.")

    normalized = "\n".join(lines).rstrip("\n") + "\n"
    return ValidatedCookieFile(normalized.encode("utf-8"), cookie_count)


def install_cookie_file(
    cookie_file: ValidatedCookieFile, destination: str | Path
) -> Path:
    """Atomically install a validated cookie file in app-private storage."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=".melody-cookies-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(cookie_file.content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, target)
        try:
            target.chmod(0o600)
        except OSError:
            # Some Android-backed filesystems do not expose POSIX permissions.
            pass
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return target
