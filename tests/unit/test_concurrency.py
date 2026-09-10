from __future__ import annotations

import time
import unittest
from threading import Event
from unittest.mock import patch

from musicplayer.core import runtime_gate
from musicplayer.core.concurrency import LazyBoundedExecutor, WorkerQueueFull


class LazyBoundedExecutorTests(unittest.TestCase):
    def test_pool_is_created_on_demand_and_retires_when_idle(self) -> None:
        executor = LazyBoundedExecutor(max_workers=1, thread_name_prefix="test-lazy")
        started = Event()
        release = Event()

        def work() -> int:
            started.set()
            release.wait(timeout=2)
            return 42

        self.assertFalse(executor.is_active)
        future = executor.submit(work)
        self.assertTrue(started.wait(timeout=1))
        self.assertTrue(executor.is_active)

        release.set()
        self.assertEqual(future.result(timeout=1), 42)
        deadline = time.monotonic() + 1
        while executor.is_active and time.monotonic() < deadline:
            time.sleep(0.001)

        self.assertFalse(executor.is_active)
        self.assertEqual(executor.in_flight, 0)
        executor.shutdown()

    def test_active_and_pending_capacity_is_strictly_bounded(self) -> None:
        executor = LazyBoundedExecutor(
            max_workers=1,
            max_pending=1,
            thread_name_prefix="test-bounded",
        )
        started = Event()
        release = Event()

        def blocked() -> str:
            started.set()
            release.wait(timeout=2)
            return "first"

        first = executor.submit(blocked)
        self.assertTrue(started.wait(timeout=1))
        second = executor.submit(lambda: "second")

        with self.assertRaises(WorkerQueueFull):
            executor.submit(lambda: "rejected")

        self.assertEqual(executor.in_flight, 2)
        release.set()
        self.assertEqual(first.result(timeout=1), "first")
        self.assertEqual(second.result(timeout=1), "second")
        executor.shutdown()

    def test_failed_task_releases_capacity_for_later_work(self) -> None:
        executor = LazyBoundedExecutor(max_workers=1, thread_name_prefix="test-error")

        def fail() -> None:
            raise OSError("expected")

        with self.assertRaises(OSError):
            executor.submit(fail).result(timeout=1)
        self.assertEqual(executor.submit(lambda: 7).result(timeout=1), 7)
        executor.shutdown()


class RuntimePreparationTests(unittest.TestCase):
    def test_registered_work_does_not_start_until_the_feature_waits(self) -> None:
        started = Event()

        with (
            patch.object(runtime_gate, "_PREPARATIONS", {}),
            patch.object(runtime_gate, "_OPERATIONS", {}),
        ):
            runtime_gate.configure_runtime_preparation(
                lambda: started.set() or "ready",
                key=runtime_gate.MEDIA_BINARY_PREPARATION,
            )
            self.assertFalse(started.is_set())
            self.assertEqual(
                runtime_gate.wait_for_runtime_preparation(
                    key=runtime_gate.MEDIA_BINARY_PREPARATION
                ),
                "ready",
            )
            self.assertTrue(started.is_set())

    def test_optional_runtime_work_starts_without_blocking_local_startup(self) -> None:
        started = Event()
        release = Event()

        def prepare() -> str:
            started.set()
            release.wait(timeout=2)
            return "ready"

        with patch.object(runtime_gate, "_PREPARATIONS", {}):
            future = runtime_gate.start_runtime_preparation(prepare, key="test")
            self.assertTrue(started.wait(timeout=1))
            self.assertFalse(future.done())
            release.set()
            self.assertEqual(
                runtime_gate.wait_for_runtime_preparation(key="test"), "ready"
            )

    def test_feature_gates_do_not_block_one_another(self) -> None:
        media_ready = Event()

        with patch.object(runtime_gate, "_PREPARATIONS", {}):
            runtime_gate.start_runtime_preparation(
                lambda: media_ready.set() or "media",
                key=runtime_gate.MEDIA_BINARY_PREPARATION,
            )

            self.assertEqual(
                runtime_gate.wait_for_runtime_preparation(key="unconfigured"), None
            )
            self.assertTrue(media_ready.wait(timeout=1))
            self.assertEqual(
                runtime_gate.wait_for_runtime_preparation(
                    key=runtime_gate.MEDIA_BINARY_PREPARATION
                ),
                "media",
            )


if __name__ == "__main__":
    unittest.main()
