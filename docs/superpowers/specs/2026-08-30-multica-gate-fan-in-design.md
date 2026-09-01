# Multica Gate Fan-In Workflow Design

Date: 2026-08-30
Status: approved in conversation; pending written-spec review

## Context

PRO-65 exposed a coordination race in the Eventra multi-repository delivery
workflow. During attempt 5, backend QA reported one defect and the backend
repair owner resumed work from that first result. Two minutes later, the
independent backend review reported a different defect. The repair owner fixed
only the first finding and pushed a replacement SHA. A refreshed gate stage
then reproduced the omitted finding and blocked the parent again.

The reusable Core already prevents `resume_parent()` from advancing while the
current Stage has active or nonterminal children. The live Eventra role
contracts undermine that barrier, however:

- Reviewer and QA instructions tell those roles to route findings directly to
  the owning implementer;
- implementer instructions allow returned findings to be fixed in a new
  commit without requiring a newly assigned repair child; and
- a repair request has no typed field carrying the complete set of terminal
  failures that caused it.

The result is a split authority model. The parent coordinator waits for a
Stage, while individual Agents may start repair work before the Stage has been
aggregated. Updating prompts alone would reduce the probability of recurrence
but would not make the workflow fail closed.

## Goals

- Make the reusable Core the authority for gate fan-in and repair dispatch.
- Wait for every child in the current gate Stage to become terminal before any
  repair can be created.
- Aggregate every terminal non-PASS gate into one immutable failure bundle.
- Create at most one new repair child per responsible repository in one shared
  repair Stage and attempt.
- Prevent completed or historical children from producing replacement SHAs.
- Reject out-of-band pull-request head changes rather than adopting them.
- Preserve exact-SHA review, QA, merge, smoke, deployment, and bounded-repair
  policies.
- Apply the same rules to Eventra and to teams created later by the reusable
  skill and CLI.

## Non-goals

- Do not add production deployment or rollback automation.
- Do not waive or reduce independent review, repository QA, integration QA,
  GitHub checks, mergeability checks, or merged smoke.
- Do not add a separate Gate Aggregator Agent.
- Do not infer a repair owner by parsing free-form comment prose.
- Do not rewrite historical completed workflow metadata.
- Do not let the Watcher plan new work, edit code, adopt a SHA, merge, or
  deploy.
- Do not expand the existing automatic repair-attempt authority.

## Chosen approach

The implementation will enforce fan-in in the generic Core and align every
role contract with that authority. A prompt-only change is insufficient
because an Agent could still act on an early comment. A separate aggregation
Agent is unnecessary because the Core already owns Stage state, idempotency,
and successor dispatch.

The Core will introduce typed failure references and one immutable failure
bundle. The bundle is constructed only from a complete terminal Stage, is
partitioned by explicitly declared responsible repositories, and is carried
by repair child requests. Eventra's compatibility workflow will expose the
same semantics through version 2 metadata and deterministic `plan-parent`
output.

## Workflow contract versioning

New fan-in metadata uses workflow contract version `2`.

- Completed version 1 Issues remain readable and are never rewritten.
- A nonterminal version 1 parent is not automatically advanced by version 2
  logic. It is blocked with an explicit migration-required reason.
- New parent Issues created after the provisioned instruction update use
  version 2.
- Version 2 readers reject missing version 2 fields instead of guessing from
  comment text or repository names.
- Eventra's compatibility adapter continues to render and parse historical
  version 1 phase records for read compatibility, while new execution paths
  render version 2.

The generic framework lock and reusable package version will identify the new
workflow metadata version so installations cannot silently mix contracts.

Version 2 parent metadata distinguishes the total repair round from automatic
authority. It records `repair_round`, `automatic_repairs_used`, and, for a
human-authorized extra round, the authorizing member comment UUID plus the
exact source failure-bundle digest. `automatic_repairs_used` cannot exceed the
manifest limit. A human authorization grants exactly the next repair round for
exactly one bundle and is consumed once; it does not reset or expand the
automatic counter.

## Typed failure evidence

### FailureEvidenceRef

Every terminal gate whose result is `fail` or `blocked` produces one immutable
failure reference with:

- child Issue identifier;
- phase: `review`, `qa`, or `integration_qa`;
- result: `fail` or `blocked`;
- Stage ordinal and shared attempt;
- exact candidate SHA map used to create and execute that child;
- nonempty responsible repository set;
- evidence comment UUID and canonical evidence URL; and
- suite key for integration QA, otherwise an empty suite key.

Responsibility is declared structurally by the gate completion:

- repository review and repository QA may name only their own repository;
- integration QA may name one or more repositories from its manifest-declared
  suite;
- PASS completions must not declare responsible repositories; and
- a non-PASS completion with an empty, unknown, or out-of-scope responsibility
  set is malformed and blocks the parent.

The Core never determines responsibility by reading natural-language evidence.

### FailureBundle

A failure bundle contains:

- parent identifier;
- workflow version;
- completed gate Stage ordinal;
- shared attempt;
- complete exact candidate SHA map;
- every `FailureEvidenceRef` from that Stage; and
- a canonical SHA-256 digest of the normalized bundle payload.

Normalization sorts failure references by repository, phase, suite, child
identifier, and evidence UUID. Repository collections and SHA maps use
manifest order or canonical key order as appropriate. This makes repeated
parent wakeups construct the same digest.

The bundle is valid only when every child in the current gate Stage is
terminal, every child completion is bound to the Stage's attempt and candidate
SHA map, and every non-PASS result has valid failure evidence. PASS evidence
remains part of the gate snapshot but is not copied into the failure list.

## Stage state machine

### Gate Stage

While any current gate child is active or nonterminal, a completion callback,
direct parent resume, Watcher wakeup, or operator retry returns `wait` with zero
successor mutation. A non-PASS result may be recorded, but it cannot dispatch
repair.

After every child is terminal:

- all PASS results proceed to the existing merge-preflight decision;
- one or more valid non-PASS results produce exactly one `FailureBundle` and
  one shared repair decision; or
- malformed, missing, stale, or contradictory evidence blocks the parent.

The repair action key includes parent identity, workflow version, source gate
Stage, next repair Stage, attempt, exact candidate SHA map, and failure-bundle
digest. The same state cannot create a duplicate repair Stage.

### Repair Stage

The Core partitions the bundle by responsible repository. It creates one new
repair child for each repository that appears in at least one failure
reference. Every child receives:

- the complete parent bundle identity and digest;
- all failure references that name that repository;
- the rejected exact candidate SHA map;
- the existing managed pull request for that repository; and
- the one shared next attempt.

A failure involving multiple repositories is included in each affected repair
child; it does not consume multiple attempts. Each repair child is created in
the same monotonically increasing Stage.

One repair may finish before its siblings. Its completion can record a
replacement SHA, but the parent cannot create successor gates until every
repair child in that Stage is terminal. All repairs must PASS, every replacement
SHA must equal the corresponding current PR head, and every unaffected
candidate must remain unchanged.

When the repair Stage passes, the Core creates a complete fresh gate Stage for
the replacement SHA map. No earlier review or QA PASS transfers.

### Immutable completed work

Only an active child in the current Stage and attempt may submit a phase
completion. Once `mark_child_done` is authoritative:

- a second completion with different evidence, SHA, result, responsibility, or
  bundle identity is rejected;
- a comment or mention on the completed child cannot invoke implementation
  authority;
- a historical implementation or repair child cannot update parent candidate
  metadata; and
- the Watcher cannot rerun it as current work.

An identical retry remains an idempotent no-op.

### Out-of-band SHA drift

Candidate SHAs are updated only by an authoritative current implementation or
repair completion. If an existing PR head changes without such a completion,
the Core does not adopt the new head, refresh gates, or create another repair.
It blocks with an out-of-band SHA-drift reason and reports the expected and
observed repository identity without exposing command output or secrets.

## Agent authority contracts

### Delivery Lead

The Delivery Lead remains the only role allowed to interpret the complete gate
set and dispatch repairs. It must use the typed parent decision and failure
bundle, create the complete repair barrier group, verify it, and then advance
Stage metadata. It cannot assemble a subset from whichever comment arrived
first.

### Independent Reviewer and Integration QA

Reviewer and QA roles may inspect exact candidates and write their own
structured completion. They must not:

- mention or message an implementer to request repair;
- post a repair instruction on an implementation or historical repair child;
- modify business code or a pull request; or
- claim that their individual failure has started a repair.

They return findings only to the gate child and parent coordinator. For
non-PASS results they must declare the structurally valid responsible
repository set.

### Frontend and backend implementers

Implementers may modify business code only while assigned to a current active
implementation or repair child. A repair child must contain a valid failure
bundle and existing managed PR. Implementers address every bundle reference
for their repository and report any unresolved reference as non-PASS. They do
not resume work from comments on completed children or gate tasks.

### Workflow Watcher

The Watcher may recover one existing current assignment only. It cannot create
a bundle, allocate an attempt, dispatch repair, adopt a SHA, rerun a historical
child, or bypass the Stage barrier. A version mismatch, malformed bundle, or
out-of-band head is a human-visible block, not stalled work.

## Generic Core component changes

### `tools/multica_delivery/workflow.py`

- Add exact immutable `FailureEvidenceRef` and `FailureBundle` models.
- Extend `PhaseCompletion`, `WorkflowChild`, and `ChildRequest` with validated
  responsibility and failure-bundle fields.
- Build a bundle only after `_current_stage_wait()` proves the barrier is
  terminal.
- Require repair requests to carry the appropriate nonempty partition.
- Reject non-current and conflicting repeated completions.
- Bind repair action keys and reconciliation reads to the bundle digest.

### `tools/multica_delivery/decisions.py`

- Keep decisions pure and fail closed.
- Return one shared repair decision only for a complete coherent terminal gate
  snapshot.
- Block malformed ownership, evidence, attempt, Stage, or exact-SHA identity.
- Preserve all-merged, attempt-budget, merge, smoke, and deployment rules.

### Multica state reader and executor

- Parse version 2 failure ownership and evidence identities from authoritative
  Issue state.
- Render all failure references into repair Issue descriptions in canonical
  order.
- Persist and reread the bundle digest before reporting successor creation.
- Never persist raw secrets, environment values, command output, or arbitrary
  comment bodies in workflow metadata.

### Metadata and lock

- Encode version 2 fields with strict exact types and closed key sets.
- Represent total repair round, consumed automatic budget, and an optional
  one-shot human authorization as separate fields.
- Include the workflow metadata version in the framework lock.
- Reject mixed version 1/version 2 mutable state.

## Eventra compatibility component changes

### `tools/multica/workflow.py`

- Add version 2 phase ownership metadata and CLI arguments for non-PASS gate
  completions.
- Make `plan-parent` emit the complete normalized failure bundle and digest for
  a repair decision.
- Reject a repair action when the latest gate Stage is incomplete or any
  failure record is missing.
- Preserve version 1 historical parsing without permitting version 1 automatic
  progression.

### Role and Squad instructions

Update Delivery Lead, Independent Reviewer, Integration QA, frontend engineer,
backend engineer, Squad, Project contexts, Watcher, and operator documentation
to reflect the single Core authority. Remove or replace every instruction that
routes an individual failure directly to an implementer.

### Provisioning

The provisioner reconciles the existing Eventra Agent instructions in place.
It does not create another Squad, duplicate Agent, Project, Watcher, or trigger.
A dry-run shows the exact intended updates. The second successful apply must
report zero mutations.

## Attempt and human-authorization policy

The configured automatic attempt limit is unchanged. Each complete non-PASS
gate Stage consumes at most one shared repair attempt, regardless of the number
of findings or repositories.

When the automatic budget is exhausted, the parent becomes `blocked`. An
explicit member comment may authorize exactly one additional repair round. The
Delivery Lead records a structured authorization containing the canonical
comment UUID, the next repair round, and the exact source failure-bundle digest;
the Core rereads the comment identity and member authorship before consuming
it. The authorization is invalid if the bundle, round, author type, or comment
identity differs, and the same comment UUID cannot authorize another round.

An authorized round still receives the complete previous failure bundle,
creates a new repair Stage, and requires a full fresh gate Stage. Human
authorization does not permit a gate Agent or historical child to edit code,
does not increase `automatic_repairs_used`, and does not authorize merge or
deployment.

## Failure behavior

- Active current-stage sibling: `wait`, zero repair mutation.
- Missing or invalid responsible repository: block.
- Missing, malformed, or stale evidence comment identity: block.
- Failure SHA map differs from the child creation SHA map: block.
- Mixed current Stage attempts or workflow versions: block.
- Failure bundle partition is empty for a requested repair: block.
- Existing successor with a different bundle digest: block as state
  corruption.
- Duplicate wakeup with the same digest: no-op.
- Missing, reused, wrong-round, non-member, or wrong-bundle human authorization:
  block without consuming authorization.
- Out-of-band PR head: block without adoption.
- Exhausted automatic attempt budget: block for explicit human decision.
- Partial cross-repository merge: preserve the existing safe stop.

## Test strategy

All behavior changes use test-driven development. Each focused regression is
observed failing before production code changes.

### PRO-65 race reproduction

1. Create a gate Stage with backend review and backend QA active.
2. Finish QA as FAIL with finding A while review remains active.
3. Assert `wait`, zero repair child, and unchanged attempt.
4. Finish review as FAIL with finding B.
5. Assert one repair Stage, one backend repair child, one shared attempt, and a
   bundle containing A and B.

### Core fan-in tests

- Multiple repositories fail in one Stage and receive one repair child each
  with the same bundle digest and attempt.
- A multi-repository integration failure is included in every declared owner
  partition.
- PASS with owners, non-PASS without owners, and out-of-scope owners block.
- An incomplete Stage cannot create repair through callback, direct resume,
  Watcher, status wakeup, or operator retry.
- Repeated wakeups create no duplicate bundle, Stage, child, or attempt.
- Each human authorization is member-authored, bundle-bound, one-round, and
  single-use; it never resets the automatic budget.
- Completed or historical child mutation is rejected; identical retry is a
  no-op.
- Unsolicited PR head changes block and are never adopted.
- Replacement SHAs invalidate every old gate and require complete fresh gates.
- Repair siblings must all finish before successor gates.

### Compatibility and provisioning tests

- Completed version 1 records remain readable.
- Nonterminal version 1 parents do not progress under version 2.
- Version 2 legacy Eventra rendering agrees with the generic contract.
- Every provisioned role instruction contains its new authority boundary.
- Provisioner dry-run is mutation-free and repeat apply is idempotent.
- Existing Eventra compatibility IDs and resource boundaries remain unchanged.

### Full verification

- `python3 -B -m unittest discover -s tools -p 'test_*.py' -v`
- `python3 -B -m compileall -q tools/multica tools/multica_delivery`
- `git diff --check`
- repository safety scans for secrets, forbidden command escape hatches,
  deployment expansion, and direct gate-to-implementer repair language
- Eventra provisioner dry-run against the current manifest

No live GitHub mutation, pull-request merge, production deployment, product
repository reset, or secret read is part of automated tests.

## Rollout and live verification

1. Implement and verify locally.
2. Review provisioner dry-run output.
3. With explicit apply authorization, reconcile the existing Eventra Agent
   instructions and reusable installation artifacts.
4. Run a second apply and require zero mutations.
5. Create a dedicated non-production pilot whose two parallel gate children
   return distinct failures.
6. Verify no repair exists after the first failure, then verify one aggregated
   repair Stage after the last gate becomes terminal.
7. Verify the repair description contains both evidence references and no
   historical child is rerun.

## Acceptance criteria

1. No repair path can execute before all current gate siblings are terminal.
2. Every repair child is created from one immutable bundle containing every
   terminal non-PASS result from the source gate Stage.
3. One Stage consumes one shared attempt and creates at most one repair child
   per responsible repository.
4. Reviewer and QA Agents cannot directly authorize implementation work.
5. Completed or historical children cannot update candidate SHAs.
6. Out-of-band PR heads are blocked, not adopted.
7. Replacement SHAs receive a complete fresh gate Stage.
8. Duplicate parent or Watcher wakeups are idempotent.
9. Eventra and newly generated multi-repository teams use the same version 2
   contract.
10. Existing completed version 1 history remains readable.
11. Automatic merge remains limited to development/local after all exact-SHA
    gates; production deployment remains manually triggered.
12. Focused regressions, the complete tools suite, compile checks, safety
    scans, compatibility tests, and provisioner dry-run pass before live apply.
