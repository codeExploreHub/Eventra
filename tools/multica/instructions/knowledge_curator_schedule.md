# Eventra repository knowledge curation run

Run exactly one bounded curation pass from the authoritative Eventra frontend
control repository:

```text
python3 -B -m tools.multica.knowledge curate --project-id __FRONTEND_PROJECT_ID__ --backend-project-id __BACKEND_PROJECT_ID__ --curator-agent-id __KNOWLEDGE_CURATOR_AGENT_ID__ --frontend-root __FRONTEND_ROOT__ --backend-root __BACKEND_ROOT__ --apply
```

Process at most one candidate. Report only the command's redacted JSON result.
Do not coordinate the delivery Squad, edit repository files, create comments,
change delivery state, merge, deploy, or run any additional mutation in this
scheduled control pass.
