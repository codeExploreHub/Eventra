import json
from pathlib import Path
import unittest
from dataclasses import FrozenInstanceError, is_dataclass

from tools.multica.eventra_adapter import (
    AutopilotSpec,
    PUBLIC_SKILL_URLS,
    LocalResource,
    ProjectConfig,
    SkillSource,
    build_eventra_config,
    eventra_manifest,
    legacy_phase_contract,
    render_phase_contract,
)
from tools.multica.workflow import (
    PhaseCompletion as LegacyPhaseCompletion,
    build_phase_metadata,
)
from tools.multica_delivery.metadata import canonical_json


class EventraAdapterTests(unittest.TestCase):
    def setUp(self):
        self.config = build_eventra_config("runtime-id", "daemon-id")

    def test_builds_the_named_project_for_the_supplied_runtime_and_daemon(self):
        self.assertEqual(self.config.project_title, "Eventra Local Development")
        self.assertEqual(
            self.config.backend_project_title,
            "Eventra Backend Local Development",
        )
        self.assertTrue(self.config.project_context_file.is_file())
        self.assertTrue(self.config.backend_project_context_file.is_file())
        self.assertEqual(self.config.runtime_id, "runtime-id")
        self.assertEqual(self.config.daemon_id, "daemon-id")

    def test_registers_only_the_two_authoritative_worktree_repositories(self):
        self.assertEqual(
            self.config.resources,
            (
                LocalResource(
                    name="Eventra Frontend",
                    local_path="/Users/didi/Eventra-workspace/Eventra",
                    execution_mode="worktree",
                ),
                LocalResource(
                    name="Eventra Backend",
                    local_path="/Users/didi/Eventra-workspace/Eventra-Backend",
                    execution_mode="worktree",
                ),
            ),
        )

    def test_routes_each_authoritative_repository_to_its_own_project(self):
        self.assertIn(
            "owns only the frontend local resource",
            " ".join(self.config.project_context_file.read_text().split()),
        )
        self.assertIn(
            self.config.resources[1].local_path,
            self.config.backend_project_context_file.read_text(),
        )

    def test_forbids_the_nested_backend_duplicate(self):
        self.assertIn(
            "/Users/didi/Eventra-workspace/Eventra/Backend",
            self.config.forbidden_paths,
        )

    def test_uses_only_the_approved_public_skill_map(self):
        expected = {
            "using-superpowers",
            "brainstorming",
            "writing-plans",
            "executing-plans",
            "test-driven-development",
            "systematic-debugging",
            "requesting-code-review",
            "receiving-code-review",
            "verification-before-completion",
            "vercel-react-best-practices",
            "rest-api-conventions",
            "testing-pyramid",
            "spring-security-jwt",
            "playwright-cli",
        }
        self.assertEqual(set(PUBLIC_SKILL_URLS), expected)
        self.assertEqual(set(self.config.skills), expected)
        self.assertTrue(all(source.url.startswith("https://github.com/") for source in self.config.skills.values()))
        self.assertEqual(
            {key: source.url for key, source in self.config.skills.items()},
            PUBLIC_SKILL_URLS,
        )

    def test_rejects_non_public_and_excluded_skill_origins_from_the_configuration(self):
        rendered_urls = " ".join(source.url for source in self.config.skills.values()).lower()
        for forbidden in (
            "aprim-opc",
            "skills.sh",
        ):
            self.assertNotIn(forbidden, rendered_urls)
        for excluded in (
            "using-git-worktrees",
            "dispatching-parallel-agents",
            "subagent-driven-development",
            "finishing-a-development-branch",
        ):
            self.assertNotIn(excluded, self.config.skills)

    def test_injects_backend_environment_only_for_backend_engineering_and_integration_qa(self):
        self.assertEqual(
            [agent.role for agent in self.config.agents if agent.needs_backend_env],
            ["backend_engineer", "integration_qa"],
        )

    def test_exposes_five_delivery_agents_plus_two_operational_agents(self):
        self.assertEqual(len(self.config.blueprint.agents), 5)
        self.assertEqual(
            [agent.role for agent in self.config.blueprint.operational_agents],
            ["workflow_watcher", "knowledge_curator"],
        )
        self.assertEqual(len(self.config.agents), 7)
        self.assertEqual(
            [agent.role for agent in self.config.agents[-2:]],
            ["workflow_watcher", "knowledge_curator"],
        )
        self.assertTrue(
            all(not agent.needs_backend_env for agent in self.config.agents[-2:])
        )

    def test_watcher_targets_the_operational_role(self):
        self.assertEqual(self.config.watcher.agent_role, "workflow_watcher")
        self.assertEqual(
            [item.key for item in self.config.operational_automations],
            ["workflow-watcher", "knowledge-curator"],
        )

    def test_exposes_frozen_dataclass_contracts(self):
        values = (
            (SkillSource("key", "https://github.com/example/repo"), "url"),
            (self.config.resources[0], "local_path"),
            (self.config, "project_title"),
        )
        for value, field in values:
            self.assertTrue(is_dataclass(value))
            with self.assertRaises(FrozenInstanceError):
                setattr(value, field, "unexpected")

    def test_defines_one_run_only_stalled_work_watcher(self):
        self.assertEqual(
            self.config.watcher,
            AutopilotSpec(
                key="workflow-watcher",
                title="Eventra · Stalled Work Watcher",
                description_file=self.config.watcher.description_file,
                cron="*/30 * * * *",
                timezone="Asia/Shanghai",
                label="Eventra stalled-work recovery",
                agent_role="workflow_watcher",
            ),
        )
        self.assertTrue(self.config.watcher.description_file.is_file())
        self.assertIn(
            "python3 -B -m tools.multica.workflow watch",
            self.config.watcher.description_file.read_text(),
        )

    def test_eventra_only_curator_has_bounded_persistent_and_scheduled_contracts(self):
        curator = next(
            agent
            for agent in self.config.blueprint.operational_agents
            if agent.role == "knowledge_curator"
        )
        self.assertEqual(curator.name, "Eventra Knowledge Curator")
        self.assertEqual(
            curator.skill_keys,
            (
                "using-superpowers",
                "systematic-debugging",
                "verification-before-completion",
            ),
        )
        self.assertFalse(curator.needs_backend_env)
        persistent = curator.instructions_file.read_text()
        for required in (
            "at most one candidate",
            "docs/agent-knowledge/**",
            "docs/delivery-knowledge/**",
            "AGENTS.md",
            "human review",
        ):
            self.assertIn(required, persistent)
        for forbidden in (
            "business code",
            "Squad coordination",
            "delivery state",
            "secrets",
            "self-approve",
            "merge",
            "deploy",
            "force-push",
        ):
            self.assertIn(forbidden, persistent)

        spec = self.config.operational_automations[1]
        self.assertEqual(
            spec,
            AutopilotSpec(
                key="knowledge-curator",
                title="Eventra · Knowledge Curator",
                description_file=spec.description_file,
                cron="17 2 * * *",
                timezone="Asia/Shanghai",
                label="Eventra repository knowledge curation",
                agent_role="knowledge_curator",
            ),
        )
        scheduled = spec.description_file.read_text()
        self.assertEqual(scheduled.count("__FRONTEND_PROJECT_ID__"), 1)
        self.assertEqual(scheduled.count("__BACKEND_PROJECT_ID__"), 1)
        self.assertEqual(scheduled.count("__KNOWLEDGE_CURATOR_AGENT_ID__"), 1)
        self.assertIn("tools.multica.knowledge curate", scheduled)
        self.assertIn("--apply", scheduled)

    def test_generic_render_preserves_every_current_eventra_phase_scope(self):
        manifest = eventra_manifest(Path("/Users/didi/Eventra-workspace"))
        cases = (
            (
                "frontend implementation with PR",
                LegacyPhaseCompletion(
                    "implementation",
                    "pass",
                    0,
                    "00000000-0000-4000-8000-000000000001",
                    "a" * 40,
                    None,
                    "https://github.com/codeExploreHub/Eventra/pull/1",
                ),
            ),
            (
                "backend repair with PR",
                LegacyPhaseCompletion(
                    "repair",
                    "pass",
                    1,
                    "00000000-0000-4000-8000-000000000002",
                    None,
                    "b" * 40,
                    "https://github.com/codeExploreHub/Eventra-Backend/pull/2",
                ),
            ),
            (
                "frontend-only review",
                LegacyPhaseCompletion(
                    "review",
                    "pass",
                    1,
                    "00000000-0000-4000-8000-000000000003",
                    "c" * 40,
                    None,
                    None,
                ),
            ),
            (
                "backend-only QA",
                LegacyPhaseCompletion(
                    "qa",
                    "fail",
                    1,
                    "00000000-0000-4000-8000-000000000004",
                    None,
                    "d" * 40,
                    None,
                ),
            ),
            (
                "cross-stack integration QA",
                LegacyPhaseCompletion(
                    "qa",
                    "pass",
                    2,
                    "00000000-0000-4000-8000-000000000005",
                    "e" * 40,
                    "f" * 40,
                    None,
                ),
            ),
            (
                "cross-stack smoke",
                LegacyPhaseCompletion(
                    "smoke",
                    "blocked",
                    2,
                    "00000000-0000-4000-8000-000000000006",
                    "1" * 40,
                    "2" * 40,
                    None,
                ),
            ),
        )

        for label, completion in cases:
            with self.subTest(label=label):
                candidate_shas = {
                    key: sha
                    for key, sha in (
                        ("frontend", completion.frontend_sha),
                        ("backend", completion.backend_sha),
                    )
                    if sha is not None
                }
                arguments = {
                    "result": completion.result,
                    "attempt": completion.attempt,
                    "evidence_comment": completion.evidence_comment,
                    "pr_url": completion.pr_url,
                }
                expected = canonical_json(build_phase_metadata(completion))
                legacy = legacy_phase_contract(
                    candidate_shas,
                    completion.kind,
                    **arguments,
                )
                generic = render_phase_contract(
                    manifest,
                    candidate_shas,
                    completion.kind,
                    **arguments,
                )
                self.assertEqual(legacy.metadata_json, expected)
                self.assertEqual(generic.metadata_json, expected)
                self.assertEqual(json.loads(generic.metadata_json), json.loads(expected))

    def test_compatibility_renderer_rejects_generic_only_phase_names(self):
        manifest = eventra_manifest(Path("/Users/didi/Eventra-workspace"))
        arguments = {
            "result": "pass",
            "attempt": 1,
            "evidence_comment": "00000000-0000-4000-8000-000000000007",
        }
        with self.assertRaisesRegex(ValueError, "phase contract"):
            legacy_phase_contract(
                {"frontend": "a" * 40},
                "integration_qa",
                **arguments,
            )
        with self.assertRaisesRegex(ValueError, "phase contract"):
            render_phase_contract(
                manifest,
                {"frontend": "a" * 40},
                "integration_qa",
                **arguments,
            )

if __name__ == "__main__":
    unittest.main()
