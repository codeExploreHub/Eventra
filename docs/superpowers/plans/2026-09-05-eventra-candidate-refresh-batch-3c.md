# Candidate refresh batch 3C — recoverable request staging

Date: 2026-09-05. Scope: local Eventra control-plane implementation only.

## Outcome

- Added `stage_refresh_request`, which persists only the exact pause prefix:
  request envelope, protocol version, immutable merge hold, and request digest.
- Every attempted mutation is surrounded by authoritative snapshots and the
  Task 4B baseline/revision proof. An ACK-loss state is accepted only when the
  next exact prefix is observed; no effect or any different effect raises.
- Replaying the same request after all four keys are present returns a zero-write
  result. A conflicting request value or a request comment before the four-key
  pause completes is rejected before another write.
- Added a workspace+parent `flock(LOCK_EX|LOCK_NB)` in the Git common directory.
  The production adapter exposes only a refresh-prefixed string metadata setter;
  no status, child creation, start, comment, or Git mutation method exists yet.

This is Task 5A only. Task 5B must still implement the UUID bindings, reserved
checkpoint, Stage 2 child initialization and dispatch recovery. Nothing here is
wired to a live CLI command or deployment.

## TDD and verification

The first staging test failed because `stage_refresh_request` did not exist.
After the minimal implementation, tests covered the exact write order, no-op
replay, all four before-effect and after-effect failure boundaries, conflicting
state, an early comment, and non-blocking same-parent lock exclusion.

- Candidate/executor/refresh workflow/real-Git selection: 109 tests, exit 0,
  21.057 seconds.
- Full control-plane suite:
  `python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'` —
  606 tests, exit 0, 26.678 seconds. The printed invalid `--attempt 4`
  argparse message is an expected negative test.
- `git diff --check`: exit 0 before checkpoint preparation.
- No live Multica/GitHub mutation, push, PR #14 update/merge, production endpoint,
  historical Smoke, Cooper, generic skill, or backend change.

## Context Receipt

```json
{"candidate_shas":{"frontend":"b84d553af82311cee32a7cd110c3b7d411765ed0"},"conflicts":[],"knowledge_digests":{"eventra-dependency-graph":"ff17272e7abd2dac2b2abf94c63f50deb9f3a61c8f9228782dfb7f5f3fc16121","eventra-system-map":"4d5804dde75cdbc11558d315444baf4ebbe5956903cdf2048721376818caf903","frontend-invariants":"48f430289212e9e320701dd67d763a9ca98dca5aeb9e54dc65da6aa0f9858051","frontend-known-pitfalls":"c4da9281e317ec71533e6feceab82f27d13dc907ad48bede6d2a737c35b2198e","frontend-repository-map":"b8012180e7ceeab8b1cd865d07fedccb0b5d03a00cba8234876f0cba2ac9394f","frontend-testing-guide":"21b02c4f90faf9504f696ace27e7dd7c6e175543f8cbd7e0638d402ace4d1783"},"knowledge_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"],"match_reasons":{"eventra-dependency-graph":"repository+task_type+path","eventra-system-map":"repository+task_type+path","frontend-invariants":"repository+task_type+path","frontend-known-pitfalls":"repository+task_type+path","frontend-repository-map":"repository+task_type+path","frontend-testing-guide":"repository+task_type+path"},"repository":"frontend","schema_version":1,"task_id":"PRO-122-refresh-request-staging","task_type":"implementation","verified_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"]}
```
