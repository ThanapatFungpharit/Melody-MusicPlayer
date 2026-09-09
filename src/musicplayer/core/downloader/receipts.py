"""Durable handoff from download-owned files to application registration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from musicplayer.core.storage import atomic_write


def receipt_path(directory: Path, task_id: UUID) -> Path:
    return directory / f".melody-download-{task_id}.json"


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_receipt(
    directory: Path, task_id: UUID, url: str, files: list[dict[str, str]]
) -> None:
    payload = {"schema": 1, "task_id": str(task_id), "url": url, "files": files}
    atomic_write(receipt_path(directory, task_id), json.dumps(payload).encode("utf-8"))


def read_receipt(path: Path) -> tuple[UUID, str, tuple[Path, ...]]:
    if path.is_symlink() or path.stat().st_size > 128 * 1024:
        raise ValueError("Invalid download receipt")
    data = json.loads(path.read_text("utf-8"))
    task_id = UUID(data["task_id"])
    if data.get("schema") != 1 or path != receipt_path(path.parent, task_id):
        raise ValueError("Invalid download receipt identity")
    files = []
    for entry in data["files"]:
        name = entry["filename"]
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("Download receipt escaped its owned folder")
        candidate = path.parent / name
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or file_digest(candidate) != entry["sha256"]
        ):
            raise ValueError("Download handoff is missing or corrupt")
        files.append(candidate)
    if not files or not isinstance(data["url"], str):
        raise ValueError("Empty download receipt")
    return task_id, data["url"], tuple(files)
