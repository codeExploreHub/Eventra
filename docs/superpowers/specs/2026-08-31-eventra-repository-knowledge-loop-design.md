# Eventra Repository Knowledge Loop Design

Date: 2026-08-31
Status: approved in conversation; pending written-spec review

## Context

Eventra currently uses two Multica Projects and one five-role delivery Squad to
coordinate frontend and backend work:

- **Eventra Local Development** owns the `Eventra` frontend repository;
- **Eventra Backend Local Development** owns the `Eventra-Backend` repository;
- **Eventra Local Delivery** contains Delivery Lead, Frontend Engineer, Backend
  Engineer, Integration QA, and Independent Reviewer; and
- **Eventra Workflow Watcher** is an operational Agent outside the Squad that
  recovers bounded stalled work.

The existing workflow gives each repository an Agent owner and uses Issues,
exact-SHA handoffs, Stage barriers, evidence, QA, and independent review to
coordinate delivery. Repository familiarity, however, is reconstructed on
each task from code, `AGENTS.md`, role instructions, Issue history, and model
reasoning. Agents have no persistent private memory that can safely serve as a
repository source of truth. Useful discoveries therefore remain scattered in
Issue comments, pull requests, and transient task context.

This design adds a repository-owned knowledge loop to the Eventra pilot. It
turns verified, reusable delivery experience into versioned files that future
Agents can retrieve, check against current code, review through pull requests,
and deprecate when stale. It does not treat model memory as an authority and
does not introduce a separate vector database or knowledge service.

The pilot is intentionally Eventra-specific. After operational evidence shows
that the contracts, routing rules, and safety boundaries work, a separate
design may promote the reusable parts into `multica-multi-repo-delivery`.

## Goals

- Give the frontend and backend Agent owners durable, repository-local
  knowledge without relying on persistent private model memory.
- Give cross-repository work one canonical place for contracts, dependency
  relationships, and integration playbooks.
- Retrieve only task-relevant knowledge and record what the Agent actually
  consulted.
- Capture reusable findings through the existing delivery evidence instead of
  adding informal Agent-to-Agent chat as a second control plane.
- Validate, deduplicate, review, version, and deprecate knowledge through a
  fail-closed workflow.
- Add a dedicated **Eventra Knowledge Curator** operational Agent without
  changing the five-person delivery Squad.
- Allow the Curator to create knowledge Issues and documentation-only pull
  requests, but never merge them.
- Keep the knowledge loop asynchronous so unavailable curation cannot block
  business delivery.
- Preserve the existing Watcher, delivery state machine, quality gates, merge
  authority, deployment boundary, and backend secret isolation.

## Non-goals

- Do not give Agents a hidden, mutable, shared memory outside version control.
- Do not add embeddings, a vector database, an external RAG service, or a new
  Multica Project for the pilot.
- Do not copy whole repositories into prompts or require Agents to read the
  full knowledge tree for every task.
- Do not let documentation override current code, tests, contracts, or live
  configuration.
- Do not auto-merge knowledge pull requests.
- Do not let the Curator edit business code, dependencies, build files,
  delivery metadata, deployment configuration, or production state.
- Do not make a pending knowledge candidate a quality gate for the originating
  delivery.
- Do not change the generic `multica-multi-repo-delivery` package, its
  `DeliveryManifest`, or its `RepositorySpec` in this pilot.
- Do not implement the separately designed Gate Fan-In version 2 as part of
  this work.

## Selected architecture

Knowledge belongs to the repository whose maintainers and Agent owner must use
and review it. Cross-repository knowledge belongs to one shared delivery
knowledge area rather than being duplicated into both repositories. Agents
communicate durable facts through typed delivery evidence, repository
knowledge, and exact-SHA handoffs; private conversation is not an authoritative
interface.

The pilot adds three capabilities:

1. repository-local, indexed knowledge files;
2. task-scoped retrieval with a recorded `ContextReceipt`; and
3. an asynchronous `KnowledgeCandidate` and Curator workflow that can create a
   documentation-only pull request for human review.

The Curator is an operational Agent, like the Watcher, rather than a sixth
delivery Squad member. It runs daily and may be triggered manually. A run
processes at most one candidate so errors, cost, and behavior remain observable
during the pilot.

## Knowledge layout and ownership

### Frontend repository knowledge

The `Eventra` repository owns:

```text
docs/agent-knowledge/
  index.yaml
  repository-map.md
  architecture.md
  invariants.md
  known-pitfalls.md
  testing-guide.md
  change-playbooks/
  decisions/
  incidents/
```

This area records frontend-specific module boundaries, invariants, recurring
change procedures, testing knowledge, and verified failure patterns.

### Backend repository knowledge

The `Eventra-Backend` repository owns the same structure under
`docs/agent-knowledge/`. It records backend domain boundaries, Spring and data
flow invariants, API implementation patterns, test procedures, and verified
operational pitfalls.

### Shared delivery knowledge

The `Eventra` repository also owns the cross-repository pilot area:

```text
docs/delivery-knowledge/
  index.yaml
  system-map.yaml
  dependency-graph.yaml
  contracts/
  integration-playbooks/
  decisions/
  incidents/
```

It contains API contracts, frontend-to-backend dependency edges, integration
sequencing, shared decisions, and incidents whose lessons affect more than one
repository. The backend index may reference a shared entry by stable knowledge
identifier and source repository path; it must not contain a copied canonical
version.

`Eventra` is the shared location because it already contains the Eventra
delivery-control tooling. This is a pilot ownership decision, not a claim that
frontend code is the conceptual owner. A future generic control repository may
host shared knowledge after the pilot is promoted.

### Canonical files and generated indexes

Markdown and YAML files in these directories are canonical. `index.yaml`
provides navigation and machine-readable selection; it is not a parallel
knowledge store. Each entry contains:

- stable knowledge identifier;
- title and short purpose;
- canonical relative path;
- scope: `frontend`, `backend`, or `cross_repo`;
- applicable repository paths and task types;
- status: `active` or `deprecated`;
- replacement identifier when deprecated;
- source Issue, evidence comment, and exact candidate SHA map;
- last verified commit SHA and date; and
- content digest.

Index verification fails on duplicate identifiers, missing files, invalid
paths, broken replacement links, digest mismatch, or a cross-repository entry
without a canonical shared source.

## Retrieval and Context Receipt

At task start, an implementation, QA, review, or coordination Agent first reads
the repository `AGENTS.md` and the appropriate knowledge index. Retrieval uses
the current Project, repository, task type, changed paths, and declared
cross-repository dependencies to select a small set of entries. It does not
load the entire knowledge tree.

The retrieval result is a typed `ContextReceipt` containing:

- task and repository identity;
- current base or candidate SHA;
- selected knowledge identifiers, paths, versions, and digests;
- the match reason for each entry;
- whether the Agent checked the relevant claim against current code, tests, or
  an authoritative contract; and
- conflicts, missing entries, or suspected staleness.

Knowledge is navigation, not truth. Current code, tests, exact-SHA evidence,
and authoritative contracts win when they conflict with a knowledge entry. An
Agent must report the conflict and may create a candidate to correct the
knowledge; it must not silently follow stale prose.

Required knowledge with a broken index, missing source, or digest mismatch
fails closed for the affected task. Optional knowledge may produce a warning
and continue. The receipt is attached to the task's normal evidence so later
reviewers can see what context informed the work.

## Knowledge candidate contract

Agents do not edit canonical knowledge as an incidental side effect of a
business-code task. When delivery reveals a novel, verified, reusable fact,
the Agent appends a structured `KnowledgeCandidate` to its existing evidence
comment. Ordinary code facts, task-specific commentary, guesses, and
duplicated documentation do not qualify.

A candidate contains:

- schema version and deterministic candidate digest;
- source Project, parent Issue, child Issue, evidence comment UUID, and URL;
- exact candidate SHA map at which the finding was verified, containing the
  source repository and every affected repository for cross-repository claims;
- target scope and target repository;
- proposed knowledge type and concise claim;
- applicability by task type and repository path;
- verification performed and relevant test or evidence references;
- candidate sensitivity classification;
- related or possibly superseded knowledge identifiers; and
- submitting role and timestamp.

Candidate content must not contain credentials, tokens, personal data,
production payloads, or unrestricted command output. References point to
authoritative evidence instead of copying sensitive material.

The Delivery Lead aggregates candidate references when it closes or blocks a
parent. It writes one immutable knowledge summary comment and only flat pointer
metadata on the parent:

```text
eventra.knowledge.version=1
eventra.knowledge.status=pending
eventra.knowledge.summary_comment=<UUID>
eventra.knowledge.candidate_digest=<SHA256>
```

When no candidate exists, the Lead records version `1` and `status=none` and
omits only the summary UUID and candidate digest. The parent delivery does not
wait for curation.

## Knowledge Curator

### Agent placement and permissions

Provision one workspace-visible operational Agent named **Eventra Knowledge
Curator** with role key `knowledge_curator` and
`max_concurrent_tasks=1`. It is not a member of **Eventra Local Delivery** and
receives no backend runtime environment, mail credentials, JWT secret,
deployment capability, or business implementation authority.

The Curator may:

- read completed or blocked Eventra parent Issues and referenced evidence;
- validate and deduplicate candidates;
- create one knowledge Issue in the correct existing Project;
- accept an assigned knowledge Issue in the target repository worktree;
- modify approved knowledge paths;
- update the affected knowledge index; and
- create a documentation-only pull request referencing its source evidence.

It may not:

- edit business code or build, dependency, deployment, or runtime files;
- change delivery Stage, gate, candidate SHA, merge, or deployment state;
- request or use backend secrets;
- merge or auto-approve its pull request;
- promote unverified natural-language claims; or
- overwrite, force-push, or discard another contributor's changes.

### Scheduling and bounded execution

The Curator Autopilot is attached to the frontend Project so one control-plane
run can scan parent Issues in both configured Eventra Projects. It has a daily
schedule of `17 2 * * *` in `Asia/Shanghai` and supports manual execution. The
minute avoids sharing the Watcher's half-hour schedule boundary.

One invocation may select, validate, and dispatch at most one candidate. A
later invocation handles the knowledge Issue in its target Project and
worktree. For a backend candidate, the control run creates a knowledge Issue in
**Eventra Backend Local Development** assigned to the Curator; that assignment
provides the backend worktree on the next run. No cross-repository filesystem
mutation is attempted from the wrong Project context.

## Components and configuration changes

### Repository instructions

Update frontend and backend `AGENTS.md` files to require:

- index-first, path-scoped retrieval;
- a `ContextReceipt` in task evidence;
- current-code verification of material knowledge claims;
- candidate creation rather than incidental canonical knowledge edits;
- separate business-code and knowledge pull requests; and
- no self-approval or self-merge of knowledge changes.

Update Eventra role and Project instructions for Delivery Lead, Frontend
Engineer, Backend Engineer, Integration QA, Independent Reviewer, the Squad,
and both Projects. Add `knowledge_curator.md` for the operational role.

### Knowledge modules

Add `tools/multica/knowledge_contracts.py` for exact immutable models including
`ContextReceipt`, `KnowledgeCandidate`, `KnowledgeEvidenceRef`,
`KnowledgeIndexEntry`, and `CurationDecision`.

Add `tools/multica/knowledge.py` for index verification, retrieval, candidate
scan, deterministic planning, and bounded curation. The intended command
surface is:

```text
python3 -B -m tools.multica.knowledge scan
python3 -B -m tools.multica.knowledge plan
python3 -B -m tools.multica.knowledge curate --apply
python3 -B -m tools.multica.knowledge verify
```

`scan`, `plan`, and `verify` are read-only. `curate --apply` performs only the
single planned mutation chain and verifies every authoritative acknowledgement
by rereading it.

### Multiple operational automations

Eventra's `ProjectConfig` currently models a singular Watcher. Replace that
pilot-specific field with an ordered
`operational_automations: tuple[AutopilotSpec, ...]` containing the existing
Watcher and the new Curator.

Update `eventra_adapter.py`, `provision.py`, `contracts.py`, and
`contract_audit.py` so reconciliation:

- resolves each automation by a stable exact key;
- rejects duplicate desired or live exact-name targets;
- preserves existing Watcher Autopilot and trigger IDs;
- creates or updates only the intended Curator objects;
- verifies schedule, assignee, Project, execution mode, and status after every
  mutation;
- remains idempotent on a second apply;
- keeps both operational Agents out of the Squad; and
- proves that neither operational Agent receives backend secrets.

The generic package remains unchanged during this pilot.

## Runtime workflow and state machine

The end-to-end flow is:

1. An Agent accepts a delivery task, retrieves scoped knowledge, verifies
   applicable claims, and emits a `ContextReceipt`.
2. The Agent performs its existing implementation, review, QA, or coordination
   work and attaches normal exact-SHA evidence.
3. If the work reveals reusable verified knowledge, the Agent adds one or more
   typed candidates to that evidence.
4. The Delivery Lead aggregates candidate references on the parent and closes
   the delivery without waiting for curation.
5. The Curator scans `pending` completed or blocked parents in deterministic
   oldest-first order, rereads authoritative evidence, and processes at most
   one candidate.
6. The Curator validates schema, source identity, exact SHA, sensitivity,
   novelty, scope, freshness, and duplication.
7. A valid candidate creates exactly one knowledge Issue in the target Project
   using a deterministic action key. The candidate becomes `issue_created`,
   and the parent summary becomes `dispatched`, only after an authoritative
   reread confirms the Issue.
8. On its target-repository assignment, the Curator creates a documentation-
   only change, verifies indexes, links, content digests, and allowed paths,
   scans for secrets, and opens a pull request.
9. A repository owner or Independent Reviewer reviews the pull request. The
   Curator cannot approve or merge it.
10. After merge is observed and the canonical index verifies at the merged
    SHA, the candidate becomes `verified` and future Agents may retrieve it.

The candidate state machine is:

```text
none
  -> pending
  -> validating
       -> deduplicated
       -> rejected
       -> needs_human
       -> issue_created
            -> pr_open
                 -> rejected
                 -> merged
                      -> verified
```

The parent knowledge summary has its own smaller state machine:

```text
none
  or pending -> dispatched
```

`dispatched` means the target knowledge Issue has been created and verified;
it is not a claim that a pull request exists or that knowledge has been
accepted.

Every transition records its source state, target state, candidate digest,
action key, authoritative object identity, and evidence reference. Identical
retries are no-ops. A transition from a stale state is rejected.

## Concurrency and idempotency

The deterministic action key includes candidate digest, target Project,
repository identity, knowledge type, and source parent. Before every mutation,
the Curator performs a stable reread of parent metadata, summary evidence, and
existing target objects. After an ambiguous acknowledgement, it rereads rather
than retrying blindly.

Concurrent scheduled and manual runs may plan the same candidate, but only one
may create the authoritative knowledge Issue or pull request. The loser
observes the existing action key and returns an idempotent no-op. A candidate
cannot be marked `issue_created`, `pr_open`, or `verified` until the referenced
object has been reread and matches its expected repository, source digest, and
state.

## Safety and failure handling

- A malformed candidate is rejected or moved to `needs_human`; it never creates
  a pull request.
- A duplicate links to the existing active knowledge entry and becomes
  `deduplicated`.
- Missing evidence, unavailable exact SHA, contradictory scope, or unverified
  claims become `needs_human`.
- Sensitive content is rejected without copying the raw value into logs,
  comments, Issues, or pull requests.
- A planned diff outside `AGENTS.md`, `docs/agent-knowledge/**`, or the
  frontend-only `docs/delivery-knowledge/**` allowlist aborts before commit or
  pull-request creation.
- A document that conflicts with current code is corrected or rejected; the
  code is not changed to make the document true.
- A pull-request conflict blocks curation. The Curator does not force-push over
  other changes.
- A rejected or closed pull request records `rejected` and is not recreated
  without new evidence or explicit human authorization.
- Obsolete knowledge is marked `deprecated` with a replacement link when
  applicable; history is not silently deleted.
- Broken required knowledge fails closed for the affected task. Broken optional
  knowledge produces a visible warning and candidate for repair.
- An unavailable Curator or paused schedule leaves delivery unaffected and
  preserves pending candidates for a later run.
- The existing Watcher behavior and authority remain unchanged.

## Testing strategy

### Unit and contract tests

Automated tests cover:

- exact serialization and validation for receipts, candidates, evidence
  references, index entries, and curation decisions;
- repository-, path-, and task-scoped retrieval;
- required versus optional knowledge failure behavior;
- candidate digest stability and source SHA, Issue, and comment validation;
- index uniqueness, path containment, digest, deprecation, and replacement
  rules;
- duplicate detection and deterministic action keys;
- sensitivity filtering and log redaction;
- documentation path allowlists;
- at-most-one candidate per Curator run;
- frontend, backend, and cross-repository routing;
- state-transition preconditions, idempotent retries, and stale-state rejection;
- ambiguous acknowledgement rereads; and
- documents-only pull-request enforcement.

### Provisioning and audit tests

Tests prove:

- the Squad still contains exactly the existing five delivery roles;
- Watcher and Curator are operational Agents outside the Squad;
- neither operational Agent receives backend environment values;
- existing Watcher Autopilot and trigger identities and behavior are preserved;
- Curator creation and update are exact-name scoped and idempotent;
- duplicate live or desired automations fail before mutation;
- the daily Curator trigger has the intended timezone and expression;
- unrelated Agents, Projects, Squads, Autopilots, and triggers remain untouched;
  and
- a second complete apply produces zero mutations.

### Pilot scenarios

The Eventra trial must demonstrate:

1. A normal frontend change with no reusable discovery creates no knowledge
   Issue or pull request.
2. A reusable backend incident finding creates a backend knowledge Issue and
   backend documentation-only pull request.
3. An API contract or integration-sequencing finding creates one canonical
   shared delivery knowledge change.
4. Repeated equivalent candidates link to one existing entry.
5. Simultaneous scheduled and manual runs create no duplicate Issue or pull
   request.
6. A proposed knowledge change containing a business-code edit is rejected
   before pull-request creation.
7. A stale knowledge entry is reported, corrected through a separate pull
   request, and deprecated or superseded without hiding history.
8. Pausing the Curator leaves ordinary Eventra delivery, Watcher recovery,
   gates, merge, and deployment behavior unchanged.

## Rollout

Rollout is incremental and dry-run-first:

1. Add seed knowledge directories, schemas, and instructions with the Curator
   disabled.
2. Enable read-only retrieval and receipts, and inspect their relevance and
   prompt cost on real tasks.
3. Enable candidate capture while leaving curation read-only.
4. Provision and verify the Curator Agent and daily Autopilot without allowing
   apply.
5. Enable bounded knowledge Issue creation and inspect routing and
   deduplication.
6. Enable documentation-only pull-request creation, still requiring human
   review and merge.
7. Run the pilot scenarios and collect measures before considering generic
   promotion.

Useful pilot measures include receipt relevance, average retrieved knowledge
count, candidate acceptance rate, duplicate rate, human review correction
rate, stale-entry rate, time from candidate to reviewed knowledge, and any
business-delivery delay attributable to the loop. The expected business
delivery delay is zero because curation is asynchronous.

## Rollback

The immediate rollback is to pause the Curator Autopilot. Pending candidates
and already reviewed repository knowledge remain visible and recoverable, while
business delivery continues unchanged.

If code rollback is required, remove Curator reconciliation and candidate
production while preserving the existing Watcher identity and configuration.
Static knowledge files may remain as ordinary documentation, or be reverted by
a separately reviewed Git change. No rollback path deletes Issues, comments,
pull requests, or knowledge history automatically.

## Implementation boundaries

Implementation will be planned in six independently verifiable increments:

1. static repository and shared knowledge structure;
2. typed contracts, index verification, and read-only retrieval;
3. role instructions, receipts, and candidate evidence;
4. Curator role plus multi-operational-automation provisioning and audit;
5. bounded knowledge Issue and documentation-only pull-request creation; and
6. end-to-end pilot validation, observability, and rollback exercise.

Each increment must preserve a green existing `tools.multica` test suite and
must not implement or depend on changes to the generic
`multica-multi-repo-delivery` package. Promotion to that package is a separate
architectural task informed by pilot evidence.
