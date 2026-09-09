import tempfile
import unittest
from pathlib import Path

from tools.check_architecture import violations


class ArchitectureTests(unittest.TestCase):
    def test_repository_boundaries(self):
        self.assertEqual(violations(), [])

    def test_reverse_relative_import_and_cycle_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "musicplayer/core").mkdir(parents=True)
            (root / "musicplayer/application").mkdir()
            (root / "musicplayer/core/bad.py").write_text(
                "from ..application import service"
            )
            (root / "musicplayer/application/service.py").write_text(
                "from ..core import bad"
            )
            errors = violations(root)
            self.assertTrue(any("reverses core" in error for error in errors))
            self.assertTrue(any("Import cycle" in error for error in errors))
