"""Crash-safe, dependency-free-at-runtime updates for the bundled yt-dlp.

The application package is intentionally never modified.  A verified pure
Python wheel is placed on ``sys.path`` ahead of the packaged distribution, so
the packaged yt-dlp remains an immutable last-resort fallback on every
platform supported by Flet.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import logging
import os
import re
import subprocess
import sys
import threading
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from musicplayer.core.storage import exclusive_file_lock

from .platform_runtime import application_data_directory

logger = logging.getLogger(__name__)

_SCHEMA = 2
_ALLOWED_RUNTIME = SpecifierSet(">=2026.8.19,<2027")
_MAX_WHEEL_BYTES = 32 * 1024 * 1024
_MAX_EXPANDED_BYTES = 128 * 1024 * 1024
_PREPARE_LOCK = threading.RLock()
_STARTUP_RESULT: UpdateResult | None = None
_SESSION = ExitStack()
_DISTRIBUTION = "yt-dlp"
_WHEEL_PREFIX = "yt_dlp-"
_WHEEL_SUFFIX = "-py3-none-any.whl"
_WHEEL_NAME = re.compile(
    r"^yt[_-]dlp-(?P<version>[^-]+)-py3-none-any\.whl$", re.IGNORECASE
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_DEFAULT_METADATA_URL = "https://pypi.org/pypi/yt-dlp/json"
_DEFAULT_TIMEOUT = 12.0


@dataclass(frozen=True)
class WheelRecord:
    """Trusted metadata for one installed runtime wheel."""

    version: Version
    filename: str
    sha256: str

    def as_json(self) -> dict[str, str]:
        return {
            "version": str(self.version),
            "filename": self.filename,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RemoteRelease:
    version: Version
    filename: str
    url: str
    sha256: str


@dataclass(frozen=True)
class UpdateResult:
    """Non-fatal startup diagnostics returned by :func:`prepare_yt_dlp`."""

    active_version: str
    bundled_version: str
    current_version: str | None
    previous_version: str | None
    remote_version: str | None
    updated: bool
    recovered: bool
    rolled_back: bool
    active_source: str
    errors: tuple[str, ...] = ()

    @property
    def active(self) -> str:
        return self.active_version

    @property
    def bundled(self) -> str:
        return self.bundled_version

    @property
    def current(self) -> str | None:
        return self.current_version

    @property
    def previous(self) -> str | None:
        return self.previous_version

    @property
    def update_occurred(self) -> bool:
        return self.updated

    @property
    def recovery_occurred(self) -> bool:
        return self.recovered

    @property
    def rollback_occurred(self) -> bool:
        return self.rolled_back


DownloadFunction = Callable[[str, Path], object]
RemoteFunction = Callable[[str], RemoteRelease | None]


class YtDlpUpdater:
    """Own the on-disk transaction and activation policy for yt-dlp."""

    def __init__(
        self,
        *,
        storage_directory: str | Path | None = None,
        bundled_version: str | Version | None = None,
        metadata_url: str = _DEFAULT_METADATA_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        download: DownloadFunction | None = None,
        fetch_remote: RemoteFunction | None = None,
    ) -> None:
        self._storage_directory = (
            Path(storage_directory) if storage_directory is not None else None
        )
        self._bundled_version_override = (
            _coerce_version(bundled_version) if bundled_version is not None else None
        )
        self._metadata_url = metadata_url
        self._timeout = timeout
        self._download_function = download
        self._fetch_remote_function = fetch_remote
        self._errors: list[str] = []
        self._recovered = False
        self._rolled_back = False
        self._updated = False
        self._pending: dict[str, Any] | None = None
        self._failed: dict[str, Any] | None = None
        self._install_retryable = False

    @property
    def storage_directory(self) -> Path | None:
        return self._storage_directory

    def prepare(self) -> UpdateResult:
        with _PREPARE_LOCK:
            if "yt_dlp" in sys.modules:
                raise RuntimeError(
                    "yt-dlp selection must finish before its first import; restart to update"
                )
            return self._prepare()

    def _prepare(self) -> UpdateResult:
        """Recover, optionally update, and activate without raising failures.

        The outer exception boundary is deliberate: update failures are
        diagnostics, never a reason to stop the application from reaching its
        bundled fallback.
        """

        self._errors = []
        self._recovered = False
        self._rolled_back = False
        self._updated = False

        try:
            storage = self._storage()
        except Exception as error:  # noqa: BLE001 - updater failures are non-fatal
            storage = None
            self._record_error(f"Runtime storage unavailable: {error}")

        bundled = self._bundled_version(storage)
        if bundled is None:
            message = "Could not determine the bundled yt-dlp version"
            self._record_error(message)
            self._deactivate_runtime_wheels(storage)
            return self._result(
                active_version="unknown",
                bundled_version="unknown",
                current=None,
                previous=None,
                remote=None,
                source="bundled",
            )

        if storage is None:
            self._deactivate_runtime_wheels(None)
            return self._result(
                active_version=str(bundled),
                bundled_version=str(bundled),
                current=None,
                previous=None,
                remote=None,
                source="bundled",
            )

        try:
            with exclusive_file_lock(storage / "runtime.lock"):
                return self._prepare_locked(storage, bundled)
        except Exception as error:  # noqa: BLE001 - validation/update failures preserve the bundle
            self._record_error(f"yt-dlp updater failure: {error}")
            self._deactivate_runtime_wheels(storage)
            return self._result(
                active_version=str(bundled),
                bundled_version=str(bundled),
                current=None,
                previous=None,
                remote=None,
                source="bundled",
            )

    def _prepare_locked(self, storage: Path, bundled: Version) -> UpdateResult:
        self._deactivate_runtime_wheels(storage)
        self._remove_temporary_files(storage)
        state, valid = self._read_state(storage)
        self._pending = state.get("pending") if valid else None
        self._failed = state.get("failed") if valid else None
        records, current, previous = self._recover_records(
            storage, bundled, state, valid
        )
        pending = self._pending
        if pending:
            # Only an explicitly validated transaction may survive the rename /
            # state-commit crash window. A directory listing never grants trust.
            candidate = _record_for_state(records, pending.get("wheel"))
            if pending.get("status") == "validated" and candidate is not None:
                previous = current
                current = candidate
            else:
                self._failed = {
                    "wheel": pending["wheel"],
                    "reason": "Interrupted update",
                    "retryable": True,
                }
            self._pending = None
            self._recovered = True
        if not valid or not _same_roles(state, current, previous):
            self._recovered = True
        durable = self._write_state(storage, current, previous)
        active = current
        remote = self._remote_release()
        if remote and remote.version > (active.version if active else bundled):
            failed_wheel = self._failed.get("wheel") if self._failed else None
            remote_record = WheelRecord(remote.version, remote.filename, remote.sha256)
            if failed_wheel == remote_record.as_json() and not (self._failed or {}).get(
                "retryable", False
            ):
                self._record_error(
                    f"Skipping previously failed yt-dlp {remote.version}"
                )
            elif durable:
                self._pending = {
                    "status": "downloading",
                    "wheel": remote_record.as_json(),
                }
                if self._write_state(storage, current, previous):
                    installed = self._install_remote(storage, bundled, remote, active)
                    if installed is not None:
                        self._pending = {
                            "status": "validated",
                            "wheel": installed.as_json(),
                        }
                        if self._write_state(storage, current, previous):
                            self._pending = None
                            if self._write_state(storage, installed, current):
                                previous, current, active = (
                                    current,
                                    installed,
                                    installed,
                                )
                                self._updated = True
                    if not self._updated:
                        self._failed = {
                            "wheel": remote_record.as_json(),
                            "reason": "Installation failed",
                            "retryable": installed is not None
                            or self._install_retryable,
                        }
                        self._pending = None
                        self._write_state(storage, current, previous)
        if active:
            self._activate(storage / active.filename)
        if self._write_state(storage, current, previous):
            self._cleanup_obsolete(storage, bundled, current, previous)
        return self._result(
            active_version=str(active.version if active else bundled),
            bundled_version=str(bundled),
            current=current,
            previous=previous,
            remote=remote,
            source="runtime" if active else "bundled",
        )

    def _bundled_version(self, storage: Path | None) -> Version | None:
        if self._bundled_version_override is not None:
            return self._bundled_version_override
        # A caller may prepare twice in one process during recovery tests or a
        # host restart handoff.  Remove an earlier runtime wheel before asking
        # importlib.metadata, otherwise it could be mistaken for the bundle.
        self._deactivate_runtime_wheels(storage)
        try:
            return _coerce_version(importlib.metadata.version(_DISTRIBUTION))
        except (
            importlib.metadata.PackageNotFoundError,
            InvalidVersion,
            OSError,
            TypeError,
        ):
            return None

    def _storage(self) -> Path:
        if self._storage_directory is None:
            self._storage_directory = (
                application_data_directory(create=True) / "yt-dlp-runtime"
            )
        return self._storage_directory

    def _read_state(self, storage: Path) -> tuple[dict[str, Any], bool]:
        path = storage / "state.json"
        try:
            with path.open("r", encoding="utf-8") as stream:
                value = json.load(stream)
        except FileNotFoundError:
            logger.info("yt-dlp runtime state is missing; rebuilding it")
            self._recovered = True
            return {}, False
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("yt-dlp runtime state is invalid: %s", error)
            self._recovered = True
            return {}, False

        if not isinstance(value, dict) or value.get("schema") not in {1, _SCHEMA}:
            logger.warning("yt-dlp runtime state has an unsupported structure")
            self._recovered = True
            return {}, False
        if not _entry_shape(value.get("current")) or not _entry_shape(
            value.get("previous")
        ):
            logger.warning("yt-dlp runtime state contains invalid package metadata")
            self._recovered = True
            return {}, False
        current = value.get("current")
        previous = value.get("previous")
        if (
            isinstance(current, dict)
            and isinstance(previous, dict)
            and current["filename"] == previous["filename"]
        ):
            logger.warning("yt-dlp runtime state refers to one wheel twice")
            self._recovered = True
            return {}, False
        for key in ("pending", "failed"):
            entry = value.get(key)
            if entry is not None and (
                not isinstance(entry, dict)
                or not isinstance(entry.get("wheel"), dict)
                or not _entry_shape(entry["wheel"])
                or (
                    key == "pending"
                    and entry.get("status") not in {"downloading", "validated"}
                )
            ):
                return {}, False
        return value, True

    def _recover_records(
        self,
        storage: Path,
        bundled: Version,
        state: Mapping[str, Any],
        state_was_valid: bool,
    ) -> tuple[dict[str, WheelRecord], WheelRecord | None, WheelRecord | None]:
        state_records = _state_records(state)
        records: dict[str, WheelRecord] = {}
        for filename, expected in state_records.items():
            record = self._validate_wheel(
                storage / filename, bundled, expected=expected
            )
            if record is not None:
                records[filename] = record
            else:
                self._failed = {
                    "wheel": expected.as_json(),
                    "reason": "Integrity or compatibility check failed",
                }
                self._recovered = True
        current = _record_for_state(records, state.get("current"))
        previous = _record_for_state(records, state.get("previous"))
        if state.get("current") and current is None and previous:
            current, previous = previous, None
            self._rolled_back = True
        if current is None:
            previous = None
        return records, current, previous

    def _validate_wheel(
        self,
        path: Path,
        bundled: Version,
        *,
        expected: WheelRecord | None,
        filename: str | None = None,
    ) -> WheelRecord | None:
        wheel_name = filename or path.name
        parsed = _parse_wheel_name(wheel_name)
        if parsed is None:
            return None
        version, parsed_filename = parsed
        if version <= bundled or version not in _ALLOWED_RUNTIME:
            self._recovered = True
            return None
        if expected is not None and (
            expected.filename != parsed_filename or expected.version != version
        ):
            self._recovered = True
            logger.warning("yt-dlp state metadata does not match %s", wheel_name)
            return None
        try:
            if path.is_symlink() or path.stat().st_size > _MAX_WHEEL_BYTES:
                raise ValueError("Runtime wheel is a symlink or exceeds the size limit")
            if expected is None:
                raise ValueError("Wheel has no trusted checksum record")
            digest = _sha256(path)
            if expected is not None and digest.casefold() != expected.sha256.casefold():
                raise ValueError("SHA-256 does not match trusted runtime metadata")
            with zipfile.ZipFile(path) as archive:
                _validate_archive(archive, version)
                if archive.testzip() is not None:
                    raise ValueError("ZIP CRC validation failed")
            _probe_wheel(path, version, timeout=self._timeout)
        except Exception as error:  # noqa: BLE001 - validation/update failures preserve the bundle
            logger.warning(
                "yt-dlp package failed integrity validation: %s (%s)", path, error
            )
            self._recovered = True
            return None
        return WheelRecord(version, parsed_filename, digest.lower())

    def _remote_release(self) -> RemoteRelease | None:
        try:
            if self._fetch_remote_function is not None:
                remote = self._fetch_remote_function(self._metadata_url)
            else:
                remote = _fetch_remote_release(self._metadata_url, self._timeout)
            if remote is not None:
                record = WheelRecord(remote.version, remote.filename, remote.sha256)
                if (
                    not _entry_shape(record.as_json())
                    or remote.version not in _ALLOWED_RUNTIME
                ):
                    raise ValueError(
                        "Remote release is outside Melody's runtime compatibility policy"
                    )
            return remote
        except Exception as error:  # noqa: BLE001 - install failures are non-fatal
            self._record_error(f"Could not check for yt-dlp updates: {error}")
            logger.warning("Could not check for yt-dlp updates: %s", error)
            return None

    def _install_remote(
        self,
        storage: Path,
        bundled: Version,
        remote: RemoteRelease,
        active: WheelRecord | None,
    ) -> WheelRecord | None:
        self._install_retryable = False
        if remote.version <= bundled:
            return None
        final = storage / remote.filename
        temporary = storage / f".{remote.filename}.{uuid.uuid4().hex}.tmp"
        logger.info("Downloading replacement yt-dlp %s", remote.version)
        try:
            if self._download_function is not None:
                self._download_function(remote.url, temporary)
            else:
                _download_file(remote.url, temporary, self._timeout)
            actual = self._validate_wheel(
                temporary,
                bundled,
                expected=WheelRecord(remote.version, remote.filename, remote.sha256),
                filename=remote.filename,
            )
            if actual is None or actual.version != remote.version:
                raise ValueError("downloaded wheel filename or version is invalid")
            if actual.sha256 != remote.sha256:
                raise ValueError("downloaded wheel SHA-256 does not match PyPI")
            with temporary.open("rb+") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, final)
            _fsync_directory(storage)
            installed = WheelRecord(remote.version, remote.filename, remote.sha256)
            logger.info("Downloaded and verified yt-dlp %s", installed.version)
            return installed
        except Exception as error:  # noqa: BLE001 - install failures are non-fatal
            self._install_retryable = isinstance(error, OSError)
            self._record_error(f"Could not install yt-dlp {remote.version}: {error}")
            logger.warning("Could not install yt-dlp %s: %s", remote.version, error)
            return None
        finally:
            _unlink(temporary)

    def _write_state(
        self,
        storage: Path,
        current: WheelRecord | None,
        previous: WheelRecord | None,
    ) -> bool:
        payload = {
            "schema": _SCHEMA,
            "pending": self._pending,
            "failed": self._failed,
            "current": current.as_json() if current else None,
            "previous": previous.as_json() if previous else None,
        }
        temporary = storage / "state.json.tmp"
        try:
            encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2)
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, storage / "state.json")
            _fsync_directory(storage)
            return True
        except OSError as error:
            self._record_error(f"Could not persist yt-dlp runtime state: {error}")
            logger.warning("Could not persist yt-dlp runtime state: %s", error)
            return False
        finally:
            _unlink(temporary)

    def _cleanup_obsolete(
        self,
        storage: Path,
        bundled: Version,
        current: WheelRecord | None,
        previous: WheelRecord | None,
    ) -> None:
        keep = {record.filename for record in (current, previous) if record}
        for path in storage.iterdir():
            if (
                path.name in {"state.json", "runtime.lock", "session.lock"}
                or path.name in keep
            ):
                continue
            if path.suffix.casefold() == ".whl" or _looks_like_runtime_wheel(path):
                parsed = _parse_wheel_name(path.name)
                if parsed is not None and parsed[0] <= bundled:
                    logger.info("Removing obsolete bundled-era yt-dlp %s", path.name)
                else:
                    logger.info("Removing obsolete yt-dlp runtime file %s", path.name)
                _unlink(path)
            elif path.name.endswith((".tmp", ".part")):
                _unlink(path)

    def _remove_temporary_files(self, storage: Path) -> None:
        for path in storage.iterdir():
            if path.name.endswith((".tmp", ".part")):
                logger.info("Removing stale yt-dlp temporary file %s", path.name)
                self._recovered = True
                _unlink(path)

    def _remove_package(self, path: Path) -> None:
        logger.warning("Removing corrupted runtime package %s", path.name)
        _unlink(path)

    def _activate(self, path: Path) -> None:
        self._deactivate_runtime_wheels(path.parent)
        sys.path.insert(0, str(path))
        importlib.invalidate_caches()
        logger.info("Activated runtime yt-dlp %s", path.name)

    def _deactivate_runtime_wheels(self, storage: Path | None) -> None:
        if storage is None:
            return
        runtime_paths: set[str] = set()
        try:
            runtime_paths = {
                str(path.resolve()) for path in storage.glob("*.whl") if path.is_file()
            }
        except OSError:
            pass
        retained: list[str] = []
        for entry in sys.path:
            try:
                resolved = str(Path(entry).resolve()) if entry else ""
            except (OSError, ValueError):
                resolved = ""
            if resolved in runtime_paths or (
                resolved
                and Path(resolved).parent == storage.resolve()
                and Path(resolved).suffix == ".whl"
            ):
                continue
            retained.append(entry)
        sys.path[:] = retained
        importlib.invalidate_caches()

    def _record_error(self, message: str) -> None:
        self._errors.append(message)

    def _result(
        self,
        *,
        active_version: str,
        bundled_version: str,
        current: WheelRecord | None,
        previous: WheelRecord | None,
        remote: RemoteRelease | None,
        source: str,
    ) -> UpdateResult:
        return UpdateResult(
            active_version=active_version,
            bundled_version=bundled_version,
            current_version=str(current.version) if current else None,
            previous_version=str(previous.version) if previous else None,
            remote_version=str(remote.version) if remote else None,
            updated=self._updated,
            recovered=self._recovered,
            rolled_back=self._rolled_back,
            active_source=source,
            errors=tuple(self._errors),
        )


async def prepare_yt_dlp(
    *,
    storage_directory: str | Path | None = None,
    bundled_version: str | Version | None = None,
    metadata_url: str = _DEFAULT_METADATA_URL,
    timeout: float = _DEFAULT_TIMEOUT,
    download: DownloadFunction | None = None,
    fetch_remote: RemoteFunction | None = None,
) -> UpdateResult:
    """Prepare yt-dlp before its first import.

    Blocking filesystem and network work runs off the event loop.  The
    updater catches ordinary failures and always returns diagnostics, leaving
    callers free to continue with the immutable bundled package.
    """

    updater = YtDlpUpdater(
        storage_directory=storage_directory,
        bundled_version=bundled_version,
        metadata_url=metadata_url,
        timeout=timeout,
        download=download,
        fetch_remote=fetch_remote,
    )

    def prepare_once() -> UpdateResult:
        global _STARTUP_RESULT
        with _PREPARE_LOCK:
            if _STARTUP_RESULT is not None:
                return _STARTUP_RESULT
            try:
                storage = updater._storage()
                # Hold for the process lifetime so another running application
                # cannot prune a wheel that this interpreter may still import.
                _SESSION.enter_context(exclusive_file_lock(storage / "session.lock"))
            except OSError as error:
                bundled = updater._bundled_version(updater.storage_directory)
                updater._record_error(
                    f"Runtime cache is in use or unavailable: {error}"
                )
                _STARTUP_RESULT = updater._result(
                    active_version=str(bundled or "unknown"),
                    bundled_version=str(bundled or "unknown"),
                    current=None,
                    previous=None,
                    remote=None,
                    source="bundled",
                )
                return _STARTUP_RESULT
            _STARTUP_RESULT = updater.prepare()
            return _STARTUP_RESULT

    return await asyncio.to_thread(prepare_once)


def _coerce_version(value: str | Version) -> Version:
    return value if isinstance(value, Version) else Version(str(value))


def _validate_archive(archive: zipfile.ZipFile, version: Version) -> None:
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if (
        len(set(names)) != len(names)
        or sum(entry.file_size for entry in entries) > _MAX_EXPANDED_BYTES
    ):
        raise ValueError("Wheel contains duplicate members or excessive expanded data")
    for name in names:
        if name.startswith("/") or "\\" in name or ".." in name.split("/"):
            raise ValueError("Wheel contains an unsafe path")
    if not {"yt_dlp/__init__.py", "yt_dlp/version.py"}.issubset(names):
        raise ValueError("Wheel is missing yt-dlp's package or version module")
    metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
    if len(metadata_names) != 1:
        raise ValueError("Wheel must contain exactly one distribution metadata record")
    metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
    if (
        str(metadata.get("Name", "")).replace("_", "-").lower() != "yt-dlp"
        or Version(str(metadata.get("Version", ""))) != version
    ):
        raise ValueError(
            "Wheel metadata does not match the selected distribution/version"
        )
    python_constraint = str(metadata.get("Requires-Python", ""))
    if python_constraint and Version(
        ".".join(map(str, sys.version_info[:3]))
    ) not in SpecifierSet(python_constraint):
        raise ValueError("Wheel requires an incompatible Python version")
    for value in metadata.get_all("Requires-Dist", []):
        requirement = Requirement(str(value))
        if requirement.marker is not None and not requirement.marker.evaluate(
            {"extra": ""}
        ):
            continue
        installed = Version(importlib.metadata.version(requirement.name))
        if installed not in requirement.specifier:
            raise ValueError(
                f"Wheel requires an incompatible dependency: {requirement.name}"
            )


_PROBE = """
import sys
from pathlib import Path
from packaging.version import Version
wheel_path, expected_version = globals().get('probe_arguments') or sys.argv[1:]
sys.path.insert(0, wheel_path)
import yt_dlp
from yt_dlp.version import __version__
if not str(Path(yt_dlp.__file__)).startswith(str(Path(wheel_path))):
    raise ValueError('Wrong package imported')
if Version(__version__) != Version(expected_version):
    raise ValueError('Runtime version mismatch')
if not callable(yt_dlp.YoutubeDL):
    raise ValueError('Missing YoutubeDL API')
if not issubclass(yt_dlp.CookieLoadError, Exception):
    raise ValueError('Missing cookie error API')
with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True}) as client:
    if not callable(client.extract_info) or not callable(client.download):
        raise ValueError('Incompatible API')
"""


def _probe_wheel(path: Path, version: Version, *, timeout: float) -> None:
    # Embedded Python cannot necessarily spawn another interpreter. Startup is the
    # only time an import probe may temporarily select a package in this process.
    standalone = bool(
        re.fullmatch(
            r"(?:pythonw?|pypy)(?:\d+(?:\.\d+)*)?(?:\.exe)?",
            Path(sys.executable).name.casefold(),
        )
    )
    if (
        sys.platform in {"ios", "android"}
        or hasattr(sys, "getandroidapilevel")
        or getattr(sys, "frozen", False)
        or not standalone
    ):
        original_path = sys.path[:]
        try:
            exec(_PROBE, {"probe_arguments": [str(path), str(version)]})  # noqa: S102 - fixed, local API probe (never remote source text)
        finally:
            sys.path[:] = original_path
            for name in list(sys.modules):
                if name == "yt_dlp" or name.startswith("yt_dlp."):
                    sys.modules.pop(name, None)
            importlib.invalidate_caches()
    else:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _PROBE, str(path), str(version)],
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            raise ValueError("yt-dlp import/API validation failed")


def _parse_wheel_name(name: str) -> tuple[Version, str] | None:
    match = _WHEEL_NAME.fullmatch(name)
    if match is None:
        return None
    try:
        version = Version(match.group("version"))
    except InvalidVersion:
        return None
    return version, name


def _entry_shape(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    if not all(
        isinstance(value.get(key), str) for key in ("version", "filename", "sha256")
    ):
        return False
    try:
        version = Version(value["version"])
    except (InvalidVersion, TypeError):
        return False
    parsed = _parse_wheel_name(value["filename"])
    return (
        parsed is not None
        and parsed[0] == version
        and _SHA256.fullmatch(value["sha256"]) is not None
        and Path(value["filename"]).name == value["filename"]
        and "/" not in value["filename"]
        and "\\" not in value["filename"]
    )


def _state_records(state: Mapping[str, Any]) -> dict[str, WheelRecord]:
    result: dict[str, WheelRecord] = {}
    entries = [state.get("current"), state.get("previous")]
    pending = state.get("pending")
    if isinstance(pending, dict) and pending.get("status") == "validated":
        entries.append(pending.get("wheel"))
    for value in entries:
        if not isinstance(value, dict) or not _entry_shape(value):
            continue
        version = Version(value["version"])
        result[value["filename"]] = WheelRecord(
            version, value["filename"], value["sha256"].lower()
        )
    return result


def _record_for_state(
    records: Mapping[str, WheelRecord], value: object
) -> WheelRecord | None:
    if not isinstance(value, dict):
        return None
    filename = value.get("filename")
    if not isinstance(filename, str):
        return None
    return records.get(filename)


def _same_roles(
    state: Mapping[str, Any],
    current: WheelRecord | None,
    previous: WheelRecord | None,
) -> bool:
    return _state_entry_matches(state.get("current"), current) and _state_entry_matches(
        state.get("previous"), previous
    )


def _state_entry_matches(value: object, record: WheelRecord | None) -> bool:
    if record is None:
        return value is None
    return isinstance(value, dict) and value == record.as_json()


def _choose_previous(
    records: Mapping[str, WheelRecord],
    current: WheelRecord | None,
    preferred: WheelRecord | None,
) -> WheelRecord | None:
    if current is None:
        return None
    if preferred is not None and preferred.filename != current.filename:
        candidate = records.get(preferred.filename)
        if candidate is not None and candidate.version < current.version:
            return candidate
    candidates = [
        record
        for record in records.values()
        if record.filename != current.filename and record.version < current.version
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.version)


def _looks_like_runtime_wheel(path: Path) -> bool:
    return path.name.startswith(_WHEEL_PREFIX) and path.name.endswith(_WHEEL_SUFFIX)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_remote_release(url: str, timeout: float) -> RemoteRelease:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Melody/yt-dlp-updater"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not response.geturl().startswith("https://"):
            raise ValueError("Update metadata requires HTTPS")
        encoded = response.read(2 * 1024 * 1024 + 1)
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError("Update metadata exceeds the size limit")
        payload = json.loads(encoded)
    info = payload["info"]
    version = Version(str(info["version"]))
    candidates = [
        item
        for item in payload["urls"]
        if isinstance(item, dict)
        and item.get("packagetype") == "bdist_wheel"
        and not item.get("yanked", False)
        and str(item.get("filename", "")).casefold().endswith(_WHEEL_SUFFIX)
        and item.get("digests", {}).get("sha256")
    ]
    candidate = next(
        (
            item
            for item in candidates
            if (_parsed := _parse_wheel_name(str(item["filename"]))) is not None
            and _parsed[0] == version
        ),
        None,
    )
    if candidate is None:
        raise ValueError("PyPI did not provide a compatible py3-none-any wheel")
    filename = str(candidate["filename"])
    sha256 = str(candidate["digests"]["sha256"]).lower()
    if _SHA256.fullmatch(sha256) is None:
        raise ValueError("PyPI provided an invalid wheel SHA-256")
    from urllib.parse import urlsplit

    wheel_url = str(candidate["url"])
    parsed_url = urlsplit(wheel_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "files.pythonhosted.org":
        raise ValueError("Wheel must come from PyPI's HTTPS file host")
    return RemoteRelease(version, filename, wheel_url, sha256)


def _download_file(url: str, destination: Path, timeout: float) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Melody/yt-dlp-updater"},
    )
    with (
        urllib.request.urlopen(request, timeout=timeout) as response,
        destination.open("wb") as stream,
    ):
        if not response.geturl().startswith("https://"):
            raise ValueError("Wheel download requires HTTPS")
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > _MAX_WHEEL_BYTES:
                raise ValueError("Wheel download exceeds the size limit")
            stream.write(chunk)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning("Could not remove yt-dlp runtime file %s: %s", path, error)


__all__ = [
    "RemoteRelease",
    "UpdateResult",
    "WheelRecord",
    "YtDlpUpdater",
    "prepare_yt_dlp",
]
