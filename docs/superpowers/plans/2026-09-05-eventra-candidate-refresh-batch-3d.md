# Candidate refresh batch 3D — reservation and child-create recovery

Date: 2026-09-05. Local control-plane checkpoint only.

- `execute_refresh` reloads the persisted request, validates the exact request
  and member grant comments, binds both UUIDs, and writes an exact `reserved`
  checkpoint whose parent projection includes the reservation revision effect.
- A create-before-effect failure leaves revision 16, no child, and the original
  Stage 1 byte-for-byte unchanged. A create-after-effect ACK loss is recovered
  by one exact Stage 2 backlog child; a second create is not issued.
- The recovery match binds parent, stage, workspace, project, Engineer,
  status, title, canonical description, empty metadata, and absent evidence.

This checkpoint deliberately stops at internal status `child_created`. Child
provenance, parent transition and run dispatch remain Task 5B2. There is no CLI,
live mutation, push, deployment, PR update, Smoke, Cooper, generic-skill or
backend change.

Verification: focused candidate/executor/workflow/Git selection 111 tests,
exit 0; full control-plane suite 608 tests, exit 0, 25.068 seconds. The printed
invalid attempt-4 argparse message is an expected negative test.
