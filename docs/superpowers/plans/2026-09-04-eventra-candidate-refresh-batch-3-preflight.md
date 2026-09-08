# Candidate refresh batch 3 — recovery preflight

Date: 2026-09-04. Status: **Task 5/6 implementation paused for protocol refinement**.

Follow-up: the user approved the refinement direction. The written addendum is
`../specs/2026-09-04-eventra-candidate-refresh-authority-design.md`, pending review.
The observations below retain the preflight's historical state; code implementation
has not resumed and live authorization has not changed.

This is an investigation record, not an approved replacement specification or
evidence that the refresh executor is complete. No production code was changed.

## Scope and baseline

- Worktree: `/Users/didi/Eventra-workspace/Eventra/.worktrees/eventra-candidate-refresh`
- Branch: `codex/eventra-candidate-refresh`
- Tested code: `b9d259cdb65ef72173038cfa271903229efbfcce`
- `python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'`: exit 0,
  **584 tests**, 20.476 seconds. The invalid-attempt argparse output is an expected
  negative test, not a suite failure.
- After recording these findings, the same full suite passed again: exit 0,
  **584 tests**, 19.619 seconds. Documentation whitespace and the Context Receipt
  JSON were checked; no code implementation or commit was made in this batch.
- Knowledge verification: exit 0, 12 entries.
- Only local reads, CLI help, official upstream source reads, and documentation
  edits in the isolated worktree. No live Multica writes or experiments, remote
  pushes, PR #14 changes, merges, deployment, historical Smoke, or Cooper usage.
- Original checkout and its user-owned untracked overview remain untouched.

## Evidence that changes the recovery assumptions

The installed CLI symlink resolves to Homebrew Multica **0.4.38**. Official source
was inspected at tag `v0.4.38` through read-only GitHub contents/tree queries.
This establishes that tag's implementation, **not the deployed pro-1 server
version or an approved live deployment contract**.

1. [CreateComment SQL](https://github.com/multica-ai/multica/blob/v0.4.38/server/pkg/db/queries/comment.sql#L424)
   creates the comment and increments its owning Issue revision by 1 in the same
   statement. A request comment and a grant comment therefore each affect the
   frozen parent's revision. Comment edits/deletes also affect owner revision;
   see the [comment activity tests](https://github.com/multica-ai/multica/blob/v0.4.38/server/internal/handler/comment_touch_issue_test.go).
2. [Metadata SQL](https://github.com/multica-ai/multica/blob/v0.4.38/server/pkg/db/queries/issue.sql#L561)
   increments revision only when a key's value actually changes. An identical
   repeated write does not increment revision. Timestamp updates are separate.
3. [Status SQL](https://github.com/multica-ai/multica/blob/v0.4.38/server/pkg/db/queries/issue.sql#L254)
   changes board position as well as status/revision. Recovery cannot assume the
   status field is the only observable effect. Local `issue update --help` exposes
   `--position` and `--no-start`; choosing the exact mutation route belongs in the
   revised adapter contract and its tests.
4. [Metadata handler](https://github.com/multica-ai/multica/blob/v0.4.38/server/internal/handler/issue_metadata.go)
   documents a 50-key and 8KB per-Issue metadata limit. The current request parser's
   16KiB text bound is not a guarantee that an envelope plus later receipts fits
   platform storage. A prospective whole-map storage budget must be checked
   before the first write; server rejection remains authoritative.

Some GitHub reads encountered TLS timeouts; successful subsequent source reads
supplied the evidence above. These were connectivity errors, not evidence of
invalid GitHub authentication.

## Local reproduction

The existing synthetic `refresh_snapshot_fixture(state="entry")` carries a grant
while keeping the parent at the request's frozen revision 7. The pure admission
function accepts that fixture. In a read-only diagnostic, adding the request
comment and accounting for the two official comment-creation increments yielded:

```text
Existing synthetic fixture: admission succeeds at revision 7
Official create-comment +1 twice: admission rejects at revision 9 : frozen parent revision or status changed
Read-only diagnosis: no Multica/GitHub writes, no production code changes
```

This uses synthetic identities and the real `admit_refresh` parser. It is not a
live reproduction or a claim that Task 5 exists. Fail-closed rejection is correct;
the gap is the missing proof for accepting the legitimate comment-only transition.

`RefreshRequest` binds selected parent fields and its starting revision but no
complete frozen authority or baseline comment-set digest. The later reservation's
`parent_projection_digest` cannot retroactively establish the pre-comment state.
Blindly accepting `old_revision + 2`, accepting any larger revision, or replacing
the frozen revision with the current one would not establish which writes occurred.

## Proposed direction — awaiting approval

Prefer extending the unreleased refresh request/paused-intent protocol to bind a
frozen authority digest and parent-comment baseline digest. The exact schema and
normalization rules need an addendum to the approved design before coding.

The intended proof must compare current authority to that frozen baseline after
accounting only for the exact request/grant comments and a known durable write
prefix. Unexpected comments, edited comments, parent changes, extra revision
increments, ambiguous children, or unknown prefixes stop recovery. It must retain
the original evidence, single-Lead constraint, local lock, merge hold, and
separate Engineer preparation / Lead publication responsibilities.

An alternative is a durable local pre-write journal, but that introduces a second
source of recovery state plus retention and host-migration obligations. It is not
being added implicitly to this Issue-backed pilot. Keeping the present strict
rejection requires manual intervention and would not fulfill automatic recovery.

After approval: refine the protocol and storage bounds; add realistic comment and
metadata mutation fixtures; test legitimate comment-only revision progression
against same-delta external drift; then resume Tasks 5 and 6 in order. Later release
validation must establish the pro-1 server contract without assuming CLI version
alone proves it. No live permission is requested or implied by this proposal.

## Context Receipt

Selected navigation claims were checked against AGENTS.md, package.json,
src/lib/api.js, scripts/smoke-local.sh, and the delivery-control sources. No
canonical knowledge was edited or automatically promoted.

```json
{"candidate_shas":{"frontend":"b9d259cdb65ef72173038cfa271903229efbfcce"},"conflicts":[],"knowledge_digests":{"eventra-dependency-graph":"ff17272e7abd2dac2b2abf94c63f50deb9f3a61c8f9228782dfb7f5f3fc16121","eventra-system-map":"4d5804dde75cdbc11558d315444baf4ebbe5956903cdf2048721376818caf903","frontend-invariants":"48f430289212e9e320701dd67d763a9ca98dca5aeb9e54dc65da6aa0f9858051","frontend-known-pitfalls":"c4da9281e317ec71533e6feceab82f27d13dc907ad48bede6d2a737c35b2198e","frontend-repository-map":"b8012180e7ceeab8b1cd865d07fedccb0b5d03a00cba8234876f0cba2ac9394f","frontend-testing-guide":"21b02c4f90faf9504f696ace27e7dd7c6e175543f8cbd7e0638d402ace4d1783"},"knowledge_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"],"match_reasons":{"eventra-dependency-graph":"repository+task_type+path","eventra-system-map":"repository+task_type+path","frontend-invariants":"repository+task_type+path","frontend-known-pitfalls":"repository+task_type+path","frontend-repository-map":"repository+task_type+path","frontend-testing-guide":"repository+task_type+path"},"repository":"frontend","schema_version":1,"task_id":"PRO-122-refresh-implementation","task_type":"implementation","verified_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"]}
```
