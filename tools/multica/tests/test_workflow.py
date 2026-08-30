"""Behavior tests for deterministic Eventra Multica workflow transitions."""

import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace

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
    }
    value.update(overrides)
    return value


class FakeWorkflowRunner:
    """Stateful argv fake at the Multica process boundary."""

    def __init__(self):
        self.issue = raw_issue()
        self.metadata = {}
        self.parent_metadata = {"eventra.workflow.version": "2"}
        self.calls = []
        self.fail_metadata_key = None
        self.corrupt_metadata_key = None
        self.inject_metadata_after_sets = None
        self.freeze_status = False

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
            return copy.deepcopy(self.issue)
        if call == (
            "issue", "metadata", "list", "PRO-36", "--output", "json"
        ):
            value = copy.deepcopy(self.metadata)
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
        if call == (
            "issue", "metadata", "list", PARENT_ID, "--output", "json"
        ):
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
        self.assertEqual(runner.calls[-2][0:3], ("issue", "status", "PRO-36"))
        self.assertEqual(
            runner.metadata["eventra.phase.result"],
            "pass",
        )

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
):
    return PhaseSnapshot(
        issue_key=issue_key,
        stage=stage,
        kind=kind,
        result=result,
        attempt=attempt,
        status="done",
        frontend_sha=frontend_sha,
        backend_sha=backend_sha,
        evidence_comment=evidence_comment,
        responsible_repositories=responsible_repositories,
        evidence_comment_url=evidence_comment_url,
        project_id=project_id,
        pr_url=pr_url,
        assignee_id=assignee_id,
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
    }
    values.update(overrides)
    return ParentSnapshot(**values)


class ParentDecisionTests(unittest.TestCase):
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
                backend_sha=backend_sha,
                evidence_comment=review_uuid,
                responsible_repositories=("backend",),
                evidence_comment_url=f"https://multica.example/comments/{review_uuid}",
                project_id=frontend_project,
            ),
            phase(
                "PRO-63", 2, "qa",
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
                f"2:PRO-35:create_gate_stage:0:frontend:{FRONTEND_SHA}:-",
                "implementation evidence is ready for exact-SHA gates",
            ),
        )

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
        decision = decide_parent_action(parent_snapshot(children=children))
        self.assertEqual(decision.kind, "create_repair_stage")
        self.assertIn(":1:frontend:", decision.action_key)

    def test_cross_stack_repair_may_target_only_affected_repository(self):
        backend_sha = "b" * 40
        children = (
            phase("PRO-36", 1, "implementation"),
            phase(
                "PRO-37",
                1,
                "implementation",
                frontend_sha=None,
                backend_sha=backend_sha,
            ),
            phase("PRO-38", 2, "review", result="fail"),
            phase(
                "PRO-39",
                2,
                "review",
                frontend_sha=None,
                backend_sha=backend_sha,
            ),
            phase("PRO-40", 2, "qa", backend_sha=backend_sha),
            phase("PRO-41", 3, "repair", attempt=1),
        )
        decision = decide_parent_action(
            parent_snapshot(
                classification="cross-stack",
                attempt=1,
                candidate_backend_sha=backend_sha,
                children=children,
            )
        )
        self.assertEqual(decision.kind, "create_gate_stage")

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
            parent_snapshot(attempt=2, children=children)
        )
        self.assertEqual(decision.kind, "block_parent")
        self.assertNotIn("repair", decision.reason)

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
            "merge",
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
    values.update(overrides)
    return WorkflowSnapshot(**values)


class RecoveryDecisionTests(unittest.TestCase):
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
            "PRO-35": {"eventra.workflow.version": "2"},
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
        self.next_child_number = 80
        self.fail_once_parent_key = None
        self.fail_once_create = False
        self._add_done_child(1, "implementation", 0, result="pass", pr=True)
        if attempt >= 1:
            self._add_done_child(3, "repair", 1, result="pass", pr=True)
        if attempt >= 2:
            self._add_done_child(5, "repair", 2, result="pass", pr=True)
        gate_stage = {0: 2, 1: 4, 2: 6}[attempt]
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
            if identifier == "PRO-65":
                return copy.deepcopy(self.parent)
            return copy.deepcopy(
                next(child for child in self.children if child["identifier"] == identifier)
            )
        if call == ("issue", "children", "PRO-65", "--output", "json"):
            return self._children_payload()
        if call[:3] == ("issue", "metadata", "list"):
            return copy.deepcopy(self.metadata[call[3]])
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
            )
            self.children.append(child)
            self.metadata[identifier] = {}
            return copy.deepcopy(child)
        if call[:3] == ("issue", "metadata", "set"):
            identifier = call[3]
            key = self._flag(args, "--key")
            value = self._flag(args, "--value")
            if identifier == "PRO-65" and key == self.fail_once_parent_key:
                self.fail_once_parent_key = None
                raise RuntimeError("injected parent metadata failure")
            self.metadata[identifier][key] = value
            return {"ok": True}
        if call[:3] == ("issue", "metadata", "delete"):
            identifier = call[3]
            key = self._flag(args, "--key")
            self.metadata[identifier].pop(key, None)
            return {"ok": True}
        if call[:2] == ("issue", "status"):
            identifier = call[2]
            status = call[3]
            child = next(child for child in self.children if child["identifier"] == identifier)
            child["status"] = status
            return copy.deepcopy(child)
        raise AssertionError(f"unsupported argv: {call!r}")


class RepairExecutionTests(unittest.TestCase):
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


class ParentSnapshotReadTests(unittest.TestCase):
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
