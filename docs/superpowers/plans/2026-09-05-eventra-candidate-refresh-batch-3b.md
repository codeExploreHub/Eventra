# Candidate refresh batch 3B — trusted freeze and initial revision proof

Date: 2026-09-05. Scope: local Eventra control-plane implementation only.

## Outcome

- `RefreshSnapshot` now carries the complete canonical parent comment manifest.
  The read adapter verifies parent/source detail metadata echoes against the
  independent metadata reads and double-reads the full result.
- `freeze_refresh_request` derives every request identity and both baseline
  digests from one validated snapshot. It rejects an unblocked parent, an
  active feature/reservation, source or assignment drift, an unknown writer,
  and metadata capacity overflow.
- `validate_initial_refresh_progress` accepts only the six-key initialization
  prefix and the zero/one/two exact new-comment prefixes. It reconstructs the
  frozen authority and comment set, verifies both request baselines, and then
  requires `current_revision == R0 + metadata_writes + comment_writes`.
- Complete initialization at R0=7 therefore proves M=6, K=2 and revision=15.
  An unregistered request waits; only the fully UUID-bound request and grant
  route to refresh initialization. The existing outer workflow bridge is
  covered by an integration regression and required no production change.

This batch does **not** add mutation methods, stage a request, create a child,
publish a comment or ref, update a PR, deploy control-plane code, or change live
Multica state. Task 5 remains the first write-capable implementation batch.

## TDD and verification

The initial snapshot test failed with missing `comment_manifest`; the trusted
freezer test failed because `freeze_refresh_request` did not exist. The first
progress tests then failed because `validate_initial_refresh_progress` did not
exist. Production behavior was added after those observed failures.

The test boundary applies changed metadata and new comments as +1 parent
revision effects, while identical metadata replay is a no-op. Coverage includes:

- all ten legal M/K initialization prefixes;
- prefix gaps, comments before the four-key pause, and grant without request;
- request/grant UUID rebinding, extra comments, baseline comment edit/deletion;
- same-revision parent field and comment-body changes, including between reads;
- exact request/grant admission and the outer workflow route;
- legacy request/grant/prepared, refresh state-machine, Git, and ordinary v2
  control-plane regressions.

Verification results:

- Candidate/executor/refresh workflow/real-Git selection: 105 tests, exit 0,
  25.865 seconds.
- Full control-plane suite:
  `python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'` —
  602 tests, exit 0, 25.571 seconds. The printed invalid `--attempt 4`
  argparse message is an expected negative test.
- Knowledge index: 12 entries, exit 0.
- `git diff --check`: exit 0 before checkpoint preparation.
- No live Multica/GitHub mutation, push, PR #14 update/merge, production endpoint,
  historical Smoke, Cooper, generic skill, or backend change.

## Context Receipt

Selected claims were checked against AGENTS.md, current source/contracts and the
authoritative sibling-repository boundary. No canonical knowledge was changed.

```json
{"candidate_shas":{"frontend":"9dd942c34f6df1ca16e77c822d73d0de53f8dbe8"},"conflicts":[],"knowledge_digests":{"eventra-dependency-graph":"ff17272e7abd2dac2b2abf94c63f50deb9f3a61c8f9228782dfb7f5f3fc16121","eventra-system-map":"4d5804dde75cdbc11558d315444baf4ebbe5956903cdf2048721376818caf903","frontend-invariants":"48f430289212e9e320701dd67d763a9ca98dca5aeb9e54dc65da6aa0f9858051","frontend-known-pitfalls":"c4da9281e317ec71533e6feceab82f27d13dc907ad48bede6d2a737c35b2198e","frontend-repository-map":"b8012180e7ceeab8b1cd865d07fedccb0b5d03a00cba8234876f0cba2ac9394f","frontend-testing-guide":"21b02c4f90faf9504f696ace27e7dd7c6e175543f8cbd7e0638d402ace4d1783"},"knowledge_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"],"match_reasons":{"eventra-dependency-graph":"repository+task_type+path","eventra-system-map":"repository+task_type+path","frontend-invariants":"repository+task_type+path","frontend-known-pitfalls":"repository+task_type+path","frontend-repository-map":"repository+task_type+path","frontend-testing-guide":"repository+task_type+path"},"repository":"frontend","schema_version":1,"task_id":"PRO-122-refresh-initial-progress","task_type":"implementation","verified_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"]}
```
