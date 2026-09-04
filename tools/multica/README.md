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

That command is a dry run by default. Eventra provision dry-run is read-only; a
later `--apply` is a separate explicit authorization with fresh authoritative
preflight and revalidation. Eventra provision does not accept, bind, or validate
a dry-run plan ID or hash. The reusable package's exact-plan hash boundary is a
separate Task 8 concern. Review dry-run output before authorizing apply; neither
operation licenses an invented or extended reconciliation:

The dry-run response is deterministic JSON derived from authoritative observed
state, not a static desired inventory. Its stable top-level schema is:

```json
{
  "actions": [],
  "mode": "dry-run",
  "mutation_count": 0,
  "preconditions": [],
  "summary": {
    "by_action": {},
    "by_kind": {},
    "blocked": false,
    "noop": true,
    "total": 0
  }
}
```

Each changed item in `actions` has `action`, `kind`, `key`, `name`, `id`,
`operation`, and `changes`; missing object IDs are rendered as `new`. Actions
are deterministically ordered across approved skills, Agents, skill bindings,
the Squad and members, Projects, worktree resources, Autopilot, and trigger.
Environment reconciliation reports environment state as only `missing`, `set`,
or `update`; it does not print environment key names or values. An empty
`actions` array with `summary.noop: true` proves that this fresh observed
preflight found no intended change. A non-empty plan is advisory input for
review: `--apply` performs its own fresh preflight and is not bound to the prior
dry-run response.

When no explicit environment authority is available, dry-run does not invent
environment mutations. It returns `actions: []`, `summary.blocked: true`, and
one sanitized `backend_environment` precondition: `requires_input` when no
valid recipient environment exists, `conflict` when existing valid recipient
environments disagree, or `requires_reuse` when a valid environment could be
reused but the recipients are not already equal. Default mode never renders an
executable environment action from inferred authority. Rerun dry-run with
`--reuse-backend-env` to choose Backend Engineer explicitly, review the exact
recipient update, and use that same environment-authority mode for apply. When
both recipients already have the same valid environment, default dry-run may
report a normal converged plan without choosing reuse.

```bash
python3 -m tools.multica.provision --runtime-id RUNTIME_ID --daemon-id DAEMON_ID --apply
```

Use `--prompt-backend-env` only when Backend Engineer and Integration QA need
the local backend environment. It prompts for the secret without echoing it
and passes it only through those agents' custom environment. Do not put a
secret in shell history, Issue text, logs, pull-request descriptions, or a
tracked environment file. Prompt mode requires `--apply` and dry-run rejects it
before any prompt, environment read, or Multica preflight. Dry-run may use
`--reuse-backend-env`: this is an explicit, read-only choice of the existing
Backend Engineer environment as comparison authority and never prints its keys
or values. Use the same environment-authority mode for the reviewed dry-run and
the later fresh `--apply`; a changed mode requires another dry-run review.

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
  --daemon-id DAEMON_ID \
  --reuse-backend-env
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
`verified_ids` contains only those explicit checks. A historical
`last_verified_sha` match, whether in the same repository or another one,
never marks a claim verified for the current task.
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

Version 2 is the active workflow metadata contract. A Gate Stage is a strict
fan-in: Delivery Lead waits for every current Gate Stage child to become
terminal, then invokes `plan-parent`. Core/plan-parent is the sole fan-in,
canonical FailureBundle producer, and decision authority; it returns canonical
JSON with the exact `failure_bundle` and digest, but does not create a Stage or
child. Delivery Lead is the sole execution actor: it validates that JSON and
uses the exact returned `failure_bundle` and digest without reconstruction to
execute its one allowed action. The decision revalidates the
version-2 parent and Stage, exact candidate SHA map, current child identities,
canonical managed PR URLs, verdict evidence UUIDs, and non-PASS canonical HTTPS
evidence-comment URLs. Before a Gate completion is accepted, the verdict Agent
must first post that evidence comment on the exact Gate child Issue. Planning,
completion replay, repair execution, and Watcher recovery reread the
child-scoped comment identity twice around an exact parent/child metadata read;
the UUID, child Issue, Agent author, and current child assignee must agree.
Comment content is not consumed by workflow logic. A non-PASS URL remains
product-neutral HTTPS, but its normalized path must end exactly in
`/comments/<evidence UUID>` and cannot contain credentials, a port, query,
fragment, or traversal. Any malformed JSON or bundle, version mismatch, stale
or deleted evidence comment, stale child, or PR drift is a human-visible block.

Every direct version-2 parent command uses the frontend Project as the sole
control Project and requires the exact provisioned `Eventra Local Delivery`
Squad assignment, Delivery Lead leader, and five-Agent canonical membership.
The same read-only authority envelope is used by planning, repair/smoke
execution, phase and parent completion, and Watcher recovery, and is reread at
each mutation boundary. Backend repository children still route to the backend
Project; that does not authorize a parent in the backend Project.

An implementation assignment is usable only after Delivery Lead creates it in
backlog and persists the canonical Stage action, single repository target,
Engineer role, exact Engineer/Project identity, and its creation provenance and
initial base candidate SHA before starting. The first authoritative
implementation completion establishes the canonical managed PR. From then on
replay, repair, Gate, and merge require that PR identity and head to remain
exact; a different URL or out-of-band head is conflicting authority and blocks.
Planner, `finish-phase`, replay, and Watcher reuse the immutable assignment
authority; an empty/manual/foreign child cannot advance merely by carrying a
PASS envelope.

Current version-2 Gate membership is typed and exact: every affected repository
has one single-repository Review and one single-repository QA child, while a
cross-stack Gate additionally has one `integration_qa` child for the complete
SHA pair. Each child carries the canonical Gate creation action, typed target,
and assigned role; a combined child is never expanded into several identities.

Reviewer and QA finish only a structured verdict. Every non-PASS completion
declares the legal repair owner(s), evidence UUID, and canonical HTTPS
`--evidence-comment-url`; PASS and smoke omit both failure-only fields. They do
not route a failure to an Implementer. After complete Gate Stage fan-in,
Delivery Lead uses Core's exact returned immutable FailureBundle and digest
without reconstruction, then executes one repair Stage with exactly one current
child per legal owner. The sole operational repair path is:

```text
python3 -B -m tools.multica.workflow execute-parent-repair PRO-M --expected-action-key ACTION_KEY
```

Do not create repair children manually. The helper freshly rereads and replans,
then reserves `eventra.workflow.repair_reservation`, creates owner children in
backlog, and persists/rereads `eventra.repair.creation_action`,
`eventra.repair.failure_bundle_digest`, sorted failure evidence UUIDs, exact
stage/round, existing managed PR, `eventra.repair.authorizing_comment_uuid`, and
the canonical immutable `eventra.repair.source_candidates` rejected-SHA map. It commits parent attempt,
`next_stage`, last action, and consumed authorization provenance, then starts
the assigned agent only for the exact children. Each child receives a
deterministic repair handoff containing the bound parent/action/bundle, source
and next Stage, candidate and rejected SHAs, managed PR, source children, and
assigned canonical evidence. The reservation is cleared last. `mutation_count`
reports authoritatively observed effects, including committed effects after a
lost acknowledgement. Exact retries resume or no-op, while conflicts and
partial mismatches block visibly. If the process stops after an exact reserved
backlog child is created but before all canonical metadata is written, retry
quarantines that child from Stage fan-in, verifies its immutable issue identity,
empty run set, current reservation, source evidence, authorization, and exact
sorted metadata prefix, then writes only the missing suffix. Conflicting or
extra metadata is never overwritten. This recovery relies on the same single
serialized Delivery Lead boundary; it does not add CAS semantics.

A version-2 repair PASS must replace its owned rejected SHA; an unchanged SHA
is not a successful repair. `finish-phase` may change only that child's owned
`eventra.phase.sha.*` once and preserves every `eventra.repair.*` field exactly.
After all repair owners pass, Delivery Lead copies the exact completed
replacement map into the parent candidate metadata, leaving untouched
repositories at their source values, and invokes `plan-parent` again. Planning
before this copy blocks with an explicit wait; it never adopts a child SHA.
Fresh gates require both the copied parent candidates and authoritative managed
PR heads to equal the completed replacements.
This Eventra-local adapter is safe only under the provisioned single serialized
Delivery Lead (`max_concurrent_tasks=1`); it is not generic CAS or transaction
safety.

After an exact Gate PASS and verified automatic merge, Delivery Lead must not
create a smoke child manually. It executes only the exact Core action:

```text
python3 -B -m tools.multica.workflow execute-parent-smoke PRO-M --expected-action-key ACTION_KEY
```

The smoke executor revalidates the completed Gate, managed merged PRs, exact
merged candidate SHA map, and provisioned Integration QA assignment. It writes
`eventra.workflow.smoke_reservation`, creates one backlog child, persists and
rereads `eventra.phase.creation_action`, `eventra.phase.target=suite:smoke`,
`eventra.phase.role=integration_qa`, and the complete SHA map, then promotes and
starts Integration QA. It commits the exact parent action and clears the
reservation only after verifying the complete effect. Retry resumes a missing
create, canonical metadata prefix, promotion, or lost acknowledgement without
duplicating the child. Conflicting identity, metadata, Gate evidence, assignment,
or merged PR state blocks without overwrite.

Smoke execution is single-flight across local processes and linked worktrees.
The executor takes a nonblocking kernel lease below the repository's shared Git
common directory, keyed by parent and carrying the exact action key. A contender
for the same key returns a non-writing `noop`; a different key fails closed. A
process exit releases the kernel lease, and the next owner explicitly reports
and replaces the stale record. The durable Multica reservation remains the
recovery authority for lost acknowledgements and interruptions; the file lease
never replaces it.

The executor reads the expensive immutable authority envelope twice before the
first effect and again immediately before and after promotion. That envelope
binds the parent/action, exact source-Issue revisions, immutable evidence
comment identities, retry-authorization content and revision, candidate
SHA/merged PR identities, Project, Squad, and agent assignment. Mutation
checkpoints reread mutable parent/child metadata, the exact source revisions,
retry authorization, child/run cardinality, and assignment authority. Expected
`backlog -> todo -> in_progress` and `queued -> dispatched -> running` changes
normalize to one active state; any second active run or other identity drift
still blocks. Deterministic fixtures reduce external reads from `1004 -> 410`
for initial execution, `1293 -> 488` for retry, and `898 -> 324` for
reservation recovery.

### Read-only Multica TLS diagnostic

Before treating a CLI failure as an authentication problem, run:

```text
python3 -B -m tools.multica.workflow diagnose-tls --timeout 15
```

The JSON result distinguishes `unreachable`, `tls_handshake`,
`http_authentication`, `business_timeout`, `business_error`, and `ok`. It first
checks the effective direct/proxy TCP route, honoring `NO_PROXY`, then runs only
the read-only API request `multica workspace list --output json`; response
bodies and credentials are never returned. If the
default probe times out, it repeats that read-only request once with a
process-only `GODEBUG=tlsmlkem=0` environment. Success on only that retry is
classified as TLS compatibility, not credential failure. This covers the known
Multica 0.4.38 / Go 1.26 / local-proxy ML-KEM handshake case without changing a
shell profile, global proxy, or persistent configuration. Go documents the
`tlsmlkem=0` compatibility control and handshake-timeout use case at
https://go.dev/doc/godebug and https://go.dev/doc/go1.24.

### One-time infrastructure-blocked Smoke retry

The Eventra pilot exposes one explicit state-machine transition,
`retry_smoke_stage`, rather than rewriting a failed result or accepting degraded
provenance. It applies only when the parent is blocked, the latest canonical
Smoke is `done + blocked` with no responsible repository, the merged PR heads
still equal the exact merged candidate SHA map, the original Gate is an exact
PASS set, and no retry was previously created or consumed.

A member posts exactly one immutable root comment on the parent. For PRO-116,
the byte-for-byte canonical body is:

```json
{"candidate_shas":{"backend":"c7b9a38a2d05ba05eec6b16c83184653aefba750"},"granted_smoke_retry":1,"source_evidence_comment_uuid":"01a0622e-72e3-7660-9fcd-a806c07a5c0f","source_smoke":"PRO-120"}
```

Store the server-returned comment UUID as
`eventra.workflow.smoke_retry_authorization_comment`, rerun `plan-parent`, and
require exactly `retry_smoke_stage`. Pass its returned action key unchanged to
the existing executor:

```text
python3 -B -m tools.multica.workflow execute-parent-smoke PRO-116 --expected-action-key ACTION_KEY
```

The executor reserves the exact source Smoke/evidence, unchanged candidate
SHAs, merged PRs, original PASS Gate, member authorization, prior parent status,
Stage, and action. It creates one backlog retry child, initializes all canonical
metadata, changes only `blocked -> in_progress` with no parent run, starts one
Integration QA run, records
`eventra.workflow.smoke_retry_authorization_consumed`, and clears the reservation
last. Replaying the same key resumes or no-ops; another key, child, run, comment,
SHA, PR head, assignment, or status fails closed.

The retry must still perform a fresh PR-ref fetch and exact `FETCH_HEAD`
verification in a clean detached worktree. PASS permits normal
`complete_parent`; BLOCKED or FAIL leaves the parent blocked. No second retry,
manual child, result rewrite, deployment, or production mutation is permitted.

```text
PRO-120 done+blocked -> member authorization -> retry_smoke_stage
-> exactly one Stage 4 Smoke -> done+pass -> complete_parent -> PRO-116 done
```

For round 3 the caller supplies only an authoritative parent-scoped comment UUID.
The helper authoritatively rereads the parent thread and accepts only
`author_type=member` with an exact canonical body containing the current bundle
digest and `granted_round=3`; caller-supplied body and author identity are not
trusted. A repair child is usable only
while active and only with that valid bundle and an existing managed PR. Gate
comments, mentions, and completed children are not repair authority. Automatic
repair rounds are exactly 1 and 2. A member comment may authorize only the exact
current FailureBundle's exact next round 3, once. If round 3 fails, block the
parent; do not create another repair child. The Watcher can recover at most one
existing current assignment and cannot create a FailureBundle or dispatch repair.

Completed version-1 metadata is read-only history. An active version-1 parent
must be explicitly migrated to version 2 before any new Stage, gate, repair, or
merge action. Historical inspection example only: read a completed version-1
parent and its recorded evidence without rerunning it or creating a child.

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

For `watch`, the first Project is the sole parent/control Project. The second
Project is only for backend repository children. A parent observed in the
backend Project is never recoverable; exact backend implementation, review, QA,
or repair children retain their repository-specific backend Project authority.

The scheduled recovery is assigned to the independent **Eventra Workflow
Watcher** Agent. It is not a member of `Eventra Local Delivery`, runs with
concurrency 1 (`--max-concurrent-tasks 1`), and does not receive backend
environment values; only Backend Engineer and Integration QA receive that
custom environment. The Agent uses the Multica 0.4.34-compatible CSV-safe
string filter for `eventra.workflow.version=2`: the JSON string is encoded as
one quoted CSV field with doubled internal quotes before it is passed through
argv. This preserves the server-side string comparison while retaining the
two-Project scope and bounded recovery behavior.

Before rerunning a child, the Watcher requires its complete immutable version-2
assignment to match the authoritative current parent: typed phase/role and
repository or suite target, Stage and attempt, Project and Agent, candidate
SHAs and pull request, creation action, and all repair bundle/evidence/round
provenance when applicable. It rereads the same immutable authority after the
rerun. The Watcher never writes parent metadata, status, Stage, or action
history; only the selected child's run/liveness state may change.

A finished Stage does not bypass these checks. Before the Watcher wakes
Delivery Lead, every exact current child must have its canonical typed
assignment and authoritative terminal completion; malformed or drifting
membership returns a zero-mutation noop. The initial empty Stage is recoverable
only from its canonical version-2 parent state.

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
  --frontend-root FRONTEND_ROOT \
  --backend-root BACKEND_ROOT \
  --apply
```

Both roots are explicit because scheduled tasks execute in runtime-managed
worktrees that are not necessarily nested under either repository.

For a manual scenario, trigger only the known Curator Autopilot and then reread
its run history and the affected Issue metadata:

```bash
multica autopilot trigger KNOWLEDGE_CURATOR_AUTOPILOT_ID --output json
multica autopilot runs KNOWLEDGE_CURATOR_AUTOPILOT_ID --limit 5 --output json
```

One pass may create at most one deterministic knowledge Issue. Cross-run
uniqueness relies on the deterministic action-key title, Multica's default
active-duplicate rejection (the command never passes `--allow-duplicate`), and
the Curator Agent's configured concurrency of 1. A loser or lost acknowledgement
is recovered by authoritative search; this is not a generic client-side CAS for
arbitrary callers. The assigned Curator rereads source evidence, changes only the repository knowledge
allowlist, runs `check-change`, opens one source-linked PR, records the canonical
`eventra.knowledge.pr` state, and stops at `pr_open` for human review. A human
may reject or merge; the Curator never does either. `verified` is recorded only
after a read-only PR observation and exact merged-SHA index verification.

Merged-SHA verification reads raw blobs from the target repository's immutable
Git commit tree (with replace objects disabled), not its working directory,
staging area, export filters, or the other repository. The requested commit
must be available locally; HEAD need not still point to it. Only regular files
inside that repository's canonical knowledge directories are accepted. Missing
or invalid indexes, symlinks, ambiguous candidate matches, and unavailable
commits fail closed. This content check does not replace GitHub merge-status
observation or human review.

The `bootstrap_design` exception is frozen to the 12 original seed entries:
full canonical entry fingerprints bind identity, content digest, scope, paths,
provenance, and verification metadata in control code. Original historical
seeds remain readable; new entries or updates (including verification metadata
updates) must migrate to `delivery_evidence`. Do not extend the seed allowlist
as part of documentation-only curation. YAML formatting changes that preserve
the parsed entry are harmless. Canonical knowledge still requires human review.

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

Provisioning or workflow approval never authorizes a Git push, tag, release,
or deployment. Those remain separately authorized actions; development/local
merge keeps its existing automatic-gate policy, while production merge and
deployment remain manual/forbidden.
