import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from tools.multica.knowledge import (
    build_context_receipt,
    extract_candidate_blocks,
    load_index,
    main,
    render_candidate_block,
    select_knowledge,
    verify_indexes,
)
from tools.multica.knowledge_contracts import candidate_digest


SHA = "a" * 40
DESIGN_SHA = "32a150dfa"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entry(path: Path, **overrides):
    value = {
        "knowledge_id": "frontend-testing",
        "title": "Frontend testing",
        "purpose": "Choose the verified local test command.",
        "relative_path": path.as_posix(),
        "scope": "frontend",
        "repositories": ["frontend"],
        "repository_paths": ["app/**"],
        "task_types": ["qa"],
        "status": "active",
        "replacement_id": None,
        "provenance": {
            "kind": "bootstrap_design",
            "design_path": "docs/superpowers/specs/2026-08-31-eventra-repository-knowledge-loop-design.md",
            "design_commit": DESIGN_SHA,
        },
        "last_verified_sha": SHA,
        "last_verified_date": "2026-08-31",
        "content_digest": "",
    }
    value.update(overrides)
    return value


class KnowledgeIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.doc = self.root / "docs" / "agent-knowledge" / "testing-guide.md"
        self.doc.parent.mkdir(parents=True)
        self.doc.write_text("# Testing\n\nRun the local suite.\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_index(self, entries, *, directory="docs/agent-knowledge"):
        index_path = self.root / directory / "index.yaml"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        prepared = []
        for item in entries:
            item = dict(item)
            if not item["content_digest"]:
                item["content_digest"] = digest(self.root / item["relative_path"])
            prepared.append(item)
        index_path.write_text(
            yaml.safe_dump({"schema_version": 1, "entries": prepared}, sort_keys=False),
            encoding="utf-8",
        )
        return index_path

    def test_loads_valid_index_and_freezes_provenance(self):
        loaded = load_index(self.write_index([entry(self.doc.relative_to(self.root))]), self.root)
        self.assertEqual(loaded[0].knowledge_id, "frontend-testing")
        self.assertEqual(loaded[0].provenance_kind, "bootstrap_design")
        self.assertEqual(loaded[0].design_commit, DESIGN_SHA)

    def test_rejects_duplicate_yaml_keys_without_echoing_values(self):
        index_path = self.root / "docs" / "agent-knowledge" / "index.yaml"
        index_path.write_text("schema_version: 1\nschema_version: 1\nentries: []\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid knowledge index") as caught:
            load_index(index_path, self.root)
        self.assertNotIn("schema_version", str(caught.exception))

    def test_rejects_duplicate_ids_traversal_missing_digest_and_broken_replacement(self):
        valid = entry(self.doc.relative_to(self.root))
        cases = (
            [valid, dict(valid)],
            [entry(self.doc.relative_to(self.root), relative_path="../secret.md", content_digest="f" * 64)],
            [entry(self.doc.relative_to(self.root), relative_path="docs/agent-knowledge/missing.md", content_digest="f" * 64)],
            [entry(self.doc.relative_to(self.root), content_digest="0" * 64)],
            [entry(self.doc.relative_to(self.root), status="deprecated", replacement_id="missing")],
        )
        for number, entries in enumerate(cases):
            with self.subTest(number=number):
                with self.assertRaisesRegex(ValueError, "invalid knowledge index"):
                    load_index(self.write_index(entries), self.root)

    def test_rejects_shared_canonical_entry_in_backend(self):
        shared = entry(
            self.doc.relative_to(self.root),
            knowledge_id="shared-contract",
            scope="cross_repo",
            repositories=["frontend", "backend"],
        )
        with self.assertRaisesRegex(ValueError, "invalid knowledge index"):
            load_index(self.write_index([shared]), self.root, owner_repository="backend")

    def test_rejects_cross_repo_entry_outside_shared_index(self):
        shared = entry(
            self.doc.relative_to(self.root),
            knowledge_id="shared-contract",
            scope="cross_repo",
            repositories=["frontend", "backend"],
        )
        with self.assertRaisesRegex(ValueError, "invalid knowledge index"):
            load_index(self.write_index([shared]), self.root, owner_repository="frontend")

    def test_rejects_bootstrap_provenance_not_bound_to_reviewed_design(self):
        invalid_values = (
            {"kind": "bootstrap_design", "design_path": "docs/other.md", "design_commit": DESIGN_SHA},
            {"kind": "bootstrap_design", "design_path": "docs/superpowers/specs/2026-08-31-eventra-repository-knowledge-loop-design.md", "design_commit": "c" * 40},
        )
        for provenance in invalid_values:
            with self.subTest(provenance=provenance):
                with self.assertRaisesRegex(ValueError, "invalid knowledge index"):
                    load_index(self.write_index([entry(self.doc.relative_to(self.root), provenance=provenance)]), self.root)

    def test_selects_matching_entries_deterministically(self):
        first = entry(self.doc.relative_to(self.root))
        second_doc = self.doc.parent / "architecture.md"
        second_doc.write_text("# Architecture\n", encoding="utf-8")
        second = entry(
            second_doc.relative_to(self.root),
            knowledge_id="frontend-architecture",
            task_types=["implementation", "qa"],
            repository_paths=["app/**/*.tsx"],
        )
        entries = load_index(self.write_index([first, second]), self.root)
        result = select_knowledge(entries, "frontend", "qa", ("app/page.tsx",))
        self.assertEqual([item.knowledge_id for item in result], ["frontend-architecture", "frontend-testing"])

    def test_recursive_glob_matches_zero_or_more_path_segments(self):
        nested_doc = self.doc.parent / "architecture.md"
        nested_doc.write_text("# Architecture\n", encoding="utf-8")
        nested = entry(
            nested_doc.relative_to(self.root),
            knowledge_id="frontend-architecture",
            repository_paths=["src/**"],
        )
        tsx = entry(
            self.doc.relative_to(self.root),
            repository_paths=["app/**/*.tsx"],
        )
        entries = load_index(self.write_index([nested, tsx]), self.root)
        self.assertEqual(
            [item.knowledge_id for item in select_knowledge(entries, "frontend", "qa", ("src/lib/api.js",))],
            ["frontend-architecture"],
        )
        self.assertEqual(
            [item.knowledge_id for item in select_knowledge(entries, "frontend", "qa", ("app/page.tsx",))],
            ["frontend-testing"],
        )

    def test_receipt_is_canonical_and_records_reasons(self):
        entries = load_index(self.write_index([entry(self.doc.relative_to(self.root))]), self.root)
        receipt = build_context_receipt("PRO-100", "frontend", "qa", {"frontend": SHA}, entries, ("app/page.tsx",))
        payload = json.loads(receipt)
        self.assertEqual(payload["knowledge_ids"], ["frontend-testing"])
        self.assertEqual(payload["candidate_shas"], {"frontend": SHA})
        self.assertEqual(payload["match_reasons"], {"frontend-testing": "repository+task_type+path"})
        self.assertEqual(receipt, json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))

    def test_receipt_records_explicit_current_code_verification_and_conflict(self):
        entries = load_index(self.write_index([entry(self.doc.relative_to(self.root))]), self.root)
        receipt = build_context_receipt(
            "PRO-100",
            "frontend",
            "qa",
            {"frontend": "c" * 40},
            entries,
            ("app/page.tsx",),
            verified_ids=("frontend-testing",),
            conflicts=("frontend-testing: command changed; current code wins",),
        )
        payload = json.loads(receipt)
        self.assertEqual(payload["verified_ids"], ["frontend-testing"])
        self.assertEqual(payload["conflicts"], ["frontend-testing: command changed; current code wins"])

    def test_receipt_rejects_verification_for_unselected_entry(self):
        entries = load_index(self.write_index([entry(self.doc.relative_to(self.root))]), self.root)
        with self.assertRaisesRegex(ValueError, "invalid context receipt"):
            build_context_receipt(
                "PRO-100",
                "frontend",
                "qa",
                {"frontend": SHA},
                entries,
                ("app/page.tsx",),
                verified_ids=("unknown",),
            )

    def test_verify_indexes_loads_frontend_local_shared_and_backend_local(self):
        frontend = self.root / "frontend"
        backend = self.root / "backend"
        for repository in (frontend, backend):
            (repository / "docs" / "agent-knowledge").mkdir(parents=True)
        front_doc = frontend / "docs" / "agent-knowledge" / "map.md"
        back_doc = backend / "docs" / "agent-knowledge" / "map.md"
        shared_doc = frontend / "docs" / "delivery-knowledge" / "system-map.yaml"
        front_doc.write_text("front\n", encoding="utf-8")
        back_doc.write_text("back\n", encoding="utf-8")
        shared_doc.parent.mkdir(parents=True)
        shared_doc.write_text("system: Eventra\n", encoding="utf-8")

        def write(repository, directory, item):
            item = dict(item)
            item["content_digest"] = digest(repository / item["relative_path"])
            path = repository / directory / "index.yaml"
            path.write_text(yaml.safe_dump({"schema_version": 1, "entries": [item]}, sort_keys=False), encoding="utf-8")

        write(frontend, "docs/agent-knowledge", entry(Path("docs/agent-knowledge/map.md"), knowledge_id="frontend-map"))
        write(backend, "docs/agent-knowledge", entry(Path("docs/agent-knowledge/map.md"), knowledge_id="backend-map", scope="backend", repositories=["backend"]))
        write(frontend, "docs/delivery-knowledge", entry(Path("docs/delivery-knowledge/system-map.yaml"), knowledge_id="shared-map", scope="cross_repo", repositories=["frontend", "backend"], repository_paths=["**"] ))

        result = verify_indexes(frontend, backend)
        self.assertEqual([item.knowledge_id for item in result], ["backend-map", "frontend-map", "shared-map"])

    def test_verify_cli_emits_only_counts_and_ids(self):
        self.write_index([entry(self.doc.relative_to(self.root))])
        backend = self.root / "backend"
        (backend / "docs" / "agent-knowledge").mkdir(parents=True)
        backend_doc = backend / "docs" / "agent-knowledge" / "map.md"
        backend_doc.write_text("back\n", encoding="utf-8")
        backend_item = entry(Path("docs/agent-knowledge/map.md"), knowledge_id="backend-map", scope="backend", repositories=["backend"], content_digest=digest(backend_doc))
        (backend / "docs" / "agent-knowledge" / "index.yaml").write_text(yaml.safe_dump({"schema_version": 1, "entries": [backend_item]}, sort_keys=False), encoding="utf-8")
        (self.root / "docs" / "delivery-knowledge").mkdir(parents=True)
        (self.root / "docs" / "delivery-knowledge" / "index.yaml").write_text("schema_version: 1\nentries: []\n", encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["verify", "--frontend-root", str(self.root), "--backend-root", str(backend)]), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["knowledge_ids"], ["backend-map", "frontend-testing"])


def candidate_input(**overrides):
    value = {
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
        "claim": "The API base remains configurable.",
        "task_types": ["implementation"],
        "repository_paths": ["src/**"],
        "verification_refs": ["npm run test:local-contract"],
        "sensitivity": "public_repo",
        "related_knowledge_ids": ["frontend-invariants"],
        "submitted_role": "frontend_engineer",
        "submitted_at": "2026-08-31T12:00:00+08:00",
    }
    value.update(overrides)
    return value


class KnowledgeCandidateCliTests(unittest.TestCase):
    def test_candidate_cli_adds_digest_and_prints_one_canonical_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(candidate_input()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["candidate", "--input", str(path)]), 0)
        rendered = output.getvalue().strip()
        self.assertTrue(rendered.startswith("```eventra-knowledge-candidate-v1\n"))
        self.assertTrue(rendered.endswith("\n```"))
        candidates = extract_candidate_blocks(rendered)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].digest, candidate_digest(candidate_input()))

    def test_render_round_trip_is_canonical(self):
        value = candidate_input()
        value["digest"] = candidate_digest(value)
        rendered = render_candidate_block(json.dumps(value, indent=2))
        parsed = extract_candidate_blocks("Evidence follows.\n\n" + rendered)
        self.assertEqual(parsed[0].claim, value["claim"])
        self.assertEqual(rendered, render_candidate_block(rendered.split("\n", 1)[1].rsplit("\n", 1)[0]))

    def test_rejects_nested_multiple_or_malformed_candidate_blocks_without_echo(self):
        value = candidate_input()
        value["digest"] = candidate_digest(value)
        valid = render_candidate_block(json.dumps(value))
        cases = (
            valid + "\n" + valid,
            "```eventra-knowledge-candidate-v1\n```json\n{}\n```\n```",
            "```eventra-knowledge-candidate-v1\nnot-json\n```",
            "```eventra-knowledge-candidate-v1\n{}",
        )
        for evidence in cases:
            with self.subTest(evidence=evidence[:20]):
                with self.assertRaisesRegex(ValueError, "invalid knowledge candidate") as caught:
                    extract_candidate_blocks(evidence)
                self.assertNotIn(value["claim"], str(caught.exception))


if __name__ == "__main__":
    unittest.main()
