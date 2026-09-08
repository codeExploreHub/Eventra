"""Behavior tests for exact-SHA business candidate purity validation."""

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.multica.candidate_purity import validate_candidate


class CandidatePurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test Agent")
        self.git("config", "user.email", "agent@example.invalid")
        self.git(
            "remote", "add", "origin",
            "https://github.com/codeExploreHub/Eventra-Backend.git",
        )
        self.write("README.md", "base\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "base")
        self.base = self.sha()

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def sha(self):
        return self.git("rev-parse", "HEAD")

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def commit(self, name, value, message):
        self.write(name, value)
        self.git("add", name)
        self.git("commit", "-qm", message)
        return self.sha()

    def test_accepts_clean_descendant_and_returns_bound_receipt(self):
        candidate = self.commit("scripts/smoke-local.sh", "echo ok\n", "change")

        receipt = validate_candidate(
            self.root, "backend", self.base, candidate)

        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(receipt["repository"], "backend")
        self.assertEqual(
            receipt["origin"],
            "https://github.com/codeExploreHub/Eventra-Backend.git",
        )
        self.assertEqual(receipt["base_sha"], self.base)
        self.assertEqual(receipt["candidate_sha"], candidate)
        self.assertEqual(receipt["changed_paths"], ["scripts/smoke-local.sh"])
        self.assertRegex(receipt["receipt_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            receipt["receipt_digest"],
            validate_candidate(self.root, "backend", self.base, candidate)[
                "receipt_digest"
            ],
        )

    def test_rejects_runtime_artifact_in_candidate_ancestry(self):
        self.commit(".worktrees/eventra-knowledge-loop", "gitlink\n", "pollution")
        candidate = self.commit("scripts/smoke-local.sh", "echo ok\n", "change")

        with self.assertRaisesRegex(
            RuntimeError, r"forbidden runtime paths: \.worktrees"
        ):
            validate_candidate(self.root, "backend", self.base, candidate)

    def test_rejects_runtime_evidence_and_root_agent_contract(self):
        for path in (".multica/PRO-137.patch", ".agent_context/receipt.json", "AGENTS.md"):
            with self.subTest(path=path):
                self.git("reset", "--hard", self.base)
                candidate = self.commit(path, "runtime\n", "pollution")
                with self.assertRaisesRegex(RuntimeError, "forbidden runtime paths"):
                    validate_candidate(self.root, "backend", self.base, candidate)

    def test_rejects_candidate_that_is_not_descended_from_base(self):
        candidate = self.commit("other.txt", "other\n", "other")
        self.git("reset", "--hard", self.base)
        later_base = self.commit("base.txt", "later\n", "later base")

        with self.assertRaisesRegex(RuntimeError, "not a descendant"):
            validate_candidate(self.root, "backend", later_base, candidate)

    def test_rejects_unknown_repository_and_empty_candidate(self):
        with self.assertRaisesRegex(ValueError, "repository"):
            validate_candidate(self.root, "api", self.base, self.base)
        with self.assertRaisesRegex(RuntimeError, "no business changes"):
            validate_candidate(self.root, "backend", self.base, self.base)

    def test_rejects_repository_label_that_does_not_match_origin(self):
        candidate = self.commit("scripts/smoke-local.sh", "echo ok\n", "change")

        with self.assertRaisesRegex(RuntimeError, "origin does not match"):
            validate_candidate(self.root, "frontend", self.base, candidate)


class CandidatePurityCLITests(unittest.TestCase):
    def test_parser_exposes_validate_candidate_command(self):
        from tools.multica.workflow import build_workflow_parser

        args = build_workflow_parser().parse_args(
            [
                "validate-candidate",
                "--repository",
                "backend",
                "--repository-root",
                "/tmp/backend",
                "--base-sha",
                "a" * 40,
                "--candidate-sha",
                "b" * 40,
            ]
        )
        self.assertEqual(args.command, "validate-candidate")
        self.assertEqual(args.repository, "backend")

    def test_cli_returns_machine_readable_block_instead_of_traceback(self):
        from tools.multica import workflow

        output = io.StringIO()
        with patch.object(
            workflow,
            "validate_candidate",
            side_effect=RuntimeError("candidate contains forbidden runtime paths"),
        ), redirect_stdout(output):
            exit_code = workflow.main(
                [
                    "validate-candidate",
                    "--repository", "backend",
                    "--repository-root", "/tmp/backend",
                    "--base-sha", "a" * 40,
                    "--candidate-sha", "b" * 40,
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "decision": "blocked",
                "reason": "candidate contains forbidden runtime paths",
            },
        )
