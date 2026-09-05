# Candidate refresh batch 3G — idempotent publication and adoption

Date: 2026-09-05. Local control-plane checkpoint only.

- A completed Stage 2 preparation is revalidated against its immutable request,
  grant, source evidence, current PR/base, control-tool identity, staging ref,
  two-parent commit and full tree before publication. The full
  `PreparedCandidate` is persisted in `candidate_registered` and read back
  before the original managed branch can move.
- Candidate publication uses the existing normal fast-forward Git path and
  judges the result from fresh remote state: source means no confirmed effect,
  the exact registered target means the effect is present, and every other head
  is a conflict. An acknowledgement loss is recovered without another child or
  a second effective push.
- Adoption is a fixed, recoverable prefix: adoption receipt, parent frontend
  SHA, consumed receipt, and `adopted` checkpoint. `merge_state=not_ready`, the
  merge hold, `last_action`, and `next_stage=3` remain intact. Only after the
  adopted checkpoint is read back is the reservation removed.
- Every one of the seven publication/adoption API write boundaries was injected
  before and after its effect. Retry converges to the same exact terminal state,
  preserves the original Stage 1 child and Stage 2 evidence, performs one
  effective managed push, and terminal replay is a zero-mutation noop.
- Publication fails closed on authorization, request/source evidence, base,
  control-tool, prepared evidence, child membership, parent status, active
  writer, PR head, and illegal partial-adoption drift. A real temporary-Git race
  also confirms base drift that occurs during publication is detected after the
  managed push and leaves the durable reservation for human resolution.
- Independent review found one Important issue at final reservation deletion:
  the original post-read could absorb an unrelated concurrent parent write.
  A RED regression reproduced a valid concurrent comment and revision change.
  The fix now accepts only reservation removal, parent revision `+1`, metadata
  echo synchronization, and server activity-clock changes; every other state
  difference fails closed. Follow-up review reported no Critical, Important, or
  Minor findings and marked Task 7 ready.

Verification from the post-review implementation tree:

- Candidate/executor/real-Git selection: 114 tests, exit 0, 22.930 seconds.
- Full control-plane suite:
  `python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'` —
  630 tests, exit 0, 25.412 seconds. The printed invalid `--attempt 4`
  argparse message is an expected negative test.
- Knowledge index verification: 12 entries, exit 0. The first Context Receipt
  attempt exposed the linked worktree's nonexistent default Backend relative
  path; rerunning with explicit authoritative frontend and Backend roots
  succeeded without modifying either knowledge index.

## Context Receipt

```json
{"candidate_shas":{"frontend":"b2a2c2115feb43c4e1a92c43a739edc1cb0e937b"},"conflicts":[],"knowledge_digests":{"eventra-dependency-graph":"ff17272e7abd2dac2b2abf94c63f50deb9f3a61c8f9228782dfb7f5f3fc16121","eventra-system-map":"4d5804dde75cdbc11558d315444baf4ebbe5956903cdf2048721376818caf903","frontend-invariants":"48f430289212e9e320701dd67d763a9ca98dca5aeb9e54dc65da6aa0f9858051","frontend-known-pitfalls":"c4da9281e317ec71533e6feceab82f27d13dc907ad48bede6d2a737c35b2198e","frontend-repository-map":"b8012180e7ceeab8b1cd865d07fedccb0b5d03a00cba8234876f0cba2ac9394f","frontend-testing-guide":"21b02c4f90faf9504f696ace27e7dd7c6e175543f8cbd7e0638d402ace4d1783"},"knowledge_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"],"match_reasons":{"eventra-dependency-graph":"repository+task_type+path","eventra-system-map":"repository+task_type+path","frontend-invariants":"repository+task_type+path","frontend-known-pitfalls":"repository+task_type+path","frontend-repository-map":"repository+task_type+path","frontend-testing-guide":"repository+task_type+path"},"repository":"frontend","schema_version":1,"task_id":"PRO-122-refresh-publication","task_type":"implementation","verified_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"]}
```

There is still no refresh CLI/Watcher mutation entry, live Multica/GitHub call,
push, deployment, PR #14 update or merge, Smoke action, Cooper operation,
generic-skill change, backend change, or canonical knowledge-body change in
this batch.
