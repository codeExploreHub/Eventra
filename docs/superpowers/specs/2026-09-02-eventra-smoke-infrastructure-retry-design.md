# Eventra Smoke Infrastructure Retry Design

## Status and scope

This design adds one auditable recovery path to the Eventra-only Multica pilot.
It does not change the generic `multica-multi-repo-delivery` package, production
deployment, business repair rounds, or the meaning of an existing phase result.

The triggering incident is PRO-116. Its implementation, review, QA, merge, and
runtime smoke behavior succeeded at backend SHA
`c7b9a38a2d05ba05eec6b16c83184653aefba750`, but PRO-120 correctly finished as
`done + blocked` because a transient GitHub git-transport outage prevented the
mandatory fresh PR-ref fetch and `FETCH_HEAD` provenance check. The current
planner can only block the parent after a non-PASS merged Smoke and provides no
legal recovery transition after the external dependency recovers.

## Goal

Permit exactly one human-authorized Smoke retry when the previous post-merge
Smoke was blocked only by external infrastructure, while preserving immutable
evidence, exact-SHA provenance, idempotent Stage creation, and fail-closed
behavior.

## Non-goals

- Do not reinterpret or overwrite PRO-120, its evidence comment, or any other
  completed phase.
- Do not accept GitHub PR API data plus a local object as a replacement for the
  required fresh fetch.
- Do not repair business code, change the merged candidate SHA, reopen or alter
  the merged PR, deploy, release, or touch production.
- Do not create an unlimited general retry framework in this pilot.
- Do not copy this Eventra-specific recovery into
  `multica-multi-repo-delivery` during the pilot.

## Considered approaches

### A. Member-authorized retry Stage — selected

Add a distinct `retry_smoke_stage` planner action. A parent-scoped immutable
member comment authorizes one retry and binds it to the exact source Smoke,
source evidence UUID, candidate SHA map, and retry ordinal. The existing Smoke
executor is generalized to reserve, create, initialize, promote, and reconcile
both initial and retry Smoke Stages.

This keeps failed evidence immutable and gives the state machine an explicit,
auditable transition. It requires the most state-machine work but retains the
existing safety model.

### B. Degraded provenance acceptance — rejected

Treat a successful runtime smoke, a local exact object, and GitHub PR API head
as sufficient when git transport is unavailable. This would be smaller, but it
silently weakens the exact-fetch gate precisely when provenance is least
observable.

### C. Manual parent override — rejected

Move PRO-116 directly to `done` after an operator reruns smoke. This is fast but
bypasses `plan-parent`, loses machine-readable lineage, and makes the handbook
contract weaker than the automation it is intended to govern.

## Authorization contract

The retry is opt-in and parent-scoped. A member posts one immutable comment on
the blocked parent with canonical JSON containing exactly:

```json
{"candidate_shas":{"backend":"c7b9a38a2d05ba05eec6b16c83184653aefba750"},"granted_smoke_retry":1,"source_evidence_comment_uuid":"01a0622e-72e3-7660-9fcd-a806c07a5c0f","source_smoke":"PRO-120"}
```

For other parents, the repository keys and SHA values reflect that parent's
classification. The parent metadata points to the comment through:

```text
eventra.workflow.smoke_retry_authorization_comment=COMMENT_UUID
```

The loader accepts the authorization only when all of the following hold:

- the comment is a root comment on the exact parent;
- `author_type=member` comes from Multica, never caller-supplied prose;
- the body is byte-for-byte canonical JSON with the exact key set;
- `granted_smoke_retry` is the integer `1`;
- `source_smoke`, source evidence UUID, and candidate SHA map equal current
  authoritative state;
- the authorization UUID has not already been consumed.

The executor records consumption as:

```text
eventra.workflow.smoke_retry_authorization_consumed=COMMENT_UUID
```

This pilot allows only one retry. A second retry, another authorization, a
reused authorization, or a non-member comment fails closed.

## Planner state transition

`ParentDecision.kind` gains `retry_smoke_stage`. It is returned only when:

- workflow version is `2`;
- the parent status is `blocked`;
- merge state is `merged`;
- there is no Repair or Smoke reservation;
- the current Stage contains exactly one canonical Smoke child;
- that child is `done + blocked`, has no responsible repository, and carries a
  valid evidence comment;
- candidate SHAs still equal every managed merged PR head;
- the initial Smoke action and all assignment authority remain canonical;
- no prior Smoke retry has been consumed or created; and
- the exact member authorization above is valid.

Without a valid authorization, the existing `block_parent` decision remains.
A Smoke `fail`, malformed evidence, a responsible repository, candidate drift,
PR drift, assignment drift, or conflicting history can never enter this path.

The action identity is deterministic and includes the source Stage and
authorization UUID:

```text
2:PRO-116:retry_smoke_stage:0:backend:-:c7b9...:next-stage:4:source-stage:3:authorization:COMMENT_UUID
```

## Executor and reservation

`execute-parent-smoke --expected-action-key ACTION_KEY` remains the sole Smoke
creation command. It accepts either `create_smoke_stage` or
`retry_smoke_stage`; no manual child creation command is added.

For a retry, the reservation additionally binds:

- `mode=retry`;
- source Smoke Stage, child identifier, result, and evidence UUID;
- the original PASS Gate Stage;
- the member authorization UUID and prior consumed value;
- the exact merged candidate map and managed PR URLs;
- the previous parent status, next Stage, and last action.

The executor uses the existing reservation/prefix-reconciliation pattern:

1. load the parent and planner decision twice;
2. write and reread the deterministic Smoke reservation;
3. create at most one backlog retry child in the next Stage;
4. persist and reread the complete canonical child metadata prefix;
5. move the parent from `blocked` to `in_progress` with `--no-start`;
6. promote the retry child and require exactly one active Integration QA run;
7. set `next_stage`, `last_action`, and consumed authorization;
8. clear the reservation last;
9. reread the converged parent, child, PR, assignment, evidence, and run state.

Every acknowledgement-loss boundary is recoverable by replaying the exact
action key. A retry never creates a duplicate child and never overwrites
conflicting metadata. A reservation permits only the expected transition from
blocked to in-progress; any other parent status drift blocks reconciliation.

## Retry child provenance

The retry child remains phase kind `smoke`, attempt `0`, target `suite:smoke`,
role `integration_qa`, and carries the unchanged candidate SHA map. Its
`eventra.phase.creation_action` is the deterministic `retry_smoke_stage` action
key. The child description names the source Smoke and evidence UUID but does
not copy or alter the old evidence body.

Smoke assignment validation accepts an initial or retry action only after
parsing its exact action shape. For a retry action it also verifies the previous
Stage is the authorized blocked Smoke and the earlier Gate Stage remains a
canonical exact-SHA PASS set.

The Integration QA contract does not change its quality threshold: it must
fresh-fetch the PR ref, verify `FETCH_HEAD`, use a clean detached exact-SHA
worktree, run repository-standard smoke, clean up only owned processes and
temporary worktrees, post a fresh Context Receipt, and finish through
`finish-phase`.

## Completion and failure behavior

- Retry Smoke PASS: `plan-parent` returns `complete_parent`; `finish-parent`
  verifies the latest retry Smoke twice and moves the parent to `done`.
- Retry Smoke BLOCKED or FAIL: `plan-parent` returns `block_parent`; no second
  retry or business Repair Stage is authorized.
- Existing Knowledge Loop metadata remains independent. PRO-116 keeps
  `eventra.knowledge.version=1` and `eventra.knowledge.status=none` unless the
  new execution evidence produces a valid candidate through the existing
  candidate protocol.
- No path in this design triggers deployment or production mutation.

## Files and interfaces

Expected Eventra pilot changes:

- `tools/multica/workflow.py`: new authorization fields, action parser,
  planner decision, generalized Smoke reservation/executor, assignment and
  completion validation, and CLI-compatible output.
- `tools/multica/tests/test_workflow.py`: planner, authority, idempotency,
  interruption, replay, terminal completion, and rejection cases.
- `tools/multica/instructions/delivery_lead.md`: member authorization and exact
  executor procedure.
- `tools/multica/instructions/integration_qa.md`: retry child provenance and
  unchanged exact-fetch requirement.
- `tools/multica/README.md`: operator runbook and recovery example.
- `docs/multica/pilot-issues.md`: pilot scenario and required evidence.
- `tools/multica/tests/test_operator_docs.py`: executable documentation
  contract coverage.
- `docs/multica/eventra-multica-automation-overview.md`: update only if the
  currently untracked user document is intentionally brought into this change;
  otherwise leave it untouched.

## Testing strategy

Use strict TDD. Each behavior begins with a failing unit test that names the
break it catches, followed by the minimal implementation and focused green run.
The suite must cover:

- exact eligible PRO-116-shaped state returns `retry_smoke_stage`;
- absent, foreign-author, malformed, stale, reused, or second authorization
  blocks without an action key;
- Smoke `fail`, responsible repository, candidate/PR/assignment/evidence drift,
  non-blocked parent, and non-terminal child never authorize retry;
- the executor creates one Stage and replays with zero duplicate effects;
- every reservation, metadata, parent-status, promotion, consumed-auth, and
  clear-reservation acknowledgement-loss boundary converges safely;
- conflicting or future children are never adopted;
- `finish-phase` accepts the canonical retry child and rejects a forged one;
- retry PASS completes the parent, while retry non-PASS blocks permanently;
- Watcher recovery neither invents authorization nor bypasses the blocked
  parent;
- operator docs describe only the supported command and exact authorization
  contract.

Run focused workflow and documentation tests first, then the full
`tools.multica` test suite and repository-standard frontend checks. No live
Multica mutation occurs until tests pass and the live reconciliation plan is
reviewed.

## Live PRO-116 rollout

After code and instructions pass review:

1. reread PRO-116, PRO-120, PR #6, Squad/Project assignments, and port/process
   state;
2. post the exact member authorization comment on PRO-116 and store its UUID in
   parent metadata;
3. run `plan-parent PRO-116` and require exactly `retry_smoke_stage`;
4. run the same `execute-parent-smoke` command with the returned action key;
5. wait for the new Integration QA child and inspect its immutable evidence;
6. require `done + pass`, then let Delivery Lead run `finish-parent`;
7. verify PRO-116 is `done`, knowledge metadata remains coherent, PR #6 remains
   merged at the same head, no local service remains, and no deployment ran.

If any authority changes, stop with the parent blocked. Do not manually edit a
child result or bypass the planner.
