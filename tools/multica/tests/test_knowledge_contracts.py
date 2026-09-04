import json
import unittest
from dataclasses import FrozenInstanceError

from tools.multica.knowledge_contracts import (
    candidate_digest,
    candidate_json,
    parse_candidate_json,
)


def candidate_payload(**overrides):
    payload = {
        "schema_version": 1,
        "evidence": {
            "project_id": "project-frontend",
            "parent_identifier": "PRO-100",
            "child_identifier": "PRO-101",
            "comment_uuid": "00000000-0000-4000-8000-000000000100",
            "comment_url": "https://multica.example/issues/PRO-101/comments/00000000-0000-4000-8000-000000000100",
        },
        "candidate_shas": {"frontend": "a" * 40},
        "target_scope": "frontend",
        "target_repository": "frontend",
        "knowledge_type": "invariant",
        "claim": "The API base must remain configurable.",
        "task_types": ["implementation"],
        "repository_paths": ["app/**"],
        "verification_refs": ["npm run test:local-contract"],
        "sensitivity": "public_repo",
        "related_knowledge_ids": [],
        "submitted_role": "frontend_engineer",
        "submitted_at": "2026-08-31T12:00:00+08:00",
    }
    payload.update(overrides)
    payload["digest"] = candidate_digest(payload)
    return payload


class KnowledgeContractTests(unittest.TestCase):
    def test_digest_is_independent_of_sha_mapping_order(self):
        left = candidate_payload(candidate_shas={"frontend": "a" * 40, "backend": "b" * 40})
        right = candidate_payload(candidate_shas={"backend": "b" * 40, "frontend": "a" * 40})
        self.assertEqual(candidate_digest(left), candidate_digest(right))

    def test_candidate_round_trip_is_canonical_and_frozen(self):
        value = parse_candidate_json(json.dumps(candidate_payload()))
        self.assertEqual(parse_candidate_json(candidate_json(value)), value)
        self.assertEqual(value.candidate_shas, (("frontend", "a" * 40),))
        with self.assertRaises(FrozenInstanceError):
            value.claim = "changed"

    def test_tampered_digest_is_rejected_without_echoing_claim(self):
        payload = candidate_payload()
        payload["digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "invalid knowledge candidate") as caught:
            parse_candidate_json(json.dumps(payload))
        self.assertNotIn(payload["claim"], str(caught.exception))

    def test_cross_repo_candidate_requires_complete_sha_map(self):
        payload = candidate_payload(target_scope="cross_repo")
        payload["digest"] = candidate_digest(payload)
        with self.assertRaisesRegex(ValueError, "invalid knowledge candidate"):
            parse_candidate_json(json.dumps(payload))

    def test_rejects_comment_url_uuid_mismatch(self):
        payload = candidate_payload()
        payload["evidence"]["comment_url"] = "https://multica.example/comments/00000000-0000-4000-8000-000000000999"
        payload["digest"] = candidate_digest(payload)
        with self.assertRaisesRegex(ValueError, "invalid knowledge candidate"):
            parse_candidate_json(json.dumps(payload))

    def test_rejects_unsafe_paths_sensitive_content_and_bad_time(self):
        cases = (
            {"repository_paths": ["../secret"]},
            {"sensitivity": "sensitive"},
            {"submitted_at": "2026-08-31"},
            {"verification_refs": []},
            {"candidate_shas": {"frontend": "A" * 40}},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                payload = candidate_payload(**changes)
                with self.assertRaisesRegex(ValueError, "invalid knowledge candidate"):
                    parse_candidate_json(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
