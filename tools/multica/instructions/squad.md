# Multi-repository delivery contract

## Classify and route work

Classify each parent Issue before implementation as frontend-only, backend-only,
or cross-repository. Create at least one scoped child Issue for every affected
repository; a cross-repository parent requires one frontend child Issue and one
backend child Issue. Each child records owner, repository, acceptance criteria,
interface expectations, base SHA, and required evidence. Clarify missing or
conflicting inputs before a child starts.

All parent Issues and frontend children belong to **Eventra Local Development**.
Backend children belong to **Eventra Backend Local Development** and must link
back to their parent. Both Projects use this same Squad.

For cross-repository work, Freeze the interface contract before implementation.
Only run frontend and backend work in parallel when the contract is frozen and neither child has a real dependency on the other. Otherwise, sequence work by dependency and hand off the producing exact SHA with its contract evidence before the dependent child starts.

Because each Project exposes exactly one worktree, cross-stack review and QA are
staged explicitly. The Independent Reviewer reviews the backend exact SHA in
the backend Project and the frontend exact SHA in the parent/frontend Project,
then returns two component decisions for one coordinated gate. Integration QA
first verifies the backend exact SHA in the backend Project. The Backend
Engineer then starts that same exact SHA on port 8080 and keeps the backend
child active; after a readiness handoff, Integration QA verifies the frontend
exact SHA in the frontend Project against `localhost:8080`. The Delivery Lead
confirms both tasks use the same daemon, records the service SHA/readiness, and
has the process owner stop the known service after QA. Never terminate an
unknown process.

## Required stages and exact-SHA handoffs

The Delivery Lead decomposes and assigns, each implementer tests and commits,
the Independent Reviewer reviews the exact SHA set, Integration QA verifies the
same exact SHA set, then the Delivery Lead evaluates gates and merges. Every
handoff states child Issue, repository, branch, exact SHA, changed paths,
commands with exit codes, evidence, and concerns. Reviewer and QA terminate at
a structured verdict completion. Core/plan-parent is the sole fan-in and
decision authority: it emits canonical JSON and does not create a FailureBundle,
Stage, or child. Delivery Lead is the sole execution actor: after validating
that canonical JSON, it creates one immutable FailureBundle and executes one
repair Stage with one bundle-bound child per legal owner and its existing
managed PR. No role routes a gate failure directly to an Implementer.

All children use version-2 ordered native Multica Stages: implementation,
exact-SHA review/QA, bounded repair plus fresh gates, then post-merge smoke.
Child `done` means execution finished, not PASS. Each child records workflow
version, phase kind, `pass|fail|blocked`, attempt, evidence comment UUID,
affected SHA, and implementation/repair PR as string metadata. Non-PASS review
and QA records also carry legal owners and a canonical HTTPS evidence-comment
URL. FAIL and BLOCKED children still become `done` so the Stage barrier wakes
Delivery Lead.

Delivery Lead calls `tools.multica.workflow plan-parent`; execution roles call
`tools.multica.workflow finish-phase`. Stage ordinals and action keys are
monotonic and idempotent. Automatic repair rounds are exactly 1 and 2. A member
comment may authorize only the exact current FailureBundle's exact next round 3,
once. If round 3 fails, block the parent; do not create another repair child.
The scheduled Watcher may rerun one stale existing current assignment; it cannot
create a FailureBundle or dispatch repair, invent work, waive a gate, merge, or
deploy. Version mismatch, malformed bundle, and PR drift block visibly for
human resolution.

After an automatic merge and post-merge smoke PASS, approved unattended local
development calls `tools.multica.workflow finish-parent` and moves the parent
directly to `done`. Routine acceptance does not stop in `in_review`. The helper
must fail closed if the merged smoke decision is incomplete or changes between
its two authoritative reads.

## Merge and deployment gates

Automatic merge is permitted only when every affected child has satisfied its
acceptance criteria, tests, independent review, integration QA, exact-SHA
evidence, and repository policy; each affected pull request remains mergeable;
the pull request head still equals the exact SHA reviewed by Independent Reviewer and verified by Integration QA; and all required repository checks still succeed.
The Delivery Lead records the gate decision before merging each approved pull
request. Local merged code may start and run smoke checks automatically.
Production deployment is always human-triggered and is never initiated by this
Squad.

## Partial cross-repository merge

If one repository merges and another cannot merge, stop the parent Issue and
escalate. Report the merged repository and SHA, the unmerged repository and
blocking gate, interface impact, rollback or compatibility options, and the
human decision required. Do not mark the parent complete or claim a fully
integrated result until the escalation is resolved.

## Forbidden actions

No role may conceal secrets, replace exact SHA evidence with a branch name,
bypass child-Issue routing, waive independent gates, or assume ambiguous scope.
Push, tag, release, and deployment require separate authority.
