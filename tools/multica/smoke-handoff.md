# Eventra pilot: complete Smoke handoff and one wake-up

This is a bounded improvement to the existing executor, not a new stage,
authorization protocol, watcher, or knowledge-curation loop. It addresses the
missing execution paths and duplicate comment/rerun observed during PRO-136.
Do not reopen completed Issues to deploy or test it.

## New Smoke actions

Delivery Lead supplies `--handoff-file /absolute/path/smoke-handoff.json` to
`execute-parent-smoke` alongside the exact action key from `plan-parent`.
Prepare this non-secret JSON before creating the task:

```json
{
  "control_tool_workspace": "/absolute/eventra-control-checkout",
  "control_tool_sha": "cccccccccccccccccccccccccccccccccccccccc",
  "repositories": {
    "backend": {
      "runtime_workspace": "/absolute/backend-source-workspace",
      "inspection_workspace": "/absolute/task-owned-smoke-inspection",
      "candidate_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "merged_sha": "dddddddddddddddddddddddddddddddddddddddd",
      "pr_url": "https://github.com/codeExploreHub/Eventra-Backend/pull/8"
    }
  }
}
```

Replace **all** example values with observed values. For frontend-only use a
`frontend` entry instead; cross-stack supplies both. Repository keys, candidate
SHAs, and exact PR URLs must match the parent plan. No other fields are accepted.
The existing 8 KiB reservation cap still applies. Routes remain the existing
frontend-only, backend-only and cross-stack routes; environment/credentials
stay in approved local runtime configuration, not in this manifest.

The executor validates and freezes this object in its existing reservation,
includes it in the child's original description, and only then completes the
existing metadata/assignment checks and promotes the child. Missing or invalid
input causes zero new writes. A retry that creates a new Smoke also needs the
manifest. An interrupted action can replay without a file using the saved
manifest. Supplying different data during reservation recovery is rejected.
Old reservations without the optional field retain their original protocol;
there is no bulk migration or rewriting of historical descriptions.

## QA consumption and Git verification

The manifest is **not** trusted evidence or authority to mutate repositories.
The validator checks shape, candidate/PR binding, and path isolation; it does
not claim that directories exist, are clean, contain the advertised SHA, or
that the supplied merge SHA came from GitHub. QA must verify these facts:

1. Read the source Project/runtime context and verify the control checkout's
   HEAD equals `control_tool_sha`. Treat both the named source runtime and the
   actual dynamically assigned runtime as protected. They need not be the same
   path. Never switch either runtime HEAD to accommodate the handoff.
2. Query the exact PR URL for `state,headRefOid,mergeCommit`. Require MERGED,
   the unchanged candidate head, and the declared merge SHA. Derive the fetch
   repository from that verified GitHub PR URL and its head ref as
   `refs/pull/NUMBER/head`; no credentials or arbitrary fetch commands are
   accepted from a manifest. Use host execution for GitHub CLI when required
   by local authentication/network policy.
3. Use the named, task-owned external inspection checkout. If it does not
   exist, QA may initialize a new isolated Git repository there and fetch the
   verified repository's PR ref and exact merge commit without updating any
   branch. If it exists, prove task ownership and cleanliness first. A
   conflicting or unrelated existing directory is not permission to clean it.
   Inspect aliases/symlinks so it cannot resolve inside a protected checkout.
4. Verify the fetched PR head and merge object independently; detach only the
   inspection checkout at the merge SHA. Run the existing scope-aware Smoke
   scripts there. A changed default branch is never substituted for this SHA.
   Record candidate and actual tested merge SHA separately. Workflow completion
   flags still name the candidate; Context Receipts name the actual tested SHA.
5. Stop only processes this task started. Remove only a temporary checkout this
   task created; leave handed-off/pre-existing inspection checkouts intact.

For legacy in-flight children, use the already approved explicit handoff. This
change does not silently upgrade their identity or fetch/test policy.

## Single-trigger recovery

On an installation where a handoff comment wakes the assigned Agent, the
comment is the wake-up. Save its UUID and inspect the Issue's runs; never follow
it with an extra rerun. Queued/dispatched/running means reuse the task, and a
completed task means that wake-up has already been consumed. Delayed visibility
or a lost acknowledgement does not justify a second trigger.

Use direct rerun only as a separate, authorized recovery when comment triggering
is known to be disabled and the exact Issue is idle with no pending trigger.
Do not run an operator recovery concurrently with Watcher. Existing executor
replay and Watcher active-run guards remain unchanged. This is an Agent/operator
rule, not an atomic guarantee against arbitrary external writers.

## Rollout and acceptance

Local implementation and tests do not update Multica live Agent instructions.
After review/QA and merge, install the new helper at a pinned control checkout
and update only Delivery Lead and Integration QA instructions together. Verify
their live instruction contents and tool checkout SHA; do not run a broad
roster/project reprovision. Do not generate a live test task as part of rollout.
Drain old queued/in-flight Smoke creation before switching versions so an old
helper never attempts to resume a new-format reservation. Rollback also waits
for new-format reservations to drain; a broad downgrade is not safe mid-action.

Acceptance for the next ordinary delivery Issue:

- The unique Smoke child has a complete manifest in its **creation** payload.
- QA uses the named control/inspection paths and verified merged commit, with
  no human comment needed to provide missing paths.
- Initial promotion/replay produces only one active run. Any necessary comment
  recovery uses the comment-triggered run, not comment plus rerun.
- Existing tests, Smoke evidence, cleanup, and parent completion still pass.

If a new problem appears, preserve its evidence and stop at that boundary; do
not create substitute stages or extend the protocol inside this pilot fix.

## Local verification record — 2026-09-11

- Branch: `fix/smoke-complete-handoff`; base: merged PR #21 commit
  `4a11965a19043c08c9a15ac9019296a5090975ee` on `master`.
- RED: the missing-handoff test originally started Smoke instead of blocking;
  the committed-replay conflict test also originally promoted the old input.
  Both were reproduced before their respective fixes.
- GREEN: `python3 -B -m unittest tools.multica.tests.test_smoke_handoff
  tools.multica.tests.test_workflow.SmokeExecutionTests` — 28 tests, exit 0.
- Final control-plane regression: `python3 -B -m unittest discover -s
  tools/multica/tests -p 'test_*.py' -q` — 765 tests, exit 0 (78.342 seconds).
  Expected argument-validation error text is from negative CLI tests.
- `git diff --check` — exit 0. Knowledge indexes verified (12 entries);
  path-scoped Context Receipt selected and verified six applicable entries.
- Independent read-only review: initial replay mismatch fixed and regression
  rerun; no remaining Critical/Important findings.
- Not done in this change: push, PR creation, merge, live instruction/control
  installation, live issue mutations, or end-to-end live acceptance. The
  duplicate-wake-up improvement is an instruction-level operating rule.
