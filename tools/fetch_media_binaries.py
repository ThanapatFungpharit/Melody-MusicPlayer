"""Download and verify the FFmpeg/FFprobe bundles used by application builds."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

VERSION = "v9.0.1"
RELEASE_ROOT = f"https://github.com/binmgr/ffmpeg/releases/download/{VERSION}"
BTBN_VERSION = "autobuild-2026-09-03-13-17"
BTBN_RELEASE_ROOT = (
    f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{BTBN_VERSION}"
)
ANDROID_VERSION = "build-264"
ANDROID_RELEASE_ROOT = (
    f"https://github.com/rhythmcache/ffmpeg-android/releases/download/{ANDROID_VERSION}"
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINARY_ROOT = PROJECT_ROOT / "src" / "musicplayer" / "bin"


@dataclass(frozen=True)
class Archive:
    filename: str
    sha256: str
    release_root: str = RELEASE_ROOT
    version: str = VERSION
    license: str = "GPL-2.0-or-later"
    source_code: str = f"https://github.com/binmgr/ffmpeg/tree/{VERSION}"

    @property
    def url(self) -> str:
        return f"{self.release_root}/{self.filename}"


ARCHIVES: dict[tuple[str, str], Archive] = {
    ("windows", "x86_64"): Archive(
        "ffmpeg-n9.0.1-11-ge47273f4d9-win64-gpl-9.0.zip",
        "cf2beec370200044af55f6bed072bb7aa4cdf49901812e249c8a00ce2a7cba0c",
        release_root=BTBN_RELEASE_ROOT,
        version="n9.0.1-11-ge47273f4d9",
        license="GPL-3.0-or-later",
        source_code=(
            "https://github.com/BtbN/FFmpeg-Builds/tree/autobuild-2026-09-03-13-17"
        ),
    ),
    ("windows", "arm64"): Archive(
        "ffmpeg-n9.0.1-11-ge47273f4d9-winarm64-gpl-9.0.zip",
        "e3f7c643a0d3c86fc5538570455906e065faa14685f7d46b1c2302bf49058084",
        release_root=BTBN_RELEASE_ROOT,
        version="n9.0.1-11-ge47273f4d9",
        license="GPL-3.0-or-later",
        source_code=(
            "https://github.com/BtbN/FFmpeg-Builds/tree/autobuild-2026-09-03-13-17"
        ),
    ),
    ("linux", "x86_64"): Archive(
        "ffmpeg-linux-amd64.tar.gz",
        "981493cde0bd9303129e6a7bef1a22bb65089bd8e04fd96622878a865761d706",
    ),
    ("linux", "arm64"): Archive(
        "ffmpeg-linux-arm64.tar.gz",
        "4fdc66bd708aef86f4e431f52f72bb26ce49eb9e12d34ff92c069e04dabc2df5",
    ),
    ("macos", "x86_64"): Archive(
        "ffmpeg-darwin-amd64.tar.gz",
        "a58d579615e8bfd54cb063e208d9c6c33b6ed81a8030885347167ff86ba43148",
    ),
    ("macos", "arm64"): Archive(
        "ffmpeg-darwin-arm64.tar.gz",
        "5e28fe92746c35be5a5c0c7bffe8397c36e9bf75867eb28992c69fcfb69be155",
    ),
    ("android", "arm64"): Archive(
        "ffmpeg-8.0-ee2eb6c-Static-android-arm64-v8a.zip",
        "a18551c916b3cb60c41930d0affdf44763497fe318ccec9555ab93097a9c28c8",
        release_root=ANDROID_RELEASE_ROOT,
        version=ANDROID_VERSION,
        license="GPL-3.0-or-later",
        source_code="https://github.com/rhythmcache/ffmpeg-android/tree/0f21485",
    ),
    ("android", "arm"): Archive(
        "ffmpeg-8.0-ee2eb6c-Static-android-armeabi-v7a.zip",
        "15fd326dd60cae3e444e33911663ba3bb37f2f9f1672581d09e1f4d4e4cf5b9f",
        release_root=ANDROID_RELEASE_ROOT,
        version=ANDROID_VERSION,
        license="GPL-3.0-or-later",
        source_code="https://github.com/rhythmcache/ffmpeg-android/tree/0f21485",
    ),
    ("android", "x86_64"): Archive(
        "ffmpeg-8.0-ee2eb6c-Static-android-x86_64.zip",
        "f41fb1339c4d609e376bc99ea0982a11db5cebeb2dce2e302c60cce9dd0ffe0e",
        release_root=ANDROID_RELEASE_ROOT,
        version=ANDROID_VERSION,
        license="GPL-3.0-or-later",
        source_code="https://github.com/rhythmcache/ffmpeg-android/tree/0f21485",
    ),

}


def fetch_bundle(
    platform_name: str,
    architecture: str,
    *,
    binary_root: Path = DEFAULT_BINARY_ROOT,
) -> Path:
    """Download, verify, and extract a single target bundle."""
    archive = ARCHIVES[(platform_name, architecture)]
    target = binary_root / platform_name / architecture
    suffix = ".exe" if platform_name == "windows" else ""
    expected = (target / f"ffmpeg{suffix}", target / f"ffprobe{suffix}")
    metadata_path = target / "bundle.json"
    if all(path.is_file() for path in expected) and metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("archive_sha256") == archive.sha256:
            print(f"Using cached media tools for {platform_name}/{architecture}")
            return target

    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="melody-ffmpeg-") as temporary:
        download = Path(temporary) / archive.filename
        print(f"Downloading {archive.url}")
        _download(archive.url, download)
        actual_digest = _sha256(download)
        if actual_digest != archive.sha256:
            raise RuntimeError(
                f"Checksum mismatch for {archive.filename}: expected "
                f"{archive.sha256}, received {actual_digest}"
            )
        extracted = _extract_programs(download, Path(temporary) / "extracted")
        for program in (f"ffmpeg{suffix}", f"ffprobe{suffix}"):
            source = extracted[program]
            destination = target / program
            shutil.copy2(source, destination)
            if platform_name != "windows":
                destination.chmod(
                    destination.stat().st_mode
                    | stat.S_IXUSR
                    | stat.S_IXGRP
                    | stat.S_IXOTH
                )

    metadata_path.write_text(
        json.dumps(
            {
                "version": archive.version,
                "source": archive.url,
                "archive_sha256": archive.sha256,
                "license": archive.license,
                "source_code": archive.source_code,
            },
            indent=2,
        )
        + os.linesep,
        encoding="utf-8",
    )
    print(f"Prepared media tools in {target}")
    return target


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Melody-build/1"})
    with (
        urllib.request.urlopen(request, timeout=60) as response,
        destination.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_programs(archive: Path, destination: Path) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    expected_names = {"ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"}
    extracted: dict[str, Path] = {}

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                name = PurePosixPath(member.filename).name
                if name.endswith(".tar.xz") and not member.is_dir():
                    with package.open(member) as nested:
                        nested_data = nested.read()
                    with tarfile.open(
                        fileobj=io.BytesIO(nested_data), mode="r:xz"
                    ) as nested_package:
                        _extract_tar_programs(
                            nested_package, destination, expected_names, extracted
                        )
                    continue
                if name not in expected_names or member.is_dir():
                    continue
                path = destination / name
                with package.open(member) as source, path.open("wb") as output:
                    shutil.copyfileobj(source, output)
                extracted[name] = path
    else:
        with tarfile.open(archive, mode="r:gz") as package:
            _extract_tar_programs(package, destination, expected_names, extracted)

    unix_programs = {"ffmpeg", "ffprobe"}
    windows_programs = {"ffmpeg.exe", "ffprobe.exe"}
    required = (
        unix_programs if unix_programs.intersection(extracted) else windows_programs
    )
    missing = required.difference(extracted)
    if missing:
        raise RuntimeError(
            f"{archive.name} did not contain: {', '.join(sorted(missing))}"
        )
    return extracted


def _extract_tar_programs(
    package: tarfile.TarFile,
    destination: Path,
    expected_names: set[str],
    extracted: dict[str, Path],
) -> None:
    for member in package.getmembers():
        name = PurePosixPath(member.name).name
        if name not in expected_names or not member.isfile():
            continue
        source = package.extractfile(member)
        if source is None:
            continue
        path = destination / name
        with source, path.open("wb") as output:
            shutil.copyfileobj(source, output)
        extracted[name] = path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        choices=("windows", "linux", "macos", "android", "all"),
        default="all",
    )
    parser.add_argument(
        "--architecture",
        choices=("x86_64", "arm64", "arm", "all"),
        default="all",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_BINARY_ROOT)
    arguments = parser.parse_args()

    platforms = (
        ("windows", "linux", "macos", "android")
        if arguments.platform == "all"
        else (arguments.platform,)
    )
    architectures = (
        ("x86_64", "arm64", "arm")
        if arguments.architecture == "all"
        else (arguments.architecture,)
    )
    for platform_name in platforms:
        for architecture in architectures:
            fetch_bundle(platform_name, architecture, binary_root=arguments.output)


if __name__ == "__main__":
    main()
