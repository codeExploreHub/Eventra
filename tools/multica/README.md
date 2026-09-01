# Eventra Multica adapter

This adapter composes a reusable five-role delivery blueprint with one
independent operational Watcher Agent across Eventra's two authoritative local
repositories. The five delivery roles form the `Eventra Local Delivery` Squad;
the Watcher Agent is outside that Squad. This is local-development automation
only; production deployment is not implemented and remains a human action.

The project-neutral Plan 1 library is documented in
[Generic Multica multi-repository delivery core](../../docs/multica-delivery-core.md).
It does not replace these Eventra entry points or provide the future generic
CLI. Eventra remains on this operational adapter while
`eventra_manifest(workspace)` supplies an immutable generic compatibility
fixture; live migration waits for compatibility pilots and separate approval.

## Inputs and safe execution

The provisioner requires a Multica `runtime_id` and `daemon_id`:

```bash
python3 -m tools.multica.provision --runtime-id RUNTIME_ID --daemon-id DAEMON_ID
```

That command is a dry run by default. Review its planned reconciliation before
using `--apply` to create or update Multica state:

```bash
python3 -m tools.multica.provision --runtime-id RUNTIME_ID --daemon-id DAEMON_ID --apply
```

Use `--prompt-backend-env` only when Backend Engineer and Integration QA need
the local backend environment. It prompts for the secret without echoing it
and passes it only through those agents' custom environment. Do not put a
secret in shell history, Issue text, logs, pull-request descriptions, or a
tracked environment file.

## Contract recovery runbook

Use this sequence only after an interrupted Eventra reconciliation. Do not
substitute identifiers from shell history or infer resource state from a prior
command's argv. The read-only audit is scalar-free: its output may establish
only JSON structure, keys, array lengths, and target-ID equality. If the
authoritative resource read cannot prove the worktree execution mode and
local-path state, stop before any mutation. Obtain a supported authoritative
read contract; do not guess from a create or update command.

Reusable recovery commands keep identifiers as placeholders:

1. Audit scalar-free read shapes before any recovery action.

```bash
python3 -m tools.multica.contract_audit \
  --runtime-id RUNTIME_ID \
  --daemon-id DAEMON_ID
```

2. Confirm the planned reconciliation without mutation.

```bash
python3 -m tools.multica.provision \
  --runtime-id RUNTIME_ID \
  --daemon-id DAEMON_ID
```

For the approved Eventra recovery target only, run the following in this exact
order after the audit and dry run succeed:

3. Run one recovery apply; it does not prompt for backend environment input.

```bash
python3 -m tools.multica.provision \
  --runtime-id de500649-cada-4419-9d5d-279045e2eaae \
  --daemon-id 019fab98-bbad-7d17-b0b7-26e56dbe1b6f \
  --apply \
  --reuse-backend-env
```

4. Prove idempotency with a normal apply and no environment-mode flag.

```bash
python3 -m tools.multica.provision \
  --runtime-id de500649-cada-4419-9d5d-279045e2eaae \
  --daemon-id 019fab98-bbad-7d17-b0b7-26e56dbe1b6f \
  --apply
```

The final command must report a `mutation_count` of `0`. If either apply
fails, stop; inspect only sanitized shapes and resume from verified state. Do
not delete or roll back partially reconciled resources automatically.

`--reuse-backend-env` reads the exact existing Backend Engineer custom
environment in the same process, validates it, and forwards that dictionary
only through stdin to Backend Engineer and Integration QA. It never puts an
environment value in argv, files, logs, exceptions, reports, or this runbook.

Merge recovery changes only through the personal `codeExploreHub/Eventra`
fork. Production deployment is a separate manual production deployment action;
this local-recovery process never deploys production.

## Inspection and reconciliation

Use the Multica CLI JSON views to inspect the resulting state, substituting
the identifiers printed by the provisioner. Multica 0.4.31 uses `--output
json`, positional object IDs, singular `squad member` and `project resource`
commands, and workspace-scoped list commands without runtime or daemon
filters:

```bash
multica runtime list --output json
multica daemon status --output json
multica agent list --output json
multica agent get AGENT_ID --output json
multica agent skills list AGENT_ID --output json
multica squad get SQUAD_ID --output json
multica squad member list SQUAD_ID --output json
multica project get PROJECT_ID --output json
multica project resource list PROJECT_ID --output json
multica skill list --output json
multica skill get SKILL_ID --output json
```

Compare every listed skill origin with the public GitHub URL map in
`eventra_adapter.py`. A pre-existing skill with the same name but a different
origin is a hard stop: resolve it explicitly before applying again. The
provisioner makes additive bindings with `agent skills add`; it never invokes
`agent skills set`, so it does not replace existing bindings.

Multica 0.4.31 permits only one `local_directory` per Project on the same
daemon. The adapter therefore maintains two fixed Projects under one Squad:
`Eventra Local Development` is the parent-Issue entry point and owns the
frontend worktree; `Eventra Backend Local Development` owns the backend
worktree and backend child Issues. The nested frontend `Backend` directory is
forbidden. Re-running a dry run or an already-applied matching configuration
is reconciliation, not permission to create duplicates.

## Delivery operation

Use the project context and each repository's `AGENTS.md` as the operating
contract. Frontend work uses `npm run test:local-contract`, `npm run
dev:local`, and `npm run smoke:local`; backend work uses
`scripts/test-local.sh`, `scripts/run-local.sh`, and
`scripts/smoke-local.sh`. Cross-stack work freezes the API contract first,
keeps one pull request per repository, and records exact reviewed and tested
commit SHAs. A partial two-repository merge stops immediately and requires
human escalation; it does not trigger rollback or deployment.

Every execution role retrieves indexed knowledge before acting. Run the
read-only context command from the authoritative Eventra control repository,
supplying one `--sha REPOSITORY=FULL_SHA` per affected repository and one or
more assigned paths:

```text
python3 -B -m tools.multica.knowledge context --task-id PRO-N --repository frontend --task-type implementation --sha frontend=FULL_SHA --path src/PATH
```

Attach the canonical JSON output as the Context Receipt and verify material
claims against current code, tests, or an authoritative contract. Rerun with
one `--verified-id KNOWLEDGE_ID` per checked entry and `--conflict TEXT` for
each stale or contradictory claim; current code and exact-SHA evidence win.
If a novel,
verified, reusable, public-repository-safe fact emerges, place its JSON in a
temporary untracked file and render the only accepted evidence format:

```text
python3 -B -m tools.multica.knowledge candidate --input CANDIDATE_JSON_FILE
```

The output is one `eventra-knowledge-candidate-v1` block. Delete the temporary
input after posting normal evidence. Do not include credentials, personal data,
production payloads, or raw logs. Candidates never block delivery and never
authorize incidental knowledge edits; accepted changes use a separate
knowledge pull request with human review and no Curator self-merge.

At parent completion, Delivery Lead selects the one pilot candidate and posts
only a machine-readable pointer; it does not copy the candidate claim:

```text
python3 -B -m tools.multica.knowledge summary --child PRO-N --evidence-comment COMMENT_UUID --candidate-digest SHA256
```

The resulting `eventra-knowledge-summary-v1` block contains only
`schema_version`, `child_identifier`, `evidence_comment_uuid`, and
`candidate_digest`. The Curator follows that pointer back to the original
comment and requires every identity and digest to match.

Cross-stack gates respect the one-worktree Project boundary. Reviewer tasks run
once per Project and are combined by Delivery Lead. QA verifies the backend SHA
in the backend Project, then Backend Engineer keeps that verified SHA running
on port 8080 while Integration QA tests the frontend SHA from the frontend
Project through the shared daemon network. If the exact service handoff cannot
be maintained and verified, the gate blocks rather than being waived.

## Native Stage automation and Watcher

The native Stage barrier is the primary wakeup path. Every execution child
finishes with `python3 -B -m tools.multica.workflow finish-phase`; Delivery Lead
uses `python3 -B -m tools.multica.workflow plan-parent` after Multica wakes the
parent. Phase `done` means execution finished, while metadata records
`pass|fail|blocked`. FAIL and BLOCKED therefore wake the parent instead of
leaving a child permanently `in_review`.

When planning returns `complete_parent`, Delivery Lead runs
`python3 -B -m tools.multica.workflow finish-parent PRO-M`. The helper reads the
authoritative merged-smoke state twice, refuses human-approval or changing
state, and moves a verified unattended local-development parent directly to
`done`. It does not deploy production.

Provisioning reconciles one run-only **Eventra · Stalled Work Watcher** with a
30-minute `Asia/Shanghai` schedule. Its rendered task invokes
`python3 -B -m tools.multica.workflow watch` for only the two configured
Projects. It performs at most one verified rerun and is a recovery fallback,
not a second coordinator.

The scheduled recovery is assigned to the independent **Eventra Workflow
Watcher** Agent. It is not a member of `Eventra Local Delivery`, runs with
concurrency 1 (`--max-concurrent-tasks 1`), and does not receive backend
environment values; only Backend Engineer and Integration QA receive that
custom environment. The Agent uses the Multica 0.4.34-compatible CSV-safe
string filter for `eventra.workflow.version=1`: the JSON string is encoded as
one quoted CSV field with doubled internal quotes before it is passed through
argv. This preserves the server-side string comparison while retaining the
two-Project scope and bounded recovery behavior.

Provisioning migrates the existing scheduled Autopilot in place to this Agent:
it preserves the existing Autopilot and trigger IDs, updates only the
assignee and any desired drift, and leaves the existing schedule trigger
(`*/30 * * * *` in `Asia/Shanghai`) authoritative. After applying, use these
read-only checks with the IDs printed by the provisioner:

```bash
multica agent get WATCHER_AGENT_ID --output json
multica squad member list SQUAD_ID --output json
multica autopilot get AUTOPILOT_ID --output json
```

The Agent must appear in the first result, must not appear in the Squad member
list, and must be the Autopilot's `assignee_id` in the third result. Confirm
that the schedule trigger ID in that Autopilot response remains unchanged.
Production remains outside this boundary: the Watcher never merges, deploys,
waives a gate, or edits business code, and production deployment remains
human-triggered.

For the one-time existing PRO-35 recovery, first merge and apply these updated
instructions and prove the second apply reports zero mutations. Reread PRO-35,
PRO-36, PR #6, current head, and runs, then invoke exactly once:

```text
multica issue rerun PRO-35 --output json
```

The updated Delivery Lead must recover the existing PRO-36 assignment and PR;
it must not create another child or PR. Do not manually mark PRO-36 PASS. The
Frontend Engineer records the real build result, posts evidence, and calls
`finish-phase`. Native Stages then drive review, QA, bounded repair, merge, and
local smoke. Production remains untouched.

## Repository knowledge loop pilot runbook

This pilot adds one independent **Eventra Knowledge Curator** Agent and one
run-only **Eventra · Knowledge Curator** Autopilot to the two existing Eventra
Projects. The Curator is outside `Eventra Local Delivery`, receives no backend
environment, and runs at `17 2 * * *` in `Asia/Shanghai`. Each scheduled or
manual control pass handles at most one candidate. Candidate production and
curation are asynchronous: an absent, rejected, paused, or delayed candidate
does not block delivery. The existing **Eventra · Stalled Work Watcher remains
active** and retains its own schedule and recovery authority.

This is an Eventra-only trial. The generic `multica-multi-repo-delivery`
remains unchanged; do not copy pilot IDs, paths, schedules, or state into it. The
Curator can create one knowledge Issue after validated evidence, while the
assigned Curator task may prepare a knowledge-only pull request. It cannot
approve, merge, deploy, edit business code, or change delivery state.

### Read-only preflight

From the frontend control worktree, first verify both repositories and inspect
pending evidence. These commands do not mutate Multica or GitHub:

```bash
python3 -B -m tools.multica.knowledge verify \
  --frontend-root FRONTEND_ROOT \
  --backend-root BACKEND_ROOT
python3 -B -m tools.multica.knowledge scan \
  --project-id FRONTEND_PROJECT_ID \
  --backend-project-id BACKEND_PROJECT_ID
python3 -B -m tools.multica.knowledge plan \
  --project-id FRONTEND_PROJECT_ID \
  --backend-project-id BACKEND_PROJECT_ID
```

Then audit the live contract shapes and render the desired reconciliation. Both
commands are read-only; scalar-free audit output is evidence about structure,
not permission to infer missing IDs:

```bash
python3 -m tools.multica.contract_audit \
  --runtime-id RUNTIME_ID \
  --daemon-id DAEMON_ID
python3 -m tools.multica.provision \
  --runtime-id RUNTIME_ID \
  --daemon-id DAEMON_ID
```

Stop if either Eventra Project, either exact repository resource, the existing
Watcher, or the five-member Squad cannot be resolved unambiguously. A valid dry
run proposes only the missing or drifted Curator objects, preserves Watcher and
trigger IDs, leaves the Squad at five members, and makes no operational-agent
environment change.

### Approval, apply, and authoritative reread

Stop for **explicit live-mutation approval** before adding/updating the Curator,
running `curate --apply`, triggering an Autopilot, creating a knowledge Issue or
pull request, or changing an Autopilot status. After that approval, reconcile
once while preserving the existing backend environment in-process:

```bash
python3 -m tools.multica.provision \
  --runtime-id RUNTIME_ID \
  --daemon-id DAEMON_ID \
  --apply \
  --reuse-backend-env
```

Use only IDs returned by that apply and reread the result:

```bash
multica agent get KNOWLEDGE_CURATOR_AGENT_ID --output json
multica squad member list SQUAD_ID --output json
multica autopilot get KNOWLEDGE_CURATOR_AUTOPILOT_ID --output json
multica autopilot get WATCHER_AUTOPILOT_ID --output json
```

Require the Curator Agent to be absent from the Squad, the Curator Autopilot to
be active/run-only and assigned to it, the schedule to remain `17 2 * * *` in
`Asia/Shanghai`, and the Watcher IDs/schedule to be unchanged. Re-run the normal
apply without an environment-mode flag; it must report a `mutation_count` of
`0`.

### One bounded curation pass and PR handoff

The scheduled description invokes this exact mutation after approval:

```bash
python3 -B -m tools.multica.knowledge curate \
  --project-id FRONTEND_PROJECT_ID \
  --backend-project-id BACKEND_PROJECT_ID \
  --curator-agent-id KNOWLEDGE_CURATOR_AGENT_ID \
  --apply
```

For a manual scenario, trigger only the known Curator Autopilot and then reread
its run history and the affected Issue metadata:

```bash
multica autopilot trigger KNOWLEDGE_CURATOR_AUTOPILOT_ID --output json
multica autopilot runs KNOWLEDGE_CURATOR_AUTOPILOT_ID --limit 5 --output json
```

One pass may create at most one deterministic knowledge Issue. The assigned
Curator rereads source evidence, changes only the repository knowledge
allowlist, runs `check-change`, opens one source-linked PR, records the canonical
`eventra.knowledge.pr` state, and stops at `pr_open` for human review. A human
may reject or merge; the Curator never does either. `verified` is recorded only
after a read-only PR observation and exact merged-SHA index verification.

### Pause, rollback, and continuity

The safe operational rollback is to pause only the Curator after explicit
approval, not to delete Agents, Issues, comments, PRs, triggers, or Git history:

```bash
multica autopilot update KNOWLEDGE_CURATOR_AUTOPILOT_ID \
  --status paused \
  --output json
multica autopilot get KNOWLEDGE_CURATOR_AUTOPILOT_ID --output json
```

Confirm it is paused and that **Eventra · Stalled Work Watcher remains active**.
Delivery, Stage barriers, local smoke, and Watcher recovery continue normally;
new candidates remain durable and pending. Resume only after separate approval
with `multica autopilot update KNOWLEDGE_CURATOR_AUTOPILOT_ID --status active
--output json`, reread it, and let oldest-first bounded processing continue.
Repository rollback, PR revert, production deployment, and candidate deletion
are not automatic rollback actions.

### Pilot measures

For every scenario record: retrieved-entry relevance, Context Receipt count,
candidate count, candidate acceptance rate, duplicate rate, human review
correction rate, stale-entry rate, candidate-to-reviewed-knowledge latency,
Curator failure/retry count, and business delivery delay. Compare delivery
lead time with the Curator active and paused. A useful pilot improves future
retrieval without increasing delivery delay, leaking sensitive data, or
creating duplicate Issues/PRs.

The eight knowledge-specific scenarios and expected evidence are in
[the pilot Issue runbook](../../docs/multica/pilot-issues.md#repository-knowledge-loop-scenarios).

## Pilot dispatch and evidence runbook

Use [the pilot Issue bodies](../../docs/multica/pilot-issues.md) to exercise
the `frontend-only`, `backend-only`, and `cross-stack` routing modes. For each
pilot, create one parent Issue, bind it to `Eventra Local Development`, assign
it to `Eventra Local Delivery`, and move it from backlog to todo. The Delivery
Lead keeps frontend children there, routes backend children to `Eventra Backend
Local Development`, keeps the parent in progress, and records all evidence
before closure.

Each implementer hands the Delivery Lead its repository, branch, PR, changed
paths, commands and exit codes, concerns, and an exact SHA. The Delivery Lead
sends that immutable SHA (or the cross-stack SHA pair) to Independent Reviewer
and Integration QA. Their decisions apply only to those exact SHAs; any new
commit repeats the affected gate. The full per-pilot test matrix, frozen API
contracts, cross-stack `buildVersion` variant, and copy-ready Issue text are in
the linked runbook.

Automatic merge is permitted only when the final exact SHA set has passed the
required tests, builds, repository checks, independent review, Integration QA,
and mergeability checks. Cross-stack PRs wait for one coordinated gate decision
and merge in API-compatible order; a partial merge stops and escalates. After
merge, record merged local smoke evidence. Local services may be started for
that smoke test, but production deployment remains human-triggered and is never
automatic.
