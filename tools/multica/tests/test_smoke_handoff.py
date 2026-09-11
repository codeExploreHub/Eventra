"""Smoke must receive its execution context before the first runnable task."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.multica import workflow
from tools.multica.tests import test_workflow as fixtures


def handoff():
    return {
        "control_tool_workspace": "/tmp/eventra-control",
        "control_tool_sha": "c" * 40,
        "repositories": {
            "backend": {
                "runtime_workspace": "/tmp/eventra-runtime",
                "inspection_workspace": "/tmp/eventra-smoke-inspection",
                "candidate_sha": fixtures.FakeRepairRunner.BACKEND_SHA,
                "merged_sha": "d" * 40,
                "pr_url": fixtures.FakeRepairRunner.BACKEND_PR,
            }
        },
    }


class SmokeHandoffTests(unittest.TestCase):
    def planned(self):
        return fixtures.SmokeExecutionTests()._planned()

    def test_missing_handoff_blocks_before_any_mutation(self):
        runner, github, decision = self.planned()
        result = workflow.execute_parent_smoke(
            runner, github, "PRO-65", expected_action_key=decision.action_key)
        self.assertEqual(result.next_action, "block")
        self.assertIn("handoff", result.reason)
        self.assertEqual(result.mutation_count, 0)
        self.assertEqual(runner.committed_mutations, 0)

    def test_creation_contains_full_handoff_before_any_run_can_start(self):
        runner, github, decision = self.planned()
        runner.hard_interrupt_after_create = True
        with self.assertRaises(KeyboardInterrupt):
            workflow.execute_parent_smoke(
                runner, github, "PRO-65", expected_action_key=decision.action_key,
                execution_handoff=handoff())
        child = next(child for child in runner.children if child["stage"] == 3)
        self.assertEqual(child["status"], "backlog")
        self.assertEqual(runner.runs[child["identifier"]], [])
        description = json.loads(child["description"])
        self.assertEqual(description["execution_handoff"], handoff())
        self.assertEqual(description["candidate_shas"]["backend"], "b" * 40)
        runner.hard_interrupt_after_create = False
        result = workflow.execute_parent_smoke(
            runner, github, "PRO-65", expected_action_key=decision.action_key)
        self.assertEqual(result.next_action, "smoke", result.reason)
        replay = workflow.execute_parent_smoke(
            runner, github, "PRO-65", expected_action_key=decision.action_key)
        self.assertEqual(replay.next_action, "noop", replay.reason)
        self.assertEqual(replay.mutation_count, 0)
        self.assertEqual(len(runner.runs[child["identifier"]]), 1)

    def test_invalid_handoff_does_not_reserve_or_create(self):
        invalid = [None, {}, {**handoff(), "repositories": {}}]
        for field, value in (
            ("candidate_sha", "a" * 40), ("merged_sha", "main"),
            ("pr_url", fixtures.FakeRepairRunner.BACKEND_PR + "9"),
            ("inspection_workspace", "relative/path"),
            ("inspection_workspace", "/tmp/eventra-runtime/inside"),
            ("runtime_workspace", "/"),
        ):
            item = handoff()
            item["repositories"]["backend"][field] = value
            invalid.append(item)
        for item in invalid:
            with self.subTest(handoff=item):
                runner, github, decision = self.planned()
                result = workflow.execute_parent_smoke(
                    runner, github, "PRO-65", expected_action_key=decision.action_key,
                    execution_handoff=item)
                self.assertEqual(result.next_action, "block", result.reason)
                self.assertEqual(runner.committed_mutations, 0)

    def test_resumption_rejects_changed_frozen_handoff_without_writes(self):
        runner, github, decision = self.planned()
        runner.hard_interrupt_after_create = True
        with self.assertRaises(KeyboardInterrupt):
            workflow.execute_parent_smoke(
                runner, github, "PRO-65", expected_action_key=decision.action_key,
                execution_handoff=handoff())
        runner.hard_interrupt_after_create = False
        before = runner.committed_mutations
        changed = copy.deepcopy(handoff())
        changed["control_tool_sha"] = "e" * 40
        result = workflow.execute_parent_smoke(
            runner, github, "PRO-65", expected_action_key=decision.action_key,
            execution_handoff=changed)
        self.assertEqual(result.next_action, "block")
        self.assertEqual(runner.committed_mutations, before)

    def test_legacy_reservation_can_resume_without_rewriting_description(self):
        runner, github, decision = self.planned()
        snapshot = workflow.load_parent_snapshot(runner, github, "PRO-65")
        reservation = workflow._build_smoke_reservation(snapshot, decision)
        runner.metadata["PRO-65"][workflow.SMOKE_RESERVATION_KEY] = (
            workflow._canonical_json(reservation))
        result = workflow.execute_parent_smoke(
            runner, github, "PRO-65", expected_action_key=decision.action_key)
        self.assertEqual(result.next_action, "smoke", result.reason)
        child = next(child for child in runner.children if child["stage"] == 3)
        self.assertNotIn("execution_handoff", json.loads(child["description"]))

    def test_committed_replay_rejects_changed_handoff_before_first_run(self):
        runner, github, decision = self.planned()
        runner.hard_interrupt_after_parent_delete_key = workflow.SMOKE_RESERVATION_KEY
        with self.assertRaises(KeyboardInterrupt):
            workflow.execute_parent_smoke(
                runner, github, "PRO-65", expected_action_key=decision.action_key,
                execution_handoff=handoff())
        runner.hard_interrupt_after_parent_delete_key = None
        self.assertNotIn(workflow.SMOKE_RESERVATION_KEY, runner.metadata["PRO-65"])
        child = next(child for child in runner.children if child["stage"] == 3)
        before = runner.committed_mutations
        changed = handoff()
        changed["control_tool_sha"] = "e" * 40
        result = workflow.execute_parent_smoke(
            runner, github, "PRO-65", expected_action_key=decision.action_key,
            execution_handoff=changed)
        self.assertEqual(result.next_action, "block")
        self.assertEqual(runner.committed_mutations, before)
        self.assertEqual(child["status"], "backlog")
        self.assertEqual(runner.runs[child["identifier"]], [])
        resumed = workflow.execute_parent_smoke(
            runner, github, "PRO-65", expected_action_key=decision.action_key,
            execution_handoff=handoff())
        self.assertEqual(resumed.next_action, "smoke", resumed.reason)
        self.assertEqual(len(runner.runs[child["identifier"]]), 1)

    def test_frontend_and_cross_stack_manifests_preserve_separate_merge_shas(self):
        for repositories in (("frontend",), ("frontend", "backend")):
            with self.subTest(repositories=repositories):
                value = handoff()
                value["repositories"] = {
                    repo: {
                        "runtime_workspace": f"/tmp/{repo}-runtime",
                        "inspection_workspace": f"/tmp/{repo}-inspection",
                        "candidate_sha": "a" * 40,
                        "merged_sha": "d" * 40,
                        "pr_url": ("https://github.com/codeExploreHub/" +
                                   ("Eventra" if repo == "frontend" else "Eventra-Backend") +
                                   "/pull/7"),
                    } for repo in repositories
                }
                prs = [{"repository": repo, "url": item["pr_url"]}
                       for repo, item in value["repositories"].items()]
                self.assertEqual(workflow.smoke_handoff.validate(
                    value, {repo: "a" * 40 for repo in repositories}, prs), value)

    def test_cli_reads_manifest_and_rejects_invalid_file(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = Path(folder) / "handoff.json"
            filename.write_text(json.dumps(handoff()), encoding="utf-8")
            args = workflow.build_workflow_parser().parse_args([
                "execute-parent-smoke", "PRO-65", "--expected-action-key", "exact",
                "--handoff-file", str(filename)])
            self.assertEqual(workflow.smoke_handoff.read_file(args.handoff_file), handoff())
            for content in ("{broken", " " * 8193):
                filename.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "handoff file"):
                    workflow.smoke_handoff.read_file(str(filename))
            with self.assertRaisesRegex(RuntimeError, "handoff file"):
                workflow.smoke_handoff.read_file(str(filename) + ".missing")

    def test_inspection_symlink_cannot_alias_protected_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / "runtime"
            runtime.mkdir()
            alias = Path(folder) / "inspection"
            alias.symlink_to(runtime, target_is_directory=True)
            value = handoff()
            value["repositories"]["backend"].update(
                runtime_workspace=str(runtime), inspection_workspace=str(alias))
            runner, github, decision = self.planned()
            result = workflow.execute_parent_smoke(
                runner, github, "PRO-65", expected_action_key=decision.action_key,
                execution_handoff=value)
            self.assertEqual(result.next_action, "block")
            self.assertEqual(runner.committed_mutations, 0)


if __name__ == "__main__":
    unittest.main()
