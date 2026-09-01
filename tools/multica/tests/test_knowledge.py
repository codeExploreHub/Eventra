import contextlib
import copy
from dataclasses import replace
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tools.multica.knowledge import (
    KnowledgeParentRef,
    build_context_receipt,
    decide_curation,
    extract_candidate_blocks,
    extract_summary_pointer,
    list_pending_parents,
    load_index,
    load_candidate_snapshot,
    main,
    render_candidate_block,
    render_summary_pointer,
    select_knowledge,
    stable_curation_decision,
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

    def test_delivery_provenance_requires_candidate_digest_and_exact_evidence(self):
        provenance = {
            "kind": "delivery_evidence",
            "source_issue": "PRO-100",
            "source_comment_uuid": "00000000-0000-4000-8000-000000000100",
            "source_candidate_shas": {"frontend": "a" * 40},
            "source_candidate_digest": "d" * 64,
        }
        loaded = load_index(
            self.write_index([entry(self.doc.relative_to(self.root), provenance=provenance)]),
            self.root,
        )
        self.assertEqual(loaded[0].source_candidate_digest, "d" * 64)
        invalid = dict(provenance)
        invalid.pop("source_candidate_digest")
        with self.assertRaisesRegex(ValueError, "invalid knowledge index"):
            load_index(
                self.write_index([entry(self.doc.relative_to(self.root), provenance=invalid)]),
                self.root,
            )

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


FRONTEND_PROJECT_ID = "00000000-0000-4000-8000-000000000201"
BACKEND_PROJECT_ID = "00000000-0000-4000-8000-000000000202"
PARENT_UUID = "01a00000-0000-7000-8000-000000000200"
CHILD_UUID = "01a00000-0000-7000-8000-000000000201"
SUMMARY_UUID = "01a00000-0000-7000-8000-000000000202"
CANDIDATE_COMMENT_UUID = "01a00000-0000-7000-8000-000000000203"
AUTHOR_UUID = "00000000-0000-4000-8000-000000000204"


def knowledge_issue(identifier, issue_id, project_id, **overrides):
    value = {
        "assignee_id": AUTHOR_UUID,
        "assignee_type": "agent",
        "created_at": "2026-08-31T08:00:00Z",
        "creator_id": AUTHOR_UUID,
        "creator_type": "agent",
        "description": "Synthetic knowledge parent",
        "due_date": None,
        "id": issue_id,
        "identifier": identifier,
        "labels": [],
        "last_activity_at": "2026-08-31T09:00:00Z",
        "metadata": {},
        "number": int(identifier.split("-")[1]),
        "parent_issue_id": None,
        "position": -1,
        "priority": "none",
        "project_id": project_id,
        "properties": {},
        "revision": 1,
        "stage": None,
        "start_date": None,
        "status": "done",
        "status_category": "done",
        "title": "Synthetic knowledge parent",
        "updated_at": "2026-08-31T09:00:00Z",
        "workspace_id": "00000000-0000-4000-8000-000000000205",
    }
    value.update(overrides)
    return value


def compact_comment(comment_id, content, *, parent_id=None, created_at="2026-08-31T09:01:00Z"):
    value = {
        "author_id": AUTHOR_UUID,
        "author_type": "agent",
        "content": content,
        "created_at": created_at,
        "id": comment_id,
        "revision": 1,
        "type": "comment",
    }
    if parent_id is not None:
        value["parent_id"] = parent_id
    return value


class KnowledgeSummaryTests(unittest.TestCase):
    def test_summary_pointer_round_trip_is_canonical_and_contains_no_claim(self):
        rendered = render_summary_pointer(
            "PRO-101", CANDIDATE_COMMENT_UUID, "d" * 64
        )
        pointer = extract_summary_pointer("Summary follows.\n\n" + rendered)
        self.assertEqual(pointer.child_identifier, "PRO-101")
        self.assertEqual(pointer.evidence_comment_uuid, CANDIDATE_COMMENT_UUID)
        self.assertEqual(pointer.candidate_digest, "d" * 64)
        self.assertNotIn("claim", rendered)

    def test_summary_pointer_rejects_multiple_nested_or_malformed_blocks(self):
        valid = render_summary_pointer("PRO-101", CANDIDATE_COMMENT_UUID, "d" * 64)
        for value in (
            valid + "\n" + valid,
            "```eventra-knowledge-summary-v1\n{}\n```",
            "```eventra-knowledge-summary-v1\n```json\n{}\n```\n```",
            "```eventra-knowledge-summary-v1\n{}",
        ):
            with self.subTest(value=value[:24]):
                with self.assertRaisesRegex(ValueError, "invalid knowledge summary"):
                    extract_summary_pointer(value)

    def test_summary_cli_renders_the_approved_pointer_only(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                main([
                    "summary",
                    "--child", "PRO-101",
                    "--evidence-comment", CANDIDATE_COMMENT_UUID,
                    "--candidate-digest", "d" * 64,
                ]),
                0,
            )
        self.assertEqual(
            output.getvalue().strip(),
            render_summary_pointer("PRO-101", CANDIDATE_COMMENT_UUID, "d" * 64),
        )


class PendingParentRunner:
    def __init__(self):
        self.calls = []
        self.records = {
            (FRONTEND_PROJECT_ID, "done"): [
                knowledge_issue("PRO-102", "01a00000-0000-7000-8000-000000000222", FRONTEND_PROJECT_ID, updated_at="2026-08-31T09:02:00Z"),
                knowledge_issue("PRO-101", "01a00000-0000-7000-8000-000000000221", FRONTEND_PROJECT_ID, updated_at="2026-08-31T09:01:00Z"),
                knowledge_issue("PRO-199", "01a00000-0000-7000-8000-000000000299", FRONTEND_PROJECT_ID, parent_issue_id=PARENT_UUID, stage=1),
            ],
            (BACKEND_PROJECT_ID, "blocked"): [],
        }

    def run(self, args, *, stdin_json=None):
        call = tuple(args)
        self.calls.append(call)
        flags = dict(zip(call[2::2], call[3::2]))
        records = self.records.get((flags["--project"], flags["--status"]), [])
        offset = int(flags["--offset"])
        page = records[offset:offset + 2]
        return {
            "has_more": offset + len(page) < len(records),
            "issues": copy.deepcopy(page),
            "limit": 50,
            "offset": offset,
            "total": len(records),
        }


class KnowledgeScanTests(unittest.TestCase):
    def test_lists_paginated_pending_top_level_parents_oldest_first(self):
        runner = PendingParentRunner()
        parents = list_pending_parents(
            runner,
            {"frontend": FRONTEND_PROJECT_ID, "backend": BACKEND_PROJECT_ID},
        )
        self.assertEqual([item.identifier for item in parents], ["PRO-101", "PRO-102"])
        self.assertTrue(all(item.issue_id != "01a00000-0000-7000-8000-000000000299" for item in parents))
        for call in runner.calls:
            rendered = " ".join(call)
            self.assertIn("--limit 50", rendered)
            self.assertIn('eventra.knowledge.version=""1""', rendered)
            self.assertIn('eventra.knowledge.status=""pending""', rendered)

    def test_rejects_same_issue_id_with_inconsistent_cross_project_record(self):
        runner = PendingParentRunner()
        runner.records[(BACKEND_PROJECT_ID, "blocked")] = [
            knowledge_issue(
                "PRO-101",
                "01a00000-0000-7000-8000-000000000221",
                BACKEND_PROJECT_ID,
                status="blocked",
                status_category="blocked",
                updated_at="2026-08-31T09:01:00Z",
            )
        ]
        with self.assertRaisesRegex(RuntimeError, "malformed knowledge parent list"):
            list_pending_parents(
                runner,
                {"frontend": FRONTEND_PROJECT_ID, "backend": BACKEND_PROJECT_ID},
            )

    def test_rejects_status_or_pagination_that_disagrees_with_query(self):
        for mutate in (
            lambda runner: runner.records[(FRONTEND_PROJECT_ID, "done")][0].__setitem__("status", "in_progress"),
            lambda runner: setattr(runner, "returned_offset", 1),
            lambda runner: setattr(runner, "returned_limit", 49),
        ):
            runner = PendingParentRunner()
            runner.returned_offset = None
            runner.returned_limit = None
            mutate(runner)
            original_run = runner.run

            def run(args, *, stdin_json=None):
                result = original_run(args, stdin_json=stdin_json)
                if runner.returned_offset is not None:
                    result["offset"] = runner.returned_offset
                if runner.returned_limit is not None:
                    result["limit"] = runner.returned_limit
                return result

            runner.run = run
            with self.subTest(mutate=mutate):
                with self.assertRaisesRegex(RuntimeError, "malformed knowledge parent list"):
                    list_pending_parents(
                        runner,
                        {"frontend": FRONTEND_PROJECT_ID, "backend": BACKEND_PROJECT_ID},
                    )


class CandidateEvidenceRunner:
    def __init__(self):
        payload = candidate_input(
            evidence={
                "project_id": FRONTEND_PROJECT_ID,
                "parent_identifier": "PRO-100",
                "child_identifier": "PRO-101",
                "comment_uuid": CANDIDATE_COMMENT_UUID,
                "comment_url": f"https://multica.example/issues/PRO-101/comments/{CANDIDATE_COMMENT_UUID}",
            }
        )
        payload["digest"] = candidate_digest(payload)
        self.payload = payload
        self.parent = knowledge_issue("PRO-100", PARENT_UUID, FRONTEND_PROJECT_ID)
        self.child = knowledge_issue(
            "PRO-101", CHILD_UUID, FRONTEND_PROJECT_ID,
            parent_issue_id=PARENT_UUID, stage=1,
        )
        self.metadata = {
            "eventra.workflow.version": "1",
            "eventra.knowledge.version": "1",
            "eventra.knowledge.status": "pending",
            "eventra.knowledge.summary_comment": SUMMARY_UUID,
            "eventra.knowledge.candidate_digest": payload["digest"],
        }
        self.summary = compact_comment(
            SUMMARY_UUID,
            render_summary_pointer("PRO-101", CANDIDATE_COMMENT_UUID, payload["digest"]),
        )
        self.evidence = compact_comment(
            CANDIDATE_COMMENT_UUID,
            render_candidate_block(json.dumps(payload)),
        )
        self.calls = []

    def run(self, args, *, stdin_json=None):
        call = tuple(args)
        self.calls.append(call)
        if call == ("issue", "get", "PRO-102", "--output", "json"):
            return copy.deepcopy(dict(self.child, identifier="PRO-102", number=102))
        if call == (
            "issue", "comment", "list", "PRO-102", "--thread",
            CANDIDATE_COMMENT_UUID, "--tail", "30", "--compact", "--output", "json",
        ):
            return [copy.deepcopy(self.evidence)]
        replies = {
            ("issue", "get", "PRO-100", "--output", "json"): self.parent,
            ("issue", "metadata", "list", "PRO-100", "--output", "json"): self.metadata,
            ("issue", "comment", "list", "PRO-100", "--thread", SUMMARY_UUID, "--tail", "30", "--compact", "--output", "json"): [self.summary],
            ("issue", "get", "PRO-101", "--output", "json"): self.child,
            ("issue", "comment", "list", "PRO-101", "--thread", CANDIDATE_COMMENT_UUID, "--tail", "30", "--compact", "--output", "json"): [self.evidence],
        }
        if call not in replies:
            raise AssertionError(f"unsupported argv: {call!r}")
        return copy.deepcopy(replies[call])


def parent_ref():
    return KnowledgeParentRef(
        issue_id=PARENT_UUID,
        identifier="PRO-100",
        project_id=FRONTEND_PROJECT_ID,
        status="done",
        updated_at="2026-08-31T09:00:00Z",
    )


class KnowledgeCandidateSnapshotTests(unittest.TestCase):
    def test_loads_summary_pointer_then_original_candidate_evidence(self):
        runner = CandidateEvidenceRunner()
        snapshot = load_candidate_snapshot(
            runner,
            parent_ref(),
            {"frontend": FRONTEND_PROJECT_ID, "backend": BACKEND_PROJECT_ID},
        )
        self.assertEqual(snapshot.candidate.digest, runner.payload["digest"])
        self.assertEqual(snapshot.candidate.evidence.child_identifier, "PRO-101")
        self.assertEqual(snapshot.summary_comment_uuid, SUMMARY_UUID)
        self.assertEqual(snapshot.candidate_comment_uuid, CANDIDATE_COMMENT_UUID)

    def test_rejects_tampered_metadata_pointer_or_evidence_identity(self):
        mutations = (
            lambda runner: runner.metadata.__setitem__("eventra.knowledge.candidate_digest", "0" * 64),
            lambda runner: setattr(runner, "summary", compact_comment(SUMMARY_UUID, render_summary_pointer("PRO-102", CANDIDATE_COMMENT_UUID, runner.payload["digest"]))),
            lambda runner: runner.payload["evidence"].__setitem__("parent_identifier", "PRO-999"),
            lambda runner: runner.child.__setitem__("parent_issue_id", None),
        )
        for mutate in mutations:
            runner = CandidateEvidenceRunner()
            mutate(runner)
            if runner.payload.get("digest") != candidate_digest(runner.payload):
                runner.payload["digest"] = candidate_digest(runner.payload)
                runner.evidence = compact_comment(CANDIDATE_COMMENT_UUID, render_candidate_block(json.dumps(runner.payload)))
            with self.subTest(mutate=mutate):
                with self.assertRaisesRegex(RuntimeError, "invalid knowledge evidence"):
                    load_candidate_snapshot(
                        runner,
                        parent_ref(),
                        {"frontend": FRONTEND_PROJECT_ID, "backend": BACKEND_PROJECT_ID},
                    )

    def test_decides_duplicate_rejection_and_deterministic_create(self):
        runner = CandidateEvidenceRunner()
        snapshot = load_candidate_snapshot(
            runner, parent_ref(),
            {"frontend": FRONTEND_PROJECT_ID, "backend": BACKEND_PROJECT_ID},
        )
        entries = load_index(
            Path("docs/agent-knowledge/index.yaml"), Path.cwd(), "frontend"
        )
        duplicate = replace(
            entries[0], source_candidate_digest=snapshot.candidate.digest
        )
        self.assertEqual(decide_curation(snapshot, (duplicate,)).kind, "deduplicated")

        decision = decide_curation(snapshot, entries)
        self.assertEqual(decision.kind, "create_issue")
        self.assertEqual(decision.target_project_id, FRONTEND_PROJECT_ID)
        self.assertEqual(decision.target_repository, "frontend")
        self.assertEqual(len(decision.action_key), 64)
        self.assertEqual(decision, decide_curation(snapshot, entries))

        bad = replace(snapshot.candidate, candidate_shas=(("backend", "b" * 40),))
        self.assertEqual(decide_curation(replace(snapshot, candidate=bad), entries).kind, "rejected")
        missing_cross_sha = replace(
            snapshot.candidate,
            target_scope="cross_repo",
            candidate_shas=(("frontend", "a" * 40),),
        )
        self.assertEqual(
            decide_curation(replace(snapshot, candidate=missing_cross_sha), entries).kind,
            "rejected",
        )
        sensitive = replace(snapshot.candidate, sensitivity="sensitive")
        self.assertEqual(
            decide_curation(replace(snapshot, candidate=sensitive), entries).kind,
            "rejected",
        )
        backend = replace(
            snapshot.candidate,
            target_scope="backend",
            target_repository="backend",
            candidate_shas=(("backend", "b" * 40),),
        )
        backend_decision = decide_curation(
            replace(snapshot, candidate=backend), entries
        )
        self.assertEqual(backend_decision.target_project_id, BACKEND_PROJECT_ID)

    def test_stable_plan_requires_two_equal_snapshot_decisions(self):
        runner = CandidateEvidenceRunner()
        snapshot = load_candidate_snapshot(
            runner, parent_ref(),
            {"frontend": FRONTEND_PROJECT_ID, "backend": BACKEND_PROJECT_ID},
        )
        entries = load_index(
            Path("docs/agent-knowledge/index.yaml"), Path.cwd(), "frontend"
        )
        changed = replace(snapshot, updated_at="2026-08-31T09:00:01Z")
        values = iter((snapshot, changed))
        decision = stable_curation_decision(values.__next__, entries)
        self.assertEqual(decision.kind, "needs_human")
        self.assertIsNone(decision.action_key)

    def test_stable_plan_treats_second_authoritative_read_failure_as_change(self):
        runner = CandidateEvidenceRunner()
        entries = load_index(
            Path("docs/agent-knowledge/index.yaml"), Path.cwd(), "frontend"
        )
        reads = 0

        def load():
            nonlocal reads
            reads += 1
            if reads == 2:
                runner.parent["updated_at"] = "2026-08-31T09:00:01Z"
            return load_candidate_snapshot(
                runner,
                parent_ref(),
                {"frontend": FRONTEND_PROJECT_ID, "backend": BACKEND_PROJECT_ID},
            )

        decision = stable_curation_decision(load, entries)
        self.assertEqual(decision.kind, "needs_human")
        self.assertIsNone(decision.action_key)


class CombinedKnowledgeRunner(CandidateEvidenceRunner):
    def run(self, args, *, stdin_json=None):
        call = tuple(args)
        if call[:2] == ("issue", "list"):
            self.calls.append(call)
            flags = dict(zip(call[2::2], call[3::2]))
            issues = []
            if (
                flags["--project"] == FRONTEND_PROJECT_ID
                and flags["--status"] == "done"
            ):
                issues = [self.parent]
            return {
                "has_more": False,
                "issues": copy.deepcopy(issues),
                "limit": 50,
                "offset": int(flags["--offset"]),
                "total": len(issues),
            }
        return super().run(args, stdin_json=stdin_json)


class KnowledgeReadOnlyCliTests(unittest.TestCase):
    def test_scan_and_plan_are_read_only_redacted_and_deterministic(self):
        roots = [
            "--frontend-root", str(Path.cwd()),
            "--backend-root", "/Users/didi/Eventra-workspace/Eventra-Backend/.worktrees/eventra-knowledge-loop",
        ]
        runner = CombinedKnowledgeRunner()
        with mock.patch("tools.multica.knowledge.MulticaRunner", return_value=runner):
            scan_output = io.StringIO()
            with contextlib.redirect_stdout(scan_output):
                self.assertEqual(
                    main([
                        "scan", "--project-id", FRONTEND_PROJECT_ID,
                        "--backend-project-id", BACKEND_PROJECT_ID,
                    ]),
                    0,
                )
            plan_output = io.StringIO()
            with contextlib.redirect_stdout(plan_output):
                self.assertEqual(
                    main([
                        "plan", "--project-id", FRONTEND_PROJECT_ID,
                        "--backend-project-id", BACKEND_PROJECT_ID,
                        *roots,
                    ]),
                    0,
                )
        self.assertEqual(json.loads(scan_output.getvalue()), {"count": 1, "parents": ["PRO-100"]})
        plan = json.loads(plan_output.getvalue())
        self.assertEqual(plan["decision"], "create_issue")
        self.assertEqual(plan["parent"], "PRO-100")
        self.assertEqual(plan["target_repository"], "frontend")
        rendered = plan_output.getvalue()
        self.assertNotIn(runner.payload["claim"], rendered)
        self.assertNotIn(CANDIDATE_COMMENT_UUID, rendered)
        self.assertNotIn(runner.payload["digest"], rendered)
        allowed = {
            ("issue", "list"),
            ("issue", "get"),
            ("issue", "metadata", "list"),
            ("issue", "comment", "list"),
        }
        self.assertTrue(
            all(
                any(call[:len(prefix)] == prefix for prefix in allowed)
                for call in runner.calls
            )
        )


if __name__ == "__main__":
    unittest.main()
