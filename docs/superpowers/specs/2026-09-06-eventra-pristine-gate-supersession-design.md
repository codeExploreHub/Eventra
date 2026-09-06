# Eventra Pristine Gate Supersession Design

**Date:** 2026-09-06

**Status:** Approved in conversation; written review pending
**Base:** PR #17 head `21078feb54adbe1c9fece2e692624369c9f204d2`

## 1. Context

The Eventra candidate-refresh v1 protocol deliberately admits only a version-2,
frontend-only parent with one completed Stage 1 implementation child and no later
children. That rule correctly rejected the first live PRO-122 preflight after it
found two already-created Stage 2 gates:

- PRO-124: independent review, `backlog`, revision 1;
- PRO-125: integration QA, `backlog`, revision 1.

Both gates have no runs, comments, phase metadata, or evidence. Their descriptions
bind the stale candidate `e507758673640a5b0e0da5bec479245f5a72a086`, PR #14,
and the original Stage 2 gate action. They therefore cannot review a refreshed
candidate and must not be reused, silently ignored, or represented as PASS/FAIL.

This design adds an explicitly authorized v2 refresh protocol for this narrow
state. It preserves the v1 rejection behavior and makes cancellation of pristine
stale gates durable, inspectable, and recoverable.

## 2. Goals

1. Admit a candidate refresh only when exactly two stale Stage 2 gates are proven
   pristine and match the expected Reviewer and Integration QA assignments.
2. Require a v2 request and exact member grant before either gate can be changed.
3. Cancel both stale gates with `--no-start` under a durable parent reservation.
4. Recover safely from every write-before/write-after failure without duplicate
   cancellation, child creation, run dispatch, publication, or adoption.
5. Preserve Stage 1 evidence and the stale gate history while creating a Stage 3
   refresh and fresh Stage 4 exact-SHA gates.
6. Retain the existing merge hold and all normal review, QA, repair, Smoke, and
   Knowledge Loop requirements after adoption.

## 3. Non-goals

- Cancelling a gate that has started, received a comment, acquired metadata, or
  changed from its original backlog revision.
- Superseding arbitrary later children, repair children, Smoke children, or more
  than one Review/QA pair.
- Reusing the old gates for the new candidate.
- Changing PRO-123 evidence or treating stale gates as PASS, FAIL, or repair input.
- Supporting cross-stack, backend-only, attempt greater than zero, or a second
  refresh generation.
- Modifying Eventra business code, the Backend repository, or the reusable
  `multica-multi-repo-delivery` package.
- Automatically merging PR #17, the supersession implementation PR, or PR #14.
- Modifying live Multica state without a later exact deployment record, staged
  request authorization, member grant, and mutation approval.

## 4. Considered approaches

### 4.1 Recommended: grant-bound durable cancellation

Freeze the two gates into a v2 request, stage the request, wait for an exact member
grant, write a parent reservation, cancel the gates in deterministic order, and
only then create the refresh child. This keeps authorization explicit and permits
prefix recovery after each mutation.

### 4.2 Rejected: cancel while staging the request

This would modify the gates before the runtime member grant exists. A staging
approval is permission to publish the exact request, not permission to cancel two
children, so this approach crosses an authorization boundary.

### 4.3 Rejected: create a replacement parent

A new parent avoids cancelling the stale gates but splits PRO-122 provenance,
leaves PR #14 attached to an obsolete delivery history, and does not validate the
failure mode that motivated this pilot.

## 5. Protocol versioning

### 5.1 Explicit opt-in

The read-only command gains an explicit flag:

```text
python3 -B -m tools.multica.workflow plan-refresh PRO-122 \
  --prerequisite-pr PR_URL \
  --control-tool-sha SHA \
  --supersede-pristine-gates
```

Without the flag, `plan-refresh` uses v1 and continues to reject any later child.
The tool never selects v2 merely because pristine-looking gates are present.

### 5.2 v1 compatibility

The existing `eventra-candidate-refresh-request-v1` and grant block retain their
exact schemas and semantics. Existing v1 request bytes, digests, action keys,
metadata prefixes, Stage 2 refresh, and Stage 3 fresh gates do not change.

### 5.3 v2 request

The v2 payload uses `schema_version=2`, `refresh_stage=3`,
`fresh_gate_stage=4`, and `refresh_generation=1`. It adds one exact
`supersession` object:

```json
{
  "gate_stage": 2,
  "mode": "cancel-pristine-gates-v1",
  "gates": [
    {
      "role": "independent_reviewer",
      "id": "server UUID",
      "identifier": "PRO-N",
      "revision": 1,
      "status": "backlog",
      "authority_digest": "sha256"
    },
    {
      "role": "integration_qa",
      "id": "server UUID",
      "identifier": "PRO-N",
      "revision": 1,
      "status": "backlog",
      "authority_digest": "sha256"
    }
  ]
}
```

The gate array is in role order: `independent_reviewer`, then `integration_qa`.
The top-level request digest binds this object, the full stable snapshot authority,
the parent comment manifest, source evidence, assignments, PR, prerequisite,
current base tip, control-tool SHA, and Git version.

The v2 assignment projection additionally binds the authoritative Reviewer and
Integration QA agent UUIDs. A v1 assignment remains byte-for-byte unchanged.

The v2 action key keeps workflow version 2 but includes the dynamic refresh stage,
refresh protocol version, and request digest:

```text
2:PARENT:create_refresh_stage:0:frontend:SOURCE:next-stage:3:refresh:2:DIGEST
```

### 5.4 Human-visible preview

`plan-refresh` remains read-only and returns `mutation_count=0`. For v2 it also
returns `supersession_preview`, containing each gate identifier, role, title,
status, revision, and stale candidate SHA. The canonical v2 request and grant
blocks are rendered separately; the preview is informational and cannot replace
the digest-bound request.

The v2 request and grant use distinct fenced-block names so a v1 parser cannot
interpret them as authority:

```text
eventra-candidate-refresh-request-v2
eventra-candidate-refresh-grant-v2
```

## 6. Pristine-gate authority

Exactly two later children must exist. Each child must satisfy all of the
following on both reads of the freeze snapshot:

- parent UUID, workspace UUID, frontend project UUID, and Stage 2 match;
- assignee type is `agent` and assignee UUID matches its authoritative role;
- roles are exactly one `independent_reviewer` and one `integration_qa`;
- status and status category are `backlog`, revision is exactly 1;
- phase and workflow metadata maps are empty;
- no evidence comment, no comment record of any type, and no execution run;
- title is exactly `PARENT frontend review` or `PARENT frontend QA` for its role;
- description contains the exact parent, PR #14, source candidate SHA, Stage 2
  creation action, and implementation evidence UUID expected from the snapshot;
- every nonvolatile detail field and the complete description are included in the
  per-gate authority digest.

The snapshot must retain a complete comment manifest for every later child rather
than only its evidence comment. Unknown comment shapes, pagination ambiguity,
identity confusion, or unbound child runs fail closed.

Any additional child, duplicate role, unknown field, changed title or description,
custom metadata, nonempty comment manifest, run, non-backlog status, or revision
other than 1 rejects v2 before the first mutation.

## 7. Authorization and deployment authority

The five operator phases remain separate:

1. `plan-refresh --supersede-pristine-gates` produces the frozen v2 request.
2. `stage-refresh-request` writes only the approved pause prefix and request
   comment after a fresh authority comparison.
3. A workspace member publishes the exact v2 grant, or separately authorizes the
   operator to publish it.
4. `execute-parent-refresh` binds the request/grant UUIDs, writes the reservation,
   cancels the gates, and initializes the Stage 3 refresh.
5. `finish-refresh` records preparation; a second `execute-parent-refresh`
   publishes and adopts the candidate.

Conversation text such as “continue” or “符合” is not a runtime member grant.
The grant must reference the exact v2 request digest and parent comment.

Every mutating command requires a trusted deployment record outside the repository.
That record must bind the live profile, workspace UUID, control checkout, exact
merged control SHA, parent identifier, protocol version 2, request digest, action
key, and the two old gate UUIDs. A temporary read-only preflight record cannot be
used for mutation.

The outer deployment record remains schema version 1. Its `mutation_contract`
accepts either the existing exact v1 object or the following exact v2 object; no
partial or extra fields are accepted:

```json
{
  "contract_version": 2,
  "refresh_protocol": 2,
  "parent_identifier": "PRO-122",
  "request_digest": "64 lowercase hex characters",
  "action_key": "exact refresh action",
  "superseded_gate_ids": ["Reviewer UUID", "Integration QA UUID"],
  "comment_create_parent_revision_delta": 1,
  "metadata_change_parent_revision_delta": 1,
  "metadata_same_value_parent_revision_delta": 0,
  "status_no_start_preserves_position": true,
  "status_category_tracks_status": true,
  "start_creates_single_run": true
}
```

The UUID array uses the same role order as the request. Read-only planning accepts
`mutation_contract=null`; every mutating command first proves that a v2 request
matches all five request-specific contract fields. The request/grant comment UUIDs
are not known when the contract is installed and are instead bound by the existing
staging and execution protocol. The current v1 mutation contract remains
byte-for-byte valid and cannot authorize a v2 request.

Live execution can begin only after PR #17 and the supersession implementation PR
are merged. The prerequisite used for the live request must be the merged
supersession implementation PR, or a later explicitly approved cumulative control
PR whose merge commit is an ancestor of the then-current base. No merge SHA is
invented before that merge exists.

## 8. Durable state machine

### 8.1 Staged intent

Staging writes the same conceptual pause prefix as v1, using version `2` and the
v2 request envelope. It does not change either gate. Initial-progress validation
proves every parent revision and comment-manifest change from the exact writes.

### 8.2 Reservation

After request and grant UUIDs are bound, the executor writes a version-2
reservation containing:

- request digest, request UUID, grant UUID, and action key;
- source, PR, prerequisite, control-tool, and parent projection identity;
- both ordered gate records and their authority digests;
- `refresh_stage=3`, `fresh_gate_stage=4`;
- parent status category and position needed for recovery;
- refresh-child fields already used by v1;
- `state`, one of `reserved`, `review_cancelled`, `gates_cancelled`,
  `child_initialized`, `child_dispatched`, `candidate_registered`, `published`,
  or `adopted`.

The reservation is persisted and read back before any gate status write.

### 8.3 Gate cancellation

Cancellation order is deterministic:

1. independent Reviewer;
2. Integration QA.

For each gate the only accepted progress states are:

| Progress | Status | Status category | Revision | Other bound authority |
|---|---|---|---:|---|
| not cancelled | `backlog` | `backlog` | 1 | identical to request |
| cancellation persisted | `cancelled` | `cancelled` | 2 | identical to request except server activity timestamps |

The executor calls `issue status GATE cancelled --no-start`. It re-reads the
complete authority after the call. A write-before failure leaves the first row; a
write-after/ACK-loss failure produces the second row and resumes without another
status write. Any other observation blocks.

After each successful cancellation, the reservation state is advanced and read
back. A crash between status persistence and reservation advancement is recovered
from the gate status and exact revision. A later run advances the missing
checkpoint without re-cancelling the child.

If only the first gate is cancelled and the second gate changes externally, the
workflow remains visibly blocked with the partial state. It does not roll back the
first gate because recreating a backlog authorization surface would hide history.

### 8.4 Refresh initialization

Only `gates_cancelled` may create a refresh child. The new child is Stage 3 and
uses the existing strict refresh provenance with protocol version 2. Initialization
then advances the parent to `eventra.workflow.next_stage=4`, binds the v2 action,
preserves parent position, transitions the parent to `in_progress --no-start`, and
dispatches exactly one Frontend Engineer run.

All create, metadata, status, and start boundaries retain v1 lost-acknowledgement
and duplicate-suppression behavior, generalized to the request’s refresh stage.

### 8.5 Publication, adoption, and fresh gates

Preparation, staging-ref verification, managed fast-forward publication, and
candidate adoption use the existing exact Git-object checks. A v2 prepared PASS
still does not mean Review or QA passed.

Before deleting the transient reservation, adoption writes a permanent canonical
`eventra.refresh.supersession` receipt containing:

- version 2 and request digest;
- request and grant UUIDs;
- both stale gate identities, roles, original digests, and cancelled revisions;
- source SHA, adopted target SHA, managed PR, and prerequisite merge SHA;
- refresh child UUID/identifier and prepared evidence UUID/digest;
- `refresh_stage=3` and `fresh_gate_stage=4`.

The receipt is read back before reservation deletion. It is included in all later
refresh provenance validation and is never deleted.

After adoption, the ordinary planner returns `create_gate_stage` for Stage 4. New
Review and Integration QA children must bind the adopted target SHA, attempt 0,
the Stage 4 creation action, and their authoritative agent assignments. The old
cancelled gates are accepted only when the complete supersession receipt matches;
otherwise they remain a conflicting history.

## 9. Concurrency and competing writers

The parent-scoped nonblocking file lock remains the local single-writer boundary.
All write phases perform fresh authoritative reads inside the lock. Active runs are
restricted to the exact Delivery Lead during initialization and the exact Frontend
Engineer for the refresh child after dispatch.

Multica does not expose a revision compare-and-swap for status changes. Therefore
the design does not claim to prevent an external human from racing the cancellation
request. Instead it minimizes the window with a pre-write read, verifies the exact
post-write state, and blocks permanently on any unexplained result. No new child or
candidate publication follows an ambiguous cancellation.

Two local executors for the same parent converge through the lock and reservation.
A different request digest, gate digest, action key, or control SHA cannot resume
the reservation.

## 10. Interaction with ordinary workflows

- Watcher, normal Delivery Lead planning, repair, Smoke, `finish-phase`, and
  `finish-parent` treat any staged or active v2 refresh as a dedicated hold.
- Malformed or partial v2 metadata blocks instead of falling back to legacy flow.
- Cancelled stale gates never contribute a phase verdict or FailureBundle.
- A refresh FAIL/BLOCKED preserves the cancelled gates and original candidate,
  records its explicit outcome, and does not consume a repair attempt.
- After adoption, only the Stage 4 gates can satisfy the gate requirement.
- If Stage 4 Review/QA fail, the existing repair workflow proceeds from the adopted
  target and retains the supersession receipt.
- Successful gates still lead to merge hold. PR #14 merge, deploy, initial Smoke,
  retry Smoke, and Knowledge Loop retain their separate authorizations and evidence.

## 11. Code boundaries

### `tools/multica/candidate_refresh.py`

- Strictly parse and build v1 and v2 requests without weakening v1.
- Derive pristine-gate roles and authority digests from the trusted snapshot.
- Validate v2 entry, initial progress, reservation, receipt, and dynamic stages.
- Return deterministic refresh decisions for both protocol versions.

### `tools/multica/refresh_executor.py`

- Preserve complete child comment manifests and bind child runs.
- Permit only the v2 executor to issue `cancelled --no-start`.
- Implement reservation-backed cancellation prefix recovery.
- Generalize refresh-child initialization and adoption to request-bound stages.
- Persist and validate the permanent supersession receipt.

### `tools/multica/workflow.py`

- Add the explicit `--supersede-pristine-gates` plan flag.
- Render the v2 request, grant, and human-readable preview.
- Route staged v2 requests through the same guarded commands.
- Keep all ordinary mutation paths on hold during v2 progress.

### Tests and operator documentation

- Extend `test_candidate_refresh.py`, `test_refresh_executor.py`, and
  `test_workflow.py` with protocol, executor, CLI, and compatibility coverage.
- Update `tools/multica/README.md` and the Delivery Lead, Frontend Engineer,
  Independent Reviewer, and Integration QA instructions.
- Record the pilot behavior in `docs/multica/pilot-issues.md` without changing
  reusable skill content.

No new service, dependency, daemon, scheduled task, or generic framework is added.

## 12. Verification strategy

Implementation follows strict RED → GREEN cycles. Required automated coverage:

1. Existing v1 requests, digests, actions, stages, and rejection tests remain
   unchanged.
2. v2 freeze succeeds only for the exact pristine Reviewer/QA pair.
3. Every stable gate field, description, assignment, comment, run, metadata,
   status, and revision participates in rejection or digest binding.
4. Without the explicit CLI flag, the same snapshot remains rejected by v1.
5. Request/grant version, UUID, digest, parent, and comment revision mismatch cause
   zero writes.
6. Every reservation, cancellation, child-create, child-metadata, parent-metadata,
   parent-status, dispatch, preparation, publication, receipt, and reservation
   deletion write boundary is tested before and after effect.
7. Cancellation ACK loss resumes without a second cancellation command.
8. A refresh child cannot exist before both stale gates are cancelled.
9. Concurrent same-key executors produce one writer, one refresh child, and one
   run; different keys conflict.
10. External drift during either cancellation blocks without publication.
11. Stage 3 refresh adoption exposes exactly Stage 4 fresh gates.
12. Old gates cannot count as PASS/FAIL, create repair, or satisfy merge gates.
13. Watcher, repair, Smoke, finish-phase, and finish-parent remain held during v2.
14. The permanent receipt survives reservation cleanup and is required later.
15. The full memory-API end-to-end path preserves Stage 1 evidence and never
    modifies the remote base branch, deploys, or invokes Smoke.

Final verification must include:

```text
python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'
npm run test:local-contract
npm run test:footer-meta
npm run test:layout-hydration
npm run test:dashboard-profile
npm run lint
npm run build
python3 -B -m tools.multica.knowledge verify --frontend-root . --backend-root /Users/didi/Eventra-workspace/Eventra-Backend
git diff --check BASE..HEAD
```

An independent Reviewer must inspect the exact implementation SHA and the full
base-to-head diff before push or PR creation.

## 13. Rollout and live pilot

1. Implement on `feat/eventra-pristine-gate-supersession`, stacked on PR #17.
2. Run full local verification and independent exact-SHA review.
3. Obtain explicit authorization before pushing or creating the implementation PR.
4. Merge PR #17 and the implementation PR only with separate authorization.
5. Create a fixed detached live control checkout at the merged implementation SHA.
6. Create a trusted deployment record with a null mutation contract and prove a
   read-only v2 `plan-refresh` for PRO-122.
7. Show the exact request, cancellation preview, action key, and proposed mutation
   contract to the user.
8. Obtain separate authorization to install that exact mutation contract and stage
   the request.
9. Obtain the exact workspace member grant or explicit delegation to publish it.
10. Execute only the approved request and verify PRO-124/125 cancellation and the
    Stage 3 refresh child after every mutation.
11. Complete preparation, publication, adoption, and fresh Stage 4 gates through
    separate evidence-bearing steps.
12. Stop at merge hold. PR #14 merge, deploy, Smoke, and Knowledge Loop are outside
    this authorization.

At every rollout step, an unexpected live field or state produces a read-only
diagnostic and a local regression test before any protocol change is proposed.
