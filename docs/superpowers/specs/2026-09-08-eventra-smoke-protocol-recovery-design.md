# Eventra Smoke Protocol Recovery Design

## Goal

Make post-merge smoke creation race-free and scope-aware, then recover
PRO-127 through the existing one-time smoke retry path without changing
business code or deploying production.

## Observed failures

The PRO-127 Stage 5 execution exposed two independent protocol defects.

First, `execute-parent-smoke` promoted the smoke child before committing the
parent action and clearing `eventra.workflow.smoke_reservation`. The assigned
agent could therefore change the child status or completion metadata while the
executor was performing stable double reads. PRO-134 did exactly that, causing
the executor to fail closed with `smoke reservation authority changed during
read` after the child had already started.

Second, the generic Integration QA instructions treated every smoke as a full
frontend/backend readiness check and instructed QA to detach the Multica-managed
task worktree. For a frontend-only parent, no backend service handoff exists, so
`npm run smoke:local` failed on port 8080. Detaching the managed task worktree
also made its delivered HEAD differ from its assigned branch, triggering the
runtime branch-integrity guard.

## Scope-aware smoke contract

Smoke behavior follows the parent classification and never invents an
unaffected candidate SHA.

- `frontend-only`: fetch and verify the merged frontend PR ref, use a temporary
  detached verification worktree at the exact frontend SHA, run the focused
  regression/local-contract checks required by the issue, start the frontend
  with `npm run dev:local`, wait for port 3000, and verify the frontend HTTP
  route. Backend port 8080 is not a prerequisite.
- `backend-only`: fetch and verify the merged backend PR ref, use a temporary
  detached verification worktree at the exact backend SHA, start the backend,
  and run `scripts/smoke-local.sh` for health and OpenAPI.
- `cross-stack`: retain the existing exact backend service handoff. QA verifies
  both candidate SHAs, requires the Backend Engineer-owned service on port
  8080, starts the exact frontend candidate, and runs the full
  `npm run smoke:local` check.

Every path records exact candidate SHAs, fetch evidence, commands and exit
codes, Context Receipt, owned process identities, and cleanup. Smoke remains a
local-development gate and never authorizes production deployment.

## Managed-worktree safety

QA must not switch, reset, clean, or detach the Multica-managed task worktree.
Exact-SHA execution happens in an external temporary detached worktree created
from the authoritative repository. The temporary worktree is removed only by
its creator after owned processes stop. The managed task worktree must finish
on the commit and branch supplied by Multica, so the runtime branch-integrity
check remains meaningful.

## Smoke creation transaction

The executor retains the reservation while child identity and canonical
metadata are incomplete. Once the child is fully initialized, it commits the
parent action in this order:

1. Set the parent `next_stage` and `last_action` (and retry-consumption metadata
   when applicable).
2. Verify the exact parent/child/PR/assignment authority while the child remains
   in `backlog` with no run.
3. Delete the reservation and verify the committed smoke assignment.
4. Promote the child and observe either a newly created run or a non-backlog
   status.

No stable authority read is performed after the agent is allowed to mutate the
child. A fast agent completion is therefore normal phase activity, not a
transaction conflict.

If the process stops after step 3 but before step 4, replaying the same action
key recognizes the exact committed backlog child and starts it. Replays no-op
when the exact child already has an active or terminal run. Conflicting child
identity, metadata, assignment, PR state, extra children, or an unexplained
parent action still fails closed.

## Recovery of PRO-134 and PRO-127

Recovery preserves the existing immutable evidence instead of rewriting it.

1. Install the tested Integration QA instruction update in the Eventra pilot.
2. Use the corrected executor reconciliation to clear only the exact stale
   PRO-127 smoke reservation after verifying that Stage 5 PRO-134, the parent
   action, `next_stage=6`, PR #18, and candidate SHA all match.
3. Restore PRO-134 to a mutable status without starting a run, then record its
   existing agent-authored evidence as canonical `done + blocked` smoke
   completion. The blocker has no responsible repository.
4. Move PRO-127 to `blocked`, publish the exact member-authored one-time retry
   authorization, and require `plan-parent` to return only
   `retry_smoke_stage`.
5. Execute that returned action key once. The retry uses the frontend-only
   smoke path and leaves the managed task worktree branch untouched.
6. On an exact PASS, run `finish-parent PRO-127`. On another non-PASS, leave the
   parent blocked and stop; no second retry or alternate Gate is permitted.

## Tests

Control-plane tests must cover:

- an agent status transition immediately after promotion does not strand a
  reservation;
- a crash after reservation clear but before promotion replays by starting the
  same backlog child exactly once;
- active and terminal committed smoke children replay as no-ops;
- conflicting committed child provenance still blocks without mutation;
- smoke instructions select frontend-only, backend-only, and cross-stack
  commands correctly;
- QA instructions explicitly preserve the managed task worktree branch.

The complete `tools/multica/tests` suite must pass before any live update. A
provision dry-run must show only the intended Integration QA instruction drift
before applying it.

## Non-goals

- No changes to PR #18 or Eventra business behavior.
- No Backend implementation changes.
- No production deployment, release, or production data access.
- No genericization into `multica-multi-repo-delivery` during this pilot fix.
- No additional repair, review, QA, or alternate smoke path outside the
  existing Stage 5 result and single authorized retry.
