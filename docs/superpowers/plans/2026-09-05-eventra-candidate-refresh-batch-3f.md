# Candidate refresh batch 3F — immutable preparation completion

Date: 2026-09-05. Local control-plane checkpoint only.

- `finish_refresh` now accepts only `pass`, `fail`, or `blocked` for the exact
  reserved Stage 2 refresh child. It rereads the frozen request, assignment,
  grant, reservation, parent/source authority, current managed PR, evidence
  comment and child projection before recording a result.
- PASS requires the fixed eight checks, an explicit frontend Context Receipt
  scoped to the refresh child and target SHA, the remote staging ref, the exact
  two-parent commit order, and the verifier-computed full tree. Completion
  changes only the refresh child's SHA/result/evidence/failure metadata and
  status; the managed PR and parent candidate remain at source, and no
  publication, adoption, gate, Smoke, merge, or deployment occurs.
- FAIL/BLOCKED uses the strict `eventra-candidate-refresh-outcome-v1` envelope.
  It binds the same child/request/source/prerequisite and Engineer identity,
  permits only the fixed checks actually run (including `knowledge`), never
  carries target authority, and leaves the child at the source SHA. An empty
  commands map is legal only as a factual record that preparation stopped
  before the fixed checks; the bounded reason remains mandatory.
- Completion is an exact metadata prefix with read-after-write recovery. PASS
  covers five writes and each non-PASS outcome covers four; every before-effect
  and after-effect interruption was injected. Retry converges without changing
  source evidence or publishing Git state. Exact terminal replay is a durable
  noop and does not depend on the later availability of the mutable staging
  ref; changed evidence, SHA, assignment, position, tree, PR head, or unknown
  metadata remains rejected.
- The reservation now binds the refresh child position so a position drift
  cannot be mistaken for a recoverable completion. Existing ordinary
  `finish-phase` continues to reject refresh, and a completed non-PASS refresh
  routes the planner to `block`, not repair.
- The Frontend Engineer contract records the future isolated preparation
  sequence and hardened Git boundary. It explicitly remains non-executable
  until Task 8 supplies a parser-backed `finish-refresh` command, guarded
  worktree handoff, and expected-tree verifier; premature deployment therefore
  instructs the agent to perform no Git or Issue mutation.

Independent read-only review initially found two Important issues: terminal
PASS replay revalidated mutable staging state, and the role instructions
presented an unavailable/insufficiently guarded operation as executable. Both
were fixed. Follow-up review reported no Critical, Important, or Minor findings
and marked Task 6 ready subject to the normal broader integration gates.

Verification from the post-review implementation tree: focused
candidate/executor/operator-doc/refresh-workflow selection 132 tests, exit 0,
8.316 seconds; full control-plane suite 622 tests, exit 0, 23.339 seconds. The
printed invalid attempt-4 argparse message is an expected negative test.

There is still no refresh CLI/Watcher mutation entry, live call, push,
deployment, PR #14 update or merge, candidate publication/adoption, Smoke
action, Cooper operation, generic-skill change, backend change, or canonical
knowledge-body change in this batch.
