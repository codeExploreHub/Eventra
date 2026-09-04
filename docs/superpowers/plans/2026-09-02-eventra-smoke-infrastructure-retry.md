# Eventra Smoke Infrastructure Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one auditable, member-authorized retry of an infrastructure-blocked post-merge Smoke Stage, then use it to reconcile PRO-116 without weakening exact-SHA provenance or creating a deployment path.

**Architecture:** Extend the existing parent state machine with one new `retry_smoke_stage` action. The parent loader reads an immutable member authorization comment, the planner validates it against the exact blocked Smoke and merged candidate set, and the existing Smoke reservation executor handles initial and retry modes through the same idempotent reconciliation path. Keep Eventra pilot logic in the existing control module and leave the generic `multica-multi-repo-delivery` package untouched.

**Tech Stack:** Python 3 dataclasses and `unittest`, Multica CLI, GitHub CLI read models, Markdown operator contracts, Next.js repository checks.

**Spec:** `docs/superpowers/specs/2026-09-02-eventra-smoke-infrastructure-retry-design.md`

## Global Constraints

- Change only the Eventra pilot under `/Users/didi/Eventra-workspace/Eventra`; do not modify `/Users/didi/Eventra-workspace/multica-multi-repo-delivery`.
- Preserve PRO-120 and its evidence comment as immutable historical evidence.
- Require a fresh PR-ref fetch and exact `FETCH_HEAD` verification in every retry Smoke; do not introduce degraded provenance.
- Permit exactly one retry, authorized by a canonical root comment whose authoritative `author_type` is `member`.
- Bind authorization, reservation, child provenance, and action identity to the exact source Smoke, evidence UUID, candidate SHA map, merged PRs, and retry ordinal `1`.
- Fail closed on malformed metadata, stale or reused authorization, assignment drift, candidate/PR drift, duplicate children, conflicting history, or status drift.
- Keep Knowledge Loop metadata independent and do not create deployment or production mutations.
- Preserve the user's untracked `docs/multica/eventra-multica-automation-overview.md` file unless the user separately asks to include it.
- Use `apply_patch` for source and documentation edits, strict red-green-refactor cycles, and a focused commit after each task.

## File map

- Modify `tools/multica/workflow.py`: authorization model and loading, retry planner transition, retry-aware Smoke assignment parser, generalized reservation/executor, recovery identity, and CLI result behavior.
- Modify `tools/multica/tests/test_workflow.py`: reusable retry fixtures plus planner, loader, assignment, executor, replay, interruption, completion, and Watcher rejection coverage.
- Modify `tools/multica/instructions/delivery_lead.md`: exact human authorization and Delivery Lead execution contract.
- Modify `tools/multica/instructions/integration_qa.md`: retry provenance and unchanged fresh-fetch contract.
- Modify `tools/multica/README.md`: operator recovery runbook for an infrastructure-blocked Smoke.
- Modify `docs/multica/pilot-issues.md`: Eventra pilot acceptance scenario and evidence requirements.
- Modify `tools/multica/tests/test_operator_docs.py`: executable assertions that documentation exposes only the supported recovery path.
- Do not modify `docs/multica/eventra-multica-automation-overview.md` in this implementation.

---

### Task 1: Load and validate the member authorization

**Files:**
- Modify: `tools/multica/workflow.py:65-255`
- Modify: `tools/multica/workflow.py:2452-2520`
- Modify: `tools/multica/workflow.py:2961-3238`
- Test: `tools/multica/tests/test_workflow.py:2427-2615`
- Test: `tools/multica/tests/test_workflow.py:9982-10480`

**Interfaces:**
- Consumes: existing `AuthorizingComment`, `_canonical_json`, `_parent_metadata`, `parse_authorizing_comment`, and `load_parent_snapshot`.
- Produces: constants `SMOKE_RETRY_AUTHORIZATION_KEY` and `SMOKE_RETRY_AUTHORIZATION_CONSUMED_KEY`; `ParentSnapshot.smoke_retry_authorization_comment_uuid`, `.consumed_smoke_retry_authorization_uuid`, and `.smoke_retry_authorizing_comment`; helper `_validated_smoke_retry_authorization(snapshot, source_smoke) -> str | None`.

- [ ] **Step 1: Add failing metadata and loader tests**

Add tests using these exact fixture values:

```python
SMOKE_RETRY_AUTH_UUID = "00000000-0000-4000-8000-000000000071"
SMOKE_EVIDENCE_UUID = "00000000-0000-4000-8000-000000000072"

def smoke_retry_authorization_content(
    *, source_smoke="PRO-68", evidence_uuid=SMOKE_EVIDENCE_UUID,
    backend_sha="b" * 40,
):
    return json.dumps(
        {
            "candidate_shas": {"backend": backend_sha},
            "granted_smoke_retry": 1,
            "source_evidence_comment_uuid": evidence_uuid,
            "source_smoke": source_smoke,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
```

Require both new UUID metadata fields to parse, reject non-UUID values, load the referenced root comment, and reread it for stability. Assert the snapshot contains an `AuthorizingComment` with the exact UUID, `author_type="member"`, and byte-identical content. Add negative cases for a foreign thread, deleted comment on the stability reread, non-member author, noncanonical whitespace, extra keys, retry ordinal `2`, stale source ID/evidence/SHA, and a consumed UUID equal to the authorization UUID.

- [ ] **Step 2: Run the focused tests and observe RED**

```bash
python3 -B -m unittest \
  tools.multica.tests.test_workflow.ParentSnapshotReadTests
```

Expected: FAIL because the new constants and `ParentSnapshot` fields do not exist and the loader does not read the Smoke retry comment.

- [ ] **Step 3: Add the minimal immutable authorization model and loader**

Add these constants and fields:

```python
SMOKE_RETRY_AUTHORIZATION_KEY = (
    "eventra.workflow.smoke_retry_authorization_comment"
)
SMOKE_RETRY_AUTHORIZATION_CONSUMED_KEY = (
    "eventra.workflow.smoke_retry_authorization_consumed"
)

@dataclass(frozen=True)
class ParentSnapshot:
    smoke_retry_authorization_comment_uuid: str = ""
    consumed_smoke_retry_authorization_uuid: str = ""
    smoke_retry_authorizing_comment: AuthorizingComment | None = None
```

Place the fields after the existing Repair authorization fields while respecting dataclass default ordering. Parse both metadata values as either empty strings or UUIDs. In `load_parent_snapshot`, read the referenced parent thread with the same exact `issue comment list ... --thread ... --full --compact --output json` pattern used by Repair authorization. When either an authorization or a Smoke reservation is present, reread the comment before returning and raise `RuntimeError("smoke retry authorization changed during recovery read")` if it changed.

Implement byte-for-byte canonical validation:

```python
def _validated_smoke_retry_authorization(
    snapshot: ParentSnapshot,
    source_smoke: PhaseSnapshot,
) -> str | None:
    comment = snapshot.smoke_retry_authorizing_comment
    expected = {
        "candidate_shas": _candidate_sha_map(snapshot),
        "granted_smoke_retry": 1,
        "source_evidence_comment_uuid": source_smoke.evidence_comment,
        "source_smoke": source_smoke.issue_key,
    }
    if (
        comment is None
        or comment.comment_uuid
        != snapshot.smoke_retry_authorization_comment_uuid
        or comment.author_type != "member"
        or comment.content != _canonical_json(expected)
        or snapshot.consumed_smoke_retry_authorization_uuid
    ):
        return None
    return comment.comment_uuid
```

- [ ] **Step 4: Run the focused tests and observe GREEN**

Run the command from Step 2. Expected: PASS, including comment-stability and malformed-metadata cases.

- [ ] **Step 5: Commit the authorization read model**

```bash
git add tools/multica/workflow.py tools/multica/tests/test_workflow.py
git commit -m "feat(multica): load smoke retry authorization"
```

### Task 2: Add the fail-closed `retry_smoke_stage` planner transition

**Files:**
- Modify: `tools/multica/workflow.py:232-246`
- Modify: `tools/multica/workflow.py:529-705`
- Modify: `tools/multica/workflow.py:1390-1570`
- Test: `tools/multica/tests/test_workflow.py:2671-4795`

**Interfaces:**
- Consumes: `_validated_smoke_retry_authorization`, `_action_key`, `_parent_decision`, `_smoke_assignment_problem`, `_attempt_history_is_consistent`, and the current Stage selection in `decide_parent_action`.
- Produces: `ParentDecision.kind == "retry_smoke_stage"` and deterministic action keys with `source_stage` and `authorizing_comment_uuid`.

- [ ] **Step 1: Add the exact eligible planner fixture and failing test**

Build a backend-only parent with a canonical PASS Gate at Stage 2 and a single `done + blocked` Smoke at Stage 3. The Smoke has no responsible repository, has `SMOKE_EVIDENCE_UUID`, the parent is `blocked`, `merge_state="merged"`, `next_stage=4`, and its authorization comment matches the source. Assert:

```python
decision = decide_parent_action(authorized_retry_snapshot())
self.assertEqual(decision.kind, "retry_smoke_stage", decision.reason)
self.assertEqual(
    decision.action_key,
    (
        "2:PRO-65:retry_smoke_stage:0:backend:-:" + "b" * 40
        + ":next-stage:4:source-stage:3:authorization:"
        + SMOKE_RETRY_AUTH_UUID
    ),
)
```

Keep the original initial Smoke creation action on the Stage 3 child; the retry action does not replace historical provenance.

- [ ] **Step 2: Add table-driven rejection tests**

For every case, assert `block_parent`, `action_key is None`, and no mutation:

```python
cases = {
    "missing authorization": {"smoke_retry_authorizing_comment": None},
    "parent not blocked": {"parent_status": "in_review"},
    "source smoke failed": {"source_result": "fail"},
    "responsible repository": {"source_owners": ("backend",)},
    "source still active": {"source_status": "in_progress"},
    "candidate drift": {"candidate_backend_sha": "c" * 40},
    "pull request drift": {"pull_request_head": "c" * 40},
    "assignment drift": {"source_assignee_id": REVIEWER_ID},
    "authorization consumed": {
        "consumed_smoke_retry_authorization_uuid": SMOKE_RETRY_AUTH_UUID
    },
    "retry child already exists": {"include_stage_four_smoke": True},
}
```

Also assert an ordinary blocked parent with no exact eligible history remains blocked and existing initial Smoke planning stays unchanged.

- [ ] **Step 3: Run the planner tests and observe RED**

```bash
python3 -B -m unittest \
  tools.multica.tests.test_workflow.ParentDecisionTests
```

Expected: FAIL because `retry_smoke_stage` is absent and a blocked Smoke routes through `_repair_or_block`.

- [ ] **Step 4: Implement the minimal transition**

Add `"retry_smoke_stage"` to `ParentDecision.kind`. In the merged Smoke branch, preserve PASS completion and gate retry as follows:

```python
source_smoke = latest[0] if len(latest) == 1 else None
authorization_uuid = (
    None
    if source_smoke is None
    else _validated_smoke_retry_authorization(snapshot, source_smoke)
)
if (
    snapshot.parent_status == "blocked"
    and source_smoke is not None
    and source_smoke.status == "done"
    and source_smoke.result == "blocked"
    and not source_smoke.responsible_repositories
    and source_smoke.evidence_comment
    and authorization_uuid is not None
    and not any(item.stage > source_smoke.stage for item in snapshot.children)
):
    return _parent_decision(
        snapshot,
        "retry_smoke_stage",
        "member authorized one infrastructure-blocked smoke retry",
        source_stage=source_smoke.stage,
        authorizing_comment_uuid=authorization_uuid,
    )
return _repair_or_block(snapshot)
```

Extend `_parent_decision` only enough to pass the two optional action-key components through to `_action_key`; never emit a retry action without both.

- [ ] **Step 5: Run focused planner coverage and observe GREEN**

Run the command from Step 3. Expected: PASS for the retry cases and all existing state-machine transitions.

- [ ] **Step 6: Commit the planner transition**

```bash
git add tools/multica/workflow.py tools/multica/tests/test_workflow.py
git commit -m "feat(multica): plan authorized smoke retry"
```

### Task 3: Validate initial and retry Smoke assignment provenance

**Files:**
- Modify: `tools/multica/workflow.py:529-580`
- Modify: `tools/multica/workflow.py:1645-1695`
- Modify: `tools/multica/workflow.py:2024-2088`
- Test: `tools/multica/tests/test_workflow.py:2150-2285`
- Test: `tools/multica/tests/test_workflow.py:3960-4040`
- Test: `tools/multica/tests/test_workflow.py:4796-4915`
- Test: `tools/multica/tests/test_workflow.py:5000-6165`

**Interfaces:**
- Consumes: canonical `_action_key`, historical Gate validation, `finish_phase`, `finish_parent`, and Watcher recovery identity.
- Produces: `_parse_smoke_creation_action(value: str) -> dict[str, object] | None`; retry-aware `_smoke_assignment_problem`; recovery identity containing the two authorization UUIDs.

- [ ] **Step 1: Add failing assignment and terminal-state tests**

Create a Stage 4 retry Smoke whose `creation_action` is the exact planner key from Task 2. Assert canonical metadata is accepted by `finish_phase`, a PASS result lets `finish_parent` move the parent to `done`, and BLOCKED/FAIL results keep the parent blocked with no second retry.

Add one test per forged component: wrong action kind, wrong next Stage, wrong source Stage, wrong authorization UUID, altered Stage 3 source evidence, missing original Stage 2 PASS Gate, altered candidate SHA, altered merged PR head, extra retry Smoke, and non-member authorization. Each must return the existing provenance conflict or `block_parent` without mutation.

- [ ] **Step 2: Run assignment, completion, and Watcher tests and observe RED**

```bash
python3 -B -m unittest \
  tools.multica.tests.test_workflow.PhaseCompletionTests \
  tools.multica.tests.test_workflow.ParentCompletionTests \
  tools.multica.tests.test_workflow.RecoveryDecisionTests \
  tools.multica.tests.test_workflow.RecoveryMutationTests \
  tools.multica.tests.test_workflow.WatchWorkflowTests
```

Expected: FAIL because `_smoke_assignment_problem` reconstructs only an initial action.

- [ ] **Step 3: Add an exact action-shape parser**

```python
def _parse_smoke_creation_action(value: str) -> dict[str, object] | None:
    tokens = value.split(":")
    if (
        len(tokens) == 9
        and tokens[2] == "create_smoke_stage"
        and tokens[7] == "next-stage"
        and tokens[8].isdigit()
    ):
        return {
            "kind": "create_smoke_stage",
            "next_stage": int(tokens[8]),
            "source_stage": None,
            "authorization_uuid": "",
        }
    if (
        len(tokens) == 13
        and tokens[2] == "retry_smoke_stage"
        and tokens[7] == "next-stage"
        and tokens[8].isdigit()
        and tokens[9] == "source-stage"
        and tokens[10].isdigit()
        and tokens[11] == "authorization"
        and _is_uuid(tokens[12])
    ):
        return {
            "kind": "retry_smoke_stage",
            "next_stage": int(tokens[8]),
            "source_stage": int(tokens[10]),
            "authorization_uuid": tokens[12],
        }
    return None
```

Reconstruct the entire action with `_action_key` after parsing; token shape alone is never sufficient authority.

- [ ] **Step 4: Generalize assignment validation without weakening initial Smoke**

For `create_smoke_stage`, reconstruct the existing exact action and keep every current check. For `retry_smoke_stage`, reconstruct the exact action with source Stage and authorization UUID; require the current Smoke at `source_stage + 1`, the exact prior `done + blocked` Smoke at `source_stage`, the earlier exact-SHA PASS Gate, the same candidate map and merged PR set, and the consumed authorization UUID equal to the parsed authorization.

Add both new UUID fields and the source Smoke immutable identity to `_recovery_authority_identity` so Watcher replay stops if retry authority changes. Do not let Watcher synthesize or consume authorization.

- [ ] **Step 5: Run focused tests and observe GREEN**

Run the command from Step 2. Expected: PASS with canonical retry accepted and every forgery rejected.

- [ ] **Step 6: Commit retry assignment validation**

```bash
git add tools/multica/workflow.py tools/multica/tests/test_workflow.py
git commit -m "feat(multica): validate smoke retry provenance"
```

### Task 4: Generalize the reservation and executor with replay-safe parent unblocking

**Files:**
- Modify: `tools/multica/workflow.py:4680-5255`
- Modify: `tools/multica/tests/test_workflow.py:6961-7745`

**Interfaces:**
- Consumes: `retry_smoke_stage`, `_validated_smoke_retry_authorization`, observed metadata helpers, stable authority readers, and the existing `execute-parent-smoke` CLI.
- Produces: retry-capable reservation encode/decode, child description, source authority reader, resume path, and executor.

- [ ] **Step 1: Extend the fake runner before executor assertions**

Teach `FakeRepairRunner.run()` to handle parent status separately:

```python
if call[:2] == ("issue", "status"):
    identifier = call[2]
    status = call[3]
    if identifier in {"PRO-65", PARENT_ID}:
        if self.parent["status"] != status:
            self.parent["status"] = status
            self.committed_mutations += 1
        self._maybe_lose_ack("parent-status")
        return copy.deepcopy(self.parent)
```

Keep the existing child branch after this guard. Add fault hooks for lost acknowledgement after parent status, consumed authorization metadata, next Stage, last action, child promotion, and reservation deletion.

- [ ] **Step 2: Add failing retry executor tests**

Add `_retry_planned()` that produces the Task 2 state through the real loader and planner. A successful call must create exactly one Stage 4 child, write its canonical metadata prefix, move the parent `blocked -> in_progress` with `--no-start`, promote exactly one Integration QA run, set `next_stage="5"`, set `last_action`, consume the authorization UUID, and clear the reservation last.

Require the exact child description:

```json
{"action":"2:PRO-65:retry_smoke_stage:0:backend:-:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:next-stage:4:source-stage:3:authorization:00000000-0000-4000-8000-000000000071","candidate_shas":{"backend":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"parent":"PRO-65","source_evidence_comment_uuid":"00000000-0000-4000-8000-000000000072","source_smoke":"PRO-68"}
```

- [ ] **Step 3: Add interruption, replay, and conflict tests**

For each boundary below, inject one lost acknowledgement, call the executor twice with the identical key, and assert one child, one active run, consumed authorization, and zero extra mutation on final replay:

```python
lost_acks = (
    "set:PRO-65:eventra.workflow.smoke_reservation",
    "create",
    "set-child:eventra.phase.creation_action",
    "parent-status",
    "status",
    "set:PRO-65:eventra.workflow.next_stage",
    "set:PRO-65:eventra.workflow.last_action",
    "set:PRO-65:eventra.workflow.smoke_retry_authorization_consumed",
    "delete:PRO-65:eventra.workflow.smoke_reservation",
)
```

Add hard interruptions after create and every child metadata prefix. Add fail-closed cases for parent status `done`/`cancelled`, changed authorization comment, a different consumed UUID, altered source Smoke/evidence, changed PR head/state, changed assignment, conflicting reservation, duplicate/future child, and two active runs.

- [ ] **Step 4: Run executor tests and observe RED**

```bash
python3 -B -m unittest \
  tools.multica.tests.test_workflow.SmokeExecutionTests
```

Expected: FAIL because the reservation accepts only initial Smoke and cannot reconcile parent status.

- [ ] **Step 5: Implement a mode-bound reservation schema**

Initial Smoke retains its current data and gains `"mode":"initial"` plus `"previous_parent_status"`. Retry persists these exact additional fields:

```python
{
    "mode": "retry",
    "source_smoke_stage": source_smoke.stage,
    "source_smoke_identifier": source_smoke.issue_key,
    "source_smoke_result": "blocked",
    "source_evidence_comment_uuid": source_smoke.evidence_comment,
    "source_gate_stage": source_smoke.stage - 1,
    "authorization_comment_uuid": authorization_uuid,
    "previous_consumed_authorization_uuid": "",
    "previous_parent_status": "blocked",
}
```

Make `_decode_smoke_reservation` require the exact key set per mode, size at most `MAX_SMOKE_RESERVATION_BYTES`, canonical action reconstruction, exact candidate/PR maps, and coherent Stage relationships. Reject unknown keys and mixed schemas.

- [ ] **Step 6: Generalize source-authority rereads**

For initial mode, keep the source Gate at `smoke_stage - 1`. For retry mode, load both the reserved source Smoke and `source_gate_stage`; validate their immutable evidence and exact-SHA assignment, allow parent status only in `{blocked, in_progress}` during reconciliation, and require the parent authorization pointer to remain exact. Include parent status, authorization comment, consumed value, source Smoke metadata/evidence, Gate evidence, PRs, assignments, child metadata prefix, and runs in the stable authority identity.

- [ ] **Step 7: Reconcile writes in the specified order**

Add a read-before/write/read-after helper:

```python
if reservation["mode"] == "retry":
    effects[0] += _parent_status_set_observed(
        runner,
        parent_key,
        expected="blocked",
        desired="in_progress",
        no_start=True,
    )
```

After status, promote the initialized child and require one active run. Then write `next_stage`, `last_action`, and consumed authorization. Delete `SMOKE_RESERVATION_KEY` last. A replay observing `in_progress` is accepted only while the exact reservation and prior authority remain. Retry description includes the source identifier and evidence UUID, never the old evidence body.

- [ ] **Step 8: Accept both decision kinds in the existing executor**

```python
if (
    decision.kind not in {"create_smoke_stage", "retry_smoke_stage"}
    or decision.action_key != expected_action_key
):
    raise RuntimeError("fresh parent plan does not authorize smoke action")
```

Keep `execute-parent-smoke --expected-action-key` as the sole CLI; add no manual retry-child command.

- [ ] **Step 9: Run executor tests and observe GREEN**

Run Step 4's command. Expected: PASS for initial regression, retry creation, all acknowledgement-loss replays, and all conflicts.

- [ ] **Step 10: Commit the generalized executor**

```bash
git add tools/multica/workflow.py tools/multica/tests/test_workflow.py
git commit -m "feat(multica): execute smoke retry idempotently"
```

### Task 5: Lock the agent and operator contracts into executable documentation

**Files:**
- Modify: `tools/multica/instructions/delivery_lead.md`
- Modify: `tools/multica/instructions/integration_qa.md`
- Modify: `tools/multica/README.md`
- Modify: `docs/multica/pilot-issues.md`
- Modify: `tools/multica/tests/test_operator_docs.py`

**Interfaces:**
- Consumes: exact metadata keys, canonical authorization JSON, `retry_smoke_stage`, and `execute-parent-smoke`.
- Produces: one supported human/Lead/Integration QA recovery runbook guarded by documentation tests.

- [ ] **Step 1: Add failing documentation assertions**

Require the docs to mention `retry_smoke_stage`, both retry metadata keys, and `execute-parent-smoke`. Assert:

```python
self.assertIn("fresh fetch", integration_qa.lower())
self.assertIn("FETCH_HEAD", integration_qa)
self.assertNotIn("manual smoke child", delivery_lead.lower())
self.assertNotIn("degraded provenance", delivery_lead.lower())
self.assertNotIn("deploy", retry_runbook_command_block.lower())
```

Also assert the untracked overview document is neither required nor referenced by the new contract.

- [ ] **Step 2: Run documentation tests and observe RED**

```bash
python3 -B -m unittest tools.multica.tests.test_operator_docs
```

Expected: FAIL because the retry protocol is undocumented.

- [ ] **Step 3: Update the Delivery Lead instructions**

Document canonical member authorization, storing only the returned UUID, rerunning `plan-parent`, requiring exactly `retry_smoke_stage`, assigning its machine-produced action key to `SMOKE_RETRY_ACTION_KEY`, and invoking:

```bash
python3 tools/multica/workflow.py execute-parent-smoke PRO-116 \
  --expected-action-key "$SMOKE_RETRY_ACTION_KEY"
```

State that the action key must be copied byte-for-byte from `plan-parent` output and no retry child may be manually created.

- [ ] **Step 4: Update Integration QA and pilot runbooks**

Describe retry child kind `smoke`, attempt `0`, target `suite:smoke`, role `integration_qa`, unchanged candidates, and source-bound action. Reaffirm fresh PR-ref fetch, exact `FETCH_HEAD`, clean detached worktree, repository-standard Smoke, owned cleanup, Context Receipt, `finish-phase`, no deployment, and independent Knowledge candidate evaluation.

Include this terminal sequence:

```text
PRO-120 done+blocked -> member authorization -> retry_smoke_stage
-> one Stage 4 Smoke -> done+pass -> complete_parent -> PRO-116 done
```

- [ ] **Step 5: Run documentation tests and observe GREEN**

Run Step 2's command. Expected: PASS.

- [ ] **Step 6: Commit the operator contract**

```bash
git add \
  tools/multica/instructions/delivery_lead.md \
  tools/multica/instructions/integration_qa.md \
  tools/multica/README.md \
  docs/multica/pilot-issues.md \
  tools/multica/tests/test_operator_docs.py
git commit -m "docs(multica): document smoke retry protocol"
```

### Task 6: Run the complete local safety gate and review the diff

**Files:**
- Verify: all files committed in Tasks 1-5
- Preserve: `docs/multica/eventra-multica-automation-overview.md`

**Interfaces:**
- Consumes: all implementation and documentation changes.
- Produces: evidence that the pilot is ready for live PRO-116 reconciliation.

- [ ] **Step 1: Run the full Multica unit suite**

```bash
python3 -B -m unittest discover \
  -s tools/multica/tests \
  -p 'test_*.py'
```

Expected: all tests PASS with zero errors and failures.

- [ ] **Step 2: Run repository-standard checks**

```bash
npm run test:local-contract
npm run lint
npm run build
```

Expected: each exits `0`. Record any pre-existing failure with exact output; do not relabel it as passing.

- [ ] **Step 3: Inspect the diff and workspace ownership**

```bash
git diff fc7a3d9a4..HEAD --check
git diff fc7a3d9a4..HEAD --stat
git status --short
```

Expected: no `diff --check` output; only planned tracked files changed; the overview remains untracked and unchanged.

- [ ] **Step 4: Perform the review checkpoint**

Reject the implementation if any path permits a second retry, non-member authorization, changed candidate/PR head, unexpected parent status, duplicate child/run, reservation deletion before durable consumption, degraded provenance, deployment, or generic-package edits.

- [ ] **Step 5: Commit review fixes only when needed**

```bash
git add tools/multica docs/multica/pilot-issues.md
git commit -m "fix(multica): harden smoke retry reconciliation"
```

Skip this commit when review yields no changes.

### Task 7: Reconcile live PRO-116 through the new protocol

**Files:**
- Read only: local Eventra and Eventra Backend repository state
- External mutations: one member comment and parent metadata on PRO-116, one planned Stage 4 Smoke, ordinary workflow status/evidence updates
- Forbidden: repository source edits, PR changes, deployment, production mutation

**Interfaces:**
- Consumes: tested `plan-parent`, `execute-parent-smoke`, Integration QA automation, and live IDs from the spec.
- Produces: PRO-116 `done` after a verified Stage 4 PASS, or a stable fail-closed blocked state.

- [ ] **Step 1: Revalidate live authority without mutation**

Require:

```text
PRO-116: blocked, workflow v2, next_stage 4, merge_state merged
PRO-120: Stage 3, smoke, attempt 0, done+blocked
PRO-120 evidence: 01a0622e-72e3-7660-9fcd-a806c07a5c0f
backend candidate: c7b9a38a2d05ba05eec6b16c83184653aefba750
PR #6: merged with the same head SHA
knowledge.version: 1
knowledge.status: none
port 8080: no leftover Eventra service
```

Also reread Squad/Project assignments, comments, runs, and owned processes. Stop without mutation if any value differs.

- [ ] **Step 2: Post exact member authorization and persist its UUID**

```bash
SMOKE_RETRY_BODY='{"candidate_shas":{"backend":"c7b9a38a2d05ba05eec6b16c83184653aefba750"},"granted_smoke_retry":1,"source_evidence_comment_uuid":"01a0622e-72e3-7660-9fcd-a806c07a5c0f","source_smoke":"PRO-120"}'
SMOKE_RETRY_COMMENT_JSON="$(multica issue comment add PRO-116 --content "$SMOKE_RETRY_BODY" --output json)"
SMOKE_RETRY_COMMENT_UUID="$(printf '%s' "$SMOKE_RETRY_COMMENT_JSON" | jq -er '.id')"
multica issue metadata set PRO-116 \
  --key eventra.workflow.smoke_retry_authorization_comment \
  --value "$SMOKE_RETRY_COMMENT_UUID" \
  --output json
```

Reread the root comment and metadata. Require exact content, authoritative `author_type=member`, and pointer equality. If output uses `comment_uuid` rather than `id`, inspect it and stop; do not guess or post a second comment.

- [ ] **Step 3: Require the exact plan and execute once**

```bash
SMOKE_RETRY_PLAN_JSON="$(python3 tools/multica/workflow.py plan-parent PRO-116)"
SMOKE_RETRY_ACTION_KEY="$(printf '%s' "$SMOKE_RETRY_PLAN_JSON" | jq -er 'select(.decision == "retry_smoke_stage") | .action_key')"
```

Require `decision=retry_smoke_stage` and a nonempty action key. Then invoke:

```bash
python3 tools/multica/workflow.py execute-parent-smoke PRO-116 \
  --expected-action-key "$SMOKE_RETRY_ACTION_KEY"
```

Never construct or edit the action key manually. Replay only the identical key when an interrupted reservation requires reconciliation.

- [ ] **Step 4: Observe Integration QA to terminal state**

Require exactly one Stage 4 child with canonical kind, attempt, target, role, SHA, creation action, source description, project, assignee, and active run. Let Integration QA execute the unchanged fresh-fetch contract and finish through `finish-phase`.

Successful evidence must include fresh PR-ref fetch resolving to `c7b9a38a2d05ba05eec6b16c83184653aefba750`, exact `FETCH_HEAD`, clean detached worktree, health/OpenAPI PASS, cleanup, Context Receipt, and a new immutable evidence comment.

- [ ] **Step 5: Finalize through the planner**

After Stage 4 is `done + pass`:

```bash
python3 tools/multica/workflow.py plan-parent PRO-116
python3 tools/multica/workflow.py finish-parent PRO-116
```

Require the first command to return `complete_parent`; never call `finish-parent` for another decision.

- [ ] **Step 6: Verify closed-loop invariants**

Require PRO-116 `done`; PRO-120 unchanged `done + blocked`; Stage 4 `done + pass`; authorization consumed exactly once; no Smoke reservation; `next_stage=5`; Knowledge metadata coherent (`version=1`, `status=none` unless the normal candidate protocol independently produces a valid candidate); PR #6 unchanged and merged; no Eventra service remains; and no deployment occurred.

If Stage 4 is BLOCKED or FAIL, require `block_parent`, leave PRO-116 blocked, preserve both evidence records, and verify no second `retry_smoke_stage` can be planned.

---

## Completion criteria

- The full Multica suite, local contract, lint, and build pass.
- Initial Smoke behavior remains backward compatible.
- One valid member authorization creates exactly one retry Smoke.
- Every tested interruption boundary converges without duplicate children or runs.
- Retry PASS completes the parent; retry BLOCKED/FAIL permanently blocks it.
- PRO-120 stays immutable, exact-SHA fresh-fetch remains mandatory, Knowledge Loop stays independent, and no deployment path is introduced.
- The generic package and the user's untracked overview document remain untouched.
