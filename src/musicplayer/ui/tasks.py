"""Page-loop ownership and bounded, cancellable blocking work."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import Future
from functools import partial, wraps
from typing import Any

from musicplayer.application.contracts import LazyBoundedExecutor

logger = logging.getLogger(__name__)


class PageTasks:
    def __init__(
        self, page: Any, workers: LazyBoundedExecutor, on_error: Callable[[str], None]
    ):
        self.page = page
        self.workers = workers
        self.on_error = on_error
        self.closed = False
        # Flet returns an asyncio task while lightweight fixtures may return a
        # concurrent future; both expose the cancellation API used here.
        self._jobs: dict[object, Any] = {}

    def dispatch(self, callback: Callable[..., Any], *args: Any) -> None:
        """Queue a short UI callback; never wait for the page from a worker."""
        if not self.closed:
            try:
                if not hasattr(self.page, "run_task"):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        callback(*args)
                    else:
                        loop.call_soon_threadsafe(callback, *args)
                    return
                self.page.run_task(self._deliver, callback, args)
            except RuntimeError:
                logger.debug("UI callback discarded after page disposal")

    async def _deliver(
        self, callback: Callable[..., Any], args: tuple[Any, ...]
    ) -> None:
        if not self.closed:
            callback(*args)

    def submit(
        self,
        operation: Callable[[], Any],
        done: Callable[[Any], None],
        *,
        key: object | None = None,
        replace: bool = False,
        failed: Callable[[str], None] | None = None,
    ) -> bool:
        if self.closed:
            return False
        key = key if key is not None else object()
        previous = self._jobs.get(key)
        if previous is not None:
            if not replace:
                return False
            previous.cancel()
        try:
            worker = self.workers.submit(operation)
        except RuntimeError as error:
            (failed or self.on_error)(str(error))
            return False
        if not hasattr(self.page, "run_task"):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    done(worker.result())
                except Exception as error:  # noqa: BLE001 - worker errors are user-facing
                    (failed or self.on_error)(str(error))
            else:
                task = loop.create_task(self._finish(key, worker, done, failed))
                self._jobs[key] = task
            return True
        task = self.page.run_task(self._finish, key, worker, done, failed)
        self._jobs[key] = task
        return True

    async def _finish(
        self,
        key: object,
        worker: Future[Any],
        done: Callable[[Any], None],
        failed: Callable[[str], None] | None,
    ) -> None:
        current = self._jobs.get(key)
        try:
            value = await asyncio.wrap_future(worker)
        except asyncio.CancelledError:
            worker.cancel()
        except Exception as error:
            logger.exception("Background operation failed")
            if not self.closed:
                (failed or self.on_error)(str(error))
        else:
            if not self.closed:
                done(value)
        finally:
            if self._jobs.get(key) is current:
                self._jobs.pop(key, None)

    def close(self) -> None:
        self.closed = True
        for task in self._jobs.values():
            task.cancel()
        self._jobs.clear()

    async def io(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if self.closed:
            raise asyncio.CancelledError
        result = await asyncio.wrap_future(
            self.workers.submit(operation, *args, **kwargs)
        )
        if self.closed:
            raise asyncio.CancelledError
        return result

    def start(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        key = (
            "action",
            getattr(operation, "__qualname__", type(operation).__qualname__),
        )
        if self.closed or key in self._jobs:
            return
        if not hasattr(self.page, "run_task"):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self._action(key, operation, args, kwargs))
            else:
                self._jobs[key] = loop.create_task(
                    self._action(key, operation, args, kwargs)
                )
            return
        self._jobs[key] = self.page.run_task(self._action, key, operation, args, kwargs)

    async def _action(
        self,
        key: object,
        operation: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        try:
            await operation(*args, **kwargs)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            logger.exception("Page action failed")
            if not self.closed:
                self.on_error(str(error))
        finally:
            self._jobs.pop(key, None)


def page_action(function):
    """Keep sync UI bindings while the action explicitly awaits blocking steps."""

    @wraps(function)
    def start(app, *args, **kwargs):
        app.tasks.start(function, app, *args, **kwargs)

    return start


def run_io(
    app: Any, operation: Callable[[], Any], done: Callable[[Any], None], **options: Any
) -> bool:
    """The single I/O boundary used by presentation actions."""
    return app.tasks.submit(operation, done, **options)


def ui_callback(app: Any, callback: Callable[..., Any], *args: Any) -> None:
    app.tasks.dispatch(callback, *args)


def call_io(
    app: Any,
    operation: Callable[..., Any],
    *args: Any,
    done: Callable[[Any], None] | None = None,
    **options: Any,
) -> bool:
    return run_io(app, partial(operation, *args), done or (lambda _: None), **options)
