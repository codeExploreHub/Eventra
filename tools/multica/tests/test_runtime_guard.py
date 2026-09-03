"""Behavior tests for cross-process workflow execution guards."""

import importlib
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path


def _hold_claim(root, parent, action_key, ready, release, result_queue):
    guard = importlib.import_module("tools.multica.runtime_guard")
    with guard.file_single_flight(
        parent,
        action_key,
        root=Path(root),
    ) as claim:
        result_queue.put((claim.acquired, claim.outcome))
        ready.set()
        release.wait(5)


def _crash_with_claim(root, parent, action_key, ready):
    guard = importlib.import_module("tools.multica.runtime_guard")
    with guard.file_single_flight(
        parent,
        action_key,
        root=Path(root),
    ) as claim:
        if not claim.acquired:
            os._exit(2)
        ready.set()
        os._exit(0)


class FileSingleFlightTests(unittest.TestCase):
    def test_first_exact_parent_action_claim_is_acquired(self):
        try:
            guard = importlib.import_module("tools.multica.runtime_guard")
        except ModuleNotFoundError:
            self.fail("runtime guard module is missing")

        with tempfile.TemporaryDirectory() as directory:
            with guard.file_single_flight(
                "PRO-122",
                "2:PRO-122:create_smoke_stage:0:frontend",
                root=Path(directory),
            ) as claim:
                self.assertTrue(claim.acquired)
                self.assertEqual(claim.outcome, "acquired")

    def test_same_parent_action_contender_returns_without_writing(self):
        guard = importlib.import_module("tools.multica.runtime_guard")
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            ready = context.Event()
            release = context.Event()
            result_queue = context.Queue()
            process = context.Process(
                target=_hold_claim,
                args=(directory, "PRO-122", "action-a", ready, release, result_queue),
            )
            process.start()
            self.assertTrue(ready.wait(3))
            self.assertEqual(result_queue.get(timeout=1), (True, "acquired"))
            timer = threading.Timer(1.0, release.set)
            timer.start()
            started = time.monotonic()
            try:
                with guard.file_single_flight(
                    "PRO-122", "action-a", root=Path(directory)
                ) as claim:
                    elapsed = time.monotonic() - started
                    self.assertFalse(claim.acquired)
                    self.assertEqual(claim.outcome, "wait")
                    self.assertEqual(claim.holder_action_key, "action-a")
                    self.assertLess(elapsed, 0.5)
            finally:
                release.set()
                timer.cancel()
                process.join(3)
            self.assertEqual(process.exitcode, 0)

    def test_different_action_for_same_parent_conflicts_without_waiting(self):
        guard = importlib.import_module("tools.multica.runtime_guard")
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            ready = context.Event()
            release = context.Event()
            result_queue = context.Queue()
            process = context.Process(
                target=_hold_claim,
                args=(directory, "PRO-122", "action-a", ready, release, result_queue),
            )
            process.start()
            self.assertTrue(ready.wait(3))
            self.assertEqual(result_queue.get(timeout=1), (True, "acquired"))
            try:
                with guard.file_single_flight(
                    "PRO-122", "action-b", root=Path(directory)
                ) as claim:
                    self.assertFalse(claim.acquired)
                    self.assertEqual(claim.outcome, "conflict")
                    self.assertEqual(claim.holder_action_key, "action-a")
            finally:
                release.set()
                process.join(3)
            self.assertEqual(process.exitcode, 0)

    def test_process_interruption_releases_lock_and_marks_stale_recovery(self):
        guard = importlib.import_module("tools.multica.runtime_guard")
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            ready = context.Event()
            process = context.Process(
                target=_crash_with_claim,
                args=(directory, "PRO-122", "action-a", ready),
            )
            process.start()
            self.assertTrue(ready.wait(3))
            process.join(3)
            self.assertEqual(process.exitcode, 0)

            with guard.file_single_flight(
                "PRO-122", "action-a", root=Path(directory)
            ) as claim:
                self.assertTrue(claim.acquired)
                self.assertTrue(claim.recovered_stale)
                self.assertEqual(claim.outcome, "acquired")


if __name__ == "__main__":
    unittest.main()
