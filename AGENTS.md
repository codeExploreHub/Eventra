# Eventra frontend agent instructions

## Repository boundary

This repository owns only the Eventra frontend. Do not edit the nested
`Backend/` directory: it is a non-authoritative copy. The authoritative backend
is the sibling `Eventra-Backend` repository and must be changed only in that
repository's assigned worktree.

## Local development

Use npm and these standard commands:

- `npm run dev:local` starts the frontend on `localhost:3000`.
- `npm run test:local-contract` checks the committed local configuration.
- `npm run smoke:local` checks frontend and backend readiness.

This project uses Next.js 16. Keep `npm run build` mapped exactly to `next
build --webpack`; the local execution environment cannot run Turbopack's
temporary CSS-worker port, while the Webpack production build is supported.

The committed development API default is `http://localhost:8080` through
`NEXT_PUBLIC_API_BASE_URL`.

## Repository knowledge

At task start, verify `docs/agent-knowledge/index.yaml` and the frontend-owned
shared `docs/delivery-knowledge/index.yaml`, then run the path-scoped
`tools.multica.knowledge context` command with the task, task type, changed
paths, and current exact repository SHA. Attach its canonical JSON as the
Context Receipt in normal task evidence.

Knowledge is navigation, not truth. Check every material selected claim
against current code, tests, or an authoritative contract. Report stale or
conflicting knowledge in the Context Receipt; code, tests, and exact-SHA
evidence win. If delivery reveals a novel, verified, reusable fact, propose a
Knowledge Candidate in the versioned evidence block. Do not edit canonical
knowledge incidentally during business work. Curation uses a separate
knowledge pull request with human review; never self-approve or self-merge it.

## Secret safety

`.env.local` is optional for personal overrides. Never commit or print it, or
any other secret, token, credential, or private environment value.

## Delivery gates

Use test-driven development for behavior changes: add a focused failing test,
record the RED result, then implement the smallest passing change. Evidence
must include commands, exit codes, branch, and exact commit SHA. Agents may
create pull requests, but merge only after both reviewer and integration-QA
gates pass.
