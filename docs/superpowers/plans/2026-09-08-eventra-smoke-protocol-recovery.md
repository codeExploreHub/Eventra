# Eventra Smoke Protocol Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Eventra post-merge smoke creation race-free and scope-aware, then recover PRO-127 through its single authorized smoke retry.

**Architecture:** The control helper will commit and verify the complete smoke assignment before starting its agent, and will explicitly reconcile an older committed assignment whose reservation was stranded after the child started. Integration QA will select a local smoke route from the parent candidate scope and will use external temporary detached worktrees without moving the Multica-managed branch.

**Tech Stack:** Python 3 standard library, `unittest`, Multica CLI, GitHub CLI, Markdown agent instructions.

**Spec:** `docs/superpowers/specs/2026-09-08-eventra-smoke-protocol-recovery-design.md`

## Global Constraints

- Do not modify Eventra business code, PR #18, or Eventra-Backend business code.
- Do not deploy, release, access production data, or terminate unknown processes.
- Do not create replacement Gate tasks or more than the existing one-time smoke retry.
- Keep exact candidate SHA, PR, assignment, Stage, and evidence validation fail-closed.
- Do not genericize this pilot fix into `multica-multi-repo-delivery`.
- Preserve the user's current checkout and all unrelated untracked files.

---

### Task 1: Commit the smoke assignment before starting its agent

**Files:**
- Modify: `tools/multica/workflow.py`
- Modify: `tools/multica/tests/test_workflow.py`

**Interfaces:**
- Consumes: `_resume_smoke_reservation`, `_metadata_set_observed`, `_metadata_delete_observed`, `load_parent_snapshot`, `_smoke_assignment_problem`.
- Produces: `_start_committed_smoke_child(runner, github, parent_key, expected_action_key, effects) -> SmokeExecutionResult` and a reordered `_resume_smoke_reservation`.

- [ ] **Step 1: Write the fast-agent failing test**

Add a fake-runner hook that changes the smoke child to a terminal run immediately after `issue status CHILD todo`. Add this test to `SmokeExecutionTests`:

```python
def test_fast_smoke_agent_completion_cannot_strand_reservation(self):
    runner, github, decision = self._planned()
    runner.complete_smoke_immediately_after_status = True

    result = execute_parent_smoke(
        runner, github, "PRO-65", expected_action_key=decision.action_key
    )

    self.assertEqual(result.next_action, "smoke", result.reason)
    self.assertNotIn(SMOKE_RESERVATION_KEY, runner.metadata["PRO-65"])
    self.assertEqual(
        runner.metadata["PRO-65"]["eventra.workflow.last_action"],
        decision.action_key,
    )
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -B -m unittest tools.multica.tests.test_workflow.SmokeExecutionTests.test_fast_smoke_agent_completion_cannot_strand_reservation
```

Expected: FAIL because the current executor performs stable reservation reads after promotion and leaves the reservation present.

- [ ] **Step 3: Implement commit-before-start**

In `_resume_smoke_reservation`, keep creation and canonical child metadata initialization unchanged, then:

```python
desired_parent = {
    "eventra.workflow.next_stage": str(int(reservation["next_stage"]) + 1),
    "eventra.workflow.last_action": str(reservation["action_key"]),
}
```

Write and verify those fields while the child is still `backlog`, delete and verify the reservation, load the committed parent snapshot, and require `_smoke_assignment_problem(...) is None`. Only then call `_start_committed_smoke_child`.

`_start_committed_smoke_child` must:

```python
if detail["status"] == "backlog" and not runs:
    runner.run(["issue", "status", child_key, "todo", "--output", "json"])
    # Success requires a non-backlog status and at least one newly observed run.
elif detail["status"] in {"todo", "in_progress", "in_review"}:
    # Require exactly one active run; return noop on replay.
elif detail["status"] in {"done", "blocked", "cancelled"}:
    # Require no active run and at least one terminal run; return noop on replay.
else:
    raise RuntimeError("committed smoke child run state is conflicting")
```

Do not reread reservation-bound metadata after starting the agent.

- [ ] **Step 4: Run the focused test and existing smoke executor tests**

Run:

```bash
python3 -B -m unittest tools.multica.tests.test_workflow.SmokeExecutionTests
```

Expected: all tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add tools/multica/workflow.py tools/multica/tests/test_workflow.py
git commit -m "fix(multica): commit smoke assignment before dispatch"
```

### Task 2: Reconcile a committed smoke assignment with a stranded reservation

**Files:**
- Modify: `tools/multica/workflow.py`
- Modify: `tools/multica/tests/test_workflow.py`

**Interfaces:**
- Consumes: the reservation decoder, stable `load_parent_snapshot`, `_smoke_assignment_problem`, and observed metadata deletion.
- Produces: `_reconcile_committed_smoke_reservation(runner, github, parent_key, reservation, effects) -> SmokeExecutionResult`.

- [ ] **Step 1: Write the PRO-134-shaped failing test**

Create the exact smoke child and canonical metadata, set parent `next_stage` and `last_action`, leave `SMOKE_RESERVATION_KEY`, and give the child a failed terminal run plus `status="blocked"`. Assert:

```python
result = execute_parent_smoke(
    runner, github, "PRO-65", expected_action_key=decision.action_key
)
self.assertEqual(result.next_action, "noop", result.reason)
self.assertNotIn(SMOKE_RESERVATION_KEY, runner.metadata["PRO-65"])
self.assertEqual(len(smoke_children), 1)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run the new test directly. Expected: FAIL with `smoke reservation parent or child authority conflicts`.

- [ ] **Step 3: Implement exact committed-reservation reconciliation**

When a reservation exists and parent metadata already contains both the reserved action and `next_stage = reserved_stage + 1`, read the full parent snapshot twice. Require identical snapshots, one current smoke child, exact `_smoke_assignment_problem` success, exact merged PR heads, and a valid active or terminal child run. Delete only `SMOKE_RESERVATION_KEY`, reread the parent, and return `noop` without starting or duplicating a child.

Any mismatched action, Stage, child identity, provenance, candidate SHA, PR state, assignment, or run state returns `block` without mutation.

- [ ] **Step 4: Add conflict cases**

Cover a wrong candidate SHA, duplicate Stage child, backlog child with an unexpected run, and terminal child with no run. Each must retain the reservation and perform zero mutations.

- [ ] **Step 5: Run `SmokeExecutionTests` and verify GREEN**

Run the complete test class and require zero failures.

- [ ] **Step 6: Commit Task 2**

```bash
git add tools/multica/workflow.py tools/multica/tests/test_workflow.py
git commit -m "fix(multica): reconcile committed smoke reservations"
```

### Task 3: Make Integration QA smoke scope-aware and branch-safe

**Files:**
- Modify: `tools/multica/instructions/integration_qa.md`
- Modify: `tools/multica/instructions/eventra_project.md`
- Modify: `tools/multica/instructions/delivery_lead.md`
- Modify: `tools/multica/README.md`

**Interfaces:**
- Consumes: parent `classification`, exact candidate SHA map, merged PR refs, existing repository commands.
- Produces: explicit frontend-only, backend-only, and cross-stack smoke routes.

- [ ] **Step 1: Update the three instruction sources and README**

Specify these exact routes:

```text
frontend-only -> exact frontend PR fetch -> temporary detached worktree ->
focused regressions + test:local-contract -> dev:local -> wait/curl port 3000

backend-only -> exact backend PR fetch -> temporary detached worktree ->
run-local.sh -> smoke-local.sh on port 8080

cross-stack -> exact pair -> Backend Engineer readiness handoff on 8080 ->
frontend dev:local -> npm run smoke:local
```

Require QA to leave the Multica-managed task worktree HEAD/branch unchanged and clean up only its temporary worktree and owned processes.

- [ ] **Step 2: Validate the rendered configuration behavior**

Run `test_provision`, then run a live provision dry-run and inspect its structured
plan. The dry-run must identify only the intended instruction updates and must
not propose Project/resource deletion, production action, or secret changes.
The definitive behavior test is Task 5's real frontend-only retry: it must run
without port 8080 and leave the Multica-managed branch unchanged.

- [ ] **Step 3: Commit Task 3**

```bash
git add tools/multica/instructions/integration_qa.md tools/multica/instructions/eventra_project.md tools/multica/instructions/delivery_lead.md tools/multica/README.md
git commit -m "fix(multica): make smoke verification scope aware"
```

### Task 4: Verify and install the pilot control-plane fix

**Files:**
- No additional source files.

**Interfaces:**
- Consumes: Tasks 1–3 commits and the existing provisioner.
- Produces: tested pilot helper code in the authoritative control checkout and exact live Integration QA instruction reconciliation.

- [ ] **Step 1: Run full local verification**

```bash
python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'
python3 -B -m compileall -q tools/multica
git diff --check HEAD~3..HEAD
```

Require exit 0 for every command.

- [ ] **Step 2: Integrate the tested branch into the authoritative checkout**

Verify the main checkout has no tracked changes, then fast-forward `codex/eventra-candidate-refresh-design` to `fix/eventra-smoke-protocol`. Preserve its unrelated untracked files.

- [ ] **Step 3: Run a provision dry-run**

Use `GODEBUG=tlsmlkem=0`, the desktop profile, workspace ID, and `--reuse-backend-env`. Require the plan to show only intended agent/instruction drift and no Project/resource deletion or production action.

- [ ] **Step 4: Apply and reread live configuration**

Apply the exact dry-run plan, then reread Integration QA and Delivery Lead details. Require the installed instructions to match the tested local files.

### Task 5: Reconcile PRO-134 and execute the one authorized retry

**Files:**
- No source changes.

**Interfaces:**
- Consumes: corrected helper, installed instructions, PRO-134 evidence comment `01a07f00-fa56-788e-8aa3-65f30f31baee`, PR #18 SHA `72d7684c2a3b4fde6566882ec921613341f6c58f`.
- Produces: canonical PRO-134 `done + blocked`, one retry smoke Stage, and either PRO-127 `done` after PASS or an exact blocker.

- [ ] **Step 1: Reconcile the stale reservation**

Run the same `execute-parent-smoke` action key. Require `decision=noop`, exactly one Stage 5 child, and absence of `eventra.workflow.smoke_reservation`.

- [ ] **Step 2: Canonically finish PRO-134 as blocked**

Move PRO-134 to `in_progress --no-start`, then call:

```bash
python3 -B -m tools.multica.workflow finish-phase PRO-134 \
  --kind smoke --result blocked --attempt 1 \
  --frontend-sha 72d7684c2a3b4fde6566882ec921613341f6c58f \
  --evidence-comment 01a07f00-fa56-788e-8aa3-65f30f31baee
```

Verify `status=done`, result `blocked`, no responsible repository, and unchanged creation provenance.

- [ ] **Step 3: Authorize exactly one retry**

Move PRO-127 to `blocked --no-start`. Post one member root comment whose canonical body is:

```json
{"candidate_shas":{"frontend":"72d7684c2a3b4fde6566882ec921613341f6c58f"},"granted_smoke_retry":1,"source_evidence_comment_uuid":"01a07f00-fa56-788e-8aa3-65f30f31baee","source_smoke":"PRO-134"}
```

Store its returned UUID in `eventra.workflow.smoke_retry_authorization_comment` and require `plan-parent PRO-127` to return `retry_smoke_stage`.

- [ ] **Step 4: Execute and monitor the retry**

Pass the returned action key unchanged to `execute-parent-smoke`. Verify exactly one new Stage child, Integration QA assignment, frontend-only candidate SHA, installed scope-aware instructions, and one run. Wait for its terminal result without starting a duplicate.

- [ ] **Step 5: Finish the parent only after PASS**

If the retry is canonical `done + pass`, require `plan-parent PRO-127` to return `complete_parent`, run `finish-parent PRO-127`, and verify the parent is `done`. For any other result, leave PRO-127 blocked and report the exact evidence; do not retry again.
