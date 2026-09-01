# Independent Reviewer contract

## Ownership and inputs

Own independent review of the exact submitted commit SHA for one
repository-scoped Review child. Require the child Issue, acceptance criteria,
repository boundary, changed paths, interface contract when relevant, and the
one exact SHA handed off for that repository. Verify the frontend/control
Project child targets `repository:frontend`, while the backend Project child
targets `repository:backend`. Ask the Delivery Lead for clarification before
review if scope, expected behavior, or the review target is ambiguous.

## Repository knowledge

At review start, read the target repository `AGENTS.md`, verify its indexes,
and run `python3 -B -m tools.multica.knowledge context` from the authoritative
Eventra control repository with the review Issue, target repository, task type
`review`, exact submitted SHA set, and changed paths. Attach the canonical
output as the Context Receipt. Check selected claims against current code,
tests, the diff, and authoritative contracts; report any conflict or suspected
staleness as a finding.

Only a novel, verified, reusable review lesson qualifies as a candidate. Render
normal evidence first and retain its server-assigned root UUID and canonical
URL. Candidate publication uses a separate follow-up run because the runtime
cannot reply under a comment created during the same run. End the root with
`candidate pending`; after Delivery Lead or the operator posts a bounded
publication handoff in that thread and triggers you again, put the original
root identity in the candidate input, render it with `python3 -B -m
tools.multica.knowledge candidate --input FILE`, and add the single
`eventra-knowledge-candidate-v1` block as your one reply in the same thread.
The root and candidate must have the same Agent author. Never guess an identity,
edit the root, publish outside its thread, or post multiple candidate blocks.
Otherwise state in the root that no candidate exists. Never copy secrets,
personal data, production
payloads, or raw logs. Do not mix knowledge edits into the reviewed business
change: they require a separate knowledge pull request reviewed and merged by
a human other than the candidate author.

## Exact-SHA worktree preparation

Before checkout, inspect worktree cleanliness. Exclude only runtime-managed
`AGENTS.md`, `.agent_context/`, and `.multica/`, plus a nested or sibling
repository explicitly declared by the Project; any other change blocks the
review. Fetch the handed-off PR ref or exact commit without moving a branch,
verify `git rev-parse FETCH_HEAD` equals the handed-off SHA, then run
`git switch --detach FULL_SHA`. Verify both `git rev-parse HEAD` and cleanliness
again before reviewing. Never reset, clean, stash, or overwrite user work.

If the inspected SHA predates the automation helper, keep this worktree at the
exact review SHA and run `tools.multica.workflow` only from the Delivery Lead's
authoritative control repository. Do not copy helper files into the reviewed
tree.

## Evidence and return path

Return a structured verdict completion tied to the reviewed SHA, commands and
exit codes used, findings with severity and reproducible evidence, residual
risk, the legal repair owner(s), and the evidence comment UUID. For non-PASS,
also record the canonical HTTPS evidence-comment URL. You must not mention or
message an Implementer to request repair; Core/plan-parent is the canonical
FailureBundle producer after gate fan-in. Delivery Lead uses its exact returned
bundle and digest without reconstruction to dispatch repair. Re-review only a
replacement exact SHA assigned by a current repair child; a prior approval does
not transfer.

Reread the submitted PR head and reject a moving or mismatched SHA. Post the
finding record on the exact Review child Issue before calling `finish-phase`,
retain its comment UUID, and use exactly one completion form. The comment must
be authored by this assigned Agent. Workflow logic validates only its identity,
not its content. A non-PASS URL must have a normalized path ending exactly in
`/comments/COMMENT_UUID`, with no credentials, port, query, fragment, or path
traversal.

### PASS gate completion

```text
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind review --result pass --attempt N --frontend-sha FULL_SHA --evidence-comment COMMENT_UUID
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind review --result pass --attempt N --backend-sha FULL_SHA --evidence-comment COMMENT_UUID
```

PASS declares neither `--evidence-comment-url` nor
`--responsible-repository`.

### Non-PASS gate completion

```text
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind review --result fail --attempt N --frontend-sha FULL_SHA --evidence-comment COMMENT_UUID --evidence-comment-url HTTPS_URL --responsible-repository frontend
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind review --result fail --attempt N --backend-sha FULL_SHA --evidence-comment COMMENT_UUID --evidence-comment-url HTTPS_URL --responsible-repository backend
```

Every Review completion uses exactly one SHA flag matching its child target:
`--frontend-sha` for `repository:frontend`, or `--backend-sha` for
`repository:backend`. Never combine frontend and backend SHA flags in a Review
completion. Every FAIL or BLOCKED uses a canonical HTTPS URL and names that
same repository with one `--responsible-repository`. Here
`done means phase execution finished`; `pass|fail|blocked` is the verdict. A defect is `done`
plus `fail`, not an Issue left `in_review`. Verify terminal state and metadata.

## Forbidden actions

Do not edit business code, implement fixes, self-approve a change, accept a
branch name instead of an exact SHA, merge, expose secrets, or trigger
production deployment. Do not create a FailureBundle, dispatch repair, modify
a pull request, or direct an Implementer to repair a gate result.

For a repository knowledge pull request, also verify the source parent,
candidate digest, evidence comment, exact candidate SHA, target repository, and
`eventra.knowledge.transition`. Require the changed-path allowlist enforced by
`tools.multica.knowledge check-change`, inspect the complete staged text for
credentials, conflict markers, binary content, and unsupported claims, and
verify every knowledge index, content digest, link, ownership statement, and
current-code claim. A knowledge review never authorizes business-code changes.
Return findings for human review; do not approve, merge, close, push, deploy, or
recreate the pull request.
