"""Refresh wire contracts: corruption must not become delivery authority."""

import copy
import hashlib
import importlib
import importlib.util
import json
import unittest
from dataclasses import replace


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
        for section in (None, "parent", "source", "pr", "prerequisite", "assignment"):
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


if __name__ == "__main__":
    unittest.main()
