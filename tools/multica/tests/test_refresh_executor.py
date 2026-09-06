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

    def add_pristine_gates(self):
        gate_action = ("2:PRO-900:create_gate_stage:0:frontend:" + "b" * 40
                       + ":-:next-stage:2")
        for number, role, suffix in (
                (14, "independent_reviewer", "review"),
                (15, "integration_qa", "QA")):
            identifier = f"PRO-{888 + number}"
            description = (
                f"## Exact-SHA scope\n\n- Parent: PRO-900\n- Role: {role}\n"
                f"- Candidate SHA: `{'b' * 40}`\n"
                f"- Managed PR: `{self.payload['pr']['url']}`\n"
                f"- Stage action: `{gate_action}`\n"
                f"- Implementation evidence: comment `{uid(4)}`"
            )
            child = issue_detail(
                id=uid(number), identifier=identifier, parent_issue_id=uid(2),
                stage=2, assignee_id=self.role_ids[role], project_id=uid(5),
                revision=1, status="backlog", status_category="backlog",
                workspace_id=uid(1), title=f"PRO-900 frontend {suffix}",
                description=description,
            )
            self.children.append(child)
            self.comments[identifier] = []
            self.runs[identifier] = []

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
            if args[2] == "PRO-900":
                value, metadata = self.parent, self.metadata
            else:
                matches = [child for child in self.children
                           if child["identifier"] == args[2]]
                if len(matches) != 1:
                    raise AssertionError("unknown issue fixture")
                value = matches[0]
                metadata = self.child_metadata if args[2] == "PRO-901" else {}
            value = copy.deepcopy(value)
            value["metadata"] = copy.deepcopy(metadata)
        elif args[:3] == ["issue", "metadata", "list"]:
            value = (self.metadata if args[3] == "PRO-900" else
                     self.child_metadata if args[3] == "PRO-901" else {})
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

    def test_scope_accepts_safe_dotted_cli_profile(self):
        self.api.scope = self.module.RefreshScope(
            "desktop-api.multica.ai", uid(1), self.scope.approved_control_sha)

        self.api._scope()

    def test_cancel_gate_emits_only_scoped_cancel_without_start(self):
        class MutationBoundary:
            def __init__(self):
                self.calls = []

            def run(self, args):
                self.calls.append(tuple(args))
                return {"identifier": "PRO-902", "status": "cancelled"}

        runner = MutationBoundary()
        api = self.module.RefreshAPI(
            runner, None, self.root, scope=self.scope,
            prerequisite_pr=self.runner.payload["prerequisite"]["pr_url"],
        )

        api.cancel_gate("PRO-902")

        self.assertEqual(runner.calls, [(
            "issue", "status", "PRO-902", "cancelled", "--no-start",
            "--output", "json", "--profile", "pro-1", "--workspace-id", uid(1),
        )])

    def test_create_child_accepts_only_request_stage_domain(self):
        class MutationBoundary:
            def __init__(self):
                self.calls = []

            def run(self, args):
                self.calls.append(tuple(args))
                return {"identifier": "PRO-904"}

        runner = MutationBoundary()
        api = self.module.RefreshAPI(
            runner, None, self.root, scope=self.scope,
            prerequisite_pr=self.runner.payload["prerequisite"]["pr_url"],
        )
        api.create_child(
            parent="PRO-900", stage=3, title="PRO-900: prepare candidate refresh",
            project_id=uid(5), assignee_id=uid(8), description="{}",
        )
        before = len(runner.calls)

        with self.assertRaisesRegex(RuntimeError, "invalid refresh stage"):
            api.create_child(
                parent="PRO-900", stage=4, title="invalid",
                project_id=uid(5), assignee_id=uid(8), description="{}",
            )

        self.assertEqual(len(runner.calls), before)

    def test_v2_snapshot_binds_complete_pristine_gate_history(self):
        self.runner.add_pristine_gates()

        snapshot = self.snapshot()
        state = snapshot.state()
        gates = [child for child in state["children"]
                 if child["detail"]["stage"] == 2]

        self.assertEqual(len(gates), 2)
        self.assertEqual([gate["comment_manifest"] for gate in gates], [[], []])
        request = contracts.freeze_refresh_request(
            snapshot, supersede_pristine_gates=True)
        self.assertEqual(contracts.refresh_protocol(request), 2)

    def test_v2_snapshot_rejects_tampered_child_comment_manifest_without_writes(self):
        self.runner.add_pristine_gates()
        state = self.snapshot().state()
        source = next(child for child in state["children"]
                      if child["detail"]["stage"] == 1)
        source["comment_manifest"][0]["content_digest"] = "0" * 64

        with self.assertRaisesRegex(ValueError, "child comment manifest mismatch"):
            contracts.freeze_refresh_request(
                contracts.RefreshSnapshot(contracts.canonical_json(state)),
                supersede_pristine_gates=True)
        self.assertEqual(self.runner.writes, [])

    def test_v2_snapshot_rejects_run_not_bound_to_observed_issue_without_writes(self):
        self.runner.add_pristine_gates()
        state = self.snapshot().state()
        state["runs"].append({
            "id": uid(98), "issue_id": uid(99), "agent_id": uid(8),
            "status": "completed", "created_at": "2026-09-05T01:00:00Z",
            "activity_at": "2026-09-05T01:01:00Z",
        })

        with self.assertRaisesRegex(ValueError, "unbound refresh run"):
            contracts.freeze_refresh_request(
                contracts.RefreshSnapshot(contracts.canonical_json(state)),
                supersede_pristine_gates=True)
        self.assertEqual(self.runner.writes + self.github.writes, [])

    def test_v2_complete_stable_reads_admit_without_writes(self):
        self.runner.add_pristine_gates()
        frozen = self.snapshot()
        request = contracts.freeze_refresh_request(
            frozen, supersede_pristine_gates=True)
        state = frozen.state()
        envelope = {"payload": request.payload(), "digest": request.digest,
                    "staging_ref": request.staging_ref}
        request_record = comment_record(
            12, "```eventra-candidate-refresh-request-v2\n"
            + encode(envelope) + "\n```", author=7, issue=uid(2))
        grant_record = comment_record(
            13, "```eventra-candidate-refresh-grant-v2\n"
            + encode({"schema_version": 2, "request_digest": request.digest,
                      "granted_refresh": 1}) + "\n```",
            author=11, issue=uid(2))
        grant_record["author_type"] = "member"
        state["metadata"].update({
            "eventra.refresh.request": contracts.canonical_json(envelope),
            "eventra.refresh.version": "2",
            "eventra.refresh.merge_permission": "hold",
            "eventra.refresh.request_digest": request.digest,
            "eventra.refresh.request_comment": uid(12),
            "eventra.refresh.authorization_comment": uid(13),
        })
        state["comments"].extend([
            asdict(contracts.RefreshComment(
                uid(2), record["id"], record["author_id"],
                record["author_type"], record["revision"], record["content"]))
            for record in (request_record, grant_record)
        ])
        state["comment_manifest"] = contracts.comment_manifest(
            [*self.runner.comments["PRO-900"], request_record, grant_record], uid(2))
        state["parent"]["metadata"] = copy.deepcopy(state["metadata"])
        state["parent"]["revision"] += 8
        grant = contracts.RefreshComment(
            uid(2), uid(13), uid(11), "member", 1, grant_record["content"])

        admitted = contracts.RefreshSnapshot(contracts.canonical_json(state))
        contracts.admit_refresh(request, admitted, grant)
        decision = contracts.plan_refresh(request, admitted)
        self.assertEqual(
            (decision.kind, decision.action_key),
            ("create_refresh_stage", contracts.refresh_action(request)),
        )
        self.assertEqual(self.runner.writes + self.github.writes, [])

    def test_v2_freeze_rejects_every_nonpristine_gate_history_identity(self):
        from tools.multica.tests.test_candidate_refresh import pristine_gate_snapshot

        identities = (
            ("member", "comment", uid(11)),
            ("agent", "comment", uid(16)),
            ("agent", "system", uid(16)),
            ("system", "system", "00000000-0000-0000-0000-000000000000"),
            ("system", "progress_update", "00000000-0000-0000-0000-000000000000"),
        )
        for author_type, record_type, author_id in identities:
            state = pristine_gate_snapshot().state()
            gate = next(child for child in state["children"]
                        if child["detail"]["identifier"] == "PRO-902")
            gate["comment_manifest"] = [{
                "issue_id": gate["detail"]["id"], "comment_uuid": uid(70),
                "author_id": author_id, "author_type": author_type,
                "type": record_type, "revision": 1, "parent_id": None,
                "created_at": "2026-09-05T01:00:00Z",
                "content_digest": hashlib.sha256(b"history").hexdigest(),
            }]
            with self.subTest(identity=(author_type, record_type)), \
                    self.assertRaises(ValueError):
                contracts.freeze_refresh_request(
                    contracts.RefreshSnapshot(contracts.canonical_json(state)),
                    supersede_pristine_gates=True)
        self.assertEqual(self.runner.writes + self.github.writes, [])

    def test_v2_freeze_rejects_gate_runs_and_semantic_mutations(self):
        from tools.multica.tests.test_candidate_refresh import pristine_gate_snapshot
        base = pristine_gate_snapshot().state()
        gate = next(child for child in base["children"]
                    if child["detail"]["identifier"] == "PRO-902")
        variants = []
        for status in ("completed", "running"):
            state = copy.deepcopy(base)
            state["runs"].append({
                "id": uid(70 if status == "completed" else 71),
                "issue_id": gate["detail"]["id"], "agent_id": uid(16),
                "status": status, "created_at": "2026-09-05T01:00:00Z",
                "activity_at": "2026-09-05T01:01:00Z",
            })
            variants.append(("run-" + status, state))
        for field, value in (
                ("project_id", uid(30)), ("assignee_id", uid(17)),
                ("title", "wrong title"), ("description", "wrong description"),
                ("status", "todo"), ("revision", 2)):
            state = copy.deepcopy(base)
            state["children"][1]["detail"][field] = value
            variants.append((field, state))
        metadata = copy.deepcopy(base)
        metadata["children"][1]["metadata"] = {"custom": "value"}
        metadata["children"][1]["detail"]["metadata"] = {"custom": "value"}
        variants.append(("metadata", metadata))
        unknown = copy.deepcopy(base)
        unknown["children"][1]["detail"]["future_field"] = True
        variants.append(("unknown-field", unknown))
        evidence_state = copy.deepcopy(base)
        evidence_gate = evidence_state["children"][1]
        evidence_record = comment_record(
            72, "gate evidence", author=16, issue=evidence_gate["detail"]["id"])
        evidence_gate["metadata"] = {"eventra.phase.evidence_comment": uid(72)}
        evidence_gate["detail"]["metadata"] = copy.deepcopy(evidence_gate["metadata"])
        evidence_gate["evidence"] = asdict(contracts.RefreshComment(
            evidence_gate["detail"]["id"], uid(72), uid(16), "agent", 1,
            evidence_record["content"]))
        evidence_gate["comment_manifest"] = contracts.comment_manifest(
            [evidence_record], evidence_gate["detail"]["id"])
        variants.append(("evidence", evidence_state))

        for name, state in variants:
            with self.subTest(name=name), self.assertRaises(ValueError):
                contracts.freeze_refresh_request(
                    contracts.RefreshSnapshot(contracts.canonical_json(state)),
                    supersede_pristine_gates=True)
        self.assertEqual(self.runner.writes + self.github.writes, [])

    def test_v2_gate_paginated_or_malformed_comment_tree_fails_closed(self):
        for records in (
                {"items": [], "has_more": True},
                [dict(comment_record(70, "reply", author=16), parent_id=uid(71))]):
            self.runner.add_pristine_gates()
            self.runner.comments["PRO-902"] = records
            with self.subTest(records=type(records).__name__), \
                    self.assertRaises((ValueError, RuntimeError)):
                self.snapshot()
            self.assertEqual(self.runner.writes + self.github.writes, [])
            self.runner.children = self.runner.children[:1]
            for identifier in ("PRO-902", "PRO-903"):
                self.runner.comments.pop(identifier, None)
                self.runner.runs.pop(identifier, None)

    def test_v2_same_revision_gate_change_between_reads_blocks_freeze(self):
        self.runner.add_pristine_gates()
        gate_reads = 0

        def change_gate(args):
            nonlocal gate_reads
            if args[:3] == ["issue", "get", "PRO-902"]:
                gate_reads += 1
                if gate_reads == 2:
                    self.runner.children[1]["title"] += " changed"

        self.runner.mutate_on_read = change_gate
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.snapshot()
        self.assertEqual(self.runner.writes + self.github.writes, [])

    def test_scope_rejects_unsafe_dotted_cli_profiles(self):
        for profile in (".hidden", "desktop..ai", "desktop.", "desktop/ai"):
            with self.subTest(profile=profile):
                self.api.scope = self.module.RefreshScope(
                    profile, uid(1), self.scope.approved_control_sha)
                with self.assertRaisesRegex(RuntimeError, "invalid profile"):
                    self.api._scope()

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

    def test_system_history_is_bound_but_cannot_become_authorizing_comment(self):
        records = [
            {"id": uid(40), "author_id": "00000000-0000-0000-0000-000000000000",
             "author_type": "system", "type": "progress_update", "revision": 1,
             "created_at": "2026-09-04T01:00:00Z", "content": "progress"},
            {"id": uid(41), "parent_id": uid(40), "author_id": uid(7),
             "author_type": "agent", "type": "comment", "revision": 1,
             "created_at": "2026-09-04T01:01:00Z", "content": "reply"},
            {"id": uid(42), "author_id": "00000000-0000-0000-0000-000000000000",
             "author_type": "system", "type": "system", "revision": 1,
             "created_at": "2026-09-04T01:02:00Z", "content": "transition"},
            {"id": uid(43), "author_id": uid(8), "author_type": "agent",
             "type": "system", "revision": 1,
             "created_at": "2026-09-04T01:03:00Z", "content": "run marker"},
        ]

        comments = self.api._comments(records, uid(2))

        self.assertEqual([comment.comment_uuid for comment in comments], [uid(41)])
        self.assertEqual(
            [item["comment_uuid"] for item in contracts.comment_manifest(records, uid(2))],
            [uid(40), uid(41), uid(42), uid(43)],
        )

    def test_freezer_accepts_complete_bound_system_history(self):
        system_history = [
            {"id": uid(40), "author_id": "00000000-0000-0000-0000-000000000000",
             "author_type": "system", "type": "progress_update", "revision": 1,
             "created_at": "2026-09-04T01:00:00Z", "content": "progress"},
            {"id": uid(41), "parent_id": uid(40), "author_id": uid(7),
             "author_type": "agent", "type": "comment", "revision": 1,
             "created_at": "2026-09-04T01:01:00Z", "content": "reply"},
            {"id": uid(42), "author_id": "00000000-0000-0000-0000-000000000000",
             "author_type": "system", "type": "system", "revision": 1,
             "created_at": "2026-09-04T01:02:00Z", "content": "transition"},
            {"id": uid(43), "author_id": uid(8), "author_type": "agent",
             "type": "system", "revision": 1,
             "created_at": "2026-09-04T01:03:00Z", "content": "run marker"},
        ]
        self.runner.comments["PRO-900"] = [
            *system_history, *self.runner.comments["PRO-900"]]

        snapshot = self.snapshot()
        request = contracts.freeze_refresh_request(snapshot)

        manifest = snapshot.state()["comment_manifest"]
        self.assertEqual(
            request.payload()["baseline"]["comments_digest"],
            hashlib.sha256(contracts.canonical_json(manifest).encode()).hexdigest(),
        )
        self.assertEqual(
            {item["comment_uuid"] for item in manifest},
            {uid(10), uid(40), uid(41), uid(42), uid(43)},
        )

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

    def test_stale_managed_pr_base_sha_uses_current_base_ref_authority(self):
        self.github.pr["base"]["sha"] = "c" * 40

        state = self.snapshot().state()

        self.assertEqual(state["pr"]["base_ref"], "master")
        self.assertEqual(state["prerequisite"]["base_sha"], "d" * 40)
        self.assertEqual(state["prerequisite"]["ancestor_sha"], "d" * 40)

    def test_freezer_binds_observed_optional_status_name(self):
        self.runner.parent["status_name"] = ""
        self.runner.child["status_name"] = ""

        snapshot = self.snapshot()
        projection = contracts.authority_projection(snapshot)

        self.assertEqual(projection["parent"]["status_name"], "")
        self.assertEqual(projection["source"]["detail"]["status_name"], "")

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
        self.create_effects, self.cancel_effects = 0, 0
        self.complete_started_run = False
        self.issue_comments = {}
        self.issue_comment_records = {}

    def use_v2(self):
        from tools.multica.tests.test_candidate_refresh import pristine_gate_snapshot

        frozen = pristine_gate_snapshot()
        self.request = contracts.freeze_refresh_request(
            frozen, supersede_pristine_gates=True)
        self.state = frozen.state()

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

    def parent_for_child(self, child):
        matches = [item for item in self.state["children"]
                   if item["detail"]["identifier"] == child]
        if len(matches) != 1 or matches[0]["detail"]["parent_issue_id"] != self.state["parent"]["id"]:
            raise RuntimeError("unknown child")
        return self.state["parent"]["identifier"]

    def add_comment(self, issue, comment):
        child = next(item for item in self.state["children"]
                     if item["detail"]["identifier"] == issue)
        if comment.issue_id != child["detail"]["id"]:
            raise RuntimeError("wrong comment scope")
        self.issue_comments[(issue, comment.comment_uuid)] = comment
        self.issue_comment_records[(issue, comment.comment_uuid)] = {
            "id": comment.comment_uuid, "issue_id": comment.issue_id,
            "author_id": comment.author_id, "author_type": comment.author_type,
            "revision": comment.revision, "type": "comment",
            "created_at": "2026-09-05T01:00:00Z", "content": comment.content,
        }
        records = [record for (record_issue, _), record in self.issue_comment_records.items()
                   if record_issue == issue]
        child["comment_manifest"] = contracts.comment_manifest(
            records, child["detail"]["id"])

    def comment(self, issue, comment_uuid):
        try:
            return self.issue_comments[(issue, comment_uuid)]
        except KeyError:
            raise RuntimeError("missing scoped comment") from None

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
            if issue != self.state["parent"]["identifier"] and key == "eventra.phase.evidence_comment":
                matches[0]["evidence"] = asdict(self.comment(issue, value))
            return True

        self._mutate("set_metadata", (issue, key, value), apply)

    def delete_metadata(self, issue, key):
        if issue != self.state["parent"]["identifier"]:
            raise RuntimeError("metadata deletion is parent-scoped")

        def apply():
            if key not in self.state["metadata"]:
                return False
            del self.state["metadata"][key]
            self.state["parent"]["metadata"] = copy.deepcopy(self.state["metadata"])
            self.state["parent"]["revision"] += 1
            return True

        self._mutate("delete_metadata", (issue, key), apply)

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
            if position is not None and (start or position != detail["position"]):
                raise RuntimeError("child position contract mismatch")

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
            elif status == "done":
                for run in self.state["runs"]:
                    if run["issue_id"] == detail["id"] and run["status"] in {
                            "queued", "dispatched", "running", "waiting_local_directory"}:
                        run["status"] = "completed"
                        run["completed_at"] = "2026-09-05T02:30:00Z"
                        changed = True
            return changed

        self._mutate("set_status", (issue, status, start, position), apply)

    def cancel_gate(self, issue):
        payload = self.request.payload()
        allowed = {gate["identifier"] for gate in payload["supersession"]["gates"]}
        if issue not in allowed:
            raise RuntimeError("gate cancellation is not request-bound")
        gate = next(child for child in self.state["children"]
                    if child["detail"]["identifier"] == issue)

        def apply():
            detail = gate["detail"]
            if detail["status"] == "cancelled":
                return False
            detail["status"] = "cancelled"
            detail["status_category"] = "cancelled"
            detail["revision"] += 1
            self.cancel_effects += 1
            return True

        self._mutate("cancel_gate", (issue,), apply)

    def publish_authorization(self):
        protocol = contracts.refresh_protocol(self.request)
        envelope = {"payload": self.request.payload(), "digest": self.request.digest,
                    "staging_ref": self.request.staging_ref}
        request_record = comment_record(
            12, f"```eventra-candidate-refresh-request-v{protocol}\n"
            + encode(envelope) + "\n```", author=7, issue=uid(2))
        grant_record = comment_record(
            13, f"```eventra-candidate-refresh-grant-v{protocol}\n"
            + encode({"schema_version": protocol,
                      "request_digest": self.request.digest,
                      "granted_refresh": 1}) + "\n```",
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
        self.write_index += 1
        if self.fail_at == self.write_index and not self.fail_after:
            raise RuntimeError("injected before effect")
        expected_stage = self.request.payload()["refresh_stage"]
        if parent != "PRO-900" or stage != expected_stage:
            raise RuntimeError("invalid child scope")
        identifier = "PRO-902" if expected_stage == 2 else "PRO-904"
        child = issue_detail(id=uid(90), identifier=identifier, parent_issue_id=uid(2), stage=stage,
                             status="backlog", project_id=project_id, assignee_id=assignee_id,
                             workspace_id=uid(1), description=description, title=title, revision=1)
        child["metadata"] = {}
        self.state["children"].append({"detail": child, "metadata": {}, "evidence": None,
                                       "comment_manifest": []})
        self.create_effects += 1
        self.writes.append(("create_child", parent, identifier))
        if self.fail_at == self.write_index and self.fail_after:
            raise RuntimeError("injected after effect")
        if self.fail_operation == "create_child_after":
            raise RuntimeError("injected create after effect")
        return identifier


class StageRequestTests(unittest.TestCase):
    def test_v2_stage_request_persists_protocol_version_and_remains_idempotent(self):
        from tools.multica.tests.test_candidate_refresh import pristine_gate_snapshot

        api = MemoryRefreshAPI()
        snapshot = pristine_gate_snapshot()
        api.request = contracts.freeze_refresh_request(
            snapshot, supersede_pristine_gates=True)
        api.state = snapshot.state()

        result = self.module().stage_refresh_request(api, "PRO-900", api.request)

        self.assertEqual((result.status, result.mutation_count),
                         ("request_staged", 4))
        self.assertEqual(api.state["metadata"]["eventra.refresh.version"], "2")
        progress = contracts.validate_initial_refresh_progress(
            api.request, api.snapshot("PRO-900"))
        self.assertEqual((progress.metadata_writes, progress.comment_writes), (4, 0))
        decision = contracts.plan_refresh(api.request, api.snapshot("PRO-900"))
        self.assertEqual((decision.kind, decision.action_key), ("wait", None))
        replay = self.module().stage_refresh_request(api, "PRO-900", api.request)
        self.assertEqual((replay.status, replay.mutation_count),
                         ("request_staged", 0))

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


def admitted_v2_execution():
    """Reach granted v2 admission through the public staging contract."""
    api = MemoryRefreshAPI()
    api.use_v2()
    module = importlib.import_module("tools.multica.refresh_executor")
    module.stage_refresh_request(api, "PRO-900", api.request)
    api.publish_authorization()
    api.writes.clear()
    api.write_index = 0
    return api, MemoryRefreshGit(api.request, api), api.request


class ExecuteRefreshTests(unittest.TestCase):
    @staticmethod
    def v2_case():
        api, _, _ = admitted_v2_execution()
        module = importlib.import_module("tools.multica.refresh_executor")
        return api, module

    def test_v2_initializes_stage_three_only_after_both_gates_cancel(self):
        api, git, request = admitted_v2_execution()
        module = importlib.import_module("tools.multica.refresh_executor")
        source_before = encode(api.state["children"][0])
        pr_before = copy.deepcopy(api.state["pr"])

        result = module.execute_refresh(
            api, git, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(request),
        )

        self.assertEqual((result.status, result.child_identifier),
                         ("child_dispatched", "PRO-904"))
        self.assertEqual([write for write in api.writes if write[0] == "cancel_gate"], [
            ("cancel_gate", "PRO-902"), ("cancel_gate", "PRO-903"),
        ])
        self.assertEqual(api.cancel_effects, 2)
        refresh_children = [child for child in api.state["children"]
                            if child["metadata"].get("eventra.phase.kind") == "refresh"]
        self.assertEqual([child["detail"]["stage"] for child in refresh_children], [3])
        self.assertEqual(refresh_children[0]["metadata"]["eventra.refresh.version"], "2")
        self.assertEqual(api.state["metadata"]["eventra.workflow.next_stage"], "4")
        self.assertEqual(sum(write[0] == "set_status" and write[3] is True
                             for write in api.writes), 1)
        self.assertEqual(len(api.state["runs"]), 1)
        self.assertEqual(api.state["pr"], pr_before)
        self.assertEqual(encode(api.state["children"][0]), source_before)
        reservation = contracts.refresh_metadata(
            api.state["metadata"])["reservation"]
        self.assertEqual(reservation["state"], "child_dispatched")

    def test_v2_cancellation_failure_boundaries_recover_without_duplicate_effects(self):
        for fail_at in range(3, 8):
            for fail_after in (False, True):
                with self.subTest(fail_at=fail_at, fail_after=fail_after):
                    api, module = self.v2_case()
                    source_before = encode(api.state["children"][0])
                    pr_before = copy.deepcopy(api.state["pr"])
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
                        self.assertEqual(len(api.state["children"]), 3)
                        self.assertEqual(api.state["runs"], [])
                        self.assertEqual(api.state["pr"], pr_before)
                        self.assertEqual(encode(api.state["children"][0]), source_before)
                        api.fail_at = None
                        result = module.execute_refresh(
                            api, None, "PRO-900", uid(12), uid(13),
                            contracts.refresh_action(api.request),
                        )

                    self.assertEqual(result.status, "child_dispatched")
                    self.assertEqual(api.cancel_effects, 2)
                    self.assertEqual(len([write for write in api.writes
                                          if write[0] == "cancel_gate"]), 2)
                    self.assertEqual(len(api.state["children"]), 4)
                    self.assertEqual(len(api.state["runs"]), 1)
                    self.assertEqual(api.state["pr"], pr_before)
                    self.assertEqual(encode(api.state["children"][0]), source_before)

    def test_v2_every_initialization_boundary_recovers_one_stage_three_child_and_run(self):
        for fail_at in range(1, 19):
            for fail_after in (False, True):
                with self.subTest(fail_at=fail_at, fail_after=fail_after):
                    api, git, request = admitted_v2_execution()
                    module = importlib.import_module("tools.multica.refresh_executor")
                    source_before = encode(api.state["children"][0])
                    api.fail_operation = "create_child"
                    with self.assertRaisesRegex(RuntimeError, "create before effect"):
                        module.execute_refresh(
                            api, git, "PRO-900", uid(12), uid(13),
                            contracts.refresh_action(request),
                        )
                    api.fail_operation = None
                    self.assertEqual(contracts.refresh_metadata(
                        api.state["metadata"])["reservation"]["state"],
                        "gates_cancelled")
                    self.assertEqual(len(api.state["children"]), 3)
                    api.writes.clear()
                    api.write_index = 0
                    api.fail_at, api.fail_after = fail_at, fail_after

                    if fail_at == 1 or not fail_after:
                        with self.assertRaisesRegex(RuntimeError,
                                                    "before effect|after effect"):
                            module.execute_refresh(
                                api, git, "PRO-900", uid(12), uid(13),
                                contracts.refresh_action(request),
                            )
                        api.fail_at = None
                        result = module.execute_refresh(
                            api, git, "PRO-900", uid(12), uid(13),
                            contracts.refresh_action(request),
                        )
                    else:
                        result = module.execute_refresh(
                            api, git, "PRO-900", uid(12), uid(13),
                            contracts.refresh_action(request),
                        )

                    action = ("2:PRO-900:create_refresh_stage:0:frontend:"
                              + "b" * 40 + ":next-stage:3:refresh:2:"
                              + request.digest)
                    refresh_children = [item for item in api.state["children"]
                                        if item["metadata"].get(
                                            "eventra.phase.kind") == "refresh"]
                    self.assertEqual(result.status, "child_dispatched")
                    self.assertEqual(len(refresh_children), 1)
                    self.assertEqual(refresh_children[0]["detail"]["stage"], 3)
                    self.assertEqual(refresh_children[0]["metadata"][
                        "eventra.phase.creation_action"], action)
                    self.assertEqual(api.state["metadata"][
                        "eventra.workflow.next_stage"], "4")
                    self.assertEqual(api.create_effects, 1)
                    child_runs = [run for run in api.state["runs"]
                                  if run["issue_id"] ==
                                  refresh_children[0]["detail"]["id"]]
                    self.assertEqual(len(child_runs), 1)
                    self.assertEqual(len(api.writes), 18)
                    self.assertEqual(api.cancel_effects, 2)
                    self.assertEqual(encode(api.state["children"][0]), source_before)


    def test_v2_replay_blocks_source_drift_before_any_gate_cancellation(self):
        for name, mutate in (
                ("source-title", lambda api:
                 api.state["children"][0]["detail"].__setitem__("title", "changed")),
                ("source-run", lambda api: api.state["runs"].append(issue_run(
                    id=uid(94), issue_id=uid(3), agent_id=uid(8),
                    workspace_id=uid(1), status="running", completed_at=None,
                )))):
            with self.subTest(name=name):
                api, module = self.v2_case()
                api.fail_at = 4
                with self.assertRaisesRegex(RuntimeError, "before effect"):
                    module.execute_refresh(
                        api, None, "PRO-900", uid(12), uid(13),
                        contracts.refresh_action(api.request),
                    )
                api.fail_at = None
                mutate(api)
                before = len(api.writes)

                with self.assertRaises((ValueError, RuntimeError)):
                    module.execute_refresh(
                        api, None, "PRO-900", uid(12), uid(13),
                        contracts.refresh_action(api.request),
                    )

                self.assertEqual(len(api.writes), before)
                self.assertEqual(api.cancel_effects, 0)

    def test_v2_cancellation_never_rewrites_later_lifecycle_state_backward(self):
        api, module = self.v2_case()
        api.fail_operation = "create_child"
        with self.assertRaisesRegex(RuntimeError, "create before effect"):
            module.execute_refresh(
                api, None, "PRO-900", uid(12), uid(13),
                contracts.refresh_action(api.request),
            )
        api.fail_operation = None
        reservation = contracts.refresh_metadata(
            api.state["metadata"])["reservation"]
        reservation["state"] = "child_initialized"
        value = contracts.canonical_json(reservation)
        api.state["metadata"]["eventra.refresh.reservation"] = value
        api.state["parent"]["metadata"] = copy.deepcopy(api.state["metadata"])
        before = len(api.writes)

        with self.assertRaises((ValueError, RuntimeError)):
            module.execute_refresh(
                api, None, "PRO-900", uid(12), uid(13),
                contracts.refresh_action(api.request),
            )

        self.assertEqual(len(api.writes), before)
        self.assertEqual(contracts.refresh_metadata(
            api.state["metadata"])["reservation"]["state"], "child_initialized")

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
        self.assertEqual(child["detail"]["stage"], 2)
        self.assertEqual(
            action,
            "2:PRO-900:create_refresh_stage:0:frontend:" + "b" * 40
            + ":next-stage:2:refresh:1:" + api.request.digest,
        )
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


class MemoryRefreshGit:
    def __init__(self, request, api=None):
        self.request = request
        self.api = api
        self.target = "f" * 40
        self.tree = "1" * 40
        self.verify_calls = []
        self.fail_verify = False
        self.fail_after_push = False
        self.managed_push_effects = 0

    def verify_candidate(self, request, target_sha):
        if self.fail_verify:
            raise RuntimeError("staging candidate unavailable")
        if request != self.request or target_sha != self.target:
            raise RuntimeError("wrong candidate")
        self.verify_calls.append((request.digest, target_sha))
        return self.tree

    def publish_candidate(self, request, prepared):
        if self.api is None or request != self.request or prepared.target_sha != self.target:
            raise RuntimeError("wrong candidate publication")
        reservation = contracts.refresh_metadata(
            self.api.state["metadata"])["reservation"]
        if (reservation["state"] != "candidate_registered"
                or reservation["prepared"] != asdict(prepared)):
            raise RuntimeError("candidate was not registered before publication")
        head = self.api.state["pr"]["head_sha"]
        if head == prepared.target_sha:
            return False
        if head != prepared.source_sha:
            raise RuntimeError("managed head drift")
        self.api.state["pr"]["head_sha"] = prepared.target_sha
        self.managed_push_effects += 1
        if self.fail_after_push:
            raise RuntimeError("managed push acknowledgement lost")
        return True


class FinishRefreshTests(unittest.TestCase):
    @staticmethod
    def pass_case():
        from tools.multica.tests.test_candidate_refresh import prepared_payload

        api = MemoryRefreshAPI()
        module = importlib.import_module("tools.multica.refresh_executor")
        module.stage_refresh_request(api, "PRO-900", api.request)
        api.publish_authorization()
        module.execute_refresh(
            api, None, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(api.request),
        )
        child = api.state["children"][1]
        payload = prepared_payload(api.request)
        payload["child_id"] = child["detail"]["id"]
        evidence_uuid = uid(93)
        evidence = contracts.RefreshComment(
            child["detail"]["id"], evidence_uuid,
            api.request.payload()["assignment"]["engineer_id"], "agent", 1,
            "Preparation only; no PR publication.\n" + block("prepared", payload),
        )
        api.add_comment("PRO-902", evidence)
        git = MemoryRefreshGit(api.request)
        return api, module, git, child, evidence_uuid

    @staticmethod
    def nonpass_case(outcome):
        api = MemoryRefreshAPI()
        module = importlib.import_module("tools.multica.refresh_executor")
        module.stage_refresh_request(api, "PRO-900", api.request)
        api.publish_authorization()
        module.execute_refresh(
            api, None, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(api.request),
        )
        child = api.state["children"][1]
        payload = {
            "schema_version": 1,
            "request_digest": api.request.digest,
            "child_id": child["detail"]["id"],
            "source_sha": "b" * 40,
            "prerequisite_sha": "d" * 40,
            "result": outcome,
            "commands": {
                "build": {"argv": ["npm", "run", "build"], "exit_code": 1},
            },
            "reason": "required build could not complete",
        }
        evidence_uuid = uid(94)
        evidence = contracts.RefreshComment(
            child["detail"]["id"], evidence_uuid,
            api.request.payload()["assignment"]["engineer_id"], "agent", 1,
            "Preparation did not pass.\n" + block("outcome", payload),
        )
        api.add_comment("PRO-902", evidence)
        git = MemoryRefreshGit(api.request)
        return api, module, git, child, evidence_uuid

    @staticmethod
    def v2_nonpass_case(outcome):
        api, git, request = admitted_v2_execution()
        module = importlib.import_module("tools.multica.refresh_executor")
        module.execute_refresh(
            api, git, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(request),
        )
        child = next(item for item in api.state["children"]
                     if item["metadata"].get("eventra.phase.kind") == "refresh")
        payload = {
            "schema_version": 2,
            "request_digest": request.digest,
            "child_id": child["detail"]["id"],
            "source_sha": "b" * 40,
            "prerequisite_sha": "d" * 40,
            "result": outcome,
            "commands": {
                "build": {"argv": ["npm", "run", "build"], "exit_code": 1},
            },
            "reason": "required build could not complete",
        }
        evidence_uuid = uid(94)
        evidence = contracts.RefreshComment(
            child["detail"]["id"], evidence_uuid,
            request.payload()["assignment"]["engineer_id"], "agent", 1,
            "Preparation did not pass.\n"
            "```eventra-candidate-refresh-outcome-v2\n"
            + encode(payload) + "\n```",
        )
        api.add_comment(child["detail"]["identifier"], evidence)
        return api, module, git, child, evidence_uuid

    def test_v2_finish_accepts_only_stage_three_protocol_two_preparation(self):
        from tools.multica.tests.test_candidate_refresh import prepared_payload

        api, git, request = admitted_v2_execution()
        module = importlib.import_module("tools.multica.refresh_executor")
        module.execute_refresh(
            api, git, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(request),
        )
        child = next(item for item in api.state["children"]
                     if item["metadata"].get("eventra.phase.kind") == "refresh")
        payload = prepared_payload(request)
        payload["schema_version"] = 2
        payload["child_id"] = child["detail"]["id"]
        payload["context_receipt"]["task_id"] = child["detail"]["identifier"]
        evidence_uuid = uid(93)
        evidence = contracts.RefreshComment(
            child["detail"]["id"], evidence_uuid,
            request.payload()["assignment"]["engineer_id"], "agent", 1,
            "Preparation only; no PR publication.\n"
            "```eventra-candidate-refresh-prepared-v2\n"
            + encode(payload) + "\n```",
        )
        api.add_comment(child["detail"]["identifier"], evidence)

        result = module.finish_refresh(
            api, git, child["detail"]["identifier"], evidence_uuid, "pass")

        self.assertEqual((result.status, result.child_identifier),
                         ("prepared", "PRO-904"))
        self.assertEqual(child["detail"]["stage"], 3)
        self.assertEqual(child["detail"]["status"], "done")
        self.assertEqual(child["metadata"]["eventra.refresh.version"], "2")
        self.assertEqual(api.state["metadata"]["eventra.workflow.next_stage"], "4")

        wrong_stage = copy.deepcopy(api.state)
        wrong_child = next(item for item in wrong_stage["children"]
                           if item["metadata"].get("eventra.phase.kind") == "refresh")
        wrong_child["detail"]["stage"] = 2
        api.state = wrong_stage
        before = len(api.writes)
        with self.assertRaises((ValueError, RuntimeError)):
            module.finish_refresh(
                api, git, child["detail"]["identifier"], evidence_uuid, "pass")
        self.assertEqual(len(api.writes), before)

    def test_v1_finish_rejects_stage_three_without_writes(self):
        api, module, git, child, evidence_uuid = self.pass_case()
        child["detail"]["stage"] = 3
        before = len(api.writes)

        with self.assertRaises((ValueError, RuntimeError)):
            module.finish_refresh(
                api, git, child["detail"]["identifier"], evidence_uuid, "pass")

        self.assertEqual(len(api.writes), before)

    def test_v2_non_pass_outcomes_use_protocol_two_without_publication(self):
        for outcome in ("fail", "blocked"):
            with self.subTest(outcome=outcome):
                api, module, git, child, evidence_uuid = self.v2_nonpass_case(outcome)
                managed_before = api.state["pr"]["head_sha"]
                api.writes.clear()

                result = module.finish_refresh(
                    api, git, child["detail"]["identifier"], evidence_uuid, outcome)

                self.assertEqual((result.status, result.child_identifier),
                                 (outcome, "PRO-904"))
                self.assertEqual(child["detail"]["stage"], 3)
                self.assertEqual(child["detail"]["status"], "done")
                self.assertEqual(child["metadata"]["eventra.refresh.version"], "2")
                self.assertEqual(child["metadata"]["eventra.phase.result"], outcome)
                self.assertEqual(api.state["pr"]["head_sha"], managed_before)
                self.assertEqual(git.verify_calls, [])

    def test_v2_non_pass_recovers_every_write_boundary(self):
        for outcome in ("fail", "blocked"):
            for fail_at in range(1, 5):
                for fail_after in (False, True):
                    with self.subTest(outcome=outcome, fail_at=fail_at,
                                      fail_after=fail_after):
                        api, module, git, child, evidence_uuid = (
                            self.v2_nonpass_case(outcome))
                        api.writes.clear()
                        api.write_index = 0
                        api.fail_at, api.fail_after = fail_at, fail_after

                        if fail_after:
                            result = module.finish_refresh(
                                api, git, "PRO-904", evidence_uuid, outcome)
                        else:
                            with self.assertRaisesRegex(RuntimeError, "before effect"):
                                module.finish_refresh(
                                    api, git, "PRO-904", evidence_uuid, outcome)
                            result = module.finish_refresh(
                                api, git, "PRO-904", evidence_uuid, outcome)

                        self.assertEqual((result.status, len(api.writes)),
                                         (outcome, 4))
                        self.assertEqual(child["detail"]["status"], "done")
                        self.assertEqual(git.verify_calls, [])

    def test_prepared_pass_does_not_publish_managed_pr(self):
        api, module, git, child, evidence_uuid = self.pass_case()
        managed_before = api.state["pr"]["head_sha"]
        source_before = copy.deepcopy(api.state["children"][0])
        api.writes.clear()

        result = module.finish_refresh(
            api, git, "PRO-902", evidence_uuid, "pass",
        )

        self.assertEqual((result.status, result.child_identifier), ("prepared", "PRO-902"))
        self.assertEqual(api.state["pr"]["head_sha"], managed_before)
        self.assertEqual(api.state["metadata"]["eventra.workflow.frontend_sha"], "b" * 40)
        self.assertNotIn("eventra.refresh.adoption", api.state["metadata"])
        self.assertNotIn("eventra.refresh.consumed", api.state["metadata"])
        self.assertEqual(api.state["children"][0], source_before)
        self.assertEqual(child["metadata"]["eventra.phase.kind"], "refresh")
        self.assertEqual(child["metadata"]["eventra.phase.sha.frontend"], "f" * 40)
        self.assertEqual(child["metadata"]["eventra.phase.result"], "pass")
        self.assertEqual(child["metadata"]["eventra.phase.evidence_comment"], evidence_uuid)
        self.assertEqual(child["detail"]["status"], "done")
        self.assertEqual(git.verify_calls, [(api.request.digest, "f" * 40)])

    def test_finish_pass_recovers_every_write_boundary_and_replays_as_noop(self):
        for fail_at in range(1, 6):
            for fail_after in (False, True):
                with self.subTest(fail_at=fail_at, fail_after=fail_after):
                    api, module, git, child, evidence_uuid = self.pass_case()
                    api.writes.clear()
                    api.write_index = 0
                    api.fail_at, api.fail_after = fail_at, fail_after

                    if fail_after:
                        result = module.finish_refresh(
                            api, git, "PRO-902", evidence_uuid, "pass",
                        )
                    else:
                        with self.assertRaisesRegex(RuntimeError, "before effect"):
                            module.finish_refresh(
                                api, git, "PRO-902", evidence_uuid, "pass",
                            )
                        result = module.finish_refresh(
                            api, git, "PRO-902", evidence_uuid, "pass",
                        )

                    self.assertEqual(result.status, "prepared")
                    self.assertEqual(child["detail"]["status"], "done")
                    self.assertEqual(len(api.writes), 5)
                    replay = module.finish_refresh(
                        api, git, "PRO-902", evidence_uuid, "pass",
                    )
                    self.assertEqual((replay.status, replay.mutation_count),
                                     ("prepared", 0))
                    self.assertEqual(len(api.writes), 5)
                    git.fail_verify = True
                    replay = module.finish_refresh(
                        api, git, "PRO-902", evidence_uuid, "pass",
                    )
                    self.assertEqual((replay.status, replay.mutation_count),
                                     ("prepared", 0))
                    self.assertEqual(len(api.writes), 5)

    def test_finish_pass_rejects_unverified_or_changed_authority_without_writes(self):
        cases = {
            "missing staging proof": lambda api, git, child: setattr(git, "fail_verify", True),
            "candidate tree mismatch": lambda api, git, child: setattr(git, "tree", "3" * 40),
            "managed head drift": lambda api, git, child: api.state["pr"].__setitem__("head_sha", "a" * 40),
            "unknown child metadata": lambda api, git, child: child["metadata"].__setitem__(
                "eventra.phase.unbound", "forged"),
            "child position drift": lambda api, git, child: child["detail"].__setitem__("position", -999),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                api, module, git, child, evidence_uuid = self.pass_case()
                api.writes.clear()
                mutate(api, git, child)
                child["detail"]["metadata"] = copy.deepcopy(child["metadata"])

                with self.assertRaises((RuntimeError, ValueError)):
                    module.finish_refresh(
                        api, git, "PRO-902", evidence_uuid, "pass",
                    )
                self.assertEqual(api.writes, [])

    def test_wrong_or_rebound_prepared_evidence_never_completes_refresh(self):
        from dataclasses import replace

        changes = {
            "wrong author": lambda evidence: replace(evidence, author_id=uid(7)),
            "edited revision": lambda evidence: replace(evidence, revision=2),
            "wrong task receipt": lambda evidence: replace(
                evidence, content=evidence.content.replace('"task_id":"PRO-902"',
                                                           '"task_id":"PRO-903"')),
            "ordinary gate body": lambda evidence: replace(
                evidence, content="QA PASS for an unrelated normal phase"),
        }
        for label, mutate in changes.items():
            with self.subTest(label=label):
                api, module, git, _, evidence_uuid = self.pass_case()
                api.issue_comments[("PRO-902", evidence_uuid)] = mutate(
                    api.issue_comments[("PRO-902", evidence_uuid)])
                api.writes.clear()

                with self.assertRaises((RuntimeError, ValueError)):
                    module.finish_refresh(
                        api, git, "PRO-902", evidence_uuid, "pass",
                    )
                self.assertEqual(api.writes, [])

        api, module, git, child, evidence_uuid = self.pass_case()
        module.finish_refresh(api, git, "PRO-902", evidence_uuid, "pass")
        before = len(api.writes)
        replacement_uuid = uid(95)
        old = api.issue_comments[("PRO-902", evidence_uuid)]
        api.add_comment("PRO-902", replace(old, comment_uuid=replacement_uuid))
        with self.assertRaises((RuntimeError, ValueError)):
            module.finish_refresh(
                api, git, "PRO-902", replacement_uuid, "pass",
            )
        self.assertEqual(len(api.writes), before)
        self.assertEqual(child["metadata"]["eventra.phase.evidence_comment"], evidence_uuid)

    def test_non_pass_outcome_finishes_child_without_candidate_or_git_publication(self):
        for outcome in ("fail", "blocked"):
            with self.subTest(outcome=outcome):
                api, module, git, child, evidence_uuid = self.nonpass_case(outcome)
                managed_before = api.state["pr"]["head_sha"]
                api.writes.clear()

                result = module.finish_refresh(
                    api, git, "PRO-902", evidence_uuid, outcome,
                )

                self.assertEqual((result.status, result.child_identifier),
                                 (outcome, "PRO-902"))
                self.assertEqual(child["metadata"]["eventra.phase.sha.frontend"], "b" * 40)
                self.assertEqual(child["metadata"]["eventra.phase.result"], outcome)
                self.assertEqual(child["metadata"]["eventra.phase.evidence_comment"], evidence_uuid)
                self.assertEqual(child["metadata"]["eventra.phase.failure_repositories"],
                                 '["frontend"]')
                self.assertEqual(child["detail"]["status"], "done")
                self.assertEqual(api.state["pr"]["head_sha"], managed_before)
                self.assertEqual(git.verify_calls, [])
                writes = len(api.writes)
                replay = module.finish_refresh(
                    api, git, "PRO-902", evidence_uuid, outcome,
                )
                self.assertEqual((replay.status, replay.mutation_count),
                                 (outcome, 0))
                self.assertEqual(len(api.writes), writes)
                decision = contracts.plan_refresh(api.request, api.snapshot("PRO-900"))
                self.assertEqual(decision.kind, "block")

    def test_non_pass_recovers_every_write_boundary(self):
        for outcome in ("fail", "blocked"):
            for fail_at in range(1, 5):
                for fail_after in (False, True):
                    with self.subTest(outcome=outcome, fail_at=fail_at,
                                      fail_after=fail_after):
                        api, module, git, child, evidence_uuid = self.nonpass_case(outcome)
                        api.writes.clear()
                        api.write_index = 0
                        api.fail_at, api.fail_after = fail_at, fail_after

                        if fail_after:
                            result = module.finish_refresh(
                                api, git, "PRO-902", evidence_uuid, outcome,
                            )
                        else:
                            with self.assertRaisesRegex(RuntimeError, "before effect"):
                                module.finish_refresh(
                                    api, git, "PRO-902", evidence_uuid, outcome,
                                )
                            result = module.finish_refresh(
                                api, git, "PRO-902", evidence_uuid, outcome,
                            )

                        self.assertEqual((result.status, len(api.writes)),
                                         (outcome, 4))
                        self.assertEqual(child["detail"]["status"], "done")
                        self.assertEqual(git.verify_calls, [])


class PublishRefreshTests(unittest.TestCase):
    @staticmethod
    def prepared_case():
        api, module, verifier, child, evidence_uuid = FinishRefreshTests.pass_case()
        module.finish_refresh(api, verifier, "PRO-902", evidence_uuid, "pass")
        api.writes.clear()
        api.write_index = 0
        publisher = MemoryRefreshGit(api.request, api)
        return api, module, publisher, child, evidence_uuid

    def test_publish_ack_loss_does_not_duplicate_delivery(self):
        api, module, git, child, _ = self.prepared_case()
        source = copy.deepcopy(api.state["children"][0])
        evidence = copy.deepcopy(child["evidence"])
        git.fail_after_push = True

        with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
            module.execute_refresh(
                api, git, "PRO-900", uid(12), uid(13),
                contracts.refresh_action(api.request),
            )

        reservation = contracts.refresh_metadata(api.state["metadata"])["reservation"]
        self.assertEqual(reservation["state"], "candidate_registered")
        prepared = contracts.parse_prepared(
            contracts.RefreshComment(**child["evidence"]), api.request,
            child["detail"]["id"],
        )
        self.assertEqual(reservation["prepared"], asdict(prepared))
        self.assertEqual(api.state["pr"]["head_sha"], git.target)
        self.assertEqual(api.state["metadata"]["eventra.workflow.frontend_sha"], "b" * 40)
        self.assertEqual(git.managed_push_effects, 1)
        self.assertEqual(git.verify_calls,
                         [(api.request.digest, git.target)] * 2)
        git.fail_after_push = False

        result = module.execute_refresh(
            api, git, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(api.request),
        )

        self.assertEqual(result.status, "adopted")
        self.assertEqual(git.managed_push_effects, 1)
        self.assertEqual(api.create_effects, 1)
        self.assertEqual(len(api.state["children"]), 2)
        self.assertEqual(api.state["children"][0], source)
        self.assertEqual(child["evidence"], evidence)
        self.assertEqual(api.state["metadata"]["eventra.workflow.frontend_sha"], git.target)
        self.assertNotIn("eventra.refresh.reservation", api.state["metadata"])
        feature = contracts.refresh_metadata(api.state["metadata"])
        self.assertEqual(feature["consumed"], {
            "version": 1, "request_digest": api.request.digest,
            "authorization_uuid": uid(13), "child_id": child["detail"]["id"],
            "target_sha": git.target,
        })
        self.assertEqual(feature["adoption"]["evidence_uuid"], uid(93))
        self.assertEqual(feature["adoption"]["target_sha"], git.target)
        self.assertEqual(feature["merge_permission"], "hold")
        self.assertEqual(api.state["metadata"]["eventra.workflow.merge_state"],
                         "not_ready")
        self.assertEqual(api.state["metadata"]["eventra.workflow.next_stage"], "3")
        self.assertEqual(api.state["metadata"]["eventra.workflow.last_action"],
                         contracts.refresh_action(api.request))

        replay = module.execute_refresh(
            api, git, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(api.request),
        )
        self.assertEqual((replay.status, replay.mutation_count), ("adopted", 0))
        self.assertEqual(git.managed_push_effects, 1)

    def test_adoption_half_write_resumes_without_second_push(self):
        api, module, git, _, _ = self.prepared_case()
        api.fail_at = 4

        with self.assertRaisesRegex(RuntimeError, "before effect"):
            module.execute_refresh(
                api, git, "PRO-900", uid(12), uid(13),
                contracts.refresh_action(api.request),
            )

        feature = contracts.refresh_metadata(api.state["metadata"])
        self.assertEqual(feature["reservation"]["state"], "published")
        self.assertIn("adoption", feature)
        self.assertNotIn("consumed", feature)
        self.assertEqual(api.state["metadata"]["eventra.workflow.frontend_sha"],
                         "b" * 40)
        self.assertEqual(git.managed_push_effects, 1)

        result = module.execute_refresh(
            api, git, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(api.request),
        )

        self.assertEqual(result.status, "adopted")
        self.assertEqual(git.managed_push_effects, 1)
        self.assertNotIn("eventra.refresh.reservation", api.state["metadata"])

    def test_every_publication_write_boundary_converges_to_the_same_state(self):
        baseline_api, baseline_module, baseline_git, _, _ = self.prepared_case()
        baseline_module.execute_refresh(
            baseline_api, baseline_git, "PRO-900", uid(12), uid(13),
            contracts.refresh_action(baseline_api.request),
        )
        baseline = copy.deepcopy(baseline_api.state)
        self.assertEqual(len(baseline_api.writes), 7)

        for fail_at in range(1, 8):
            for fail_after in (False, True):
                with self.subTest(fail_at=fail_at, fail_after=fail_after):
                    api, module, git, child, _ = self.prepared_case()
                    source = copy.deepcopy(api.state["children"][0])
                    evidence = copy.deepcopy(child["evidence"])
                    api.fail_at, api.fail_after = fail_at, fail_after

                    if fail_after:
                        result = module.execute_refresh(
                            api, git, "PRO-900", uid(12), uid(13),
                            contracts.refresh_action(api.request),
                        )
                    else:
                        with self.assertRaisesRegex(RuntimeError, "before effect"):
                            module.execute_refresh(
                                api, git, "PRO-900", uid(12), uid(13),
                                contracts.refresh_action(api.request),
                            )
                        result = module.execute_refresh(
                            api, git, "PRO-900", uid(12), uid(13),
                            contracts.refresh_action(api.request),
                        )

                    self.assertEqual(result.status, "adopted")
                    self.assertEqual(api.state, baseline)
                    self.assertEqual(len(api.writes), 7)
                    self.assertEqual(api.state["children"][0], source)
                    self.assertEqual(child["evidence"], evidence)
                    self.assertEqual(git.managed_push_effects, 1)
                    replay = module.execute_refresh(
                        api, git, "PRO-900", uid(12), uid(13),
                        contracts.refresh_action(api.request),
                    )
                    self.assertEqual((replay.status, replay.mutation_count),
                                     ("adopted", 0))
                    self.assertEqual(git.managed_push_effects, 1)

    def test_publication_drift_fails_before_new_write_or_push(self):
        def edit_prepared(api, child):
            child["evidence"]["revision"] = 2

        def duplicate_child(api, child):
            duplicate = copy.deepcopy(child)
            duplicate["detail"]["id"] = uid(96)
            duplicate["detail"]["identifier"] = "PRO-906"
            api.state["children"].append(duplicate)

        cases = {
            "wrong grant": (lambda api, child: None, uid(99)),
            "source evidence drift": (
                lambda api, child: api.state["children"][0]["evidence"].__setitem__(
                    "content", "rewritten source PASS"), uid(13)),
            "base drift": (
                lambda api, child: api.state["prerequisite"].__setitem__(
                    "base_sha", "a" * 40), uid(13)),
            "control tool drift": (
                lambda api, child: api.state["tool"].__setitem__("sha", "a" * 40),
                uid(13)),
            "edited prepared": (edit_prepared, uid(13)),
            "duplicate child": (duplicate_child, uid(13)),
            "unregistered target head": (
                lambda api, child: api.state["pr"].__setitem__(
                    "head_sha", "f" * 40), uid(13)),
        }
        for label, (mutate, grant_uuid) in cases.items():
            with self.subTest(label=label):
                api, module, git, child, _ = self.prepared_case()
                mutate(api, child)
                before = len(api.writes)

                with self.assertRaises((RuntimeError, ValueError)):
                    module.execute_refresh(
                        api, git, "PRO-900", uid(12), grant_uuid,
                        contracts.refresh_action(api.request),
                    )

                self.assertEqual(len(api.writes), before)
                self.assertEqual(git.managed_push_effects, 0)

    def test_illegal_partial_adoption_is_not_recovered(self):
        api, module, git, _, _ = self.prepared_case()
        api.fail_at = 3
        with self.assertRaisesRegex(RuntimeError, "before effect"):
            module.execute_refresh(
                api, git, "PRO-900", uid(12), uid(13),
                contracts.refresh_action(api.request),
            )
        feature = contracts.refresh_metadata(api.state["metadata"])
        self.assertEqual(feature["reservation"]["state"], "published")
        consumed = encode({
            "version": 1, "request_digest": api.request.digest,
            "authorization_uuid": uid(13),
            "child_id": api.state["children"][1]["detail"]["id"],
            "target_sha": "f" * 40,
        })
        api.state["metadata"]["eventra.refresh.consumed"] = consumed
        api.state["parent"]["metadata"] = copy.deepcopy(api.state["metadata"])
        api.state["parent"]["revision"] += 1
        before = len(api.writes)

        with self.assertRaises(RuntimeError):
            module.execute_refresh(
                api, git, "PRO-900", uid(12), uid(13),
                contracts.refresh_action(api.request),
            )

        self.assertEqual(len(api.writes), before)
        self.assertEqual(git.managed_push_effects, 1)

    def test_final_reservation_delete_rejects_concurrent_parent_write(self):
        api, module, git, _, _ = self.prepared_case()
        original_delete = api.delete_metadata

        def delete_then_race(issue, key):
            original_delete(issue, key)
            record = comment_record(
                96, "unrelated concurrent parent comment", author=96,
                issue=api.state["parent"]["id"],
            )
            api.state["comments"].append(asdict(contracts.RefreshComment(
                record["issue_id"], record["id"], record["author_id"],
                record["author_type"], record["revision"], record["content"],
            )))
            api.state["comment_manifest"].append({
                "issue_id": record["issue_id"], "comment_uuid": record["id"],
                "author_id": record["author_id"],
                "author_type": record["author_type"], "type": "comment",
                "revision": record["revision"], "parent_id": None,
                "created_at": record["created_at"],
                "content_digest": hashlib.sha256(
                    record["content"].encode("utf-8")).hexdigest(),
            })
            api.state["comment_manifest"].sort(
                key=lambda item: item["comment_uuid"])
            api.state["parent"]["revision"] += 1

        api.delete_metadata = delete_then_race

        with self.assertRaisesRegex(RuntimeError, "deletion.*drift"):
            module.execute_refresh(
                api, git, "PRO-900", uid(12), uid(13),
                contracts.refresh_action(api.request),
            )

        self.assertEqual(git.managed_push_effects, 1)
        self.assertNotIn("eventra.refresh.reservation", api.state["metadata"])


class FullRefreshDeliveryTests(unittest.TestCase):
    @staticmethod
    def _retry_injected(api, call):
        try:
            return call()
        except RuntimeError:
            # The executor may deliberately translate an injected transport
            # failure into a closed authority error. One fresh retry is the
            # recovery contract; a genuine conflict will fail again.
            if api.fail_at is None or api.write_index < api.fail_at:
                raise
            return call()

    def _run_delivery(self, *, fail_at=None, fail_after=False):
        from tools.multica.tests.test_candidate_refresh import prepared_payload

        api = MemoryRefreshAPI()
        module = importlib.import_module("tools.multica.refresh_executor")
        api.fail_at, api.fail_after = fail_at, fail_after
        self._retry_injected(
            api,
            lambda: module.stage_refresh_request(api, "PRO-900", api.request)
        )
        api.publish_authorization()
        self._retry_injected(api, lambda: module.execute_refresh(
            api,
            None,
            "PRO-900",
            uid(12),
            uid(13),
            contracts.refresh_action(api.request),
        ))
        child = api.state["children"][1]
        payload = prepared_payload(api.request)
        payload["child_id"] = child["detail"]["id"]
        evidence_uuid = uid(93)
        api.add_comment(
            "PRO-902",
            contracts.RefreshComment(
                child["detail"]["id"],
                evidence_uuid,
                api.request.payload()["assignment"]["engineer_id"],
                "agent",
                1,
                "Preparation only; no PR publication.\n"
                + block("prepared", payload),
            ),
        )
        verifier = MemoryRefreshGit(api.request)
        self._retry_injected(api, lambda: module.finish_refresh(
            api, verifier, "PRO-902", evidence_uuid, "pass"
        ))
        publisher = MemoryRefreshGit(api.request, api)
        self._retry_injected(api, lambda: module.execute_refresh(
            api,
            publisher,
            "PRO-900",
            uid(12),
            uid(13),
            contracts.refresh_action(api.request),
        ))
        return api, publisher

    def test_every_recorded_write_boundary_converges_to_full_delivery(self):
        baseline_api, baseline_git = self._run_delivery()
        expected_state = copy.deepcopy(baseline_api.state)
        expected_writes = copy.deepcopy(baseline_api.writes)
        total_writes = baseline_api.write_index

        self.assertGreater(total_writes, 0)
        self.assertEqual(total_writes, len(expected_writes))
        self.assertEqual(baseline_git.managed_push_effects, 1)
        for fail_at in range(1, total_writes + 1):
            for fail_after in (False, True):
                with self.subTest(fail_at=fail_at, fail_after=fail_after):
                    api, git = self._run_delivery(
                        fail_at=fail_at,
                        fail_after=fail_after,
                    )
                    self.assertEqual(api.state, expected_state)
                    self.assertEqual(api.writes, expected_writes)
                    self.assertEqual(git.managed_push_effects, 1)

    def test_incompatible_external_write_blocks_without_followup_effect(self):
        api, module, git, child, _ = PublishRefreshTests.prepared_case()
        api.state["children"][0]["evidence"]["content"] = (
            "externally rewritten source evidence"
        )
        before_writes = copy.deepcopy(api.writes)

        with self.assertRaises((RuntimeError, ValueError)):
            module.execute_refresh(
                api,
                git,
                "PRO-900",
                uid(12),
                uid(13),
                contracts.refresh_action(api.request),
            )

        self.assertEqual(api.writes, before_writes)
        self.assertEqual(git.managed_push_effects, 0)
        self.assertEqual(child["detail"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
