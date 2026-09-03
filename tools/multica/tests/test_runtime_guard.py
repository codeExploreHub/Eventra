"""Behavior tests for cross-process workflow execution guards."""

import importlib
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
