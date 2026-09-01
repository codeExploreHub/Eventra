# Multica Gate Fan-In Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce complete gate fan-in before repair dispatch, carry every terminal failure into one immutable repair bundle, and make the same version 2 contract authoritative in the reusable package and Eventra.

**Architecture:** Implement the version 2 domain and workflow first in the reusable `multica-multi-repo-delivery` package, then adapt Eventra's compatibility CLI and provisioned role contracts to that authority. A terminal gate Stage produces one canonical `FailureBundle`; repair children receive repository-specific partitions of that bundle, and no individual gate or historical child can mutate candidate SHAs. The live Multica update remains a separate exact-plan authorization after local verification.

**Tech Stack:** Python 3.11+, frozen dataclasses, canonical JSON, SHA-256 action identities, `unittest`, Multica 0.4.x CLI contracts, GitHub exact-SHA evidence.

**Spec:** `docs/superpowers/specs/2026-08-30-multica-gate-fan-in-design.md`

## Global Constraints

- Reusable package root: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery`.
- Eventra control/compatibility root: `/Users/didi/Eventra-workspace/Eventra`.
- Authoritative backend product root: `/Users/didi/Eventra-workspace/Eventra-Backend`; do not edit it for this workflow change.
- New mutable workflow state uses workflow version `2` and metadata version `2`.
- Completed version 1 state remains readable; nonterminal version 1 state does not auto-progress.
- Automatic repair authority remains exactly two complete rounds.
- Every human-authorized extra round is member-authored, bound to one failure-bundle digest, grants only the next round, and is single-use.
- Reviewer and QA roles never implement or directly dispatch repairs.
- Production deployment remains forbidden; automatic merge remains development/local only after all current exact-SHA gates pass.
- Do not query or import skills from the company-internal SkillsHub.
- Do not perform live Multica apply, GitHub push, tag, release, merge, or deployment without a fresh explicit authorization for that action.
- Preserve unrelated user changes and never reset, clean, stash, or overwrite product worktrees.

---

### Task 1: Version 2 parent metadata and one-shot repair authorization

**Files:**
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/core/metadata.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/core/test_metadata.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/core/provision.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/core/test_provision.py`

**Interfaces:**
- Produces: `LegacyParentMetadataV1`, `RepairAuthorization`, and version 2 `ParentMetadata`.
- Produces: `decode_parent_metadata(text) -> LegacyParentMetadataV1 | ParentMetadata`.
- Produces: `ParentMetadata.repair_round`, `automatic_repairs_used`, and optional `repair_authorization`.
- Consumes later: workflow state validation and repair dispatch in Tasks 3–4.

- [ ] **Step 1: Write RED metadata tests**

Add focused tests proving the exact version boundary and one-shot envelope:

```python
def test_version_two_metadata_separates_round_from_automatic_budget(self):
    authorization = RepairAuthorization(
        comment_uuid="00000000-0000-4000-8000-000000000011",
        comment_url="https://multica.example/comments/00000000-0000-4000-8000-000000000011",
        bundle_digest="a" * 64,
        granted_round=3,
    )
    metadata = ParentMetadata(
        workflow_version=2,
        metadata_version=2,
        instance_key="demo",
        affected_repositories=("api",),
        repository_dag={"api": ()},
        repair_round=2,
        automatic_repairs_used=2,
        repair_authorization=authorization,
    )
    observed = decode_parent_metadata(encode_parent_metadata(metadata))
    self.assertEqual(observed, metadata)

def test_human_authorization_must_grant_exactly_the_next_round(self):
    with self.assertRaisesRegex(MetadataError, "next repair round"):
        ParentMetadata(
            workflow_version=2,
            metadata_version=2,
            instance_key="demo",
            affected_repositories=("api",),
            repository_dag={"api": ()},
            repair_round=2,
            automatic_repairs_used=2,
            repair_authorization=RepairAuthorization(
                "00000000-0000-4000-8000-000000000012",
                "https://multica.example/comments/00000000-0000-4000-8000-000000000012",
                "b" * 64,
                4,
            ),
        )

def test_completed_version_one_metadata_remains_decodable(self):
    legacy = decode_parent_metadata(
        '{"affected_repositories":[],"attempt":2,"candidate_shas":{},'
        '"contract_hashes":{},"instance_key":"demo","last_action":"dispatch",'
        '"merge_plan":[],"merge_state":"blocked","metadata_version":1,'
        '"repository_dag":{},"stage_ordinal":6,"workflow_version":1}'
    )
    self.assertIsInstance(legacy, LegacyParentMetadataV1)
    self.assertEqual(legacy.attempt, 2)
```

Also add table-driven rejection for a noncanonical UUID, non-HTTPS URL,
non-64-hex digest, negative round, automatic count above two, authorization at
rounds one or two, and unknown version 2 fields.

- [ ] **Step 2: Run the metadata tests and verify RED**

Run:

```bash
python3 -B -m unittest tests.core.test_metadata -v
```

Expected: FAIL because version 2 metadata and `RepairAuthorization` do not yet
exist.

- [ ] **Step 3: Implement the minimal version 2 metadata types**

Add these public shapes and exact validation:

```python
@dataclass(frozen=True)
class RepairAuthorization:
    comment_uuid: str
    comment_url: str
    bundle_digest: str
    granted_round: int


@dataclass(frozen=True)
class LegacyParentMetadataV1:
    workflow_version: int
    metadata_version: int
    instance_key: str
    affected_repositories: tuple[str, ...]
    repository_dag: Mapping[str, tuple[str, ...]]
    candidate_shas: Mapping[str, str]
    contract_hashes: Mapping[str, str]
    stage_ordinal: int
    merge_plan: tuple[str, ...]
    merge_state: str
    attempt: int
    last_action: str


@dataclass(frozen=True)
class ParentMetadata:
    workflow_version: int = 2
    metadata_version: int = 2
    instance_key: str = "default"
    affected_repositories: tuple[str, ...] = ()
    repository_dag: Mapping[str, tuple[str, ...]] | None = None
    candidate_shas: Mapping[str, str] = field(default_factory=dict)
    contract_hashes: Mapping[str, str] = field(default_factory=dict)
    stage_ordinal: int = 0
    merge_plan: tuple[str, ...] = ()
    merge_state: str = "pending"
    repair_round: int = 0
    automatic_repairs_used: int = 0
    repair_authorization: RepairAuthorization | None = None
    last_action: str = "dispatch"
```

`ParentMetadata.__post_init__` must require version and metadata version `2`,
`automatic_repairs_used <= 2`, `automatic_repairs_used <= repair_round`, and an
authorization only when `automatic_repairs_used == 2` and
`granted_round == repair_round + 1`. Keep legacy decoding closed to the exact
old field set and never encode a new mutable version 1 record.

Keep `ParentSnapshot.attempt`, `PhaseCompletion.attempt`, and
`WorkflowChild.attempt` as the runtime name for the current repair round; the
state boundary maps version 2 `ParentMetadata.repair_round` to those runtime
fields. Set `GenericWorkflow`'s default and supported mutable workflow version
to `2`. A read containing `LegacyParentMetadataV1` may return historical status
only; `resume_parent()` and `record_phase_completion()` return a zero-mutation
version-migration block for a nonterminal version 1 parent.

- [ ] **Step 4: Bind provisioned framework metadata to version 2**

Change:

```python
WORKFLOW_METADATA_VERSION = 2
```

Update provision tests so an initialized version 1 lock reports explicit
migration required, while a version 2 lock converges normally. Do not yet bump
the package version; Task 8 owns release identity.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
python3 -B -m unittest tests.core.test_metadata tests.core.test_provision -v
```

Expected: PASS with no network or external mutation.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/multica_delivery/core/metadata.py src/multica_delivery/core/provision.py tests/core/test_metadata.py tests/core/test_provision.py
git commit -m "feat: add version two workflow metadata"
```

---

### Task 2: Immutable failure evidence and bundle domain models

**Files:**
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/core/workflow.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/core/test_workflow.py`

**Interfaces:**
- Consumes: version 2 metadata from Task 1.
- Produces: `FailureEvidenceRef.to_canonical_dict()`, `FailureBundle.build(parent_identifier, workflow_version, source_stage_ordinal, repair_round, candidate_shas, failures)`, and `FailureBundle.for_repository(key)`.
- Produces: `PhaseCompletion.responsible_repositories` and evidence fields on `WorkflowChild`.
- Produces: repair-only bundle fields on `ChildRequest` for Task 3.

- [ ] **Step 1: Write RED model and validation tests**

Add tests with real frozen values:

```python
def failure_ref(child, repository, *, phase, comment_digit):
    comment_uuid = f"123e4567-e89b-42d3-a456-42661417400{comment_digit}"
    return FailureEvidenceRef(
        child_identifier=child,
        phase=phase,
        result="fail",
        stage_ordinal=7,
        repair_round=2,
        candidate_shas=SHA,
        responsible_repositories=(repository,),
        evidence_comment_uuid=comment_uuid,
        evidence_comment_url=f"https://multica.example/comments/{comment_uuid}",
    )

def test_failure_bundle_is_canonical_and_order_independent(self):
    review = failure_ref("PRO-201", "api", phase="review", comment_digit="1")
    qa = failure_ref("PRO-202", "api", phase="qa", comment_digit="2")
    first = FailureBundle.build("PRO-200", 2, 7, 3, SHA, (review, qa))
    second = FailureBundle.build("PRO-200", 2, 7, 3, SHA, (qa, review))
    self.assertEqual(first, second)
    self.assertRegex(first.digest, r"^[0-9a-f]{64}$")
    self.assertEqual(first.for_repository("api"), (review, qa))

def test_nonpassing_gate_requires_in_scope_responsible_repository(self):
    completion = completion_for(
        "api",
        phase="review",
        result="fail",
        responsible_repositories=(),
    )
    self.assertEqual(
        _phase_completion_schema_problem(completion, manifest=self.manifest),
        "non-PASS gate completion requires responsible repositories",
    )

def test_pass_gate_forbids_responsible_repositories(self):
    completion = completion_for(
        "api",
        phase="qa",
        result="pass",
        responsible_repositories=("api",),
    )
    self.assertEqual(
        _phase_completion_schema_problem(completion, manifest=self.manifest),
        "PASS gate completion cannot name responsible repositories",
    )
```

Add integration-suite tests showing `("api",)` and `("api", "web")` are
valid for a suite containing both, while `("worker",)` is rejected. Add exact
type, UUID, URL, phase, result, Stage, round, candidate-map, duplicate-owner,
and digest-tampering cases.

- [ ] **Step 2: Run the focused workflow model tests and verify RED**

Run:

```bash
python3 -B -m unittest \
  tests.core.test_workflow.WorkflowValueValidationTests \
  tests.core.test_workflow.WorkflowCompletionSchemaTests -v
```

Expected: FAIL because the new types and completion fields are absent.

- [ ] **Step 3: Implement exact frozen evidence models**

Add:

```python
@dataclass(frozen=True)
class FailureEvidenceRef:
    child_identifier: str
    phase: str
    result: str
    stage_ordinal: int
    repair_round: int
    candidate_shas: Mapping[str, str]
    responsible_repositories: tuple[str, ...]
    evidence_comment_uuid: str
    evidence_comment_url: str
    suite_key: str = ""

    def to_canonical_dict(self):
        return {
            "candidate_shas": dict(self.candidate_shas),
            "child_identifier": self.child_identifier,
            "evidence_comment_url": self.evidence_comment_url,
            "evidence_comment_uuid": self.evidence_comment_uuid,
            "phase": self.phase,
            "repair_round": self.repair_round,
            "responsible_repositories": list(self.responsible_repositories),
            "result": self.result,
            "stage_ordinal": self.stage_ordinal,
            "suite_key": self.suite_key,
        }


@dataclass(frozen=True)
class FailureBundle:
    parent_identifier: str
    workflow_version: int
    source_stage_ordinal: int
    repair_round: int
    candidate_shas: Mapping[str, str]
    failures: tuple[FailureEvidenceRef, ...]
    digest: str

    @classmethod
    def build(cls, parent_identifier, workflow_version, source_stage_ordinal,
              repair_round, candidate_shas, failures):
        ordered = tuple(sorted(
            failures,
            key=lambda item: (
                item.responsible_repositories,
                item.phase,
                item.suite_key,
                item.child_identifier,
                item.evidence_comment_uuid,
            ),
        ))
        payload = {
            "candidate_shas": dict(sorted(candidate_shas.items())),
            "failures": [item.to_canonical_dict() for item in ordered],
            "parent_identifier": parent_identifier,
            "repair_round": repair_round,
            "source_stage_ordinal": source_stage_ordinal,
            "workflow_version": workflow_version,
        }
        digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        return cls(
            parent_identifier,
            workflow_version,
            source_stage_ordinal,
            repair_round,
            MappingProxyType(dict(sorted(candidate_shas.items()))),
            ordered,
            digest,
        )

    def for_repository(self, repository_key):
        return tuple(
            item for item in self.failures
            if repository_key in item.responsible_repositories
        )
```

The implementation must canonicalize maps and tuples before hashing this exact
payload with `canonical_json()` and SHA-256:

```python
{
    "candidate_shas": dict(sorted(candidate_shas.items())),
    "failures": [failure.to_canonical_dict() for failure in ordered_failures],
    "parent_identifier": parent_identifier,
    "repair_round": repair_round,
    "source_stage_ordinal": source_stage_ordinal,
    "workflow_version": workflow_version,
}
```

Do not accept a caller-supplied digest that differs from the computed value.

- [ ] **Step 4: Extend completion, child, and request types**

Add these fields:

```python
PhaseCompletion.responsible_repositories: tuple[str, ...] = ()
PhaseCompletion.failure_bundle_digest: str = ""
WorkflowChild.phase_result: str = ""
WorkflowChild.evidence_comment_url: str = ""
WorkflowChild.responsible_repositories: tuple[str, ...] = ()
WorkflowChild.failure_bundle_digest: str = ""
WorkflowChild.failure_evidence_uuids: tuple[str, ...] = ()
ChildRequest.failure_bundle: FailureBundle | None = None
ChildRequest.failure_refs: tuple[FailureEvidenceRef, ...] = ()
```

Repository review/QA may own only `repository_key`. Integration QA ownership
must be a nonempty subset of its declared manifest suite. Implementation and
repair completions cannot declare gate ownership. Repair requests require one
bundle, a nonempty partition, and every partition reference must name the
request repository; all non-repair requests forbid these fields.
Repair completions must repeat the exact source bundle digest from their
assigned repair child; every other completion forbids that field.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the two focused classes from Step 2 and confirm PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/multica_delivery/core/workflow.py tests/core/test_workflow.py
git commit -m "feat: model immutable gate failure bundles"
```

---

### Task 3: Complete-stage fan-in and one repair partition per repository

**Files:**
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/core/workflow.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/core/decisions.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/core/test_workflow.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/core/test_decisions.py`

**Interfaces:**
- Consumes: terminal gate children with structured failure evidence.
- Produces: `_failure_bundle(state, decision) -> FailureBundle`.
- Produces: `_repair_requests(state, decision, bundle, ordinal) -> tuple[ChildRequest, ...]`.
- Produces: bundle-bound repair action identities and reconciliation.

- [ ] **Step 1: Write the PRO-65 race as a RED regression**

Add one test that completes QA before review and returns two distinct findings:

```python
def test_pro_65_race_waits_then_dispatches_one_repair_with_both_findings(self):
    snapshot = replace(
        passing_snapshot(),
        attempt=2,
        reviews={
            "api": RepositoryEvidence(SHA["api"], "pending"),
            "web": RepositoryEvidence(SHA["web"], "pass"),
        },
        qa={
            "api": RepositoryEvidence(SHA["api"], "pending"),
            "web": RepositoryEvidence(SHA["web"], "pass"),
        },
    )
    children = (
        WorkflowChild(
            "PRO-201-REVIEW", "api", "api", "", "review", 7, 2,
            "in_progress", "review:" + "1" * 64, True,
            creation_candidate_shas=SHA,
        ),
        WorkflowChild(
            "PRO-202-QA", "api", "api", "", "qa", 7, 2,
            "in_progress", "qa:" + "2" * 64, True,
            creation_candidate_shas=SHA,
        ),
    )
    self.store.add_state(
        "PRO-200",
        snapshot,
        children=children,
        pull_requests=pull_request_targets(),
    )

    qa_result = self.workflow.record_phase_completion(
        completion_for(
            "api",
            phase="qa",
            result="fail",
            responsible_repositories=("api",),
            comment_digit="2",
        )
    )
    self.assertEqual(qa_result.next_action, "wait")
    self.assertEqual(
        tuple(event for event in self.store.events if event[0] == "create"),
        (),
    )

    review_result = self.workflow.record_phase_completion(
        completion_for(
            "api",
            phase="review",
            result="fail",
            responsible_repositories=("api",),
            comment_digit="1",
        )
    )
    requests = [event for event in self.store.events if event[0] == "create"][-1][2]
    self.assertEqual(review_result.next_action, "repair")
    self.assertEqual(len(requests), 1)
    self.assertEqual(requests[0].repository_key, "api")
    self.assertEqual(
        {ref.child_identifier for ref in requests[0].failure_refs},
        {"PRO-201-REVIEW", "PRO-202-QA"},
    )
```

- [ ] **Step 2: Add RED fan-in matrix tests**

Cover:

- callback, direct resume, Watcher-style resume, and repeated resume while a
  sibling is active all return `wait` with zero mutation;
- two repositories fail and receive exactly two repair requests sharing one
  bundle digest and repair round;
- a cross-repository integration finding appears in both owner partitions;
- a terminal malformed failure blocks instead of dispatching a partial bundle;
- a duplicate wakeup with the same source Stage and digest is a no-op;
- an existing successor with the same round but a different digest blocks as
  state corruption.

- [ ] **Step 3: Run the fan-in tests and verify RED**

Run:

```bash
python3 -B -m unittest \
  tests.core.test_workflow.GenericWorkflowTests.test_pro_65_race_waits_then_dispatches_one_repair_with_both_findings \
  tests.core.test_workflow.GenericWorkflowTests.test_two_repository_failures_share_one_bundle_and_round \
  tests.core.test_workflow.GenericWorkflowTests.test_duplicate_bundle_dispatch_is_idempotent \
  tests.core.test_decisions.ParentDecisionTests.test_terminal_gate_failures_use_one_shared_repair_decision -v
```

Expected: FAIL because repair dispatch does not yet carry or reconcile a
complete bundle.

- [ ] **Step 4: Implement terminal-stage bundle construction**

Add a private builder that:

```python
def _failure_bundle(self, state: WorkflowState, decision: ParentDecision) -> FailureBundle:
    current = tuple(
        child for child in state.children
        if child.stage_ordinal == state.metadata.stage_ordinal
        and child.attempt == state.snapshot.attempt
    )
    # require every child terminal, exact creation candidates, evidence URL/UUID,
    # non-PASS result ownership, and decision repository agreement
```

It must reject active children, missing terminal evidence, wrong Stage/round,
wrong SHA map, missing ownership, and decision repositories that differ from
the union of bundle owners.

- [ ] **Step 5: Dispatch canonical repository partitions**

Replace inline repair request construction with:

```python
bundle = self._failure_bundle(state, decision)
requests = tuple(
    ChildRequest(
        target_key=repository,
        repository_key=repository,
        suite_key="",
        phase="repair",
        stage_ordinal=ordinal,
        attempt=decision.next_attempt,
        candidate_shas=state.snapshot.candidate_shas,
        pull_request=state.pull_requests[repository],
        failure_bundle=bundle,
        failure_refs=bundle.for_repository(repository),
    )
    for repository in decision.repositories
)
```

Include `bundle.digest` in the repair action-key payload, successor identity,
and post-create reconciliation tuple. Keep implementation and gate action keys
unchanged.

- [ ] **Step 6: Make pure decisions fail closed on incomplete gate evidence**

Extend `_snapshot_problem()` to reject repair repositories outside the affected
set, and make `decide_parent_action()` return WAIT while any repository or
integration gate result is `pending`. Keep the workflow as the authority for
comment UUIDs, Stage membership, and bundle construction.

- [ ] **Step 7: Run focused and adjacent workflow tests**

```bash
python3 -B -m unittest tests.core.test_decisions tests.core.test_workflow -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/multica_delivery/core/workflow.py src/multica_delivery/core/decisions.py tests/core/test_workflow.py tests/core/test_decisions.py
git commit -m "fix: aggregate complete gate stages before repair"
```

---

### Task 4: Completion immutability, out-of-band SHA drift, and authorized extra rounds

**Files:**
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/core/workflow.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/core/test_workflow.py`

**Interfaces:**
- Consumes: bundle-bound repair requests and version 2 parent authorization.
- Produces: `_repair_authority(state, bundle) -> tuple[int, bool]` where the bool records automatic versus human authority.
- Produces: `_candidate_heads_match(state) -> bool` before successor decisions.
- Produces: `AuthorizingComment(comment_uuid, comment_url, author_type)` and `WorkflowSnapshotReader.read_authorizing_comment(parent_identifier, comment_uuid)`.
- Preserves: identical phase replay as no-op; conflicting replay as block.

- [ ] **Step 1: Write RED immutable-completion tests**

Add tests proving:

```python
def test_completed_repair_cannot_submit_a_second_replacement_sha(self):
    first = self.workflow.record_phase_completion(repair_completion(REPLACEMENT_SHA))
    second = self.workflow.record_phase_completion(repair_completion(OTHER_SHA))
    self.assertEqual(first.completed_child_status, "done")
    self.assertEqual(second.next_action, "block")
    self.assertEqual(self.store.candidate_sha("api"), REPLACEMENT_SHA)

def test_identical_completed_repair_replay_is_noop(self):
    completion = repair_completion(REPLACEMENT_SHA)
    self.workflow.record_phase_completion(completion)
    replay = self.workflow.record_phase_completion(completion)
    self.assertEqual(replay.next_action, "noop")
    self.assertEqual(replay.mutation_count, 0)
```

Also prove a completion from a historical Stage, wrong repair round, or child
without the current bundle digest is rejected.

Extend the existing `completion_for()` test helper with
`failure_bundle_digest`. Add this exact wrapper for repair cases:

```python
def repair_completion(candidate_sha, bundle_digest):
    return completion_for(
        "api",
        phase="repair",
        result="pass",
        attempt=3,
        sha=candidate_sha,
        pull_request_url="https://github.com/example/api/pull/7",
        failure_bundle_digest=bundle_digest,
    )
```

- [ ] **Step 2: Write RED PR drift tests**

Build a state whose recorded candidate is `a * 40` and authoritative managed
PR head is `b * 40`, without a current implementation/repair completion. Assert
`resume_parent()` blocks with `out-of-band pull-request head change` and never
creates gate or repair children. Cover each affected repository and a partial
multi-repository drift.

- [ ] **Step 3: Write RED human-authorization tests**

Cover automatic rounds one and two, then:

```python
def test_bundle_bound_member_authorization_grants_exactly_one_extra_round(self):
    state = exhausted_state(bundle_digest="a" * 64)
    state = with_authorization(
        state,
        author_type="member",
        comment_uuid="00000000-0000-4000-8000-000000000021",
        bundle_digest="a" * 64,
        granted_round=3,
    )
    first = self.workflow.resume_parent("PRO-200")
    second = self.workflow.resume_parent("PRO-200")
    self.assertEqual(first.next_action, "repair")
    self.assertEqual(first.created_children, (("api", "repair"),))
    self.assertEqual(second.next_action, "noop")
    self.assertEqual(self.store.automatic_repairs_used("PRO-200"), 2)
```

Reject agent-authored, reused UUID, wrong digest, skipped round, and missing
authoritative comment reread.

Add this immutable reader value and protocol method before implementing the
authorization tests:

```python
@dataclass(frozen=True)
class AuthorizingComment:
    comment_uuid: str
    comment_url: str
    author_type: str

class WorkflowSnapshotReader(Protocol):
    def read_authorizing_comment(
        self, parent_identifier: str, comment_uuid: str
    ) -> AuthorizingComment: ...
```

The stateful fake stores authorizing comments by `(parent_identifier,
comment_uuid)` and records each reread. A missing key raises its existing
read-boundary exception.

- [ ] **Step 4: Run focused tests and verify RED**

Run the new immutable, drift, and authorization test classes. Expected: FAIL.

- [ ] **Step 5: Enforce current-child completion provenance**

Before writing phase evidence, require one active current Stage child whose
creation action, candidate map, repair bundle digest, and round exactly match
the completion. In replay, compare the complete persisted value including
responsibility and bundle identity. Never allow a comment or later run to
reuse a done child with different evidence.

- [ ] **Step 6: Add authoritative PR-head drift preflight**

Immediately before DISPATCH, REPAIR, or MERGE, compare every managed PR head in
the authoritative snapshot with `candidate_shas`. A mismatch is accepted only
inside the atomic reconciliation of the current implementation/repair
completion that names that exact new SHA. Every other mismatch returns a
zero-mutation block.

- [ ] **Step 7: Consume one-shot human authorization safely**

When automatic rounds are exhausted, require a current `RepairAuthorization`
whose comment reread proves `author_type == "member"`, digest equals the
current bundle, and granted round is exactly next. Record the comment UUID in
the repair action identity and clear/mark it consumed in the same metadata
transition that creates the repair Stage. Do not increment
`automatic_repairs_used`.

- [ ] **Step 8: Run focused and full core workflow tests**

```bash
python3 -B -m unittest tests.core.test_workflow -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 4**

```bash
git add src/multica_delivery/core/workflow.py tests/core/test_workflow.py
git commit -m "fix: close repair and candidate mutation races"
```

---

### Task 5: Core executor contract and deterministic repair handoff rendering

**Files:**
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/core/workflow.py`
- Create: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/core/handoffs.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/core/test_workflow.py`
- Create: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/core/test_handoffs.py`

**Interfaces:**
- Consumes: `FailureBundle` and repair partitions from Task 3.
- Produces: `render_repair_handoff(request: ChildRequest) -> str` without raw comment bodies.
- Produces: exact bundle fields on the existing `WorkflowSnapshotReader` and `WorkflowExecutor` protocols.
- Preserves: lifecycle `MulticaClient` mutation authority remains limited to exact provision/apply actions; this task adds no Issue mutation path to that adapter.

- [ ] **Step 1: Write RED handoff serialization tests**

Assert `render_repair_handoff()` contains only deterministic fields:

```text
Parent: PRO-200
Workflow: 2
Source Stage: 7
Repair round: 3
Failure bundle: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Rejected candidates:
- api: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
Required findings:
- PRO-201 | review | fail | evidence https://multica.example/comments/00000000-0000-4000-8000-000000000041
- PRO-202 | qa | fail | evidence https://multica.example/comments/00000000-0000-4000-8000-000000000042
```

Assert canonical ordering is independent of input order. Assert no raw comment
content, environment value, token, command output, or unrelated repository
appears. Assert non-repair requests and malformed partitions are rejected.

- [ ] **Step 2: Write RED executor-protocol reconciliation tests**

Using the existing stateful fake executor, cover malformed responsibility
arrays, digest mismatch, absent evidence URL, duplicate evidence UUID, mixed
workflow version, missing repair partition, and a post-create child whose
stored digest differs from the requested digest. Require every real executor
implementation to persist and reread the same fields through the existing
protocol; do not add mutation methods to the provisioning `MulticaClient`.

- [ ] **Step 3: Run handoff and workflow tests and verify RED**

```bash
python3 -B -m unittest tests.core.test_handoffs tests.core.test_workflow -v
```

Expected: FAIL on missing version 2 persistence/rendering.

- [ ] **Step 4: Implement the pure handoff renderer**

Use a fixed line renderer for the human-readable Issue description. Render
identifiers, digests, repository keys, phase/result, Stage/round, candidate
SHAs, and URLs only. The function requires `request.phase == "repair"`, a
valid `FailureBundle`, and the exact nonempty partition for
`request.repository_key`.

- [ ] **Step 5: Bind the executor protocol and reconciliation to bundle identity**

Extend `WorkflowChild` and the created-child observation tuple to include
bundle digest and sorted failure UUIDs. A concrete executor mutation is
successful only when its authoritative reread matches every requested field.
The package fake proves the protocol; Task 6 implements concrete Eventra CLI
persistence without widening lifecycle apply authority.

- [ ] **Step 6: Run adapter and workflow tests and verify GREEN**

Run the Step 3 command. Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/multica_delivery/core/workflow.py src/multica_delivery/core/handoffs.py tests/core/test_workflow.py tests/core/test_handoffs.py
git commit -m "feat: render bundle-bound repair handoffs"
```

---

### Task 6: Eventra version 2 compatibility CLI and regression parity

**Files:**
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/workflow.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/eventra_adapter.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/tests/test_workflow.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/tests/test_eventra_adapter.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica_delivery/metadata.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica_delivery/workflow.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica_delivery/decisions.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica_delivery/provision.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica_delivery/tests/test_metadata.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica_delivery/tests/test_workflow.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica_delivery/tests/test_decisions.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica_delivery/tests/test_eventra_compatibility.py`

**Interfaces:**
- Consumes: reusable Core version 2 semantics from Tasks 1–5.
- Produces: Eventra `finish-phase --responsible-repository` and JSON `plan-parent` failure-bundle output.
- Preserves: read-only completed version 1 parsing and current Eventra repository identity.

- [ ] **Step 1: Port the reusable Core behavior into Eventra's embedded runtime**

Apply the same domain interfaces and tests to `tools/multica_delivery`, changing
only package import paths. Do not copy package CLI code into Eventra and do not
change Eventra repository, runtime, daemon, Agent, Project, Squad, Autopilot,
or trigger identities.

- [ ] **Step 2: Write RED Eventra phase metadata tests**

Add exact version 2 cases:

```python
completion = PhaseCompletion(
    kind="review",
    result="fail",
    attempt=3,
    evidence_comment="00000000-0000-4000-8000-000000000031",
    frontend_sha=None,
    backend_sha="b" * 40,
    pr_url=None,
    responsible_repositories=("backend",),
)
self.assertEqual(
    build_phase_metadata(completion)["eventra.phase.failure_repositories"],
    '["backend"]',
)
self.assertEqual(
    build_phase_metadata(completion)["eventra.workflow.version"],
    "2",
)
```

PASS with owners, non-PASS without owners, wrong scope, duplicate owner, and
version 1 mutable completion must fail.

- [ ] **Step 3: Write RED parser and plan-parent output tests**

Require repeatable CLI ownership flags:

```bash
python3 -B -m tools.multica.workflow finish-phase PRO-99 \
  --kind review --result fail --attempt 3 \
  --backend-sha bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --evidence-comment 00000000-0000-4000-8000-000000000031 \
  --responsible-repository backend
```

Make `plan-parent` print canonical JSON with keys
`decision`, `action_key`, `reason`, and `failure_bundle`. The PRO-65 fixture
must contain both backend review and QA evidence in the bundle after the final
gate child is done, and no bundle while either child is active.

- [ ] **Step 4: Run Eventra RED tests**

```bash
python3 -B -m unittest \
  tools.multica.tests.test_workflow \
  tools.multica.tests.test_eventra_adapter \
  tools.multica_delivery.tests.test_eventra_compatibility -v
```

Expected: FAIL on version 2 fields and fan-in output.

- [ ] **Step 5: Implement version 2 Eventra CLI behavior**

Add `responsible_repositories: tuple[str, ...]` to the compatibility
`PhaseCompletion`, add `eventra.phase.failure_repositories` to the controlled
metadata keys, and parse it as canonical JSON. Add repeatable
`--responsible-repository` only for non-PASS review/QA. Extend
`PhaseSnapshot` with evidence UUID and owners so `decide_parent_action()` can
construct the same canonical bundle shape as the reusable Core.

For version 1:

- completed child/parent data remains inspectable;
- `finish-phase`, `plan-parent`, and Watcher recovery never mutate a
  nonterminal version 1 parent; and
- output states `version 1 workflow requires explicit migration`.

- [ ] **Step 6: Make Eventra compatibility parity exact**

Update `render_phase_contract()` and `legacy_phase_contract()` tests so the
version 2 generic and Eventra renderers match for implementation, repair,
review, repository QA, cross-stack QA, and smoke. Keep separate frozen tests
for historical version 1 payloads.

- [ ] **Step 7: Run Eventra focused tests and verify GREEN**

Run the Step 4 command. Expected: PASS.

- [ ] **Step 8: Commit Task 6 in the Eventra repository**

```bash
git add tools/multica/workflow.py tools/multica/eventra_adapter.py tools/multica/tests/test_workflow.py tools/multica/tests/test_eventra_adapter.py tools/multica_delivery
git commit -m "feat: adopt version two gate fan-in contract"
```

---

### Task 7: Align Agent authority contracts and provisioning instructions

**Files:**
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/instructions/delivery_lead.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/instructions/independent_reviewer.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/instructions/integration_qa.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/instructions/frontend_engineer.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/instructions/backend_engineer.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/instructions/squad.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/instructions/workflow_watcher.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/instructions/eventra_project.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/instructions/eventra_backend_project.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/README.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/docs/multica-delivery-core.md`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica/tests/test_operator_docs.py`
- Modify: `/Users/didi/Eventra-workspace/Eventra/tools/multica_delivery/tests/test_documentation.py`

**Interfaces:**
- Consumes: version 2 CLI commands and bundle semantics.
- Produces: provisioned contracts with no direct gate-to-implementer repair path.
- Preserves: team identities, exact-SHA gates, automatic development merge, manual production deployment.

- [ ] **Step 1: Write RED instruction-contract tests**

Assert normalized role text contains these exact authority statements:

```python
self.assertIn("wait for every current Gate Stage child to become terminal", lead)
self.assertIn("one immutable FailureBundle", lead)
self.assertIn("must not mention or message an Implementer to request repair", reviewer)
self.assertIn("must not mention or message an Implementer to request repair", qa)
self.assertIn("modify business code only for a current active implementation or repair child", frontend)
self.assertIn("modify business code only for a current active implementation or repair child", backend)
self.assertIn("cannot create a FailureBundle or dispatch repair", watcher)
```

Add negative scans rejecting the existing phrases `Route failures to the
owning implementer` and `Fix returned findings in a new commit` from gate and
implementer contracts.

- [ ] **Step 2: Run documentation tests and verify RED**

```bash
python3 -B -m unittest \
  tools.multica.tests.test_operator_docs \
  tools.multica_delivery.tests.test_documentation -v
```

Expected: FAIL because existing instructions still authorize direct routing.

- [ ] **Step 3: Rewrite role contracts around the Core authority**

Delivery Lead instructions must require canonical `plan-parent` JSON, complete
bundle validation, one repair Stage, one child per owner, and bundle-bound
human authorization after automatic exhaustion. Reviewer/QA instructions must
end at structured verdict completion. Implementer instructions must reject
gate comments and completed-child mentions as coding authority. Watcher must
classify version mismatch, malformed bundle, and PR drift as human blocks.

- [ ] **Step 4: Update operator and Core documentation**

Document version 2 metadata, bundle fields, Stage fan-in, read-only version 1
history, exact dry-run/apply boundary, and the separate authority required for
push/tag/release/deploy. Remove version 1 examples from the new-Issue path but
retain a labeled historical inspection example.

- [ ] **Step 5: Run documentation and provision rendering tests**

```bash
python3 -B -m unittest \
  tools.multica.tests.test_operator_docs \
  tools.multica.tests.test_blueprint \
  tools.multica.tests.test_provision \
  tools.multica_delivery.tests.test_documentation \
  tools.multica_delivery.tests.test_provision -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 7 in the Eventra repository**

```bash
git add tools/multica/instructions tools/multica/README.md docs/multica-delivery-core.md tools/multica/tests/test_operator_docs.py tools/multica_delivery/tests/test_documentation.py
git commit -m "docs: enforce coordinator-only repair dispatch"
```

---

### Task 8: Package version 0.2.0, templates, migration, and skill guidance

**Files:**
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/__init__.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/core/provision.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/cli/upgrade.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/cli/templates.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/templates/framework.lock`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/src/multica_delivery/templates/AGENTS.md`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/skills/multica-multi-repo-delivery/SKILL.md`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/skills/multica-multi-repo-delivery/references/lifecycle.md`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/skills/multica-multi-repo-delivery/references/safety-boundaries.md`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/skills/multica-multi-repo-delivery/references/troubleshooting.md`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/README.md`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/CHANGELOG.md`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/test_package.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/test_documentation.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/cli/test_doctor_upgrade.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/cli/test_init_validate.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/core/test_provision.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/parity/test_eventra_fixture.py`
- Modify: `/Users/didi/Eventra-workspace/multica-multi-repo-delivery/tests/parity/fixtures/eventra-expected.json`

**Interfaces:**
- Produces: package and Skill version `0.2.0` with workflow metadata version `2`.
- Produces: explicit `0.1.0 -> 0.2.0` migration plan; applying remains separately authorized.
- Preserves: immutable v0.1.0 history and exact Eventra manifest identity.

- [ ] **Step 1: Write RED release identity and template tests**

Require:

```python
self.assertEqual(multica_delivery.__version__, "0.2.0")
self.assertEqual(WORKFLOW_METADATA_VERSION, 2)
self.assertIn("workflow_metadata_version: 2", rendered_lock)
self.assertIn(("0.1.0", "0.2.0"), _MIGRATION_EDGES)
```

Update skill tests to require CLI `0.2.0`, fan-in language, version 1 migration
guidance, and the exact-plan authorization boundary.

Add Core provision tests requiring generated Delivery Lead instructions to
wait for the complete terminal gate Stage and use the failure bundle,
Reviewer/QA instructions to stop at verdict evidence, Engineer instructions to
require a current repair child, and the Watcher to forbid repair dispatch.

- [ ] **Step 2: Run release/documentation tests and verify RED**

```bash
python3 -B -m unittest \
  tests.test_package \
  tests.test_documentation \
  tests.cli.test_doctor_upgrade \
  tests.cli.test_init_validate \
  tests.core.test_provision \
  tests.skill.test_skill_package \
  tests.parity.test_eventra_fixture -v
```

Expected: FAIL on v0.1.0 and metadata version 1 assertions.

- [ ] **Step 3: Bump package, Core, templates, and Skill to 0.2.0**

Set `__version__ = "0.2.0"`, `WORKFLOW_METADATA_VERSION = 2`, update lock and
AGENTS templates, and require `multica-delivery --version` to report `0.2.0`
in the Skill. Changelog entries must state that v2 is a fail-closed workflow
metadata migration and that v1 active parents require explicit migration.

Update `_desired_state()` Agent instructions in Core provisioning so every
newly generated team carries the same coordinator-only fan-in authority as
Eventra. Keep the lifecycle `apply` mutation allowlist unchanged.

- [ ] **Step 4: Implement the explicit upgrade edge**

Add `("0.1.0", "0.2.0")` to the closed migration graph. The generated
migration plan may update installed framework files and Multica Agent
instructions, but it must not mutate Issues, adopt PR SHAs, merge, push, tag,
release, or deploy. It must require the existing complete 64-character plan
hash approval before apply.

- [ ] **Step 5: Refresh Eventra parity fixtures without changing topology**

Update only expected versioned metadata and digests. Assertions for repository
keys, GitHub repositories, dependency DAG, merge order, commands, secret names,
runtime/daemon compatibility IDs, and automatic-merge/manual-deploy policy must
remain unchanged.

- [ ] **Step 6: Run release/documentation tests and verify GREEN**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 7: Build and inspect package artifacts locally**

```bash
python3 -m build
python3 -B -m unittest tests.test_wheel tests.test_reproducible_build -v
```

Expected: wheel and sdist report `0.2.0`; reproducible-build tests PASS. Do not
publish or tag.

- [ ] **Step 8: Commit Task 8 in the package repository**

```bash
git add src skills tests README.md CHANGELOG.md
git commit -m "feat: prepare multica delivery v0.2.0"
```

---

### Task 9: Whole-system verification, safety audit, and dry-run handoff

**Files:**
- Modify only if a verification failure exposes a requirements gap; any such fix must start with a focused RED regression in the owning repository.
- Inspect: both repositories' complete committed diffs.

**Interfaces:**
- Consumes: Tasks 1–8.
- Produces: fresh verification evidence and a sanitized Multica dry-run plan.
- Does not authorize: live apply, push, tag, release, merge, or deployment.

- [ ] **Step 1: Run the complete reusable-package suite**

From `/Users/didi/Eventra-workspace/multica-multi-repo-delivery`:

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
python3 -B -m compileall -q src tests
git diff --check
```

Expected: all tests PASS, compileall exit 0, diff check exit 0.

- [ ] **Step 2: Run the complete Eventra tools suite**

From `/Users/didi/Eventra-workspace/Eventra`:

```bash
python3 -B -m unittest discover -s tools -p 'test_*.py' -v
python3 -B -m compileall -q tools/multica tools/multica_delivery
git diff --check
```

Expected: all tests PASS, compileall exit 0, diff check exit 0.

- [ ] **Step 3: Run safety scans in both repositories**

Require zero matches for direct gate-driven repair language and deployment
authority expansion:

```bash
rg -n "Route failures to the owning implementer|Fix returned findings in a new commit" tools src skills
rg -n "automatic production deployment|production automatic merge" tools src skills docs README.md
```

Inspect any match in historical specs separately; runtime instructions and
current Skill guidance must have zero prohibited matches. Also run existing
contract-audit and redaction test modules in each repository.

- [ ] **Step 4: Review both branch diffs against the Spec**

Check each acceptance criterion and verify:

- failure bundle includes every terminal non-PASS gate;
- no active sibling permits repair;
- one repair child per responsible repository;
- exact bundle digest in action/reconciliation identity;
- completion replay and out-of-band head behavior;
- version 1 read-only and version 2 mutable boundaries;
- one-shot human authorization;
- role instructions and Watcher authority;
- automatic merge/manual deployment policy unchanged.

- [ ] **Step 5: Generate a read-only Eventra provisioner dry-run**

Run this existing Eventra provision command without `--apply`:

```bash
python3 -B -m tools.multica.provision \
  --runtime-id de500649-cada-4419-9d5d-279045e2eaae \
  --daemon-id 019fab98-bbad-7d17-b0b7-26e56dbe1b6f
```

Capture only sanitized action kinds/counts and changed resource identities; do
not print or request backend secret values in dry-run.

Expected: updates target existing Agent/instruction resources only; no duplicate
Squad, Agent, Project, Autopilot, or trigger create appears.

- [ ] **Step 6: Stop at the live-apply authority boundary**

Present:

- package and Eventra commit SHAs;
- complete test counts and commands;
- dry-run action summary;
- whether a framework migration plan/hash is required;
- confirmation that push/tag/release/merge/deployment were not performed.

Request explicit authorization before live Multica apply. If apply is later
authorized, run exactly the fresh plan, verify authoritative convergence, run a
second plan requiring zero mutations, and then stop again before GitHub push,
tag, or release.

- [ ] **Step 7: Commit any verification-only documentation updates**

Only when Step 4 found a documentation evidence gap:

```bash
git add docs/multica-delivery-core.md tools/multica/README.md tools/multica/tests/test_operator_docs.py tools/multica_delivery/tests/test_documentation.py
git commit -m "docs: record gate fan-in verification"
```

If no files changed, do not create an empty commit.
