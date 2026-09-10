from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .models import RepeatMode


@dataclass
class PlaybackQueue:
    """A persistent temporary play queue, independent of library playlists."""

    items: list[str] = field(default_factory=list)
    current_index: int = -1
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.OFF

    @property
    def current(self) -> str | None:
        if 0 <= self.current_index < len(self.items):
            return self.items[self.current_index]
        return None

    @property
    def upcoming(self) -> tuple[str, ...]:
        if self.current_index < 0:
            return tuple(self.items)
        return tuple(self.items[self.current_index + 1 :])

    def replace(
        self,
        track_ids: list[str],
        *,
        start_index: int = 0,
        shuffle: bool | None = None,
    ) -> str | None:
        self.items = list(dict.fromkeys(str(item) for item in track_ids))
        self.current_index = (
            max(0, min(start_index, len(self.items) - 1)) if self.items else -1
        )
        if shuffle is not None:
            self.shuffle = bool(shuffle)
        if self.shuffle and self.items:
            # Shuffle is a one-time queue reorder, not a random decision at
            # every transition. An explicit shuffled collection reorders every
            # row; otherwise a specifically requested track remains first.
            if shuffle is True:
                random.shuffle(self.items)
            else:
                current = self.items.pop(self.current_index)
                random.shuffle(self.items)
                self.items.insert(0, current)
            self.current_index = 0
        return self.current

    def select(self, index: int) -> str | None:
        """Select a visible queue row without mutating its order."""
        if not self.items:
            self.current_index = -1
        else:
            self.current_index = max(0, min(int(index), len(self.items) - 1))
        return self.current

    def add_next(self, track_id: str) -> None:
        track_id = str(track_id)
        if not self.items or self.current_index < 0:
            self.items.insert(0, track_id)
            self.current_index = max(self.current_index, 0)
            return
        self.items.insert(self.current_index + 1, track_id)

    def add_last(self, track_id: str) -> None:
        self.items.append(str(track_id))
        self.current_index = max(self.current_index, 0)

    def remove_at(self, index: int) -> str:
        removed = self.items.pop(index)
        if not self.items:
            self.current_index = -1
        elif index < self.current_index:
            self.current_index -= 1
        elif index == self.current_index and self.current_index >= len(self.items):
            self.current_index = len(self.items) - 1
        return removed

    def move(self, old_index: int, new_index: int) -> None:
        if old_index == new_index:
            return
        current_index = self.current_index
        item = self.items.pop(old_index)
        target = max(0, min(new_index, len(self.items)))
        self.items.insert(target, item)
        if current_index == old_index:
            self.current_index = target
        elif old_index < current_index <= target:
            self.current_index -= 1
        elif target <= current_index < old_index:
            self.current_index += 1

    def clear(self, *, keep_current: bool = False) -> None:
        current = self.current if keep_current else None
        self.items = [current] if current else []
        self.current_index = 0 if current else -1

    def set_shuffle(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        if self.shuffle != enabled:
            self.shuffle = enabled
            if enabled:
                start = max(0, self.current_index + 1)
                upcoming = self.items[start:]
                random.shuffle(upcoming)
                self.items[start:] = upcoming
        return self.shuffle

    def peek_next(self, *, automatic: bool = False) -> str | None:
        """Return the next visible queue item without advancing the queue."""
        if not self.items:
            return None
        if automatic and self.repeat is RepeatMode.TRACK:
            return self.current
        if self.current_index + 1 < len(self.items):
            return self.items[self.current_index + 1]
        if self.repeat is RepeatMode.PLAYLIST:
            return self.items[0]
        return None

    def next(self, *, automatic: bool = False) -> str | None:
        if not self.items:
            return None
        if automatic and self.repeat is RepeatMode.TRACK:
            return self.current
        if self.current_index + 1 < len(self.items):
            self.current_index += 1
            return self.current
        if self.repeat is RepeatMode.PLAYLIST:
            self.current_index = 0
            return self.current
        return None

    def previous(self) -> str | None:
        if not self.items:
            return None
        if self.current_index > 0:
            self.current_index -= 1
        elif self.repeat is RepeatMode.PLAYLIST:
            self.current_index = len(self.items) - 1
        return self.current

    def cycle_repeat(self) -> RepeatMode:
        order = (RepeatMode.OFF, RepeatMode.TRACK, RepeatMode.PLAYLIST)
        self.repeat = order[(order.index(self.repeat) + 1) % len(order)]
        return self.repeat

    def to_dict(self, *, position_ms: int = 0) -> dict[str, Any]:
        return {
            "queue": list(self.items),
            "current_index": self.current_index,
            "position_ms": max(0, int(position_ms)),
            "shuffle": self.shuffle,
            "repeat": self.repeat.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlaybackQueue:
        items = [str(item) for item in data.get("queue", [])]
        index = int(data.get("current_index", -1))
        if not items:
            index = -1
        else:
            index = max(0, min(index, len(items) - 1))
        try:
            repeat = RepeatMode(data.get("repeat", RepeatMode.OFF.value))
        except ValueError:
            repeat = RepeatMode.OFF
        return cls(items, index, bool(data.get("shuffle", False)), repeat)
