"""Refresh admission must derive authority from complete, stable scoped reads."""

import copy
import hashlib
import importlib
import importlib.util
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from tools.multica import candidate_refresh as contracts
from tools.multica.blueprint import build_multi_repo_blueprint
from tools.multica.tests.test_candidate_refresh import uid, request_payload, block, encode
from tools.multica.tests.test_issue_contracts import issue_detail, issue_run


def comment_record(number, content, *, author=8, issue=None):
    result = {"id": uid(number), "author_id": uid(author), "author_type": "agent",
              "content": content, "revision": 1, "type": "comment",
              "created_at": "2026-09-04T01:00:00Z"}
    if issue is not None:
        result["issue_id"] = issue
    return result


class ReadBoundary:
    """Only observed CLI read shapes; unknown routes and all mutations fail."""

    def __init__(self, tool_sha, version):
        self.calls, self.writes = [], []
        self.payload = request_payload()
        self.payload.update(control_tool_sha=tool_sha, git_version=version)
        self.evidence = comment_record(4, "Original Stage 1 PASS for " + "b" * 40)
        self.payload["source"]["evidence_digest"] = hashlib.sha256(self.evidence["content"].encode()).hexdigest()
        self.request = contracts.build_request(self.payload)
        self.grant = comment_record(10, block("grant", {"schema_version": 1,
                                   "request_digest": self.request.digest, "granted_refresh": 1}), author=11)
        self.grant["author_type"] = "member"
        self.parent = issue_detail(id=uid(2), identifier="PRO-900", parent_issue_id=None,
                                  stage=None, assignee_id=uid(6), assignee_type="squad",
                                  project_id=uid(5), revision=7, status="blocked", workspace_id=uid(1))
        self.child = issue_detail(id=uid(3), identifier="PRO-901", parent_issue_id=uid(2),
                                 assignee_id=uid(8), project_id=uid(5), revision=8,
                                 status="done", workspace_id=uid(1))
        self.metadata = {"eventra.workflow.version": "2", "eventra.workflow.classification": "frontend-only",
                         "eventra.workflow.attempt": "0", "eventra.workflow.next_stage": "2",
                         "eventra.workflow.merge_state": "not_ready", "eventra.workflow.frontend_sha": "b" * 40,
                         "eventra.workflow.last_action": self.payload["parent"]["last_action"]}
        self.child_metadata = {"eventra.workflow.version": "2", "eventra.phase.kind": "implementation",
                               "eventra.phase.attempt": "0", "eventra.phase.result": "pass",
                               "eventra.phase.sha.frontend": "b" * 40, "eventra.phase.evidence_comment": uid(4),
                               "eventra.phase.pr": self.payload["pr"]["url"],
                               "eventra.phase.creation_action": self.payload["parent"]["last_action"],
                               "eventra.phase.target": "repository:frontend", "eventra.phase.role": "frontend_engineer"}
        self.comments = {"PRO-900": [self.grant], "PRO-901": [self.evidence]}
        self.runs = {"PRO-900": [], "PRO-901": []}
        self.children = [self.child]
        blueprint = build_multi_repo_blueprint("Eventra")
        self.role_ids = {agent.role: uid(20 + index) for index, agent in enumerate(blueprint.agents)}
        self.role_ids.update(delivery_lead=uid(7), frontend_engineer=uid(8))
        self.agents = [{"id": self.role_ids[a.role], "name": a.name} for a in blueprint.agents]
        self.projects = [{"id": uid(5), "title": "Eventra Local Development"},
                         {"id": uid(30), "title": "Eventra Backend Local Development"}]
        self.squad = {"id": uid(6), "name": "Eventra Local Delivery", "leader_id": uid(7),
                      "description": "Fixture", "instructions": "Fixture"}
        self.members = [{"id": uid(100 + index), "squad_id": uid(6), "member_id": agent_id,
                         "member_type": "agent", "role": "leader" if role == "delivery_lead" else role}
                        for index, (role, agent_id) in enumerate(self.role_ids.items())]
        self.mutate_on_read = None

    def run(self, args):
        self.calls.append(tuple(args))
        if self.mutate_on_read:
            self.mutate_on_read(args)
        scope = ["--profile", "pro-1", "--workspace-id", uid(1)]
        if args[-4:] != scope:
            raise AssertionError("missing explicit CLI scope")
        args = args[:-4]
        if args[-2:] != ["--output", "json"]:
            raise AssertionError("missing JSON boundary")
        args = args[:-2]
        if args[:2] == ["issue", "get"]:
            value = {"PRO-900": self.parent, "PRO-901": self.child}[args[2]]
        elif args[:3] == ["issue", "metadata", "list"]:
            value = {"PRO-900": self.metadata, "PRO-901": self.child_metadata}[args[3]]
        elif args == ["issue", "children", "PRO-900"]:
            stages = sorted({child["stage"] for child in self.children if child["stage"] is not None})
            value = {"total": len(self.children), "unstaged": [c for c in self.children if c["stage"] is None],
                     "stages": [{"stage": stage, "issues": [c for c in self.children if c["stage"] == stage],
                                 "total": sum(c["stage"] == stage for c in self.children),
                                 "done": sum(c["stage"] == stage and c["status"] == "done" for c in self.children)}
                                for stage in stages]}
        elif args[:2] == ["issue", "runs"]:
            value = self.runs[args[2]]
        elif args[:3] == ["issue", "comment", "list"] and args[4:] == ["--full", "--compact"]:
            value = self.comments[args[3]]
        elif args == ["agent", "list"]:
            value = self.agents
        elif args == ["project", "list"]:
            value = self.projects
        elif args == ["squad", "list"]:
            value = [self.squad]
        elif args == ["squad", "get", uid(6)]:
            value = self.squad
        elif args == ["squad", "member", "list", uid(6)]:
            value = self.members
        else:
            self.writes.append(tuple(args))
            raise AssertionError("unexpected read or mutation")
        return copy.deepcopy(value)


class GitHubBoundary:
    def __init__(self, payload):
        self.calls, self.writes = [], []
        self.payload = payload
        self.pr = {"html_url": payload["pr"]["url"], "state": "open", "merged": False,
                   "head": {"sha": "b" * 40, "ref": payload["pr"]["head_ref"],
                            "repo": {"full_name": "codeExploreHub/Eventra"}},
                   "base": {"sha": "d" * 40, "ref": "master", "repo": {"full_name": "codeExploreHub/Eventra"}}}
        self.prerequisite = copy.deepcopy(self.pr)
        self.prerequisite.update(html_url=payload["prerequisite"]["pr_url"], state="closed", merged=True,
                                 merge_commit_sha="d" * 40)
        self.base = {"ref": "refs/heads/master", "object": {"type": "commit", "sha": "d" * 40}}
        self.compare = {"status": "identical", "merge_base_commit": {"sha": "d" * 40}}

    def run(self, args):
        self.calls.append(tuple(args))
        routes = {"repos/codeExploreHub/Eventra/pulls/90": self.pr,
                  "repos/codeExploreHub/Eventra/pulls/91": self.prerequisite,
                  "repos/codeExploreHub/Eventra/git/ref/heads/master": self.base,
                  "repos/codeExploreHub/Eventra/compare/" + "d" * 40 + "..." + "d" * 40: self.compare}
        if len(args) != 4 or args[:3] != ["api", "--method", "GET"] or args[3] not in routes:
            self.writes.append(tuple(args))
            raise AssertionError("unexpected GitHub operation")
        return copy.deepcopy(routes[args[3]])


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        name = "tools.multica.refresh_executor"
        self.assertIsNotNone(importlib.util.find_spec(name), "refresh authority adapter not implemented")
        self.module = importlib.import_module(name)
        self.temp = tempfile.TemporaryDirectory(prefix="eventra-refresh-admission-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "--template=", str(self.root)], check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "control"],
                       cwd=self.root, check=True, capture_output=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        version = subprocess.check_output(["git", "version"], text=True).strip()
        self.runner = ReadBoundary(sha, version)
        self.github = GitHubBoundary(self.runner.payload)
        self.scope = self.module.RefreshScope("pro-1", uid(1), sha)
        self.api = self.module.RefreshAPI(self.runner, self.github, self.root, scope=self.scope,
                                         prerequisite_pr=self.runner.payload["prerequisite"]["pr_url"])

    def snapshot(self):
        return self.api.snapshot("PRO-900")

    def admit(self):
        state = self.snapshot()
        grant = self.api.comment("PRO-900", uid(10))
        contracts.admit_refresh(self.runner.request, state, grant)
        return state

    def test_complete_stable_reads_admit_without_writes(self):
        state = self.admit().state()
        self.assertEqual(state["parent"]["revision"], 7)
        self.assertEqual(state["children"][0]["evidence"]["content"], self.runner.evidence["content"])
        self.assertEqual(self.runner.writes + self.github.writes, [])
        self.assertGreaterEqual(sum(call[:3] == ("issue", "get", "PRO-900") for call in self.runner.calls), 2)

    def test_no_api_revision_is_not_assumed_immutable(self):
        record = dict(self.runner.grant)
        del record["revision"]
        with self.assertRaisesRegex(RuntimeError, "revision"):
            self.api.parse_scoped_comment([record], uid(10), uid(2))

    def test_comment_scope_author_revision_and_unknown_semantics_fail_closed(self):
        for change in ({"issue_id": uid(90)}, {"author_id": "member-1"}, {"revision": True},
                       {"content_truncated": True}, {"folded_count": 1}, {"unexpected": True}):
            with self.subTest(change=change), self.assertRaises((ValueError, RuntimeError)):
                self.api.parse_scoped_comment([dict(self.runner.grant, **change)], uid(10), uid(2))

    def test_duplicate_and_paginated_comment_envelopes_are_not_complete(self):
        for records in ([self.runner.grant] * 2, {"items": [self.runner.grant], "has_more": True}):
            with self.subTest(records=type(records)), self.assertRaises(RuntimeError):
                self.api.parse_scoped_comment(records, uid(10), uid(2))

    def test_grant_must_be_exactly_the_comment_in_snapshot(self):
        state = self.snapshot()
        grant = self.api.comment("PRO-900", uid(10))
        for field, value in (("content", grant.content + "\n"), ("revision", 2), ("issue_id", uid(3)),
                             ("author_type", "agent"), ("author_id", uid(90)), ("comment_uuid", uid(90))):
            with self.subTest(field=field), self.assertRaises(ValueError):
                contracts.admit_refresh(self.runner.request, state, contracts.RefreshComment(**(asdict(grant) | {field: value})))

    def test_ambiguous_matching_grants_are_rejected(self):
        self.runner.comments["PRO-900"].append(dict(self.runner.grant, id=uid(90)))
        with self.assertRaises(ValueError):
            self.admit()

    def test_business_revision_drift_is_never_accepted_as_greater_than(self):
        self.runner.parent["revision"] = 8
        with self.assertRaises(ValueError):
            self.admit()

    def test_every_entry_precondition_is_checked(self):
        changes = [("eventra.workflow.version", "1"), ("eventra.workflow.classification", "cross-stack"),
                   ("eventra.workflow.attempt", "1"), ("eventra.workflow.next_stage", "3"),
                   ("eventra.workflow.frontend_sha", "f" * 40), ("eventra.workflow.backend_sha", "f" * 40),
                   ("eventra.workflow.merge_state", "ready"), ("eventra.workflow.last_action", ""),
                   ("eventra.workflow.repair_reservation", "{}"), ("eventra.workflow.smoke_reservation", "{}"),
                   ("eventra.refresh.consumed", "{}"), ("eventra.refresh.generation", "1"),
                   ("eventra.refresh.reservation", "{}")]
        original = copy.deepcopy(self.runner.metadata)
        for key, value in changes:
            self.runner.metadata = original | {key: value}
            with self.subTest(key=key), self.assertRaises((ValueError, RuntimeError)):
                self.admit()
            self.assertEqual(self.runner.writes + self.github.writes, [])

    def test_source_identity_metadata_and_immutable_body_are_bound(self):
        for field, value in (("eventra.phase.kind", "repair"), ("eventra.phase.result", "fail"),
                             ("eventra.phase.sha.frontend", "f" * 40), ("eventra.phase.target", "repository:backend"),
                             ("eventra.phase.creation_action", "other"), ("eventra.phase.role", "integration_qa")):
            old = self.runner.child_metadata[field]
            self.runner.child_metadata[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.admit()
            self.runner.child_metadata[field] = old
        self.runner.evidence["content"] += " edited"
        with self.assertRaises(ValueError):
            self.admit()

    def test_additional_child_even_unstaged_blocks_entry(self):
        self.runner.children.append(issue_detail(id=uid(90), identifier="PRO-990", parent_issue_id=uid(2), stage=None))
        with self.assertRaises((ValueError, RuntimeError)):
            self.admit()

    def test_active_children_unknown_writer_and_multiple_leads_block_entry(self):
        for parent_runs, child_runs in (([], [issue_run(id=uid(90), issue_id=uid(3), agent_id=uid(8), workspace_id=uid(1), status="running")]),
                ([issue_run(id=uid(90), issue_id=uid(2), agent_id=uid(8), workspace_id=uid(1), status="running")], []),
                ([issue_run(id=uid(n), issue_id=uid(2), agent_id=uid(7), workspace_id=uid(1), status="running") for n in (90, 91)], [])):
            self.runner.runs = {"PRO-900": parent_runs, "PRO-901": child_runs}
            with self.subTest(runs=self.runner.runs), self.assertRaises((ValueError, RuntimeError)):
                self.admit()

    def test_assignment_mapping_is_not_copied_from_request(self):
        self.runner.squad["leader_id"] = uid(90)
        with self.assertRaises(RuntimeError):
            self.admit()

    def test_cross_workspace_parent_is_rejected(self):
        self.runner.parent["workspace_id"] = uid(90)
        with self.assertRaises(RuntimeError):
            self.snapshot()

    def test_pr_identity_head_and_prerequisite_ancestry_must_match(self):
        original = copy.deepcopy(self.github.pr)
        cases = [dict(original, html_url=self.runner.payload["prerequisite"]["pr_url"]),
                 dict(original, state="closed"), dict(original, merged=True)]
        for case in cases:
            self.github.pr = case
            with self.subTest(case=case), self.assertRaises((ValueError, RuntimeError)):
                self.admit()
        self.github.pr = original
        self.github.compare["status"] = "diverged"
        with self.assertRaises((ValueError, RuntimeError)):
            self.admit()

    def test_parent_or_comment_change_between_reads_blocks_freeze(self):
        reads = 0
        def mutate(args):
            nonlocal reads
            if args[:3] == ["issue", "get", "PRO-900"]:
                reads += 1
                if reads == 2:
                    self.runner.parent["revision"] += 1
        self.runner.mutate_on_read = mutate
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.snapshot()

    def test_control_identity_is_read_from_checkout_not_request(self):
        payload = self.runner.request.payload()
        payload["control_tool_sha"] = "f" * 40
        with self.assertRaises(ValueError):
            contracts.admit_refresh(contracts.build_request(payload), self.snapshot(), self.api.comment("PRO-900", uid(10)))

    def test_unconfigured_scope_and_unapproved_checkout_fail_before_network(self):
        for scope in (None, self.module.RefreshScope("pro-1", uid(1), "f" * 40)):
            self.runner.calls.clear()
            with self.subTest(scope=scope), self.assertRaises(RuntimeError):
                self.module.RefreshAPI(self.runner, self.github, self.root, scope=scope,
                                       prerequisite_pr=self.runner.payload["prerequisite"]["pr_url"]).snapshot("PRO-900")
            self.assertEqual(self.runner.calls, [])

    def test_snapshot_does_not_share_mutable_state(self):
        snapshot = self.snapshot()
        state = snapshot.state()
        state["parent"]["revision"] = 99
        self.assertEqual(snapshot.state()["parent"]["revision"], 7)


if __name__ == "__main__":
    unittest.main()
