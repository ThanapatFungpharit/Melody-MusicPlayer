"""Read the sole release version without importing the application/toolchain."""

from __future__ import annotations

import argparse
import ast
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def release_version(root: Path = ROOT) -> str:
    tree = ast.parse((root / "src/musicplayer/__init__.py").read_text("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            version = ast.literal_eval(node.value)
            if isinstance(version, str) and re.fullmatch(
                r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version
            ):
                return version
    raise ValueError("Melody's __version__ must be a literal major.minor.patch version")


def build_number(version: str) -> int:
    major, minor, patch = map(int, version.split("."))
    if major > 2099 or minor >= 1000 or patch >= 1000:
        raise ValueError("Version exceeds the Android build-number allocation")
    return major * 1_000_000 + minor * 1_000 + patch


def validate_tag(version: str, ref: str) -> None:
    if ref.startswith("refs/tags/") and ref != f"refs/tags/v{version}":
        raise ValueError(f"Release tag {ref!r} must match v{version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    args = parser.parse_args()
    version = release_version()
    validate_tag(version, args.ref)
    print(version)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"version={version}\nbuild_number={build_number(version)}\n")


if __name__ == "__main__":
    main()
