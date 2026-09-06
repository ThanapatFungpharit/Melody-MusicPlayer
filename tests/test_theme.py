from __future__ import annotations

import unittest

import flet as ft

from musicplayer.ui.theme import card


class ThemeTests(unittest.TestCase):
    def test_card_forwards_control_key(self) -> None:
        result = card(ft.Text("Track"), key="playlist:0:track")

        self.assertEqual(result.key, "playlist:0:track")


if __name__ == "__main__":
    unittest.main()
