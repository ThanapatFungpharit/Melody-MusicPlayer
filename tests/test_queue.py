from __future__ import annotations

import unittest
from unittest.mock import patch

from musicplayer.application.models import RepeatMode
from musicplayer.application.queue import PlaybackQueue


class PlaybackQueueTests(unittest.TestCase):
    def test_play_next_and_append_are_distinct(self) -> None:
        queue = PlaybackQueue()
        queue.replace(["a", "b"])
        queue.add_next("next")
        queue.add_last("last")
        self.assertEqual(queue.items, ["a", "next", "b", "last"])

    def test_move_preserves_current_track(self) -> None:
        queue = PlaybackQueue(["a", "b", "c"], current_index=1)
        queue.move(0, 2)
        self.assertEqual(queue.current, "b")

    def test_move_preserves_the_current_duplicate_instance(self) -> None:
        queue = PlaybackQueue(["a", "b", "a"], current_index=2)
        queue.move(0, 1)
        self.assertEqual(queue.items, ["b", "a", "a"])
        self.assertEqual(queue.current_index, 2)

    def test_repeat_track_and_playlist(self) -> None:
        queue = PlaybackQueue(["a", "b"], current_index=1, repeat=RepeatMode.TRACK)
        self.assertEqual(queue.next(automatic=True), "b")
        queue.repeat = RepeatMode.PLAYLIST
        self.assertEqual(queue.next(automatic=True), "a")

    def test_round_trip(self) -> None:
        original = PlaybackQueue(["a", "b"], 1, True, RepeatMode.PLAYLIST)
        restored = PlaybackQueue.from_dict(original.to_dict(position_ms=3456))
        self.assertEqual(restored.items, original.items)
        self.assertEqual(restored.current, "b")
        self.assertTrue(restored.shuffle)
        self.assertEqual(restored.repeat, RepeatMode.PLAYLIST)

    def test_shuffle_selects_without_building_a_choices_list(self) -> None:
        queue = PlaybackQueue(["a", "b", "c", "d"], current_index=1, shuffle=True)

        with patch(
            "musicplayer.application.queue.random.randrange", return_value=1
        ) as randrange:
            selected = queue.next()

        randrange.assert_called_once_with(3)
        self.assertEqual(selected, "c")

    def test_shuffle_history_has_a_fixed_memory_bound(self) -> None:
        queue = PlaybackQueue(["a", "b"], current_index=0, shuffle=True)

        for _ in range(300):
            queue.next()

        self.assertEqual(len(queue._shuffle_history), 250)


if __name__ == "__main__":
    unittest.main()
