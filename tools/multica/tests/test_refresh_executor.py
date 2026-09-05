"""Refresh admission must derive authority from complete, stable scoped reads."""

import copy
import hashlib
import importlib
import importlib.util
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
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
                               "eventra.phase.target": "repository:frontend", "eventra.phase.role": "frontend_engineer",
                               "eventra.phase.failure_repositories": "[]"}
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
            value = copy.deepcopy({"PRO-900": self.parent, "PRO-901": self.child}[args[2]])
            value["metadata"] = copy.deepcopy({"PRO-900": self.metadata,
                                               "PRO-901": self.child_metadata}[args[2]])
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
        self.baseline_snapshot = self.snapshot()
        self.request = contracts.freeze_refresh_request(self.baseline_snapshot)

    def snapshot(self):
        return self.api.snapshot("PRO-900")

    def admit(self):
        request, state, _, grant = self.progress()
        contracts.admit_refresh(request, state, grant)
        return state

    def progress(self, *, metadata_writes=6, comment_writes=2):
        request = self.request
        state = self.snapshot().state()
        envelope = {"payload": request.payload(), "digest": request.digest,
                    "staging_ref": request.staging_ref}
        request_record = comment_record(12, block("request", envelope), author=7, issue=uid(2))
        grant_record = comment_record(13, block("grant", {"schema_version": 1,
                                      "request_digest": request.digest, "granted_refresh": 1}),
                                      author=11, issue=uid(2))
        grant_record["author_type"] = "member"
        extras = [request_record, grant_record][:comment_writes]
        prefix = [
            ("eventra.refresh.request", contracts.canonical_json(envelope)),
            ("eventra.refresh.version", "1"),
            ("eventra.refresh.merge_permission", "hold"),
            ("eventra.refresh.request_digest", request.digest),
            ("eventra.refresh.request_comment", uid(12)),
            ("eventra.refresh.authorization_comment", uid(13)),
        ]
        def set_metadata(key, value):
            if state["metadata"].get(key) != value:
                state["metadata"][key] = value
                state["parent"]["revision"] += 1
        for key, value in prefix[:min(metadata_writes, 4)]:
            set_metadata(key, value)
        for record in extras:
            state["comments"].append(asdict(contracts.RefreshComment(
                uid(2), record["id"], record["author_id"], record["author_type"],
                record["revision"], record["content"])))
            state["parent"]["revision"] += 1
        for key, value in prefix[4:metadata_writes]:
            set_metadata(key, value)
        state["comment_manifest"] = contracts.comment_manifest(
            [*self.runner.comments["PRO-900"], *extras], uid(2))
        state["parent"]["metadata"] = copy.deepcopy(state["metadata"])
        snapshot = contracts.RefreshSnapshot(contracts.canonical_json(state))
        return request, snapshot, contracts.RefreshComment(uid(2), uid(12), uid(7), "agent", 1,
                                                           request_record["content"]), contracts.RefreshComment(
            uid(2), uid(13), uid(11), "member", 1, grant_record["content"])

    def test_complete_stable_reads_admit_without_writes(self):
        state = self.admit().state()
        self.assertEqual(state["parent"]["revision"], 15)
        self.assertEqual(state["children"][0]["evidence"]["content"], self.runner.evidence["content"])
        self.assertEqual(self.runner.writes + self.github.writes, [])
        self.assertGreaterEqual(sum(call[:3] == ("issue", "get", "PRO-900") for call in self.runner.calls), 2)

    def test_snapshot_carries_complete_parent_comment_manifest(self):
        state = self.snapshot().state()
        expected = contracts.comment_manifest(self.runner.comments["PRO-900"], uid(2))
        self.assertEqual(state["comment_manifest"], expected)
        self.assertNotIn("content", state["comment_manifest"][0])

    def test_trusted_freezer_derives_both_baselines_from_one_snapshot(self):
        snapshot = self.snapshot()
        request = contracts.freeze_refresh_request(snapshot)
        payload, state = request.payload(), snapshot.state()
        self.assertEqual(payload["baseline"]["authority_digest"], contracts.authority_digest(snapshot))
        self.assertEqual(payload["baseline"]["comments_digest"], hashlib.sha256(
            contracts.canonical_json(state["comment_manifest"]).encode()).hexdigest())
        self.assertEqual(payload["parent"]["revision"], 7)
        self.assertEqual(payload["source"]["evidence_uuid"], uid(4))
        self.assertEqual(payload["assignment"], {key: state["assignment"][key] for key in
                                                  ("project_id", "squad_id", "lead_id", "engineer_id")})

    def test_initial_progress_proves_exact_metadata_and_comment_writes(self):
        request, snapshot, request_comment, grant = self.progress()
        progress = contracts.validate_initial_refresh_progress(request, snapshot)
        self.assertEqual((progress.metadata_writes, progress.comment_writes), (6, 2))
        self.assertEqual(progress.request_comment, request_comment)
        self.assertEqual(progress.grant_comment, grant)
        self.assertEqual(snapshot.state()["parent"]["revision"], 15)
        contracts.admit_refresh(request, snapshot, grant)

    def test_initial_progress_same_value_metadata_replay_is_revision_noop(self):
        request, snapshot, _, _ = self.progress()
        state = snapshot.state()
        before = state["parent"]["revision"]
        def replay_same_value(key, value):
            if state["metadata"].get(key) != value:
                state["metadata"][key] = value
                state["parent"]["revision"] += 1
        replay_same_value("eventra.refresh.request_digest", request.digest)
        state["parent"]["metadata"] = copy.deepcopy(state["metadata"])
        self.assertEqual(state["parent"]["revision"], before)
        self.assertEqual(contracts.validate_initial_refresh_progress(
            request, contracts.RefreshSnapshot(contracts.canonical_json(state))).metadata_writes, 6)

    def test_every_legal_initial_prefix_is_recoverable(self):
        legal = ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0),
                 (4, 1), (4, 2), (5, 1), (5, 2), (6, 2))
        for metadata_writes, comment_writes in legal:
            request, snapshot, _, _ = self.progress(
                metadata_writes=metadata_writes, comment_writes=comment_writes)
            with self.subTest(metadata_writes=metadata_writes, comment_writes=comment_writes):
                progress = contracts.validate_initial_refresh_progress(request, snapshot)
                self.assertEqual((progress.metadata_writes, progress.comment_writes),
                                 (metadata_writes, comment_writes))

    def test_illegal_initial_prefix_combinations_are_rejected(self):
        for metadata_writes, comment_writes in ((3, 1), (5, 0), (6, 1)):
            request, snapshot, _, _ = self.progress(
                metadata_writes=metadata_writes, comment_writes=comment_writes)
            with self.subTest(metadata_writes=metadata_writes, comment_writes=comment_writes), \
                    self.assertRaises(ValueError):
                contracts.validate_initial_refresh_progress(request, snapshot)

    def test_initial_progress_rejects_extra_deleted_or_rebound_comments(self):
        request, snapshot, _, _ = self.progress()
        variants = []
        extra = snapshot.state()
        record = comment_record(90, "unrelated progress", author=7, issue=uid(2))
        extra["comments"].append(asdict(contracts.RefreshComment(
            uid(2), record["id"], record["author_id"], record["author_type"], 1, record["content"])))
        extra["comment_manifest"] = contracts.comment_manifest(
            [*self.runner.comments["PRO-900"],
             comment_record(12, next(item["content"] for item in extra["comments"] if item["comment_uuid"] == uid(12)),
                            author=7, issue=uid(2)),
             dict(comment_record(13, next(item["content"] for item in extra["comments"] if item["comment_uuid"] == uid(13)),
                                 author=11, issue=uid(2)), author_type="member"), record], uid(2))
        extra["parent"]["revision"] += 1
        variants.append(extra)
        deleted = snapshot.state()
        deleted["comments"] = [item for item in deleted["comments"] if item["comment_uuid"] != uid(10)]
        deleted["comment_manifest"] = [item for item in deleted["comment_manifest"] if item["comment_uuid"] != uid(10)]
        variants.append(deleted)
        edited = snapshot.state()
        old = next(item for item in edited["comments"] if item["comment_uuid"] == uid(10))
        old["content"] += " edited baseline"
        old_manifest = next(item for item in edited["comment_manifest"] if item["comment_uuid"] == uid(10))
        old_manifest["content_digest"] = hashlib.sha256(old["content"].encode()).hexdigest()
        variants.append(edited)
        rebound = snapshot.state()
        rebound["metadata"]["eventra.refresh.request_comment"] = uid(13)
        rebound["parent"]["metadata"] = copy.deepcopy(rebound["metadata"])
        variants.append(rebound)
        for index, state in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                contracts.validate_initial_refresh_progress(
                    request, contracts.RefreshSnapshot(contracts.canonical_json(state)))

    def test_initial_progress_rejects_equal_revision_with_different_authority(self):
        request, snapshot, _, _ = self.progress()
        for mutation in ("parent", "comment"):
            state = snapshot.state()
            if mutation == "parent":
                state["parent"]["title"] = "changed without a new revision"
            else:
                target = next(item for item in state["comments"] if item["comment_uuid"] == uid(12))
                target["content"] += " edited"
                manifest = next(item for item in state["comment_manifest"] if item["comment_uuid"] == uid(12))
                manifest["content_digest"] = hashlib.sha256(target["content"].encode()).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                contracts.validate_initial_refresh_progress(
                    request, contracts.RefreshSnapshot(contracts.canonical_json(state)))

    def test_initial_progress_rejects_prefix_gaps_and_grant_without_request(self):
        request, snapshot, _, _ = self.progress(metadata_writes=1, comment_writes=0)
        state = snapshot.state()
        value = state["metadata"].pop("eventra.refresh.request")
        state["metadata"]["eventra.refresh.version"] = "1"
        state["parent"]["metadata"] = copy.deepcopy(state["metadata"])
        with self.assertRaises(ValueError):
            contracts.validate_initial_refresh_progress(
                request, contracts.RefreshSnapshot(contracts.canonical_json(state)))
        request, snapshot, _, _ = self.progress(metadata_writes=4, comment_writes=2)
        state = snapshot.state()
        state["comments"] = [item for item in state["comments"] if item["comment_uuid"] != uid(12)]
        state["comment_manifest"] = [item for item in state["comment_manifest"] if item["comment_uuid"] != uid(12)]
        state["parent"]["revision"] -= 1
        with self.assertRaises(ValueError):
            contracts.validate_initial_refresh_progress(
                request, contracts.RefreshSnapshot(contracts.canonical_json(state)))

    def test_no_api_revision_is_not_assumed_immutable(self):
        record = dict(self.runner.grant)
        del record["revision"]
        with self.assertRaisesRegex(RuntimeError, "revision"):
            self.api.parse_scoped_comment([record], uid(10), uid(2))

    def test_comment_scope_author_revision_and_unknown_semantics_fail_closed(self):
        for change in ({"issue_id": uid(90)}, {"author_id": "member-1"}, {"revision": True},
                       {"created_at": "yesterday"},
                       {"content_truncated": True}, {"folded_count": 1}, {"unexpected": True}):
            with self.subTest(change=change), self.assertRaises((ValueError, RuntimeError)):
                self.api.parse_scoped_comment([dict(self.runner.grant, **change)], uid(10), uid(2))

    def test_duplicate_and_paginated_comment_envelopes_are_not_complete(self):
        for records in ([self.runner.grant] * 2, {"items": [self.runner.grant], "has_more": True}):
            with self.subTest(records=type(records)), self.assertRaises(RuntimeError):
                self.api.parse_scoped_comment(records, uid(10), uid(2))

    def test_grant_must_be_exactly_the_comment_in_snapshot(self):
        request, state, _, grant = self.progress()
        for field, value in (("content", grant.content + "\n"), ("revision", 2), ("issue_id", uid(3)),
                             ("author_type", "agent"), ("author_id", uid(90)), ("comment_uuid", uid(90))):
            with self.subTest(field=field), self.assertRaises(ValueError):
                contracts.admit_refresh(request, state, contracts.RefreshComment(**(asdict(grant) | {field: value})))

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

    def test_same_revision_content_or_parent_field_change_between_reads_blocks_freeze(self):
        original_title = self.runner.parent["title"]
        original_content = self.runner.comments["PRO-900"][0]["content"]
        comment_reads = 0
        def change_comment(args):
            nonlocal comment_reads
            if args[:4] == ["issue", "comment", "list", "PRO-900"]:
                comment_reads += 1
                if comment_reads == 2:
                    self.runner.comments["PRO-900"][0]["content"] += " same-revision edit"
        self.runner.mutate_on_read = change_comment
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.snapshot()
        self.runner.comments["PRO-900"][0]["content"] = original_content
        parent_reads = 0
        def change_parent(args):
            nonlocal parent_reads
            if args[:3] == ["issue", "get", "PRO-900"]:
                parent_reads += 1
                if parent_reads == 2:
                    self.runner.parent["title"] = original_title + " same-revision edit"
        self.runner.mutate_on_read = change_parent
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.snapshot()

    def test_control_identity_is_read_from_checkout_not_request(self):
        payload = self.request.payload()
        payload["control_tool_sha"] = "f" * 40
        with self.assertRaises(ValueError):
            _, state, _, grant = self.progress()
            contracts.admit_refresh(contracts.build_request(payload), state, grant)

    def test_unconfigured_scope_and_unapproved_checkout_fail_before_network(self):
        for scope in (None, self.module.RefreshScope("pro-1", uid(1), "f" * 40)):
            self.runner.calls.clear()
            with self.subTest(scope=scope), self.assertRaises(RuntimeError):
                self.module.RefreshAPI(self.runner, self.github, self.root, scope=scope,
                                       prerequisite_pr=self.runner.payload["prerequisite"]["pr_url"]).snapshot("PRO-900")
            self.assertEqual(self.runner.calls, [])

    def test_parent_executor_lock_is_nonblocking_and_parent_scoped(self):
        with self.api.parent_lock("PRO-900"):
            with self.assertRaisesRegex(RuntimeError, "lock is busy"):
                with self.api.parent_lock("PRO-900"):
                    self.fail("same parent lock unexpectedly re-entered")

    def test_snapshot_does_not_share_mutable_state(self):
        snapshot = self.snapshot()
        state = snapshot.state()
        state["parent"]["revision"] = 99
        self.assertEqual(snapshot.state()["parent"]["revision"], 7)


class MemoryRefreshAPI:
    """Task-owned write fake with the observed metadata revision semantics."""

    def __init__(self):
        from tools.multica.tests.test_candidate_refresh import refresh_snapshot_fixture
        self.request, self.state = refresh_snapshot_fixture(state="entry")
        self.writes, self.write_index = [], 0
        self.fail_at, self.fail_after = None, False
        self.fail_operation = None
        self.create_effects = 0
        self.complete_started_run = False

    def _mutate(self, operation, args, apply):
        self.write_index += 1
        if self.fail_at == self.write_index and not self.fail_after:
            raise RuntimeError("injected before effect")
        changed = apply()
        if changed:
            self.writes.append((operation, *args))
        if self.fail_at == self.write_index and self.fail_after:
            raise RuntimeError("injected after effect")
        return changed

    def parent_lock(self, parent):
        if parent != self.state["parent"]["identifier"]:
            raise RuntimeError("wrong parent lock")
        return nullcontext()

    def snapshot(self, parent):
        if parent != self.state["parent"]["identifier"]:
            raise RuntimeError("unknown parent")
        return contracts.RefreshSnapshot(contracts.canonical_json(copy.deepcopy(self.state)))

    def set_metadata(self, issue, key, value):
        if issue == self.state["parent"]["identifier"]:
            detail, metadata = self.state["parent"], self.state["metadata"]
        else:
            matches = [item for item in self.state["children"]
                       if item["detail"]["identifier"] == issue]
            if len(matches) != 1:
                raise RuntimeError("unknown issue")
            detail, metadata = matches[0]["detail"], matches[0]["metadata"]

        def apply():
            if metadata.get(key) == value:
                return False
            metadata[key] = value
            detail["metadata"] = copy.deepcopy(metadata)
            detail["revision"] += 1
            return True

        self._mutate("set_metadata", (issue, key, value), apply)

    def set_status(self, issue, status, *, start, position=None):
        if issue == self.state["parent"]["identifier"]:
            detail = self.state["parent"]
            if position != detail["position"] or start:
                raise RuntimeError("parent position/start contract mismatch")
        else:
            matches = [item for item in self.state["children"]
                       if item["detail"]["identifier"] == issue]
            if len(matches) != 1:
                raise RuntimeError("unknown issue")
            detail = matches[0]["detail"]
            if position is not None:
                raise RuntimeError("child position must not be supplied")

        def apply():
            changed = detail["status"] != status
            if changed:
                detail["status"] = status
                detail["status_category"] = status
                detail["revision"] += 1
            if start:
                active = [run for run in self.state["runs"]
                          if run["issue_id"] == detail["id"]
                          and run["status"] in {"queued", "dispatched", "running", "waiting_local_directory"}]
                if not active:
                    run = issue_run(
                        id=uid(91), issue_id=detail["id"], agent_id=detail["assignee_id"],
                        workspace_id=detail["workspace_id"], status="queued",
                        completed_at=None, started_at=None, dispatched_at=None,
                    )
                    if self.complete_started_run:
                        run["status"] = "completed"
                        run["completed_at"] = "2026-09-05T02:00:00Z"
                    self.state["runs"].append(run)
                    changed = True
            return changed

        self._mutate("set_status", (issue, status, start, position), apply)

    def publish_authorization(self):
        envelope = {"payload": self.request.payload(), "digest": self.request.digest,
                    "staging_ref": self.request.staging_ref}
        request_record = comment_record(12, block("request", envelope), author=7, issue=uid(2))
        grant_record = comment_record(13, block("grant", {"schema_version": 1,
                                      "request_digest": self.request.digest, "granted_refresh": 1}),
                                      author=11, issue=uid(2))
        grant_record["author_type"] = "member"
        for record in (request_record, grant_record):
            self.state["comments"].append(asdict(contracts.RefreshComment(
                uid(2), record["id"], record["author_id"], record["author_type"],
                record["revision"], record["content"])))
            self.state["parent"]["revision"] += 1
        self.state["comment_manifest"] = contracts.comment_manifest(
            [request_record, grant_record], uid(2))
        self.state["parent"]["metadata"] = copy.deepcopy(self.state["metadata"])

    def create_child(self, *, parent, stage, title, project_id, assignee_id, description):
        if self.fail_operation == "create_child":
            raise RuntimeError("injected create before effect")
        if parent != "PRO-900" or stage != 2:
            raise RuntimeError("invalid child scope")
        child = issue_detail(id=uid(90), identifier="PRO-902", parent_issue_id=uid(2), stage=stage,
                             status="backlog", project_id=project_id, assignee_id=assignee_id,
                             workspace_id=uid(1), description=description, title=title, revision=1)
        child["metadata"] = {}
        self.state["children"].append({"detail": child, "metadata": {}, "evidence": None})
        self.create_effects += 1
        self.writes.append(("create_child", parent, "PRO-902"))
        if self.fail_operation == "create_child_after":
            raise RuntimeError("injected create after effect")
        return "PRO-902"


class StageRequestTests(unittest.TestCase):
    def test_stage_request_writes_exact_four_key_prefix_and_is_idempotent(self):
        api = MemoryRefreshAPI()
        result = self.module().stage_refresh_request(api, "PRO-900", api.request)
        self.assertEqual((result.status, result.mutation_count, result.child_identifier),
                         ("request_staged", 4, ""))
        self.assertEqual([write[2] for write in api.writes], [
            "eventra.refresh.request", "eventra.refresh.version",
            "eventra.refresh.merge_permission", "eventra.refresh.request_digest"])
        progress = contracts.validate_initial_refresh_progress(api.request, api.snapshot("PRO-900"))
        self.assertEqual((progress.metadata_writes, progress.comment_writes), (4, 0))
        replay = self.module().stage_refresh_request(api, "PRO-900", api.request)
        self.assertEqual((replay.status, replay.mutation_count, len(api.writes)),
                         ("request_staged", 0, 4))

    def test_stage_request_recovers_every_metadata_failure_boundary(self):
        for fail_at in range(1, 5):
            for fail_after in (False, True):
                api = MemoryRefreshAPI()
                api.fail_at, api.fail_after = fail_at, fail_after
                with self.subTest(fail_at=fail_at, fail_after=fail_after):
                    if fail_after:
                        self.module().stage_refresh_request(api, "PRO-900", api.request)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "before effect"):
                            self.module().stage_refresh_request(api, "PRO-900", api.request)
                        api.fail_at = None
                        self.module().stage_refresh_request(api, "PRO-900", api.request)
                    progress = contracts.validate_initial_refresh_progress(
                        api.request, api.snapshot("PRO-900"))
                    self.assertEqual((progress.metadata_writes, progress.comment_writes), (4, 0))
                    self.assertEqual(len(api.writes), 4)
                    self.assertEqual(api.state["parent"]["revision"], 11)

    def test_stage_request_never_overwrites_conflict_or_early_comment(self):
        api = MemoryRefreshAPI()
        api.state["metadata"]["eventra.refresh.request"] = "different"
        api.state["parent"]["metadata"] = copy.deepcopy(api.state["metadata"])
        api.state["parent"]["revision"] += 1
        with self.assertRaises(ValueError):
            self.module().stage_refresh_request(api, "PRO-900", api.request)
        self.assertEqual(api.writes, [])

        api = MemoryRefreshAPI()
        envelope = {"payload": api.request.payload(), "digest": api.request.digest,
                    "staging_ref": api.request.staging_ref}
        record = comment_record(12, block("request", envelope), author=7, issue=uid(2))
        api.state["comments"].append(asdict(contracts.RefreshComment(
            uid(2), uid(12), uid(7), "agent", 1, record["content"])))
        api.state["comment_manifest"] = contracts.comment_manifest([record], uid(2))
        api.state["parent"]["revision"] += 1
        with self.assertRaises(ValueError):
            self.module().stage_refresh_request(api, "PRO-900", api.request)
        self.assertEqual(api.writes, [])

    @staticmethod
    def module():
        return importlib.import_module("tools.multica.refresh_executor")


class ExecuteRefreshTests(unittest.TestCase):
    def test_create_before_effect_failure_leaves_exact_reserved_checkpoint(self):
        api = MemoryRefreshAPI()
        module = importlib.import_module("tools.multica.refresh_executor")
        module.stage_refresh_request(api, "PRO-900", api.request)
        api.publish_authorization()
        api.writes.clear()
        source = copy.deepcopy(api.state["children"][0])
        api.fail_operation = "create_child"

        with self.assertRaisesRegex(RuntimeError, "create before effect"):
            module.execute_refresh(api, None, "PRO-900", uid(12), uid(13),
                                   contracts.refresh_action(api.request))

        feature = contracts.refresh_metadata(api.state["metadata"])
        self.assertEqual(feature["reservation"]["state"], "reserved")
        self.assertEqual(feature["request_comment"], uid(12))
        self.assertEqual(feature["authorization_comment"], uid(13))
        self.assertEqual(api.state["parent"]["revision"], 16)
        self.assertEqual(api.state["children"], [source])
        self.assertEqual([write[2] for write in api.writes], [
            "eventra.refresh.request_comment", "eventra.refresh.authorization_comment",
            "eventra.refresh.reservation"])

    def test_duplicate_create_ack_loss_recovers_same_child(self):
        api = MemoryRefreshAPI()
        module = importlib.import_module("tools.multica.refresh_executor")
        module.stage_refresh_request(api, "PRO-900", api.request)
        api.publish_authorization()
        source = copy.deepcopy(api.state["children"][0])
        api.fail_operation = "create_child_after"
        with self.assertRaisesRegex(RuntimeError, "create after effect"):
            module.execute_refresh(api, None, "PRO-900", uid(12), uid(13),
                                   contracts.refresh_action(api.request))
        self.assertEqual(contracts.plan_refresh(api.request, api.snapshot("PRO-900")).kind,
                         "resume_refresh")
        api.fail_operation = None

        result = module.execute_refresh(api, None, "PRO-900", uid(12), uid(13),
                                        contracts.refresh_action(api.request))

        self.assertEqual(result.child_identifier, "PRO-902")
        self.assertEqual(api.create_effects, 1)
        self.assertEqual(len(api.state["children"]), 2)
        self.assertEqual(api.state["children"][0], source)

    def test_child_is_fully_initialized_before_single_dispatch(self):
        api = MemoryRefreshAPI()
        module = importlib.import_module("tools.multica.refresh_executor")
        module.stage_refresh_request(api, "PRO-900", api.request)
        api.publish_authorization()
        api.writes.clear()

        result = module.execute_refresh(
            api, None, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(api.request),
        )

        action = contracts.refresh_action(api.request)
        child = api.state["children"][1]
        self.assertEqual(result.status, "child_dispatched")
        self.assertEqual(child["metadata"], {
            "eventra.workflow.version": "2",
            "eventra.phase.kind": "refresh",
            "eventra.phase.attempt": "0",
            "eventra.phase.target": "repository:frontend",
            "eventra.phase.role": "frontend_engineer",
            "eventra.phase.creation_action": action,
            "eventra.phase.pr": api.request.payload()["pr"]["url"],
            "eventra.refresh.version": "1",
            "eventra.refresh.request_digest": api.request.digest,
            "eventra.refresh.source_sha": api.request.payload()["source"]["sha"],
            "eventra.phase.sha.frontend": api.request.payload()["source"]["sha"],
        })
        self.assertEqual(api.state["metadata"]["eventra.workflow.next_stage"], "3")
        self.assertEqual(api.state["metadata"]["eventra.workflow.last_action"], action)
        self.assertEqual(api.state["parent"]["status"], "in_progress")
        parent_status = next(write for write in api.writes
                             if write[:3] == ("set_status", "PRO-900", "in_progress"))
        self.assertEqual(parent_status,
                         ("set_status", "PRO-900", "in_progress", False,
                          api.state["parent"]["position"]))
        reservation = contracts.refresh_metadata(api.state["metadata"])["reservation"]
        self.assertEqual(reservation["state"], "child_dispatched")
        self.assertEqual(reservation["child_id"], child["detail"]["id"])
        self.assertEqual(reservation["child_identifier"], "PRO-902")
        active = [run for run in api.state["runs"] if run["status"] in
                  {"queued", "dispatched", "running", "waiting_local_directory"}]
        self.assertEqual([(run["issue_id"], run["agent_id"]) for run in active],
                         [(child["detail"]["id"], api.request.payload()["assignment"]["engineer_id"])])
        start_index = next(index for index, write in enumerate(api.writes)
                           if write[:3] == ("set_status", "PRO-902", "todo"))
        self.assertTrue(all(write[0] == "set_metadata" for write in api.writes[4:start_index]
                            if write[1] == "PRO-902"))
        self.assertEqual(api.writes[start_index - 1][:3],
                         ("set_metadata", "PRO-900", "eventra.refresh.reservation"))
        self.assertEqual(api.writes[start_index + 1][:3],
                         ("set_metadata", "PRO-900", "eventra.refresh.reservation"))
        self.assertEqual(contracts.plan_refresh(api.request, api.snapshot("PRO-900")).kind,
                         "wait")

    def test_fast_completed_run_is_still_a_single_durable_dispatch(self):
        api = MemoryRefreshAPI()
        api.complete_started_run = True
        module = importlib.import_module("tools.multica.refresh_executor")
        module.stage_refresh_request(api, "PRO-900", api.request)
        api.publish_authorization()

        result = module.execute_refresh(
            api, None, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(api.request),
        )

        self.assertEqual(result.status, "child_dispatched")
        child_runs = [run for run in api.state["runs"]
                      if run["issue_id"] == api.state["children"][1]["detail"]["id"]]
        self.assertEqual([run["status"] for run in child_runs], ["completed"])
        replay = module.execute_refresh(
            api, None, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(api.request),
        )
        self.assertEqual((replay.status, replay.mutation_count),
                         ("child_dispatched", 0))

    def test_every_initialization_write_boundary_recovers_without_duplicate_dispatch(self):
        for fail_at in range(1, 18):
            for fail_after in (False, True):
                with self.subTest(fail_at=fail_at, fail_after=fail_after):
                    api = MemoryRefreshAPI()
                    module = importlib.import_module("tools.multica.refresh_executor")
                    module.stage_refresh_request(api, "PRO-900", api.request)
                    api.publish_authorization()
                    api.fail_operation = "create_child_after"
                    with self.assertRaisesRegex(RuntimeError, "create after effect"):
                        module.execute_refresh(
                            api, None, "PRO-900", uid(12), uid(13),
                            contracts.refresh_action(api.request),
                        )
                    api.fail_operation = None
                    api.writes.clear()
                    api.write_index = 0
                    api.fail_at, api.fail_after = fail_at, fail_after

                    if fail_after:
                        result = module.execute_refresh(
                            api, None, "PRO-900", uid(12), uid(13),
                            contracts.refresh_action(api.request),
                        )
                    else:
                        with self.assertRaisesRegex(RuntimeError, "before effect"):
                            module.execute_refresh(
                                api, None, "PRO-900", uid(12), uid(13),
                                contracts.refresh_action(api.request),
                            )
                        result = module.execute_refresh(
                            api, None, "PRO-900", uid(12), uid(13),
                            contracts.refresh_action(api.request),
                        )

                    self.assertEqual(result.status, "child_dispatched")
                    self.assertEqual(api.create_effects, 1)
                    active = [run for run in api.state["runs"]
                              if run["status"] in {"queued", "dispatched", "running", "waiting_local_directory"}]
                    self.assertEqual(len(active), 1)
                    self.assertEqual(len(api.writes), 17)
                    replay = module.execute_refresh(
                        api, None, "PRO-900", uid(12), uid(13),
                        contracts.refresh_action(api.request),
                    )
                    self.assertEqual((replay.status, replay.mutation_count),
                                     ("child_dispatched", 0))
                    self.assertEqual(len(api.writes), 17)

    def test_replay_rejects_unknown_child_authority_layout_drift_and_duplicate_run(self):
        mutations = (
            lambda api: api.state["children"][1]["metadata"].__setitem__(
                "eventra.phase.unbound", "forged"),
            lambda api: api.state["parent"].__setitem__("position", -999),
            lambda api: api.state["runs"].append(issue_run(
                id=uid(92), issue_id=api.state["children"][1]["detail"]["id"],
                agent_id=api.request.payload()["assignment"]["engineer_id"],
                workspace_id=uid(1), status="running", completed_at=None,
            )),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                api = MemoryRefreshAPI()
                module = importlib.import_module("tools.multica.refresh_executor")
                module.stage_refresh_request(api, "PRO-900", api.request)
                api.publish_authorization()
                module.execute_refresh(
                    api, None, "PRO-900", uid(12), uid(13),
                    contracts.refresh_action(api.request),
                )
                before = len(api.writes)
                mutate(api)
                child = api.state["children"][1]
                child["detail"]["metadata"] = copy.deepcopy(child["metadata"])

                with self.assertRaises(RuntimeError):
                    module.execute_refresh(
                        api, None, "PRO-900", uid(12), uid(13),
                        contracts.refresh_action(api.request),
                    )
                self.assertEqual(len(api.writes), before)


if __name__ == "__main__":
    unittest.main()
