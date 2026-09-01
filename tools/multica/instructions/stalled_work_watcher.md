# Eventra stalled-work Watcher

This is a bounded run-only recovery task. The native Multica Stage barrier is
the normal coordinator; this schedule only repairs factual dispatch drift.

From the checked-out Eventra frontend repository root, invoke this exact argv
with shell expansion disabled:

```text
python3 -B -m tools.multica.workflow watch --project-id __FRONTEND_PROJECT_ID__ --backend-project-id __BACKEND_PROJECT_ID__ --apply
```

The helper may inspect only those two Projects. Workflow contract version `2`
is the only recoverable authority. Version `1` may be recognized only to report
a migration block; it is never a recovery target and must not cause a rerun or
any metadata, status, Stage, or action-history write. For an exact v2 target,
the helper rereads authoritative Issue and run state, performs at most one
existing-Issue rerun, and verifies a new active task before reporting recovery.
The command's first Project is the sole parent/control Project; the second
Project is only for backend repository children. A parent in the backend
Project is not recoverable, while an exact backend child remains eligible under
its repository-specific assignment authority.
Treat `queued`, `dispatched`, `running`, and `waiting_local_directory` as
active. Do not duplicate a child, stage, PR, comment, repair attempt, or run.

If the helper reports no candidate, finish without mutation. If it fails,
report only the fixed error and stop; do not improvise a rerun, status change,
merge, permission request, secret lookup, process termination, repository
mutation, deployment, or production action.
