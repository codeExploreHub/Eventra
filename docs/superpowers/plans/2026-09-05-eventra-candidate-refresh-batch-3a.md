# Candidate refresh batch 3A — frozen baseline contracts

Date: 2026-09-05. Scope: local Eventra control-plane implementation only.

## Outcome

- Request-v1 now requires `baseline.authority_digest` and
  `baseline.comments_digest`; both values participate in the independently
  checked request digest and deterministic staging ref.
- Complete parent comment responses produce a strict, UUID-sorted manifest that
  binds scope, author, type, revision, thread parent, creation time, and exact
  UTF-8 content digest. The real read adapter invokes this parser before its
  existing comment conversion.
- A new freeze-only authority projection binds complete supported parent/source
  detail, full metadata, original evidence, assignment, PR, prerequisite and tool
  identity while excluding only metadata echoes and the two server activity clocks.
  Metadata echo conflict, unknown semantic detail, a non-blocked parent or a later
  child rejects the freeze.
- Conservative preflight validates 50 keys, 6144 ASCII-JSON bytes, a 3072-byte
  request envelope, and 64-character Issue identifiers. Current synthetic
  candidate-registered/adopted peak fixtures remain within the budget.

This batch does **not** implement Task 4B revision-prefix proof, Task 5 mutations,
Task 6 Engineer completion, CLI wiring, deployment, or live adoption. The two
fixture baseline digests are wire-contract values, not a claim that the fixture was
produced by the not-yet-implemented trusted freezer. Existing exact-revision
admission stays fail-closed.

## TDD and verification

The first request-baseline test failed because production rejected the new field.
The first comment and authority tests failed because their interfaces did not
exist. The timestamp adapter regression failed because the prior parser accepted
an invalid creation time. Each was implemented only after its observed RED.

- Request/Grant/Prepared plus real-Git module: 33 tests, exit 0.
- Candidate/refresh-executor/real-Git modules: 76 tests, exit 0, 12.626 seconds.
- Full control-plane suite:
  `python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'` —
  590 tests, exit 0, 18.536 seconds. The printed invalid `--attempt 4` argparse
  message is an expected negative test.
- `git diff --check`: exit 0.
- Knowledge index: 12 entries verified before implementation.
- No live Multica/GitHub mutation, push, PR #14 update/merge, production endpoint,
  historical Smoke, Cooper, generic skill or backend change.

## Context Receipt

Selected claims were checked against AGENTS.md, current source/contracts and the
authoritative sibling-repository boundary. No canonical knowledge was changed.

```json
{"candidate_shas":{"frontend":"fe1352d76b7e54f9b034b10c9201fc4b6acd3911"},"conflicts":[],"knowledge_digests":{"eventra-dependency-graph":"ff17272e7abd2dac2b2abf94c63f50deb9f3a61c8f9228782dfb7f5f3fc16121","eventra-system-map":"4d5804dde75cdbc11558d315444baf4ebbe5956903cdf2048721376818caf903","frontend-invariants":"48f430289212e9e320701dd67d763a9ca98dca5aeb9e54dc65da6aa0f9858051","frontend-known-pitfalls":"c4da9281e317ec71533e6feceab82f27d13dc907ad48bede6d2a737c35b2198e","frontend-repository-map":"b8012180e7ceeab8b1cd865d07fedccb0b5d03a00cba8234876f0cba2ac9394f","frontend-testing-guide":"21b02c4f90faf9504f696ace27e7dd7c6e175543f8cbd7e0638d402ace4d1783"},"knowledge_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"],"match_reasons":{"eventra-dependency-graph":"repository+task_type+path","eventra-system-map":"repository+task_type+path","frontend-invariants":"repository+task_type+path","frontend-known-pitfalls":"repository+task_type+path","frontend-repository-map":"repository+task_type+path","frontend-testing-guide":"repository+task_type+path"},"repository":"frontend","schema_version":1,"task_id":"PRO-122-refresh-implementation","task_type":"implementation","verified_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"]}
```
