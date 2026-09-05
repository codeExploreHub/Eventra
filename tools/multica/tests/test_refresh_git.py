"""Exercise Git guards against real local object graphs and bare remotes."""

import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.multica.candidate_refresh import PreparedCandidate, build_request
from tools.multica.tests.test_candidate_refresh import request_payload, uid


ORIGIN = "https://github.com/codeExploreHub/Eventra.git"


class LocalTransport:
    """Only replace network transport; all Git operations still really execute."""

    def __init__(self, remote):
        self.remote = remote
        self.calls = []
        self.lose_push_ack = False
        self.race = None

    def __call__(self, argv, *, cwd, env):
        self.calls.append(tuple(argv))
        if "push" in argv and self.race:
            race, self.race = self.race, None
            race()
        mapped = [str(self.remote) if arg == ORIGIN else arg for arg in argv]
        # No test is allowed to fall through to a real network destination.
        if any(arg.startswith(("https://", "ssh://", "git@")) for arg in mapped):
            raise AssertionError("unexpected external network destination")
        result = subprocess.run(mapped, cwd=cwd, env={**env, "GIT_ALLOW_PROTOCOL": "file"},
                                capture_output=True, text=True, timeout=30)
        if "push" in argv and self.lose_push_ack and result.returncode == 0:
            self.lose_push_ack = False
            raise subprocess.TimeoutExpired(argv, 30)
        return result


class GitTests(unittest.TestCase):
    def setUp(self):
        name = "tools.multica.refresh_git"
        self.assertIsNotNone(importlib.util.find_spec(name), "refresh Git guard not implemented")
        self.module = importlib.import_module(name)
        self.temporary = tempfile.TemporaryDirectory(prefix="eventra-refresh-git-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "engineer"
        self.remote = self.root / "remote.git"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_SYSTEM=os.devnull, GIT_AUTHOR_NAME="Test",
                        GIT_AUTHOR_EMAIL="test@example.invalid", GIT_COMMITTER_NAME="Test",
                        GIT_COMMITTER_EMAIL="test@example.invalid", GIT_TERMINAL_PROMPT="0")
        self.run_at(self.root, "init", "--bare", "--template=", str(self.remote))
        self.run_at(self.root, "init", "--template=", str(self.repo))
        (self.repo / "common").write_text("base\n")
        self.run_git("add", "common")
        self.run_git("commit", "-m", "base")
        self.base = self.run_git("rev-parse", "HEAD")
        self.run_git("switch", "-c", "candidate")
        (self.repo / "frontend").write_text("source\n")
        self.run_git("add", "frontend")
        self.run_git("commit", "-m", "source")
        self.source = self.run_git("rev-parse", "HEAD")
        self.run_git("switch", "-c", "prerequisite", self.base)
        (self.repo / "knowledge").write_text("prerequisite\n")
        self.run_git("add", "knowledge")
        self.run_git("commit", "-m", "prerequisite")
        self.prerequisite = self.run_git("rev-parse", "HEAD")
        self.run_git("remote", "add", "origin", str(self.remote))
        self.run_git("push", "origin", self.source + ":refs/heads/candidate",
                     self.prerequisite + ":refs/heads/master")
        self.version = self.run_git("version")
        payload = request_payload()
        payload["source"]["sha"] = self.source
        payload["pr"]["head_ref"] = "candidate"
        payload["prerequisite"]["merge_sha"] = self.prerequisite
        payload["prerequisite"]["base_sha"] = self.prerequisite
        payload["git_version"] = self.version
        self.request = build_request(payload)
        self.transport = LocalTransport(self.remote)
        self.git = self.module.RefreshGit(self.repo, run=self.transport)
        # Build an independent expected merge with normal Git, not the guard under test.
        self.run_git("switch", "candidate")
        self.run_git("merge", "--no-ff", "-m", "combine", self.prerequisite)
        self.target = self.run_git("rev-parse", "HEAD")
        self.tree = self.run_git("rev-parse", "HEAD^{tree}")
        self.prepared = PreparedCandidate(self.request.digest, uid(9), self.source,
                                         self.prerequisite, self.target, self.tree,
                                         uid(12), "c" * 64, self.request.staging_ref)

    def run_at(self, cwd, *args):
        result = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True,
                                text=True, timeout=30)
        if result.returncode:
            raise AssertionError(f"test Git command failed: {args}: {result.stderr}")
        return result.stdout.strip()

    def run_git(self, *args):
        return self.run_at(self.repo, *args)

    def stage(self, sha=None):
        self.run_git("push", "origin", (sha or self.target) + ":" + self.request.staging_ref)

    def test_clean_two_parent_merge_is_verified_from_staging_ref(self):
        self.stage()
        self.assertEqual(self.git.verify_candidate(self.request, self.target), self.tree)
        self.assertEqual(self.git.expected_tree(self.source, self.prerequisite, self.version), self.tree)

    def test_local_object_without_remote_staging_proof_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.git.verify_candidate(self.request, self.target)

    def test_reversed_parents_are_rejected(self):
        swapped = self.run_git("commit-tree", self.tree, "-p", self.prerequisite,
                              "-p", self.source, "-m", "wrong order")
        self.stage(swapped)
        with self.assertRaisesRegex(RuntimeError, "parents"):
            self.git.verify_candidate(self.request, swapped)

    def test_extra_code_with_correct_parents_is_rejected(self):
        (self.repo / "unapproved").write_text("extra\n")
        self.run_git("add", "unapproved")
        tree = self.run_git("write-tree")
        forged = self.run_git("commit-tree", tree, "-p", self.source,
                             "-p", self.prerequisite, "-m", "extra")
        self.stage(forged)
        with self.assertRaisesRegex(RuntimeError, "tree"):
            self.git.verify_candidate(self.request, forged)

    def test_single_parent_or_extra_parent_is_rejected(self):
        for parents in ((self.source,), (self.source, self.prerequisite, self.base)):
            args = ["commit-tree", self.tree, "-m", "wrong parent count"]
            for parent in parents:
                args += ["-p", parent]
            forged = self.run_git(*args)
            # Each probe has a fresh deterministic ref request; no force pushes.
            payload = self.request.payload()
            payload["parent"]["revision"] += len(parents)
            request = build_request(payload)
            self.run_git("push", "origin", forged + ":" + request.staging_ref)
            with self.assertRaisesRegex(RuntimeError, "parents"):
                self.git.verify_candidate(request, forged)

    def test_dirty_runtime_checkout_and_index_remain_untouched(self):
        self.stage()
        (self.repo / "common").write_text("local only\n")
        self.run_git("add", "common")
        before = self.run_git("status", "--porcelain"), self.run_git("write-tree")
        self.assertEqual(self.git.verify_candidate(self.request, self.target), self.tree)
        self.assertEqual((self.run_git("status", "--porcelain"), self.run_git("write-tree")), before)

    def test_conflict_and_noop_refresh_are_rejected(self):
        self.run_git("switch", "-c", "left", self.base)
        (self.repo / "common").write_text("left\n")
        self.run_git("commit", "-am", "left")
        left = self.run_git("rev-parse", "HEAD")
        self.run_git("switch", "-c", "right", self.base)
        (self.repo / "common").write_text("right\n")
        self.run_git("commit", "-am", "right")
        right = self.run_git("rev-parse", "HEAD")
        with self.assertRaisesRegex(RuntimeError, "conflict"):
            self.git.expected_tree(left, right, self.version)
        with self.assertRaises(RuntimeError):
            self.git.expected_tree(self.target, self.prerequisite, self.version)

    def test_replace_refs_missing_objects_and_git_version_drift_are_rejected(self):
        with self.assertRaises(RuntimeError):
            self.git.expected_tree(self.source, self.prerequisite, "git version 0.0.0")
        with self.assertRaises(RuntimeError):
            self.git.expected_tree("0" * 40, self.prerequisite, self.version)
        self.run_git("replace", self.source, self.prerequisite)
        with self.assertRaisesRegex(RuntimeError, "replace"):
            self.git.expected_tree(self.source, self.prerequisite, self.version)

    def test_custom_merge_driver_is_rejected_without_executing_it(self):
        marker = self.root / "driver-executed"
        self.run_git("switch", "-c", "driver", self.source)
        (self.repo / ".gitattributes").write_text("common merge=untrusted\n")
        self.run_git("add", ".gitattributes")
        self.run_git("commit", "-m", "driver")
        source = self.run_git("rev-parse", "HEAD")
        self.run_git("config", "merge.untrusted.driver", f"touch {marker}")
        with self.assertRaisesRegex(RuntimeError, "driver"):
            self.git.expected_tree(source, self.prerequisite, self.version)
        self.assertFalse(marker.exists())

    def test_staging_publish_and_retry_are_idempotent(self):
        self.assertTrue(self.git.publish_staging(self.request, self.target))
        self.assertFalse(self.git.publish_staging(self.request, self.target))
        self.assertEqual(self.git.read_ref(self.request.staging_ref), self.target)
        self.assertEqual(self.git.read_ref("refs/heads/candidate"), self.source)

    def test_wrong_existing_staging_ref_is_not_overwritten(self):
        self.stage(self.source)
        with self.assertRaises(RuntimeError):
            self.git.publish_staging(self.request, self.target)
        self.assertEqual(self.git.read_ref(self.request.staging_ref), self.source)

    def test_registered_candidate_is_published_without_force_or_other_ref_updates(self):
        self.stage()
        self.assertTrue(self.git.publish_candidate(self.request, self.prepared))
        self.assertFalse(self.git.publish_candidate(self.request, self.prepared))
        self.assertEqual(self.git.read_ref("refs/heads/candidate"), self.target)
        self.assertEqual(self.git.read_ref("refs/heads/master"), self.prerequisite)
        for call in self.transport.calls:
            self.assertFalse(any(arg.startswith(("--force", "+")) for arg in call))

    def test_base_drift_blocks_publication(self):
        self.stage()
        self.run_git("push", "origin", self.target + ":refs/heads/master")
        with self.assertRaisesRegex(RuntimeError, "base"):
            self.git.publish_candidate(self.request, self.prepared)
        self.assertEqual(self.git.read_ref("refs/heads/candidate"), self.source)

    def test_base_drift_during_publication_is_detected_after_managed_push(self):
        self.stage()

        def concurrent_base_push():
            self.run_git("push", "origin", self.target + ":refs/heads/master")

        self.transport.race = concurrent_base_push
        with self.assertRaisesRegex(RuntimeError, "base drift after publication"):
            self.git.publish_candidate(self.request, self.prepared)
        self.assertEqual(self.git.read_ref("refs/heads/candidate"), self.target)
        self.assertEqual(self.git.read_ref("refs/heads/master"), self.target)

    def test_divergent_concurrent_push_is_not_overwritten(self):
        self.stage()
        other = self.run_git("commit-tree", self.tree, "-p", self.source, "-m", "other writer")
        def concurrent_push():
            self.run_git("push", "origin", other + ":refs/heads/candidate")
        self.transport.race = concurrent_push
        with self.assertRaises(RuntimeError):
            self.git.publish_candidate(self.request, self.prepared)
        self.assertEqual(self.git.read_ref("refs/heads/candidate"), other)

    def test_ack_loss_is_reconciled_by_reading_remote(self):
        self.stage()
        self.transport.lose_push_ack = True
        self.assertTrue(self.git.publish_candidate(self.request, self.prepared))
        self.assertFalse(self.git.publish_candidate(self.request, self.prepared))
        pushes = [call for call in self.transport.calls if "push" in call]
        self.assertEqual(len(pushes), 1)

    def test_ref_injection_is_rejected_before_transport(self):
        before = len(self.transport.calls)
        for ref in ("--upload-pack=evil", "refs/heads/x..y", "refs/tags/v1", "refs/heads/x\nother"):
            with self.subTest(ref=ref), self.assertRaises((RuntimeError, ValueError)):
                self.git.read_ref(ref)
        self.assertEqual(len(self.transport.calls), before)


if __name__ == "__main__":
    unittest.main()
