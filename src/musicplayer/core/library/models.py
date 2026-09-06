import time
from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class Track:
    id: UUID
    filename: str
    source: str = ""
    title: str = ""
    added_at: float = field(default_factory=time.time)
    content_hash: str = ""


@dataclass
class Playlist:
    id: UUID
    name: str
    track_ids: list[UUID] = field(default_factory=list)


@dataclass(frozen=True)
class IntegrityProblem:
    track_id: UUID
    filename: str
    kind: str
    message: str
    expected_hash: str = ""
    actual_hash: str = ""
