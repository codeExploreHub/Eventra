# Multica pilot Issues and evidence runbook

Use the three bodies below as separate parent Issues. They are deliberately
small, copy-ready pilots of the three Multica routing modes: `frontend-only`,
`backend-only`, and `cross-stack`.

The final section adds eight repository-knowledge scenarios. Run them only
after the read-only audit, dry run, and explicit live-mutation approval in the
adapter runbook. Every Curator pass processes at most one candidate, stops a
knowledge PR at human review, and must never merge or deploy.

## Dispatch and evidence rules (apply to every pilot)

1. Create **one parent Issue** from the selected body. Bind it to the
   **Eventra Local Development** Project, assign it to the **Eventra Local
   Delivery** Squad, and move it from **backlog** to **todo**. The Delivery
   Lead classifies and decomposes it, then keeps the parent **in progress**
   until every listed gate, merge, and post-merge smoke check has evidence.
2. The Delivery Lead creates and assigns the routed child Issue or Issues
   below. Frontend children remain in **Eventra Local Development**; backend
   children are created in **Eventra Backend Local Development** and link back
   to the parent. Each child names its owner, authoritative repository, base SHA,
   acceptance criteria, interface contract (when applicable), and evidence.
   The nested frontend `Backend/` directory is never a repository target.
   Create implementation children in Stage 1 with `--parent` and `--stage`.
   Later Stages are exact-SHA review/QA, bounded repair followed by fresh
   gates, and post-merge smoke. Never reuse a Stage ordinal.
3. Every implementer returns its child Issue to the Delivery Lead with the
   repository, base and feature branches, pull-request link, **exact SHA**,
   changed paths, commands and exit codes, results, contract notes, and
   concerns. A branch name is not evidence.
   The PR body includes `Closes PRO-N` for the implementation child and
   `Related to PRO-M` for its parent. After evidence, every execution role
   calls `tools.multica.workflow finish-phase`. Child `done` means phase
   execution finished; `eventra.phase.result=pass|fail|blocked` is the verdict.
   FAIL and BLOCKED still become `done` so the native Stage barrier advances.
4. The Delivery Lead sends the immutable exact SHA (or exact SHA pair) to the
   **Independent Reviewer** and **Integration QA**. Their approval or QA
   result is valid only for that SHA set. A replacement commit requires fresh
   review and QA on its new exact SHA.
   For a cross-stack pair, create one Reviewer task per Project. Integration QA
   first verifies the backend SHA in **Eventra Backend Local Development**;
   Backend Engineer then starts that verified SHA on port 8080 and keeps its
   child active while Integration QA verifies the frontend SHA in **Eventra
   Local Development** against `localhost:8080`. Record the shared daemon,
   service SHA, start/readiness evidence, and process-owner cleanup. If the
   exact service cannot remain available, block the gate.
5. An **automatic merge** is permitted only after each affected PR is
   mergeable, its head equals the reviewed and QA-tested exact SHA, all Issue
   acceptance criteria and required repository checks pass, and both
   independent gates pass. The Delivery Lead records the gate decision before
   merging. For `cross-stack`, wait for both PRs to pass before the coordinated
   automatic merge; merge in the API-compatible order. If one merge succeeds
   and the other fails, stop, mark the parent blocked, preserve the exact merge
   evidence, and escalate—do not deploy, auto-revert, or continue.
   Delivery Lead calls read-only `tools.multica.workflow plan-parent` before
   each transition and permits at most two complete repair attempts.
6. After merge, record the merged SHA(s), started local services, commands and
   exit codes, and observed result. Local development may automatically start
   the merged applications and run smoke checks. **Production deployment is
   always human-triggered; it is never automatic and is outside this runbook.**

### Evidence template

Use this compact record for every child handoff, review, QA result, merge, and
post-merge smoke result:

| Field | Record |
| --- | --- |
| Parent / child Issue | Identifiers and current parent status |
| Repository / branch / PR | Authoritative repository, base branch, feature branch, PR link |
| SHA | Exact submitted, reviewed, QA-tested, or merged SHA (state which) |
| Scope / contract | Changed paths; frozen API contract and compatibility notes when applicable |
| Repository knowledge | Context Receipt; current-code verification or conflict; candidate block reference or `none` |
| Checks | Command, exit code, concise result, and repository-check status |
| Independent gates | Independent Reviewer decision and Integration QA decision, each tied to the exact SHA |
| Smoke / concerns | Local command, exit code, observed behavior, limitations, and escalation if needed |

## Pilot 1 — frontend-only

### Parent Issue body

**Title:** Show a development-only local API indicator

**Classification and dispatch:** `frontend-only`. Bind this one parent Issue
to **Eventra Local Development**, assign it to **Eventra Local Delivery**, and
move it from backlog to todo. The Delivery Lead creates one frontend child
Issue assigned to **Eventra Frontend Engineer**. Keep the parent in progress
through the exact-SHA review, QA, automatic merge, and merged local smoke
evidence.

**Change:** Add a small development-only indicator to the Eventra UI showing
that the API target is local. Derive its text from the configured API base URL.
Render it only when the API hostname is `localhost` and `NODE_ENV` is
`development`, and meet existing responsive and accessibility conventions. Do
not modify backend code.

**Repository and PR boundary:** Authoritative frontend repository only;
exactly one frontend PR. The child must not inspect, edit, test, or register
the nested frontend `Backend/` directory.

**Acceptance and required checks:**

- Add a focused test for the visibility logic, including localhost/development
  visibility and a hidden non-local or non-development case.
- Run `npm run test:local-contract`, the focused test, `npm run lint`, and
  `npm run build`; record every command and exit code.
- The Independent Reviewer reviews the submitted exact frontend SHA. Integration
  QA verifies that same SHA with the frontend behavior and local-browser or
  smoke evidence; neither gate may self-approve or use a moving branch.
- Confirm the PR is mergeable, the head remains the reviewed and QA-tested
  exact SHA, and all required repository checks pass. The Delivery Lead records
  these gates and performs the automatic merge only then.
- After merge, record the frontend merged SHA and `npm run smoke:local` exit
  code with the indicator observed only in the required local development
  condition. Keep production deployment human-triggered.

**Handoff route:** Frontend Engineer → Delivery Lead → Independent Reviewer and
Integration QA → Delivery Lead merge decision. Review or QA findings return to
the frontend child; a corrected commit supplies a new exact SHA for both gates.

## Pilot 2 — backend-only

### Parent Issue body

**Title:** Add a public API metadata endpoint

**Classification and dispatch:** `backend-only`. Bind this one parent Issue to
**Eventra Local Development**, assign it to **Eventra Local Delivery**, and
move it from backlog to todo. The Delivery Lead creates one backend child Issue
in **Eventra Backend Local Development**, links it to the parent, and assigns it
to **Eventra Backend Engineer**. Keep the parent in progress until the
exact-SHA gates, automatic merge, and merged local backend smoke evidence are
recorded.

**Change:** Add `GET /api/meta` returning stable JSON fields `service` and
`apiVersion`. Keep it public in Spring Security and document it in OpenAPI. Do
not modify frontend code.

**Frozen contract:** `GET /api/meta` returns HTTP 200 JSON with exactly the
stable public fields `service` and `apiVersion` for this pilot. Any later
additive field is a new approved contract change; the cross-stack post-pilot
variant below is the specifically approved one.

**Repository and PR boundary:** Authoritative backend repository only; exactly
one backend PR. No frontend source, nested frontend `Backend/`, or unrelated
repository change belongs in the child.

**Acceptance and required checks:**

- Controller and security tests cover public HTTP 200 access and the response
  schema containing `service` and `apiVersion`.
- Run the focused controller/security tests as
  `scripts/test-local.sh -Dtest=...`, then the complete suite as
  `scripts/test-local.sh`, and `scripts/smoke-local.sh`; record every command
  and exit code. The smoke result must include an observed successful
  `GET /api/meta` response without secrets.
- The Independent Reviewer reviews the submitted exact backend SHA. Integration
  QA verifies that exact SHA's endpoint, public-access behavior, schema, and
  local backend smoke evidence.
- Confirm the PR is mergeable, its head is the exact SHA reviewed and
  QA-tested, and all required repository checks pass. The Delivery Lead records
  the gates and performs the automatic merge only after they all pass.
- After merge, record the backend merged SHA and a fresh merged local
  `scripts/smoke-local.sh` result plus the safe API observation. Production
  deployment remains a human-triggered action.

**Handoff route:** Backend Engineer → Delivery Lead → Independent Reviewer and
Integration QA → Delivery Lead merge decision. Findings return to the backend
child; a changed SHA repeats review and QA.

## Pilot 3 — cross-stack

### Parent Issue body

**Title:** Display backend API version in the Eventra footer

**Classification and dispatch:** `cross-stack`. Bind this one parent Issue to
**Eventra Local Development**, assign it to **Eventra Local Delivery**, and
move it from backlog to todo. The Delivery Lead creates two linked child
Issues: a backend child assigned to **Eventra Backend Engineer** and a frontend
child assigned to **Eventra Frontend Engineer**. Place the backend child in
**Eventra Backend Local Development**, keep the frontend child with the parent,
and link both children to it. Keep the parent in progress
until the exact-SHA pair passes independent review and QA, both PRs complete
the coordinated automatic merge, and merged local smoke evidence is recorded.

**Change:** Use `GET /api/meta` to display the backend `apiVersion` in the
frontend footer. The UI must show a non-disruptive unavailable state when the
request fails. Freeze the response contract before implementation and create
one PR in each authoritative repository.

**Frozen contract (first cross-stack run):**

```json
{
  "service": "<stable service value>",
  "apiVersion": "<stable API version value>"
}
```

`GET /api/meta` remains public. The backend child supplies this contract and
its exact SHA; the frontend child consumes `apiVersion` and preserves a
non-disruptive unavailable state for request, response, or parse failure. Once
the contract is frozen, the children may work in parallel against it; otherwise
the Delivery Lead sequences them by the real dependency.

**Post-backend-pilot variant (required if Pilot 2 has already merged):** Do not
repeat Pilot 2 unchanged. Change the backend contract additively to:

```json
{
  "service": "<unchanged stable service value>",
  "apiVersion": "<unchanged stable API version value>",
  "buildVersion": "<stable build version value>"
}
```

`service` and `apiVersion` remain backward-compatible and unchanged; add
`buildVersion` without removing or renaming either field. The frontend displays
`buildVersion` in the footer and retains the unavailable state. Freeze this
three-field contract before implementation. This variant still requires the
backend child/PR and frontend child/PR, preserving the cross-stack exercise
after `/api/meta` already exists.

**Repository and PR boundary:** Exactly two linked PRs—one in the authoritative
backend repository and one in the authoritative frontend repository. Each child
records its base SHA, feature SHA, frozen contract, and the linked companion
PR. Neither child modifies the nested frontend `Backend/` directory.

**Acceptance and required checks:**

- Backend: add contract tests for the selected frozen response and public
  endpoint behavior. Run focused tests as `scripts/test-local.sh -Dtest=...`
  and the complete suite as `scripts/test-local.sh`, recording commands and
  exits.
- Frontend: add success and failure tests for footer rendering, including the
  selected `apiVersion` or `buildVersion` display and the non-disruptive
  unavailable state. Run the focused tests, `npm run test:local-contract`,
  `npm run lint`, and `npm run build`, recording commands and exits.
- Submit the immutable exact backend SHA to a Reviewer task in the backend
  Project and the exact frontend SHA to a Reviewer task in the frontend
  Project; Delivery Lead combines both component decisions. Integration QA
  first verifies the backend SHA in the backend Project. Backend Engineer then
  starts that same verified SHA on port 8080 and keeps the backend child active.
  After its exact-SHA readiness handoff, Integration QA verifies the frontend
  SHA in the frontend Project with Playwright/local-browser evidence against
  `localhost:8080`, including the success view and unavailable state. Record
  that both tasks use the configured daemon and have the process owner clean up
  only the known backend service after QA.
- Both PRs must be mergeable; both heads must still equal the SHA pair reviewed
  and QA-tested; every listed test, build, acceptance criterion, and required
  repository check must pass. The Delivery Lead records one coordinated gate
  decision before automatic merge.
- Merge both approved PRs consecutively in API-compatible order (normally
  backend then frontend) only after the two-repository gate passes. If the
  second merge fails, stop, block and escalate the parent with merged SHA,
  unmerged repository, failed gate, interface impact, and recovery options; do
  not deploy, auto-revert, or claim completion.
- After both merges, record both merged SHAs, start the local backend and
  frontend, run `scripts/smoke-local.sh` in the backend and `npm run
  smoke:local` in the frontend, and retain the Playwright/local-browser footer
  evidence against the merged result. Production deployment is human-triggered
  and never part of the automatic merge.

**Handoff route:** Backend Engineer and Frontend Engineer → Delivery Lead →
Independent Reviewer (exact SHA pair) and Integration QA (same exact SHA pair)
→ Delivery Lead coordinated merge decision. Any changed SHA invalidates the
corresponding review or QA result and returns to its owning child.

## Parent closure checklist

### PRO-116 infrastructure-blocked Smoke recovery

This pilot preserves PRO-120 as `done + blocked`; it never edits or reinterprets
that result. A member may authorize exactly one `retry_smoke_stage` by posting
this byte-for-byte canonical root comment on PRO-116:

```json
{"candidate_shas":{"backend":"c7b9a38a2d05ba05eec6b16c83184653aefba750"},"granted_smoke_retry":1,"source_evidence_comment_uuid":"01a0622e-72e3-7660-9fcd-a806c07a5c0f","source_smoke":"PRO-120"}
```

Store the returned UUID in
`eventra.workflow.smoke_retry_authorization_comment`. Require `plan-parent` to
return `retry_smoke_stage`, then call only
`execute-parent-smoke --expected-action-key ACTION_KEY`. The executor records
the same UUID in `eventra.workflow.smoke_retry_authorization_consumed` and
creates exactly one Stage 4 Integration QA Smoke bound to the unchanged backend
SHA, merged PR #6, source Smoke, evidence UUID, and original PASS Gate. The
retry retains the mandatory fresh fetch, exact `FETCH_HEAD`, clean detached
worktree, health/OpenAPI evidence, Context Receipt, and owned cleanup.

The executor is single-flight for the exact parent/action across local
processes and linked worktrees. Same-key contenders perform no writes;
conflicting keys fail closed; a process crash releases the kernel lease while
the durable Smoke reservation preserves resumability. Its revision-fenced
authority envelope keeps exact source-Issue and retry-comment revisions,
authorization content, source evidence, candidate SHA, merged PR,
Project/Squad assignment, Stage/attempt, unique run, and durable commit checks
while allowing only expected child/run startup transitions. Full authority is
reread immediately before and after child promotion. The deterministic retry
and recovery fixtures reduce external reads from `1293 -> 475` and `898 ->
314`, respectively.

For an ambiguous Multica connectivity failure, use the read-only
`tools.multica.workflow diagnose-tls` entry point. It distinguishes route, TLS
handshake, HTTP authentication, and business timeout failures without returning
credentials. The Multica 0.4.38 / Go 1.26 local-proxy ML-KEM case uses only the
process-level `GODEBUG=tlsmlkem=0` workaround; global configuration changes are
forbidden.

```text
PRO-120 done+blocked -> member authorization -> retry_smoke_stage
-> exactly one Stage 4 Smoke -> done+pass -> complete_parent -> PRO-116 done
```

If Stage 4 is BLOCKED or FAIL, keep PRO-116 blocked and preserve both evidence
records. A second retry, manual child, result rewrite, degraded provenance,
deployment, or production mutation is forbidden.

Before moving a parent from in progress to done, the Delivery Lead verifies the
evidence template contains all routed child records, the required PR count,
review and QA results for the final exact SHA set, required checks, merge
evidence, and merged local smoke evidence. The closure record must state that
production deployment was not triggered; a human separately decides whether to
deploy.

## Repository knowledge loop scenarios

These scenarios validate the learning path independently of delivery success.
Use existing completed or blocked pilot parents where possible; do not invent a
candidate merely to make a scenario pass. For every run capture the source
parent/child, evidence comment UUID, candidate digest, deterministic action key,
target Project, resulting Issue/PR identity or terminal no-create decision, and
delivery completion time. The Curator processes at most one candidate per pass,
all PRs stop for human review, and Agents must never merge a knowledge PR or
trigger production deployment.

### K1 — no-candidate frontend delivery

Complete a frontend-only parent whose Agents retrieve a Context Receipt but
discover no novel reusable fact. Delivery Lead records
`eventra.knowledge.status=none` and posts no summary pointer. A read-only scan
and plan find no pending parent from this delivery; a Curator run creates no
Issue or PR. Expected result: normal delivery closure and unchanged business
lead time prove that the learning loop is optional and non-blocking.

### K2 — backend incident learning

Use a backend failure whose resolution produces one current-code-verified,
public-repository-safe invariant or known pitfall. The backend Agent posts one
candidate and Delivery Lead posts its digest-only summary. Expected result: one
deterministic Issue in **Eventra Backend Local Development**, one change limited
to backend `AGENTS.md` or `docs/agent-knowledge/**`, valid indexes/digests, and
one Eventra-Backend PR stopped at `pr_open`. Human review confirms the claim
without receiving credentials, payloads, or raw logs.

### K3 — cross-repository contract

Use an accepted frontend/backend contract decision that is useful to both
repositories. The candidate uses `cross_repo` scope and identifies both exact
source SHAs. Expected result: one shared knowledge Issue routed to **Eventra
Local Development**, one frontend knowledge-only PR updating the shared
`docs/delivery-knowledge/**` entry/index, and retrieval from both repository
contexts after an exact merged-SHA verification. No duplicate backend PR is
created.

### K4 — duplicate candidate

Submit a semantically identical candidate twice, or replay an already observed
summary/action key. Expected result: the existing active knowledge ID or
existing deterministic knowledge Issue is returned, the summary reaches the
deduplicated/previously-dispatched terminal result, and no second Issue, branch,
or PR appears. Record the duplicate reason and increment the duplicate metric.

### K5 — concurrent Curator triggers

After one valid pending candidate exists, invoke two approved Curator passes
close together. Both must use the same candidate digest and deterministic
action key. Expected result: authoritative rereads converge on one target Issue;
one pass may create it and the other becomes a no-op/recovery observation. Each
pass still handles at most one candidate, and no duplicate PR is opened.

### K6 — business-code path rejection

From an assigned knowledge Issue, deliberately include a harmless business-code
path such as frontend `src/App.tsx` or backend `src/main/java/App.java` beside a
knowledge document, without committing or pushing it. Run `check-change` over
the complete staged text. Expected result: a redacted `invalid knowledge change`
failure, no PR, no approval or merge, and no business-code mutation through the
knowledge path. Remove the test edit recoverably before continuing.

### K7 — stale knowledge correction

Have an Agent retrieve an indexed entry that conflicts with current code at an
exact SHA. The Context Receipt records the conflict; the candidate points to
the stale knowledge ID and proposes a verified correction or replacement.
Expected result: one knowledge Issue/PR updates the claim and index lifecycle
fields, preserves provenance, and marks replacement/deprecation only after
human review and merge. Until exact merged-SHA verification, current code wins
and the stale entry is not treated as trusted.

### K8 — paused Curator continuity

With explicit approval, pause only the Curator Autopilot and reread it. Confirm
the existing Watcher remains active, the five-person delivery Squad and both
Projects are unchanged, delivery can finish, and new summary pointers remain
durable without Issues or PRs being created. After separate approval, resume
the same Curator and verify oldest-first processing at most one candidate per
pass. Do not delete queued evidence, recreate terminal PRs, or alter production
deployment state.
