"""Shared guardrails for production build wrappers."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

RELEASE_PYTHON_VERSION = "3.13"
TARGET_PRESENTATIONS = {
    "windows": "desktop",
    "linux": "desktop",
    "macos": "desktop",
    "apk": "mobile",
    "aab": "mobile",
}

_FALSE_VALUES = {"", "0", "false", "no", "off"}
_DEBUG_ENVIRONMENT_VARIABLES = (
    "MELODY_DEBUG",
    "FLET_DEBUG",
    "PYTHONDEVMODE",
    "PYTHONASYNCIODEBUG",
)
_DEVELOPMENT_ENVIRONMENT_VARIABLES = (
    "FLET_DISPLAY_URL_PREFIX",
    "FLET_FORCE_WEB_SERVER",
    "FLET_SERVER_IP",
    "FLET_SERVER_PORT",
    "FLET_VIEW_PATH",
)
ANDROID_SIGNING_ENVIRONMENT_VARIABLES = (
    "FLET_ANDROID_SIGNING_KEY_STORE",
    "FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD",
    "FLET_ANDROID_SIGNING_KEY_PASSWORD",
    "FLET_ANDROID_SIGNING_KEY_ALIAS",
)
_FORBIDDEN_FLET_ARGUMENTS = {
    "--analyze-size",
    "--dart-define",
    "--dart-define-from-file",
    "--debug",
    "--dump-info",
    "--no-strip",
    "--profile",
    "--source-maps",
    "--split-debug-info",
    "--track-widget-creation",
    "--verbose",
    "--no-compile-app",
    "--no-compile-packages",
    "--python-version",
}
_FORBIDDEN_FLET_PREFIXES = tuple(
    f"{argument}=" for argument in _FORBIDDEN_FLET_ARGUMENTS
) + (
    "--flutter-build-args=--debug",
    "--flutter-build-args=--profile",
)
_REQUIRED_FLET_ARGUMENTS = (
    "--python-version",
    RELEASE_PYTHON_VERSION,
    "--compile-app",
    "--compile-packages",
    "--cleanup-app",
    "--cleanup-packages",
)
_PRODUCTION_VALUES = {
    "MELODY_ENV": "production",
    "MELODY_DEBUG": "0",
    "FLET_DEBUG": "0",
    "PYTHONDEVMODE": "0",
    "PYTHONASYNCIODEBUG": "0",
    "PYTHONOPTIMIZE": "2",
}


def _is_truthy(value: str) -> bool:
    return value.strip().casefold() not in _FALSE_VALUES


def validate_production_environment(environment: Mapping[str, str]) -> None:
    """Reject development/staging settings before starting a release build."""
    configured_environment = environment.get("MELODY_ENV")
    if configured_environment and configured_environment.casefold() != "production":
        raise RuntimeError(
            "Production builds require MELODY_ENV=production; "
            f"received {configured_environment!r}."
        )

    enabled_debug_flags = [
        name
        for name in _DEBUG_ENVIRONMENT_VARIABLES
        if name in environment and _is_truthy(environment[name])
    ]
    if enabled_debug_flags:
        raise RuntimeError(
            "Production builds cannot run with debug flags enabled: "
            + ", ".join(enabled_debug_flags)
        )

    development_settings = [
        name
        for name in _DEVELOPMENT_ENVIRONMENT_VARIABLES
        if environment.get(name, "").strip()
    ]
    if development_settings:
        raise RuntimeError(
            "Production builds cannot inherit development Flet settings: "
            + ", ".join(development_settings)
        )


def validate_android_release_signing(environment: Mapping[str, str]) -> None:
    """Require an explicit non-debug Android signing configuration."""
    missing = [
        name
        for name in ANDROID_SIGNING_ENVIRONMENT_VARIABLES
        if not environment.get(name, "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Android production builds require release signing variables: "
            + ", ".join(missing)
        )

    key_store = Path(environment["FLET_ANDROID_SIGNING_KEY_STORE"])
    alias = environment["FLET_ANDROID_SIGNING_KEY_ALIAS"].casefold()
    if not key_store.is_file():
        raise RuntimeError(f"Android release key store does not exist: {key_store}")
    if "debug" in key_store.name.casefold() or alias == "androiddebugkey":
        raise RuntimeError("Android production builds cannot use a debug signing key.")


def production_build_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child-process environment with release-only settings."""
    result = dict(os.environ if environment is None else environment)
    validate_production_environment(result)
    result.update(_PRODUCTION_VALUES)
    return result


def hardened_flet_arguments(arguments: Sequence[str]) -> list[str]:
    """Reject release-weakening flags and append mandatory packaging flags."""
    normalized = [argument.casefold() for argument in arguments]
    forbidden = [
        argument
        for argument in normalized
        if argument in _FORBIDDEN_FLET_ARGUMENTS
        or argument.startswith(_FORBIDDEN_FLET_PREFIXES)
        or "--dart-define" in argument
        or (argument.startswith("-v") and set(argument[1:]) == {"v"})
    ]
    if forbidden:
        raise ValueError(
            "Development-oriented Flet arguments are not allowed in a "
            f"production build: {', '.join(forbidden)}"
        )
    return [*arguments, *_REQUIRED_FLET_ARGUMENTS]


def platform_flet_arguments(
    arguments: Sequence[str], *, target: str, project_file: Path
) -> list[str]:
    """Keep shared exclusions while packaging only the target's presentation.

    Flet's CLI exclusion list replaces the TOML list. Repeated --exclude options
    extend the CLI list, so user exclusions and our required exclusions coexist.
    Both separators are needed by the native packager on Windows and POSIX.
    """
    presentation = TARGET_PRESENTATIONS[target]
    opposite = "mobile" if presentation == "desktop" else "desktop"
    with project_file.open("rb") as stream:
        config = tomllib.load(stream)["tool"]["flet"]
    config_platform = "android" if target in {"apk", "aab"} else target
    exclusions = [
        *config.get("app", {}).get("exclude", []),
        *config.get(config_platform, {}).get("app", {}).get("exclude", []),
        f"musicplayer/ui/{opposite}",
        f"musicplayer\\ui\\{opposite}",
    ]
    return [
        *hardened_flet_arguments(arguments),
        "--exclude",
        *dict.fromkeys(exclusions),
    ]


def required_ui_modules(project_file: Path, presentation: str) -> set[str]:
    """Expected compiled UI inventory, including every shared UI module."""
    if presentation not in {"mobile", "desktop"}:
        raise ValueError(f"Unknown presentation: {presentation}")
    with project_file.open("rb") as stream:
        config = tomllib.load(stream)["tool"]["flet"]
    package = project_file.parent / config["app"]["path"] / "musicplayer"
    opposite = "mobile" if presentation == "desktop" else "desktop"
    modules = {
        path.relative_to(package).with_suffix(".pyc").as_posix()
        for path in (package / "ui").rglob("*.py")
        if opposite not in path.relative_to(package / "ui").parts
    }
    if f"ui/{presentation}/shell.pyc" not in modules:
        raise ValueError(f"Missing {presentation} UI sources under {package / 'ui'}")
    return modules


@contextmanager
def production_process_environment() -> Iterator[None]:
    """Temporarily apply the production environment to an in-process build."""
    safe_environment = production_build_environment()
    previous = {name: os.environ.get(name) for name in _PRODUCTION_VALUES}
    os.environ.update({name: safe_environment[name] for name in _PRODUCTION_VALUES})
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
