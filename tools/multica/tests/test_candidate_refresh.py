"""Refresh wire contracts: corruption must not become delivery authority."""

import copy
import hashlib
import importlib
import importlib.util
import json
import unittest
from dataclasses import replace


def refresh_snapshot_fixture(*, state="candidate_registered", adopted=False):
    """Explicit authority fixture, without production admission/planner helpers."""
    from tools.multica import candidate_refresh as c
    from tools.multica.tests.test_refresh_executor import ReadBoundary
    from tools.multica.tests.test_issue_contracts import issue_detail
    boundary = ReadBoundary("e" * 40, "git version 2.50.1")
    comment = lambda raw, issue: {"issue_id": issue, "comment_uuid": raw["id"], "author_id": raw["author_id"],
                                  "author_type": raw["author_type"], "revision": raw["revision"], "content": raw["content"]}
    def sync_echoes(data):
        data["parent"]["metadata"] = copy.deepcopy(data["metadata"])
        for child in data["children"]:
            child["detail"]["metadata"] = copy.deepcopy(child["metadata"])
    def finish(request, data):
        sync_echoes(data)
        return request, data
    data = {"parent": copy.deepcopy(boundary.parent), "metadata": copy.deepcopy(boundary.metadata),
            "children": [{"detail": copy.deepcopy(boundary.child), "metadata": copy.deepcopy(boundary.child_metadata),
                          "evidence": comment(boundary.evidence, uid(3))}], "runs": [],
            "comments": [], "comment_manifest": [],
            "pr": {**boundary.payload["pr"], "head_sha": "b" * 40, "state": "open", "merged": False},
            "prerequisite": {**boundary.payload["prerequisite"], "merged": True, "ancestor_sha": "d" * 40},
            "assignment": {**boundary.payload["assignment"], "workspace_id": uid(1), "roles": boundary.role_ids,
                           "projects": {"frontend": uid(5), "backend": uid(30)},
                           "members": sorted([{k: value[k] for k in ("member_id", "member_type", "role")}
                                              for value in boundary.members], key=lambda item: (item["role"], item["member_id"]))},
            "tool": {"sha": "e" * 40, "git_version": "git version 2.50.1"}}
    sync_echoes(data)
    request = c.freeze_refresh_request(c.RefreshSnapshot(c.canonical_json(data)))
    action = "2:PRO-900:create_refresh_stage:0:frontend:" + "b" * 40 + ":next-stage:2:refresh:1:" + request.digest
    envelope = {"payload": request.payload(), "digest": request.digest, "staging_ref": request.staging_ref}
    if state == "entry":
        return finish(request, data)
    request_record = {"id": uid(12), "issue_id": uid(2), "author_type": "agent", "author_id": uid(7),
                      "revision": 1, "type": "comment", "created_at": "2026-09-04T01:01:00Z",
                      "content": block("request", envelope)}
    grant_record = {"id": uid(10), "issue_id": uid(2), "author_type": "member", "author_id": uid(11),
                    "revision": 1, "type": "comment", "created_at": "2026-09-04T01:02:00Z",
                    "content": block("grant", {"schema_version": 1, "request_digest": request.digest,
                                                 "granted_refresh": 1})}
    data["metadata"].update({"eventra.refresh.version": "1", "eventra.refresh.request": encode(envelope),
                              "eventra.refresh.request_digest": request.digest, "eventra.refresh.merge_permission": "hold",
                              "eventra.refresh.request_comment": uid(12), "eventra.refresh.authorization_comment": uid(10)})
    data["comments"] = [comment(request_record, uid(2)), comment(grant_record, uid(2))]
    data["comment_manifest"] = c.comment_manifest([request_record, grant_record], uid(2))
    data["parent"]["revision"] = request.payload()["parent"]["revision"] + 8
    if state == "intent":
        del data["metadata"]["eventra.refresh.authorization_comment"]
        data["comments"] = data["comments"][:1]
        data["comment_manifest"] = c.comment_manifest([request_record], uid(2))
        data["parent"]["revision"] -= 2
        return finish(request, data)
    if state == "admitted":
        return finish(request, data)
    prepared_block = prepared_payload(request)
    prepared_comment = {"issue_id": uid(9), "comment_uuid": uid(13), "author_id": uid(8), "author_type": "agent",
                        "revision": 1, "content": block("prepared", prepared_block)}
    prepared = {"request_digest": request.digest, "child_id": uid(9), "source_sha": "b" * 40,
                "prerequisite_sha": "d" * 40, "target_sha": "f" * 40, "tree_sha": "1" * 40,
                "evidence_uuid": uid(13), "evidence_digest": hashlib.sha256(prepared_comment["content"].encode()).hexdigest(),
                "staging_ref": request.staging_ref}
    if state != "reserved":
        data["parent"]["status"] = "in_progress"
        data["parent"]["status_category"] = "in_progress"
        data["metadata"].update({"eventra.workflow.next_stage": "3", "eventra.workflow.last_action": action})
        metadata = {"eventra.workflow.version": "2", "eventra.phase.kind": "refresh", "eventra.phase.attempt": "0",
                    "eventra.phase.target": "repository:frontend", "eventra.phase.role": "frontend_engineer",
                    "eventra.phase.creation_action": action, "eventra.phase.pr": request.payload()["pr"]["url"],
                    "eventra.phase.sha.frontend": "f" * 40, "eventra.phase.result": "pass", "eventra.phase.evidence_comment": uid(13),
                    "eventra.phase.failure_repositories": "[]",
                    "eventra.refresh.version": "1", "eventra.refresh.request_digest": request.digest, "eventra.refresh.source_sha": "b" * 40}
        data["children"].append({"detail": issue_detail(id=uid(9), identifier="PRO-902", parent_issue_id=uid(2), stage=2,
                                          project_id=uid(5), assignee_id=uid(8), status="done", workspace_id=uid(1)),
                                  "metadata": metadata, "evidence": prepared_comment})
    if adopted:
        consumed = {"version": 1, "request_digest": request.digest, "authorization_uuid": uid(10),
                    "child_id": uid(9), "target_sha": "f" * 40}
        adoption = {**consumed, "source_sha": "b" * 40, "prerequisite_sha": "d" * 40, "evidence_uuid": uid(13),
                    "evidence_digest": prepared["evidence_digest"], "stage": 2, "control_tool_sha": "e" * 40}
        data["metadata"].update({"eventra.refresh.consumed": encode(consumed), "eventra.refresh.adoption": encode(adoption),
                                 "eventra.workflow.frontend_sha": "f" * 40})
        data["pr"]["head_sha"] = "f" * 40
    else:
        if state == "published":
            data["pr"]["head_sha"] = "f" * 40
        reservation = {"version": 1, "request_digest": request.digest, "authorization_uuid": uid(10), "action_key": action,
                       "state": state, "child_id": None if state == "reserved" else uid(9),
                       "child_identifier": None if state == "reserved" else "PRO-902",
                       "child_position": None if state == "reserved" else -8,
                       "prepared": prepared if state in {"candidate_registered", "published", "adopted"} else None,
                       "parent_status_category": boundary.parent["status_category"],
                       "parent_position": boundary.parent["position"],
                       "parent_projection_digest": hashlib.sha256(encode({"parent": {k: v for k, v in data["parent"].items()
                                                                                     if k not in {"metadata", "updated_at", "last_activity_at"}},
                                                                           "metadata": data["metadata"]}).encode()).hexdigest()}
        data["metadata"]["eventra.refresh.reservation"] = encode(reservation)
    return finish(request, data)


class RefreshDecisionTests(unittest.TestCase):
    def setUp(self):
        from tools.multica import candidate_refresh as c
        self.c = c
        self.assertTrue(hasattr(c, "plan_refresh"), "refresh state machine not implemented")

    def decision(self, request, data):
        return self.c.plan_refresh(request, self.c.RefreshSnapshot(encode(data)))

    def test_entry_has_deterministic_refresh_identity(self):
        request, data = refresh_snapshot_fixture(state="entry")
        result = self.decision(request, data)
        self.assertEqual(result.kind, "wait")
        self.assertIsNone(result.action_key)

    def test_complete_bound_prefix_has_deterministic_refresh_identity(self):
        request, data = refresh_snapshot_fixture(state="admitted")
        result = self.decision(request, data)
        self.assertEqual(result.kind, "create_refresh_stage")
        self.assertEqual(result.action_key, "2:PRO-900:create_refresh_stage:0:frontend:" + "b" * 40
                         + ":next-stage:2:refresh:1:" + request.digest)

    def test_unadopted_refresh_cannot_open_gate(self):
        request, data = refresh_snapshot_fixture()
        before = copy.deepcopy(data)
        self.assertEqual(self.decision(request, data).kind, "publish_refresh")
        self.assertEqual(data, before)

    def test_published_reservation_only_resumes_same_action(self):
        request, data = refresh_snapshot_fixture(state="published")
        result = self.decision(request, data)
        self.assertEqual(result.kind, "resume_refresh")
        self.assertIn(request.digest, result.action_key)

    def test_reserved_stage_resumes_not_recreates(self):
        request, data = refresh_snapshot_fixture(state="reserved")
        self.assertEqual(self.decision(request, data).kind, "resume_refresh")

    def test_done_prepared_is_not_qa_and_needs_registration(self):
        request, data = refresh_snapshot_fixture(state="child_dispatched")
        self.assertEqual(self.decision(request, data).kind, "resume_refresh")

    def test_complete_adoption_opens_fresh_gate_without_changing_history(self):
        request, data = refresh_snapshot_fixture(adopted=True)
        source = copy.deepcopy(data["children"][0])
        self.assertEqual(self.decision(request, data).kind, "create_gate_stage")
        self.assertEqual(data["children"][0], source)
        self.assertEqual(data["metadata"]["eventra.workflow.attempt"], "0")

    def test_adopted_state_rejects_mutable_parent_or_unexpected_writer(self):
        from tools.multica.tests.test_issue_contracts import issue_run

        cases = (
            lambda request, data: data["parent"].update(
                status="blocked", status_category="blocked"),
            lambda request, data: data["runs"].append(issue_run(
                id=uid(98), issue_id=data["children"][1]["detail"]["id"],
                agent_id=request.payload()["assignment"]["engineer_id"],
                workspace_id=uid(1), status="running", completed_at=None,
            )),
            lambda request, data: data["runs"].extend([
                issue_run(
                    id=uid(number), issue_id=request.payload()["parent"]["id"],
                    agent_id=request.payload()["assignment"]["lead_id"],
                    workspace_id=uid(1), status="running", completed_at=None,
                )
                for number in (97, 98)
            ]),
        )
        for mutate in cases:
            request, data = refresh_snapshot_fixture(adopted=True)
            mutate(request, data)
            with self.subTest(mutate=mutate):
                self.assertEqual(self.decision(request, data).kind, "block")

    def test_intent_without_grant_waits(self):
        request, data = refresh_snapshot_fixture(state="intent")
        self.assertEqual(self.decision(request, data).kind, "wait")

    def test_incomplete_or_unknown_feature_fields_block(self):
        for key, value in (("eventra.refresh.version", "2"), ("eventra.refresh.request_digest", "0" * 64),
                           ("eventra.refresh.merge_permission", "allow"), ("eventra.refresh.unknown", "1")):
            request, data = refresh_snapshot_fixture()
            data["metadata"][key] = value
            with self.subTest(key=key):
                self.assertEqual(self.decision(request, data).kind, "block")

    def test_unregistered_or_unrelated_head_drift_blocks(self):
        for state, head in (("child_dispatched", "f" * 40), ("candidate_registered", "a" * 40)):
            request, data = refresh_snapshot_fixture(state=state)
            data["pr"]["head_sha"] = head
            with self.subTest(state=state):
                self.assertEqual(self.decision(request, data).kind, "block")

    def test_illegal_receipt_and_missing_consumption_block_gate(self):
        for key in ("eventra.refresh.consumed", "eventra.refresh.adoption", "eventra.refresh.authorization_comment"):
            request, data = refresh_snapshot_fixture(adopted=True)
            del data["metadata"][key]
            with self.subTest(key=key):
                self.assertEqual(self.decision(request, data).kind, "block")

    def test_source_evidence_rewrite_still_blocks_after_adoption(self):
        request, data = refresh_snapshot_fixture(adopted=True)
        data["children"][0]["evidence"]["content"] = "replacement PASS"
        self.assertEqual(self.decision(request, data).kind, "block")

    def test_duplicate_refresh_and_wrong_child_assignment_block(self):
        request, data = refresh_snapshot_fixture(adopted=True)
        data["children"].append(copy.deepcopy(data["children"][1]))
        self.assertEqual(self.decision(request, data).kind, "block")
        request, data = refresh_snapshot_fixture(adopted=True)
        data["children"][1]["detail"]["assignee_id"] = uid(99)
        self.assertEqual(self.decision(request, data).kind, "block")

    def test_refresh_failure_blocks_without_repair_or_attempt_increment(self):
        for outcome in ("fail", "blocked"):
            request, data = refresh_snapshot_fixture(state="child_dispatched")
            data["children"][1]["metadata"]["eventra.phase.result"] = outcome
            with self.subTest(outcome=outcome):
                self.assertEqual(self.decision(request, data).kind, "block")
                self.assertEqual(data["metadata"]["eventra.workflow.attempt"], "0")

    def test_reservation_projection_mismatch_blocks_recovery(self):
        request, data = refresh_snapshot_fixture()
        data["parent"]["revision"] += 1
        self.assertEqual(self.decision(request, data).kind, "block")

    def test_embedded_issue_metadata_does_not_make_reservation_digest_recursive(self):
        request, data = refresh_snapshot_fixture()
        # Real issue get can echo the KV reservation. The separate metadata read
        # is authoritative; its echoed copy must not hash its own digest.
        data["parent"]["metadata"] = copy.deepcopy(data["metadata"])
        self.assertEqual(self.decision(request, data).kind, "publish_refresh")

    def test_server_activity_clocks_are_not_self_written_projection_fields(self):
        request, data = refresh_snapshot_fixture()
        data["parent"]["updated_at"] = "2026-09-04T02:00:00Z"
        data["parent"]["last_activity_at"] = "2026-09-04T02:00:00Z"
        self.assertEqual(self.decision(request, data).kind, "publish_refresh")
        data["parent"]["revision"] += 1
        self.assertEqual(self.decision(request, data).kind, "block")

    def test_prepared_identity_or_body_drift_blocks_publication(self):
        request, data = refresh_snapshot_fixture()
        data["children"][1]["evidence"]["revision"] = 2
        self.assertEqual(self.decision(request, data).kind, "block")

    def test_malformed_snapshot_returns_block_not_exception(self):
        request, data = refresh_snapshot_fixture()
        for changed in ({}, {**data, "children": [None]}, {**data, "pr": None}):
            with self.subTest(changed=changed.keys()):
                self.assertEqual(self.decision(request, changed).kind, "block")

    def test_published_malformed_partial_receipt_must_not_resume(self):
        request, data = refresh_snapshot_fixture(state="published")
        data["metadata"]["eventra.refresh.adoption"] = encode({"target_sha": "a" * 40})
        reservation = json.loads(data["metadata"]["eventra.refresh.reservation"])
        projection = {"parent": {k: v for k, v in data["parent"].items() if k not in {"metadata", "updated_at", "last_activity_at"}}, "metadata": {k: v for k, v in data["metadata"].items()
                                                               if k != "eventra.refresh.reservation"}}
        reservation["parent_projection_digest"] = hashlib.sha256(encode(projection).encode()).hexdigest()
        data["metadata"]["eventra.refresh.reservation"] = encode(reservation)
        self.assertEqual(self.decision(request, data).kind, "block")

    def test_post_adoption_children_cannot_skip_fresh_stage_three(self):
        request, data = refresh_snapshot_fixture(adopted=True)
        later = copy.deepcopy(data["children"][1])
        later["detail"].update(id=uid(99), identifier="PRO-999", stage=4)
        later["metadata"]["eventra.phase.kind"] = "qa"
        data["children"].append(later)
        self.assertEqual(self.decision(request, data).kind, "block")


def uid(number):
    return f"00000000-0000-4000-8000-{number:012d}"


def request_payload():
    return {
        "schema_version": 1, "workspace_id": uid(1),
        "parent": {"id": uid(2), "identifier": "PRO-900", "revision": 7,
                   "stage": 1, "attempt": 0, "next_stage": 2,
                   "status": "blocked", "merge_state": "not_ready",
                   "last_action": "2:PRO-900:create_implementation_stage:0:frontend:"
                                  + "a" * 40 + ":-:next-stage:1"},
        "source": {"child_id": uid(3), "child_identifier": "PRO-901",
                   "sha": "b" * 40, "evidence_uuid": uid(4),
                   "evidence_revision": 1, "evidence_digest": "c" * 64},
        "pr": {"url": "https://github.com/codeExploreHub/Eventra/pull/90",
               "repository": "codeExploreHub/Eventra",
               "head_ref": "agent/eventra-frontend-engineer/pro-901",
               "base_ref": "master"},
        "prerequisite": {"pr_url": "https://github.com/codeExploreHub/Eventra/pull/91",
                         "merge_sha": "d" * 40, "base_sha": "d" * 40},
        "assignment": {"project_id": uid(5), "squad_id": uid(6),
                       "lead_id": uid(7), "engineer_id": uid(8)},
        "baseline": {"authority_digest": "1" * 64, "comments_digest": "2" * 64},
        "refresh_stage": 2, "refresh_generation": 1,
        "staging_ref_prefix": "refs/heads/eventra-refresh/",
        "tree_transform": "clean-two-parent-merge-v1", "merge_permission": "hold",
        "control_tool_sha": "e" * 40, "git_version": "git version 2.50.1",
    }


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def block(kind, value):
    return f"```eventra-candidate-refresh-{kind}-v1\n{encode(value)}\n```"


def prepared_payload(request):
    return {
        "schema_version": 1, "request_digest": request.digest, "child_id": uid(9),
        "source_sha": "b" * 40, "prerequisite_sha": "d" * 40,
        "target_sha": "f" * 40, "tree_sha": "1" * 40,
        "staging_ref": request.staging_ref, "control_tool_sha": "e" * 40,
        "git_version": "git version 2.50.1",
        "context_receipt": {
            "schema_version": 1, "task_id": "PRO-902", "repository": "frontend",
            "task_type": "implementation", "candidate_shas": {"frontend": "f" * 40},
            "knowledge_ids": ["frontend-invariants"],
            "knowledge_digests": {"frontend-invariants": "2" * 64},
            "match_reasons": {"frontend-invariants": "repository+task_type+path"},
            "verified_ids": ["frontend-invariants"], "conflicts": [],
        },
        "commands": {
            "python": {"argv": ["python3", "-B", "-m", "unittest", "discover",
                                "-s", "tools/multica/tests", "-p", "test_*.py"], "exit_code": 0},
            "local_contract": {"argv": ["npm", "run", "test:local-contract"], "exit_code": 0},
            "footer": {"argv": ["npm", "run", "test:footer-meta"], "exit_code": 0},
            "hydration": {"argv": ["npm", "run", "test:layout-hydration"], "exit_code": 0},
            "dashboard": {"argv": ["npm", "run", "test:dashboard-profile"], "exit_code": 0},
            "lint": {"argv": ["npm", "run", "lint"], "exit_code": 0},
            "build": {"argv": ["npm", "run", "build"], "exit_code": 0},
            "knowledge": {"argv": ["python3", "-B", "-m", "tools.multica.knowledge",
                                   "verify", "--frontend-root", "/tmp/refresh-test/frontend",
                                   "--backend-root", "/tmp/refresh-test/backend"], "exit_code": 0},
        },
    }


class ContractCase(unittest.TestCase):
    def setUp(self):
        name = "tools.multica.candidate_refresh"
        self.assertIsNotNone(importlib.util.find_spec(name), "refresh contracts not implemented")
        self.api = importlib.import_module(name)
        self.request = self.api.build_request(request_payload())


class RequestTests(ContractCase):
    def test_request_baseline_participates_in_digest(self):
        payload = request_payload()
        expected = hashlib.sha256(encode(payload).encode()).hexdigest()

        try:
            request = self.api.build_request(payload)
        except ValueError as exc:
            self.fail(f"approved baseline was rejected: {exc}")

        self.assertEqual(request.digest, expected)
        changed = copy.deepcopy(payload)
        changed["baseline"]["comments_digest"] = "3" * 64
        self.assertNotEqual(self.api.build_request(changed).digest, expected)

    def test_request_digest_and_ref_bind_all_payload_bytes(self):
        expected = hashlib.sha256(encode(request_payload()).encode()).hexdigest()
        self.assertEqual(self.request.digest, expected)
        self.assertEqual(self.request.staging_ref, "refs/heads/eventra-refresh/" + expected)
        envelope = {"payload": request_payload(), "digest": expected,
                    "staging_ref": self.request.staging_ref}
        self.assertEqual(self.api.parse_request(envelope), self.request)
        self.assertEqual(self.api.parse_request(encode(envelope)), self.request)

    def test_mutating_input_or_decoded_copy_does_not_rewrite_frozen_request(self):
        payload = request_payload()
        request = self.api.build_request(payload)
        payload["parent"]["revision"] = 99
        decoded = request.payload()
        decoded["parent"]["revision"] = 101
        self.assertEqual(request.payload()["parent"]["revision"], 7)

    def test_extra_or_missing_fields_at_every_level_are_rejected(self):
        for section in (None, "parent", "source", "pr", "prerequisite", "assignment", "baseline"):
            for operation in ("extra", "missing"):
                payload = request_payload()
                target = payload if section is None else payload[section]
                if operation == "extra":
                    target["unapproved"] = "value"
                else:
                    del target[next(iter(target))]
                with self.subTest(section=section, operation=operation), self.assertRaises(ValueError):
                    self.api.build_request(payload)

    def test_unsupported_scope_and_unsafe_identity_are_rejected(self):
        cases = [
            ("schema_version", True), ("refresh_generation", 2), ("refresh_stage", 3),
            ("merge_permission", "allow"), ("tree_transform", "ours"),
            ("staging_ref_prefix", "refs/heads/master/"), ("workspace_id", "not-uuid"),
            ("control_tool_sha", "a" * 39), ("git_version", "git version 2.50.1\nextra"),
            ("parent.attempt", 1), ("parent.stage", 2), ("parent.next_stage", 3),
            ("parent.revision", True), ("parent.revision", 0), ("parent.status", "done"),
            ("parent.merge_state", "ready"), ("parent.identifier", "PRO-0"),
            ("parent.last_action", "2:PRO-999:create_implementation_stage:0:frontend:"
                                     + "a" * 40 + ":-:next-stage:1"),
            ("source.sha", "B" * 40), ("source.evidence_revision", 2),
            ("source.evidence_digest", "not-digest"), ("source.child_id", uid(2)),
            ("source.child_identifier", "PRO-900"),
            ("pr.url", "https://github.com/attacker/Eventra/pull/90"),
            ("pr.url", "https://github.com/codeExploreHub/Eventra/pull/90?x=1"),
            ("pr.repository", "codeExploreHub/Eventra-Backend"),
            ("pr.head_ref", "--upload-pack=evil"), ("pr.head_ref", "branch..other"),
            ("pr.head_ref", "refs/heads/x"), ("pr.head_ref", "feature/x.lock"),
            ("pr.head_ref", "feature/@{1}"), ("pr.head_ref", "master"),
            ("pr.base_ref", "master\nnext"),
            ("prerequisite.pr_url", "https://github.com/codeExploreHub/Eventra/pull/90"),
            ("prerequisite.merge_sha", "b" * 40),
            ("assignment.engineer_id", uid(7)), ("assignment.project_id", None),
            ("baseline.authority_digest", "x" * 64), ("baseline.comments_digest", True),
        ]
        for path, value in cases:
            payload = request_payload()
            parts = path.split(".")
            target = payload
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = value
            with self.subTest(path=path, value=value), self.assertRaises(ValueError):
                self.api.build_request(payload)

    def test_envelope_tampering_duplicate_keys_and_unbounded_input_rejected(self):
        envelope = {"payload": request_payload(), "digest": self.request.digest,
                    "staging_ref": self.request.staging_ref}
        for field, value in (("digest", "0" * 64), ("staging_ref", "refs/heads/master"),
                             ("unexpected", 1)):
            raw = {**envelope, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.api.parse_request(raw)
        duplicate = encode(envelope).replace('"schema_version":1', '"schema_version":1,"schema_version":1')
        for raw in (duplicate, " " * 17000 + encode(envelope), "[" * 2000,
                    encode(envelope).replace('"revision":7', '"revision":NaN'),
                    None, [], {1: "wrong"}):
            with self.subTest(raw_type=type(raw).__name__), self.assertRaises(ValueError):
                self.api.parse_request(raw)

    def test_canonical_json_rejects_nonfinite_numbers(self):
        with self.assertRaises(ValueError):
            self.api.canonical_json({"value": float("nan")})


class GrantTests(ContractCase):
    def grant(self, **changes):
        content = block("grant", {"schema_version": 1, "request_digest": self.request.digest,
                                  "granted_refresh": 1})
        comment = self.api.RefreshComment(uid(2), uid(10), uid(11), "member", 1, content)
        return replace(comment, **changes)

    def test_member_grant_binds_exact_request_and_parent(self):
        self.assertIsNone(self.api.validate_grant(self.grant(), self.request))

    def test_wrong_author_parent_revision_and_body_are_rejected(self):
        content = self.grant().content
        cases = [dict(author_type="agent"), dict(issue_id=uid(12)), dict(revision=2),
                 dict(revision=True), dict(comment_uuid="x"), dict(author_id="member"),
                 dict(content="Approved\n" + content), dict(content=content + "\n" + content),
                 dict(content=content.replace(self.request.digest, "0" * 64)),
                 dict(content=content.replace('"granted_refresh":1', '"granted_refresh":true')),
                 dict(content=content.replace('"schema_version":1', '"schema_version":1,"schema_version":1'))]
        for changes in cases:
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                self.api.validate_grant(self.grant(**changes), self.request)

    def test_forged_dataclass_is_not_a_validated_request(self):
        forged = replace(self.request, digest="0" * 64)
        with self.assertRaises(ValueError):
            self.api.validate_grant(self.grant(), forged)


class BaselineContractTests(ContractCase):
    def records(self):
        return [
            {"id": uid(30), "issue_id": uid(2), "author_id": uid(11),
             "author_type": "member", "type": "comment", "revision": 1,
             "created_at": "2026-09-04T01:00:00Z", "content": "old"},
            {"id": uid(31), "issue_id": uid(2), "parent_id": uid(30),
             "author_id": uid(7), "author_type": "agent", "type": "comment",
             "revision": 2, "created_at": "2026-09-04T01:01:00+00:00",
             "content": "reply", "reply_count": 0},
        ]

    def test_comment_manifest_binds_thread_identity_without_copying_content(self):
        self.assertTrue(hasattr(self.api, "comment_manifest"),
                        "comment manifest contract not implemented")
        manifest = self.api.comment_manifest(self.records(), uid(2))
        expected = [
            {"issue_id": uid(2), "comment_uuid": uid(30), "author_id": uid(11),
             "author_type": "member", "type": "comment", "revision": 1,
             "parent_id": None, "created_at": "2026-09-04T01:00:00Z",
             "content_digest": hashlib.sha256(b"old").hexdigest()},
            {"issue_id": uid(2), "comment_uuid": uid(31), "author_id": uid(7),
             "author_type": "agent", "type": "comment", "revision": 2,
             "parent_id": uid(30), "created_at": "2026-09-04T01:01:00+00:00",
             "content_digest": hashlib.sha256(b"reply").hexdigest()},
        ]
        self.assertEqual(manifest, expected)
        self.assertEqual(self.api.comment_manifest_digest(self.records(), uid(2)),
                         hashlib.sha256(encode(expected).encode()).hexdigest())

    def test_comment_manifest_binds_known_system_history_shapes(self):
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

        manifest = self.api.comment_manifest(records, uid(2))

        self.assertEqual(
            [(item["comment_uuid"], item["author_type"], item["type"],
              item["parent_id"]) for item in manifest],
            [
                (uid(40), "system", "progress_update", None),
                (uid(41), "agent", "comment", uid(40)),
                (uid(42), "system", "system", None),
                (uid(43), "agent", "system", None),
            ],
        )

    def test_comment_manifest_rejects_system_identity_confusion(self):
        base = {"id": uid(40), "author_id": uid(8), "author_type": "agent",
                "type": "system", "revision": 1,
                "created_at": "2026-09-04T01:00:00Z", "content": "event"}
        cases = (
            base | {"author_type": "member"},
            base | {"type": "progress_update"},
            base | {"author_type": "system", "type": "comment",
                    "author_id": "00000000-0000-0000-0000-000000000000"},
            base | {"author_type": "system", "author_id": uid(8)},
            base | {"type": "unknown"},
        )
        for record in cases:
            with self.subTest(author=record["author_type"], kind=record["type"]), \
                    self.assertRaises(ValueError):
                self.api.comment_manifest([record], uid(2))

    def test_comment_manifest_rejects_incomplete_or_ambiguous_history(self):
        cases = []
        cases.append(self.records() + [copy.deepcopy(self.records()[0])])
        wrong_scope = self.records(); wrong_scope[0]["issue_id"] = uid(9); cases.append(wrong_scope)
        unknown = self.records(); unknown[0]["folded"] = True; cases.append(unknown)
        orphan = self.records(); orphan[1]["parent_id"] = uid(99); cases.append(orphan)
        invalid_time = self.records(); invalid_time[0]["created_at"] = "yesterday"; cases.append(invalid_time)
        incomplete = self.records(); incomplete[0]["reply_count"] = 2; cases.append(incomplete)
        for records in cases:
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.api.comment_manifest(records, uid(2))
        for wrapper in ({"items": self.records(), "has_more": True}, None):
            with self.subTest(wrapper=type(wrapper).__name__), self.assertRaises(ValueError):
                self.api.comment_manifest(wrapper, uid(2))

    def authority_state(self):
        _, data = refresh_snapshot_fixture(state="entry")
        data["parent"]["metadata"] = copy.deepcopy(data["metadata"])
        data["children"][0]["detail"]["metadata"] = copy.deepcopy(data["children"][0]["metadata"])
        return data

    def test_authority_digest_binds_complete_nonvolatile_parent_and_source(self):
        self.assertTrue(hasattr(self.api, "authority_projection"),
                        "authority baseline projection not implemented")
        data = self.authority_state()
        projection = self.api.authority_projection(self.api.RefreshSnapshot(encode(data)))
        self.assertEqual(set(projection), {"parent", "metadata", "source", "assignment",
                                           "pr", "prerequisite", "tool"})
        self.assertNotIn("updated_at", projection["parent"])
        self.assertNotIn("last_activity_at", projection["source"]["detail"])
        self.assertNotIn("metadata", projection["parent"])
        self.assertEqual(projection["parent"]["revision"], 7)
        self.assertEqual(projection["source"]["metadata"], data["children"][0]["metadata"])
        self.assertEqual(self.api.authority_digest(self.api.RefreshSnapshot(encode(data))),
                         hashlib.sha256(encode(projection).encode()).hexdigest())
        changed = copy.deepcopy(data)
        changed["parent"]["title"] = "changed at the same revision"
        self.assertNotEqual(self.api.authority_digest(self.api.RefreshSnapshot(encode(changed))),
                            self.api.authority_digest(self.api.RefreshSnapshot(encode(data))))

    def test_authority_projection_rejects_echo_unknown_and_freeze_state_conflicts(self):
        cases = []
        echo = self.authority_state(); echo["parent"]["metadata"] = {}; cases.append(echo)
        unknown = self.authority_state(); unknown["parent"]["future_semantic"] = "x"; cases.append(unknown)
        active = self.authority_state(); active["parent"]["status"] = "in_progress"; cases.append(active)
        later = self.authority_state(); later["children"].append(copy.deepcopy(later["children"][0])); cases.append(later)
        for state in cases:
            with self.subTest(state=state["parent"].get("status")), self.assertRaises(ValueError):
                self.api.authority_projection(self.api.RefreshSnapshot(encode(state)))

    def test_metadata_budget_counts_whole_ascii_encoded_map_and_request(self):
        self.assertTrue(hasattr(self.api, "validate_metadata_budget"),
                        "refresh metadata budget not implemented")
        self.assertIsNone(self.api.validate_metadata_budget(
            {"eventra.workflow.version": "2", "other": "雪"}, request=self.request))
        for metadata in ({f"k{index}": "" for index in range(51)},
                         {"existing": "雪" * 1100}):
            with self.subTest(keys=len(metadata)), self.assertRaises(ValueError):
                self.api.validate_metadata_budget(metadata, request=self.request)
        payload = request_payload()
        payload["parent"]["identifier"] = "PRO-" + "9" * 61
        payload["parent"]["last_action"] = ("2:" + payload["parent"]["identifier"]
            + ":create_implementation_stage:0:frontend:" + "a" * 40 + ":-:next-stage:1")
        long_request = self.api.build_request(payload)
        with self.assertRaises(ValueError):
            self.api.validate_metadata_budget({}, request=long_request)

class PreparedTests(ContractCase):
    def evidence(self, payload=None, **changes):
        content = "Preparation only; no PR publication.\n" + block(
            "prepared", prepared_payload(self.request) if payload is None else payload)
        comment = self.api.RefreshComment(uid(9), uid(12), uid(8), "agent", 1, content)
        return replace(comment, **changes)

    def test_preparation_binds_evidence_bytes_and_explicit_target(self):
        comment = self.evidence()
        parsed = self.api.parse_prepared(comment, self.request, uid(9))
        self.assertEqual(parsed.target_sha, "f" * 40)
        self.assertEqual(parsed.evidence_uuid, uid(12))
        self.assertEqual(parsed.evidence_digest, hashlib.sha256(comment.content.encode()).hexdigest())

    def test_wrong_evidence_author_issue_or_revision_are_rejected(self):
        for changes in (dict(author_id=uid(7)), dict(author_type="member"),
                        dict(issue_id=uid(3)), dict(revision=2), dict(revision=True)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.api.parse_prepared(self.evidence(**changes), self.request, uid(9))

    def test_prepared_identity_drift_and_extra_fields_are_rejected(self):
        changes = {"request_digest": "0" * 64, "child_id": uid(3),
                   "source_sha": "a" * 40, "prerequisite_sha": "a" * 40,
                   "target_sha": "b" * 40, "tree_sha": "bad",
                   "control_tool_sha": "a" * 40, "git_version": "git version 2.1",
                   "staging_ref": "refs/heads/master", "schema_version": True,
                   "extra": "unapproved"}
        for key, value in changes.items():
            payload = prepared_payload(self.request)
            payload[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.api.parse_prepared(self.evidence(payload), self.request, uid(9))

    def test_missing_failed_or_substituted_commands_cannot_claim_pass(self):
        for check in prepared_payload(self.request)["commands"]:
            for mutation in ("missing", "failed", "boolean", "substitute", "extra"):
                payload = prepared_payload(self.request)
                command = payload["commands"][check]
                if mutation == "missing":
                    del payload["commands"][check]
                elif mutation == "failed":
                    command["exit_code"] = 1
                elif mutation == "boolean":
                    command["exit_code"] = False
                elif mutation == "substitute":
                    command["argv"] = ["true"]
                else:
                    command["skipped"] = True
                with self.subTest(check=check, mutation=mutation), self.assertRaises(ValueError):
                    self.api.parse_prepared(self.evidence(payload), self.request, uid(9))

    def test_scoped_lint_is_accepted_but_skipping_all_files_is_not(self):
        payload = prepared_payload(self.request)
        payload["commands"]["lint"]["argv"] += ["--", "--ignore-pattern", ".worktrees/**"]
        self.api.parse_prepared(self.evidence(payload), self.request, uid(9))
        payload["commands"]["lint"]["argv"][-1] = "**"
        with self.assertRaises(ValueError):
            self.api.parse_prepared(self.evidence(payload), self.request, uid(9))

    def test_wrong_context_sha_conflicts_or_invented_verified_ids_are_rejected(self):
        changes = {"candidate_shas": {"frontend": "b" * 40}, "repository": "backend",
                   "task_id": "PRO-901", "task_type": "review", "schema_version": True,
                   "conflicts": ["knowledge disagrees"], "verified_ids": ["invented"],
                   "knowledge_digests": {}, "match_reasons": {}, "extra": "value"}
        for key, value in changes.items():
            payload = prepared_payload(self.request)
            payload["context_receipt"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.api.parse_prepared(self.evidence(payload), self.request, uid(9))

    def test_duplicate_or_nested_prepared_blocks_are_rejected(self):
        text = self.evidence().content
        for content in (text + "\n" + text, "```text\n" + text + "\n```",
                        text.replace('"schema_version":1', '"schema_version":1,"schema_version":1')):
            with self.subTest(content_length=len(content)), self.assertRaises(ValueError):
                self.api.parse_prepared(self.evidence(content=content), self.request, uid(9))


class OutcomeTests(ContractCase):
    def evidence(self, result="blocked", **changes):
        payload = {
            "schema_version": 1,
            "request_digest": self.request.digest,
            "child_id": uid(9),
            "source_sha": "b" * 40,
            "prerequisite_sha": "d" * 40,
            "result": result,
            "commands": {
                "build": {"argv": ["npm", "run", "build"], "exit_code": 1},
            },
            "reason": "required build could not complete",
        }
        payload.update(changes)
        return self.api.RefreshComment(
            uid(9), uid(12), uid(8), "agent", 1,
            "Preparation did not pass.\n" + block("outcome", payload),
        )

    def test_non_pass_outcome_binds_identity_without_target(self):
        for result in ("fail", "blocked"):
            with self.subTest(result=result):
                parsed = self.api.parse_outcome(
                    self.evidence(result), self.request, uid(9), result,
                )
                self.assertEqual(parsed.result, result)
                self.assertEqual(parsed.source_sha, "b" * 40)
                self.assertEqual(parsed.evidence_uuid, uid(12))

    def test_non_pass_records_only_checks_actually_run(self):
        cases = (
            {},
            {
                "knowledge": {
                    "argv": [
                        "python3", "-B", "-m", "tools.multica.knowledge", "verify",
                        "--frontend-root", "/tmp/frontend",
                        "--backend-root", "/tmp/backend",
                    ],
                    "exit_code": 1,
                },
            },
            {
                "python": {
                    "argv": [
                        "python3", "-B", "-m", "unittest", "discover", "-s",
                        "tools/multica/tests", "-p", "test_*.py",
                    ],
                    "exit_code": 0,
                },
            },
        )
        for commands in cases:
            with self.subTest(commands=commands):
                parsed = self.api.parse_outcome(
                    self.evidence(commands=commands), self.request, uid(9), "blocked",
                )
                self.assertEqual(parsed.result, "blocked")

    def test_outcome_rejects_pass_target_and_identity_drift(self):
        for changes in (
                {"result": "pass"}, {"target_sha": "f" * 40},
                {"request_digest": "0" * 64}, {"child_id": uid(3)},
                {"source_sha": "a" * 40}, {"prerequisite_sha": "a" * 40},
                {"schema_version": True}, {"reason": ""},
                {"commands": {"build": {"argv": ["true"], "exit_code": 1}}},
                {"commands": {"build": {"argv": ["npm", "run", "build"], "exit_code": False}}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.api.parse_outcome(
                    self.evidence(**changes), self.request, uid(9), "blocked",
                )


if __name__ == "__main__":
    unittest.main()
