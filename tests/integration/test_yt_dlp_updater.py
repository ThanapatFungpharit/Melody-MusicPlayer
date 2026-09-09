from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from packaging.version import Version

from musicplayer.yt_dlp_updater import (
    RemoteRelease,
    WheelRecord,
    YtDlpUpdater,
)


class YtDlpUpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.imported_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "yt_dlp" or name.startswith("yt_dlp.")
        }
        for name in self.imported_modules:
            sys.modules.pop(name)
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.storage = root / "runtime"
        self.sources = root / "sources"
        self.sources.mkdir()

    def tearDown(self) -> None:
        storage_prefix = str(self.storage.resolve())
        sys.path[:] = [
            entry
            for entry in sys.path
            if not str(entry).casefold().startswith(storage_prefix.casefold())
        ]
        self.temporary_directory.cleanup()
        sys.modules.update(self.imported_modules)

    def test_downloads_only_newer_release_and_activates_verified_wheel(self) -> None:
        source = _make_wheel(self.sources, "2026.9.12")
        remote = _remote(source, "2026.9.12")
        downloads: list[str] = []

        def download(_url: str, destination: Path) -> None:
            downloads.append(destination.name)
            shutil.copyfile(source, destination)

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: remote,
            download=download,
        ).prepare()

        self.assertTrue(result.updated)
        self.assertEqual(result.active_source, "runtime")
        self.assertEqual(result.active_version, "2026.9.12")
        self.assertEqual(result.current_version, "2026.9.12")
        self.assertIsNone(result.previous_version)
        self.assertEqual(len(downloads), 1)
        self.assertEqual(Path(sys.path[0]), self.storage / source.name)
        self.assertEqual(
            json.loads((self.storage / "state.json").read_text()),
            {
                "schema": 2,
                "pending": None,
                "failed": None,
                "current": {
                    "filename": source.name,
                    "sha256": _sha256(source),
                    "version": "2026.9.12",
                },
                "previous": None,
            },
        )

    def test_does_not_download_when_remote_is_not_newer(self) -> None:
        current = _make_wheel(self.storage, "2026.9.12")
        _write_state(self.storage, _record(current, "2026.9.12"), None)
        download = Mock()

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: _remote(
                _make_wheel(self.sources, "2026.9.11"), "2026.9.11"
            ),
            download=download,
        ).prepare()

        self.assertFalse(result.updated)
        self.assertEqual(result.active_version, "2026.9.12")
        download.assert_not_called()

    def test_current_corruption_rolls_back_to_previous(self) -> None:
        current = self.storage / "yt_dlp-2026.9.12-py3-none-any.whl"
        self.storage.mkdir(parents=True)
        current.write_bytes(b"not a wheel")
        trusted_hash = _sha256(current)
        previous = _make_wheel(self.storage, "2026.9.08")
        _write_state(
            self.storage,
            WheelRecord(Version("2026.9.12"), current.name, trusted_hash),
            _record(previous, "2026.9.08"),
        )
        # The current hash in state is intentionally wrong for the bytes in the
        # package after the state transaction.
        current.write_bytes(b"corrupted after state commit")

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: None,
        ).prepare()

        self.assertTrue(result.rolled_back)
        self.assertEqual(result.active_version, "2026.9.8")
        self.assertEqual(result.current_version, "2026.9.8")
        self.assertIsNone(result.previous_version)
        self.assertFalse(current.exists())
        self.assertEqual(Path(sys.path[0]), previous)

    def test_both_corrupt_runtime_packages_fall_back_and_reset_state(self) -> None:
        current = self.storage / "yt_dlp-2026.9.12-py3-none-any.whl"
        previous = self.storage / "yt_dlp-2026.9.08-py3-none-any.whl"
        self.storage.mkdir(parents=True)
        current.write_bytes(b"bad current")
        previous.write_bytes(b"bad previous")
        _write_state(
            self.storage,
            WheelRecord(Version("2026.9.12"), current.name, "0" * 64),
            WheelRecord(Version("2026.9.08"), previous.name, "1" * 64),
        )

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: None,
        ).prepare()

        self.assertEqual(result.active_source, "bundled")
        self.assertEqual(result.active_version, "2026.8.19")
        self.assertTrue(result.recovered)
        self.assertFalse(current.exists())
        self.assertFalse(previous.exists())
        state = json.loads((self.storage / "state.json").read_text())
        self.assertIsNone(state["current"])
        self.assertIsNone(state["previous"])
        self.assertIsNotNone(state["failed"])

    def test_corrupt_state_does_not_trust_unreferenced_wheels(self) -> None:
        _make_wheel(self.storage, "2026.9.12")
        (self.storage / "state.json").write_text('{"schema": 1,', encoding="utf-8")
        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _: None,
        ).prepare()
        self.assertTrue(result.recovered)
        self.assertEqual(result.active_source, "bundled")
        self.assertEqual(list(self.storage.glob("*.whl")), [])

    def test_missing_current_uses_previous_and_repairs_state(self) -> None:
        previous = _make_wheel(self.storage, "2026.9.08")
        missing = WheelRecord(
            Version("2026.9.12"),
            "yt_dlp-2026.9.12-py3-none-any.whl",
            "a" * 64,
        )
        _write_state(self.storage, missing, _record(previous, "2026.9.08"))

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: None,
        ).prepare()

        self.assertTrue(result.rolled_back)
        self.assertEqual(result.current_version, "2026.9.8")
        self.assertIsNone(result.previous_version)
        self.assertEqual(
            json.loads((self.storage / "state.json").read_text())["previous"], None
        )

    def test_hash_mismatch_and_zip_corruption_are_removed(self) -> None:
        hash_mismatch = self.storage / "yt_dlp-2026.9.12-py3-none-any.whl"
        zip_corruption = self.storage / "yt_dlp-2026.9.11-py3-none-any.whl"
        self.storage.mkdir(parents=True)
        hash_mismatch.write_bytes(b"not trusted")
        zip_corruption.write_bytes(b"not a zip")
        _write_state(
            self.storage,
            WheelRecord(Version("2026.9.12"), hash_mismatch.name, "f" * 64),
            WheelRecord(
                Version("2026.9.11"), zip_corruption.name, _sha256(zip_corruption)
            ),
        )

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: None,
        ).prepare()

        self.assertEqual(result.active_source, "bundled")
        self.assertFalse(hash_mismatch.exists())
        self.assertFalse(zip_corruption.exists())

    def test_stale_temporary_files_are_removed_on_every_launch(self) -> None:
        current = _make_wheel(self.storage, "2026.9.12")
        _write_state(self.storage, _record(current, "2026.9.12"), None)
        stale_tmp = self.storage / ".yt_dlp-2026.9.13.whl.download.tmp"
        stale_part = self.storage / "state.json.part"
        stale_tmp.write_bytes(b"partial")
        stale_part.write_bytes(b"partial")

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: None,
        ).prepare()

        self.assertTrue(result.recovered)
        self.assertFalse(stale_tmp.exists())
        self.assertFalse(stale_part.exists())
        self.assertTrue(current.exists())

    def test_offline_recovery_keeps_valid_current_runtime(self) -> None:
        current = _make_wheel(self.storage, "2026.9.12")
        _write_state(self.storage, _record(current, "2026.9.12"), None)

        def offline(_url: str) -> None:
            raise OSError("offline")

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=offline,
        ).prepare()

        self.assertEqual(result.active_source, "runtime")
        self.assertEqual(result.active_version, "2026.9.12")
        self.assertTrue(any("offline" in error for error in result.errors))

    def test_bundled_upgrade_removes_runtime_versions_at_or_below_bundle(self) -> None:
        old = _make_wheel(self.storage, "2026.9.08")
        _write_state(self.storage, _record(old, "2026.9.08"), None)

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.9.08",
            fetch_remote=lambda _url: None,
        ).prepare()

        self.assertEqual(result.active_source, "bundled")
        self.assertFalse(old.exists())
        self.assertEqual(result.current_version, None)

    def test_failed_download_keeps_old_current_and_removes_temp(self) -> None:
        current = _make_wheel(self.storage, "2026.9.08")
        _write_state(self.storage, _record(current, "2026.9.08"), None)
        remote_source = _make_wheel(self.sources, "2026.9.12")
        remote = _remote(remote_source, "2026.9.12")

        def interrupted(_url: str, destination: Path) -> None:
            destination.write_bytes(b"partial")
            raise OSError("battery loss")

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: remote,
            download=interrupted,
        ).prepare()

        self.assertFalse(result.updated)
        self.assertEqual(result.active_version, "2026.9.8")
        self.assertTrue(current.exists())
        self.assertEqual(list(self.storage.glob("*.tmp")), [])
        self.assertTrue(any("battery loss" in error for error in result.errors))

    def test_hash_mismatched_download_is_not_activated(self) -> None:
        current = _make_wheel(self.storage, "2026.9.08")
        _write_state(self.storage, _record(current, "2026.9.08"), None)
        expected = _make_wheel(self.sources, "2026.9.12")
        wrong = _make_wheel(self.sources, "2026.9.13")
        remote = _remote(expected, "2026.9.12")

        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: remote,
            download=lambda _url, destination: shutil.copyfile(wrong, destination),
        ).prepare()

        self.assertEqual(result.active_version, "2026.9.8")
        self.assertFalse(result.updated)
        self.assertTrue(current.exists())
        self.assertFalse((self.storage / expected.name).exists())

    def test_atomic_replacement_uses_os_replace_for_wheel_and_state(self) -> None:
        source = _make_wheel(self.sources, "2026.9.12")
        remote = _remote(source, "2026.9.12")

        with patch(
            "musicplayer.yt_dlp_updater.os.replace", wraps=os.replace
        ) as replace:
            YtDlpUpdater(
                storage_directory=self.storage,
                bundled_version="2026.8.19",
                fetch_remote=lambda _url: remote,
                download=lambda _url, destination: shutil.copyfile(source, destination),
            ).prepare()

        calls = [call.args for call in replace.call_args_list]
        self.assertTrue(any(str(args[0]).endswith(".tmp") for args in calls))
        self.assertTrue(any(str(args[1]).endswith(source.name) for args in calls))
        self.assertTrue(any(str(args[1]).endswith("state.json") for args in calls))

    def test_repeated_recovery_runs_converge_to_two_valid_runtime_versions(
        self,
    ) -> None:
        current = self.storage / "yt_dlp-2026.9.12-py3-none-any.whl"
        previous = self.storage / "yt_dlp-2026.9.08-py3-none-any.whl"
        self.storage.mkdir(parents=True)
        current.write_bytes(b"corrupt")
        previous.write_bytes(b"corrupt")
        _write_state(
            self.storage,
            WheelRecord(Version("2026.9.12"), current.name, "0" * 64),
            WheelRecord(Version("2026.9.08"), previous.name, "1" * 64),
        )

        first = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: None,
        ).prepare()
        second = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: None,
        ).prepare()

        self.assertTrue(first.recovered)
        self.assertFalse(second.recovered)
        self.assertEqual(second.active_source, "bundled")
        self.assertEqual(list(self.storage.glob("*.whl")), [])

    def test_only_current_and_previous_runtime_wheels_are_retained(self) -> None:
        wheels = [
            _make_wheel(self.storage, version)
            for version in ("2026.9.08", "2026.9.10", "2026.9.12")
        ]

        _write_state(
            self.storage,
            _record(wheels[2], "2026.9.12"),
            _record(wheels[1], "2026.9.10"),
        )
        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _url: None,
        ).prepare()

        retained = sorted(path.name for path in self.storage.glob("*.whl"))
        self.assertEqual(retained, [wheels[1].name, wheels[2].name])
        self.assertEqual(result.current_version, "2026.9.12")
        self.assertEqual(result.previous_version, "2026.9.10")

    def test_validated_transaction_is_completed_after_restart(self):
        current = _make_wheel(self.storage, "2026.9.8")
        pending = _make_wheel(self.storage, "2026.9.12")
        _write_state(self.storage, _record(current, "2026.9.8"), None)
        state_path = self.storage / "state.json"
        state = json.loads(state_path.read_text())
        state.update(
            schema=2,
            pending={
                "status": "validated",
                "wheel": _record(pending, "2026.9.12").as_json(),
            },
        )
        state_path.write_text(json.dumps(state))
        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _: None,
        ).prepare()
        self.assertEqual((result.current, result.previous), ("2026.9.12", "2026.9.8"))
        self.assertIsNone(json.loads(state_path.read_text())["pending"])

    def test_downloading_transaction_never_promotes_existing_candidate(self):
        current = _make_wheel(self.storage, "2026.9.8")
        pending = _make_wheel(self.storage, "2026.9.12")
        _write_state(self.storage, _record(current, "2026.9.8"), None)
        state_path = self.storage / "state.json"
        state = json.loads(state_path.read_text())
        state.update(
            schema=2,
            pending={
                "status": "downloading",
                "wheel": _record(pending, "2026.9.12").as_json(),
            },
        )
        state_path.write_text(json.dumps(state))
        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _: None,
        ).prepare()
        self.assertEqual(result.current, "2026.9.8")
        self.assertFalse(pending.exists())
        self.assertIsNotNone(json.loads(state_path.read_text())["failed"])

    def test_healthy_zip_with_broken_import_rolls_back(self):
        old = _make_wheel(self.storage, "2026.9.8")
        broken = _make_wheel(self.storage, "2026.9.12")
        with zipfile.ZipFile(broken) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files["yt_dlp/__init__.py"] = b"raise RuntimeError('incompatible runtime')"
        with zipfile.ZipFile(broken, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        _write_state(
            self.storage, _record(broken, "2026.9.12"), _record(old, "2026.9.8")
        )
        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _: None,
        ).prepare()
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.current, "2026.9.8")
        self.assertNotIn("yt_dlp", sys.modules)

    def test_release_outside_compatibility_window_is_not_downloaded(self):
        future = _make_wheel(self.sources, "2027.1.1")
        download = Mock()
        result = YtDlpUpdater(
            storage_directory=self.storage,
            bundled_version="2026.8.19",
            fetch_remote=lambda _: _remote(future, "2027.1.1"),
            download=download,
        ).prepare()
        self.assertEqual(result.active_source, "bundled")
        download.assert_not_called()

    def test_failed_state_commit_never_switches_the_running_package(self):
        old = _make_wheel(self.storage, "2026.9.8")
        source = _make_wheel(self.sources, "2026.9.12")
        _write_state(self.storage, _record(old, "2026.9.8"), None)
        replace = os.replace

        def fail_new_state(source_path, destination):
            if Path(destination).name == "state.json":
                state = json.loads(Path(source_path).read_text())
                if state.get("current", {}).get("version") == "2026.9.12":
                    raise OSError("disk full")
            replace(source_path, destination)

        with patch("musicplayer.yt_dlp_updater.os.replace", side_effect=fail_new_state):
            result = YtDlpUpdater(
                storage_directory=self.storage,
                bundled_version="2026.8.19",
                fetch_remote=lambda _: _remote(source, "2026.9.12"),
                download=lambda _, destination: shutil.copyfile(source, destination),
            ).prepare()
        self.assertFalse(result.updated)
        self.assertEqual(result.active, "2026.9.8")
        self.assertEqual(Path(sys.path[0]), old)
        self.assertTrue(old.is_file())

    def test_selection_cannot_change_after_first_import(self):
        from types import ModuleType

        with (
            patch.dict(sys.modules, {"yt_dlp": ModuleType("yt_dlp")}),
            self.assertRaisesRegex(RuntimeError, "first import"),
        ):
            YtDlpUpdater(
                storage_directory=self.storage,
                bundled_version="2026.8.19",
                fetch_remote=lambda _: None,
            ).prepare()

    def test_embedded_desktop_probes_without_relaunching_the_app(self):
        source = _make_wheel(self.storage, "2026.9.12")
        _write_state(self.storage, _record(source, "2026.9.12"), None)
        original_argv = sys.argv[:]
        with (
            patch.object(sys, "executable", "C:/Program Files/Melody/melody.exe"),
            patch("musicplayer.yt_dlp_updater.subprocess.run") as run,
        ):
            result = YtDlpUpdater(
                storage_directory=self.storage,
                bundled_version="2026.8.19",
                fetch_remote=lambda _: None,
            ).prepare()
        self.assertEqual(result.current, "2026.9.12")
        self.assertNotIn("yt_dlp", sys.modules)
        self.assertEqual(sys.argv, original_argv)
        run.assert_not_called()


def _make_wheel(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"yt_dlp-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "yt_dlp/__init__.py",
            """
class CookieLoadError(Exception): pass
class YoutubeDL:
    def __init__(self, options): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def extract_info(self): pass
    def download(self): pass
""",
        )
        archive.writestr("yt_dlp/version.py", f"__version__ = {version!r}\n")
        archive.writestr(
            f"yt_dlp-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: yt-dlp\nVersion: {version}\nRequires-Python: >=3.12\n",
        )
        archive.writestr(
            f"yt_dlp-{version}.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(path: Path, version: str) -> WheelRecord:
    return WheelRecord(Version(version), path.name, _sha256(path))


def _remote(path: Path, version: str) -> RemoteRelease:
    return RemoteRelease(
        Version(version), path.name, "https://example.test/yt-dlp.whl", _sha256(path)
    )


def _write_state(
    storage: Path,
    current: WheelRecord | None,
    previous: WheelRecord | None,
) -> None:
    storage.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "current": current.as_json() if current else None,
        "previous": previous.as_json() if previous else None,
    }
    (storage / "state.json").write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
