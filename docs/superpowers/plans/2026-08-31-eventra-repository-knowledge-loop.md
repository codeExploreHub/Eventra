# Eventra Repository Knowledge Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add repository-owned knowledge, Context Receipts, Knowledge Candidates, and a bounded non-Squad Curator to the two Eventra Projects.

**Architecture:** Each repository owns its local indexed knowledge; Eventra owns one shared cross-repository index. Delivery Agents retrieve scoped entries and record receipts/candidates in normal evidence. An Eventra-only Curator validates, deduplicates, creates one correctly routed knowledge Issue, and prepares a knowledge-only PR that requires human review and merge.

**Tech Stack:** Python 3, frozen dataclasses, PyYAML 6.0.2, unittest, Multica CLI 0.4.x, Git/GitHub, Markdown/YAML.

**Spec:** `docs/superpowers/specs/2026-08-31-eventra-repository-knowledge-loop-design.md`

## Global Constraints

- Do not modify `tools/multica_delivery` or generic `multica-multi-repo-delivery` contracts.
- Keep exactly five Squad Agents; Watcher and Curator stay outside.
- Only Backend Engineer and Integration QA receive backend secrets.
- Curator: concurrency 1, no secrets, one candidate/run, schedule `17 2 * * * Asia/Shanghai`.
- Preserve Watcher IDs, trigger, authority, and `*/30 * * * * Asia/Shanghai`.
- Curator allowlist: `AGENTS.md`, `docs/agent-knowledge/**`, and frontend-only `docs/delivery-knowledge/**`.
- Curator cannot change business/delivery state, approve, merge, deploy, or force-push.
- Code/tests/exact-SHA evidence/contracts override prose; curation never blocks delivery.
- Bootstrap entries alone reference reviewed design commit `32a150dfa`; later entries require delivery evidence.
- No live mutation before tests, scalar-free audit, dry run, and explicit approval.

## File Map

- New: `tools/multica/{knowledge_contracts,knowledge}.py` and matching tests.
- New: frontend `docs/agent-knowledge/**` and `docs/delivery-knowledge/**`.
- New: backend `../Eventra-Backend/docs/agent-knowledge/**`.
- Modify: both `AGENTS.md` files; role/Project/Squad instructions.
- Modify: `issue_contracts.py`, `eventra_adapter.py`, `provision.py`, `contract_audit.py` and tests.
- New: `instructions/{knowledge_curator,scheduled_knowledge_curator}.md`.
- Modify: operator README and pilot scenarios.

---

### Task 1: Typed contracts and digests

**Files:** create `knowledge_contracts.py` and `test_knowledge_contracts.py`.

**Produces:** `ContextReceipt`, `KnowledgeEvidenceRef`, `KnowledgeCandidate`, `KnowledgeIndexEntry`, `CurationDecision`, `parse_candidate_json`, `candidate_json`, `candidate_digest`.

- [ ] Write failing tests for digest order independence, tampering, UUID/URL match, Issue IDs, lowercase SHAs, complete cross-repo SHA map, path safety, RFC3339, sensitivity, deprecation, frozen mutation.

```python
def test_tampered_digest_is_rejected(self):
    payload = candidate_payload(candidate_shas={"frontend": "a" * 40})
    payload["digest"] = "0" * 64
    with self.assertRaisesRegex(ValueError, "knowledge candidate"):
        parse_candidate_json(json.dumps(payload))
```

- [ ] Run RED.

```bash
python3 -B -m unittest tools.multica.tests.test_knowledge_contracts -v
```

- [ ] Implement frozen dataclasses. Nested mappings become sorted tuples. Canonical JSON is compact/sorted; digest excludes only its own field. Fixed error categories never echo content.

- [ ] Run GREEN and commit.

```bash
python3 -B -m unittest tools.multica.tests.test_knowledge_contracts -v
git add tools/multica/knowledge_contracts.py tools/multica/tests/test_knowledge_contracts.py
git commit -m "feat: add Eventra knowledge contracts"
```

---

### Task 2: Index verifier, retrieval, and seed knowledge

**Files:** create `knowledge.py`, `test_knowledge.py`, both repository knowledge trees, and shared delivery tree.

**Produces:** `load_index`, `verify_indexes`, `select_knowledge`, `build_context_receipt`, read-only `context` and `verify` commands.

- [ ] Write failing tests for duplicate YAML keys/IDs, traversal, missing file, digest mismatch, broken replacement, illegal backend shared canonical entry, deterministic repo/task/path selection.

```python
def test_selects_matching_entry(self):
    result = select_knowledge(self.entries, "frontend", "qa", ("app/page.tsx",))
    self.assertEqual([item.knowledge_id for item in result], ["frontend-testing"])
```

- [ ] Run RED.

```bash
python3 -B -m unittest tools.multica.tests.test_knowledge.KnowledgeIndexTests -v
```

- [ ] Implement unique-key YAML loading, root containment, exact-byte SHA-256, active-entry filtering with `PurePosixPath.match`, stable ordering, canonical receipt JSON.

- [ ] Seed only verified repository boundaries, commands, API base, Java/Spring rules, secrets, SHA gates, dependency/start order. Use `bootstrap_design` with commit `32a150dfa`. Compute exact hashes with `shasum -a 256 FILE`; never invent an Issue.

- [ ] Verify and commit Eventra, then backend separately.

```bash
python3 -B -m tools.multica.knowledge verify
python3 -B -m unittest tools.multica.tests.test_knowledge -v
git add tools/multica/knowledge.py tools/multica/tests/test_knowledge.py docs/agent-knowledge docs/delivery-knowledge
git commit -m "feat: add Eventra knowledge indexes and retrieval"
```

Backend commit: `docs: add backend agent knowledge index`.

---

### Task 3: Receipts and candidates in Agent evidence

**Files:** modify both `AGENTS.md` files, `knowledge.py`, all delivery instructions, `test_knowledge.py`, `test_operator_docs.py`.

**Produces:** read-only `candidate --input FILE` and one `eventra-knowledge-candidate-v1` fenced JSON block.

- [ ] Write failing CLI/instruction tests.

```python
def test_roles_require_receipt_and_candidate(self):
    for name in ("frontend_engineer.md", "backend_engineer.md", "integration_qa.md", "independent_reviewer.md"):
        text = (Path("tools/multica/instructions") / name).read_text()
        self.assertIn("Context Receipt", text)
        self.assertIn("eventra-knowledge-candidate-v1", text)
```

- [ ] Run RED.

```bash
python3 -B -m unittest tools.multica.tests.test_knowledge tools.multica.tests.test_operator_docs -v
```

- [ ] Implement strict candidate file validation/rendering and fenced-block extraction. Reject malformed/nested blocks without echoing them.

- [ ] Require index/context, receipt, current-code verification, conflict reporting, novel/verified/reusable candidates, no secrets/raw logs, separate knowledge PR. Lead writes `none` or `pending` summary metadata and never waits for curation.

- [ ] Run tests/verify; commit Eventra and backend independently.

---

### Task 4: Freeze real Multica comment-read contract

**Files:** modify `contract_audit.py`, `issue_contracts.py`, `provision.py` and contract tests; add scalar-free versioned comment-shape fixture.

**Produces:** `parse_issue_comments(value, expected_issue_id)` normalized oldest-first.

- [ ] Write failing tests for envelope, IDs, Issue match, parent link, author/body, timestamps, uniqueness, ordering, and scalar-free audit.

```python
def test_comment_audit_discards_scalars(self):
    rendered = json.dumps(collect_comment_contract_shape(self.runner, "PRO-100", COMMENT_UUID))
    self.assertNotIn("COMMENT_BODY_SENTINEL", rendered)
    self.assertNotIn(COMMENT_UUID, rendered)
```

- [ ] Run RED.

```bash
python3 -B -m unittest tools.multica.tests.test_issue_contracts tools.multica.tests.test_contract_audit -v
```

- [ ] When server is reachable, audit one known non-sensitive comment using `issue comment list ISSUE --thread COMMENT --tail 30 --compact --output json`. Transform scalars to type/length before storing. If unreachable, stop before Task 5, report connectivity unknown, and do not invent schema/auth failure.

- [ ] Implement only the observed envelope/fields; add comment-list to read-only prefixes, no comment write.

- [ ] Run contract suites and commit `feat: add strict knowledge evidence read contracts`.

---

### Task 5: Deterministic scan, dedupe, and pure plan

**Files:** modify `knowledge.py` and `test_knowledge.py`.

**Produces:** `KnowledgeParentSnapshot`, `list_pending_parents`, `load_candidate_snapshot`, `decide_curation`, read-only `scan`/`plan`.

- [ ] Write failing tests for pagination, done/blocked top-level only, exact string metadata, oldest-first, duplicate, malformed evidence, missing/cross-repo SHA, sensitivity, stale reread.

```python
def test_duplicate_is_deduplicated(self):
    decision = decide_curation(self.snapshot, (existing_entry(digest=self.digest),))
    self.assertEqual(decision.kind, "deduplicated")
```

- [ ] Run RED.

```bash
python3 -B -m unittest tools.multica.tests.test_knowledge.KnowledgeScanTests -v
```

- [ ] Query both Projects for version 1/pending in done+blocked, page 50, discard children, dedupe ID, sort timestamp/identifier. Action key hashes digest, target Project/repo/type, parent. Never infer routing/dedupe from prose.

- [ ] Verify with knowledge and workflow suites; commit `feat: plan Eventra knowledge curation`.

---

### Task 6: Bounded knowledge Issue apply

**Files:** modify `knowledge.py`, `provision.py`, `test_knowledge.py`.

**Produces:** `curate_once(runner, project_ids, curator_agent_id, apply)` and `curate --apply`.

- [ ] Extend fake argv grammar and write failing tests for backend routing, assignee UUID, description-file, replay, concurrent wake, ambiguous committed create, stale parent, zero non-create mutation.

```python
def test_ambiguous_create_does_not_duplicate(self):
    self.runner.raise_after_committed_issue_create = True
    curate_once(self.runner, self.project_ids, "curator-agent", apply=True)
    second = curate_once(self.runner, self.project_ids, "curator-agent", apply=True)
    self.assertEqual(len(self.runner.created_issues), 1)
    self.assertEqual(second.created, 0)
```

- [ ] Run RED.

- [ ] Implement exact order: double equal snapshot/decision; search action key; write worktree-local description; one Issue create with description-file/assignee-id/project; clean file in finally; reread Issue/action key; record candidate `issue_created`; then set/reread parent `dispatched`. Parent `dispatched` never claims a PR exists.

- [ ] Enforce one candidate/run and redacted output. Add no comment/status/merge/deploy mutations.

- [ ] Run knowledge/provision tests; commit `feat: dispatch bounded Eventra knowledge issues`.

---

### Task 7: Eventra-only Curator topology

**Files:** create Curator persistent/scheduled instructions; modify `eventra_adapter.py`, blueprint/adapter tests.

**Produces:** Eventra-composed `knowledge_curator` and ordered `operational_automations`; generic blueprint remains Watcher-only.

- [ ] Write failing tests: generic operational roles remain one; Eventra roles become Watcher+Curator; five Squad/seven configured Agents; minimal skills/no env; two stable automation keys; exact Curator schedule/placeholders.

```python
def test_eventra_only_adds_curator(self):
    self.assertEqual([a.role for a in build_multi_repo_blueprint("Sample").operational_agents], ["workflow_watcher"])
    self.assertEqual([a.role for a in self.config.blueprint.operational_agents], ["workflow_watcher", "knowledge_curator"])
```

- [ ] Run RED.

- [ ] Compose with `replace(base, operational_agents=base.operational_agents + (curator,))`. Add `AutopilotSpec.key`; replace singular watcher with tuple; preserve Watcher exact fields. Curator scheduled command uses both rendered Project IDs and apply.

- [ ] Instructions allow validation/one knowledge Issue/allowlisted docs/index/PR; forbid Squad coordination, delivery state, business code, secrets, approval/merge/deploy/force-push.

- [ ] Run tests and commit `feat: add Eventra knowledge curator topology`.

---

### Task 8: Multiple operational Autopilot reconciliation

**Files:** modify `provision.py`, `contract_audit.py` and provision/audit/contracts tests.

**Produces:** keyed preflight details, `ProvisioningResult.autopilot_ids`, per-spec reconciliation.

- [ ] Write failing tests for seven Agents/five members/two Autopilots/no Curator env; preserved Watcher/trigger IDs; zero second apply; duplicate desired/live target; drift; multiple triggers; wrong Project/assignee; unrelated object; dry run.

- [ ] Run RED.

- [ ] List once and resolve all exact titles before mutation; reject duplicates; render placeholders; reconcile ordered specs; verify one trigger; preserve IDs; return dict. Validate operational roles outside Squad and exact env recipients.

- [ ] Generalize audit to sorted scalar-free target shapes.

- [ ] Run contract/audit/provision/adapter suites; commit `feat: reconcile Eventra operational automations`.

---

### Task 9: Knowledge-only PR policy

**Files:** modify `knowledge.py`, `test_knowledge.py`, Curator/Reviewer instructions, operator-doc tests.

**Produces:** `verify_knowledge_change(repository, changed_paths, staged_text)`, `record_knowledge_pr`, `reconcile_knowledge_pr`, a strict read-only PR snapshot, and changed-path/staged-text/PR-state CLI support.

- [ ] Write failing allowlist/redaction tests.

```python
def test_backend_rejects_business_path(self):
    with self.assertRaisesRegex(ValueError, "knowledge change"):
        verify_knowledge_change("backend", ("src/main/java/App.java",), "safe")

def test_secret_error_is_redacted(self):
    secret = "ghp_" + "A" * 36
    with self.assertRaises(ValueError) as caught:
        verify_knowledge_change("frontend", ("docs/agent-knowledge/invariants.md",), secret)
    self.assertNotIn(secret, str(caught.exception))
```

- [ ] Run RED.

- [ ] Implement exact frontend/backend allowlists; reject absolute/traversal/symlink escape, empty diff, conflict marker, binary, secret patterns; verify indexes after change.

- [ ] Add state tests for `issue_created -> pr_open -> merged -> verified`, rejected/closed PRs, wrong-repository PR URLs, merged digest mismatch, and idempotent repeated observation.

- [ ] Implement PR convergence: `record_knowledge_pr` validates target-repository URL, rereads an open PR and expected source branch, then records `pr_open`. `reconcile_knowledge_pr` reads only: open stays `pr_open`; closed-unmerged becomes `rejected`; merged becomes `merged`, verifies the exact merged SHA plus indexes/content digests, then becomes `verified`. Missing evidence or digest drift becomes `needs_human`. Tests prove the GitHub argv boundary cannot merge, review, close, push, or deploy.

- [ ] Curator rereads source, verifies repo, edits allowed files, verifies, uses dedicated branch, creates one source-linked PR, records its URL, and stops at `pr_open`. Reviewer checks evidence/current code/ownership/index/secrets. No Curator merge or auto-recreate.

- [ ] Run tests; commit `feat: enforce knowledge-only review handoffs`.

---

### Task 10: Verification, runbook, guarded pilot

**Files:** modify `tools/multica/README.md`, `docs/multica/pilot-issues.md`, operator-doc tests.

- [ ] Write failing docs tests for Curator/schedule/commands/one-candidate/pause/nonblocking/no-generic-change and eight scenarios: no-candidate frontend, backend incident, cross-repo contract, dedupe, concurrent triggers, business-code rejection, stale correction, paused continuity.

- [ ] Run RED; document audit, verify, dry run, approval, apply/reread, trigger, rollback, and metrics.

- [ ] Run Eventra full verification:

```bash
python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py' -v
python3 -B -m tools.multica.knowledge verify
python3 -B -m compileall -q tools/multica
git diff --check
git status --short
```

- [ ] Run backend verification: `scripts/test-local.sh`, diff check, clean status.

- [ ] Commit runbook `docs: add Eventra knowledge loop pilot runbook`.

- [ ] Run live read-only audit and provision dry run. Require scalar-free output, zero mutations, existing Watcher resolution, only Curator objects proposed, five Squad members, no operational env change.

- [ ] Stop for explicit live-mutation approval. Do not apply/create/trigger/PR/merge/deploy before it.

- [ ] After approval run provision apply with `--reuse-backend-env`, then reread. Require unchanged Watcher IDs, Curator outside Squad/no env, exact active schedule, five members, zero second-run mutations.

- [ ] Exercise eight scenarios; stop PRs at human review; measure relevance/count/acceptance/dedupe/correction/staleness/latency/business delay.

- [ ] Pause only Curator and verify Watcher/delivery unchanged; preserve Issues/comments/PRs/Git; re-enable only with approval.
