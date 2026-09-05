# Candidate refresh batch 3E — durable initialization and dispatch

Date: 2026-09-05. Local control-plane checkpoint only.

- `execute_refresh` now advances the durable states `reserved →
  child_initialized → child_dispatched`. The Stage 2 child begins in backlog,
  is assigned to the frontend Engineer/project, and receives the exact fixed
  workflow/phase/refresh provenance prefix before any run can start.
- Parent `next_stage=3` and `last_action` are read back before the parent moves
  to `in_progress`. That status transition uses `issue update` with the frozen
  position and `--no-start`; the Engineer child is promoted separately and only
  after the `child_initialized` checkpoint.
- Reservations retain the original parent `status_category` and `position` so
  the fixed parent-write prefix can be reversed and checked after an ACK loss.
  The expected post-transition category remains fail-closed and must be
  confirmed against pro-1 during Task 8 before any live entry is enabled.
- All 17 post-create writes were injected with before-effect and after-effect
  failures. Retry completes the same prefix, creates no second child or run,
  and a completed owner run between dispatch and checkpoint is recognized as
  the same durable dispatch rather than restarted.
- The pure planner accepts only an exact `reserved + child-create/metadata
  prefix`; changed title/body/assignment/revision, extra metadata, evidence or
  a premature run still blocks. This keeps Watcher routing aligned with the
  executor after create acknowledgement loss.

Independent read-only review initially found position preservation, fast-run
completion and planner routing gaps. All three were fixed and the follow-up
review reported no Critical or Important findings.

Verification from the final post-review tree: focused
candidate/executor/Git selection 97 tests, exit 0, 16.752 seconds; full
control-plane suite 612 tests, exit 0, 23.626 seconds. The printed invalid
attempt-4 argparse message is an expected negative test.

There is still no refresh CLI/Watcher mutation entry, live call, push,
deployment, PR update, Smoke action, Cooper operation, generic-skill change or
backend change in this batch.
