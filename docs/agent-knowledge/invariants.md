# Frontend invariants

- Keep `npm run build` mapped exactly to `next build --webpack`.
- Keep the committed local API default at `http://localhost:8080` and make it
  configurable with `NEXT_PUBLIC_API_BASE_URL`.
- Do not edit the nested `Backend/` directory; backend work belongs in the
  sibling `Eventra-Backend` worktree.
- Never commit or print `.env.local`, secrets, tokens, credentials, or private
  environment values.
- Behavior changes follow RED, minimal implementation, then GREEN.
- Delivery evidence records commands, exit codes, branch, and exact commit
  SHA. Merge waits for both independent review and integration-QA gates.
