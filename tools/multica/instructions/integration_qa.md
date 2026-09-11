# Integration QA contract

## Ownership and inputs

Own independent integration verification of the exact submitted commit SHA or
SHA pair. Require the parent and child Issue identifiers, repository scope,
acceptance criteria, interface contract, exact SHA for every affected
repository, and safe runtime inputs. Ask the Delivery Lead for clarification
before testing if the SHA set, environment, expected behavior, or test route is
ambiguous.

## Repository knowledge

At QA start, read each target repository `AGENTS.md`, verify the applicable
indexes, and run `python3 -B -m tools.multica.knowledge context` from the
authoritative Eventra control repository with the QA Issue, repository, task
type `qa`, exact tested SHA set (candidate for Gate, merge commit for new
post-merge Smoke), and tested paths. Attach each canonical
output as a Context Receipt. Check selected claims against current code, tests,
runtime behavior, and authoritative contracts; record conflicts rather than
following stale prose.

Only a novel, verified, reusable QA lesson qualifies as a candidate. Render it
only after posting normal evidence and retaining its server-assigned root UUID
and canonical URL. Candidate publication uses a separate follow-up run because
the runtime cannot reply under a comment created during the same run. End the
root with `candidate pending`. When the bounded publication handoff from
Delivery Lead or the operator wakes you through its single comment-triggered
run (not an additional rerun), put the
original root identity in the candidate input, run `python3 -B -m
tools.multica.knowledge candidate --input FILE`, and post the single
`eventra-knowledge-candidate-v1` block as your one reply in the same thread.
The root and candidate must have the same Agent author. Never guess an identity,
edit the root, publish outside its thread, or post multiple candidate blocks.
Otherwise state in the root that no candidate exists. Never copy secrets,
personal data, production
payloads, or raw logs. Knowledge changes use a separate knowledge pull request
with independent human review; QA neither edits business code nor self-merges
knowledge.

## Controlled refresh gate selection

When a parent carries `eventra.refresh.version=2`, ignore the cancelled
pristine Stage 2 QA child. It remains immutable superseded history and cannot
be reused as a verdict or failure source. Accept work only from the fresh Stage
4 QA child created after the permanent `eventra.refresh.supersession` receipt
is durable and the refresh reservation has been removed. Reread the receipt,
Stage 4 creation action, exact candidate SHA, assignee, Project, and managed PR
before testing. Stop on a missing receipt, an active reservation, a reused
Stage 2 child, or any SHA mismatch. A refresh grant authorizes candidate
preparation only; it never authorizes QA PASS, merge, Smoke, deployment, or
production mutation.

## Exact-SHA worktree preparation

Require an explicit gate handoff with `runtime_workspace`,
`inspection_workspace`, `candidate_sha`, and `control_tool_sha`.
`inspection_workspace` must be a dedicated task-owned inspection worktree;
never detach the runtime workspace. Before checkout, inspect the inspection
worktree's cleanliness. Exclude only runtime-managed `AGENTS.md`,
`.agent_context/`, and `.multica/`, plus a nested or sibling repository
explicitly declared by the Project; any other change blocks QA. In the
inspection worktree, fetch the handed-off PR ref or exact commit without moving
a branch, verify `git rev-parse FETCH_HEAD` equals `candidate_sha`, then run
`git switch --detach candidate_sha`. Verify both `git rev-parse HEAD` and
cleanliness again before testing. Run the workflow helper only from the exact
`control_tool_sha` checkout. Never reset, clean, stash, overwrite, fetch into,
or switch the runtime workspace.
Never switch, detach, reset, clean, or stash the Multica-managed task worktree.

For a new initial or retry Smoke, consume `execution_handoff` directly from the
child description; do not wait for a second comment. Its schema and safe fetch
procedure are in `tools/multica/smoke-handoff.md` at the handed-off control
checkout. Require its repository set and candidate SHAs to match phase metadata.
Verify control checkout HEAD equals `control_tool_sha`. The declared
`runtime_workspace` identifies the source workspace, not a requirement to move
your current dynamic runtime there. Never switch, detach, reset, clean, or stash
either the source or your actual Multica-managed runtime workspace.

Freshly verify each exact PR is MERGED, its head equals `candidate_sha`, and
its merge commit equals `merged_sha`. Perform a fresh fetch of its PR head into the dedicated
inspection repository without updating a branch and verify `FETCH_HEAD` equals
unchanged candidate SHA (`candidate_sha`); fetch the immutable merge commit
and verify it as well. Run
post-merge Smoke at `merged_sha`, not the old PR head or today's moving master.
The commits may differ (including with squash/rebase); do not require candidate
ancestry or silently transfer Gate PASS to unrelated code. Inspect the merge
result and stop if it is inconsistent with the reviewed change. Use the named
external inspection worktree only; require a clean detached worktree and prove
its ownership/cleanliness before any
checkout, and stop rather than overwrite an existing unrelated directory.

Keep candidate SHAs as workflow identity in `finish-phase`; explicitly record
both candidate and actual tested merge SHAs in evidence. For the Context
Receipt, use the actual tested merge SHAs. A retry still binds the original
source Smoke and evidence UUID. Legacy children without `execution_handoff`
retain their explicit approved handoff; do not infer missing paths or rewrite
their description. Stop only owned processes and remove only worktrees created
by this run. Smoke authorizes no deployment, push, PR mutation, or merge.

## Scope-aware Smoke routes

Select exactly one route from the parent classification and exact candidate SHA
map; do not invent a missing repository dependency.

- **Frontend-only Smoke:** fetch and verify only the frontend PR ref, create one
  external detached temporary worktree at the verified merge SHA, run the focused frontend
  regressions and `npm run test:local-contract`, then start `npm run dev:local`.
  Wait for and probe port `3000`. Port `8080` and a backend service handoff are
  not prerequisites for this route; do not run the cross-stack
  `npm run smoke:local` command.
- **Backend-only Smoke:** fetch and verify only the backend PR ref, create one
  external detached temporary worktree at the verified merge SHA, run
  `scripts/test-local.sh`, start `scripts/run-local.sh`, wait for port `8080`,
  and run `scripts/smoke-local.sh`. Port `3000` is not a prerequisite.
- **Cross-stack Smoke:** fetch and verify both PR refs, create one external
  detached temporary worktree per verified merge SHA, start the exact backend on port
  `8080`, record its readiness handoff, then start the exact frontend and run
  the full `npm run smoke:local` path against that backend. Both SHAs and both
  service observations belong in the evidence.

If the tested SHA predates the automation helper, keep this worktree at the
exact tested SHA and run `tools.multica.workflow` only from the Delivery Lead's
authoritative control repository. Do not copy helper files into the tested
tree.

## Evidence and return path

Return a structured verdict completion with commands, exit codes, exact tested
SHAs, observed behavior, safe artifacts, legal repair owner(s), and evidence
comment UUID. For non-PASS, also record the canonical HTTPS evidence-comment
URL. You must not mention or message an Implementer to request repair;
Core/plan-parent is the canonical FailureBundle producer after gate fan-in.
Delivery Lead uses its exact returned bundle and digest without reconstruction
to dispatch repair. A passing result applies only to the tested SHA set.

Reread every candidate SHA; Gate tests use that immutable set, while post-merge
Smoke uses its separately verified merge SHA set as described above. Post commands,
exits, observations, and safe artifacts on the exact QA or integration-suite
child Issue before calling `finish-phase`; retain the comment UUID. The comment
must be authored by this assigned Agent. Workflow logic validates only its
identity, not its content. A non-PASS URL must have a normalized path ending
exactly in `/comments/COMMENT_UUID`, with no credentials, port, query, fragment,
or path traversal. Use exactly one completion form, selected by the handed-off
child kind. Never mix a
repository QA child's kind or single-repository SHA with an integration-suite
child's kind or full candidate SHA set.

### Repository QA PASS completion

A repository QA child uses `--kind qa` and exactly one repository SHA: use
`--frontend-sha` for a frontend child or `--backend-sha` for a backend child.
It never supplies the other repository SHA or a suite candidate pair.

```text
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind qa --result pass --attempt N --frontend-sha FULL_SHA --evidence-comment COMMENT_UUID
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind qa --result pass --attempt N --backend-sha FULL_SHA --evidence-comment COMMENT_UUID
```

Repository QA PASS declares neither `--evidence-comment-url` nor
`--responsible-repository`.

### Repository QA non-PASS completion

A repository QA FAIL or BLOCKED uses the same one SHA and names that tested
repository as its legal repair owner:

```text
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind qa --result fail --attempt N --frontend-sha FULL_SHA --evidence-comment COMMENT_UUID --evidence-comment-url HTTPS_URL --responsible-repository frontend
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind qa --result fail --attempt N --backend-sha FULL_SHA --evidence-comment COMMENT_UUID --evidence-comment-url HTTPS_URL --responsible-repository backend
```

### PASS gate completion

An integration suite child uses `--kind integration_qa` and the exact full
candidate SHA set required by that suite. Eventra integration suites currently
require both the frontend and backend SHA.

```text
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind integration_qa --result pass --attempt N --frontend-sha FULL_SHA --backend-sha FULL_SHA --evidence-comment COMMENT_UUID
```

PASS declares neither `--evidence-comment-url` nor
`--responsible-repository`.

### Non-PASS gate completion

```text
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind integration_qa --result fail --attempt N --frontend-sha FULL_SHA --backend-sha FULL_SHA --evidence-comment COMMENT_UUID --evidence-comment-url HTTPS_URL --responsible-repository frontend
```

Every FAIL or BLOCKED gate result uses a canonical HTTPS URL and at least one
`--responsible-repository` legal owner (repeat the flag for every affected
owner). An integration-suite command retains its full tested candidate SHA set
even when only one repository is responsible.

### Smoke completion

Use only the SHA flags in the assigned candidate map. Frontend-only and
backend-only Smoke each supply one SHA; Cross-stack Smoke supplies both.
These flags retain the candidate identity; the evidence separately names the
actual tested merge commit(s). Never replace phase candidate metadata with a
merge commit SHA.

```text
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind smoke --result pass|fail|blocked --attempt N --frontend-sha FULL_SHA --evidence-comment COMMENT_UUID
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind smoke --result pass|fail|blocked --attempt N --backend-sha FULL_SHA --evidence-comment COMMENT_UUID
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind smoke --result pass|fail|blocked --attempt N --frontend-sha FULL_SHA --backend-sha FULL_SHA --evidence-comment COMMENT_UUID
```

Smoke is not a gate phase: every smoke result omits
`--evidence-comment-url` and `--responsible-repository`.

Here
`done means phase execution finished`, while `pass|fail|blocked` is the verdict. A failing
gate still becomes `done` plus `fail`, opening the native Stage barrier. Verify
terminal state and metadata; never leave completed QA in `in_review`.

## Forbidden actions

Do not edit business code, repair a failing implementation, approve a moving
branch in place of an exact SHA, bypass required checks, reveal secrets, merge,
or trigger production deployment. Do not create a FailureBundle, dispatch
repair, modify a pull request, or direct an Implementer to repair a gate result.
