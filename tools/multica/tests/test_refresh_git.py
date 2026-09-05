"""Exercise Git guards against real local object graphs and bare remotes."""

import copy
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

    def test_full_refresh_delivery_uses_real_git_and_stops_at_merge_hold(self):
        from tools.multica import candidate_refresh as contracts
        from tools.multica import refresh_executor
        from tools.multica import workflow
        from tools.multica.tests.test_candidate_refresh import block, prepared_payload
        from tools.multica.tests.test_issue_contracts import issue_detail
        from tools.multica.tests.test_refresh_executor import MemoryRefreshAPI

        api = MemoryRefreshAPI()
        state = api.state
        source_child = state["children"][0]
        source_child["metadata"]["eventra.phase.sha.frontend"] = self.source
        source_child["detail"]["metadata"] = copy.deepcopy(
            source_child["metadata"]
        )
        source_child["evidence"]["content"] = (
            "Original Stage 1 PASS for " + self.source
        )
        state["metadata"]["eventra.workflow.frontend_sha"] = self.source
        state["parent"]["metadata"] = copy.deepcopy(state["metadata"])
        state["pr"]["head_sha"] = self.source
        state["prerequisite"].update(
            merge_sha=self.prerequisite,
            base_sha=self.prerequisite,
            ancestor_sha=self.prerequisite,
        )
        state["tool"]["git_version"] = self.version
        api.request = contracts.freeze_refresh_request(
            contracts.RefreshSnapshot(contracts.canonical_json(state))
        )
        request = api.request
        source_before = copy.deepcopy(source_child)
        base_before = self.git.read_ref("refs/heads/master")
        head_ref = "refs/heads/" + request.payload()["pr"]["head_ref"]
        self.run_git("push", "origin", self.source + ":" + head_ref)
        real_git = self.module.RefreshGit(self.repo, run=self.transport)

        class SyncedGit:
            def verify_candidate(_, current_request, target_sha):
                return real_git.verify_candidate(current_request, target_sha)

            def publish_candidate(_, current_request, prepared):
                changed = real_git.publish_candidate(current_request, prepared)
                state["pr"]["head_sha"] = real_git.read_ref(head_ref)
                return changed

        refresh_executor.stage_refresh_request(api, "PRO-900", request)
        api.publish_authorization()
        refresh_executor.execute_refresh(
            api,
            None,
            "PRO-900",
            uid(12),
            uid(13),
            contracts.refresh_action(request),
        )
        real_git.publish_staging(request, self.target)
        refresh_child = state["children"][1]
        evidence_payload = prepared_payload(request)
        evidence_payload.update(
            child_id=refresh_child["detail"]["id"],
            source_sha=self.source,
            prerequisite_sha=self.prerequisite,
            target_sha=self.target,
            tree_sha=self.tree,
            git_version=self.version,
        )
        evidence_payload["context_receipt"]["candidate_shas"] = {
            "frontend": self.target
        }
        evidence_uuid = uid(93)
        api.add_comment(
            "PRO-902",
            contracts.RefreshComment(
                refresh_child["detail"]["id"],
                evidence_uuid,
                request.payload()["assignment"]["engineer_id"],
                "agent",
                1,
                "Preparation only; no PR publication.\n"
                + block("prepared", evidence_payload),
            ),
        )
        refresh_executor.finish_refresh(
            api, SyncedGit(), "PRO-902", evidence_uuid, "pass"
        )
        result = refresh_executor.execute_refresh(
            api,
            SyncedGit(),
            "PRO-900",
            uid(12),
            uid(13),
            contracts.refresh_action(request),
        )

        self.assertEqual(result.status, "adopted")
        self.assertEqual(state["children"][0], source_before)
        self.assertEqual(real_git.read_ref("refs/heads/master"), base_before)
        self.assertEqual(real_git.read_ref(head_ref), self.target)
        for write in api.writes:
            self.assertNotIn(source_before["detail"]["id"], write)
            self.assertNotIn(source_before["detail"]["identifier"], write)
        push_calls = [
            call for call in self.transport.calls if "push" in call
        ]
        self.assertFalse(any(
            "refs/heads/master" in argument
            for call in push_calls
            for argument in call
        ))
        self.assertEqual(
            contracts.plan_refresh(request, api.snapshot("PRO-900")).kind,
            "create_gate_stage",
        )

        action = (
            "2:PRO-900:create_gate_stage:0:frontend:"
            + self.target
            + ":-:next-stage:3"
        )
        state["metadata"].update(
            {
                "eventra.workflow.next_stage": "4",
                "eventra.workflow.last_action": action,
            }
        )
        gate_comments = {}
        for index, (kind, role) in enumerate(
            (("review", "independent_reviewer"),
             ("qa", "integration_qa"))
        ):
            metadata = {
                "eventra.workflow.version": "2",
                "eventra.phase.kind": kind,
                "eventra.phase.attempt": "0",
                "eventra.phase.sha.frontend": self.target,
                "eventra.phase.creation_action": action,
                "eventra.phase.target": "repository:frontend",
                "eventra.phase.role": role,
            }
            identifier = f"PRO-{940 + index}"
            detail = issue_detail(
                id=uid(40 + index),
                identifier=identifier,
                parent_issue_id=uid(2),
                stage=3,
                project_id=uid(5),
                assignee_id=state["assignment"]["roles"][role],
                status="in_review",
                workspace_id=uid(1),
            )
            detail["metadata"] = copy.deepcopy(metadata)
            state["children"].append(
                {
                    "detail": detail,
                    "metadata": metadata,
                    "evidence": None,
                }
            )
            evidence_uuid = uid(42 + index)
            gate_comments[identifier] = [{
                "id": evidence_uuid,
                "issue_id": detail["id"],
                "author_id": detail["assignee_id"],
                "author_type": "agent",
                "content": kind + " PASS for " + self.target,
            }]
        state["parent"]["metadata"] = copy.deepcopy(state["metadata"])

        class GateRunner:
            def __init__(self):
                self.calls = []
                self.agents = [
                    {
                        "id": state["assignment"]["roles"][role],
                        "name": name,
                    }
                    for role, name in (
                        ("delivery_lead", "Eventra Delivery Lead"),
                        ("frontend_engineer", "Eventra Frontend Engineer"),
                        ("backend_engineer", "Eventra Backend Engineer"),
                        ("integration_qa", "Eventra Integration QA"),
                        ("independent_reviewer", "Eventra Independent Reviewer"),
                    )
                ]

            @staticmethod
            def _child(identifier):
                return next(
                    child for child in state["children"]
                    if child["detail"]["identifier"] == identifier
                )

            def run(self, args, *, stdin_json=None):
                if stdin_json is not None:
                    raise AssertionError("Gate runner never accepts stdin")
                call = tuple(args)
                self.calls.append(call)
                if call == ("agent", "list", "--output", "json"):
                    return copy.deepcopy(self.agents)
                if call == ("project", "list", "--output", "json"):
                    return [
                        {"id": state["assignment"]["projects"]["frontend"],
                         "title": "Eventra Local Development"},
                        {"id": state["assignment"]["projects"]["backend"],
                         "title": "Eventra Backend Local Development"},
                    ]
                if call == ("squad", "list", "--output", "json"):
                    return [{
                        "id": state["assignment"]["squad_id"],
                        "name": "Eventra Local Delivery",
                        "leader_id": state["assignment"]["lead_id"],
                    }]
                if call == (
                    "squad", "get", state["assignment"]["squad_id"],
                    "--output", "json",
                ):
                    return {
                        "id": state["assignment"]["squad_id"],
                        "name": "Eventra Local Delivery",
                        "leader_id": state["assignment"]["lead_id"],
                        "description": "Fixture",
                        "instructions": "Fixture",
                    }
                if call == (
                    "squad", "member", "list",
                    state["assignment"]["squad_id"], "--output", "json",
                ):
                    return [
                        {
                            "id": uid(100 + index),
                            "squad_id": state["assignment"]["squad_id"],
                            **copy.deepcopy(member),
                        }
                        for index, member in enumerate(
                            state["assignment"]["members"]
                        )
                    ]
                if call[:2] == ("issue", "get"):
                    identifier = call[2]
                    if identifier in {
                        state["parent"]["id"], state["parent"]["identifier"]
                    }:
                        return copy.deepcopy(state["parent"])
                    return copy.deepcopy(self._child(identifier)["detail"])
                if call == (
                    "issue", "children", state["parent"]["identifier"],
                    "--output", "json",
                ):
                    stages = []
                    for stage in sorted({
                        child["detail"]["stage"] for child in state["children"]
                    }):
                        issues = [
                            copy.deepcopy(child["detail"])
                            for child in state["children"]
                            if child["detail"]["stage"] == stage
                        ]
                        stages.append({
                            "stage": stage,
                            "total": len(issues),
                            "done": sum(
                                issue["status"] == "done" for issue in issues
                            ),
                            "issues": issues,
                        })
                    return {
                        "stages": stages,
                        "total": len(state["children"]),
                        "unstaged": [],
                    }
                if call[:3] == ("issue", "metadata", "list"):
                    identifier = call[3]
                    if identifier in {
                        state["parent"]["id"], state["parent"]["identifier"]
                    }:
                        return copy.deepcopy(state["metadata"])
                    return copy.deepcopy(self._child(identifier)["metadata"])
                if call[:3] == ("issue", "comment", "list"):
                    return copy.deepcopy(gate_comments.get(call[3], []))
                if call[:3] == ("issue", "runs"):
                    return copy.deepcopy([
                        run for run in state["runs"]
                        if run["issue_id"] == self._child(call[2])["detail"]["id"]
                    ])
                if call[:3] == ("issue", "metadata", "set"):
                    child = self._child(call[3])
                    key = args[args.index("--key") + 1]
                    value = args[args.index("--value") + 1]
                    if child["metadata"].get(key) != value:
                        child["metadata"][key] = value
                        child["detail"]["metadata"] = copy.deepcopy(
                            child["metadata"]
                        )
                        child["detail"]["revision"] += 1
                    return {"ok": True}
                if call[:2] == ("issue", "status"):
                    child = self._child(call[2])
                    child["detail"]["status"] = call[3]
                    child["detail"]["status_category"] = call[3]
                    child["detail"]["revision"] += 1
                    return copy.deepcopy(child["detail"])
                raise AssertionError(f"unexpected Gate argv: {call!r}")

        class RefreshReader:
            def snapshot(_, parent):
                if parent != "PRO-900":
                    raise AssertionError("cross-parent refresh read")
                return contracts.RefreshSnapshot(
                    contracts.canonical_json(state)
                )

        class GateGitHub:
            def run(_, args):
                return {
                    "url": state["pr"]["url"],
                    "headRefOid": self.target,
                    "state": "OPEN",
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "statusCheckRollup": [],
                }

        gate_runner = GateRunner()
        refresh_reader = RefreshReader()
        for index, (identifier, kind) in enumerate(
            (("PRO-940", "review"), ("PRO-941", "qa"))
        ):
            result = workflow.finish_phase(
                gate_runner,
                identifier,
                workflow.PhaseCompletion(
                    kind=kind,
                    result="pass",
                    attempt=0,
                    evidence_comment=uid(42 + index),
                    frontend_sha=self.target,
                    backend_sha=None,
                    pr_url=None,
                ),
                refresh_api=refresh_reader,
            )
            self.assertEqual((result.status, result.result), ("done", "pass"))
        parent = workflow.load_parent_snapshot(
            gate_runner,
            GateGitHub(),
            "PRO-900",
            refresh_api=refresh_reader,
        )
        decision = workflow.decide_parent_action(parent)

        self.assertEqual(
            (decision.kind, decision.reason),
            ("noop", "human merge approval required"),
        )
        mutation_trace = repr(api.writes).lower()
        self.assertNotIn("deploy", mutation_trace)
        self.assertNotIn("smoke", mutation_trace)
        self.assertEqual(
            [child["metadata"]["eventra.phase.kind"]
             for child in state["children"]],
            ["implementation", "refresh", "review", "qa"],
        )

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
