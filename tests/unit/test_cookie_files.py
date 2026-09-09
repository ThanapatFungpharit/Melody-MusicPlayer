from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from musicplayer.application.cookie_files import (
    CookieFileError,
    install_cookie_file,
    validate_cookie_file,
)

VALID_COOKIE_FILE = b"""# Netscape HTTP Cookie File
# This file was exported manually.
.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret
#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t2147483647\tHSID\tother
"""


class CookieFileTests(unittest.TestCase):
    def test_valid_netscape_file_is_normalized_and_counted(self) -> None:
        validated = validate_cookie_file(b"\xef\xbb\xbf" + VALID_COOKIE_FILE)

        self.assertEqual(validated.cookie_count, 2)
        self.assertTrue(validated.content.startswith(b"# Netscape HTTP Cookie File"))
        self.assertTrue(validated.content.endswith(b"\n"))

    def test_json_cookie_export_is_rejected(self) -> None:
        with self.assertRaisesRegex(CookieFileError, "Netscape cookies.txt"):
            validate_cookie_file(b'[{"domain": ".youtube.com"}]')

    def test_malformed_netscape_record_is_rejected(self) -> None:
        with self.assertRaisesRegex(CookieFileError, "line 2"):
            validate_cookie_file(
                b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\n"
            )

    def test_cookie_file_must_contain_at_least_one_cookie(self) -> None:
        with self.assertRaisesRegex(CookieFileError, "does not contain any cookies"):
            validate_cookie_file(b"# Netscape HTTP Cookie File\n# comments only\n")

    def test_validated_cookie_file_is_installed_atomically(self) -> None:
        validated = validate_cookie_file(VALID_COOKIE_FILE)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "private" / "cookies.txt"

            result = install_cookie_file(validated, destination)

            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), validated.content)


if __name__ == "__main__":
    unittest.main()
