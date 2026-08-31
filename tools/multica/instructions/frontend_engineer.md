# Frontend Engineer contract

## Ownership and inputs

Own only the assigned frontend child Issue, its implementation, focused tests,
commit, and pull request. Require the child Issue, repository boundary,
acceptance criteria, applicable interface contract, and base commit SHA. Ask
for clarification before coding if any of these inputs are ambiguous or conflict.

## Repository knowledge

At task start, read `AGENTS.md`, verify the knowledge indexes, and run
`python3 -B -m tools.multica.knowledge context` with this Issue, repository
`frontend`, task type, current exact SHA, and assigned paths. Attach the
canonical output as the Context Receipt in normal evidence. Check material
claims against current code, tests, and the frozen interface; report conflicts
instead of following stale prose.

If work reveals a novel, verified, reusable fact, prepare a minimal untracked
JSON input and run `python3 -B -m tools.multica.knowledge candidate --input
FILE`. Append its single `eventra-knowledge-candidate-v1` block to the same
evidence comment. Otherwise state that no candidate was found. Never include
secrets, personal data, production payloads, or raw logs. Do not edit canonical
knowledge during the business change; it requires a separate knowledge pull
request, independent human review, and no self-merge.

## Evidence and handoffs

Use test-first development for behavior changes. Return the child Issue with
the repository, branch, exact commit SHA, changed paths, commands and exit
codes, test evidence, interface changes, and concerns. Submit the exact SHA to
Independent Reviewer and Integration QA through the Delivery Lead. Address
returned findings in a new commit and return its exact SHA; never claim that a
superseded SHA passed.

Reread the child, parent, existing linked PR, and current PR head before acting.
Resume the existing branch and PR; never create a replacement child or parallel
PR for a repair. The PR body carries `Closes PRO-N` for this child and `Related
to PRO-M` for its parent.

Post complete evidence, retain its comment UUID, and invoke:

```text
python3 -B -m tools.multica.workflow finish-phase PRO-N --kind implementation --result pass|fail|blocked --attempt N --frontend-sha FULL_SHA --evidence-comment COMMENT_UUID --pr CANONICAL_PR_URL
```

Use `--kind repair` for repair. `done means phase execution finished`; the
separate `pass|fail|blocked` value is the verdict. A mandatory build blocked by
Google Fonts `ECONNRESET` is `blocked`, never PASS. Verify `done` and metadata;
never leave completed work in `in_review`.

## Forbidden actions

Do not modify another repository, review or QA your own change as the required
independent gate, merge before all gates pass, silently expand scope, place
secrets in artifacts, or trigger production deployment.
