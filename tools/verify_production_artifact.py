"""Fail when a production artifact contains development-only material."""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tomllib
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

EXPECTED_RELEASE_CONFIG = {
    "schema": 1,
    "environment": "production",
    "debug": False,
    "mock_data": False,
    "source_maps": False,
}

_KNOWN_DEVELOPMENT_PACKAGES = {
    "coverage",
    "flet-cli",
    "mypy",
    "pytest",
    "pytest-asyncio",
    "ruff",
    "tox",
}
_FORBIDDEN_DIRECTORY_NAMES = {
    ".flet",
    ".git",
    ".github",
    ".idea",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    "__pycache__",
    "node_modules",
    "test",
    "tests",
    "tools",
}
_FORBIDDEN_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".dart",
    ".dill",
    ".h",
    ".hpp",
    ".ilk",
    ".java",
    ".kt",
    ".map",
    ".pdb",
    ".py",
    ".pyi",
}
_ARCHIVE_SUFFIXES = {".aab", ".apk", ".jar", ".whl", ".zip"}
_MAX_NESTED_ARCHIVE_BYTES = 256 * 1024 * 1024
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
_REQUIRED_RUNTIME_SOURCES = {
    "_sp_bootstrap.py",
    "flet/messaging/__init__.py",
    "bundle-metadata/com.android.tools.build.obfuscation/proguard.map",
}
_REMOTE_DEBUGGING_FILES = {
    "_remote_debugging.pyd",
    "_remote_debugging.soref",
    "lib_remote_debugging.so",
}


def canonicalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def dependency_group_packages(project_file: Path) -> set[str]:
    """Return packages reachable only from non-runtime dependency groups."""
    with project_file.open("rb") as stream:
        project = tomllib.load(stream)

    packages: set[str] = set()
    groups = project.get("dependency-groups", {})
    if not isinstance(groups, dict):
        return packages

    for values in groups.values():
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            match = _REQUIREMENT_NAME.match(value.strip())
            if match:
                packages.add(canonicalize_package_name(match.group(0)))

    lock_file = project_file.with_name("uv.lock")
    if not lock_file.is_file():
        return packages

    with lock_file.open("rb") as stream:
        lock = tomllib.load(stream)
    locked_packages = lock.get("package", [])
    if not isinstance(locked_packages, list):
        return packages

    project_name = canonicalize_package_name(
        str(project.get("project", {}).get("name", ""))
    )
    graph: dict[str, set[str]] = {}
    runtime_roots: set[str] = set()
    group_roots: set[str] = set(packages)
    for locked_package in locked_packages:
        if not isinstance(locked_package, dict):
            continue
        name = canonicalize_package_name(str(locked_package.get("name", "")))
        dependencies = locked_package.get("dependencies", [])
        graph[name] = {
            canonicalize_package_name(str(dependency.get("name", "")))
            for dependency in dependencies
            if isinstance(dependency, dict) and dependency.get("name")
        }
        if name == project_name:
            runtime_roots = set(graph[name])
            development_groups = locked_package.get("dev-dependencies", {})
            if isinstance(development_groups, dict):
                group_roots.update(
                    canonicalize_package_name(str(dependency.get("name", "")))
                    for group in development_groups.values()
                    if isinstance(group, list)
                    for dependency in group
                    if isinstance(dependency, dict) and dependency.get("name")
                )

    def closure(roots: set[str]) -> set[str]:
        visited: set[str] = set()
        pending = list(roots)
        while pending:
            package = pending.pop()
            if package in visited:
                continue
            visited.add(package)
            pending.extend(graph.get(package, set()) - visited)
        return visited

    return closure(group_roots) - closure(runtime_roots)


def _looks_like_distribution(component: str, package: str) -> bool:
    normalized = canonicalize_package_name(component)
    return (
        normalized == package
        or normalized.startswith(f"{package}-")
        and normalized.endswith(("-dist-info", "-egg-info", "-data"))
    )


def _path_violation(path: str, development_packages: set[str]) -> str | None:
    normalized_path = path.replace("\\", "/").replace("!", "/").strip("/")
    parts = [part for part in PurePosixPath(normalized_path).parts if part not in {"", "."}]
    folded_parts = [part.casefold() for part in parts]
    filename = folded_parts[-1] if folded_parts else ""

    forbidden_directory = next(
        (part for i, part in enumerate(folded_parts[:-1]) 
         if part in _FORBIDDEN_DIRECTORY_NAMES
         and not (part == "__pycache__" and "stdlib" in folded_parts[:i])),
        None,
    )
    if forbidden_directory:
        return f"development/tooling directory {forbidden_directory!r}"

    if any(part.endswith(".dsym") for part in folded_parts):
        return "debug-symbol bundle"

    if filename.startswith(".env"):
        return "environment file"

    if filename in {"pyproject.toml", "requirements.txt", "uv.lock"}:
        return "build/dependency manifest"

    if filename in {"pytest_plugin.pyc", "pytest_plugin.py"}:
        return "test-framework plugin"

    if filename in _REMOTE_DEBUGGING_FILES:
        return "remote-debugging runtime"

    if filename.startswith("test_") or filename.endswith(("_test.py", "_test.pyc")):
        return "test module"

    member_path = path.replace("\\", "/").split("!")[-1].strip("/").casefold()
    required_runtime_source = any(
        member_path.endswith(allowed) for allowed in _REQUIRED_RUNTIME_SOURCES
    )
    if filename.endswith(tuple(_FORBIDDEN_SUFFIXES)) and not required_runtime_source:
        return f"non-runtime source/debug artifact ({PurePosixPath(filename).suffix})"

    if normalized_path.casefold().endswith("musicplayer/__main__.pyc"):
        return "source-checkout development entry point"

    for component in parts:
        for package in development_packages:
            if _looks_like_distribution(component, package):
                return f"development-only package {package!r}"
    return None


@dataclass
class ArtifactAudit:
    development_packages: set[str]
    violations: list[str] = field(default_factory=list)
    release_configs: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    files_checked: int = 0
    archives_checked: int = 0

    def inspect_path(self, display_path: str, data: bytes | None = None) -> None:
        self.files_checked += 1
        violation = _path_violation(display_path, self.development_packages)
        if violation:
            self.violations.append(f"{display_path}: {violation}")

        normalized = display_path.replace("\\", "/").casefold()
        if normalized.endswith("musicplayer/release.json"):
            if data is None:
                self.violations.append(
                    f"{display_path}: release configuration could not be read"
                )
            else:
                try:
                    config = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    self.violations.append(
                        f"{display_path}: invalid release configuration ({error})"
                    )
                else:
                    if isinstance(config, dict):
                        self.release_configs.append((display_path, config))
                    else:
                        self.violations.append(
                            f"{display_path}: release configuration must be an object"
                        )

    def inspect_zip(self, archive_data: bytes, display_path: str) -> None:
        self.archives_checked += 1
        try:
            with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    nested_path = f"{display_path}!{info.filename}"
                    suffix = PurePosixPath(info.filename).suffix.casefold()
                    needs_data = (
                        info.filename.replace("\\", "/")
                        .casefold()
                        .endswith("musicplayer/release.json")
                        or suffix in _ARCHIVE_SUFFIXES
                    )
                    member_data: bytes | None = None
                    if needs_data:
                        if info.file_size > _MAX_NESTED_ARCHIVE_BYTES:
                            self.violations.append(
                                f"{nested_path}: nested archive/config exceeds "
                                f"{_MAX_NESTED_ARCHIVE_BYTES} bytes"
                            )
                        else:
                            member_data = archive.read(info)
                    self.inspect_path(nested_path, member_data)
                    if (
                        suffix in _ARCHIVE_SUFFIXES
                        and member_data is not None
                        and zipfile.is_zipfile(io.BytesIO(member_data))
                    ):
                        self.inspect_zip(member_data, nested_path)
        except (OSError, zipfile.BadZipFile) as error:
            self.violations.append(f"{display_path}: unreadable archive ({error})")

    def inspect_artifact(self, artifact: Path) -> None:
        if not artifact.exists():
            self.violations.append(f"{artifact}: artifact does not exist")
            return

        if artifact.is_dir():
            files = sorted(path for path in artifact.rglob("*") if path.is_file())
            if not files:
                self.violations.append(f"{artifact}: artifact directory is empty")
            for path in files:
                relative = path.relative_to(artifact).as_posix()
                suffix = path.suffix.casefold()
                needs_data = (
                    relative.casefold().endswith("musicplayer/release.json")
                    or suffix in _ARCHIVE_SUFFIXES
                )
                data = path.read_bytes() if needs_data else None
                display_path = f"{artifact}!{relative}"
                self.inspect_path(display_path, data)
                if (
                    suffix in _ARCHIVE_SUFFIXES
                    and data is not None
                    and zipfile.is_zipfile(io.BytesIO(data))
                ):
                    self.inspect_zip(data, display_path)
            return

        suffix = artifact.suffix.casefold()
        data = artifact.read_bytes() if suffix in _ARCHIVE_SUFFIXES else None
        self.inspect_path(str(artifact), data)
        if data is not None and zipfile.is_zipfile(io.BytesIO(data)):
            self.inspect_zip(data, str(artifact))

    def validate_release_config(self) -> None:
        if not self.release_configs:
            self.violations.append(
                "artifact: missing musicplayer/release.json production marker"
            )
            return
        for path, config in self.release_configs:
            if config != EXPECTED_RELEASE_CONFIG:
                self.violations.append(
                    f"{path}: expected release settings "
                    f"{EXPECTED_RELEASE_CONFIG!r}, found {config!r}"
                )


def audit_artifacts(
    artifacts: Iterable[Path], project_file: Path
) -> ArtifactAudit:
    development_packages = (
        dependency_group_packages(project_file) | _KNOWN_DEVELOPMENT_PACKAGES
    )
    audit = ArtifactAudit(development_packages=development_packages)
    for artifact in artifacts:
        audit.inspect_artifact(artifact)
    audit.validate_release_config()
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifacts",
        nargs="+",
        type=Path,
        help="Final artifact files or directories to inspect",
    )
    parser.add_argument(
        "--project-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "pyproject.toml",
        help="pyproject.toml used to discover non-runtime dependency groups",
    )
    arguments = parser.parse_args(argv)

    audit = audit_artifacts(arguments.artifacts, arguments.project_file)
    if audit.violations:
        print("Production artifact verification failed:", file=sys.stderr)
        for violation in audit.violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1

    package_list = ", ".join(sorted(audit.development_packages))
    print(
        "Production artifact verification passed: "
        f"{audit.files_checked} files in {audit.archives_checked} archives; "
        f"excluded packages checked: {package_list}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
