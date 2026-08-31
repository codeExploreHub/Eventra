# Frontend repository map

This repository is the authoritative Eventra frontend. The sibling
`Eventra-Backend` repository is the authoritative backend; do not modify the
nested `Backend/` copy.

- `src/` owns the Next.js application and browser-side API client.
- `scripts/` owns local start, smoke, and contract checks.
- `tools/multica/` owns Eventra's local multi-repository delivery control
  plane, contracts, provisioning, and tests.
- `docs/agent-knowledge/` is the canonical frontend repository knowledge.
- `docs/delivery-knowledge/` is the only canonical shared cross-repository
  knowledge tree.

Verify boundaries against `AGENTS.md` before changing code.
