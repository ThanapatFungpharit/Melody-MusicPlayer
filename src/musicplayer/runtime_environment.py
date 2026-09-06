"""Runtime policy for packaged production releases."""

from __future__ import annotations

import json
import logging
import os
import re
from importlib import resources
from typing import Any

EXPECTED_RELEASE_CONFIG: dict[str, Any] = {
    "schema": 1,
    "environment": "production",
    "debug": False,
    "mock_data": False,
    "source_maps": False,
}
_DEVELOPMENT_FLET_VARIABLES = (
    "FLET_DEBUG",
    "FLET_DISPLAY_URL_PREFIX",
    "FLET_FORCE_WEB_SERVER",
    "FLET_SERVER_IP",
    "FLET_SERVER_PORT",
    "FLET_VIEW_PATH",
)
_TECHNICAL_ERROR_MARKERS = (
    "[errno ",
    "[winerror ",
    "exception:",
    "traceback (most recent call last)",
)
_SENSITIVE_ERROR_VALUE = re.compile(
    r"(?:https?://\S+|[A-Za-z]:[\\/]|(?:^|\s)/(?:[^\s/]+/)+|\\\\[^\\]+\\)",
    re.IGNORECASE,
)
_GENERIC_USER_ERROR = "The operation could not be completed. Please try again."


def load_release_config() -> dict[str, Any]:
    """Load and validate the configuration embedded in a release artifact."""
    config_resource = resources.files("musicplayer").joinpath("release.json")
    config = json.loads(config_resource.read_text(encoding="utf-8"))
    if config != EXPECTED_RELEASE_CONFIG:
        raise RuntimeError("The packaged release configuration is invalid.")
    return config


def configure_production_runtime() -> None:
    """Force production configuration and silence diagnostic logging."""
    load_release_config()
    os.environ["MELODY_ENV"] = "production"
    os.environ["MELODY_DEBUG"] = "0"
    for variable in _DEVELOPMENT_FLET_VARIABLES:
        os.environ.pop(variable, None)
    logging.raiseExceptions = False
    logging.disable(logging.CRITICAL)


def public_error_message(message: str) -> str:
    """Keep user-facing failures concise and free of paths, URLs, or traces."""
    compact = " ".join(message.split()).strip()
    lowered = compact.casefold()
    if (
        not compact
        or len(compact) > 240
        or any(marker in lowered for marker in _TECHNICAL_ERROR_MARKERS)
        or _SENSITIVE_ERROR_VALUE.search(compact)
    ):
        return _GENERIC_USER_ERROR
    return compact
