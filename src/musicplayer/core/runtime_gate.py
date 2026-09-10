"""One-shot readiness gate for optional runtime preparation."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from threading import Lock, Thread
from typing import Any

_LOCK = Lock()
MEDIA_BINARY_PREPARATION = "media-binaries"
_PREPARATIONS: dict[str, Future[Any]] = {}
_OPERATIONS: dict[str, Callable[[], Any]] = {}


def configure_runtime_preparation(operation: Callable[[], Any], *, key: str) -> None:
    """Register optional setup without starting work on the launch path."""
    with _LOCK:
        if key not in _PREPARATIONS:
            _OPERATIONS[key] = operation


def start_runtime_preparation(operation: Callable[[], Any], *, key: str) -> Future[Any]:
    """Start optional network/runtime work without delaying the application UI."""
    with _LOCK:
        existing = _PREPARATIONS.get(key)
        if existing is not None:
            return existing
        future: Future[Any] = Future()
        _PREPARATIONS[key] = future

    def run() -> None:
        try:
            result = operation()
        except Exception as error:  # noqa: BLE001 - forwarded to the feature worker
            future.set_exception(error)
        else:
            future.set_result(result)

    thread_name = f"melody-{key}-prepare"
    Thread(target=run, name=thread_name, daemon=True).start()
    return future


def wait_for_runtime_preparation(*, key: str) -> Any:
    """Start registered work on demand and wait at its feature boundary."""
    with _LOCK:
        future = _PREPARATIONS.get(key)
        operation = _OPERATIONS.get(key) if future is None else None
    if operation is not None:
        future = start_runtime_preparation(operation, key=key)
    return future.result() if future is not None else None
