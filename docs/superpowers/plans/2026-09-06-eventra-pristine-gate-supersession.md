# Eventra Pristine Gate Supersession Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicitly authorized, crash-recoverable v2 candidate-refresh path that cancels exactly two pristine stale Stage 2 gates, prepares the refreshed candidate in Stage 3, and creates fresh exact-SHA gates in Stage 4.

**Architecture:** Preserve candidate-refresh v1 byte-for-byte and add a strict v2 request selected only by `--supersede-pristine-gates`. Pure authority and state transitions remain in `candidate_refresh.py`; complete live reads and durable effects remain in `refresh_executor.py`; CLI, trusted deployment contracts, and ordinary-workflow holds remain in `workflow.py`. Every external mutation is preceded and followed by a complete authority read and is recoverable from a digest-bound parent reservation.

**Tech Stack:** Python 3 standard library, `unittest`, Multica CLI 0.4.38 JSON boundaries, GitHub CLI/API adapters, Git, Next.js/npm regression commands.

**Spec:** `docs/superpowers/specs/2026-09-06-eventra-pristine-gate-supersession-design.md`

## File structure

- Modify `tools/multica/candidate_refresh.py`: versioned request/grant contracts, pristine-gate authority, dynamic refresh stages, reservation/receipt validation, pure decisions.
- Modify `tools/multica/refresh_executor.py`: complete child history reads, deterministic cancellation, prefix recovery, dynamic-stage execution, final receipt persistence.
- Modify `tools/multica/workflow.py`: explicit plan flag, v2 preview and fenced blocks, request-file validation, trusted deployment contracts, ordinary workflow holds.
- Modify `tools/multica/tests/test_candidate_refresh.py`: v1 compatibility and v2 pure-contract/state-machine tests.
- Modify `tools/multica/tests/test_refresh_executor.py`: read-boundary, cancellation, failure injection, publication, and end-to-end tests.
- Modify `tools/multica/tests/test_workflow.py`: parser, output, deployment contract, hold, and routing tests.
- Modify `tools/multica/README.md`: v2 operator protocol and recovery runbook.
- Modify `tools/multica/instructions/delivery_lead.md`: request/grant/cancellation and Stage 4 handoff rules.
- Modify `tools/multica/instructions/frontend_engineer.md`: Stage 3 refresh preparation constraints.
- Modify `tools/multica/instructions/independent_reviewer.md`: ignore superseded gates and inspect only Stage 4 exact SHA.
- Modify `tools/multica/instructions/integration_qa.md`: ignore superseded gates and test only Stage 4 exact SHA.
- Modify `docs/multica/pilot-issues.md`: PRO-122 pilot state and evidence expectations.

## Global constraints

- Candidate-refresh v1 payloads, digests, fenced blocks, action keys, Stage 2 refresh, and Stage 3 gate behavior remain unchanged.
- v2 is selected only by `plan-refresh --supersede-pristine-gates`; live state never auto-selects it.
- v2 supports only frontend-only, attempt 0, refresh generation 1, one pristine Reviewer/Integration QA pair, Stage 3 refresh, and Stage 4 fresh gates.
- No gate mutation occurs before an exact v2 member grant is bound and a durable reservation is read back.
- A stale gate is eligible only at `backlog`, `status_category=backlog`, revision 1, with empty metadata, comments, runs, and evidence.
- The only accepted cancellation result is `cancelled`, `status_category=cancelled`, revision 2, with all other nonvolatile authority unchanged.
- Partial cancellation is never rolled back; unexplained state blocks all later effects.
- PRO-123 evidence is immutable. PRO-124/125 never receive fabricated phase verdicts or repair provenance.
- PR merges, live deployment configuration, PRO-124/125 mutation, PR #14 merge, deploy, Smoke, and Knowledge Loop are outside local implementation authorization.
- Use `apply_patch` for edits, TDD for every behavior change, and a separate commit for each task.

### Task 1: Define versioned v2 request and pristine-gate authority

**Files:**

- Modify: `tools/multica/candidate_refresh.py`
- Modify: `tools/multica/tests/test_candidate_refresh.py`

**Interfaces:**

- Change `freeze_refresh_request(snapshot: RefreshSnapshot, *, supersede_pristine_gates: bool = False) -> RefreshRequest`.
- Add `refresh_protocol(request: RefreshRequest) -> int` returning only `1` or `2`.
- Add `supersession_preview(request: RefreshRequest) -> tuple[dict[str, object], ...]` returning an empty tuple for v1 and two role-ordered records for v2.
- Keep `build_request(payload)`, `parse_request(value)`, and `RefreshRequest.payload()` as the single strict wire-validation boundary.
- Add test helper `pristine_gate_snapshot(**overrides) -> RefreshSnapshot`; it constructs explicit snapshot bytes and never calls production freeze/admission helpers.

- [ ] Write v1 compatibility and v2 request RED tests in `RequestTests`.

```python
def test_v1_request_bytes_and_action_remain_unchanged(self):
    request = self.c.build_request(request_payload())
    self.assertEqual(self.c.refresh_protocol(request), 1)
    self.assertEqual(request.payload()["refresh_stage"], 2)
    self.assertNotIn("supersession", request.payload())

def test_v2_request_binds_exact_pristine_gate_pair(self):
    snapshot = pristine_gate_snapshot()
    request = self.c.freeze_refresh_request(
        snapshot, supersede_pristine_gates=True)
    payload = request.payload()
    self.assertEqual(payload["schema_version"], 2)
    self.assertEqual(payload["refresh_stage"], 3)
    self.assertEqual(payload["fresh_gate_stage"], 4)
    self.assertEqual(
        [gate["role"] for gate in payload["supersession"]["gates"]],
        ["independent_reviewer", "integration_qa"],
    )
```

- [ ] Add table-driven RED cases proving that missing/extra fields, reordered or duplicate roles, bad UUIDs, non-1 revisions, non-backlog status, wrong or missing exact titles, wrong stages, and unsafe stage values are rejected by `build_request`.

- [ ] Run the focused tests and record the expected missing-interface/schema failures.

```text
python3 -B -m unittest tools.multica.tests.test_candidate_refresh.RequestTests -v
```

- [ ] Split `build_request` internally into exact v1 and v2 validators without changing the public parser or v1 canonical bytes.

```python
def refresh_protocol(request: RefreshRequest) -> int:
    version = request.payload()["schema_version"]
    _require(type(version) is int and version in {1, 2})
    return version

def freeze_refresh_request(snapshot, *, supersede_pristine_gates=False):
    return (_freeze_v2(snapshot) if supersede_pristine_gates
            else _freeze_v1(snapshot))
```

- [ ] Add a pure `_pristine_gate_projection(state, source)` that returns the exact ordered two-gate authority, including role, identifiers, stable detail, empty metadata/evidence, complete comment manifest, description digest, and matching run absence.

- [ ] Implement `supersession_preview` from validated request bytes only; do not read ambient state or add authority fields to the informational preview.

- [ ] Run request and baseline contract tests GREEN, then run all candidate-refresh tests.

```text
python3 -B -m unittest tools.multica.tests.test_candidate_refresh.RequestTests tools.multica.tests.test_candidate_refresh.BaselineContractTests -v
python3 -B -m unittest tools.multica.tests.test_candidate_refresh
```

- [ ] Check the diff and commit.

```text
git diff --check
git add tools/multica/candidate_refresh.py tools/multica/tests/test_candidate_refresh.py
git commit -m "feat(multica): define pristine gate refresh requests"
```

### Task 2: Bind complete child comments, runs, and pristine eligibility

**Files:**

- Modify: `tools/multica/refresh_executor.py`
- Modify: `tools/multica/candidate_refresh.py`
- Modify: `tools/multica/tests/test_refresh_executor.py`
- Modify: `tools/multica/tests/test_candidate_refresh.py`

**Interfaces:**

- Extend each refresh snapshot child to exact fields `detail metadata evidence comment_manifest`.
- Continue using top-level `runs`, with every run bound to an observed parent or child UUID.
- Add pure `admit_refresh` support for v2 while preserving v1 entry rules.

- [ ] Extend `ReadBoundary` with two exact Stage 2 gate fixtures and add a RED snapshot test.

```python
def test_v2_snapshot_binds_complete_pristine_gate_history(self):
    self.runner.add_pristine_gates()
    snapshot = self.snapshot()
    request = contracts.freeze_refresh_request(
        snapshot, supersede_pristine_gates=True)
    state = snapshot.state()
    gates = [child for child in state["children"] if child["detail"]["stage"] == 2]
    self.assertEqual(len(gates), 2)
    self.assertEqual([gate["comment_manifest"] for gate in gates], [[], []])
    self.assertEqual(contracts.refresh_protocol(request), 2)
```

- [ ] Add RED subtests that inject a gate comment of every known identity/type, a paginated or malformed comment tree, a completed or active run, metadata, evidence, unknown detail field, wrong project/assignee/title/description, and a same-revision content change between the two reads. Assert `runner.writes == []` and `github.writes == []`.

- [ ] Run `SnapshotTests` and capture the first failure at the missing child manifest/eligibility boundary.

```text
python3 -B -m unittest tools.multica.tests.test_refresh_executor.SnapshotTests -v
```

- [ ] Change `RefreshAPI._read_once` to retain `comment_manifest` for every child and reject any run whose `issue_id` is not the parent or one of the fully read children.

- [ ] Extend `_snapshot` and v2 authority projection to require the exact child shape and bind the two comment manifests plus all normalized runs. Keep the v1 authority digest and payload construction unchanged for one-child snapshots.

- [ ] Implement semantic gate checks: exact role assignment, exact title, unique required description markers for parent/PR/source/action/evidence, and full description digest binding.

- [ ] Run snapshot and baseline tests GREEN, followed by the two complete modules.

```text
python3 -B -m unittest tools.multica.tests.test_refresh_executor.SnapshotTests tools.multica.tests.test_candidate_refresh.BaselineContractTests -v
python3 -B -m unittest tools.multica.tests.test_candidate_refresh tools.multica.tests.test_refresh_executor
```

- [ ] Check and commit.

```text
git diff --check
git add tools/multica/candidate_refresh.py tools/multica/refresh_executor.py tools/multica/tests/test_candidate_refresh.py tools/multica/tests/test_refresh_executor.py
git commit -m "feat(multica): bind pristine gate snapshot authority"
```

### Task 3: Add v2 planning, fenced blocks, and exact mutation contracts

**Files:**

- Modify: `tools/multica/workflow.py`
- Modify: `tools/multica/candidate_refresh.py`
- Modify: `tools/multica/refresh_executor.py`
- Modify: `tools/multica/tests/test_workflow.py`
- Modify: `tools/multica/tests/test_candidate_refresh.py`
- Modify: `tools/multica/tests/test_refresh_executor.py`

**Interfaces:**

- Add `--supersede-pristine-gates` only to `plan-refresh`.
- Change `print_refresh_plan(request)` and `_read_refresh_request_file` to accept exact version-specific plan fields.
- Keep the outer `RefreshDeployment` schema at version 1; accept either the existing v1 behavioral contract or exact v2 request-specific contract.
- Add `_require_refresh_mutation_contract(deployment, request, parent)` and call it before any mutating refresh function.
- Add test helper `run_plan(argv, snapshot) -> dict[str, object]`; it patches only the I/O adapters, invokes `workflow.main`, and decodes stdout.

- [ ] Add parser/output RED tests proving the flag defaults false, v1 output is unchanged, and v2 output contains only the expected extra `supersession_preview` field and v2 fenced-block names.

```python
def test_v2_plan_requires_explicit_flag_and_prints_zero_write_preview(self):
    args = build_workflow_parser().parse_args([
        "plan-refresh", "PRO-900", "--prerequisite-pr", FRONTEND_PR,
        "--control-tool-sha", "e" * 40, "--supersede-pristine-gates",
    ])
    self.assertTrue(args.supersede_pristine_gates)
    plan = run_plan(args)
    self.assertEqual(plan["mutation_count"], 0)
    self.assertEqual(len(plan["supersession_preview"]), 2)
    self.assertIn("request-v2", plan["request_comment"])
    self.assertIn("grant-v2", plan["grant_comment"])
```

- [ ] Add deployment RED tests for missing/extra v2 fields, wrong types/order/parent/digest/action/gate UUIDs, v1 contract attempting v2, v2 contract attempting v1, untrusted path, and `mutation_contract=null`. Assert read-only plan accepts null and every mutation rejects it.

- [ ] Run focused workflow tests RED.

```text
python3 -B -m unittest tools.multica.tests.test_workflow.PhaseCompletionTests tools.multica.tests.test_workflow.RefreshWorkflowTests -v
```

- [ ] Route the explicit flag into `freeze_refresh_request(..., supersede_pristine_gates=args.supersede_pristine_gates)`; never infer it from snapshot children.

- [ ] Make request/grant block versions derive from `refresh_protocol(request)`. Strictly validate the version-specific plan field set when reading a request file.

- [ ] Introduce exact `REFRESH_MUTATION_CONTRACT_V1` and v2 validation matching the spec. Preserve the old six-field v1 object and outer deployment schema.

- [ ] Enforce request-specific v2 contract identity before `stage_refresh_request`, `execute_refresh`, or `finish_refresh` can perform a network write. For `finish-refresh`, derive and validate the parent/request through a read-only snapshot before allowing the finish path.

- [ ] Generalize staged metadata version and grant schema to the request protocol while retaining v1 revision-prefix expectations.

- [ ] Run focused tests GREEN and all three refresh/workflow modules.

```text
python3 -B -m unittest tools.multica.tests.test_candidate_refresh tools.multica.tests.test_refresh_executor tools.multica.tests.test_workflow
```

- [ ] Check and commit.

```text
git diff --check
git add tools/multica/workflow.py tools/multica/candidate_refresh.py tools/multica/refresh_executor.py tools/multica/tests/test_workflow.py tools/multica/tests/test_candidate_refresh.py tools/multica/tests/test_refresh_executor.py
git commit -m "feat(multica): expose guarded gate supersession plans"
```

### Task 4: Implement durable, prefix-recoverable gate cancellation

**Files:**

- Modify: `tools/multica/candidate_refresh.py`
- Modify: `tools/multica/refresh_executor.py`
- Modify: `tools/multica/tests/test_candidate_refresh.py`
- Modify: `tools/multica/tests/test_refresh_executor.py`

**Interfaces:**

- Add immutable `SupersessionProgress` with `cancelled_roles: tuple[str, ...]` and `next_role: str | None`.
- Add `validate_supersession_progress(request, snapshot, reservation) -> SupersessionProgress`.
- Add `RefreshAPI.cancel_gate(issue: str) -> object`, emitting only `issue status ISSUE cancelled --no-start`.
- Extend v2 reservation states with `review_cancelled` and `gates_cancelled`; v1 reservation schema remains exact.
- Add test helper `cancel_gate_fixture(data, role) -> None`; it changes only status, status category, and revision according to the observed Multica contract.

- [ ] Add pure RED tests for the only three legal cancellation prefixes: neither cancelled, Reviewer only, both cancelled.

```python
def test_v2_cancellation_prefixes_are_exact(self):
    request, data, reservation = v2_reserved_fixture()
    self.assertEqual(
        self.c.validate_supersession_progress(
            request, self.c.RefreshSnapshot(encode(data)), reservation),
        self.c.SupersessionProgress((), "independent_reviewer"),
    )
    cancel_gate_fixture(data, "independent_reviewer")
    self.assertEqual(
        self.c.validate_supersession_progress(
            request, self.c.RefreshSnapshot(encode(data)), reservation),
        self.c.SupersessionProgress(("independent_reviewer",), "integration_qa"),
    )
```

- [ ] Add table-driven RED cases for revision 0/3, `todo`, `in_progress`, `done`, `blocked`, wrong status category, position/assignee/title/description drift, new comment/run/metadata, QA cancelled before Reviewer, and a gate resurrected after cancellation.

- [ ] Extend `MemoryRefreshAPI` with a distinct `cancel_gate` write and add RED failure-injection tests at: reservation write, Reviewer status before/after, Reviewer checkpoint before/after, QA status before/after, and final checkpoint before/after.

- [ ] Assert every failure prefix has no refresh child, no Engineer run, no Git push, no PR update, and no change to Stage 1 evidence bytes.

- [ ] Run RED tests.

```text
python3 -B -m unittest tools.multica.tests.test_candidate_refresh.RefreshDecisionTests tools.multica.tests.test_refresh_executor.ExecuteRefreshTests -v
```

- [ ] Implement strict v2 reservation parsing and `validate_supersession_progress`. Bind original gate authority in the reservation; never accept a request-file reconstruction as observed authority.

- [ ] Implement `RefreshAPI.cancel_gate` with no generic ability to cancel arbitrary issues. Validate the issue against the next role’s request-bound gate before calling it.

- [ ] In `execute_refresh`, after the v2 reservation is read back, reconcile each deterministic cancellation step. Treat the exact post-state as ACK-loss success, advance the missing reservation state, and never repeat the status call.

- [ ] Keep partial cancellation blocked on unexplained state and never attempt rollback.

- [ ] Run all focused failure boundaries GREEN and confirm the write log contains exactly two cancellation effects on the complete path.

```text
python3 -B -m unittest tools.multica.tests.test_candidate_refresh.RefreshDecisionTests tools.multica.tests.test_refresh_executor.ExecuteRefreshTests -v
```

- [ ] Run both complete modules, check, and commit.

```text
python3 -B -m unittest tools.multica.tests.test_candidate_refresh tools.multica.tests.test_refresh_executor
git diff --check
git add tools/multica/candidate_refresh.py tools/multica/refresh_executor.py tools/multica/tests/test_candidate_refresh.py tools/multica/tests/test_refresh_executor.py
git commit -m "feat(multica): cancel stale gates with durable recovery"
```

### Task 5: Generalize refresh initialization and preparation to request-bound stages

**Files:**

- Modify: `tools/multica/candidate_refresh.py`
- Modify: `tools/multica/refresh_executor.py`
- Modify: `tools/multica/tests/test_candidate_refresh.py`
- Modify: `tools/multica/tests/test_refresh_executor.py`

**Interfaces:**

- Add internal helpers `_refresh_stage(request) -> int` and `_fresh_gate_stage(request) -> int`.
- Keep public `execute_refresh` and `finish_refresh` signatures unchanged.
- Make refresh child provenance bind `eventra.refresh.version` to request protocol and bind the request’s refresh stage.
- Add test helper `admitted_v2_execution() -> tuple[MemoryRefreshAPI, MemoryRefreshGit, RefreshRequest]`; it returns a fully granted, reservation-ready fixture without bypassing admission validation.

- [ ] Add a RED end-to-end initialization test proving v2 creates no child before `gates_cancelled`, then creates exactly one Stage 3 child, sets parent `next_stage=4`, and starts exactly one Engineer run.

```python
def test_v2_initializes_stage_three_only_after_both_gates_cancel(self):
    api, git, request = admitted_v2_execution()
    result = execute_to_dispatch(api, git, request)
    refresh_children = [
        child for child in api.state["children"]
        if child["metadata"].get("eventra.phase.kind") == "refresh"
    ]
    self.assertEqual([c["detail"]["stage"] for c in refresh_children], [3])
    self.assertEqual(api.state["metadata"]["eventra.workflow.next_stage"], "4")
    self.assertEqual(
        sum(write[0] == "set_status" and write[3] is True for write in api.writes),
        1,
    )
```

- [ ] Add RED recovery tests for every v2 child create, metadata prefix, parent next-stage, action, parent status, reservation state, and dispatch write-before/write-after boundary. Assert a single child/run and exact action on every replay.

- [ ] Add v1 control assertions to the same tests: Stage 2 refresh, parent next_stage 3, and existing action bytes.

- [ ] Run `ExecuteRefreshTests` RED.

```text
python3 -B -m unittest tools.multica.tests.test_refresh_executor.ExecuteRefreshTests -v
```

- [ ] Replace literal Stage 2/3 checks in request-driven refresh initialization with `_refresh_stage` and `_fresh_gate_stage`; do not alter ordinary workflow stage calculations.

- [ ] Generalize child creation validation to the exact request stage and update `RefreshAPI.create_child` to accept only the validated stage from the executor call path.

- [ ] Generalize prepared evidence, finish validation, and refresh-child lookup to the request stage. Reject a Stage 2 child for v2 and Stage 3 child for v1.

- [ ] Run execute/finish tests GREEN, then run the complete candidate/executor suites.

```text
python3 -B -m unittest tools.multica.tests.test_refresh_executor.ExecuteRefreshTests tools.multica.tests.test_refresh_executor.FinishRefreshTests -v
python3 -B -m unittest tools.multica.tests.test_candidate_refresh tools.multica.tests.test_refresh_executor
```

- [ ] Check and commit.

```text
git diff --check
git add tools/multica/candidate_refresh.py tools/multica/refresh_executor.py tools/multica/tests/test_candidate_refresh.py tools/multica/tests/test_refresh_executor.py
git commit -m "feat(multica): execute refresh at request-bound stages"
```

### Task 6: Persist supersession receipt and expose only fresh Stage 4 gates

**Files:**

- Modify: `tools/multica/candidate_refresh.py`
- Modify: `tools/multica/refresh_executor.py`
- Modify: `tools/multica/workflow.py`
- Modify: `tools/multica/tests/test_candidate_refresh.py`
- Modify: `tools/multica/tests/test_refresh_executor.py`
- Modify: `tools/multica/tests/test_workflow.py`

**Interfaces:**

- Add `eventra.refresh.supersession` to the exact v2 feature fields only.
- Add strict `_supersession_receipt(feature, request, prepared, state) -> dict[str, object]` validation.
- Keep publication and Git interfaces unchanged.
- Add test helper `prepared_v2_execution() -> tuple[MemoryRefreshAPI, MemoryRefreshGit, RefreshRequest]`; it reaches prepared PASS through the real executor and finish APIs.

- [ ] Add RED publication tests proving the permanent receipt is written and read back before reservation deletion; inject failure before and after each receipt/reservation boundary.

```python
def test_v2_adoption_commits_receipt_before_reservation_cleanup(self):
    api, git, request = prepared_v2_execution()
    execute_refresh(api, git, "PRO-900", uid(12), uid(13),
                    contracts.refresh_action(request))
    self.assertIn("eventra.refresh.supersession", api.state["metadata"])
    self.assertNotIn("eventra.refresh.reservation", api.state["metadata"])
    receipt = json.loads(api.state["metadata"]["eventra.refresh.supersession"])
    self.assertEqual(receipt["refresh_stage"], 3)
    self.assertEqual(receipt["fresh_gate_stage"], 4)
```

- [ ] Add RED drift tests for receipt gate order/UUID/digest/revision, request/grant, source/target/prerequisite/control SHA, refresh child/evidence, stages, missing receipt, extra receipt fields, and receipt present before legal adoption.

- [ ] Add workflow RED tests: after adoption the decision is `create_gate_stage` at Stage 4; only Stage 4 Review+QA with target SHA count; cancelled Stage 2 gates never satisfy, fail, or trigger repair; later repair/Smoke history preserves the receipt.

- [ ] Run publish and parent-decision tests RED.

```text
python3 -B -m unittest tools.multica.tests.test_refresh_executor.PublishRefreshTests tools.multica.tests.test_candidate_refresh.RefreshDecisionTests tools.multica.tests.test_workflow.ParentDecisionTests -v
```

- [ ] Extend v2 adoption to write the canonical receipt after adoption identity is fully durable and before deleting the reservation. Reconcile write-before/write-after failures by exact readback.

- [ ] Require the receipt whenever cancelled Stage 2 gates coexist with an adopted v2 candidate. Preserve v1 feature-field strictness.

- [ ] Generalize the post-adoption gate checks and action to `fresh_gate_stage`; verify exact role, candidate SHA, attempt, assignment, and creation action.

- [ ] Update ordinary history consistency so only receipt-bound cancelled gates are ignored. All unbound cancelled or later children remain conflicts.

- [ ] Run focused tests GREEN, then the full workflow-related modules.

```text
python3 -B -m unittest tools.multica.tests.test_candidate_refresh tools.multica.tests.test_refresh_executor tools.multica.tests.test_workflow
```

- [ ] Check and commit.

```text
git diff --check
git add tools/multica/candidate_refresh.py tools/multica/refresh_executor.py tools/multica/workflow.py tools/multica/tests/test_candidate_refresh.py tools/multica/tests/test_refresh_executor.py tools/multica/tests/test_workflow.py
git commit -m "feat(multica): preserve gate supersession receipts"
```

### Task 7: Hold competing workflows and document the operator protocol

**Files:**

- Modify: `tools/multica/workflow.py`
- Modify: `tools/multica/tests/test_workflow.py`
- Modify: `tools/multica/README.md`
- Modify: `tools/multica/instructions/delivery_lead.md`
- Modify: `tools/multica/instructions/frontend_engineer.md`
- Modify: `tools/multica/instructions/independent_reviewer.md`
- Modify: `tools/multica/instructions/integration_qa.md`
- Modify: `docs/multica/pilot-issues.md`

**Interfaces:**

- No new mutation command.
- `plan-parent`, Watcher, repair, Smoke, `finish-phase`, and `finish-parent` return or raise stable dedicated-refresh hold reasons for every legal v2 prefix.
- Malformed or unknown v2 metadata remains a hard block.

- [ ] Add RED workflow tests for staged intent, grant wait, each cancellation prefix, dispatched refresh, prepared, published, adopted-before-cleanup, and adopted receipt. Exercise Watcher apply mode and every competing mutation helper; assert their runner mutation counts remain zero.

- [ ] Add RED cases for malformed reservation/receipt and unknown v2 fields. Assert they block rather than fall back to v1 or ordinary gate creation.

- [ ] Run the exact workflow classes that own competing decisions and mutations, and record RED failures.

```text
python3 -B -m unittest tools.multica.tests.test_workflow.ParentDecisionTests tools.multica.tests.test_workflow.PhaseCompletionTests tools.multica.tests.test_workflow.ParentCompletionTests tools.multica.tests.test_workflow.RecoveryDecisionTests tools.multica.tests.test_workflow.RecoveryMutationTests tools.multica.tests.test_workflow.WatchWorkflowTests tools.multica.tests.test_workflow.SmokeExecutionTests tools.multica.tests.test_workflow.RepairExecutionTests -v
```

- [ ] Centralize the refresh hold classification used by ordinary workflow paths so legal states produce stable recovery guidance and malformed states produce a stable block reason.

- [ ] Update README with the exact v2 five-command sequence, cancellation preview, mutation-contract schema, partial-cancellation recovery, and explicit statements that chat approval is not a member grant and that merge/deploy/Smoke remain separate.

- [ ] Update role instructions: Delivery Lead may only invoke the exact request; Frontend Engineer owns Stage 3 preparation; Reviewer and Integration QA ignore cancelled Stage 2 tasks and inspect only new Stage 4 tasks at the adopted SHA.

- [ ] Update `pilot-issues.md` with PRO-122/123/124/125 identities and expected transitions, but do not claim live mutations have happened.

- [ ] Run workflow tests GREEN and inspect documentation for conflicting Stage 2/3 language.

```text
python3 -B -m unittest tools.multica.tests.test_workflow
rg -n "Stage 2|Stage 3|Stage 4|supersed|cancel" tools/multica/README.md tools/multica/instructions docs/multica/pilot-issues.md
```

- [ ] Check and commit.

```text
git diff --check
git add tools/multica/workflow.py tools/multica/tests/test_workflow.py tools/multica/README.md tools/multica/instructions/delivery_lead.md tools/multica/instructions/frontend_engineer.md tools/multica/instructions/independent_reviewer.md tools/multica/instructions/integration_qa.md docs/multica/pilot-issues.md
git commit -m "docs(multica): describe controlled gate supersession"
```

### Task 8: Prove end-to-end behavior and complete exact-SHA verification

**Files:**

- Modify: `tools/multica/tests/test_refresh_executor.py`
- Modify: `tools/multica/tests/test_workflow.py`
- Modify only if a failing proof exposes a defect: the production files owned by Tasks 1–7.

**Interfaces:**

- Reuse public `stage_refresh_request`, `execute_refresh`, `finish_refresh`, and `plan_refresh`.
- No test-only bypass around request/grant, reservation, Git-object validation, receipt, or fresh gates.
- Add test helpers `pristine_v2_delivery`, `stage_exact_request_and_grant`, `finish_exact_preparation`, `create_fresh_stage_four_gates`, and `plan_parent`; each composes public production entry points and explicit fake external effects.

- [ ] Add a RED memory-API end-to-end test from pristine Stage 2 gates through Stage 4 gate creation and merge hold.

```python
def test_v2_full_delivery_preserves_history_and_stops_at_merge_hold(self):
    api, git, request = pristine_v2_delivery()
    source_before = copy.deepcopy(api.state["children"][0])
    stage_exact_request_and_grant(api, request)
    execute_refresh(api, git, "PRO-900", uid(12), uid(13),
                    contracts.refresh_action(request))
    finish_exact_preparation(api, git)
    execute_refresh(api, git, "PRO-900", uid(12), uid(13),
                    contracts.refresh_action(request))
    create_fresh_stage_four_gates(api)
    self.assertEqual(plan_parent(api).kind, "noop")
    self.assertEqual(api.state["children"][0], source_before)
    self.assertEqual(git.managed_push_effects, 1)
```

- [ ] Add replay tests over every recorded v2 write boundary, including lost acknowledgements, and assert the same final gate IDs, target SHA, receipt bytes, two cancellations, one refresh child, one Engineer run, and one managed publication.

- [ ] Add negative end-to-end tests for two simultaneous executors, different action keys, changed current base, changed prerequisite ancestry, changed managed PR head, and a resurrected old gate.

- [ ] Run the new end-to-end tests RED, make only proof-driven fixes, and rerun GREEN.

```text
python3 -B -m unittest tools.multica.tests.test_refresh_executor.FullRefreshDeliveryTests -v
```

- [ ] Run the complete Python suite.

```text
python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'
```

- [ ] Run frontend and knowledge regressions serially.

```text
npm run test:local-contract
npm run test:footer-meta
npm run test:layout-hydration
npm run test:dashboard-profile
npm run lint
npm run build
python3 -B -m tools.multica.knowledge verify --frontend-root . --backend-root /Users/didi/Eventra-workspace/Eventra-Backend
```

- [ ] Verify exact branch scope and commit the end-to-end tests or proof-driven fixes.

```text
git diff --check 21078feb54adbe1c9fece2e692624369c9f204d2..HEAD
git status --short
git diff --stat 21078feb54adbe1c9fece2e692624369c9f204d2..HEAD
git add tools/multica/tests/test_refresh_executor.py tools/multica/tests/test_workflow.py
git commit -m "test(multica): prove pristine gate supersession delivery"
```

- [ ] If a proof-driven production or documentation fix exists, stage only its explicit path in the same final commit; never use broad repository-root staging.

- [ ] Request an independent exact-SHA review of base `21078feb54adbe1c9fece2e692624369c9f204d2` to the final HEAD. Require findings by severity, v1 compatibility confirmation, v2 authority/cancellation/recovery review, and verification that no business or Backend files changed.

- [ ] Resolve review findings with a new RED → GREEN cycle and rerun all affected plus full verification commands.

- [ ] Stop with a clean local branch and report the exact HEAD, commits, test counts, known concerns, and read-only live-preflight command. Do not push, create a PR, merge, install live configuration, stage a request, publish a grant, cancel gates, update PR #14, deploy, or run Smoke without separate authorization.

## Execution handoff

The plan is designed for `superpowers:executing-plans` in the current isolated worktree with review checkpoints after Tasks 2, 4, 6, and 8. If agent delegation is explicitly selected, use `superpowers:subagent-driven-development` and give each worker ownership only of the task’s listed files; workers must preserve concurrent changes and never edit live configuration.
