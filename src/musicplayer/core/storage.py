"""Small filesystem primitives shared by durable state owners."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _cleanup_temporary(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logging.getLogger(__name__).warning(
            "Could not remove temporary file %s", path, exc_info=True
        )


def sync_directory(directory: Path) -> None:
    """Flush a committed rename without misreporting it as an aborted write.

    A directory barrier can be unsupported or fail after the file was replaced.
    Raising then would make callers roll back memory although disk has committed.
    Report the durability limitation while preserving the committed state.
    """
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        logging.getLogger(__name__).warning(
            "File replacement committed, but directory durability could not be confirmed: %s",
            directory,
            exc_info=True,
        )


def atomic_write(path: Path, contents: bytes) -> None:
    """Replace a file only after all bytes have reached durable storage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        _cleanup_temporary(temporary)


def atomic_copy(source: Path, destination: Path) -> None:
    """Copy across filesystems without exposing a partial destination file."""
    descriptor, name = tempfile.mkstemp(
        dir=destination.parent, prefix=".melody-import-", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        sync_directory(destination.parent)
    finally:
        _cleanup_temporary(temporary)


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Nonblocking OS lock; process termination releases it automatically.

    The lock file must remain in place: unlinking it can create two lock owners.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
