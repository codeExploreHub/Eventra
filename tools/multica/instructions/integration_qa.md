# Integration QA contract

## Ownership and inputs

Own independent integration verification of the exact submitted commit SHA or
SHA pair. Require the parent and child Issue identifiers, repository scope,
acceptance criteria, interface contract, exact SHA for every affected
repository, and safe runtime inputs. Ask the Delivery Lead for clarification
before testing if the SHA set, environment, expected behavior, or test route is
ambiguous.

## Exact-SHA worktree preparation

Before checkout, inspect worktree cleanliness. Exclude only runtime-managed
`AGENTS.md`, `.agent_context/`, and `.multica/`, plus a nested or sibling
repository explicitly declared by the Project; any other change blocks QA.
Fetch the handed-off PR ref or exact commit without moving a branch, verify
`git rev-parse FETCH_HEAD` equals the handed-off SHA, then run
`git switch --detach FULL_SHA`. Verify both `git rev-parse HEAD` and cleanliness
again before testing. Never reset, clean, stash, or overwrite user work.

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

Reread every candidate SHA and test only that immutable set. Post commands,
exits, observations, and safe artifacts; retain the comment UUID. Use exactly
one completion form.

### PASS gate completion

```text
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind qa --result pass --attempt N --frontend-sha FULL_SHA --backend-sha FULL_SHA --evidence-comment COMMENT_UUID
```

PASS declares neither `--evidence-comment-url` nor
`--responsible-repository`.

### Non-PASS gate completion

```text
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind qa --result fail --attempt N --frontend-sha FULL_SHA --backend-sha FULL_SHA --evidence-comment COMMENT_UUID --evidence-comment-url HTTPS_URL --responsible-repository frontend
```

Omit only the unaffected SHA flag. Every FAIL or BLOCKED gate result uses a
canonical HTTPS URL and at least one `--responsible-repository` legal owner
(repeat the flag for every affected owner).

### Smoke completion

```text
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
