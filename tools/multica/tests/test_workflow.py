"""Behavior tests for deterministic Eventra Multica workflow transitions."""

import copy
import hashlib
import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import patch

import tools.multica.workflow as workflow_module
from tools.multica.workflow import (
    AuthorizingComment,
    ChildRunSnapshot,
    ParentDecision,
    ParentSnapshot,
    PhaseCompletion,
    PhaseSnapshot,
    PullRequestSnapshot,
    RecoveryDecision,
    WorkflowSnapshot,
    WatchResult,
    _action_key,
    _build_repair_reservation,
    _failure_bundle,
    _repair_child_specs,
    _string_metadata_filter,
    build_phase_metadata,
    build_workflow_parser,
    decide_parent_action,
    decide_recovery,
    execute_parent_repair,
    finish_parent,
    finish_phase,
    load_parent_snapshot,
    print_phase_result,
    print_parent_decision,
    print_parent_result,
    recover_once,
    print_watch_result,
    watch_projects,
)


ISSUE_ID = "01a00000-0000-7000-8000-000000000002"
PARENT_ID = "01a00000-0000-7000-8000-000000000001"
AGENT_ID = "00000000-0000-4000-8000-000000000004"
PROJECT_ID = "00000000-0000-4000-8000-000000000003"
BACKEND_PROJECT_ID = "00000000-0000-4000-8000-000000000013"
REVIEWER_ID = "00000000-0000-4000-8000-000000000014"
QA_ID = "00000000-0000-4000-8000-000000000015"
COMMENT_ID = "01a00000-0000-7000-8000-000000000010"
FRONTEND_SHA = "a" * 40
FRONTEND_PR = "https://github.com/codeExploreHub/Eventra/pull/6"


def raw_issue(**overrides):
    value = {
        "id": ISSUE_ID,
        "identifier": "PRO-36",
        "parent_issue_id": PARENT_ID,
        "stage": 1,
        "status": "in_review",
        "assignee_id": AGENT_ID,
        "assignee_type": "agent",
        "project_id": PROJECT_ID,
        "updated_at": "2026-08-25T08:50:06Z",
        "title": "Existing issue",
        "description": "Existing issue description",
    }
    value.update(overrides)
    return value


class FakeWorkflowRunner:
    """Stateful argv fake at the Multica process boundary."""

    def __init__(self):
        self.issue = raw_issue()
        self.parent = raw_issue(
            id=PARENT_ID,
            identifier="PRO-35",
            parent_issue_id=None,
            stage=None,
            status="in_progress",
            assignee_type="squad",
        )
        self.metadata = {}
        self.parent_metadata = {
            "eventra.workflow.version": "2",
            "eventra.workflow.classification": "frontend-only",
            "eventra.workflow.next_stage": "2",
            "eventra.workflow.attempt": "0",
            "eventra.workflow.frontend_sha": FRONTEND_SHA,
            "eventra.workflow.merge_state": "not_ready",
            "eventra.workflow.last_action": "",
        }
        self.include_child = True
        self.calls = []
        self.fail_metadata_key = None
        self.corrupt_metadata_key = None
        self.inject_metadata_after_sets = None
        self.freeze_status = False
        self.change_metadata_after_first_post_write_read = False
        self.drift_parent_before_status = False
        self.drift_child_before_status = False
        self.drift_post_done_metadata = False
        self.drift_post_done_detail = False
        self._post_write_metadata_reads = 0
        self._post_done_metadata_reads = 0
        self._post_done_detail_reads = 0

    @property
    def mutation_count(self):
        return sum(call[0:3] in {
            ("issue", "metadata", "set"),
            ("issue", "status", "PRO-36"),
        } for call in self.calls)

    def run(self, args, *, stdin_json=None):
        if stdin_json is not None:
            raise AssertionError("workflow commands never accept stdin JSON")
        call = tuple(args)
        self.calls.append(call)
        if call == ("issue", "get", "PRO-36", "--output", "json"):
            if self.issue["status"] == "done":
                self._post_done_detail_reads += 1
                if (
                    self.drift_post_done_detail
                    and self._post_done_detail_reads >= 2
                ):
                    self.issue["status"] = "blocked"
            return copy.deepcopy(self.issue)
        if call == ("issue", "get", PARENT_ID, "--output", "json"):
            return copy.deepcopy(self.parent)
        if call == ("issue", "children", "PRO-35", "--output", "json"):
            issues = [copy.deepcopy(self.issue)] if self.include_child else []
            return {
                "stages": (
                    []
                    if not issues
                    else [
                        {
                            "stage": self.issue["stage"],
                            "total": 1,
                            "done": int(self.issue["status"] == "done"),
                            "issues": issues,
                        }
                    ]
                ),
                "total": len(issues),
                "unstaged": [],
            }
        if call == (
            "issue", "metadata", "list", "PRO-36", "--output", "json"
        ):
            value = copy.deepcopy(self.metadata)
            after_writes = any(
                previous[:3] == ("issue", "metadata", "set")
                for previous in self.calls[:-1]
            )
            if after_writes and self.issue["status"] != "done":
                self._post_write_metadata_reads += 1
                if (
                    self.change_metadata_after_first_post_write_read
                    and self._post_write_metadata_reads >= 2
                ):
                    value["eventra.phase.result"] = "fail"
                if self._post_write_metadata_reads >= 2:
                    if self.drift_parent_before_status:
                        self.parent_metadata["eventra.workflow.next_stage"] = "3"
                    if self.drift_child_before_status:
                        self.issue["stage"] = 2
            if self.issue["status"] == "done":
                self._post_done_metadata_reads += 1
                if (
                    self.drift_post_done_metadata
                    and self._post_done_metadata_reads >= 2
                ):
                    value["eventra.phase.result"] = "fail"
            if self.corrupt_metadata_key is not None and any(
                previous[:3] == ("issue", "metadata", "set")
                for previous in self.calls[:-1]
            ):
                value[self.corrupt_metadata_key] = "corrupt"
            if self.inject_metadata_after_sets is not None and any(
                previous[:3] == ("issue", "metadata", "set")
                for previous in self.calls[:-1]
            ):
                key, item = self.inject_metadata_after_sets
                value[key] = item
            return value
        if call in {
            ("issue", "metadata", "list", PARENT_ID, "--output", "json"),
            ("issue", "metadata", "list", "PRO-35", "--output", "json"),
        }:
            return copy.deepcopy(self.parent_metadata)
        if call[:3] == ("issue", "metadata", "set"):
            self.assert_metadata_set_grammar(call)
            key = call[5]
            if key == self.fail_metadata_key:
                raise RuntimeError("Multica command failed with exit 1")
            self.metadata[key] = call[7]
            return {"ignored": "mutation acknowledgement"}
        if call == (
            "issue", "status", "PRO-36", "done", "--no-start", "--output", "json"
        ):
            if not self.freeze_status:
                self.issue["status"] = "done"
                self.issue["updated_at"] = "2026-08-25T09:00:00Z"
            return {"ignored": "mutation acknowledgement"}
        raise AssertionError(f"unsupported argv: {call!r}")

    def assert_metadata_set_grammar(self, call):
        self.assertEqual(call[0:4], ("issue", "metadata", "set", "PRO-36"))
        self.assertEqual(call[4], "--key")
        self.assertEqual(call[6], "--value")
        self.assertEqual(call[8:12], ("--type", "string", "--output", "json"))

    def assertEqual(self, left, right):
        if left != right:
            raise AssertionError(f"{left!r} != {right!r}")


class FakeSnapshotFinishRunner:
    """Authoritative Multica reads for one snapshot-backed completion."""

    def __init__(self, snapshot, target_key, *, repair_replay=False):
        self.snapshot = snapshot
        self.target_key = target_key
        self.parent = raw_issue(
            id=PARENT_ID,
            identifier=snapshot.identifier,
            parent_issue_id=None,
            stage=None,
            status=snapshot.parent_status,
            assignee_type="squad",
        )
        self.issues = {}
        self.metadata = {}
        self.runs = {}
        for index, item in enumerate(snapshot.children, start=100):
            issue_id = f"01a00000-0000-7000-8000-{index:012d}"
            self.issues[item.issue_key] = raw_issue(
                id=issue_id,
                identifier=item.issue_key,
                parent_issue_id=PARENT_ID,
                stage=item.stage,
                status=item.status,
                project_id=item.project_id or PROJECT_ID,
                assignee_id=item.assignee_id or AGENT_ID,
                assignee_type=item.assignee_type,
            )
            metadata = {
                "eventra.workflow.version": str(item.workflow_version),
                "eventra.phase.kind": item.kind,
                "eventra.phase.attempt": str(item.attempt),
                "eventra.phase.failure_repositories": json.dumps(
                    list(item.responsible_repositories),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
            if item.result is not None:
                metadata.update(
                    {
                        "eventra.phase.result": item.result,
                        "eventra.phase.evidence_comment": item.evidence_comment,
                    }
                )
            if item.evidence_comment_url is not None:
                metadata["eventra.phase.evidence_comment_url"] = (
                    item.evidence_comment_url
                )
            if item.frontend_sha is not None:
                metadata["eventra.phase.sha.frontend"] = item.frontend_sha
            if item.backend_sha is not None:
                metadata["eventra.phase.sha.backend"] = item.backend_sha
            if item.pr_url:
                metadata["eventra.phase.pr"] = item.pr_url
            if item.creation_action:
                if item.kind == "repair":
                    metadata.update(
                    {
                        "eventra.repair.creation_action": item.creation_action,
                        "eventra.repair.failure_bundle_digest": (
                            item.failure_bundle_digest
                        ),
                        "eventra.repair.failure_evidence_uuids": json.dumps(
                            list(item.failure_evidence_uuids),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        "eventra.repair.authorizing_comment_uuid": (
                            item.authorizing_comment_uuid
                        ),
                        "eventra.repair.repository": item.repair_repository,
                        "eventra.repair.pull_request": item.repair_pull_request,
                        "eventra.repair.round": str(item.repair_round),
                        "eventra.repair.source_candidates": json.dumps(
                            dict(item.repair_source_candidates),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    }
                    )
                else:
                    metadata.update(
                        {
                            "eventra.phase.creation_action": item.creation_action,
                            "eventra.phase.target": item.phase_target,
                            "eventra.phase.role": item.phase_role,
                        }
                    )
            self.metadata[item.issue_key] = metadata
            self.runs[item.issue_key] = (
                [
                    {
                        "id": f"run-{item.issue_key}",
                        "issue_id": issue_id,
                        "status": "running",
                        "created_at": "2026-08-25T09:00:00Z",
                        "dispatched_at": "2026-08-25T09:00:01Z",
                        "started_at": "2026-08-25T09:00:02Z",
                        "completed_at": None,
                    }
                ]
                if item.status in {"todo", "in_progress", "in_review"}
                else []
            )
        self.parent_metadata = {
            "eventra.workflow.version": str(snapshot.workflow_version),
            "eventra.workflow.classification": snapshot.classification,
            "eventra.workflow.next_stage": str(snapshot.next_stage),
            "eventra.workflow.attempt": str(snapshot.attempt),
            "eventra.workflow.merge_state": snapshot.merge_state,
            "eventra.workflow.last_action": snapshot.last_action or "",
        }
        if snapshot.candidate_frontend_sha is not None:
            self.parent_metadata["eventra.workflow.frontend_sha"] = (
                snapshot.candidate_frontend_sha
            )
        if snapshot.candidate_backend_sha is not None:
            self.parent_metadata["eventra.workflow.backend_sha"] = (
                snapshot.candidate_backend_sha
            )
        if snapshot.consumed_authorization_uuid:
            self.parent_metadata[
                workflow_module.REPAIR_AUTHORIZATION_CONSUMED_KEY
            ] = snapshot.consumed_authorization_uuid
        if repair_replay:
            self._install_repair_replay_identity()
        self.calls = []

    def _install_repair_replay_identity(self):
        repairs = tuple(
            item
            for item in self.snapshot.children
            if item.kind == "repair"
            and item.creation_action == self.snapshot.last_action
        )
        action_next_stage, source_stage = (
            workflow_module._repair_action_stage_identity(
                self.snapshot.last_action
            )
        )
        repair_round = repairs[0].repair_round
        authorization_uuid = repairs[0].authorizing_comment_uuid
        source_candidates = dict(repairs[0].repair_source_candidates)
        source_snapshot = workflow_module._snapshot_with_candidates(
            replace(
                self.snapshot,
                attempt=repair_round - 1,
                next_stage=action_next_stage,
                last_action=None,
                authorization_comment_uuid="",
                authorizing_comment=None,
                repair_reservation=None,
            ),
            source_candidates,
        )
        source_phases = tuple(
            item
            for item in self.snapshot.children
            if item.stage == source_stage
        )
        bundle = _failure_bundle(source_snapshot, source_phases)
        reservation = workflow_module._decode_repair_reservation(
            workflow_module._canonical_json(
                {
                    "action_key": self.snapshot.last_action,
                    "authorizing_comment_uuid": authorization_uuid,
                    "child_specs": _repair_child_specs(source_snapshot, bundle),
                    "failure_bundle": bundle,
                    "next_stage": action_next_stage,
                    "parent_identifier": self.snapshot.identifier,
                    "previous_last_action": "",
                    "prior_consumed_authorization_uuid": "",
                    "repair_round": repair_round,
                    "source_attempt": repair_round - 1,
                    "source_candidates": source_candidates,
                }
            )
        )
        specs = {
            str(spec["repository"]): spec
            for spec in reservation["child_specs"]
        }
        for repair in repairs:
            spec = specs[repair.repair_repository]
            issue = self.issues[repair.issue_key]
            issue["title"] = workflow_module._repair_child_title(
                reservation,
                spec,
            )
            issue["description"] = workflow_module._render_repair_handoff(
                reservation,
                spec,
            )

    @property
    def mutation_count(self):
        return sum(
            call[:3] == ("issue", "metadata", "set")
            or call[:3] == ("issue", "status", self.target_key)
            for call in self.calls
        )

    def _children_payload(self):
        stages = []
        for stage in sorted({item["stage"] for item in self.issues.values()}):
            issues = [
                copy.deepcopy(item)
                for item in self.issues.values()
                if item["stage"] == stage
            ]
            stages.append(
                {
                    "stage": stage,
                    "total": len(issues),
                    "done": sum(item["status"] == "done" for item in issues),
                    "issues": issues,
                }
            )
        return {"stages": stages, "total": len(self.issues), "unstaged": []}

    @staticmethod
    def _flag(args, name):
        return args[args.index(name) + 1]

    def run(self, args, *, stdin_json=None):
        if stdin_json is not None:
            raise AssertionError("snapshot completion never accepts stdin JSON")
        call = tuple(args)
        self.calls.append(call)
        if call[:2] == ("issue", "get"):
            identifier = call[2]
            if identifier in {PARENT_ID, self.snapshot.identifier}:
                return copy.deepcopy(self.parent)
            return copy.deepcopy(self.issues[identifier])
        if call == (
            "issue", "children", self.snapshot.identifier, "--output", "json"
        ):
            return self._children_payload()
        if call[:3] == ("issue", "metadata", "list"):
            identifier = call[3]
            if identifier in {PARENT_ID, self.snapshot.identifier}:
                return copy.deepcopy(self.parent_metadata)
            return copy.deepcopy(self.metadata[identifier])
        if call[:3] == ("issue", "metadata", "set"):
            identifier = call[3]
            self.metadata[identifier][self._flag(args, "--key")] = self._flag(
                args,
                "--value",
            )
            return {"ok": True}
        if call[:2] == ("issue", "runs"):
            return copy.deepcopy(self.runs[call[2]])
        if call[:3] == ("issue", "status", self.target_key):
            self.issues[self.target_key]["status"] = call[3]
            return copy.deepcopy(self.issues[self.target_key])
        raise AssertionError(f"unsupported snapshot completion argv: {call!r}")


class FakeSnapshotGitHubRunner:
    def __init__(self, pull_requests):
        self.pull_requests = {item.url: item for item in pull_requests}
        self.calls = []

    def run(self, args):
        self.calls.append(tuple(args))
        item = self.pull_requests[args[2]]
        return {
            "url": item.url,
            "headRefOid": item.head_sha,
            "state": item.state.upper(),
            "mergeable": "MERGEABLE" if item.mergeable else "CONFLICTING",
            "mergeStateStatus": "CLEAN" if item.checks_pass else "BLOCKED",
            "statusCheckRollup": [],
        }


def implementation_completion(**overrides):
    values = {
        "kind": "implementation",
        "result": "pass",
        "attempt": 0,
        "evidence_comment": COMMENT_ID,
        "frontend_sha": FRONTEND_SHA,
        "backend_sha": None,
        "pr_url": FRONTEND_PR,
    }
    values.update(overrides)
    return PhaseCompletion(**values)


class PhaseCompletionTests(unittest.TestCase):
    def _review_completion(self, **overrides):
        values = {
            "kind": "review",
            "result": "fail",
            "attempt": 3,
            "evidence_comment": "00000000-0000-4000-8000-000000000031",
            "evidence_comment_url": (
                "https://multica.example/comments/"
                "00000000-0000-4000-8000-000000000031"
            ),
            "frontend_sha": None,
            "backend_sha": "b" * 40,
            "pr_url": None,
            "responsible_repositories": ("backend",),
        }
        values.update(overrides)
        try:
            return PhaseCompletion(**values)
        except TypeError as error:
            self.fail(f"PhaseCompletion rejected the version 2 contract: {error}")

    def test_build_metadata_uses_version_two_failure_ownership(self):
        metadata = build_phase_metadata(self._review_completion())

        self.assertEqual(metadata["eventra.workflow.version"], "2")
        self.assertEqual(
            metadata["eventra.phase.failure_repositories"],
            '["backend"]',
        )
        self.assertEqual(
            metadata["eventra.phase.evidence_comment_url"],
            "https://multica.example/comments/00000000-0000-4000-8000-000000000031",
        )

    def test_nonpassing_gate_requires_one_canonical_evidence_url(self):
        cases = (
            ("missing", {"evidence_comment_url": None}),
            ("HTTP", {"evidence_comment_url": "http://multica.example/comments/31"}),
            ("query", {"evidence_comment_url": "https://multica.example/comments/31?token=x"}),
            (
                "PASS",
                {"result": "pass", "responsible_repositories": ()},
            ),
            (
                "non-gate",
                {"kind": "smoke", "responsible_repositories": ()},
            ),
        )
        for label, overrides in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "invalid phase completion"):
                    build_phase_metadata(self._review_completion(**overrides))

    def test_failure_ownership_is_exact_and_gate_scoped(self):
        cases = (
            ("PASS with owners", {"result": "pass"}),
            ("non-PASS without owners", {"responsible_repositories": ()}),
            ("owner outside phase SHA scope", {"responsible_repositories": ("frontend",)}),
            ("duplicate owner", {"responsible_repositories": ("backend", "backend")}),
            ("implementation owner", {"kind": "implementation"}),
        )
        for label, overrides in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "invalid phase completion"):
                    build_phase_metadata(self._review_completion(**overrides))

    def test_nonterminal_version_one_phase_requires_explicit_migration(self):
        runner = FakeWorkflowRunner()
        runner.metadata = {"eventra.workflow.version": "1"}

        with self.assertRaisesRegex(
            RuntimeError,
            "version 1 workflow requires explicit migration",
        ):
            finish_phase(runner, "PRO-36", self._review_completion())

        self.assertEqual(runner.mutation_count, 0)

    def test_version_one_parent_prevents_a_new_child_completion(self):
        runner = FakeWorkflowRunner()
        runner.parent_metadata = {"eventra.workflow.version": "1"}

        with self.assertRaisesRegex(
            RuntimeError,
            "version 1 workflow requires explicit migration",
        ):
            finish_phase(runner, "PRO-36", implementation_completion())

        self.assertEqual(runner.mutation_count, 0)

    def test_build_metadata_uses_exact_string_contract(self):
        self.assertEqual(
            build_phase_metadata(implementation_completion()),
            {
                "eventra.workflow.version": "2",
                "eventra.phase.kind": "implementation",
                "eventra.phase.result": "pass",
                "eventra.phase.attempt": "0",
                "eventra.phase.evidence_comment": COMMENT_ID,
                "eventra.phase.failure_repositories": "[]",
                "eventra.phase.sha.frontend": FRONTEND_SHA,
                "eventra.phase.pr": FRONTEND_PR,
            },
        )

    def test_build_metadata_rejects_invalid_values_before_any_mutation(self):
        cases = (
            {"kind": "unknown"},
            {"result": "unknown"},
            {"attempt": -1},
            {"attempt": 4},
            {"attempt": True},
            {"evidence_comment": "not-a-uuid"},
            {"frontend_sha": "abc"},
            {"frontend_sha": "A" * 40},
            {"frontend_sha": None},
            {"pr_url": "http://github.com/codeExploreHub/Eventra/pull/6"},
            {"pr_url": "https://github.com/Aprim-OPC/Eventra/pull/6"},
            {"pr_url": None},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, "invalid phase completion"):
                    build_phase_metadata(implementation_completion(**overrides))

    def test_implementation_pr_must_match_its_single_repository_sha(self):
        backend_pr = "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
        cases = (
            {"backend_sha": "b" * 40},
            {
                "frontend_sha": None,
                "backend_sha": "b" * 40,
                "pr_url": FRONTEND_PR,
            },
            {"frontend_sha": FRONTEND_SHA, "pr_url": backend_pr},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, "invalid phase completion"):
                    build_phase_metadata(implementation_completion(**overrides))

        metadata = build_phase_metadata(
            implementation_completion(
                frontend_sha=None,
                backend_sha="b" * 40,
                pr_url=backend_pr,
            )
        )
        self.assertEqual(metadata["eventra.phase.sha.backend"], "b" * 40)
        self.assertNotIn("eventra.phase.sha.frontend", metadata)

    def test_finish_phase_writes_verified_metadata_before_done(self):
        runner = FakeWorkflowRunner()
        result = finish_phase(runner, "PRO-36", implementation_completion())

        self.assertEqual(result.issue_id, ISSUE_ID)
        self.assertEqual(result.issue_key, "PRO-36")
        self.assertEqual(result.status, "done")
        self.assertEqual(result.kind, "implementation")
        self.assertEqual(result.result, "pass")
        self.assertEqual(result.mutation_count, 9)
        status_index = runner.calls.index(
            (
                "issue", "status", "PRO-36", "done", "--no-start", "--output", "json"
            )
        )
        self.assertLess(status_index, len(runner.calls) - 2)
        self.assertEqual(
            runner.metadata["eventra.phase.result"],
            "pass",
        )
        self.assertEqual(runner._post_write_metadata_reads, 2)
        self.assertEqual(runner._post_done_metadata_reads, 2)
        self.assertEqual(runner._post_done_detail_reads, 2)

    def test_finish_phase_never_returns_done_after_write_boundary_drift(self):
        cases = (
            "metadata double-read",
            "parent before status",
            "child before status",
            "metadata after done",
            "detail after done",
        )
        for label in cases:
            with self.subTest(label=label):
                runner = FakeWorkflowRunner()
                if label == "metadata double-read":
                    runner.change_metadata_after_first_post_write_read = True
                elif label == "parent before status":
                    runner.drift_parent_before_status = True
                elif label == "child before status":
                    runner.drift_child_before_status = True
                elif label == "metadata after done":
                    runner.drift_post_done_metadata = True
                else:
                    runner.drift_post_done_detail = True

                with self.assertRaises(RuntimeError):
                    finish_phase(runner, "PRO-36", implementation_completion())

                status_calls = tuple(
                    call for call in runner.calls if call[:2] == ("issue", "status")
                )
                if label in {
                    "metadata double-read",
                    "parent before status",
                    "child before status",
                }:
                    self.assertFalse(status_calls)
                    self.assertNotEqual(runner.issue["status"], "done")
                else:
                    self.assertEqual(len(status_calls), 1)

    def test_finish_phase_rejects_noncurrent_stage_attempt_and_membership_without_mutation(self):
        cases = {
            "stage behind": ({"stage": 1}, {"eventra.workflow.next_stage": "3"}, 0),
            "stage ahead": ({"stage": 3}, {"eventra.workflow.next_stage": "3"}, 0),
            "attempt mismatch": ({}, {"eventra.workflow.attempt": "1"}, 0),
            "not a current child": ({}, {}, 0),
            "repair reservation": (
                {},
                {workflow_module.REPAIR_RESERVATION_KEY: "{}"},
                0,
            ),
        }
        for label, (detail_changes, parent_changes, _) in cases.items():
            with self.subTest(label=label):
                runner = FakeWorkflowRunner()
                runner.issue.update(detail_changes)
                runner.parent_metadata.update(parent_changes)
                if label == "not a current child":
                    runner.include_child = False

                with self.assertRaises(RuntimeError):
                    finish_phase(
                        runner,
                        "PRO-36",
                        implementation_completion(),
                    )

                self.assertEqual(runner.mutation_count, 0)
                self.assertEqual(runner.issue["status"], "in_review")

    def test_finish_phase_rejects_wrong_current_gate_authority_without_mutation(self):
        review = phase(
            "PRO-36",
            2,
            "review",
            result=None,
            status="in_review",
        )
        qa = phase(
            "PRO-38",
            2,
            "qa",
            evidence_comment="00000000-0000-4000-8000-000000000032",
        )
        valid = parent_snapshot(children=(review, qa))
        target = valid.children[0]
        corruptions = {
            "creation action": replace(target, creation_action="forged"),
            "target": replace(target, phase_target="repository:backend"),
            "role": replace(target, phase_role="integration_qa"),
            "project": replace(
                target,
                project_id="00000000-0000-4000-8000-000000000099",
            ),
            "assignee": replace(target, assignee_id=valid.children[1].assignee_id),
            "candidate": replace(target, frontend_sha="c" * 40),
        }
        completion = PhaseCompletion(
            kind="review",
            result="pass",
            attempt=0,
            evidence_comment="00000000-0000-4000-8000-000000000031",
            frontend_sha=FRONTEND_SHA,
            backend_sha=None,
            pr_url=None,
        )
        for label, corrupt in corruptions.items():
            with self.subTest(label=label):
                snapshot = replace(
                    valid,
                    children=(corrupt, *valid.children[1:]),
                )
                runner = FakeSnapshotFinishRunner(snapshot, "PRO-36")
                with self.assertRaises(RuntimeError):
                    finish_phase(runner, "PRO-36", completion)
                self.assertEqual(runner.mutation_count, 0)
        runner = FakeSnapshotFinishRunner(valid, "PRO-36")
        result = finish_phase(runner, "PRO-36", completion)
        self.assertEqual(result.status, "done")

    def test_finish_phase_accepts_valid_repository_qa_and_integration_suite(self):
        backend_sha = "b" * 40
        templates = (
            phase("PRO-36", 2, "review", evidence_comment="00000000-0000-4000-8000-000000000031"),
            phase("PRO-37", 2, "qa", evidence_comment="00000000-0000-4000-8000-000000000032"),
            phase("PRO-38", 2, "review", frontend_sha=None, backend_sha=backend_sha, evidence_comment="00000000-0000-4000-8000-000000000033"),
            phase("PRO-39", 2, "qa", frontend_sha=None, backend_sha=backend_sha, evidence_comment="00000000-0000-4000-8000-000000000034"),
            phase("PRO-40", 2, "integration_qa", backend_sha=backend_sha, evidence_comment="00000000-0000-4000-8000-000000000035"),
        )
        completions = {
            "PRO-37": PhaseCompletion(
                "qa", "pass", 0,
                "00000000-0000-4000-8000-000000000032",
                FRONTEND_SHA, None, None,
            ),
            "PRO-40": PhaseCompletion(
                "integration_qa", "pass", 0,
                "00000000-0000-4000-8000-000000000035",
                FRONTEND_SHA, backend_sha, None,
            ),
        }
        for target_key, completion in completions.items():
            with self.subTest(kind=completion.kind):
                children = tuple(
                    replace(item, result=None, status="in_review")
                    if item.issue_key == target_key
                    else item
                    for item in templates
                )
                snapshot = parent_snapshot(
                    classification="cross-stack",
                    candidate_backend_sha=backend_sha,
                    children=children,
                    pull_requests=(
                        frontend_pr(),
                        PullRequestSnapshot(
                            "backend",
                            "https://github.com/codeExploreHub/Eventra-Backend/pull/7",
                            backend_sha,
                            "open", True, True,
                        ),
                    ),
                )
                runner = FakeSnapshotFinishRunner(snapshot, target_key)
                self.assertEqual(
                    finish_phase(runner, target_key, completion).status,
                    "done",
                )

    def test_finish_phase_requires_and_preserves_current_repair_provenance(self):
        digest = "d" * 64
        source_uuid = "00000000-0000-4000-8000-000000000071"
        action = (
            "2:PRO-35:create_repair_stage:1:frontend:"
            + FRONTEND_SHA
            + ":-:next-stage:3:source-stage:2:bundle:"
            + digest
        )
        repair = replace(
            implementation_completion(kind="repair", attempt=1),
            frontend_sha="c" * 40,
        )
        provenance = {
            "eventra.workflow.version": "2",
            "eventra.phase.kind": "repair",
            "eventra.phase.attempt": "1",
            "eventra.phase.failure_repositories": "[]",
            "eventra.phase.sha.frontend": FRONTEND_SHA,
            "eventra.phase.pr": FRONTEND_PR,
            "eventra.repair.creation_action": action,
            "eventra.repair.failure_bundle_digest": digest,
            "eventra.repair.failure_evidence_uuids": f'["{source_uuid}"]',
            "eventra.repair.authorizing_comment_uuid": "",
            "eventra.repair.repository": "frontend",
            "eventra.repair.pull_request": FRONTEND_PR,
            "eventra.repair.round": "1",
            "eventra.repair.source_candidates": json.dumps(
                {"frontend": FRONTEND_SHA},
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        repair_keys = tuple(
            key for key in provenance if key.startswith("eventra.repair.")
        )
        for missing in (None, *repair_keys):
            with self.subTest(missing=missing or "all repair provenance"):
                runner = FakeWorkflowRunner()
                runner.issue["stage"] = 3
                runner.parent_metadata.update(
                    {
                        "eventra.workflow.next_stage": "4",
                        "eventra.workflow.attempt": "1",
                        "eventra.workflow.last_action": action,
                    }
                )
                runner.metadata.update(
                    {
                        key: value
                        for key, value in provenance.items()
                        if not key.startswith("eventra.repair.")
                        or (missing is not None and key != missing)
                    }
                )

                with self.assertRaises(RuntimeError):
                    finish_phase(runner, "PRO-36", repair)

                self.assertEqual(runner.mutation_count, 0)

        snapshot = ParentDecisionTests()._partial_cross_stack_repair_snapshot(
            frontend_done=False,
            backend_owned=False,
            frontend_head="c" * 40,
        )
        valid = FakeSnapshotFinishRunner(snapshot, "PRO-76")
        github = FakeSnapshotGitHubRunner(snapshot.pull_requests)
        immutable_before = {
            key: value
            for key, value in valid.metadata["PRO-76"].items()
            if key.startswith("eventra.repair.")
        }

        with patch.object(
            workflow_module,
            "GitHubRunner",
            return_value=github,
        ):
            result = finish_phase(valid, "PRO-76", repair)

        self.assertEqual(result.status, "done")
        self.assertEqual(
            {
                key: value
                for key, value in valid.metadata["PRO-76"].items()
                if key.startswith("eventra.repair.")
            },
            immutable_before,
        )

    def test_finish_phase_replaces_seeded_repair_sha_once_without_rewriting_provenance(self):
        replacement_sha = "c" * 40
        snapshot = ParentDecisionTests()._partial_cross_stack_repair_snapshot(
            frontend_done=False,
            backend_owned=False,
            frontend_head=replacement_sha,
        )
        runner = FakeSnapshotFinishRunner(snapshot, "PRO-76")
        github = FakeSnapshotGitHubRunner(snapshot.pull_requests)
        immutable_before = {
            key: value
            for key, value in runner.metadata["PRO-76"].items()
            if key.startswith("eventra.repair.")
        }

        with patch.object(
            workflow_module,
            "GitHubRunner",
            return_value=github,
        ):
            result = finish_phase(
                runner,
                "PRO-76",
                replace(
                    implementation_completion(kind="repair", attempt=1),
                    frontend_sha=replacement_sha,
                ),
            )

        self.assertEqual(result.status, "done")
        self.assertEqual(
            runner.metadata["PRO-76"]["eventra.phase.sha.frontend"],
            replacement_sha,
        )
        self.assertEqual(
            {
                key: value
                for key, value in runner.metadata["PRO-76"].items()
                if key.startswith("eventra.repair.")
            },
            immutable_before,
        )

    def test_finish_phase_rejects_conflicting_current_repair_provenance(self):
        digest = "d" * 64
        source_uuid = "00000000-0000-4000-8000-000000000071"
        action = (
            "2:PRO-35:create_repair_stage:1:frontend:"
            + FRONTEND_SHA
            + ":-:next-stage:3:source-stage:2:bundle:"
            + digest
        )
        provenance = {
            "eventra.workflow.version": "2",
            "eventra.phase.kind": "repair",
            "eventra.phase.attempt": "1",
            "eventra.phase.failure_repositories": "[]",
            "eventra.phase.sha.frontend": FRONTEND_SHA,
            "eventra.phase.pr": FRONTEND_PR,
            "eventra.repair.creation_action": action,
            "eventra.repair.failure_bundle_digest": digest,
            "eventra.repair.failure_evidence_uuids": f'["{source_uuid}"]',
            "eventra.repair.authorizing_comment_uuid": "",
            "eventra.repair.repository": "frontend",
            "eventra.repair.pull_request": FRONTEND_PR,
            "eventra.repair.round": "1",
            "eventra.repair.source_candidates": json.dumps(
                {"frontend": FRONTEND_SHA},
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        corruptions = {
            "creation action": {
                "eventra.repair.creation_action": action.replace("PRO-35", "PRO-99"),
            },
            "bundle digest": {"eventra.repair.failure_bundle_digest": "e" * 64},
            "evidence partition": {"eventra.repair.failure_evidence_uuids": "[]"},
            "automatic authorization": {
                "eventra.repair.authorizing_comment_uuid": source_uuid,
            },
            "repository": {"eventra.repair.repository": "backend"},
            "repair PR": {
                "eventra.repair.pull_request": (
                    "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
                ),
            },
            "round": {"eventra.repair.round": "2"},
            "managed phase PR": {
                "eventra.phase.pr": (
                    "https://github.com/codeExploreHub/Eventra/pull/8"
                ),
            },
            "candidate SHA": {"eventra.phase.sha.frontend": "e" * 40},
            "malformed source candidates": {
                "eventra.repair.source_candidates": "not-json",
            },
            "wrong source candidates": {
                "eventra.repair.source_candidates": json.dumps(
                    {"frontend": "e" * 40},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
            "extra source candidate": {
                "eventra.repair.source_candidates": json.dumps(
                    {"backend": "b" * 40, "frontend": FRONTEND_SHA},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        }
        for label, changes in corruptions.items():
            with self.subTest(label=label):
                runner = FakeWorkflowRunner()
                runner.issue["stage"] = 3
                runner.parent_metadata.update(
                    {
                        "eventra.workflow.next_stage": "4",
                        "eventra.workflow.attempt": "1",
                        "eventra.workflow.last_action": action,
                    }
                )
                runner.metadata.update(provenance)
                runner.metadata.update(changes)

                with self.assertRaises(RuntimeError):
                    finish_phase(
                        runner,
                        "PRO-36",
                        replace(
                            implementation_completion(kind="repair", attempt=1),
                            frontend_sha="c" * 40,
                        ),
                    )

                self.assertEqual(runner.mutation_count, 0)

        forged_actions = {
            "parent identifier": action.replace("PRO-35", "PRO-99"),
            "repair attempt": action.replace(
                "create_repair_stage:1",
                "create_repair_stage:2",
            ),
            "workflow scope": action.replace(":frontend:", ":backend:"),
            "candidate SHA": action.replace(FRONTEND_SHA, "e" * 40),
            "automatic authorization": action + ":authorization:" + source_uuid,
        }
        for label, forged_action in forged_actions.items():
            with self.subTest(forged_action=label):
                runner = FakeWorkflowRunner()
                runner.issue["stage"] = 3
                runner.parent_metadata.update(
                    {
                        "eventra.workflow.next_stage": "4",
                        "eventra.workflow.attempt": "1",
                        "eventra.workflow.last_action": forged_action,
                    }
                )
                runner.metadata.update(provenance)
                runner.metadata["eventra.repair.creation_action"] = forged_action

                with self.assertRaises(RuntimeError):
                    finish_phase(
                        runner,
                        "PRO-36",
                        replace(
                            implementation_completion(kind="repair", attempt=1),
                            frontend_sha="c" * 40,
                        ),
                    )

                self.assertEqual(runner.mutation_count, 0)

    def test_repair_pass_rejects_noop_sha_and_terminal_replay_is_exact(self):
        replacement_sha = "c" * 40
        snapshot = ParentDecisionTests()._partial_cross_stack_repair_snapshot(
            frontend_done=False,
            backend_owned=False,
            frontend_head=replacement_sha,
        )
        runner = FakeSnapshotFinishRunner(snapshot, "PRO-76")
        github = FakeSnapshotGitHubRunner(snapshot.pull_requests)
        noop = implementation_completion(kind="repair", attempt=1)

        with self.assertRaisesRegex(RuntimeError, "replacement SHA"):
            finish_phase(runner, "PRO-76", noop)
        self.assertEqual(runner.mutation_count, 0)

        replacement = replace(noop, frontend_sha=replacement_sha)
        with patch.object(
            workflow_module,
            "GitHubRunner",
            return_value=github,
        ):
            finish_phase(runner, "PRO-76", replacement)
        before_replay = runner.mutation_count

        with patch.object(
            workflow_module,
            "GitHubRunner",
            return_value=github,
        ):
            replay = finish_phase(runner, "PRO-76", replacement)
        self.assertEqual(replay.mutation_count, 0)
        self.assertEqual(runner.mutation_count, before_replay)
        with self.assertRaisesRegex(RuntimeError, "terminal phase metadata conflicts"):
            finish_phase(
                runner,
                "PRO-76",
                replace(replacement, frontend_sha="e" * 40),
            )
        self.assertEqual(runner.mutation_count, before_replay)

    def test_historical_terminal_replay_cannot_claim_current_completion(self):
        runner = FakeWorkflowRunner()
        runner.issue["status"] = "done"
        runner.metadata.update(build_phase_metadata(implementation_completion()))
        runner.parent_metadata.update(
            {
                "eventra.workflow.next_stage": "6",
                "eventra.workflow.attempt": "2",
            }
        )

        with self.assertRaises(RuntimeError):
            finish_phase(runner, "PRO-36", implementation_completion())

        self.assertEqual(runner.mutation_count, 0)

    def test_finish_phase_accepts_each_valid_current_nonrepair_kind(self):
        completions = (
            implementation_completion(),
            implementation_completion(kind="smoke", pr_url=None),
        )
        for completion in completions:
            with self.subTest(kind=completion.kind):
                runner = FakeWorkflowRunner()

                result = finish_phase(runner, "PRO-36", completion)

                self.assertEqual(result.status, "done")

    def test_finish_phase_is_idempotent_when_done_metadata_matches(self):
        runner = FakeWorkflowRunner()
        wanted = build_phase_metadata(implementation_completion())
        runner.issue["status"] = "done"
        runner.metadata.update(wanted)

        result = finish_phase(runner, "PRO-36", implementation_completion())

        self.assertEqual(result.mutation_count, 0)
        self.assertFalse(any(call[:3] == ("issue", "metadata", "set") for call in runner.calls))
        self.assertFalse(any(call[:2] == ("issue", "status") for call in runner.calls))

    def test_completed_version_one_phase_remains_inspectable(self):
        runner = FakeWorkflowRunner()
        legacy = build_phase_metadata(implementation_completion())
        legacy["eventra.workflow.version"] = "1"
        legacy.pop("eventra.phase.failure_repositories")
        runner.issue["status"] = "done"
        runner.metadata.update(legacy)

        result = finish_phase(runner, "PRO-36", implementation_completion())

        self.assertEqual(result.mutation_count, 0)
        self.assertEqual(result.status, "done")
        self.assertFalse(any(call[:3] == ("issue", "metadata", "set") for call in runner.calls))

    def test_terminal_conflicting_metadata_fails_closed(self):
        runner = FakeWorkflowRunner()
        runner.issue["status"] = "done"
        runner.metadata.update(build_phase_metadata(implementation_completion()))
        runner.metadata["eventra.phase.result"] = "blocked"

        with self.assertRaisesRegex(RuntimeError, "terminal phase metadata conflicts"):
            finish_phase(runner, "PRO-36", implementation_completion())
        self.assertEqual(runner.mutation_count, 0)

    def test_partial_metadata_failure_leaves_issue_nonterminal(self):
        runner = FakeWorkflowRunner()
        runner.fail_metadata_key = "eventra.phase.result"

        with self.assertRaisesRegex(RuntimeError, "Multica command failed"):
            finish_phase(runner, "PRO-36", implementation_completion())
        self.assertEqual(runner.issue["status"], "in_review")
        self.assertFalse(any(call[:2] == ("issue", "status") for call in runner.calls))

    def test_read_after_write_mismatch_leaves_issue_nonterminal(self):
        runner = FakeWorkflowRunner()
        runner.corrupt_metadata_key = "eventra.phase.result"

        with self.assertRaisesRegex(RuntimeError, "phase metadata reconciliation failed"):
            finish_phase(runner, "PRO-36", implementation_completion())
        self.assertEqual(runner.issue["status"], "in_review")

    def test_read_after_write_rejects_unexpected_controlled_metadata(self):
        runner = FakeWorkflowRunner()
        runner.inject_metadata_after_sets = (
            "eventra.phase.sha.backend",
            "b" * 40,
        )

        with self.assertRaisesRegex(RuntimeError, "phase metadata reconciliation failed"):
            finish_phase(runner, "PRO-36", implementation_completion())
        self.assertEqual(runner.issue["status"], "in_review")

    def test_status_read_after_write_is_authoritative(self):
        runner = FakeWorkflowRunner()
        runner.freeze_status = True

        with self.assertRaisesRegex(RuntimeError, "phase completion failed"):
            finish_phase(runner, "PRO-36", implementation_completion())

    def test_nonchild_unstaged_and_terminal_blocked_issue_are_rejected(self):
        cases = (
            {"parent_issue_id": None},
            {"stage": None},
            {"status": "blocked"},
            {"status": "cancelled"},
        )
        for overrides in cases:
            runner = FakeWorkflowRunner()
            runner.issue.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaises(RuntimeError):
                    finish_phase(runner, "PRO-36", implementation_completion())
                self.assertEqual(runner.mutation_count, 0)

    def test_parser_accepts_only_phase_arguments_and_prints_scalar_summary(self):
        parser = build_workflow_parser()
        args = parser.parse_args(
            [
                "finish-phase", "PRO-36",
                "--kind", "implementation",
                "--result", "pass",
                "--attempt", "0",
                "--frontend-sha", FRONTEND_SHA,
                "--evidence-comment", COMMENT_ID,
                "--pr", FRONTEND_PR,
            ]
        )
        self.assertEqual(args.command, "finish-phase")
        self.assertFalse(hasattr(args, "jwt_secret"))

        runner = FakeWorkflowRunner()
        result = finish_phase(runner, "PRO-36", implementation_completion())
        output = io.StringIO()
        with redirect_stdout(output):
            print_phase_result(result)
        self.assertEqual(
            output.getvalue(),
            "issue=PRO-36 status=done kind=implementation result=pass mutations=9\n",
        )
        self.assertNotIn(FRONTEND_SHA, output.getvalue())
        self.assertNotIn(FRONTEND_PR, output.getvalue())

    def test_parser_accepts_repeatable_responsible_repository_flags(self):
        parser = build_workflow_parser()
        argv = [
            "finish-phase", "PRO-99",
            "--kind", "review",
            "--result", "fail",
            "--attempt", "3",
            "--backend-sha", "b" * 40,
            "--evidence-comment", "00000000-0000-4000-8000-000000000031",
            "--evidence-comment-url",
            "https://multica.example/comments/00000000-0000-4000-8000-000000000031",
            "--responsible-repository", "backend",
            "--responsible-repository", "frontend",
        ]
        try:
            args = parser.parse_args(argv)
        except SystemExit as error:
            self.fail(f"version 2 ownership flags were rejected: {error}")

        self.assertEqual(args.responsible_repository, ["backend", "frontend"])
        self.assertEqual(
            args.evidence_comment_url,
            "https://multica.example/comments/00000000-0000-4000-8000-000000000031",
        )

    def test_parser_rejects_attempts_above_the_one_shot_human_round(self):
        parser = build_workflow_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "finish-phase", "PRO-99",
                    "--kind", "review",
                    "--result", "fail",
                    "--attempt", "4",
                    "--backend-sha", "b" * 40,
                    "--evidence-comment", "00000000-0000-4000-8000-000000000031",
                    "--responsible-repository", "backend",
                ]
            )


def phase(
    issue_key,
    stage,
    kind,
    result="pass",
    attempt=0,
    frontend_sha=FRONTEND_SHA,
    backend_sha=None,
    evidence_comment="",
    responsible_repositories=(),
    evidence_comment_url=None,
    project_id="",
    pr_url="",
    assignee_id="",
    status="done",
    creation_action="",
    phase_target="",
    phase_role="",
    failure_bundle_digest="",
    failure_evidence_uuids=(),
    authorizing_comment_uuid="",
    repair_repository="",
    repair_pull_request="",
    repair_round=0,
    repair_source_candidates=(),
    workflow_version=2,
):
    return PhaseSnapshot(
        issue_key=issue_key,
        stage=stage,
        kind=kind,
        result=result,
        attempt=attempt,
        status=status,
        frontend_sha=frontend_sha,
        backend_sha=backend_sha,
        evidence_comment=evidence_comment,
        responsible_repositories=responsible_repositories,
        evidence_comment_url=evidence_comment_url,
        project_id=project_id,
        pr_url=pr_url,
        assignee_id=assignee_id,
        creation_action=creation_action,
        phase_target=phase_target,
        phase_role=phase_role,
        failure_bundle_digest=failure_bundle_digest,
        failure_evidence_uuids=failure_evidence_uuids,
        authorizing_comment_uuid=authorizing_comment_uuid,
        repair_repository=repair_repository,
        repair_pull_request=repair_pull_request,
        repair_round=repair_round,
        repair_source_candidates=repair_source_candidates,
        workflow_version=workflow_version,
    )


def frontend_pr(**overrides):
    values = {
        "repository": "frontend",
        "url": FRONTEND_PR,
        "head_sha": FRONTEND_SHA,
        "state": "open",
        "mergeable": True,
        "checks_pass": True,
    }
    values.update(overrides)
    return PullRequestSnapshot(**values)


def parent_snapshot(**overrides):
    values = {
        "identifier": "PRO-35",
        "classification": "frontend-only",
        "attempt": 0,
        "last_action": None,
        "merge_state": "not_ready",
        "candidate_frontend_sha": FRONTEND_SHA,
        "candidate_backend_sha": None,
        "children": (phase("PRO-36", 1, "implementation"),),
        "pull_requests": (frontend_pr(),),
        "next_stage": 2,
    }
    values.update(overrides)
    if "next_stage" not in overrides:
        values["next_stage"] = max(
            (child.stage for child in values["children"]),
            default=0,
        ) + 1
    current_stage = values["next_stage"] - 1
    current = tuple(
        child for child in values["children"] if child.stage == current_stage
    )
    if current and {child.kind for child in current} <= {
        "review", "qa", "integration_qa"
    }:
        action_snapshot = ParentSnapshot(**values)
        action = _action_key(
            replace(action_snapshot, next_stage=current_stage, last_action=None),
            "create_gate_stage",
            values["attempt"],
        )
        if "last_action" not in overrides:
            values["last_action"] = action
        enriched = []
        for child in values["children"]:
            if child.stage != current_stage:
                enriched.append(child)
                continue
            repositories = tuple(
                repository
                for repository, sha in (
                    ("frontend", child.frontend_sha),
                    ("backend", child.backend_sha),
                )
                if sha is not None
            )
            if child.kind in {"review", "qa"} and len(repositories) == 1:
                repository = repositories[0]
                role = (
                    "independent_reviewer"
                    if child.kind == "review"
                    else "integration_qa"
                )
                target = f"repository:{repository}"
                project_id = (
                    PROJECT_ID if repository == "frontend" else BACKEND_PROJECT_ID
                )
            else:
                role = "integration_qa"
                target = "suite:integration"
                project_id = PROJECT_ID
            enriched.append(
                replace(
                    child,
                    creation_action=child.creation_action or action,
                    phase_target=child.phase_target or target,
                    phase_role=child.phase_role or role,
                    project_id=child.project_id or project_id,
                    assignee_id=child.assignee_id or (
                        REVIEWER_ID if role == "independent_reviewer" else QA_ID
                    ),
                )
            )
        values["children"] = tuple(enriched)
    return ParentSnapshot(**values)


class ParentDecisionTests(unittest.TestCase):
    def _authoritative_current_repair_snapshot(self, *, parent_copied=True):
        replacement_sha = "c" * 40
        project_id = "00000000-0000-4000-8000-000000000040"
        assignee_id = "00000000-0000-4000-8000-000000000042"
        review_uuid = "00000000-0000-4000-8000-000000000071"
        qa_uuid = "00000000-0000-4000-8000-000000000072"
        implementation = phase(
            "PRO-60",
            1,
            "implementation",
            project_id=project_id,
            pr_url=FRONTEND_PR,
            assignee_id=assignee_id,
        )
        gates = (
            phase(
                "PRO-61",
                2,
                "review",
                result="fail",
                evidence_comment=review_uuid,
                responsible_repositories=("frontend",),
                evidence_comment_url=f"https://multica.example/comments/{review_uuid}",
            ),
            phase(
                "PRO-62",
                2,
                "qa",
                evidence_comment=qa_uuid,
            ),
        )
        source = parent_snapshot(
            children=(implementation, *gates),
            next_stage=3,
        )
        decision = decide_parent_action(source)
        self.assertEqual(decision.kind, "create_repair_stage")
        bundle = decision.failure_bundle
        self.assertIsInstance(bundle, dict)
        repair = phase(
            "PRO-63",
            3,
            "repair",
            attempt=1,
            project_id=project_id,
            pr_url=FRONTEND_PR,
            assignee_id=assignee_id,
            creation_action=decision.action_key,
            failure_bundle_digest=bundle["digest"],
            failure_evidence_uuids=(review_uuid,),
            repair_repository="frontend",
            repair_pull_request=FRONTEND_PR,
            repair_round=1,
            repair_source_candidates=(("frontend", FRONTEND_SHA),),
            frontend_sha=replacement_sha,
        )
        return replace(
            source,
            attempt=1,
            last_action=decision.action_key,
            next_stage=4,
            children=(*source.children, repair),
            candidate_frontend_sha=(
                replacement_sha if parent_copied else FRONTEND_SHA
            ),
            pull_requests=(frontend_pr(head_sha=replacement_sha),),
        )

    def _partial_cross_stack_repair_snapshot(
        self,
        *,
        repair_round=1,
        frontend_done=True,
        backend_owned=True,
        backend_done=False,
        frontend_head=None,
        backend_head=None,
        parent_frontend_sha=None,
        parent_backend_sha=None,
    ):
        backend_sha = "b" * 40
        frontend_replacement = "c" * 40
        backend_replacement = "d" * 40
        backend_pr = "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
        frontend_project = "00000000-0000-4000-8000-000000000040"
        backend_project = "00000000-0000-4000-8000-000000000041"
        frontend_owner = "00000000-0000-4000-8000-000000000042"
        backend_owner = "00000000-0000-4000-8000-000000000043"
        frontend_failure = "00000000-0000-4000-8000-000000000091"
        backend_evidence = "00000000-0000-4000-8000-000000000092"
        source_attempt = repair_round - 1
        source_stage = repair_round * 2
        repair_stage = source_stage + 1
        authorization_uuid = (
            "00000000-0000-4000-8000-000000000099"
            if repair_round == 3
            else ""
        )
        implementation = (
            phase(
                "PRO-70", 1, "implementation",
                evidence_comment="00000000-0000-4000-8000-000000000070",
                project_id=frontend_project,
                pr_url=FRONTEND_PR,
                assignee_id=frontend_owner,
            ),
            phase(
                "PRO-71", 1, "implementation",
                frontend_sha=None,
                backend_sha=backend_sha,
                evidence_comment="00000000-0000-4000-8000-000000000071",
                project_id=backend_project,
                pr_url=backend_pr,
                assignee_id=backend_owner,
            ),
        )
        gates = (
            phase(
                "PRO-72", source_stage, "review",
                result="fail",
                attempt=source_attempt,
                evidence_comment=frontend_failure,
                responsible_repositories=("frontend",),
                evidence_comment_url=(
                    f"https://multica.example/comments/{frontend_failure}"
                ),
            ),
            phase(
                "PRO-73", source_stage, "review",
                result="fail" if backend_owned else "pass",
                attempt=source_attempt,
                frontend_sha=None,
                backend_sha=backend_sha,
                evidence_comment=backend_evidence,
                responsible_repositories=("backend",) if backend_owned else (),
                evidence_comment_url=(
                    f"https://multica.example/comments/{backend_evidence}"
                    if backend_owned
                    else None
                ),
            ),
            phase(
                "PRO-74", source_stage, "qa",
                attempt=source_attempt,
                evidence_comment="00000000-0000-4000-8000-000000000093",
            ),
            phase(
                "PRO-75", source_stage, "qa",
                attempt=source_attempt,
                frontend_sha=None,
                backend_sha=backend_sha,
                evidence_comment="00000000-0000-4000-8000-000000000094",
            ),
            phase(
                "PRO-78", source_stage, "integration_qa",
                attempt=source_attempt,
                backend_sha=backend_sha,
                evidence_comment="00000000-0000-4000-8000-000000000097",
            ),
        )
        source_prs = (
            frontend_pr(),
            PullRequestSnapshot(
                "backend", backend_pr, backend_sha, "open", True, True,
            ),
        )
        source = parent_snapshot(
            classification="cross-stack",
            attempt=source_attempt,
            candidate_backend_sha=backend_sha,
            children=(*implementation, *gates),
            pull_requests=source_prs,
            next_stage=repair_stage,
        )
        bundle = _failure_bundle(source, gates)
        action = _action_key(
            source,
            "create_repair_stage",
            repair_round,
            bundle["digest"],
            authorization_uuid or None,
            source_stage,
        )
        source_candidates = (
            ("backend", backend_sha),
            ("frontend", FRONTEND_SHA),
        )
        repairs = [
            phase(
                "PRO-76", repair_stage, "repair",
                result="pass" if frontend_done else None,
                attempt=repair_round,
                status="done" if frontend_done else "in_progress",
                evidence_comment=(
                    "00000000-0000-4000-8000-000000000095"
                    if frontend_done
                    else ""
                ),
                frontend_sha=(
                    frontend_replacement if frontend_done else FRONTEND_SHA
                ),
                project_id=frontend_project,
                pr_url=FRONTEND_PR,
                assignee_id=frontend_owner,
                creation_action=action,
                failure_bundle_digest=bundle["digest"],
                failure_evidence_uuids=(frontend_failure,),
                authorizing_comment_uuid=authorization_uuid,
                repair_repository="frontend",
                repair_pull_request=FRONTEND_PR,
                repair_round=repair_round,
                repair_source_candidates=source_candidates,
            )
        ]
        if backend_owned:
            repairs.append(
                phase(
                    "PRO-77", repair_stage, "repair",
                    result="pass" if backend_done else None,
                    attempt=repair_round,
                    status="done" if backend_done else "in_progress",
                    evidence_comment=(
                        "00000000-0000-4000-8000-000000000096"
                        if backend_done
                        else ""
                    ),
                    frontend_sha=None,
                    backend_sha=(
                        backend_replacement if backend_done else backend_sha
                    ),
                    project_id=backend_project,
                    pr_url=backend_pr,
                    assignee_id=backend_owner,
                    creation_action=action,
                    failure_bundle_digest=bundle["digest"],
                    failure_evidence_uuids=(backend_evidence,),
                    authorizing_comment_uuid=authorization_uuid,
                    repair_repository="backend",
                    repair_pull_request=backend_pr,
                    repair_round=repair_round,
                    repair_source_candidates=source_candidates,
                )
            )
        return replace(
            source,
            attempt=repair_round,
            last_action=action,
            next_stage=repair_stage + 1,
            children=(*source.children, *repairs),
            candidate_frontend_sha=(
                FRONTEND_SHA
                if parent_frontend_sha is None
                else parent_frontend_sha
            ),
            candidate_backend_sha=(
                backend_sha if parent_backend_sha is None else parent_backend_sha
            ),
            pull_requests=(
                frontend_pr(
                    head_sha=(
                        frontend_replacement
                        if frontend_head is None
                        else frontend_head
                    )
                ),
                replace(
                    source_prs[1],
                    head_sha=(backend_sha if backend_head is None else backend_head),
                ),
            ),
            consumed_authorization_uuid=authorization_uuid,
        )

    def test_partial_repair_requires_completed_output_to_equal_current_head(self):
        valid = self._partial_cross_stack_repair_snapshot()

        self.assertEqual(decide_parent_action(valid).kind, "noop")

    def test_partial_repair_blocks_completed_output_not_matching_current_head(self):
        valid = self._partial_cross_stack_repair_snapshot()

        for stale_or_wrong in (FRONTEND_SHA, "e" * 40):
            with self.subTest(frontend_head=stale_or_wrong):
                changed = replace(
                    valid,
                    pull_requests=(
                        replace(valid.pull_requests[0], head_sha=stale_or_wrong),
                        valid.pull_requests[1],
                    ),
                )

                self.assertEqual(
                    decide_parent_action(changed).kind,
                    "block_parent",
                )
    def test_partial_repair_allows_active_head_motion_but_not_early_adoption(self):
        for repair_round in (1, 2, 3):
            for active_head in ("b" * 40, "e" * 40, "f" * 40):
                with self.subTest(
                    repair_round=repair_round,
                    active_head=active_head,
                ):
                    snapshot = self._partial_cross_stack_repair_snapshot(
                        repair_round=repair_round,
                        backend_head=active_head,
                    )

                    self.assertEqual(decide_parent_action(snapshot).kind, "noop")

        partial_copy = self._partial_cross_stack_repair_snapshot(
            backend_head="e" * 40,
            parent_frontend_sha="c" * 40,
        )
        self.assertEqual(decide_parent_action(partial_copy).kind, "noop")
        for label, changed in {
            "unknown completed output": replace(
                partial_copy,
                candidate_frontend_sha="e" * 40,
            ),
            "active owner adopted": replace(
                partial_copy,
                candidate_backend_sha="e" * 40,
            ),
        }.items():
            with self.subTest(label=label):
                self.assertEqual(decide_parent_action(changed).kind, "block_parent")

    def test_partial_one_owner_repair_ignores_owner_motion_but_blocks_unaffected_drift(self):
        active = self._partial_cross_stack_repair_snapshot(
            frontend_done=False,
            backend_owned=False,
            frontend_head="e" * 40,
        )

        self.assertEqual(decide_parent_action(active).kind, "noop")
        unaffected_drift = replace(
            active,
            pull_requests=(
                active.pull_requests[0],
                replace(active.pull_requests[1], head_sha="f" * 40),
            ),
        )
        self.assertEqual(
            decide_parent_action(unaffected_drift).kind,
            "block_parent",
        )

    def test_source_equal_repair_completion_reads_every_managed_head(self):
        backend_pr = "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
        for repair_round in (1, 2, 3):
            with self.subTest(repair_round=repair_round):
                snapshot = self._partial_cross_stack_repair_snapshot(
                    repair_round=repair_round,
                    frontend_done=False,
                    backend_owned=False,
                    frontend_head="c" * 40,
                )
                self.assertEqual(decide_parent_action(snapshot).kind, "noop")
                runner = FakeSnapshotFinishRunner(snapshot, "PRO-76")
                github = FakeSnapshotGitHubRunner(snapshot.pull_requests)

                with patch.object(
                    workflow_module,
                    "GitHubRunner",
                    return_value=github,
                ):
                    result = finish_phase(
                        runner,
                        "PRO-76",
                        PhaseCompletion(
                            kind="repair",
                            result="pass",
                            attempt=repair_round,
                            evidence_comment=COMMENT_ID,
                            frontend_sha="c" * 40,
                            backend_sha=None,
                            pr_url=FRONTEND_PR,
                        ),
                    )

                self.assertEqual(
                    (
                        result.status,
                        runner.mutation_count,
                        frozenset(call[2] for call in github.calls),
                    ),
                    ("done", 9, frozenset((FRONTEND_PR, backend_pr))),
                )

    def test_source_equal_repair_completion_blocks_unaffected_head_drift(self):
        backend_pr = "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
        for repair_round in (1, 2, 3):
            with self.subTest(repair_round=repair_round):
                snapshot = self._partial_cross_stack_repair_snapshot(
                    repair_round=repair_round,
                    frontend_done=False,
                    backend_owned=False,
                    frontend_head="c" * 40,
                    backend_head="f" * 40,
                )
                self.assertEqual(
                    decide_parent_action(snapshot).kind,
                    "block_parent",
                )
                runner = FakeSnapshotFinishRunner(snapshot, "PRO-76")
                github = FakeSnapshotGitHubRunner(snapshot.pull_requests)
                error = None

                with patch.object(
                    workflow_module,
                    "GitHubRunner",
                    return_value=github,
                ):
                    try:
                        finish_phase(
                            runner,
                            "PRO-76",
                            PhaseCompletion(
                                kind="repair",
                                result="pass",
                                attempt=repair_round,
                                evidence_comment=COMMENT_ID,
                                frontend_sha="c" * 40,
                                backend_sha=None,
                                pr_url=FRONTEND_PR,
                            ),
                        )
                    except RuntimeError as problem:
                        error = problem

                self.assertEqual(
                    (
                        type(error),
                        runner.mutation_count,
                        frozenset(call[2] for call in github.calls),
                    ),
                    (RuntimeError, 0, frozenset((FRONTEND_PR, backend_pr))),
                )

    def test_partial_repair_multiset_and_terminal_controls_remain_fail_closed(self):
        partial = self._partial_cross_stack_repair_snapshot()
        current = partial.children[-2:]
        missing_owner = replace(
            partial,
            children=partial.children[:-1],
        )
        duplicate_owner = replace(
            partial,
            children=(*partial.children, replace(current[0], issue_key="PRO-78")),
        )
        failed_owner = replace(
            partial,
            children=(
                *partial.children[:-1],
                replace(current[1], status="done", result="fail"),
            ),
            pull_requests=(
                partial.pull_requests[0],
                replace(partial.pull_requests[1], head_sha="b" * 40),
            ),
        )
        for label, changed in {
            "missing owner": missing_owner,
            "duplicate owner": duplicate_owner,
            "terminal failure": failed_owner,
        }.items():
            with self.subTest(label=label):
                self.assertEqual(decide_parent_action(changed).kind, "block_parent")

        completed = self._partial_cross_stack_repair_snapshot(
            backend_done=True,
            backend_head="d" * 40,
            parent_frontend_sha="c" * 40,
            parent_backend_sha="d" * 40,
        )
        self.assertEqual(decide_parent_action(completed).kind, "create_gate_stage")

    def test_partial_parent_copy_composes_with_remaining_sibling_completion(self):
        for repair_round, backend_replacement in zip(
            (1, 2, 3),
            ("d" * 40, "e" * 40, "f" * 40),
            strict=True,
        ):
            with self.subTest(repair_round=repair_round):
                snapshot = self._partial_cross_stack_repair_snapshot(
                    repair_round=repair_round,
                    backend_head=backend_replacement,
                    parent_frontend_sha="c" * 40,
                )
                self.assertEqual(decide_parent_action(snapshot).kind, "noop")
                runner = FakeSnapshotFinishRunner(snapshot, "PRO-77")
                github = FakeSnapshotGitHubRunner(snapshot.pull_requests)
                immutable_before = {
                    key: value
                    for key, value in runner.metadata["PRO-77"].items()
                    if key.startswith("eventra.repair.")
                }

                with patch.object(
                    workflow_module,
                    "GitHubRunner",
                    return_value=github,
                ):
                    result = finish_phase(
                        runner,
                        "PRO-77",
                        PhaseCompletion(
                            kind="repair",
                            result="pass",
                            attempt=repair_round,
                            evidence_comment=COMMENT_ID,
                            frontend_sha=None,
                            backend_sha=backend_replacement,
                            pr_url=(
                                "https://github.com/"
                                "codeExploreHub/Eventra-Backend/pull/7"
                            ),
                        ),
                    )

                self.assertEqual(result.status, "done")
                self.assertEqual(
                    runner.metadata["PRO-77"]["eventra.phase.sha.backend"],
                    backend_replacement,
                )
                self.assertEqual(
                    {
                        key: value
                        for key, value in runner.metadata["PRO-77"].items()
                        if key.startswith("eventra.repair.")
                    },
                    immutable_before,
                )

    def test_partial_parent_copy_completion_rejects_unverified_compositions(self):
        valid = self._partial_cross_stack_repair_snapshot(
            backend_head="d" * 40,
            parent_frontend_sha="c" * 40,
        )
        wrong_seed_child = replace(
            valid.children[-1],
            backend_sha="e" * 40,
        )
        cases = {
            "invalid completed copy": replace(
                valid,
                candidate_frontend_sha="e" * 40,
            ),
            "completed child head mismatch": replace(
                valid,
                pull_requests=(
                    replace(valid.pull_requests[0], head_sha=FRONTEND_SHA),
                    valid.pull_requests[1],
                ),
            ),
            "wrong finishing seed": replace(
                valid,
                children=(*valid.children[:-1], wrong_seed_child),
            ),
            "active owner adopted": replace(
                valid,
                candidate_backend_sha="d" * 40,
            ),
        }

        for label, snapshot in cases.items():
            with self.subTest(label=label):
                self.assertEqual(
                    decide_parent_action(snapshot).kind,
                    "block_parent",
                )
                runner = FakeSnapshotFinishRunner(snapshot, "PRO-77")
                github = FakeSnapshotGitHubRunner(snapshot.pull_requests)
                with patch.object(
                    workflow_module,
                    "GitHubRunner",
                    return_value=github,
                ):
                    with self.assertRaises(RuntimeError):
                        finish_phase(
                            runner,
                            "PRO-77",
                            PhaseCompletion(
                                kind="repair",
                                result="pass",
                                attempt=1,
                                evidence_comment=COMMENT_ID,
                                frontend_sha=None,
                                backend_sha="d" * 40,
                                pr_url=(
                                    "https://github.com/"
                                    "codeExploreHub/Eventra-Backend/pull/7"
                                ),
                            ),
                        )
                self.assertEqual(runner.mutation_count, 0)

    def test_nonrepair_stages_keep_the_generic_parent_head_drift_boundary(self):
        drifted_pr = frontend_pr(head_sha="e" * 40)
        cases = {
            "implementation": replace(
                parent_snapshot(),
                pull_requests=(drifted_pr,),
            ),
            "gate": parent_snapshot(
                children=(
                    phase(
                        "PRO-80", 2, "review",
                        evidence_comment=(
                            "00000000-0000-4000-8000-000000000080"
                        ),
                    ),
                    phase(
                        "PRO-81", 2, "qa",
                        evidence_comment=(
                            "00000000-0000-4000-8000-000000000081"
                        ),
                    ),
                ),
                pull_requests=(drifted_pr,),
                next_stage=3,
            ),
            "smoke": parent_snapshot(
                merge_state="merged",
                children=(
                    phase(
                        "PRO-82", 4, "smoke",
                        evidence_comment=(
                            "00000000-0000-4000-8000-000000000082"
                        ),
                    ),
                ),
                pull_requests=(drifted_pr,),
                next_stage=5,
            ),
        }

        for label, snapshot in cases.items():
            with self.subTest(label=label):
                decision = decide_parent_action(snapshot)

                self.assertEqual(decision.kind, "block_parent")
                self.assertIn("out-of-band", decision.reason)

    def test_current_repair_pass_requires_complete_authoritative_provenance(self):
        valid = self._authoritative_current_repair_snapshot()
        current = valid.children[-1]
        corruptions = {
            "all provenance absent": replace(
                current,
                creation_action="",
                failure_bundle_digest="",
                failure_evidence_uuids=(),
                repair_repository="",
                repair_pull_request="",
                repair_round=0,
                repair_source_candidates=(),
            ),
            "workflow version": replace(current, workflow_version=1),
            "creation action": replace(current, creation_action="different-action"),
            "bundle digest": replace(current, failure_bundle_digest="f" * 64),
            "evidence partition": replace(current, failure_evidence_uuids=()),
            "repository": replace(current, repair_repository="backend"),
            "repair PR": replace(
                current,
                repair_pull_request=(
                    "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
                ),
            ),
            "round": replace(current, repair_round=2),
            "source candidates missing": replace(
                current,
                repair_source_candidates=(),
            ),
            "source candidates wrong": replace(
                current,
                repair_source_candidates=(("frontend", "e" * 40),),
            ),
            "source candidates extra": replace(
                current,
                repair_source_candidates=(
                    ("backend", "b" * 40),
                    ("frontend", FRONTEND_SHA),
                ),
            ),
            "managed phase PR": replace(current, pr_url=""),
            "project identity": replace(current, project_id=""),
            "assignee identity": replace(current, assignee_id=""),
            "automatic authorization": replace(
                current,
                authorizing_comment_uuid="00000000-0000-4000-8000-000000000099",
            ),
        }

        for label, repair in corruptions.items():
            with self.subTest(label=label):
                decision = decide_parent_action(
                    replace(valid, children=(*valid.children[:-1], repair))
                )

                self.assertEqual(decision.kind, "block_parent")
                self.assertIsNone(decision.action_key)
                self.assertIsNone(decision.failure_bundle)

        self.assertEqual(
            decide_parent_action(valid).kind,
            "create_gate_stage",
        )

    def test_completed_repair_before_parent_copy_blocks_with_copy_instruction(self):
        valid = self._authoritative_current_repair_snapshot(parent_copied=False)

        decision = decide_parent_action(valid)

        self.assertEqual(decision.kind, "block_parent")
        self.assertIn("parent candidate", decision.reason)

    def test_completed_repair_after_parent_copy_gates_the_replacement(self):
        valid = self._authoritative_current_repair_snapshot()

        decision = decide_parent_action(valid)

        self.assertEqual(decision.kind, "create_gate_stage")

    def test_completed_repair_requires_current_pr_head_and_exact_parent_copy(self):
        valid = self._authoritative_current_repair_snapshot()
        cases = {
            "managed PR drift": replace(
                valid,
                pull_requests=(frontend_pr(head_sha="e" * 40),),
            ),
            "wrong parent copy": replace(
                valid,
                candidate_frontend_sha="e" * 40,
            ),
        }

        for label, snapshot in cases.items():
            with self.subTest(label=label):
                decision = decide_parent_action(snapshot)

                self.assertEqual(decision.kind, "block_parent")
                self.assertIsNone(decision.action_key)
    def test_cross_stack_repair_owner_uses_its_managed_pr_project(self):
        backend_sha = "b" * 40
        frontend_project = "00000000-0000-4000-8000-000000000040"
        backend_project = "00000000-0000-4000-8000-000000000041"
        frontend_owner = "00000000-0000-4000-8000-000000000042"
        backend_owner = "00000000-0000-4000-8000-000000000043"
        backend_pr_url = "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
        review_uuid = "00000000-0000-4000-8000-000000000062"
        qa_uuid = "00000000-0000-4000-8000-000000000063"
        children = (
            phase(
                "PRO-60", 1, "implementation",
                project_id=frontend_project,
                pr_url=FRONTEND_PR,
                assignee_id=frontend_owner,
            ),
            phase(
                "PRO-61", 1, "implementation",
                frontend_sha=None,
                backend_sha=backend_sha,
                project_id=backend_project,
                pr_url=backend_pr_url,
                assignee_id=backend_owner,
            ),
            phase(
                "PRO-62", 2, "review", result="fail",
                frontend_sha=None,
                backend_sha=backend_sha,
                evidence_comment=review_uuid,
                responsible_repositories=("backend",),
                evidence_comment_url=f"https://multica.example/comments/{review_uuid}",
                project_id=frontend_project,
            ),
            phase(
                "PRO-63", 2, "qa",
                frontend_sha=None,
                backend_sha=backend_sha,
                evidence_comment=qa_uuid,
                project_id=frontend_project,
            ),
        )
        snapshot = parent_snapshot(
            classification="cross-stack",
            candidate_backend_sha=backend_sha,
            children=children,
            pull_requests=(
                frontend_pr(),
                PullRequestSnapshot(
                    "backend", backend_pr_url, backend_sha,
                    "open", True, True,
                ),
            ),
        )
        bundle = _failure_bundle(snapshot, children[-2:])

        specs = _repair_child_specs(snapshot, bundle)

        self.assertEqual(specs[0]["repository"], "backend")
        self.assertEqual(specs[0]["project_id"], backend_project)
        self.assertEqual(specs[0]["assignee_id"], backend_owner)

    def _round_three_snapshot(
        self,
        *,
        author_type="member",
        content=None,
        authorization_comment_uuid="00000000-0000-4000-8000-000000000061",
        consumed_authorization_uuid="",
        pr_head="b" * 40,
        attempt=2,
    ):
        backend_sha = "b" * 40
        review_uuid = "00000000-0000-4000-8000-000000000062"
        qa_uuid = "00000000-0000-4000-8000-000000000063"
        children = (
            phase(
                "PRO-66", 1, "implementation", attempt=0,
                frontend_sha=None, backend_sha=backend_sha,
            ),
            phase(
                "PRO-67", 3, "repair", attempt=1,
                frontend_sha=None, backend_sha=backend_sha,
            ),
            phase(
                "PRO-68", 5, "repair", attempt=2,
                frontend_sha=None, backend_sha=backend_sha,
            ),
            phase(
                "PRO-69", 6, "review", result="fail", attempt=2,
                frontend_sha=None, backend_sha=backend_sha,
                evidence_comment=review_uuid,
                responsible_repositories=("backend",),
                evidence_comment_url=f"https://multica.example/comments/{review_uuid}",
            ),
            phase(
                "PRO-70", 6, "qa", attempt=2,
                frontend_sha=None, backend_sha=backend_sha,
                evidence_comment=qa_uuid,
            ),
        )
        base = parent_snapshot(
            identifier="PRO-65",
            classification="backend-only",
            attempt=attempt,
            candidate_frontend_sha=None,
            candidate_backend_sha=backend_sha,
            children=children,
            pull_requests=(
                PullRequestSnapshot(
                    repository="backend",
                    url="https://github.com/codeExploreHub/Eventra-Backend/pull/7",
                    head_sha=pr_head,
                    state="open",
                    mergeable=True,
                    checks_pass=True,
                ),
            ),
            next_stage=7,
            authorization_comment_uuid=authorization_comment_uuid,
            consumed_authorization_uuid=consumed_authorization_uuid,
        )
        bundle = _failure_bundle(base, children[-2:])
        if content is None:
            content = json.dumps(
                {
                    "bundle_digest": bundle["digest"],
                    "granted_round": 3,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        return replace(
            base,
            authorizing_comment=(
                None
                if not authorization_comment_uuid
                else AuthorizingComment(
                    authorization_comment_uuid,
                    author_type,
                    content or "",
                )
            ),
        )

    def _version_two_backend_gate(self, issue_key, kind, *, status="done"):
        values = {
            "issue_key": issue_key,
            "stage": 2,
            "kind": kind,
            "result": "fail",
            "attempt": 0,
            "status": status,
            "frontend_sha": None,
            "backend_sha": "b" * 40,
            "evidence_comment": (
                "00000000-0000-4000-8000-000000000041"
                if kind == "review"
                else "00000000-0000-4000-8000-000000000042"
            ),
            "evidence_comment_url": (
                "https://multica.example/comments/"
                "00000000-0000-4000-8000-000000000041"
                if kind == "review"
                else "https://multica.example/comments/"
                "00000000-0000-4000-8000-000000000042"
            ),
            "responsible_repositories": ("backend",),
        }
        try:
            return PhaseSnapshot(**values)
        except TypeError as error:
            self.fail(f"PhaseSnapshot rejected version 2 evidence: {error}")

    def _pro_65_snapshot(self, *, review_status="done", qa_status="done"):
        backend_sha = "b" * 40
        return parent_snapshot(
            identifier="PRO-65",
            classification="backend-only",
            candidate_frontend_sha=None,
            candidate_backend_sha=backend_sha,
            children=(
                self._version_two_backend_gate("PRO-66", "review", status=review_status),
                self._version_two_backend_gate("PRO-67", "qa", status=qa_status),
            ),
            pull_requests=(
                PullRequestSnapshot(
                    repository="backend",
                    url="https://github.com/codeExploreHub/Eventra-Backend/pull/7",
                    head_sha=backend_sha,
                    state="open",
                    mergeable=True,
                    checks_pass=True,
                ),
            ),
            next_stage=3,
        )

    def test_pro_65_waits_for_active_gate_sibling_without_a_bundle(self):
        decision = decide_parent_action(
            self._pro_65_snapshot(review_status="in_review")
        )

        self.assertEqual(decision.kind, "noop")
        self.assertIsNone(decision.failure_bundle)

    def test_pro_65_final_gate_builds_one_complete_failure_bundle(self):
        decision = decide_parent_action(self._pro_65_snapshot())

        self.assertEqual(decision.kind, "create_repair_stage")
        expected_digest = "519192e8528dffa2be8651febe876afb00718a6cfb597bde475f252cd2028a53"
        backend_sha = "b" * 40
        self.assertEqual(
            decision.failure_bundle,
            {
                "candidate_shas": {"backend": backend_sha},
                "digest": expected_digest,
                "failures": [
                    {
                        "candidate_shas": {"backend": backend_sha},
                        "child_identifier": "PRO-67",
                        "evidence_comment_url": (
                            "https://multica.example/comments/"
                            "00000000-0000-4000-8000-000000000042"
                        ),
                        "evidence_comment_uuid": "00000000-0000-4000-8000-000000000042",
                        "phase": "qa",
                        "repair_round": 0,
                        "responsible_repositories": ["backend"],
                        "result": "fail",
                        "stage_ordinal": 2,
                        "suite_key": "",
                    },
                    {
                        "candidate_shas": {"backend": backend_sha},
                        "child_identifier": "PRO-66",
                        "evidence_comment_url": (
                            "https://multica.example/comments/"
                            "00000000-0000-4000-8000-000000000041"
                        ),
                        "evidence_comment_uuid": "00000000-0000-4000-8000-000000000041",
                        "phase": "review",
                        "repair_round": 0,
                        "responsible_repositories": ["backend"],
                        "result": "fail",
                        "stage_ordinal": 2,
                        "suite_key": "",
                    },
                ],
                "parent_identifier": "PRO-65",
                "repair_round": 1,
                "source_stage_ordinal": 2,
                "workflow_version": 2,
            },
        )
        self.assertEqual(
            decision.action_key,
            "2:PRO-65:create_repair_stage:1:backend:-:"
            + backend_sha
            + ":next-stage:3:source-stage:2"
            + ":bundle:"
            + expected_digest,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_parent_decision(decision)
        rendered = output.getvalue().strip()
        payload = json.loads(rendered)
        self.assertEqual(
            set(payload),
            {"decision", "action_key", "reason", "failure_bundle"},
        )
        self.assertEqual(rendered, json.dumps(payload, sort_keys=True, separators=(",", ":")))

    def test_failure_bundle_rejects_a_duplicate_evidence_uuid(self):
        snapshot = self._pro_65_snapshot()
        review, qa = snapshot.children
        duplicated = replace(
            qa,
            evidence_comment=review.evidence_comment,
            evidence_comment_url=review.evidence_comment_url,
        )

        decision = decide_parent_action(
            replace(snapshot, children=(review, duplicated))
        )

        self.assertEqual(decision.kind, "block_parent")
        self.assertEqual(
            decision.reason,
            "terminal gate failure evidence is malformed",
        )
        self.assertIsNone(decision.failure_bundle)

    def test_finished_implementation_creates_one_exact_sha_gate_stage(self):
        decision = decide_parent_action(parent_snapshot())
        self.assertEqual(
            decision,
            ParentDecision(
                "create_gate_stage",
                (
                    f"2:PRO-35:create_gate_stage:0:frontend:{FRONTEND_SHA}:-"
                    ":next-stage:2"
                ),
                "implementation evidence is ready for exact-SHA gates",
            ),
        )

    def test_future_gate_stage_cannot_override_authoritative_current_stage(self):
        snapshot = parent_snapshot(
            next_stage=3,
            children=(
                phase("PRO-36", 1, "implementation"),
                phase("PRO-37", 2, "review", status="in_progress"),
                phase("PRO-38", 2, "qa", status="in_progress"),
                phase("PRO-97", 99, "review"),
                phase("PRO-98", 99, "qa"),
            ),
        )

        decision = decide_parent_action(snapshot)

        self.assertEqual(decision.kind, "block_parent")

    def test_duplicate_current_gate_identity_never_merges(self):
        snapshot = parent_snapshot(
            next_stage=3,
            children=(
                phase("PRO-36", 1, "implementation"),
                phase("PRO-37", 2, "review"),
                phase("PRO-38", 2, "review"),
                phase("PRO-39", 2, "qa"),
            ),
        )

        decision = decide_parent_action(snapshot)

        self.assertEqual(decision.kind, "block_parent")

    def test_future_smoke_stage_cannot_complete_parent(self):
        snapshot = parent_snapshot(
            merge_state="merged",
            next_stage=4,
            children=(
                phase("PRO-36", 1, "implementation"),
                phase("PRO-37", 2, "review"),
                phase("PRO-38", 2, "qa"),
                phase("PRO-39", 3, "smoke", status="in_progress"),
                phase("PRO-99", 99, "smoke"),
            ),
        )

        decision = decide_parent_action(snapshot)

        self.assertEqual(decision.kind, "block_parent")

    def test_cross_stack_implementation_requires_exact_repository_coverage(self):
        backend_sha = "b" * 40
        snapshot = parent_snapshot(
            classification="cross-stack",
            candidate_backend_sha=backend_sha,
            children=(phase("PRO-36", 1, "implementation"),),
        )
        self.assertEqual(decide_parent_action(snapshot).kind, "block_parent")

        backend_implementation = phase(
            "PRO-37",
            1,
            "implementation",
            frontend_sha=None,
            backend_sha=backend_sha,
        )
        complete = ParentSnapshot(
            **{
                **snapshot.__dict__,
                "children": snapshot.children + (backend_implementation,),
            }
        )
        self.assertEqual(
            decide_parent_action(complete).kind,
            "create_gate_stage",
        )

    def test_gate_failure_routes_bounded_repair_and_never_merge(self):
        children = (
            phase("PRO-36", 1, "implementation"),
            phase(
                "PRO-37",
                2,
                "review",
                result="fail",
                evidence_comment="00000000-0000-4000-8000-000000000037",
                responsible_repositories=("frontend",),
                evidence_comment_url=(
                    "https://multica.example/comments/"
                    "00000000-0000-4000-8000-000000000037"
                ),
            ),
            phase(
                "PRO-38",
                2,
                "qa",
                evidence_comment="00000000-0000-4000-8000-000000000038",
            ),
        )
        decision = decide_parent_action(
            parent_snapshot(children=children, next_stage=3)
        )
        self.assertEqual(decision.kind, "create_repair_stage")
        self.assertIn(":1:frontend:", decision.action_key)

    def test_cross_stack_repair_may_target_only_affected_repository(self):
        backend_sha = "b" * 40
        backend_pr_url = "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
        frontend_project = "00000000-0000-4000-8000-000000000040"
        backend_project = "00000000-0000-4000-8000-000000000041"
        frontend_owner = "00000000-0000-4000-8000-000000000042"
        backend_owner = "00000000-0000-4000-8000-000000000043"
        review_uuid = "00000000-0000-4000-8000-000000000081"
        qa_uuid = "00000000-0000-4000-8000-000000000082"
        backend_review_uuid = "00000000-0000-4000-8000-000000000083"
        backend_qa_uuid = "00000000-0000-4000-8000-000000000084"
        source_children = (
            phase(
                "PRO-36", 1, "implementation",
                project_id=frontend_project,
                pr_url=FRONTEND_PR,
                assignee_id=frontend_owner,
            ),
            phase(
                "PRO-37",
                1,
                "implementation",
                frontend_sha=None,
                backend_sha=backend_sha,
                project_id=backend_project,
                pr_url=backend_pr_url,
                assignee_id=backend_owner,
            ),
            phase(
                "PRO-38", 2, "review", result="fail",
                evidence_comment=review_uuid,
                responsible_repositories=("frontend",),
                evidence_comment_url=f"https://multica.example/comments/{review_uuid}",
            ),
            phase(
                "PRO-39",
                2,
                "review",
                frontend_sha=None,
                backend_sha=backend_sha,
                evidence_comment=backend_review_uuid,
            ),
            phase("PRO-40", 2, "qa", evidence_comment=qa_uuid),
            phase(
                "PRO-41", 2, "qa",
                frontend_sha=None,
                backend_sha=backend_sha,
                evidence_comment=backend_qa_uuid,
            ),
            phase(
                "PRO-45", 2, "integration_qa",
                backend_sha=backend_sha,
                evidence_comment="00000000-0000-4000-8000-000000000085",
            ),
        )
        pull_requests = (
            frontend_pr(),
            PullRequestSnapshot(
                "backend", backend_pr_url, backend_sha, "open", True, True,
            ),
        )
        source = parent_snapshot(
            classification="cross-stack",
            candidate_backend_sha=backend_sha,
            children=source_children,
            pull_requests=pull_requests,
            next_stage=3,
        )
        repair_decision = decide_parent_action(source)
        self.assertEqual(repair_decision.kind, "create_repair_stage")
        bundle = repair_decision.failure_bundle
        replacement_sha = "c" * 40
        repair = phase(
            "PRO-42", 3, "repair", attempt=1,
            project_id=frontend_project,
            pr_url=FRONTEND_PR,
            assignee_id=frontend_owner,
            creation_action=repair_decision.action_key,
            failure_bundle_digest=bundle["digest"],
            failure_evidence_uuids=(review_uuid,),
            repair_repository="frontend",
            repair_pull_request=FRONTEND_PR,
            repair_round=1,
            repair_source_candidates=(
                ("backend", backend_sha),
                ("frontend", FRONTEND_SHA),
            ),
            frontend_sha=replacement_sha,
        )
        decision = decide_parent_action(
            replace(
                source,
                attempt=1,
                last_action=repair_decision.action_key,
                next_stage=4,
                children=(*source.children, repair),
                candidate_frontend_sha=replacement_sha,
                pull_requests=(
                    replace(pull_requests[0], head_sha=replacement_sha),
                    pull_requests[1],
                ),
            )
        )
        self.assertEqual(decision.kind, "create_gate_stage")

    def test_cross_stack_multi_owner_replacements_share_one_source_map(self):
        backend_sha = "b" * 40
        replacement_frontend = "c" * 40
        replacement_backend = "d" * 40
        backend_pr = "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
        frontend_project = "00000000-0000-4000-8000-000000000040"
        backend_project = "00000000-0000-4000-8000-000000000041"
        frontend_owner = "00000000-0000-4000-8000-000000000042"
        backend_owner = "00000000-0000-4000-8000-000000000043"
        frontend_failure = "00000000-0000-4000-8000-000000000091"
        backend_failure = "00000000-0000-4000-8000-000000000092"
        source_children = (
            phase(
                "PRO-70", 1, "implementation",
                project_id=frontend_project,
                pr_url=FRONTEND_PR,
                assignee_id=frontend_owner,
            ),
            phase(
                "PRO-71", 1, "implementation",
                frontend_sha=None,
                backend_sha=backend_sha,
                project_id=backend_project,
                pr_url=backend_pr,
                assignee_id=backend_owner,
            ),
            phase(
                "PRO-72", 2, "review", result="fail",
                evidence_comment=frontend_failure,
                responsible_repositories=("frontend",),
                evidence_comment_url=(
                    f"https://multica.example/comments/{frontend_failure}"
                ),
            ),
            phase(
                "PRO-73", 2, "review", result="fail",
                frontend_sha=None,
                backend_sha=backend_sha,
                evidence_comment=backend_failure,
                responsible_repositories=("backend",),
                evidence_comment_url=(
                    f"https://multica.example/comments/{backend_failure}"
                ),
            ),
            phase(
                "PRO-74", 2, "qa",
                evidence_comment="00000000-0000-4000-8000-000000000093",
            ),
            phase(
                "PRO-75", 2, "qa",
                frontend_sha=None,
                backend_sha=backend_sha,
                evidence_comment="00000000-0000-4000-8000-000000000094",
            ),
            phase(
                "PRO-78", 2, "integration_qa",
                backend_sha=backend_sha,
                evidence_comment="00000000-0000-4000-8000-000000000095",
            ),
        )
        source_prs = (
            frontend_pr(),
            PullRequestSnapshot(
                "backend", backend_pr, backend_sha, "open", True, True,
            ),
        )
        source = parent_snapshot(
            classification="cross-stack",
            candidate_backend_sha=backend_sha,
            children=source_children,
            pull_requests=source_prs,
            next_stage=3,
        )
        repair_decision = decide_parent_action(source)
        self.assertEqual(repair_decision.kind, "create_repair_stage")
        bundle = repair_decision.failure_bundle
        source_map = (
            ("backend", backend_sha),
            ("frontend", FRONTEND_SHA),
        )
        repairs = (
            phase(
                "PRO-76", 3, "repair", attempt=1,
                frontend_sha=replacement_frontend,
                project_id=frontend_project,
                pr_url=FRONTEND_PR,
                assignee_id=frontend_owner,
                creation_action=repair_decision.action_key,
                failure_bundle_digest=bundle["digest"],
                failure_evidence_uuids=(frontend_failure,),
                repair_repository="frontend",
                repair_pull_request=FRONTEND_PR,
                repair_round=1,
                repair_source_candidates=source_map,
            ),
            phase(
                "PRO-77", 3, "repair", attempt=1,
                frontend_sha=None,
                backend_sha=replacement_backend,
                project_id=backend_project,
                pr_url=backend_pr,
                assignee_id=backend_owner,
                creation_action=repair_decision.action_key,
                failure_bundle_digest=bundle["digest"],
                failure_evidence_uuids=(backend_failure,),
                repair_repository="backend",
                repair_pull_request=backend_pr,
                repair_round=1,
                repair_source_candidates=source_map,
            ),
        )
        completed = replace(
            source,
            attempt=1,
            last_action=repair_decision.action_key,
            next_stage=4,
            children=(*source.children, *repairs),
            candidate_frontend_sha=replacement_frontend,
            candidate_backend_sha=replacement_backend,
            pull_requests=(
                frontend_pr(head_sha=replacement_frontend),
                replace(source_prs[1], head_sha=replacement_backend),
            ),
        )

        self.assertEqual(decide_parent_action(completed).kind, "create_gate_stage")
        for label, changed in {
            "one managed head drift": replace(
                completed,
                pull_requests=(
                    completed.pull_requests[0],
                    replace(completed.pull_requests[1], head_sha="e" * 40),
                ),
            ),
            "sibling source map conflict": replace(
                completed,
                children=(
                    *completed.children[:-1],
                    replace(
                        completed.children[-1],
                        repair_source_candidates=(("backend", backend_sha),),
                    ),
                ),
            ),
        }.items():
            with self.subTest(label=label):
                self.assertEqual(decide_parent_action(changed).kind, "block_parent")

    def test_second_failed_complete_repair_cycle_blocks_without_third(self):
        children = (
            phase("PRO-36", 1, "implementation"),
            phase("PRO-37", 2, "review", result="fail"),
            phase("PRO-38", 2, "qa"),
            phase("PRO-39", 3, "repair", attempt=1),
            phase("PRO-40", 4, "review", result="fail", attempt=1),
            phase("PRO-41", 4, "qa", attempt=1),
            phase("PRO-42", 5, "repair", attempt=2),
            phase("PRO-43", 6, "review", result="fail", attempt=2),
            phase("PRO-44", 6, "qa", attempt=2),
        )
        decision = decide_parent_action(
            parent_snapshot(attempt=2, children=children, next_stage=7)
        )
        self.assertEqual(decision.kind, "block_parent")
        self.assertNotIn("repair", decision.reason)

    def test_failed_gate_requires_the_exact_consecutive_next_stage(self):
        valid = self._pro_65_snapshot()
        self.assertEqual(decide_parent_action(valid).kind, "create_repair_stage")

        for next_stage in (1, 2, 4, 99):
            with self.subTest(next_stage=next_stage):
                decision = decide_parent_action(
                    replace(valid, next_stage=next_stage)
                )

                self.assertEqual(decision.kind, "block_parent")
                self.assertIsNone(decision.action_key)
                self.assertIsNone(decision.failure_bundle)

        no_source = decide_parent_action(
            replace(valid, children=(), next_stage=99)
        )
        self.assertEqual(no_source.kind, "noop")
        self.assertIsNone(no_source.action_key)

    def test_exact_member_comment_authorizes_only_round_three_and_binds_action(self):
        snapshot = self._round_three_snapshot()

        decision = decide_parent_action(snapshot)

        self.assertEqual(decision.kind, "create_repair_stage")
        self.assertEqual(decision.failure_bundle["repair_round"], 3)
        self.assertIn(snapshot.authorization_comment_uuid, decision.action_key)

    def test_round_three_authorization_fails_closed_for_every_identity_mismatch(self):
        valid = self._round_three_snapshot()
        digest = decide_parent_action(valid).failure_bundle["digest"]
        cases = {
            "missing": replace(
                valid,
                authorization_comment_uuid="",
                authorizing_comment=None,
            ),
            "non-member": replace(
                valid,
                authorizing_comment=replace(
                    valid.authorizing_comment,
                    author_type="agent",
                ),
            ),
            "malformed body": replace(
                valid,
                authorizing_comment=replace(valid.authorizing_comment, content="not-json"),
            ),
            "wrong digest": replace(
                valid,
                authorizing_comment=replace(
                    valid.authorizing_comment,
                    content=json.dumps(
                        {"bundle_digest": "f" * 64, "granted_round": 3},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            ),
            "wrong round": replace(
                valid,
                authorizing_comment=replace(
                    valid.authorizing_comment,
                    content=json.dumps(
                        {"bundle_digest": digest, "granted_round": 4},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            ),
            "consumed": replace(
                valid,
                consumed_authorization_uuid=valid.authorization_comment_uuid,
            ),
            "different authorization already consumed": replace(
                valid,
                consumed_authorization_uuid=(
                    "00000000-0000-4000-8000-000000000099"
                ),
            ),
        }
        for label, snapshot in cases.items():
            with self.subTest(label=label):
                decision = decide_parent_action(snapshot)
                self.assertEqual(decision.kind, "block_parent")
                self.assertIsNone(decision.failure_bundle)

    def test_round_four_and_out_of_band_head_drift_fail_closed(self):
        round_four = decide_parent_action(
            replace(self._round_three_snapshot(), attempt=3)
        )
        drift = decide_parent_action(self._round_three_snapshot(pr_head="e" * 40))

        self.assertEqual(round_four.kind, "block_parent")
        self.assertEqual(drift.kind, "block_parent")
        self.assertIn("out-of-band", drift.reason)

    def test_attempt_metadata_cannot_reset_completed_repair_history(self):
        children = (
            phase("PRO-36", 1, "implementation"),
            phase("PRO-37", 2, "review", result="fail"),
            phase("PRO-38", 2, "qa"),
            phase("PRO-39", 3, "repair", attempt=1),
            phase("PRO-40", 4, "review", result="fail", attempt=1),
            phase("PRO-41", 4, "qa", attempt=1),
            phase("PRO-42", 5, "repair", attempt=2),
            phase("PRO-43", 6, "review", result="fail", attempt=2),
            phase("PRO-44", 6, "qa", attempt=2),
        )
        decision = decide_parent_action(
            parent_snapshot(attempt=0, children=children)
        )
        self.assertEqual(decision.kind, "block_parent")
        self.assertIn("attempt", decision.reason)

    def test_replacement_sha_invalidates_old_pass_and_creates_fresh_gates(self):
        replacement = "c" * 40
        children = (
            phase("PRO-37", 2, "review"),
            phase("PRO-38", 2, "qa"),
        )
        decision = decide_parent_action(
            parent_snapshot(
                candidate_frontend_sha=replacement,
                children=children,
                pull_requests=(frontend_pr(head_sha=replacement),),
            )
        )
        self.assertEqual(decision.kind, "create_gate_stage")
        self.assertIn(replacement, decision.action_key)

    def test_exact_sha_gates_merge_only_with_current_ready_pr(self):
        children = (
            phase("PRO-37", 2, "review"),
            phase("PRO-38", 2, "qa"),
        )
        self.assertEqual(
            decide_parent_action(parent_snapshot(children=children)).kind,
            "merge",
        )
        for pr in (
            frontend_pr(head_sha="d" * 40),
            frontend_pr(mergeable=False),
            frontend_pr(checks_pass=False),
            frontend_pr(state="merged"),
        ):
            with self.subTest(pr=pr):
                self.assertEqual(
                    decide_parent_action(
                        parent_snapshot(children=children, pull_requests=(pr,))
                    ).kind,
                    "block_parent",
                )

    def test_cross_stack_requires_review_coverage_for_each_repository(self):
        backend_sha = "b" * 40
        backend_pr = PullRequestSnapshot(
            repository="backend",
            url="https://github.com/codeExploreHub/Eventra-Backend/pull/7",
            head_sha=backend_sha,
            state="open",
            mergeable=True,
            checks_pass=True,
        )
        frontend_review = phase("PRO-60", 2, "review")
        combined_qa = phase(
            "PRO-61", 2, "qa", backend_sha=backend_sha
        )
        snapshot = parent_snapshot(
            classification="cross-stack",
            candidate_backend_sha=backend_sha,
            children=(frontend_review, combined_qa),
            pull_requests=(frontend_pr(), backend_pr),
        )
        self.assertEqual(decide_parent_action(snapshot).kind, "block_parent")

        backend_review = phase(
            "PRO-62",
            2,
            "review",
            frontend_sha=None,
            backend_sha=backend_sha,
        )
        self.assertEqual(
            decide_parent_action(
                ParentSnapshot(
                    **{
                        **snapshot.__dict__,
                        "children": (frontend_review, backend_review, combined_qa),
                    }
                )
            ).kind,
            "block_parent",
        )

    def test_cross_stack_gate_membership_is_typed_not_sha_expanded(self):
        backend_sha = "b" * 40
        backend_pr = PullRequestSnapshot(
            repository="backend",
            url="https://github.com/codeExploreHub/Eventra-Backend/pull/7",
            head_sha=backend_sha,
            state="open",
            mergeable=True,
            checks_pass=True,
        )
        combined = (
            phase("PRO-60", 2, "review", backend_sha=backend_sha),
            phase("PRO-61", 2, "qa", backend_sha=backend_sha),
        )
        invalid = parent_snapshot(
            classification="cross-stack",
            candidate_backend_sha=backend_sha,
            children=combined,
            pull_requests=(frontend_pr(), backend_pr),
        )
        self.assertEqual(decide_parent_action(invalid).kind, "block_parent")

        legal = (
            phase("PRO-60", 2, "review"),
            phase("PRO-61", 2, "qa"),
            phase(
                "PRO-62", 2, "review",
                frontend_sha=None,
                backend_sha=backend_sha,
            ),
            phase(
                "PRO-63", 2, "qa",
                frontend_sha=None,
                backend_sha=backend_sha,
            ),
            phase("PRO-64", 2, "integration_qa", backend_sha=backend_sha),
        )
        valid = parent_snapshot(
            classification="cross-stack",
            candidate_backend_sha=backend_sha,
            children=legal,
            pull_requests=(frontend_pr(), backend_pr),
        )
        self.assertEqual(decide_parent_action(valid).kind, "merge")

    def test_current_gate_provenance_rejects_wrong_authority(self):
        valid = parent_snapshot(
            children=(
                phase("PRO-37", 2, "review"),
                phase("PRO-38", 2, "qa"),
            )
        )
        self.assertEqual(decide_parent_action(valid).kind, "merge")
        current = valid.children
        corruptions = {
            "creation action": replace(current[0], creation_action="forged"),
            "target": replace(current[0], phase_target="repository:backend"),
            "role": replace(current[0], phase_role="integration_qa"),
            "project": replace(
                current[0],
                project_id="00000000-0000-4000-8000-000000000099",
            ),
            "assignee": replace(current[0], assignee_id=current[1].assignee_id),
        }
        for label, corrupt in corruptions.items():
            with self.subTest(label=label):
                self.assertEqual(
                    decide_parent_action(
                        replace(valid, children=(corrupt, *current[1:]))
                    ).kind,
                    "block_parent",
                )
        self.assertEqual(
            decide_parent_action(replace(valid, last_action="forged")).kind,
            "block_parent",
        )


    def test_partial_merge_blocks_while_merged_state_routes_smoke_then_done(self):
        self.assertEqual(
            decide_parent_action(parent_snapshot(merge_state="partial")).kind,
            "block_parent",
        )
        self.assertEqual(
            decide_parent_action(parent_snapshot(merge_state="merged")).kind,
            "create_smoke_stage",
        )
        smoke = (phase("PRO-50", 4, "smoke"),)
        self.assertEqual(
            decide_parent_action(
                parent_snapshot(merge_state="merged", children=smoke)
            ).kind,
            "complete_parent",
        )

    def test_merged_smoke_failure_cannot_bypass_completed_attempt_history(self):
        children = (
            phase("PRO-36", 1, "implementation"),
            phase("PRO-37", 2, "review", result="fail"),
            phase("PRO-38", 2, "qa"),
            phase("PRO-39", 3, "repair", attempt=1),
            phase("PRO-40", 4, "review", result="fail", attempt=1),
            phase("PRO-41", 4, "qa", attempt=1),
            phase("PRO-42", 5, "repair", attempt=2),
            phase("PRO-43", 6, "review", attempt=2),
            phase("PRO-44", 6, "qa", attempt=2),
            phase("PRO-45", 7, "smoke", result="fail", attempt=2),
        )
        decision = decide_parent_action(
            parent_snapshot(
                attempt=0,
                merge_state="merged",
                children=children,
            )
        )
        self.assertEqual(decision.kind, "block_parent")
        self.assertIn("attempt", decision.reason)

    def test_incomplete_stage_and_recorded_action_are_noops(self):
        incomplete = (
            PhaseSnapshot(
                issue_key="PRO-36",
                stage=1,
                kind="implementation",
                result=None,
                attempt=0,
                status="in_review",
                frontend_sha=FRONTEND_SHA,
                backend_sha=None,
            ),
        )
        self.assertEqual(
            decide_parent_action(parent_snapshot(children=incomplete)).kind,
            "noop",
        )
        first = decide_parent_action(parent_snapshot())
        second = decide_parent_action(parent_snapshot(last_action=first.action_key))
        self.assertEqual(second.kind, "noop")


class FakeParentCompletionRunner:
    def __init__(self, *, status="in_review", assignee_type="squad"):
        self.issue = raw_issue(
            identifier="PRO-35",
            parent_issue_id=None,
            stage=None,
            status=status,
            assignee_type=assignee_type,
        )
        self.calls = []
        self.freeze_status = False

    def run(self, args, *, stdin_json=None):
        if stdin_json is not None:
            raise AssertionError("workflow commands never accept stdin JSON")
        call = tuple(args)
        self.calls.append(call)
        if call == ("issue", "get", "PRO-35", "--output", "json"):
            return copy.deepcopy(self.issue)
        if call == (
            "issue", "status", "PRO-35", "done", "--no-start", "--output", "json"
        ):
            if not self.freeze_status:
                self.issue["status"] = "done"
            return {"ignored": "mutation acknowledgement"}
        raise AssertionError(f"unsupported argv: {call!r}")


class ParentCompletionTests(unittest.TestCase):
    def completion_snapshot(self, **overrides):
        values = {
            "merge_state": "merged",
            "children": (phase("PRO-50", 4, "smoke"),),
        }
        values.update(overrides)
        return parent_snapshot(**values)

    def test_verified_merged_smoke_moves_parent_directly_to_done(self):
        runner = FakeParentCompletionRunner()
        snapshots = iter((self.completion_snapshot(), self.completion_snapshot()))

        result = finish_parent(runner, "PRO-35", lambda: next(snapshots))

        self.assertEqual(result.issue_key, "PRO-35")
        self.assertEqual(result.status, "done")
        self.assertEqual(result.mutation_count, 1)
        self.assertIn(
            (
                "issue", "status", "PRO-35", "done", "--no-start",
                "--output", "json",
            ),
            runner.calls,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            print_parent_result(result)
        self.assertEqual(output.getvalue(), "issue=PRO-35 status=done mutations=1\n")

    def test_parent_completion_is_idempotent_after_done(self):
        runner = FakeParentCompletionRunner(status="done")

        result = finish_parent(
            runner,
            "PRO-35",
            lambda: self.fail("done parent must not reload mutable gate state"),
        )

        self.assertEqual(result.mutation_count, 0)
        self.assertFalse(any(call[:2] == ("issue", "status") for call in runner.calls))

    def test_parent_completion_fails_closed_without_stable_gate_decision(self):
        cases = (
            (
                self.completion_snapshot(
                    children=(phase("PRO-50", 4, "smoke", result="fail"),)
                ),
            )
            * 2,
            (
                self.completion_snapshot(),
                self.completion_snapshot(candidate_frontend_sha="c" * 40),
            ),
        )
        for snapshots in cases:
            runner = FakeParentCompletionRunner()
            with self.subTest(snapshots=snapshots):
                with self.assertRaisesRegex(
                    RuntimeError, "parent completion is not authorized"
                ):
                    finish_parent(runner, "PRO-35", iter(snapshots).__next__)
                self.assertFalse(
                    any(call[:2] == ("issue", "status") for call in runner.calls)
                )

    def test_parent_completion_rejects_future_smoke_over_current_active_stage(self):
        snapshot = self.completion_snapshot(
            next_stage=5,
            children=(
                phase("PRO-50", 4, "smoke", status="in_progress"),
                phase("PRO-99", 99, "smoke"),
            ),
        )
        runner = FakeParentCompletionRunner()

        with self.assertRaisesRegex(
            RuntimeError,
            "parent completion is not authorized",
        ):
            finish_parent(runner, "PRO-35", lambda: snapshot)

        self.assertFalse(any(call[:2] == ("issue", "status") for call in runner.calls))

    def test_parent_completion_rejects_human_wait_and_unverified_status_write(self):
        human = FakeParentCompletionRunner(assignee_type="member")
        with self.assertRaisesRegex(RuntimeError, "human approval wait"):
            finish_parent(human, "PRO-35", self.completion_snapshot)

        frozen = FakeParentCompletionRunner()
        frozen.freeze_status = True
        with self.assertRaisesRegex(RuntimeError, "parent completion failed"):
            finish_parent(frozen, "PRO-35", self.completion_snapshot)

    def test_parser_accepts_finish_parent_without_deployment_flags(self):
        args = build_workflow_parser().parse_args(["finish-parent", "PRO-35"])
        self.assertEqual(args.command, "finish-parent")
        self.assertEqual(args.parent, "PRO-35")
        self.assertFalse(hasattr(args, "deploy"))


def stalled_workflow(**overrides):
    child = ChildRunSnapshot(
        issue_id=ISSUE_ID,
        identifier="PRO-36",
        stage=1,
        issue_status="in_review",
        latest_run_status="completed",
        latest_run_activity_at="2026-08-25T08:50:28Z",
        has_active_run=False,
        has_phase_completion=False,
    )
    values = {
        "parent_issue_id": PARENT_ID,
        "parent_identifier": "PRO-35",
        "has_human_approval_wait": False,
        "has_malformed_state": False,
        "latest_stage_finished": False,
        "has_later_parent_run": False,
        "active_parent_has_no_executable_successor": False,
        "children": (child,),
    }
    current_stage = overrides.pop("current_stage", 1)
    values.update(overrides)
    snapshot = WorkflowSnapshot(**values)
    object.__setattr__(snapshot, "current_stage", current_stage)
    return snapshot


class RecoveryDecisionTests(unittest.TestCase):
    def test_recovery_never_selects_a_historical_child_over_current_stage(self):
        old_terminal = stalled_workflow().children[0]
        current_active = replace(
            old_terminal,
            issue_id="01a00000-0000-7000-8000-000000000020",
            identifier="PRO-40",
            stage=5,
            latest_run_status="running",
            has_active_run=True,
        )
        active = stalled_workflow(
            current_stage=5,
            children=(old_terminal, current_active),
        )

        self.assertEqual(decide_recovery(active).kind, "noop")

        old_unstarted = replace(
            old_terminal,
            issue_status="todo",
            latest_run_status=None,
            latest_run_activity_at=None,
        )
        current_unstarted = replace(
            old_unstarted,
            issue_id="01a00000-0000-7000-8000-000000000021",
            identifier="PRO-41",
            stage=5,
        )
        eligible = stalled_workflow(
            current_stage=5,
            children=(old_unstarted, current_unstarted),
        )

        self.assertEqual(
            decide_recovery(eligible).issue_key,
            "PRO-41",
        )

    def test_recovery_noops_when_authoritative_current_stage_membership_is_missing(self):
        old_terminal = stalled_workflow().children[0]
        snapshot = stalled_workflow(
            current_stage=5,
            children=(old_terminal,),
        )

        decision = decide_recovery(snapshot)

        self.assertEqual(decision.kind, "noop")
        self.assertIn("current Stage", decision.reason)

    def test_loaded_historical_child_alone_is_visibly_not_current(self):
        runner = FakeWatchRunner()
        runner.metadata["PRO-35"]["eventra.workflow.next_stage"] = "6"

        snapshot = workflow_module.load_workflow_snapshot(runner, "PRO-35")
        decision = decide_recovery(snapshot)

        self.assertTrue(snapshot.has_malformed_state)
        self.assertEqual(snapshot.current_stage, 5)
        self.assertEqual(decision.kind, "noop")
        self.assertIn("current Stage", decision.reason)
    def test_version_one_workflow_requires_migration_without_recovery(self):
        decision = decide_recovery(stalled_workflow(workflow_version=1))

        self.assertEqual(decision.kind, "noop")
        self.assertEqual(
            decision.reason,
            "version 1 workflow requires explicit migration",
        )

    def test_completed_child_run_left_in_review_recovers_oldest_child(self):
        newer = ChildRunSnapshot(
            issue_id="01a00000-0000-7000-8000-000000000020",
            identifier="PRO-40",
            stage=1,
            issue_status="in_review",
            latest_run_status="completed",
            latest_run_activity_at="2026-08-25T09:50:28Z",
            has_active_run=False,
            has_phase_completion=False,
        )
        snapshot = stalled_workflow(
            children=stalled_workflow().children + (newer,)
        )
        self.assertEqual(
            decide_recovery(snapshot),
            RecoveryDecision(
                "rerun_child",
                "PRO-36",
                "terminal run without terminal phase transition",
            ),
        )

    def test_finished_stage_without_successor_recovers_parent_once(self):
        decision = decide_recovery(
            stalled_workflow(
                latest_stage_finished=True,
                children=(),
            )
        )
        self.assertEqual(decision.kind, "rerun_parent")
        self.assertEqual(decision.issue_key, "PRO-35")

    def test_verified_phase_metadata_with_nonterminal_issue_is_recovered(self):
        child = ChildRunSnapshot(
            issue_id=ISSUE_ID,
            identifier="PRO-36",
            stage=1,
            issue_status="in_review",
            latest_run_status="completed",
            latest_run_activity_at="2026-08-25T08:50:28Z",
            has_active_run=False,
            has_phase_completion=True,
        )
        decision = decide_recovery(stalled_workflow(children=(child,)))
        self.assertEqual(decision.kind, "rerun_child")
        self.assertEqual(decision.issue_key, "PRO-36")

    def test_nonterminal_agent_child_without_any_run_is_recovered(self):
        child = ChildRunSnapshot(
            issue_id=ISSUE_ID,
            identifier="PRO-36",
            stage=1,
            issue_status="todo",
            latest_run_status=None,
            latest_run_activity_at=None,
            has_active_run=False,
            has_phase_completion=False,
        )
        decision = decide_recovery(stalled_workflow(children=(child,)))
        self.assertEqual(decision.kind, "rerun_child")
        self.assertEqual(decision.issue_key, "PRO-36")

    def test_human_wait_malformed_and_active_work_are_noops(self):
        active_child = ChildRunSnapshot(
            issue_id=ISSUE_ID,
            identifier="PRO-36",
            stage=1,
            issue_status="in_progress",
            latest_run_status="running",
            latest_run_activity_at="2026-08-25T08:50:28Z",
            has_active_run=True,
            has_phase_completion=False,
        )
        for snapshot in (
            stalled_workflow(has_human_approval_wait=True),
            stalled_workflow(has_malformed_state=True),
            stalled_workflow(children=(active_child,)),
            stalled_workflow(
                children=(),
                latest_stage_finished=True,
                has_later_parent_run=True,
            ),
        ):
            with self.subTest(snapshot=snapshot):
                self.assertEqual(decide_recovery(snapshot).kind, "noop")


class FakeRecoveryRunner:
    def __init__(self):
        self.issue = raw_issue()
        self.runs = [
            {
                "id": "01a00000-0000-7000-8000-000000000030",
                "issue_id": ISSUE_ID,
                "status": "completed",
                "created_at": "2026-08-25T08:33:39Z",
                "dispatched_at": "2026-08-25T08:33:39Z",
                "started_at": "2026-08-25T08:34:04Z",
                "completed_at": "2026-08-25T08:50:28Z",
            }
        ]
        self.calls = []
        self.freeze_rerun = False

    def run(self, args, *, stdin_json=None):
        call = tuple(args)
        self.calls.append(call)
        if call == ("issue", "get", "PRO-36", "--output", "json"):
            return copy.deepcopy(self.issue)
        if call == ("issue", "runs", "PRO-36", "--output", "json"):
            return copy.deepcopy(self.runs)
        if call == ("issue", "rerun", "PRO-36", "--output", "json"):
            if not self.freeze_rerun:
                self.runs.append(
                    {
                        "id": "01a00000-0000-7000-8000-000000000031",
                        "issue_id": ISSUE_ID,
                        "status": "queued",
                        "created_at": "2026-08-25T10:00:00Z",
                        "dispatched_at": None,
                        "started_at": None,
                        "completed_at": None,
                    }
                )
            return {"ignored": "ack"}
        raise AssertionError(f"unsupported argv: {call!r}")


class RecoveryMutationTests(unittest.TestCase):
    def test_recover_once_rereads_then_verifies_new_active_task(self):
        runner = FakeRecoveryRunner()
        snapshots = [stalled_workflow(), stalled_workflow()]

        result = recover_once(runner, lambda: snapshots.pop(0))

        self.assertEqual(result.decision.kind, "rerun_child")
        self.assertEqual(result.mutation_count, 1)
        self.assertEqual(runner.calls[-2], ("issue", "rerun", "PRO-36", "--output", "json"))
        self.assertEqual(runner.calls[-1], ("issue", "runs", "PRO-36", "--output", "json"))

    def test_recover_once_fails_when_rerun_has_no_new_active_task(self):
        runner = FakeRecoveryRunner()
        runner.freeze_rerun = True
        snapshots = [stalled_workflow(), stalled_workflow()]
        with self.assertRaisesRegex(RuntimeError, "recovery verification failed"):
            recover_once(runner, lambda: snapshots.pop(0))

    def test_recover_once_stops_when_authoritative_reread_changes_decision(self):
        runner = FakeRecoveryRunner()
        healthy = stalled_workflow(
            has_human_approval_wait=True,
        )
        result = recover_once(runner, iter((stalled_workflow(), healthy)).__next__)
        self.assertEqual(result.decision.kind, "noop")
        self.assertEqual(result.mutation_count, 0)
        self.assertFalse(any(call[:2] == ("issue", "rerun") for call in runner.calls))

    def test_recover_once_stops_if_native_wakeup_appears_before_rerun(self):
        runner = FakeRecoveryRunner()
        runner.runs.append(
            {
                "id": "01a00000-0000-7000-8000-000000000032",
                "issue_id": ISSUE_ID,
                "status": "queued",
                "created_at": "2026-08-25T09:59:59Z",
                "dispatched_at": None,
                "started_at": None,
                "completed_at": None,
            }
        )
        snapshots = [stalled_workflow(), stalled_workflow()]

        result = recover_once(runner, lambda: snapshots.pop(0))

        self.assertEqual(result.decision.kind, "noop")
        self.assertEqual(result.mutation_count, 0)
        self.assertFalse(any(call[:2] == ("issue", "rerun") for call in runner.calls))


class FakeWatchRunner:
    PROJECTS = (PROJECT_ID, "00000000-0000-4000-8000-000000000040")

    def __init__(self):
        self.parent = raw_issue(
            id=PARENT_ID,
            identifier="PRO-35",
            parent_issue_id=None,
            stage=None,
            status="in_progress",
            assignee_type="squad",
            updated_at="2026-08-25T08:33:49Z",
        )
        self.child = raw_issue()
        self.metadata = {
            "PRO-35": {
                "eventra.workflow.version": "2",
                "eventra.workflow.next_stage": "2",
            },
            "PRO-36": {},
        }
        self.runs = {
            "PRO-35": [
                {
                    "id": "01a00000-0000-7000-8000-000000000050",
                    "issue_id": PARENT_ID,
                    "status": "completed",
                    "created_at": "2026-08-25T08:31:59Z",
                    "dispatched_at": "2026-08-25T08:32:00Z",
                    "started_at": "2026-08-25T08:32:17Z",
                    "completed_at": "2026-08-25T08:34:02Z",
                }
            ],
            "PRO-36": copy.deepcopy(FakeRecoveryRunner().runs),
        }
        self.calls = []

    def run(self, args, *, stdin_json=None):
        call = tuple(args)
        self.calls.append(call)
        if call[:2] == ("issue", "list"):
            flags = dict(zip(call[2::2], call[3::2]))
            self._assert_list_flags(flags)
            parent_version = self.metadata["PRO-35"]["eventra.workflow.version"]
            expected_filter = {
                "1": '"eventra.workflow.version=""1"""',
                "2": '"eventra.workflow.version=""2"""',
            }[parent_version]
            issues = (
                [self.parent]
                if flags["--project"] == PROJECT_ID
                and flags["--status"] == "in_progress"
                and flags["--metadata"] == expected_filter
                else []
            )
            return {
                "has_more": False,
                "issues": copy.deepcopy(issues),
                "limit": 50,
                "offset": 0,
                "total": len(issues),
            }
        if call[:2] == ("issue", "get"):
            return copy.deepcopy(self.parent if call[2] == "PRO-35" else self.child)
        if call == ("issue", "children", "PRO-35", "--output", "json"):
            child_done = int(self.child["status"] == "done")
            return {
                "stages": [
                    {
                        "stage": 1,
                        "total": 1,
                        "done": child_done,
                        "issues": [copy.deepcopy(self.child)],
                    }
                ],
                "total": 1,
                "unstaged": [],
            }
        if call[:3] == ("issue", "metadata", "list"):
            return copy.deepcopy(self.metadata[call[3]])
        if call[:2] == ("issue", "runs"):
            return copy.deepcopy(self.runs[call[2]])
        if call == ("issue", "rerun", "PRO-36", "--output", "json"):
            self.runs["PRO-36"].append(
                {
                    "id": "01a00000-0000-7000-8000-000000000051",
                    "issue_id": ISSUE_ID,
                    "status": "queued",
                    "created_at": "2026-08-25T10:00:00Z",
                    "dispatched_at": None,
                    "started_at": None,
                    "completed_at": None,
                }
            )
            return {"ignored": "ack"}
        raise AssertionError(f"unsupported argv: {call!r}")

    def _assert_list_flags(self, flags):
        expected = {
            "--limit": "50",
            "--offset": "0",
            "--output": "json",
        }
        for key, item in expected.items():
            if flags.get(key) != item:
                raise AssertionError(f"wrong list flag {key}")
        if flags.get("--metadata") not in {
            '"eventra.workflow.version=""1"""',
            '"eventra.workflow.version=""2"""',
        }:
            raise AssertionError("wrong list flag --metadata")
        if flags.get("--project") not in self.PROJECTS:
            raise AssertionError("foreign project")
        if flags.get("--status") not in {"in_progress", "in_review"}:
            raise AssertionError("foreign status")


class WatchWorkflowTests(unittest.TestCase):
    def test_version_one_watcher_state_never_mutates(self):
        runner = FakeWatchRunner()
        runner.metadata["PRO-35"] = {"eventra.workflow.version": "1"}

        result = watch_projects(runner, runner.PROJECTS, apply=True)

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.applied, 0)
        self.assertEqual(result.decision, "noop")
        self.assertEqual(
            getattr(result, "reason", None),
            "version 1 workflow requires explicit migration",
        )
        self.assertFalse(any(call[:2] == ("issue", "rerun") for call in runner.calls))
        output = io.StringIO()
        with redirect_stdout(output):
            print_watch_result(result)
        self.assertIn(
            "version 1 workflow requires explicit migration",
            output.getvalue(),
        )

    def test_string_metadata_filter_is_json_string_inside_one_csv_field(self):
        self.assertEqual(
            _string_metadata_filter("eventra.workflow.version", "1"),
            '"eventra.workflow.version=""1"""',
        )

    def test_watch_dry_run_detects_but_does_not_mutate_stalled_pro_35(self):
        runner = FakeWatchRunner()
        result = watch_projects(runner, runner.PROJECTS, apply=False)
        self.assertEqual(result, WatchResult(1, 1, 0, "rerun_child"))
        self.assertFalse(any(call[:2] == ("issue", "rerun") for call in runner.calls))

    def test_watch_apply_recovers_at_most_once_and_second_apply_is_noop(self):
        runner = FakeWatchRunner()
        first = watch_projects(runner, runner.PROJECTS, apply=True)
        second = watch_projects(runner, runner.PROJECTS, apply=True)
        self.assertEqual(first.applied, 1)
        self.assertEqual(second.applied, 0)
        self.assertEqual(
            sum(call[:2] == ("issue", "rerun") for call in runner.calls),
            1,
        )

    def test_watch_parser_requires_two_project_ids_and_defaults_to_dry_run(self):
        args = build_workflow_parser().parse_args(
            [
                "watch",
                "--project-id", PROJECT_ID,
                "--backend-project-id", FakeWatchRunner.PROJECTS[1],
            ]
        )
        self.assertEqual(args.command, "watch")
        self.assertFalse(args.apply)

    def test_historical_member_child_does_not_suppress_recovery(self):
        runner = FakeWatchRunner()
        runner.child["assignee_type"] = "member"
        runner.child["status"] = "done"
        runner.metadata["PRO-36"] = build_phase_metadata(
            implementation_completion()
        )
        runner.child["updated_at"] = "2026-08-25T08:51:00Z"

        result = watch_projects(runner, runner.PROJECTS, apply=False)

        self.assertEqual(result.decision, "rerun_parent")

    def test_active_member_assignment_suppresses_recovery_without_metadata(self):
        runner = FakeWatchRunner()
        runner.child["assignee_type"] = "member"

        result = watch_projects(runner, runner.PROJECTS, apply=False)

        self.assertEqual(result, WatchResult(1, 0, 0, "noop"))


class FakeParentRunner(FakeWatchRunner):
    def __init__(self):
        super().__init__()
        self.comment_records = []
        self.metadata["PRO-35"] = {
            "eventra.workflow.version": "2",
            "eventra.workflow.classification": "frontend-only",
            "eventra.workflow.next_stage": "2",
            "eventra.workflow.attempt": "0",
            "eventra.workflow.frontend_sha": FRONTEND_SHA,
            "eventra.workflow.merge_state": "not_ready",
            "eventra.workflow.last_action": "",
        }
        self.metadata["PRO-36"] = build_phase_metadata(
            implementation_completion()
        )
        self.child["status"] = "done"

    def run(self, args, *, stdin_json=None):
        call = tuple(args)
        if call[:4] == ("issue", "comment", "list", "PRO-35"):
            self.calls.append(call)
            return copy.deepcopy(self.comment_records)
        if call == ("issue", "children", "PRO-35", "--output", "json"):
            self.calls.append(call)
            return {
                "stages": [
                    {"stage": 1, "total": 1, "done": 1, "issues": [copy.deepcopy(self.child)]}
                ],
                "total": 1,
                "unstaged": [],
            }
        return super().run(args, stdin_json=stdin_json)


class FakeGitHubRunner:
    def __init__(self):
        self.calls = []

    def run(self, args):
        self.calls.append(tuple(args))
        if tuple(args) != (
            "pr", "view", FRONTEND_PR,
            "--json", "url,headRefOid,state,mergeable,mergeStateStatus,statusCheckRollup",
        ):
            raise AssertionError(f"unsupported gh argv: {args!r}")
        return {
            "url": FRONTEND_PR,
            "headRefOid": FRONTEND_SHA,
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [],
        }


class FakeRepairGitHubRunner:
    def __init__(self):
        self.head_sha = "b" * 40
        self.calls = []

    def run(self, args):
        self.calls.append(tuple(args))
        url = "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
        if tuple(args) != (
            "pr", "view", url,
            "--json", "url,headRefOid,state,mergeable,mergeStateStatus,statusCheckRollup",
        ):
            raise AssertionError(f"unsupported gh argv: {args!r}")
        return {
            "url": url,
            "headRefOid": self.head_sha,
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [],
        }


class FakeCrossStackRepairGitHubRunner:
    def __init__(self):
        self.head_shas = {
            FRONTEND_PR: FRONTEND_SHA,
            FakeRepairRunner.BACKEND_PR: FakeRepairRunner.BACKEND_SHA,
        }
        self.calls = []

    def run(self, args):
        self.calls.append(tuple(args))
        url = args[2] if len(args) > 2 else ""
        if tuple(args) != (
            "pr", "view", url,
            "--json", "url,headRefOid,state,mergeable,mergeStateStatus,statusCheckRollup",
        ) or url not in self.head_shas:
            raise AssertionError(f"unsupported gh argv: {args!r}")
        return {
            "url": url,
            "headRefOid": self.head_shas[url],
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [],
        }


class FakeRepairRunner:
    BACKEND_PR = "https://github.com/codeExploreHub/Eventra-Backend/pull/7"
    BACKEND_SHA = "b" * 40
    AUTH_UUID = "00000000-0000-4000-8000-000000000061"
    REVIEW_UUID = "00000000-0000-4000-8000-000000000062"
    QA_UUID = "00000000-0000-4000-8000-000000000063"

    def __init__(self, *, attempt=2):
        self.parent = raw_issue(
            id=PARENT_ID,
            identifier="PRO-65",
            parent_issue_id=None,
            stage=None,
            status="in_progress",
            assignee_type="squad",
        )
        self.children = []
        self.metadata = {
            "PRO-65": {
                "eventra.workflow.version": "2",
                "eventra.workflow.classification": "backend-only",
                "eventra.workflow.next_stage": str({0: 3, 1: 5, 2: 7}[attempt]),
                "eventra.workflow.attempt": str(attempt),
                "eventra.workflow.backend_sha": self.BACKEND_SHA,
                "eventra.workflow.merge_state": "not_ready",
                "eventra.workflow.last_action": "",
            }
        }
        self.comments = []
        self.calls = []
        self.runs = {}
        self.next_child_number = 80
        self.fail_once_parent_key = None
        self.fail_once_create = False
        self.lost_ack_once = set()
        self.committed_mutations = 0
        self.drift_reservation_after_parent_metadata_reads = None
        self.corrupt_created_title = False
        self.suppress_status_run = False
        self.create_two_active_runs = False
        self._add_done_child(1, "implementation", 0, result="pass", pr=True)
        if attempt >= 1:
            self._add_done_child(3, "repair", 1, result="pass", pr=True)
        if attempt >= 2:
            self._add_done_child(5, "repair", 2, result="pass", pr=True)
        gate_stage = {0: 2, 1: 4, 2: 6}[attempt]
        self.metadata["PRO-65"]["eventra.workflow.last_action"] = (
            f"2:PRO-65:create_gate_stage:{attempt}:backend:-:"
            f"{self.BACKEND_SHA}:next-stage:{gate_stage}"
        )
        self._add_done_child(
            gate_stage,
            "review",
            attempt,
            result="fail",
            comment_uuid=self.REVIEW_UUID,
            owners=("backend",),
        )
        self._add_done_child(
            gate_stage,
            "qa",
            attempt,
            result="pass",
            comment_uuid=self.QA_UUID,
        )

    def _identifier(self):
        identifier = f"PRO-{self.next_child_number}"
        self.next_child_number += 1
        return identifier

    def _add_done_child(
        self,
        stage,
        kind,
        attempt,
        *,
        result,
        pr=False,
        comment_uuid=None,
        owners=(),
    ):
        identifier = self._identifier()
        child = raw_issue(
            id=f"01a00000-0000-7000-8000-{self.next_child_number:012d}",
            identifier=identifier,
            parent_issue_id=PARENT_ID,
            stage=stage,
            status="done",
            project_id="00000000-0000-4000-8000-000000000040",
            assignee_id=(
                REVIEWER_ID if kind == "review" else QA_ID
            ),
        )
        comment_uuid = comment_uuid or f"00000000-0000-4000-8000-{self.next_child_number:012d}"
        metadata = {
            "eventra.workflow.version": "2",
            "eventra.phase.kind": kind,
            "eventra.phase.result": result,
            "eventra.phase.attempt": str(attempt),
            "eventra.phase.evidence_comment": comment_uuid,
            "eventra.phase.failure_repositories": json.dumps(list(owners)),
            "eventra.phase.sha.backend": self.BACKEND_SHA,
        }
        if owners:
            metadata["eventra.phase.evidence_comment_url"] = (
                f"https://multica.example/comments/{comment_uuid}"
            )
        if pr:
            metadata["eventra.phase.pr"] = self.BACKEND_PR
        if kind in {"review", "qa"}:
            metadata.update(
                {
                    "eventra.phase.creation_action": (
                        f"2:PRO-65:create_gate_stage:{attempt}:backend:-:"
                        f"{self.BACKEND_SHA}:next-stage:{stage}"
                    ),
                    "eventra.phase.target": "repository:backend",
                    "eventra.phase.role": (
                        "independent_reviewer"
                        if kind == "review"
                        else "integration_qa"
                    ),
                }
            )
        self.children.append(child)
        self.metadata[identifier] = metadata

    def authorize(self, bundle_digest, *, author_type="member", granted_round=3):
        self.metadata["PRO-65"][
            "eventra.workflow.repair_authorization_comment"
        ] = self.AUTH_UUID
        self.comments = [
            {
                "id": self.AUTH_UUID,
                "author_type": author_type,
                "content": json.dumps(
                    {
                        "bundle_digest": bundle_digest,
                        "granted_round": granted_round,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        ]

    @property
    def mutation_calls(self):
        return [
            call
            for call in self.calls
            if call[:2] == ("issue", "create")
            or call[:3] == ("issue", "metadata", "set")
            or call[:3] == ("issue", "metadata", "delete")
            or call[:2] == ("issue", "status")
        ]

    def _children_payload(self):
        stages = []
        for stage in sorted({child["stage"] for child in self.children}):
            issues = [child for child in self.children if child["stage"] == stage]
            stages.append(
                {
                    "stage": stage,
                    "total": len(issues),
                    "done": sum(child["status"] == "done" for child in issues),
                    "issues": copy.deepcopy(issues),
                }
            )
        return {"stages": stages, "total": len(self.children), "unstaged": []}

    def _maybe_lose_ack(self, *tokens):
        match = next((token for token in tokens if token in self.lost_ack_once), None)
        if match is not None:
            self.lost_ack_once.remove(match)
            raise RuntimeError("injected lost acknowledgement")

    @staticmethod
    def _flag(args, name):
        return args[args.index(name) + 1]

    def run(self, args, *, stdin_json=None):
        if stdin_json is not None:
            raise AssertionError("repair executor does not accept stdin JSON")
        call = tuple(args)
        self.calls.append(call)
        if call[:2] == ("issue", "get"):
            identifier = call[2]
            if identifier in {"PRO-65", PARENT_ID}:
                return copy.deepcopy(self.parent)
            return copy.deepcopy(
                next(child for child in self.children if child["identifier"] == identifier)
            )
        if call == ("issue", "children", "PRO-65", "--output", "json"):
            return self._children_payload()
        if call[:3] == ("issue", "metadata", "list"):
            value = copy.deepcopy(self.metadata[call[3]])
            if (
                call[3] == "PRO-65"
                and self.drift_reservation_after_parent_metadata_reads is not None
            ):
                self.drift_reservation_after_parent_metadata_reads -= 1
                if self.drift_reservation_after_parent_metadata_reads == 0:
                    reservation = json.loads(
                        self.metadata["PRO-65"][
                            "eventra.workflow.repair_reservation"
                        ]
                    )
                    reservation["previous_last_action"] = "concurrent-drift"
                    self.metadata["PRO-65"][
                        "eventra.workflow.repair_reservation"
                    ] = json.dumps(
                        reservation,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    self.drift_reservation_after_parent_metadata_reads = None
            return value
        if call[:4] == ("issue", "comment", "list", "PRO-65"):
            return copy.deepcopy(self.comments)
        if call[:2] == ("issue", "create"):
            if self.fail_once_create:
                self.fail_once_create = False
                raise RuntimeError("injected child create failure")
            identifier = self._identifier()
            child = raw_issue(
                id=f"01a00000-0000-7000-8000-{self.next_child_number:012d}",
                identifier=identifier,
                parent_issue_id=PARENT_ID,
                stage=int(self._flag(args, "--stage")),
                status=self._flag(args, "--status"),
                project_id=self._flag(args, "--project"),
                assignee_id=self._flag(args, "--assignee-id"),
                title=self._flag(args, "--title"),
                description=self._flag(args, "--description"),
            )
            if self.corrupt_created_title:
                child["title"] = "server-side conflicting title"
            self.children.append(child)
            self.metadata[identifier] = {}
            self.runs[identifier] = []
            self.committed_mutations += 1
            self._maybe_lose_ack("create")
            return copy.deepcopy(child)
        if call[:3] == ("issue", "metadata", "set"):
            identifier = call[3]
            key = self._flag(args, "--key")
            value = self._flag(args, "--value")
            if identifier == "PRO-65" and key == self.fail_once_parent_key:
                self.fail_once_parent_key = None
                raise RuntimeError("injected parent metadata failure")
            if self.metadata[identifier].get(key) != value:
                self.metadata[identifier][key] = value
                self.committed_mutations += 1
            self._maybe_lose_ack(
                f"set:{identifier}:{key}",
                f"set-child:{key}" if identifier != "PRO-65" else "",
            )
            return {"ok": True}
        if call[:3] == ("issue", "metadata", "delete"):
            identifier = call[3]
            key = self._flag(args, "--key")
            if key in self.metadata[identifier]:
                self.metadata[identifier].pop(key)
                self.committed_mutations += 1
            self._maybe_lose_ack(f"delete:{identifier}:{key}")
            return {"ok": True}
        if call[:2] == ("issue", "status"):
            identifier = call[2]
            status = call[3]
            child = next(child for child in self.children if child["identifier"] == identifier)
            if child["status"] != status:
                child["status"] = status
                self.committed_mutations += 1
            if status == "done":
                for run in self.runs.get(identifier, []):
                    if run["status"] in {"queued", "dispatched", "running"}:
                        run["status"] = "completed"
                        run["completed_at"] = "2026-08-25T09:30:00Z"
            if "--no-start" not in call and not self.suppress_status_run:
                runs = self.runs.setdefault(identifier, [])
                if not any(run["status"] in {"queued", "dispatched", "running"} for run in runs):
                    runs.append(
                        {
                            "id": f"run-{identifier}-{len(runs) + 1}",
                            "issue_id": child["id"],
                            "status": "queued",
                            "created_at": "2026-08-25T09:00:00Z",
                            "dispatched_at": None,
                            "started_at": None,
                            "completed_at": None,
                        }
                    )
                    if self.create_two_active_runs:
                        duplicate_run = copy.deepcopy(runs[-1])
                        duplicate_run["id"] += "-duplicate"
                        runs.append(duplicate_run)
            self._maybe_lose_ack("status")
            return copy.deepcopy(child)
        if call[:2] == ("issue", "runs"):
            return copy.deepcopy(self.runs.get(call[2], []))
        raise AssertionError(f"unsupported argv: {call!r}")


class RepairExecutionTests(unittest.TestCase):
    SOURCE_GATE_FORGERIES = (
        "creation_action",
        "phase_target",
        "phase_role",
        "assignee_type",
        "project_id",
        "candidate_sha",
    )

    def test_parser_requires_the_exact_expected_repair_action(self):
        args = build_workflow_parser().parse_args(
            [
                "execute-parent-repair",
                "PRO-65",
                "--expected-action-key",
                "2:PRO-65:create_repair_stage:3:backend:-:" + "b" * 40,
            ]
        )

        self.assertEqual(args.command, "execute-parent-repair")
        self.assertEqual(args.parent, "PRO-65")
        self.assertTrue(args.expected_action_key.startswith("2:PRO-65:"))

    def _planned(self, *, attempt=2):
        runner = FakeRepairRunner(attempt=attempt)
        github = FakeRepairGitHubRunner()
        snapshot = load_parent_snapshot(runner, github, "PRO-65")
        provisional_bundle = _failure_bundle(
            snapshot,
            tuple(child for child in snapshot.children if child.stage == snapshot.next_stage - 1),
        )
        if attempt == 2:
            runner.authorize(provisional_bundle["digest"])
        snapshot = load_parent_snapshot(runner, github, "PRO-65")
        decision = decide_parent_action(snapshot)
        self.assertEqual(decision.kind, "create_repair_stage")
        return runner, github, decision

    @staticmethod
    def _forge_source_gate_identity(runner, face):
        source_reviews = [
            child
            for child in runner.children
            if runner.metadata[child["identifier"]].get("eventra.phase.kind")
            == "review"
        ]
        source = max(source_reviews, key=lambda child: child["stage"])
        metadata = runner.metadata[source["identifier"]]
        if face == "creation_action":
            metadata["eventra.phase.creation_action"] = "forged-source-action"
        elif face == "phase_target":
            metadata["eventra.phase.target"] = "repository:frontend"
        elif face == "phase_role":
            metadata["eventra.phase.role"] = "integration_qa"
        elif face == "assignee_type":
            source["assignee_type"] = "member"
        elif face == "project_id":
            source["project_id"] = "00000000-0000-4000-8000-000000000098"
        elif face == "candidate_sha":
            metadata["eventra.phase.sha.backend"] = "e" * 40
        else:
            raise AssertionError(f"unknown source Gate forgery: {face}")

    def test_planner_rejects_forged_historical_source_gate_identity_all_rounds(self):
        for attempt in (0, 1, 2):
            for face in self.SOURCE_GATE_FORGERIES:
                with self.subTest(repair_round=attempt + 1, face=face):
                    runner, github, decision = self._planned(attempt=attempt)
                    created = execute_parent_repair(
                        runner,
                        github,
                        "PRO-65",
                        expected_action_key=decision.action_key,
                    )
                    self.assertEqual(created.next_action, "repair", created.reason)
                    self._forge_source_gate_identity(runner, face)
                    before = len(runner.mutation_calls)

                    planned = decide_parent_action(
                        load_parent_snapshot(runner, github, "PRO-65")
                    )

                    self.assertEqual(planned.kind, "block_parent", planned.reason)
                    self.assertEqual(len(runner.mutation_calls), before)

    def test_reservation_rejects_forged_historical_source_gate_identity_all_rounds(self):
        for attempt in (0, 1, 2):
            for face in self.SOURCE_GATE_FORGERIES:
                with self.subTest(repair_round=attempt + 1, face=face):
                    runner, github, decision = self._planned(attempt=attempt)
                    snapshot = load_parent_snapshot(runner, github, "PRO-65")
                    reservation = _build_repair_reservation(snapshot, decision)
                    self._forge_source_gate_identity(runner, face)
                    fresh = load_parent_snapshot(runner, github, "PRO-65")
                    before = len(runner.mutation_calls)

                    with self.assertRaisesRegex(
                        RuntimeError, "source Gate identity"
                    ):
                        workflow_module._validate_repair_reservation(
                            fresh,
                            reservation,
                            decision.action_key,
                        )

                    self.assertEqual(len(runner.mutation_calls), before)

    def test_replay_rejects_forged_historical_source_gate_identity_all_rounds(self):
        for attempt in (0, 1, 2):
            for face in self.SOURCE_GATE_FORGERIES:
                with self.subTest(repair_round=attempt + 1, face=face):
                    runner, github, decision = self._planned(attempt=attempt)
                    created = execute_parent_repair(
                        runner,
                        github,
                        "PRO-65",
                        expected_action_key=decision.action_key,
                    )
                    self.assertEqual(created.next_action, "repair", created.reason)
                    self._forge_source_gate_identity(runner, face)
                    before = len(runner.mutation_calls)

                    replay = execute_parent_repair(
                        runner,
                        github,
                        "PRO-65",
                        expected_action_key=decision.action_key,
                    )

                    self.assertEqual(replay.next_action, "block", replay.reason)
                    self.assertEqual(replay.mutation_count, 0)
                    self.assertEqual(len(runner.mutation_calls), before)

    @staticmethod
    def _retarget_latest_child(
        runner,
        *,
        repository,
        project_id,
        assignee_id,
        creation_action=None,
        target=None,
        role=None,
    ):
        child = runner.children[-1]
        metadata = runner.metadata[child["identifier"]]
        metadata.pop("eventra.phase.sha.frontend", None)
        metadata.pop("eventra.phase.sha.backend", None)
        metadata[f"eventra.phase.sha.{repository}"] = (
            FRONTEND_SHA if repository == "frontend" else runner.BACKEND_SHA
        )
        if metadata.get("eventra.phase.pr") is not None:
            metadata["eventra.phase.pr"] = (
                FRONTEND_PR if repository == "frontend" else runner.BACKEND_PR
            )
        if creation_action is not None:
            metadata["eventra.phase.creation_action"] = creation_action
        if target is not None:
            metadata["eventra.phase.target"] = target
        if role is not None:
            metadata["eventra.phase.role"] = role
        child["project_id"] = project_id
        child["assignee_id"] = assignee_id
        return child, metadata

    def _planned_cross_stack_integration_failure(
        self,
        *,
        attempt=2,
        owners=("backend", "frontend"),
    ):
        runner = FakeRepairRunner(attempt=attempt)
        runner.metadata["PRO-65"].update(
            {
                "eventra.workflow.classification": "cross-stack",
                "eventra.workflow.frontend_sha": FRONTEND_SHA,
            }
        )
        runner._add_done_child(1, "implementation", 0, result="pass", pr=True)
        self._retarget_latest_child(
            runner,
            repository="frontend",
            project_id=PROJECT_ID,
            assignee_id=AGENT_ID,
        )
        gate_stage = {0: 2, 1: 4, 2: 6}[attempt]
        gate_action = (
            f"2:PRO-65:create_gate_stage:{attempt}:cross-stack:"
            f"{FRONTEND_SHA}:{runner.BACKEND_SHA}:next-stage:{gate_stage}"
        )
        runner.metadata["PRO-65"]["eventra.workflow.last_action"] = gate_action
        current = [child for child in runner.children if child["stage"] == gate_stage]
        for child in current:
            metadata = runner.metadata[child["identifier"]]
            metadata["eventra.phase.creation_action"] = gate_action
            metadata["eventra.phase.result"] = "pass"
            metadata["eventra.phase.failure_repositories"] = "[]"
            metadata.pop("eventra.phase.evidence_comment_url", None)

        runner._add_done_child(
            gate_stage,
            "review",
            attempt,
            result="pass",
            comment_uuid="00000000-0000-4000-8000-000000000064",
        )
        self._retarget_latest_child(
            runner,
            repository="frontend",
            project_id=PROJECT_ID,
            assignee_id=REVIEWER_ID,
            creation_action=gate_action,
            target="repository:frontend",
            role="independent_reviewer",
        )
        runner._add_done_child(
            gate_stage,
            "qa",
            attempt,
            result="pass",
            comment_uuid="00000000-0000-4000-8000-000000000065",
        )
        self._retarget_latest_child(
            runner,
            repository="frontend",
            project_id=PROJECT_ID,
            assignee_id=QA_ID,
            creation_action=gate_action,
            target="repository:frontend",
            role="integration_qa",
        )
        integration_uuid = "00000000-0000-4000-8000-000000000066"
        runner._add_done_child(
            gate_stage,
            "integration_qa",
            attempt,
            result="fail",
            comment_uuid=integration_uuid,
            owners=owners,
        )
        integration_child = runner.children[-1]
        integration_metadata = runner.metadata[integration_child["identifier"]]
        integration_metadata.update(
            {
                "eventra.phase.sha.frontend": FRONTEND_SHA,
                "eventra.phase.sha.backend": runner.BACKEND_SHA,
                "eventra.phase.failure_repositories": json.dumps(
                    list(owners), separators=(",", ":")
                ),
                "eventra.phase.creation_action": gate_action,
                "eventra.phase.target": "suite:integration",
                "eventra.phase.role": "integration_qa",
            }
        )
        integration_child["project_id"] = PROJECT_ID
        integration_child["assignee_id"] = QA_ID
        github = FakeCrossStackRepairGitHubRunner()
        snapshot = load_parent_snapshot(runner, github, "PRO-65")
        if attempt == 2:
            provisional_bundle = _failure_bundle(
                snapshot,
                tuple(
                    child
                    for child in snapshot.children
                    if child.stage == snapshot.next_stage - 1
                ),
            )
            runner.authorize(provisional_bundle["digest"])
            snapshot = load_parent_snapshot(runner, github, "PRO-65")
        decision = decide_parent_action(snapshot)
        self.assertEqual(decision.kind, "create_repair_stage")
        return runner, github, decision, integration_uuid

    @staticmethod
    def _change_failure_suite(reservation, phase, suite_key):
        changed = copy.deepcopy(reservation)
        failure = next(
            item
            for item in changed["failure_bundle"]["failures"]
            if item["phase"] == phase
        )
        failure["suite_key"] = suite_key
        payload = dict(changed["failure_bundle"])
        old_digest = payload.pop("digest")
        new_digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        changed["failure_bundle"]["digest"] = new_digest
        changed["action_key"] = changed["action_key"].replace(
            f":bundle:{old_digest}", f":bundle:{new_digest}"
        )
        return changed

    def test_integration_failure_handoff_is_partitioned_and_suite_bound(self):
        runner, _, decision, integration_uuid = (
            self._planned_cross_stack_integration_failure()
        )
        snapshot = load_parent_snapshot(
            runner, FakeCrossStackRepairGitHubRunner(), "PRO-65"
        )
        reservation = _build_repair_reservation(snapshot, decision)

        self.assertEqual(
            [spec["repository"] for spec in reservation["child_specs"]],
            ["backend", "frontend"],
        )
        for spec in reservation["child_specs"]:
            with self.subTest(repository=spec["repository"]):
                rendered = workflow_module._render_repair_handoff(
                    reservation, spec
                )
                self.assertIn("integration_qa |", rendered)
                self.assertIn("suite=integration", rendered)
                self.assertIn(integration_uuid, rendered)
                self.assertEqual(spec["evidence_uuids"], [integration_uuid])

        for malformed_suite in ("", "unknown", "suite:integration"):
            with self.subTest(malformed_suite=malformed_suite):
                malformed = self._change_failure_suite(
                    reservation, "integration_qa", malformed_suite
                )
                spec = malformed["child_specs"][0]
                with self.assertRaisesRegex(RuntimeError, "failure identity"):
                    workflow_module._render_repair_handoff(malformed, spec)

    def test_repository_gate_handoff_rejects_coherent_nonempty_suite_forgery(self):
        for failure_phase in ("review", "qa"):
            with self.subTest(failure_phase=failure_phase):
                runner = FakeRepairRunner(attempt=0)
                if failure_phase == "qa":
                    for child in runner.children:
                        if child["stage"] != 2:
                            continue
                        metadata = runner.metadata[child["identifier"]]
                        if metadata["eventra.phase.kind"] == "review":
                            metadata["eventra.phase.result"] = "pass"
                            metadata["eventra.phase.failure_repositories"] = "[]"
                            metadata.pop(
                                "eventra.phase.evidence_comment_url", None
                            )
                        elif metadata["eventra.phase.kind"] == "qa":
                            evidence_uuid = metadata[
                                "eventra.phase.evidence_comment"
                            ]
                            metadata.update(
                                {
                                    "eventra.phase.result": "fail",
                                    "eventra.phase.failure_repositories": '["backend"]',
                                    "eventra.phase.evidence_comment_url": (
                                        "https://multica.example/comments/"
                                        + evidence_uuid
                                    ),
                                }
                            )
                github = FakeRepairGitHubRunner()
                snapshot = load_parent_snapshot(runner, github, "PRO-65")
                decision = decide_parent_action(snapshot)
                self.assertEqual(decision.kind, "create_repair_stage")
                reservation = _build_repair_reservation(snapshot, decision)
                spec = reservation["child_specs"][0]
                valid = workflow_module._render_repair_handoff(
                    reservation, spec
                )
                self.assertIn(f"{failure_phase} |", valid)
                self.assertNotIn("suite=", valid)
                forged = self._change_failure_suite(
                    reservation,
                    failure_phase,
                    "forged-suite",
                )
                before = len(runner.mutation_calls)

                with self.assertRaisesRegex(RuntimeError, "failure identity"):
                    workflow_module._render_repair_handoff(forged, spec)

                self.assertEqual(len(runner.mutation_calls), before)

    def test_execute_integration_failure_creates_exact_owner_repairs_all_rounds(self):
        for attempt in (0, 1, 2):
            with self.subTest(repair_round=attempt + 1):
                runner, github, decision, integration_uuid = (
                    self._planned_cross_stack_integration_failure(attempt=attempt)
                )

                result = execute_parent_repair(
                    runner,
                    github,
                    "PRO-65",
                    expected_action_key=decision.action_key,
                )

                self.assertEqual(result.next_action, "repair", result.reason)
                created = [
                    child
                    for child in runner.children
                    if child["stage"] == {0: 3, 1: 5, 2: 7}[attempt]
                ]
                self.assertEqual(len(created), 2)
                for child in created:
                    metadata = runner.metadata[child["identifier"]]
                    self.assertEqual(
                        metadata["eventra.repair.failure_evidence_uuids"],
                        json.dumps([integration_uuid], separators=(",", ":")),
                    )
                    self.assertIn("suite=integration", child["description"])

    def test_integration_failure_strict_owner_subset_creates_only_that_repair(self):
        runner, github, decision, integration_uuid = (
            self._planned_cross_stack_integration_failure(
                attempt=0,
                owners=("frontend",),
            )
        )

        result = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )

        self.assertEqual(result.next_action, "repair", result.reason)
        created = [child for child in runner.children if child["stage"] == 3]
        self.assertEqual(len(created), 1)
        metadata = runner.metadata[created[0]["identifier"]]
        self.assertEqual(metadata["eventra.repair.repository"], "frontend")
        self.assertEqual(
            metadata["eventra.repair.failure_evidence_uuids"],
            json.dumps([integration_uuid], separators=(",", ":")),
        )

    def test_mixed_failure_handoff_has_stable_review_qa_suite_order(self):
        runner, github, _, _ = self._planned_cross_stack_integration_failure(
            attempt=0
        )
        for child in runner.children:
            metadata = runner.metadata[child["identifier"]]
            if (
                child["stage"] == 2
                and metadata["eventra.phase.kind"] in {"review", "qa"}
                and "eventra.phase.sha.backend" in metadata
            ):
                evidence_uuid = metadata["eventra.phase.evidence_comment"]
                metadata.update(
                    {
                        "eventra.phase.result": "fail",
                        "eventra.phase.failure_repositories": '["backend"]',
                        "eventra.phase.evidence_comment_url": (
                            f"https://multica.example/comments/{evidence_uuid}"
                        ),
                    }
                )
        snapshot = load_parent_snapshot(runner, github, "PRO-65")
        decision = decide_parent_action(snapshot)
        self.assertEqual(decision.kind, "create_repair_stage")
        reservation = _build_repair_reservation(snapshot, decision)
        backend_spec = next(
            spec
            for spec in reservation["child_specs"]
            if spec["repository"] == "backend"
        )

        rendered = workflow_module._render_repair_handoff(
            reservation, backend_spec
        )

        self.assertLess(rendered.index("review |"), rendered.index("qa |"))
        self.assertLess(
            rendered.index("qa |"), rendered.index("integration_qa |")
        )

    def test_integration_failure_malformed_authority_blocks_before_mutation(self):
        cases = {
            "missing suite": lambda runner, child, metadata: metadata.pop(
                "eventra.phase.target"
            ),
            "malformed suite": lambda runner, child, metadata: metadata.__setitem__(
                "eventra.phase.target", "suite:unknown"
            ),
            "unknown owner": lambda runner, child, metadata: metadata.__setitem__(
                "eventra.phase.failure_repositories", '["frontend","unknown"]'
            ),
            "empty owners": lambda runner, child, metadata: metadata.__setitem__(
                "eventra.phase.failure_repositories", "[]"
            ),
            "noncanonical evidence URL": lambda runner, child, metadata: metadata.__setitem__(
                "eventra.phase.evidence_comment_url",
                metadata["eventra.phase.evidence_comment_url"] + "?forged=1",
            ),
            "malformed evidence UUID": lambda runner, child, metadata: metadata.__setitem__(
                "eventra.phase.evidence_comment", "not-a-uuid"
            ),
            "mixed duplicate evidence": lambda runner, child, metadata: self._make_duplicate_gate_evidence(
                runner, metadata["eventra.phase.evidence_comment"]
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                runner, github, decision, _ = (
                    self._planned_cross_stack_integration_failure(attempt=0)
                )
                integration = next(
                    child
                    for child in runner.children
                    if runner.metadata[child["identifier"]]["eventra.phase.kind"]
                    == "integration_qa"
                )
                metadata = runner.metadata[integration["identifier"]]
                mutate(runner, integration, metadata)

                result = execute_parent_repair(
                    runner,
                    github,
                    "PRO-65",
                    expected_action_key=decision.action_key,
                )

                self.assertEqual(result.next_action, "block")
                self.assertEqual(result.mutation_count, 0)

    @staticmethod
    def _make_duplicate_gate_evidence(runner, evidence_uuid):
        backend_qa = next(
            child
            for child in runner.children
            if child["stage"] == 2
            and runner.metadata[child["identifier"]]["eventra.phase.kind"] == "qa"
            and "eventra.phase.sha.backend" in runner.metadata[child["identifier"]]
        )
        metadata = runner.metadata[backend_qa["identifier"]]
        metadata.update(
            {
                "eventra.phase.result": "fail",
                "eventra.phase.evidence_comment": evidence_uuid,
                "eventra.phase.evidence_comment_url": (
                    f"https://multica.example/comments/{evidence_uuid}"
                ),
                "eventra.phase.failure_repositories": '["backend"]',
            }
        )

    def test_executor_replans_binds_consumes_and_replays_round_three_once(self):
        runner, github, decision = self._planned()

        first = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )
        before_replay = len(runner.mutation_calls)
        replay = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )

        self.assertEqual(first.next_action, "repair")
        self.assertEqual(replay.next_action, "noop")
        self.assertEqual(len(runner.mutation_calls), before_replay)
        parent = runner.metadata["PRO-65"]
        self.assertEqual(parent["eventra.workflow.attempt"], "3")
        self.assertEqual(parent["eventra.workflow.next_stage"], "8")
        self.assertEqual(parent["eventra.workflow.last_action"], decision.action_key)
        self.assertNotIn(
            "eventra.workflow.repair_authorization_comment",
            parent,
        )
        self.assertEqual(
            parent["eventra.workflow.repair_authorization_consumed"],
            FakeRepairRunner.AUTH_UUID,
        )
        self.assertNotIn("eventra.workflow.repair_reservation", parent)
        repairs = [child for child in runner.children if child["stage"] == 7]
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["status"], "todo")
        metadata = runner.metadata[repairs[0]["identifier"]]
        self.assertEqual(metadata["eventra.repair.creation_action"], decision.action_key)
        self.assertEqual(
            metadata["eventra.repair.failure_bundle_digest"],
            decision.failure_bundle["digest"],
        )
        self.assertEqual(
            metadata["eventra.repair.authorizing_comment_uuid"],
            FakeRepairRunner.AUTH_UUID,
        )
        self.assertEqual(metadata["eventra.repair.pull_request"], runner.BACKEND_PR)
        self.assertEqual(
            metadata["eventra.repair.source_candidates"],
            json.dumps(
                {"backend": runner.BACKEND_SHA},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        create_call = next(
            call for call in runner.calls if call[:2] == ("issue", "create")
        )
        self.assertIn("--assignee-id", create_call)
        self.assertNotIn("--assignee", create_call)

    def test_executor_is_the_exact_single_path_for_automatic_rounds_one_and_two(self):
        for current_round in (0, 1):
            with self.subTest(current_round=current_round):
                runner, github, decision = self._planned(attempt=current_round)

                result = execute_parent_repair(
                    runner,
                    github,
                    "PRO-65",
                    expected_action_key=decision.action_key,
                )

                self.assertEqual(result.next_action, "repair")
                self.assertEqual(
                    runner.metadata["PRO-65"]["eventra.workflow.attempt"],
                    str(current_round + 1),
                )
                repair = next(
                    child
                    for child in runner.children
                    if child["stage"] == {0: 3, 1: 5}[current_round]
                )
                self.assertEqual(
                    runner.metadata[repair["identifier"]][
                        "eventra.repair.authorizing_comment_uuid"
                    ],
                    "",
                )

    def test_rounds_finish_with_replacements_wait_for_lead_copy_then_gate_and_replay(self):
        for source_attempt, replacement_sha in zip(
            (0, 1, 2),
            ("c" * 40, "d" * 40, "e" * 40),
            strict=True,
        ):
            with self.subTest(repair_round=source_attempt + 1):
                runner, github, decision = self._planned(attempt=source_attempt)
                executed = execute_parent_repair(
                    runner,
                    github,
                    "PRO-65",
                    expected_action_key=decision.action_key,
                )
                self.assertEqual(executed.next_action, "repair")
                stage = {0: 3, 1: 5, 2: 7}[source_attempt]
                child = next(item for item in runner.children if item["stage"] == stage)
                child_key = str(child["identifier"])
                immutable_before = {
                    key: value
                    for key, value in runner.metadata[child_key].items()
                    if key.startswith("eventra.repair.")
                }
                completion = PhaseCompletion(
                    kind="repair",
                    result="pass",
                    attempt=source_attempt + 1,
                    evidence_comment=COMMENT_ID,
                    frontend_sha=None,
                    backend_sha=replacement_sha,
                    pr_url=runner.BACKEND_PR,
                )
                github.head_sha = replacement_sha

                with patch.object(
                    workflow_module,
                    "GitHubRunner",
                    return_value=github,
                ):
                    finished = finish_phase(runner, child_key, completion)

                self.assertEqual(finished.status, "done")
                self.assertEqual(
                    runner.metadata[child_key]["eventra.phase.sha.backend"],
                    replacement_sha,
                )
                self.assertEqual(
                    {
                        key: value
                        for key, value in runner.metadata[child_key].items()
                        if key.startswith("eventra.repair.")
                    },
                    immutable_before,
                )
                self.assertEqual(
                    runner.metadata["PRO-65"]["eventra.workflow.backend_sha"],
                    runner.BACKEND_SHA,
                )

                before_copy = decide_parent_action(
                    load_parent_snapshot(runner, github, "PRO-65")
                )
                self.assertEqual(before_copy.kind, "block_parent")
                self.assertIn("parent candidate", before_copy.reason)

                before_pre_copy_replay = len(runner.mutation_calls)
                pre_copy_replay = execute_parent_repair(
                    runner,
                    github,
                    "PRO-65",
                    expected_action_key=decision.action_key,
                )
                self.assertEqual(
                    pre_copy_replay.next_action,
                    "noop",
                    pre_copy_replay.reason,
                )
                self.assertEqual(
                    len(runner.mutation_calls),
                    before_pre_copy_replay,
                )

                runner.metadata["PRO-65"][
                    "eventra.workflow.backend_sha"
                ] = replacement_sha
                after_copy = decide_parent_action(
                    load_parent_snapshot(runner, github, "PRO-65")
                )
                self.assertEqual(after_copy.kind, "create_gate_stage")

                before_replay = len(runner.mutation_calls)
                replay = execute_parent_repair(
                    runner,
                    github,
                    "PRO-65",
                    expected_action_key=decision.action_key,
                )
                self.assertEqual(replay.next_action, "noop", replay.reason)
                self.assertEqual(len(runner.mutation_calls), before_replay)

    def test_partial_cross_stack_exact_action_replay_uses_planner_head_rules(self):
        snapshots = ParentDecisionTests()
        for repair_round in (1, 2, 3):
            for parent_copied in (False, True):
                for active_head in ("b" * 40, "e" * 40):
                    with self.subTest(
                        repair_round=repair_round,
                        parent_copied=parent_copied,
                        active_head=active_head,
                    ):
                        snapshot = snapshots._partial_cross_stack_repair_snapshot(
                            repair_round=repair_round,
                            backend_head=active_head,
                            parent_frontend_sha=(
                                "c" * 40 if parent_copied else FRONTEND_SHA
                            ),
                        )
                        self.assertEqual(decide_parent_action(snapshot).kind, "noop")
                        runner = FakeSnapshotFinishRunner(
                            snapshot,
                            "PRO-77",
                            repair_replay=True,
                        )
                        github = FakeSnapshotGitHubRunner(snapshot.pull_requests)

                        result = execute_parent_repair(
                            runner,
                            github,
                            snapshot.identifier,
                            expected_action_key=snapshot.last_action,
                        )

                        self.assertEqual(result.next_action, "noop", result.reason)
                        self.assertEqual(result.mutation_count, 0)
                        self.assertEqual(runner.mutation_count, 0)

    def test_partial_cross_stack_exact_action_replay_blocks_head_provenance_drift(self):
        snapshots = ParentDecisionTests()
        for repair_round in (1, 2, 3):
            valid = snapshots._partial_cross_stack_repair_snapshot(
                repair_round=repair_round,
            )
            active_single_owner = snapshots._partial_cross_stack_repair_snapshot(
                repair_round=repair_round,
                frontend_done=False,
                backend_owned=False,
                frontend_head="e" * 40,
            )
            cases = {
                "completed head mismatch": replace(
                    valid,
                    pull_requests=(
                        replace(valid.pull_requests[0], head_sha="e" * 40),
                        valid.pull_requests[1],
                    ),
                ),
                "active owner adopted": replace(
                    valid,
                    candidate_backend_sha="e" * 40,
                    pull_requests=(
                        valid.pull_requests[0],
                        replace(valid.pull_requests[1], head_sha="e" * 40),
                    ),
                ),
                "unaffected head drift": replace(
                    active_single_owner,
                    pull_requests=(
                        active_single_owner.pull_requests[0],
                        replace(
                            active_single_owner.pull_requests[1],
                            head_sha="f" * 40,
                        ),
                    ),
                ),
            }
            for label, snapshot in cases.items():
                with self.subTest(repair_round=repair_round, case=label):
                    self.assertEqual(
                        decide_parent_action(snapshot).kind,
                        "block_parent",
                    )
                    runner = FakeSnapshotFinishRunner(
                        snapshot,
                        "PRO-77",
                        repair_replay=True,
                    )
                    github = FakeSnapshotGitHubRunner(snapshot.pull_requests)

                    result = execute_parent_repair(
                        runner,
                        github,
                        snapshot.identifier,
                        expected_action_key=snapshot.last_action,
                    )

                    self.assertEqual(result.next_action, "block", result.reason)
                    self.assertEqual(result.mutation_count, 0)
                    self.assertEqual(runner.mutation_count, 0)

    def test_executor_recovers_exact_reservation_after_partial_failure(self):
        runner, github, decision = self._planned()
        runner.fail_once_parent_key = "eventra.workflow.attempt"

        interrupted = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )
        self.assertEqual(interrupted.next_action, "block")
        self.assertIn(
            "eventra.workflow.repair_reservation",
            runner.metadata["PRO-65"],
        )

        recovered = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )

        self.assertEqual(recovered.next_action, "repair")
        self.assertNotIn(
            "eventra.workflow.repair_reservation",
            runner.metadata["PRO-65"],
        )
        self.assertEqual(
            len([child for child in runner.children if child["stage"] == 7]),
            1,
        )

    def test_executor_recovers_missing_owner_child_and_blocks_a_duplicate(self):
        runner, github, decision = self._planned()
        runner.fail_once_create = True

        interrupted = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )
        self.assertEqual(interrupted.next_action, "block")
        self.assertEqual(
            len([child for child in runner.children if child["stage"] == 7]),
            0,
        )

        recovered = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )
        self.assertEqual(recovered.next_action, "repair")
        self.assertEqual(
            len([child for child in runner.children if child["stage"] == 7]),
            1,
        )

        runner, github, decision = self._planned()
        runner.fail_once_parent_key = "eventra.workflow.attempt"
        interrupted = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )
        self.assertEqual(interrupted.next_action, "block")
        original = next(child for child in runner.children if child["stage"] == 7)
        duplicate = copy.deepcopy(original)
        duplicate["identifier"] = runner._identifier()
        duplicate["id"] = "01a00000-0000-7000-8000-000000000099"
        runner.children.append(duplicate)
        runner.metadata[duplicate["identifier"]] = copy.deepcopy(
            runner.metadata[original["identifier"]]
        )
        before = len(runner.mutation_calls)

        blocked = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )

        self.assertEqual(blocked.next_action, "block")
        self.assertEqual(len(runner.mutation_calls), before)

    def test_executor_blocks_conflicting_reservation_duplicate_child_and_drift(self):
        runner, github, decision = self._planned()
        runner.metadata["PRO-65"]["eventra.workflow.repair_reservation"] = json.dumps(
            {
                "action_key": "2:PRO-65:create_repair_stage:3:backend:-:"
                + runner.BACKEND_SHA
                + ":bundle:"
                + "f" * 64,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        before = len(runner.mutation_calls)

        conflict = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )

        self.assertEqual(conflict.next_action, "block")
        self.assertEqual(len(runner.mutation_calls), before)

        runner, github, decision = self._planned()
        github.head_sha = "e" * 40
        drift = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )
        self.assertEqual(drift.next_action, "block")
        self.assertEqual(runner.mutation_calls, [])

    def test_inner_reread_blocks_reservation_drift_before_child_creation(self):
        runner, github, decision = self._planned()
        # Return the persisted reservation to the outer caller, then change the
        # authoritative value before the child-creation reread.
        runner.drift_reservation_after_parent_metadata_reads = 2

        result = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )

        self.assertEqual(result.next_action, "block")
        self.assertFalse(any(call[:2] == ("issue", "create") for call in runner.calls))

    def test_action_identity_binds_the_exact_source_and_next_stage(self):
        runner, github, decision = self._planned()
        snapshot = load_parent_snapshot(runner, github, "PRO-65")
        bundle = decision.failure_bundle
        self.assertIsNotNone(bundle)

        changed = _action_key(
            replace(snapshot, next_stage=snapshot.next_stage + 1),
            "create_repair_stage",
            3,
            bundle["digest"],
            FakeRepairRunner.AUTH_UUID,
        )
        changed_source = _action_key(
            snapshot,
            "create_repair_stage",
            3,
            bundle["digest"],
            FakeRepairRunner.AUTH_UUID,
            5,
        )

        self.assertNotEqual(decision.action_key, changed)
        self.assertNotEqual(decision.action_key, changed_source)

    def test_reservation_parser_binds_consecutive_and_action_stages(self):
        runner, github, decision = self._planned()
        snapshot = load_parent_snapshot(runner, github, "PRO-65")
        reservation = _build_repair_reservation(snapshot, decision)

        malformed = []
        for next_stage in (5, 6, 8, 99):
            changed = copy.deepcopy(reservation)
            changed["next_stage"] = next_stage
            malformed.append(changed)
        wrong_source_action = copy.deepcopy(reservation)
        wrong_source_action["action_key"] = wrong_source_action[
            "action_key"
        ].replace(":source-stage:6:", ":source-stage:5:")
        malformed.append(wrong_source_action)
        empty_source_action = copy.deepcopy(reservation)
        empty_source_action["action_key"] = empty_source_action[
            "action_key"
        ].replace(":source-stage:6:", ":source-stage::")
        malformed.append(empty_source_action)
        wrong_next_action = copy.deepcopy(reservation)
        wrong_next_action["action_key"] = wrong_next_action[
            "action_key"
        ].replace(":next-stage:7:", ":next-stage:99:")
        malformed.append(wrong_next_action)
        missing_source_candidates = copy.deepcopy(reservation)
        missing_source_candidates.pop("source_candidates")
        malformed.append(missing_source_candidates)
        wrong_source_candidates = copy.deepcopy(reservation)
        wrong_source_candidates["source_candidates"] = {"backend": "e" * 40}
        malformed.append(wrong_source_candidates)
        extra_source_candidate = copy.deepcopy(reservation)
        extra_source_candidate["source_candidates"]["frontend"] = FRONTEND_SHA
        malformed.append(extra_source_candidate)

        for value in malformed:
            with self.subTest(
                next_stage=value["next_stage"],
                action=value["action_key"],
            ):
                encoded = json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                with self.assertRaisesRegex(RuntimeError, "malformed repair reservation"):
                    workflow_module._decode_repair_reservation(encoded)

    def test_executor_blocks_nonconsecutive_stage_and_a_stale_expected_key(self):
        runner = FakeRepairRunner()
        github = FakeRepairGitHubRunner()
        runner.metadata["PRO-65"]["eventra.workflow.next_stage"] = "99"
        snapshot = load_parent_snapshot(runner, github, "PRO-65")
        bundle = _failure_bundle(
            snapshot,
            tuple(child for child in snapshot.children if child.stage == 6),
        )
        runner.authorize(bundle["digest"])
        snapshot = load_parent_snapshot(runner, github, "PRO-65")
        forged_current_key = _action_key(
            snapshot,
            "create_repair_stage",
            3,
            bundle["digest"],
            runner.AUTH_UUID,
            6,
        )
        stale_valid_key = _action_key(
            replace(snapshot, next_stage=7),
            "create_repair_stage",
            3,
            bundle["digest"],
            runner.AUTH_UUID,
            6,
        )

        stale = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=stale_valid_key,
        )
        current = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=forged_current_key,
        )

        self.assertEqual(stale.next_action, "block")
        self.assertEqual(current.next_action, "block")
        self.assertEqual(runner.mutation_calls, [])

    def test_replay_requires_complete_exact_authoritative_child_identity(self):
        cases = {
            "workflow v1": lambda issue, metadata: metadata.__setitem__(
                "eventra.workflow.version", "1"
            ),
            "missing phase PR": lambda issue, metadata: metadata.pop(
                "eventra.phase.pr"
            ),
            "changed bundle digest": lambda issue, metadata: metadata.__setitem__(
                "eventra.repair.failure_bundle_digest", "f" * 64
            ),
            "missing source candidates": lambda issue, metadata: metadata.pop(
                "eventra.repair.source_candidates"
            ),
            "changed source candidates": lambda issue, metadata: metadata.__setitem__(
                "eventra.repair.source_candidates",
                json.dumps(
                    {"backend": "e" * 40},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
            "changed title": lambda issue, metadata: issue.__setitem__(
                "title", "forged repair handoff"
            ),
            "malformed title": lambda issue, metadata: issue.__setitem__(
                "title", None
            ),
            "changed description": lambda issue, metadata: issue.__setitem__(
                "description", "forged repair handoff"
            ),
            "extra provenance": lambda issue, metadata: metadata.__setitem__(
                "eventra.repair.unbound", "forged"
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                runner, github, decision = self._planned()
                completed = execute_parent_repair(
                    runner,
                    github,
                    "PRO-65",
                    expected_action_key=decision.action_key,
                )
                self.assertEqual(completed.next_action, "repair")
                child = next(item for item in runner.children if item["stage"] == 7)
                mutate(child, runner.metadata[child["identifier"]])
                before = len(runner.mutation_calls)

                replay = execute_parent_repair(
                    runner,
                    github,
                    "PRO-65",
                    expected_action_key=decision.action_key,
                )

                self.assertEqual(replay.next_action, "block")
                self.assertEqual(len(runner.mutation_calls), before)

    def test_handoff_is_complete_partitioned_deterministic_and_redacted(self):
        runner = FakeRepairRunner()
        qa = next(
            child
            for child in runner.children
            if child["stage"] == 6
            and runner.metadata[child["identifier"]]["eventra.phase.kind"] == "qa"
        )
        qa_metadata = runner.metadata[qa["identifier"]]
        qa_metadata["eventra.phase.result"] = "blocked"
        qa_metadata["eventra.phase.failure_repositories"] = '["backend"]'
        qa_metadata["eventra.phase.evidence_comment_url"] = (
            f"https://multica.example/comments/{runner.QA_UUID}"
        )
        github = FakeRepairGitHubRunner()
        snapshot = load_parent_snapshot(runner, github, "PRO-65")
        bundle = _failure_bundle(
            snapshot,
            tuple(child for child in snapshot.children if child.stage == 6),
        )
        runner.authorize(bundle["digest"])
        snapshot = load_parent_snapshot(runner, github, "PRO-65")
        decision = decide_parent_action(snapshot)
        reservation = _build_repair_reservation(snapshot, decision)
        spec = reservation["child_specs"][0]

        rendered = workflow_module._render_repair_handoff(reservation, spec)

        self.assertIn(f"Parent: PRO-65", rendered)
        self.assertIn(f"Action: {decision.action_key}", rendered)
        self.assertIn(f"Failure bundle: {bundle['digest']}", rendered)
        self.assertIn("Source stage: 6", rendered)
        self.assertIn("Next stage: 7", rendered)
        self.assertIn("Repair round: 3", rendered)
        self.assertIn(f"backend: {runner.BACKEND_SHA}", rendered)
        self.assertIn(f"Managed PR: {runner.BACKEND_PR}", rendered)
        self.assertIn(runner.REVIEW_UUID, rendered)
        self.assertIn(runner.QA_UUID, rendered)
        self.assertLess(rendered.index("review |"), rendered.index("qa |"))
        self.assertNotIn(runner.comments[0]["content"], rendered)
        self.assertLessEqual(
            len(rendered.encode("utf-8")),
            workflow_module.MAX_REPAIR_DESCRIPTION_BYTES,
        )
        self.assertLessEqual(
            len(json.dumps(reservation, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            workflow_module.MAX_REPAIR_RESERVATION_BYTES,
        )

        incomplete = copy.deepcopy(spec)
        incomplete["evidence_uuids"] = incomplete["evidence_uuids"][:-1]
        with self.assertRaisesRegex(RuntimeError, "partition"):
            workflow_module._render_repair_handoff(reservation, incomplete)

        with patch.object(workflow_module, "MAX_REPAIR_DESCRIPTION_BYTES", 1):
            with self.assertRaisesRegex(RuntimeError, "description limit"):
                workflow_module._render_repair_handoff(reservation, spec)
        with patch.object(workflow_module, "MAX_REPAIR_RESERVATION_BYTES", 1):
            with self.assertRaisesRegex(RuntimeError, "metadata limit"):
                _build_repair_reservation(snapshot, decision)

    def test_promotion_starts_exactly_one_active_agent_run(self):
        runner, github, decision = self._planned()

        result = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )
        child = next(item for item in runner.children if item["stage"] == 7)
        status_call = next(call for call in runner.calls if call[:2] == ("issue", "status"))

        self.assertEqual(result.next_action, "repair")
        self.assertNotIn("--no-start", status_call)
        self.assertEqual(len(runner.runs[child["identifier"]]), 1)
        self.assertEqual(runner.runs[child["identifier"]][0]["status"], "queued")

        replay = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )
        self.assertEqual(replay.next_action, "noop")
        self.assertEqual(len(runner.runs[child["identifier"]]), 1)

    def test_retry_blocks_two_active_runs_and_retains_the_reservation(self):
        runner, github, decision = self._planned()
        runner.create_two_active_runs = True

        first = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )

        self.assertEqual(first.next_action, "block")
        self.assertIn("exactly one active agent run", first.reason)
        self.assertEqual(first.mutation_count, runner.committed_mutations)
        self.assertIn(
            "eventra.workflow.repair_reservation",
            runner.metadata["PRO-65"],
        )
        child = next(item for item in runner.children if item["stage"] == 7)
        self.assertEqual(len(runner.runs[child["identifier"]]), 2)
        committed_before_retry = runner.committed_mutations

        retry = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )

        self.assertEqual(retry.next_action, "block")
        self.assertIn("exactly one active agent run", retry.reason)
        self.assertEqual(retry.mutation_count, 0)
        self.assertEqual(runner.committed_mutations, committed_before_retry)
        self.assertIn(
            "eventra.workflow.repair_reservation",
            runner.metadata["PRO-65"],
        )

    def test_replay_allows_active_owner_head_motion_without_adoption(self):
        runner, github, decision = self._planned()
        completed = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )
        self.assertEqual(completed.next_action, "repair")
        github.head_sha = "e" * 40

        replay = execute_parent_repair(
            runner,
            github,
            "PRO-65",
            expected_action_key=decision.action_key,
        )

        self.assertEqual(replay.next_action, "noop", replay.reason)
        self.assertEqual(replay.mutation_count, 0)
        self.assertEqual(
            runner.metadata["PRO-65"]["eventra.workflow.backend_sha"],
            runner.BACKEND_SHA,
        )

    def test_lost_acknowledgements_reconcile_and_report_observed_effects(self):
        cases = (
            "set:PRO-65:eventra.workflow.repair_reservation",
            "create",
            "set-child:eventra.repair.creation_action",
            "set:PRO-65:eventra.workflow.attempt",
            "status",
            "delete:PRO-65:eventra.workflow.repair_reservation",
        )
        for lost_ack in cases:
            with self.subTest(lost_ack=lost_ack):
                runner, github, decision = self._planned()
                runner.lost_ack_once.add(lost_ack)

                result = execute_parent_repair(
                    runner,
                    github,
                    "PRO-65",
                    expected_action_key=decision.action_key,
                )

                self.assertEqual(result.next_action, "repair")
                self.assertEqual(result.mutation_count, runner.committed_mutations)
                self.assertGreater(result.mutation_count, 0)

    def test_blocked_partial_effects_are_still_reported_truthfully(self):
        for fault in ("corrupt_created_title", "suppress_status_run"):
            with self.subTest(fault=fault):
                runner, github, decision = self._planned()
                setattr(runner, fault, True)

                result = execute_parent_repair(
                    runner,
                    github,
                    "PRO-65",
                    expected_action_key=decision.action_key,
                )

                self.assertEqual(result.next_action, "block")
                self.assertEqual(result.mutation_count, runner.committed_mutations)
                self.assertGreater(result.mutation_count, 0)


class ParentSnapshotReadTests(unittest.TestCase):
    def test_loaded_partial_repair_executor_provenance_fails_closed(self):
        digest = "d" * 64
        action = (
            "2:PRO-65:create_repair_stage:1:backend:-:"
            + FakeRepairRunner.BACKEND_SHA
            + ":next-stage:3:source-stage:2:bundle:"
            + digest
        )
        evidence_uuid = "00000000-0000-4000-8000-000000000071"
        provenance = {
            "eventra.repair.creation_action": action,
            "eventra.repair.failure_bundle_digest": digest,
            "eventra.repair.failure_evidence_uuids": f'["{evidence_uuid}"]',
            "eventra.repair.authorizing_comment_uuid": "",
            "eventra.repair.repository": "backend",
            "eventra.repair.pull_request": FakeRepairRunner.BACKEND_PR,
            "eventra.repair.round": "1",
            "eventra.repair.source_candidates": json.dumps(
                {"backend": FakeRepairRunner.BACKEND_SHA},
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        for missing in provenance:
            with self.subTest(missing=missing):
                runner = FakeRepairRunner(attempt=1)
                repair = next(
                    child for child in runner.children if child["stage"] == 3
                )
                runner.metadata[str(repair["identifier"])].update(
                    {
                        key: value
                        for key, value in provenance.items()
                        if key != missing
                    }
                )

                with self.assertRaisesRegex(
                    RuntimeError,
                    "repair provenance",
                ):
                    load_parent_snapshot(
                        runner,
                        FakeRepairGitHubRunner(),
                        "PRO-65",
                    )

    def test_loaded_repair_source_candidates_are_canonical_complete_and_action_bound(self):
        runner = FakeRepairRunner(attempt=1)
        repair = next(child for child in runner.children if child["stage"] == 3)
        repair_key = str(repair["identifier"])
        digest = "d" * 64
        action = (
            "2:PRO-65:create_repair_stage:1:backend:-:"
            + FakeRepairRunner.BACKEND_SHA
            + ":next-stage:3:source-stage:2:bundle:"
            + digest
        )
        complete = {
            "eventra.repair.creation_action": action,
            "eventra.repair.failure_bundle_digest": digest,
            "eventra.repair.failure_evidence_uuids": (
                '["00000000-0000-4000-8000-000000000071"]'
            ),
            "eventra.repair.authorizing_comment_uuid": "",
            "eventra.repair.repository": "backend",
            "eventra.repair.pull_request": FakeRepairRunner.BACKEND_PR,
            "eventra.repair.round": "1",
            "eventra.repair.source_candidates": json.dumps(
                {"backend": FakeRepairRunner.BACKEND_SHA},
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        cases = {
            "malformed": "not-json",
            "empty": "{}",
            "partial or extra": json.dumps(
                {
                    "backend": FakeRepairRunner.BACKEND_SHA,
                    "frontend": FRONTEND_SHA,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "wrong source": json.dumps(
                {"backend": "e" * 40},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "noncanonical": json.dumps(
                {"backend": FakeRepairRunner.BACKEND_SHA},
            ),
        }

        for label, source_candidates in cases.items():
            with self.subTest(label=label):
                runner.metadata[repair_key] = {
                    **runner.metadata[repair_key],
                    **complete,
                    "eventra.repair.source_candidates": source_candidates,
                }

                with self.assertRaisesRegex(RuntimeError, "repair provenance"):
                    load_parent_snapshot(
                        runner,
                        FakeRepairGitHubRunner(),
                        "PRO-65",
                    )

    def test_loaded_current_repair_without_executor_provenance_blocks(self):
        runner = FakeRepairRunner(attempt=1)
        runner.children = [
            child for child in runner.children if child["stage"] <= 3
        ]
        runner.metadata["PRO-65"]["eventra.workflow.next_stage"] = "4"
        runner.metadata["PRO-65"]["eventra.workflow.last_action"] = (
            "2:PRO-65:create_repair_stage:1:backend:-:"
            + runner.BACKEND_SHA
            + ":next-stage:3:source-stage:2:bundle:"
            + "d" * 64
        )

        snapshot = load_parent_snapshot(
            runner,
            FakeRepairGitHubRunner(),
            "PRO-65",
        )
        decision = decide_parent_action(snapshot)

        self.assertEqual(decision.kind, "block_parent")
        self.assertNotEqual(decision.kind, "create_gate_stage")

    def test_parent_authorization_comment_is_reread_from_the_parent_scoped_thread(self):
        runner = FakeParentRunner()
        comment_uuid = "00000000-0000-4000-8000-000000000061"
        content = json.dumps(
            {"bundle_digest": "a" * 64, "granted_round": 3},
            sort_keys=True,
            separators=(",", ":"),
        )
        runner.metadata["PRO-35"][
            "eventra.workflow.repair_authorization_comment"
        ] = comment_uuid
        runner.comment_records = [
            {
                "id": comment_uuid,
                "author_type": "member",
                "content": content,
            }
        ]

        snapshot = load_parent_snapshot(runner, FakeGitHubRunner(), "PRO-35")

        self.assertEqual(snapshot.authorization_comment_uuid, comment_uuid)
        self.assertEqual(snapshot.authorizing_comment.author_type, "member")
        self.assertIn(
            (
                "issue", "comment", "list", "PRO-35", "--thread",
                comment_uuid, "--full", "--compact", "--output", "json",
            ),
            runner.calls,
        )

    def test_missing_or_wrong_parent_authorization_comment_fails_closed(self):
        runner = FakeParentRunner()
        comment_uuid = "00000000-0000-4000-8000-000000000061"
        runner.metadata["PRO-35"][
            "eventra.workflow.repair_authorization_comment"
        ] = comment_uuid
        runner.comment_records = [
            {
                "id": "00000000-0000-4000-8000-000000000099",
                "author_type": "member",
                "content": "{}",
            }
        ]

        with self.assertRaisesRegex(RuntimeError, "malformed authorizing comment"):
            load_parent_snapshot(runner, FakeGitHubRunner(), "PRO-35")

    def test_persisted_version_two_phase_ownership_is_semantically_validated(self):
        comment_uuid = "00000000-0000-4000-8000-000000000051"
        evidence_url = f"https://multica.example/comments/{comment_uuid}"
        base = {
            "eventra.workflow.version": "2",
            "eventra.phase.kind": "review",
            "eventra.phase.result": "fail",
            "eventra.phase.attempt": "0",
            "eventra.phase.evidence_comment": comment_uuid,
            "eventra.phase.evidence_comment_url": evidence_url,
            "eventra.phase.failure_repositories": '["frontend"]',
            "eventra.phase.sha.frontend": FRONTEND_SHA,
        }
        cases = {
            "PASS gate with owners": {
                **base,
                "eventra.phase.result": "pass",
                "eventra.phase.evidence_comment_url": None,
            },
            "non-PASS gate without owners": {
                **base,
                "eventra.phase.failure_repositories": "[]",
            },
            "repository gate with a foreign owner": {
                **base,
                "eventra.phase.failure_repositories": '["backend"]',
            },
            "non-gate with owners": {
                **base,
                "eventra.phase.kind": "implementation",
                "eventra.phase.evidence_comment_url": None,
            },
        }
        for label, metadata in cases.items():
            with self.subTest(label=label):
                runner = FakeParentRunner()
                runner.metadata["PRO-36"] = {
                    key: value for key, value in metadata.items() if value is not None
                }
                github = FakeGitHubRunner()

                with self.assertRaisesRegex(
                    RuntimeError, "malformed child phase metadata"
                ):
                    load_parent_snapshot(runner, github, "PRO-35")

                self.assertEqual(github.calls, [])

    def test_completed_version_one_parent_is_readable_but_not_plannable(self):
        runner = FakeParentRunner()
        runner.parent["status"] = "done"
        runner.metadata["PRO-35"]["eventra.workflow.version"] = "1"
        legacy = dict(runner.metadata["PRO-36"])
        legacy["eventra.workflow.version"] = "1"
        legacy.pop("eventra.phase.failure_repositories")
        runner.metadata["PRO-36"] = legacy

        decision = decide_parent_action(
            load_parent_snapshot(runner, FakeGitHubRunner(), "PRO-35")
        )

        self.assertEqual(decision.kind, "noop")
        self.assertEqual(
            decision.reason,
            "terminal version 1 workflow is read-only",
        )
        self.assertFalse(any(call[:2] == ("issue", "status") for call in runner.calls))

    def test_nonterminal_version_one_parent_requires_explicit_migration(self):
        runner = FakeParentRunner()
        runner.metadata["PRO-35"]["eventra.workflow.version"] = "1"
        legacy = dict(runner.metadata["PRO-36"])
        legacy["eventra.workflow.version"] = "1"
        legacy.pop("eventra.phase.failure_repositories")
        runner.metadata["PRO-36"] = legacy

        decision = decide_parent_action(
            load_parent_snapshot(runner, FakeGitHubRunner(), "PRO-35")
        )

        self.assertEqual(decision.kind, "block_parent")
        self.assertEqual(
            decision.reason,
            "version 1 workflow requires explicit migration",
        )

    def test_load_parent_snapshot_reads_exact_metadata_and_current_pr_state(self):
        runner = FakeParentRunner()
        github = FakeGitHubRunner()
        snapshot = load_parent_snapshot(runner, github, "PRO-35")
        self.assertEqual(snapshot.classification, "frontend-only")
        self.assertEqual(snapshot.candidate_frontend_sha, FRONTEND_SHA)
        self.assertEqual(snapshot.children[0].kind, "implementation")
        self.assertEqual(snapshot.pull_requests[0].head_sha, FRONTEND_SHA)
        self.assertEqual(decide_parent_action(snapshot).kind, "create_gate_stage")

    def test_plan_parent_parser_is_read_only_and_prints_only_decision_fields(self):
        args = build_workflow_parser().parse_args(["plan-parent", "PRO-35"])
        self.assertEqual(args.command, "plan-parent")
        decision = decide_parent_action(
            load_parent_snapshot(FakeParentRunner(), FakeGitHubRunner(), "PRO-35")
        )
        output = io.StringIO()
        with redirect_stdout(output):
            print_parent_decision(decision)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["decision"], "create_gate_stage")
        self.assertEqual(
            set(payload),
            {"decision", "action_key", "reason", "failure_bundle"},
        )
        self.assertNotIn(FRONTEND_PR, output.getvalue())

    def test_malformed_parent_metadata_fails_closed_without_github_read(self):
        runner = FakeParentRunner()
        runner.metadata["PRO-35"]["eventra.workflow.attempt"] = "two"
        github = FakeGitHubRunner()
        with self.assertRaisesRegex(RuntimeError, "malformed parent workflow metadata"):
            load_parent_snapshot(runner, github, "PRO-35")
        self.assertEqual(github.calls, [])

    def test_empty_check_rollup_requires_clean_merge_state(self):
        github = FakeGitHubRunner()
        original_run = github.run

        def blocked(args):
            value = original_run(args)
            value["mergeStateStatus"] = "BLOCKED"
            return value

        github.run = blocked
        snapshot = load_parent_snapshot(FakeParentRunner(), github, "PRO-35")
        self.assertFalse(snapshot.pull_requests[0].checks_pass)


if __name__ == "__main__":
    unittest.main()
