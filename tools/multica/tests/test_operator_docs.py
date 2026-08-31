import re
import unittest
from pathlib import Path


class OperatorDocsTests(unittest.TestCase):
    def test_authority_contracts_route_gate_results_only_through_delivery_lead(self):
        instructions = Path("tools/multica/instructions")
        rendered = {
            path.stem: " ".join(path.read_text().split())
            for path in instructions.glob("*.md")
        }
        lead = rendered["delivery_lead"]
        reviewer = rendered["independent_reviewer"]
        qa = rendered["integration_qa"]
        frontend = rendered["frontend_engineer"]
        backend = rendered["backend_engineer"]
        watcher = rendered["workflow_watcher"]

        self.assertIn("wait for every current Gate Stage child to become terminal", lead)
        self.assertIn("exact returned immutable FailureBundle", lead)
        self.assertIn(
            "must not mention or message an Implementer to request repair", reviewer
        )
        self.assertIn(
            "must not mention or message an Implementer to request repair", qa
        )
        self.assertIn(
            "modify business code only for a current active implementation or repair child",
            frontend,
        )
        self.assertIn(
            "modify business code only for a current active implementation or repair child",
            backend,
        )
        self.assertIn("cannot create a FailureBundle or dispatch repair", watcher)

        forbidden = (
            "Route failures to the owning implementer",
            "Fix returned findings in a new commit",
        )
        for name in (
            "delivery_lead",
            "independent_reviewer",
            "integration_qa",
            "frontend_engineer",
            "backend_engineer",
            "squad",
            "workflow_watcher",
            "eventra_project",
            "eventra_backend_project",
        ):
            for phrase in forbidden:
                with self.subTest(contract=name, phrase=phrase):
                    self.assertNotIn(phrase, rendered[name])

    def test_version_two_contracts_keep_core_decision_and_lead_execution_separate(self):
        instructions = Path("tools/multica/instructions")
        lead = " ".join((instructions / "delivery_lead.md").read_text().split())
        reviewer = (instructions / "independent_reviewer.md").read_text()
        qa = (instructions / "integration_qa.md").read_text()
        frontend = " ".join((instructions / "frontend_engineer.md").read_text().split())
        backend = " ".join((instructions / "backend_engineer.md").read_text().split())
        squad = " ".join((instructions / "squad.md").read_text().split())
        readme = " ".join(Path("tools/multica/README.md").read_text().split())

        for rendered in (lead, squad, readme):
            self.assertIn(
                "Core/plan-parent is the sole fan-in, canonical FailureBundle producer, and decision authority",
                rendered,
            )
            self.assertIn(
                "Delivery Lead is the sole execution actor", rendered
            )
            self.assertIn(
                "exact returned `failure_bundle` and digest without reconstruction",
                rendered,
            )
            self.assertNotIn("Lead creates one immutable FailureBundle", rendered)

        for engineer in (frontend, backend):
            self.assertIn(
                "address every assigned failure-partition reference", engineer
            )
            self.assertIn(
                "unresolved reference requires a non-PASS repair verdict", engineer
            )

        self.assertIn("### PASS gate completion", reviewer)
        self.assertIn("### Non-PASS gate completion", reviewer)
        self.assertIn("### PASS gate completion", qa)
        self.assertIn("### Non-PASS gate completion", qa)
        self.assertIn("### Smoke completion", qa)
        for rendered in (reviewer, qa):
            pass_form = rendered.split("### PASS gate completion", 1)[1].split(
                "### Non-PASS gate completion", 1
            )[0]
            pass_command = next(
                line for line in pass_form.splitlines() if line.startswith("python3 ")
            )
            self.assertNotIn("--evidence-comment-url", pass_command)
            self.assertNotIn("--responsible-repository", pass_command)
            nonpass_form = rendered.split("### Non-PASS gate completion", 1)[1]
            nonpass_command = next(
                line for line in nonpass_form.splitlines() if line.startswith("python3 ")
            )
            self.assertIn("--evidence-comment-url HTTPS_URL", nonpass_command)
            self.assertIn("--responsible-repository", nonpass_command)

        smoke = qa.split("### Smoke completion", 1)[1].split("## Forbidden", 1)[0]
        smoke_command = next(
            line for line in smoke.splitlines() if line.startswith("python3 ")
        )
        self.assertNotIn("--evidence-comment-url", smoke_command)
        self.assertNotIn("--responsible-repository", smoke_command)
        self.assertIn("Smoke is not a gate phase", smoke)

        for rendered in (lead, squad, readme):
            self.assertIn("Automatic repair rounds are exactly 1 and 2.", rendered)
            self.assertIn(
                "member comment may authorize only the exact current FailureBundle's exact next round 3, once.",
                rendered,
            )
            self.assertIn(
                "If round 3 fails, block the parent; do not create another repair child.",
                rendered,
            )

        for rendered in (" ".join(reviewer.split()), " ".join(qa.split())):
            self.assertIn("Core/plan-parent is the canonical FailureBundle producer", rendered)
            self.assertIn("Delivery Lead uses its exact returned bundle and digest", rendered)

        self.assertIn(
            "Eventra provision dry-run is read-only; a later `--apply` is a separate explicit authorization with fresh authoritative preflight and revalidation.",
            readme,
        )
        self.assertIn(
            "Eventra provision does not accept, bind, or validate a dry-run plan ID or hash.",
            readme,
        )

    def test_provision_dry_run_observed_plan_schema_is_documented(self):
        readme = " ".join(
            Path("tools/multica/README.md").read_text().split()
        )
        for fragment in (
            '"mode": "dry-run"',
            '"mutation_count": 0',
            '"summary"',
            '"preconditions": []',
            '"actions"',
            '"blocked": false',
            '`action`, `kind`, `key`, `name`, `id`, `operation`, and `changes`',
            'environment state as only `missing`, `set`, or `update`',
            'does not print environment key names or values',
            'An empty `actions` array with `summary.noop: true`',
            '`requires_input`',
            '`conflict`',
            'same environment-authority mode',
            'Dry-run may use `--reuse-backend-env`',
            'deterministic',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, readme)

    def test_every_execution_role_uses_terminal_phase_helper(self):
        instructions = Path("tools/multica/instructions")
        for name in (
            "frontend_engineer.md",
            "backend_engineer.md",
            "independent_reviewer.md",
            "integration_qa.md",
        ):
            rendered = (instructions / name).read_text()
            with self.subTest(role=name):
                self.assertIn("tools.multica.workflow finish-phase", rendered)
                self.assertIn("done means phase execution finished", rendered)
                self.assertIn("pass|fail|blocked", rendered)

    def test_delivery_lead_uses_native_stages_and_deterministic_parent_plan(self):
        rendered = Path(
            "tools/multica/instructions/delivery_lead.md"
        ).read_text()
        for fragment in (
            "tools.multica.workflow plan-parent",
            "--stage",
            "eventra.workflow.next_stage",
            "eventra.workflow.last_action",
            "Automatic repair rounds are exactly 1 and 2.",
            "Closes PRO-N",
            "Related to PRO-M",
            "production deployment is always human-triggered",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, rendered)

    def test_repair_execution_uses_the_verified_serialized_adapter_only(self):
        lead = " ".join(
            Path("tools/multica/instructions/delivery_lead.md").read_text().split()
        )
        readme = " ".join(Path("tools/multica/README.md").read_text().split())
        core = " ".join(Path("docs/multica-delivery-core.md").read_text().split())

        for rendered in (lead, readme, core):
            self.assertIn(
                "tools.multica.workflow execute-parent-repair",
                rendered,
            )
            self.assertIn("--expected-action-key", rendered)
            self.assertIn("authoritative parent-scoped comment UUID", rendered)
            self.assertIn("single serialized Delivery Lead", rendered)
            self.assertIn("eventra.workflow.repair_reservation", rendered)
            self.assertIn("eventra.repair.creation_action", rendered)
            self.assertIn("eventra.repair.failure_bundle_digest", rendered)
            self.assertIn("eventra.repair.authorizing_comment_uuid", rendered)
            self.assertIn("not generic CAS or transaction safety", rendered)
            self.assertIn("deterministic repair handoff", rendered)
            self.assertIn("starts the assigned agent", rendered)
            self.assertIn(
                "`mutation_count` reports authoritatively observed effects",
                rendered,
            )

        self.assertIn(
            "Do not create repair children manually",
            lead,
        )

    def test_unattended_parent_completion_and_exact_sha_checkout_are_explicit(self):
        instructions = Path("tools/multica/instructions")
        lead = (instructions / "delivery_lead.md").read_text()
        reviewer = (instructions / "independent_reviewer.md").read_text()
        qa = (instructions / "integration_qa.md").read_text()
        squad = (instructions / "squad.md").read_text()
        readme = Path("tools/multica/README.md").read_text()

        for fragment in (
            "tools.multica.workflow finish-parent",
            "directly to `done`",
            "production deployment is always human-triggered",
            "authoritative control repository",
        ):
            with self.subTest(role="lead", fragment=fragment):
                self.assertIn(fragment, " ".join(lead.split()))

        for role, rendered in (("reviewer", reviewer), ("qa", qa)):
            normalized = " ".join(rendered.split())
            for fragment in (
                "git switch --detach FULL_SHA",
                "FETCH_HEAD",
                "runtime-managed `AGENTS.md`, `.agent_context/`, and `.multica/`",
                "authoritative control repository",
            ):
                with self.subTest(role=role, fragment=fragment):
                    self.assertIn(fragment, normalized)

        for role, rendered in (("squad", squad), ("runbook", readme)):
            normalized = " ".join(rendered.split())
            with self.subTest(role=role):
                self.assertIn("tools.multica.workflow finish-parent", normalized)
                self.assertIn("directly to `done`", normalized)

    def test_runbook_documents_watcher_and_one_time_pro_35_recovery(self):
        readme = Path("tools/multica/README.md").read_text()
        for fragment in (
            "Eventra · Stalled Work Watcher",
            "native Stage barrier",
            "python3 -B -m tools.multica.workflow watch",
            "multica issue rerun PRO-35 --output json",
            "Do not manually mark PRO-36 PASS",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, readme)

    def test_runbook_documents_independent_watcher_agent(self):
        readme = Path("tools/multica/README.md").read_text()
        normalized = " ".join(readme.split())
        for fragment in (
            "Eventra Workflow Watcher",
            "not a member of `Eventra Local Delivery`",
            "does not receive backend environment values",
            "preserves the existing Autopilot and trigger IDs",
            "Multica 0.4.34",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, normalized)

    def test_cross_stack_qa_has_executable_dual_project_service_handoff(self):
        project_context = Path(
            "tools/multica/instructions/eventra_project.md"
        ).read_text()
        backend_context = Path(
            "tools/multica/instructions/eventra_backend_project.md"
        ).read_text()
        pilot = Path("docs/multica/pilot-issues.md").read_text()

        for rendered in (project_context, backend_context, pilot):
            self.assertIn("port 8080", rendered)
            self.assertIn("exact SHA", rendered)
        self.assertIn("keeps the backend child active", pilot)
        self.assertIn("one Reviewer task per Project", pilot)

    def test_pilot_backend_checks_use_deterministic_test_wrapper(self):
        pilot = Path("docs/multica/pilot-issues.md").read_text()
        normalized_pilot = " ".join(pilot.split())
        backend_only = pilot.split("## Pilot 2 — backend-only", 1)[1].split(
            "## Pilot 3 — cross-stack", 1
        )[0]
        cross_stack = pilot.split("## Pilot 3 — cross-stack", 1)[1]

        self.assertNotIn(
            "./mvnw -s .mvn/settings-public.xml test", normalized_pilot
        )
        for section_name, section in (
            ("backend-only", backend_only),
            ("cross-stack", cross_stack),
        ):
            normalized_section = " ".join(section.split())
            with self.subTest(section=section_name):
                self.assertIn(
                    "scripts/test-local.sh -Dtest=...", normalized_section
                )
                self.assertIn(
                    "complete suite as `scripts/test-local.sh`", normalized_section
                )

    def test_inspection_examples_match_multica_0_4_31(self):
        readme = Path("tools/multica/README.md").read_text()

        supported_commands = (
            "multica runtime list --output json",
            "multica daemon status --output json",
            "multica agent list --output json",
            "multica agent get AGENT_ID --output json",
            "multica agent skills list AGENT_ID --output json",
            "multica squad get SQUAD_ID --output json",
            "multica squad member list SQUAD_ID --output json",
            "multica project get PROJECT_ID --output json",
            "multica project resource list PROJECT_ID --output json",
            "multica skill list --output json",
            "multica skill get SKILL_ID --output json",
        )
        for command in supported_commands:
            with self.subTest(command=command):
                self.assertIn(command, readme)

        obsolete_fragments = (
            " --json",
            "--runtime-id",
            "--daemon-id",
            "agent skills list --agent-id",
            "squad members",
            "--squad-id",
            "project resources",
            "--project-id",
        )
        inspection_section = readme.split("## Inspection and reconciliation", 1)[1].split("## Delivery operation", 1)[0]
        for fragment in obsolete_fragments:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, inspection_section)

    def test_contract_recovery_runbook(self):
        """Recovery must stop on unobservable worktrees and preserve env boundaries."""

        readme = Path("tools/multica/README.md").read_text()
        self.assertIn("## Contract recovery runbook", readme)
        recovery = readme.split("## Contract recovery runbook", 1)[1].split(
            "## Inspection and reconciliation", 1
        )[0]

        required_fragments = (
            "`mutation_count` of `0`",
            "cannot prove the worktree",
            "codeExploreHub/Eventra",
            "manual production deployment",
            "stdin",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, recovery)

        blocks = [
            " ".join(match.group(1).replace("\\\n", " ").split())
            for match in re.finditer(r"```bash\n(.*?)\n```", recovery, re.DOTALL)
        ]
        self.assertEqual(4, len(blocks))
        audit, dry_run, recovery_apply, normal_apply = blocks

        self.assertEqual(
            "python3 -m tools.multica.contract_audit --runtime-id RUNTIME_ID --daemon-id DAEMON_ID",
            audit,
        )
        self.assertEqual(
            "python3 -m tools.multica.provision --runtime-id RUNTIME_ID "
            "--daemon-id DAEMON_ID --reuse-backend-env",
            dry_run,
        )

        approved_runtime = "de500649-cada-4419-9d5d-279045e2eaae"
        approved_daemon = "019fab98-bbad-7d17-b0b7-26e56dbe1b6f"
        self.assertEqual(
            f"python3 -m tools.multica.provision --runtime-id {approved_runtime} "
            f"--daemon-id {approved_daemon} --apply --reuse-backend-env",
            recovery_apply,
        )
        self.assertEqual(
            f"python3 -m tools.multica.provision --runtime-id {approved_runtime} "
            f"--daemon-id {approved_daemon} --apply",
            normal_apply,
        )

        reusable = "\n".join((audit, dry_run))
        eventra_specific = "\n".join((recovery_apply, normal_apply))
        uuid_pattern = r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b"
        self.assertEqual([], re.findall(uuid_pattern, reusable))
        self.assertEqual(
            {approved_runtime, approved_daemon},
            set(re.findall(uuid_pattern, eventra_specific)),
        )
        self.assertNotIn("--prompt-backend-env", recovery_apply)
        self.assertNotIn("--prompt-backend-env", normal_apply)
        self.assertNotIn("--reuse-backend-env", normal_apply)

        command_text = "\n".join(blocks)
        forbidden_command_patterns = (
            r"(?m)(?:^|[;\s])(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=",
            r"\b[A-Za-z_][A-Za-z0-9_]*(?:_SENTINEL|_VALUE|_SECRET|_PASSWORD)\b",
            r"Aprim-OPC|SkillsHub",
        )
        for pattern in forbidden_command_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, command_text))


if __name__ == "__main__":
    unittest.main()
