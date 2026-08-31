"""Deterministic Eventra workflow transitions over the Multica CLI boundary."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, Sequence
from urllib.parse import urlsplit

from .issue_contracts import (
    ACTIVE_RUN_STATUSES,
    parse_authorizing_comment,
    parse_issue_children,
    parse_issue_detail,
    parse_issue_list,
    parse_issue_metadata,
    parse_issue_runs,
)
from .provision import MulticaRunner


PHASE_KINDS = frozenset({"implementation", "review", "qa", "repair", "smoke"})
PHASE_RESULTS = frozenset({"pass", "fail", "blocked"})
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
ISSUE_KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*\Z")
PR_PATTERN = re.compile(
    r"https://github\.com/codeExploreHub/(?:Eventra|Eventra-Backend)/pull/[1-9][0-9]*\Z"
)
CONTROLLED_PHASE_KEYS = frozenset(
    {
        "eventra.workflow.version",
        "eventra.phase.kind",
        "eventra.phase.result",
        "eventra.phase.attempt",
        "eventra.phase.evidence_comment",
        "eventra.phase.evidence_comment_url",
        "eventra.phase.sha.frontend",
        "eventra.phase.sha.backend",
        "eventra.phase.pr",
        "eventra.phase.failure_repositories",
    }
)
REPAIR_RESERVATION_KEY = "eventra.workflow.repair_reservation"
REPAIR_AUTHORIZATION_KEY = "eventra.workflow.repair_authorization_comment"
REPAIR_AUTHORIZATION_CONSUMED_KEY = (
    "eventra.workflow.repair_authorization_consumed"
)
MAX_REPAIR_RESERVATION_BYTES = 16_384
MAX_REPAIR_DESCRIPTION_BYTES = 16_384
MAX_REPAIR_TITLE_BYTES = 255
REPAIR_ASSIGNEES = {
    "frontend": "Eventra Frontend Engineer",
    "backend": "Eventra Backend Engineer",
}
REPAIR_PROVENANCE_KEYS = frozenset(
    {
        "eventra.repair.creation_action",
        "eventra.repair.failure_bundle_digest",
        "eventra.repair.failure_evidence_uuids",
        "eventra.repair.authorizing_comment_uuid",
        "eventra.repair.repository",
        "eventra.repair.pull_request",
        "eventra.repair.round",
        "eventra.repair.source_candidates",
    }
)


@dataclass(frozen=True)
class PhaseCompletion:
    kind: Literal["implementation", "review", "qa", "repair", "smoke"]
    result: Literal["pass", "fail", "blocked"]
    attempt: int
    evidence_comment: str
    frontend_sha: str | None
    backend_sha: str | None
    pr_url: str | None
    responsible_repositories: tuple[str, ...] = ()
    evidence_comment_url: str | None = None


@dataclass(frozen=True)
class PhaseResult:
    issue_id: str
    issue_key: str
    status: str
    kind: str
    result: str
    mutation_count: int


@dataclass(frozen=True)
class ParentCompletionResult:
    issue_id: str
    issue_key: str
    status: str
    mutation_count: int


@dataclass(frozen=True)
class PhaseSnapshot:
    issue_key: str
    stage: int
    kind: str
    result: str | None
    attempt: int
    status: str
    frontend_sha: str | None
    backend_sha: str | None
    evidence_comment: str = ""
    responsible_repositories: tuple[str, ...] = ()
    evidence_comment_url: str | None = None
    project_id: str = ""
    creation_action: str = ""
    failure_bundle_digest: str = ""
    failure_evidence_uuids: tuple[str, ...] = ()
    authorizing_comment_uuid: str = ""
    repair_repository: str = ""
    repair_pull_request: str = ""
    repair_round: int = 0
    repair_source_candidates: tuple[tuple[str, str], ...] = ()
    pr_url: str = ""
    assignee_id: str = ""
    assignee_type: str = "agent"
    workflow_version: int = 2


@dataclass(frozen=True)
class PullRequestSnapshot:
    repository: str
    url: str
    head_sha: str
    state: str
    mergeable: bool
    checks_pass: bool


@dataclass(frozen=True)
class AuthorizingComment:
    comment_uuid: str
    author_type: str
    content: str


@dataclass(frozen=True)
class ParentSnapshot:
    identifier: str
    classification: str
    attempt: int
    last_action: str | None
    merge_state: str
    candidate_frontend_sha: str | None
    candidate_backend_sha: str | None
    children: tuple[PhaseSnapshot, ...]
    pull_requests: tuple[PullRequestSnapshot, ...]
    workflow_version: int = 2
    parent_status: str = "in_review"
    next_stage: int = 1
    authorization_comment_uuid: str = ""
    consumed_authorization_uuid: str = ""
    authorizing_comment: AuthorizingComment | None = None
    repair_reservation: dict[str, object] | None = None
    parent_id: str = ""


@dataclass(frozen=True)
class ParentDecision:
    kind: Literal[
        "noop",
        "create_gate_stage",
        "create_repair_stage",
        "merge",
        "create_smoke_stage",
        "complete_parent",
        "block_parent",
    ]
    action_key: str | None
    reason: str
    failure_bundle: dict[str, object] | None = None


@dataclass(frozen=True)
class RepairExecutionResult:
    parent_identifier: str
    next_action: Literal["repair", "noop", "block"]
    reason: str
    action_key: str
    mutation_count: int
    child_identifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChildRunSnapshot:
    issue_id: str
    identifier: str
    stage: int
    issue_status: str
    latest_run_status: str | None
    latest_run_activity_at: str | None
    has_active_run: bool
    has_phase_completion: bool


@dataclass(frozen=True)
class WorkflowSnapshot:
    parent_issue_id: str
    parent_identifier: str
    has_human_approval_wait: bool
    has_malformed_state: bool
    latest_stage_finished: bool
    has_later_parent_run: bool
    active_parent_has_no_executable_successor: bool
    children: tuple[ChildRunSnapshot, ...]
    workflow_version: int = 2
    current_stage: int | None = None

    def first_terminal_run_needing_transition(
        self,
    ) -> ChildRunSnapshot | None:
        candidates = sorted(
            (
                child
                for child in self.children
                if child.stage == self.current_stage
                and child.latest_run_status in {"completed", "failed"}
                and child.issue_status in {"todo", "in_progress", "in_review"}
                and not child.has_active_run
            ),
            key=lambda child: (
                child.latest_run_activity_at or "",
                child.identifier,
            ),
        )
        return candidates[0] if candidates else None


@dataclass(frozen=True)
class RecoveryDecision:
    kind: Literal["noop", "rerun_child", "rerun_parent"]
    issue_key: str | None
    reason: str


@dataclass(frozen=True)
class RecoveryResult:
    decision: RecoveryDecision
    mutation_count: int


@dataclass(frozen=True)
class WatchResult:
    scanned: int
    candidates: int
    applied: int
    decision: str
    reason: str = ""


class GitHubRunner:
    """Strict non-shelling JSON boundary for read-only GitHub PR state."""

    def run(self, args: list[str]) -> dict[str, object]:
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise TypeError("GitHub arguments must be a list of strings")
        try:
            completed = subprocess.run(
                ["gh", *args],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("GitHub command timed out") from None
        except OSError:
            raise RuntimeError("GitHub command could not be started") from None
        if completed.returncode != 0:
            raise RuntimeError(
                f"GitHub command failed with exit {completed.returncode}"
            )
        try:
            value = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError):
            raise RuntimeError("GitHub returned an invalid JSON response") from None
        if not isinstance(value, dict):
            raise RuntimeError("GitHub returned an invalid JSON response")
        return value


def _invalid_completion() -> None:
    raise ValueError("invalid phase completion")


def _validated_sha(value: str) -> str:
    if not isinstance(value, str) or SHA_PATTERN.fullmatch(value) is None:
        _invalid_completion()
    return value


def _validated_pr_url(value: str) -> str:
    if not isinstance(value, str) or PR_PATTERN.fullmatch(value) is None:
        _invalid_completion()
    return value


def _is_canonical_evidence_url(value: object) -> bool:
    if type(value) is not str:
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and bool(parsed.path)
    )


def _valid_phase_ownership(
    kind: str,
    result: str | None,
    phase_repositories: set[str],
    responsible_repositories: tuple[str, ...],
    evidence_comment_url: str | None,
) -> bool:
    owners = set(responsible_repositories)
    if kind not in {"review", "qa"}:
        return not owners and evidence_comment_url is None
    if result == "pass":
        return not owners and evidence_comment_url is None
    if result not in {"fail", "blocked"}:
        return not owners and evidence_comment_url is None
    if (
        not owners
        or not owners <= phase_repositories
        or not _is_canonical_evidence_url(evidence_comment_url)
    ):
        return False
    return len(phase_repositories) > 1 or owners == phase_repositories


def build_phase_metadata(value: PhaseCompletion) -> dict[str, str]:
    """Validate one phase envelope and build explicitly string-valued metadata."""

    if (
        not isinstance(value, PhaseCompletion)
        or value.kind not in PHASE_KINDS
        or value.result not in PHASE_RESULTS
        or not isinstance(value.attempt, int)
        or isinstance(value.attempt, bool)
        or not 0 <= value.attempt <= 3
        or type(value.responsible_repositories) is not tuple
        or any(
            type(repository) is not str
            or repository not in {"frontend", "backend"}
            for repository in value.responsible_repositories
        )
        or len(set(value.responsible_repositories))
        != len(value.responsible_repositories)
    ):
        _invalid_completion()
    try:
        if str(uuid.UUID(value.evidence_comment)) != value.evidence_comment:
            _invalid_completion()
    except (AttributeError, TypeError, ValueError):
        _invalid_completion()
    if value.frontend_sha is None and value.backend_sha is None:
        _invalid_completion()
    if value.kind in {"implementation", "repair"} and value.pr_url is None:
        _invalid_completion()
    if value.kind in {"implementation", "repair"}:
        if (value.frontend_sha is None) == (value.backend_sha is None):
            _invalid_completion()
        pr_url = _validated_pr_url(value.pr_url)
        if (
            "/codeExploreHub/Eventra/pull/" in pr_url
            and value.frontend_sha is None
        ) or (
            "/codeExploreHub/Eventra-Backend/pull/" in pr_url
            and value.backend_sha is None
        ):
            _invalid_completion()

    phase_repositories = {
        repository
        for repository, sha in (
            ("frontend", value.frontend_sha),
            ("backend", value.backend_sha),
        )
        if sha is not None
    }
    if not _valid_phase_ownership(
        value.kind,
        value.result,
        phase_repositories,
        value.responsible_repositories,
        value.evidence_comment_url,
    ):
        _invalid_completion()

    result = {
        "eventra.workflow.version": "2",
        "eventra.phase.kind": value.kind,
        "eventra.phase.result": value.result,
        "eventra.phase.attempt": str(value.attempt),
        "eventra.phase.evidence_comment": value.evidence_comment,
        "eventra.phase.failure_repositories": json.dumps(
            sorted(value.responsible_repositories),
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    if value.evidence_comment_url is not None:
        result["eventra.phase.evidence_comment_url"] = value.evidence_comment_url
    if value.frontend_sha is not None:
        result["eventra.phase.sha.frontend"] = _validated_sha(value.frontend_sha)
    if value.backend_sha is not None:
        result["eventra.phase.sha.backend"] = _validated_sha(value.backend_sha)
    if value.pr_url is not None:
        result["eventra.phase.pr"] = _validated_pr_url(value.pr_url)
    return result


def _scope(snapshot: ParentSnapshot) -> str:
    return {
        "frontend-only": "frontend",
        "backend-only": "backend",
        "cross-stack": "cross-stack",
    }.get(snapshot.classification, "invalid")


def _action_key(
    snapshot: ParentSnapshot,
    kind: str,
    attempt: int,
    bundle_digest: str | None = None,
    authorizing_comment_uuid: str | None = None,
    source_stage: int | None = None,
) -> str:
    return ":".join(
        (
            "2",
            snapshot.identifier,
            kind,
            str(attempt),
            _scope(snapshot),
            snapshot.candidate_frontend_sha or "-",
            snapshot.candidate_backend_sha or "-",
            "next-stage",
            str(snapshot.next_stage),
            *(("source-stage", str(source_stage)) if source_stage is not None else ()),
            *(("bundle", bundle_digest) if bundle_digest is not None else ()),
            *(
                ("authorization", authorizing_comment_uuid)
                if authorizing_comment_uuid is not None
                else ()
            ),
        )
    )


def _repair_action_stage_identity(value: str) -> tuple[int, int]:
    parts = value.split(":") if type(value) is str else []
    if (
        len(parts) not in {13, 15}
        or parts[0] != "2"
        or ISSUE_KEY_PATTERN.fullmatch(parts[1]) is None
        or parts[2] != "create_repair_stage"
        or parts[3] not in {"1", "2", "3"}
        or parts[4] not in {"frontend", "backend", "cross-stack"}
        or any(
            candidate != "-" and SHA_PATTERN.fullmatch(candidate) is None
            for candidate in parts[5:7]
        )
        or parts[7] != "next-stage"
        or not parts[8].isdigit()
        or str(int(parts[8])) != parts[8]
        or int(parts[8]) < 1
        or parts[9] != "source-stage"
        or not parts[10].isdigit()
        or str(int(parts[10])) != parts[10]
        or int(parts[10]) < 1
        or parts[11] != "bundle"
        or re.fullmatch(r"[0-9a-f]{64}", parts[12]) is None
        or (
            len(parts) == 15
            and (parts[13] != "authorization" or not _is_uuid(parts[14]))
        )
    ):
        raise RuntimeError("malformed repair action identity")
    return int(parts[8]), int(parts[10])


def _repair_action_source_candidates(value: str) -> dict[str, str]:
    _repair_action_stage_identity(value)
    parts = value.split(":")
    candidates = {
        repository: candidate
        for repository, candidate in zip(
            ("frontend", "backend"),
            parts[5:7],
            strict=True,
        )
        if candidate != "-"
    }
    expected = {
        "frontend": {"frontend"},
        "backend": {"backend"},
        "cross-stack": {"frontend", "backend"},
    }[parts[4]]
    if set(candidates) != expected:
        raise RuntimeError("malformed repair action source candidates")
    return dict(sorted(candidates.items()))


def _decode_source_candidates(value: str) -> dict[str, str]:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        raise RuntimeError("malformed repair source candidates") from None
    if (
        not isinstance(decoded, dict)
        or not decoded
        or value != _canonical_json(decoded)
        or set(decoded) - set(REPAIR_ASSIGNEES)
        or any(
            type(repository) is not str
            or type(sha) is not str
            or SHA_PATTERN.fullmatch(sha) is None
            for repository, sha in decoded.items()
        )
    ):
        raise RuntimeError("malformed repair source candidates")
    return dict(sorted(decoded.items()))


def _snapshot_with_candidates(
    snapshot: ParentSnapshot,
    candidates: dict[str, str],
) -> ParentSnapshot:
    return replace(
        snapshot,
        candidate_frontend_sha=candidates.get("frontend"),
        candidate_backend_sha=candidates.get("backend"),
    )


def _parent_decision(
    snapshot: ParentSnapshot,
    kind: str,
    reason: str,
    *,
    attempt: int | None = None,
    failure_bundle: dict[str, object] | None = None,
    authorizing_comment_uuid: str | None = None,
) -> ParentDecision:
    if kind == "noop":
        return ParentDecision("noop", None, reason, failure_bundle)
    bundle_digest = (
        None if failure_bundle is None else str(failure_bundle["digest"])
    )
    key = _action_key(
        snapshot,
        kind,
        snapshot.attempt if attempt is None else attempt,
        bundle_digest,
        authorizing_comment_uuid,
        (
            None
            if failure_bundle is None
            else int(failure_bundle["source_stage_ordinal"])
        ),
    )
    if snapshot.last_action == key:
        return ParentDecision("noop", None, "coordinator action already recorded")
    return ParentDecision(kind, key, reason, failure_bundle)


def _phase_shas_match(snapshot: ParentSnapshot, phases: tuple[PhaseSnapshot, ...]) -> bool:
    for phase_value in phases:
        if (
            phase_value.frontend_sha is not None
            and phase_value.frontend_sha != snapshot.candidate_frontend_sha
        ) or (
            phase_value.backend_sha is not None
            and phase_value.backend_sha != snapshot.candidate_backend_sha
        ):
            return False
    return True


def _expected_repositories(snapshot: ParentSnapshot) -> set[str]:
    return {
        repository
        for repository, sha in (
            ("frontend", snapshot.candidate_frontend_sha),
            ("backend", snapshot.candidate_backend_sha),
        )
        if sha is not None
    }


def _work_repository_coverage(
    snapshot: ParentSnapshot,
    phases: tuple[PhaseSnapshot, ...],
    *,
    require_full_scope: bool,
) -> bool:
    expected = _expected_repositories(snapshot)
    observed: list[str] = []
    for item in phases:
        repositories = {
            repository
            for repository, sha in (
                ("frontend", item.frontend_sha),
                ("backend", item.backend_sha),
            )
            if sha is not None
        }
        if len(repositories) != 1 or item.attempt != snapshot.attempt:
            return False
        observed.extend(repositories)
    if not observed or len(observed) != len(set(observed)):
        return False
    if require_full_scope:
        return set(observed) == expected
    return set(observed) <= expected


def _attempt_history_is_consistent(snapshot: ParentSnapshot) -> bool:
    completed = tuple(item for item in snapshot.children if item.status == "done")
    if not completed:
        return snapshot.attempt == 0
    if max(item.attempt for item in completed) != snapshot.attempt:
        return False
    stage_attempts: dict[int, set[int]] = {}
    for item in completed:
        stage_attempts.setdefault(item.stage, set()).add(item.attempt)
    if any(len(attempts) != 1 for attempts in stage_attempts.values()):
        return False
    repair_attempts = {
        item.attempt
        for item in completed
        if item.kind == "repair"
    }
    return repair_attempts == set(range(1, snapshot.attempt + 1))


def _gate_coverage(snapshot: ParentSnapshot, phases: tuple[PhaseSnapshot, ...]) -> bool:
    if {item.kind for item in phases} != {"review", "qa"}:
        return False
    expected = _expected_repositories(snapshot)
    return all(
        {
            repository
            for item in phases
            if item.kind == kind
            for repository, sha in (
                ("frontend", item.frontend_sha),
                ("backend", item.backend_sha),
            )
            if sha is not None
        }
        == expected
        for kind in ("review", "qa")
    )


def _pull_requests_ready(snapshot: ParentSnapshot) -> bool:
    expected = {
        repository: sha
        for repository, sha in (
            ("frontend", snapshot.candidate_frontend_sha),
            ("backend", snapshot.candidate_backend_sha),
        )
        if sha is not None
    }
    if len(snapshot.pull_requests) != len(expected):
        return False
    observed = {item.repository: item for item in snapshot.pull_requests}
    return set(observed) == set(expected) and all(
        item.head_sha == expected[repository]
        and item.state == "open"
        and item.mergeable is True
        and item.checks_pass is True
        for repository, item in observed.items()
    )


def _candidate_sha_map(snapshot: ParentSnapshot) -> dict[str, str]:
    return dict(
        sorted(
            (
                (repository, sha)
                for repository, sha in (
                    ("frontend", snapshot.candidate_frontend_sha),
                    ("backend", snapshot.candidate_backend_sha),
                )
                if sha is not None
            )
        )
    )


def _failure_bundle(
    snapshot: ParentSnapshot,
    phases: tuple[PhaseSnapshot, ...],
) -> dict[str, object]:
    if not phases or snapshot.workflow_version != 2:
        raise ValueError("failure bundle requires version 2 gate evidence")
    stage = phases[0].stage
    candidates = _candidate_sha_map(snapshot)
    failures: list[dict[str, object]] = []
    evidence_uuids: set[str] = set()
    for phase in phases:
        if phase.stage != stage or phase.attempt != snapshot.attempt:
            raise ValueError("failure bundle gate identity is inconsistent")
        phase_candidates = {
            repository: sha
            for repository, sha in (
                ("frontend", phase.frontend_sha),
                ("backend", phase.backend_sha),
            )
            if sha is not None
        }
        owners = phase.responsible_repositories
        if (
            phase.kind not in {"review", "qa"}
            or type(owners) is not tuple
            or len(set(owners)) != len(owners)
            or any(repository not in phase_candidates for repository in owners)
            or not _is_uuid(phase.evidence_comment)
            or phase.evidence_comment in evidence_uuids
            or not _valid_phase_ownership(
                phase.kind,
                phase.result,
                set(phase_candidates),
                phase.responsible_repositories,
                phase.evidence_comment_url,
            )
        ):
            raise ValueError("failure bundle gate evidence is malformed")
        evidence_uuids.add(phase.evidence_comment)
        if phase.result == "pass":
            if owners:
                raise ValueError("passing gate cannot declare failure ownership")
            continue
        if phase.result not in {"fail", "blocked"} or not owners:
            raise ValueError("nonpassing gate requires failure ownership")
        if not _is_canonical_evidence_url(phase.evidence_comment_url):
            raise ValueError("nonpassing gate requires canonical evidence URL")
        failures.append(
            {
                "candidate_shas": candidates,
                "child_identifier": phase.issue_key,
                "evidence_comment_url": phase.evidence_comment_url,
                "evidence_comment_uuid": phase.evidence_comment,
                "phase": phase.kind,
                "repair_round": snapshot.attempt,
                "responsible_repositories": list(sorted(owners)),
                "result": phase.result,
                "stage_ordinal": stage,
                "suite_key": "",
            }
        )
    if not failures:
        raise ValueError("failure bundle requires nonpassing gate evidence")
    failures.sort(
        key=lambda failure: (
            tuple(failure["responsible_repositories"]),
            failure["phase"],
            failure["suite_key"],
            failure["child_identifier"],
            failure["evidence_comment_uuid"],
        )
    )
    payload: dict[str, object] = {
        "candidate_shas": candidates,
        "failures": failures,
        "parent_identifier": snapshot.identifier,
        "repair_round": snapshot.attempt + 1,
        "source_stage_ordinal": stage,
        "workflow_version": 2,
    }
    payload["digest"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return payload


def _repair_authorization_matches(
    snapshot: ParentSnapshot,
    bundle: dict[str, object],
) -> bool:
    comment_uuid = snapshot.authorization_comment_uuid
    comment = snapshot.authorizing_comment
    if (
        not _is_uuid(comment_uuid)
        or bool(snapshot.consumed_authorization_uuid)
        or not isinstance(comment, AuthorizingComment)
        or comment.comment_uuid != comment_uuid
        or comment.author_type != "member"
    ):
        return False
    try:
        content = json.loads(comment.content)
    except (json.JSONDecodeError, TypeError):
        return False
    return (
        isinstance(content, dict)
        and set(content) == {"bundle_digest", "granted_round"}
        and content.get("bundle_digest") == bundle.get("digest")
        and type(content.get("granted_round")) is int
        and content["granted_round"] == 3
        and bundle.get("repair_round") == 3
        and comment.content
        == json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


def _repair_or_block(
    snapshot: ParentSnapshot,
    phases: tuple[PhaseSnapshot, ...] = (),
) -> ParentDecision:
    if snapshot.attempt > 2:
        return _parent_decision(
            snapshot,
            "block_parent",
            "member-authorized repair round is already consumed",
        )
    if not phases:
        return ParentDecision(
            "block_parent",
            None,
            "repair requires one exact source gate Stage",
        )
    source_stages = {phase.stage for phase in phases}
    if len(source_stages) != 1 or snapshot.next_stage != next(iter(source_stages)) + 1:
        return ParentDecision(
            "block_parent",
            None,
            "next Stage must immediately follow the source gate Stage",
        )
    bundle = None
    try:
        bundle = _failure_bundle(snapshot, phases)
    except ValueError:
        return _parent_decision(
            snapshot,
            "block_parent",
            "terminal gate failure evidence is malformed",
        )
    authorizing_comment_uuid = None
    if snapshot.attempt == 2:
        if bundle is None or not _repair_authorization_matches(snapshot, bundle):
            return _parent_decision(
                snapshot,
                "block_parent",
                "automatic attempt limit exhausted without exact member authorization",
            )
        authorizing_comment_uuid = snapshot.authorization_comment_uuid
    return _parent_decision(
        snapshot,
        "create_repair_stage",
        "current exact-SHA gate set did not pass",
        attempt=snapshot.attempt + 1,
        failure_bundle=bundle,
        authorizing_comment_uuid=authorizing_comment_uuid,
    )


def _current_repair_provenance_problem(
    snapshot: ParentSnapshot,
    phases: tuple[PhaseSnapshot, ...],
) -> str | None:
    if not phases or {item.kind for item in phases} != {"repair"}:
        return "current repair Stage membership is malformed"
    stage = phases[0].stage
    if (
        any(item.stage != stage for item in phases)
        or stage != snapshot.next_stage - 1
        or snapshot.last_action is None
    ):
        return "current repair Stage identity is not authoritative"
    rounds = {item.attempt for item in phases}
    creation_actions = {item.creation_action for item in phases}
    digests = {item.failure_bundle_digest for item in phases}
    authorizations = {item.authorizing_comment_uuid for item in phases}
    source_candidate_sets = {
        item.repair_source_candidates for item in phases
    }
    if (
        rounds != {snapshot.attempt}
        or snapshot.attempt not in {1, 2, 3}
        or creation_actions != {snapshot.last_action}
        or len(digests) != 1
        or re.fullmatch(r"[0-9a-f]{64}", next(iter(digests), "")) is None
        or len(authorizations) != 1
        or len(source_candidate_sets) != 1
        or not next(iter(source_candidate_sets), ())
    ):
        return "current repair provenance is incomplete or conflicting"
    authorization_uuid = next(iter(authorizations))
    if snapshot.attempt == 3:
        if (
            not _is_uuid(authorization_uuid)
            or authorization_uuid != snapshot.consumed_authorization_uuid
        ):
            return "current repair authorization provenance is invalid"
    elif authorization_uuid:
        return "automatic repair provenance carries an authorization"
    try:
        action_next_stage, source_stage = _repair_action_stage_identity(
            snapshot.last_action
        )
        action_source_candidates = _repair_action_source_candidates(
            snapshot.last_action
        )
    except RuntimeError:
        return "current repair creation action is malformed"
    source_candidates = dict(next(iter(source_candidate_sets)))
    if action_source_candidates != source_candidates:
        return "current repair source candidate provenance is conflicting"
    if action_next_stage != stage or source_stage != stage - 1:
        return "current repair creation action has the wrong Stage identity"
    source_phases = tuple(
        item for item in snapshot.children if item.stage == source_stage
    )
    source_snapshot = _snapshot_with_candidates(
        replace(
            snapshot,
            attempt=snapshot.attempt - 1,
            next_stage=stage,
            last_action=None,
            authorization_comment_uuid="",
            authorizing_comment=None,
            repair_reservation=None,
        ),
        source_candidates,
    )
    try:
        if (
            not source_phases
            or any(item.status != "done" for item in source_phases)
            or not _gate_coverage(source_snapshot, source_phases)
            or not _phase_shas_match(source_snapshot, source_phases)
        ):
            return "current repair source gate membership is incomplete"
        bundle = _failure_bundle(source_snapshot, source_phases)
        expected_action = _action_key(
            source_snapshot,
            "create_repair_stage",
            snapshot.attempt,
            str(bundle["digest"]),
            authorization_uuid or None,
            int(bundle["source_stage_ordinal"]),
        )
        specs = _repair_child_specs(source_snapshot, bundle)
    except (RuntimeError, ValueError, TypeError):
        return "current repair source bundle cannot be reconstructed"
    digest = str(bundle["digest"])
    if expected_action != snapshot.last_action or digests != {digest}:
        return "current repair bundle or creation action is not canonical"
    expected_specs = {
        str(spec["repository"]): spec for spec in specs
    }
    observed: dict[str, PhaseSnapshot] = {}
    replacement_candidates = dict(source_candidates)
    all_repairs_passed = True
    for item in phases:
        repository = item.repair_repository
        if repository in observed or repository not in expected_specs:
            return "current repair child multiset is incomplete or conflicting"
        spec = expected_specs[repository]
        phase_repositories = {
            name
            for name, sha in (
                ("frontend", item.frontend_sha),
                ("backend", item.backend_sha),
            )
            if sha is not None
        }
        if (
            item.workflow_version != 2
            or item.repair_round != snapshot.attempt
            or dict(item.repair_source_candidates) != source_candidates
            or phase_repositories != {repository}
            or item.pr_url != spec["pull_request"]
            or item.repair_pull_request != spec["pull_request"]
            or item.project_id != spec["project_id"]
            or item.assignee_id != spec["assignee_id"]
            or item.assignee_type != "agent"
            or item.failure_evidence_uuids
            != tuple(spec["evidence_uuids"])
            or not item.failure_evidence_uuids
        ):
            return "current repair child provenance is incomplete or conflicting"
        phase_sha = (
            item.frontend_sha
            if repository == "frontend"
            else item.backend_sha
        )
        if item.status == "done" and item.result == "pass":
            if phase_sha == source_candidates[repository]:
                return "completed repair PASS did not produce a replacement SHA"
            replacement_candidates[repository] = str(phase_sha)
        else:
            all_repairs_passed = False
            if phase_sha != source_candidates[repository]:
                return "nonpassing or active repair changed its seeded source SHA"
        observed[repository] = item
    if set(observed) != set(expected_specs):
        return "current repair child multiset is incomplete or conflicting"
    parent_candidates = _candidate_sha_map(snapshot)
    observed_heads = {
        item.repository: item.head_sha for item in snapshot.pull_requests
    }
    if (
        len(snapshot.pull_requests) != len(source_candidates)
        or set(observed_heads) != set(source_candidates)
    ):
        return "current managed pull-request identity set is incomplete"
    allowed_partial_parent_values: dict[str, set[str]] = {
        repository: {source_sha}
        for repository, source_sha in source_candidates.items()
    }
    for repository, source_sha in source_candidates.items():
        item = observed.get(repository)
        head_sha = observed_heads[repository]
        if item is None:
            if head_sha != source_sha:
                return "unaffected managed pull-request head changed during repair"
            continue
        phase_sha = (
            item.frontend_sha
            if repository == "frontend"
            else item.backend_sha
        )
        if item.status == "done" and item.result == "pass":
            if head_sha != phase_sha:
                return "completed repair replacement does not match its current head"
            allowed_partial_parent_values[repository].add(str(phase_sha))
        elif item.status == "done" and head_sha != source_sha:
            return "nonpassing repair changed its managed pull-request head"
    if all_repairs_passed:
        if observed_heads != replacement_candidates:
            return "current managed pull-request heads do not match completed repair replacements"
        if parent_candidates == source_candidates:
            return "completed repair replacement awaits parent candidate metadata copy"
        if parent_candidates != replacement_candidates:
            return "parent candidate metadata does not match completed repair replacements"
    else:
        if set(parent_candidates) != set(source_candidates) or any(
            parent_candidates[repository] not in allowed_values
            for repository, allowed_values in allowed_partial_parent_values.items()
        ):
            return "parent candidate metadata changed before its repair completed"
    return None


def decide_parent_action(snapshot: ParentSnapshot) -> ParentDecision:
    """Return one deterministic coordinator action without mutating state."""

    if (
        not isinstance(snapshot, ParentSnapshot)
        or _scope(snapshot) == "invalid"
        or snapshot.attempt < 0
        or snapshot.merge_state not in {"not_ready", "ready", "merged", "partial"}
    ):
        return ParentDecision("block_parent", None, "malformed parent workflow state")
    if snapshot.workflow_version == 1:
        if snapshot.parent_status in {"done", "blocked", "cancelled"}:
            return ParentDecision(
                "noop",
                None,
                "terminal version 1 workflow is read-only",
            )
        return ParentDecision(
            "block_parent",
            None,
            "version 1 workflow requires explicit migration",
        )
    if snapshot.workflow_version != 2:
        return ParentDecision("block_parent", None, "malformed parent workflow state")
    if snapshot.repair_reservation is not None:
        return _parent_decision(
            snapshot,
            "block_parent",
            "repair reservation requires exact executor reconciliation",
        )
    if snapshot.merge_state == "partial":
        return _parent_decision(
            snapshot,
            "block_parent",
            "cross-repository merge is partial",
        )
    if snapshot.children:
        latest_stage = max(item.stage for item in snapshot.children)
        latest = tuple(item for item in snapshot.children if item.stage == latest_stage)
    else:
        latest = ()
    current_repair_stage = bool(
        latest and {item.kind for item in latest} == {"repair"}
    )
    if current_repair_stage:
        repair_problem = _current_repair_provenance_problem(snapshot, latest)
        if repair_problem is not None:
            return ParentDecision("block_parent", None, repair_problem)
    expected_heads = {
        "frontend": snapshot.candidate_frontend_sha,
        "backend": snapshot.candidate_backend_sha,
    }
    if not current_repair_stage and any(
        pull_request.head_sha != expected_heads.get(pull_request.repository)
        for pull_request in snapshot.pull_requests
    ):
        return _parent_decision(
            snapshot,
            "block_parent",
            "out-of-band pull-request head change",
        )

    if snapshot.merge_state == "merged":
        if not _attempt_history_is_consistent(snapshot):
            return _parent_decision(
                snapshot,
                "block_parent",
                "parent attempt conflicts with completed child history",
            )
        if latest and {item.kind for item in latest} == {"smoke"}:
            if any(item.status != "done" for item in latest):
                return ParentDecision("noop", None, "smoke stage is still active")
            if all(item.result == "pass" for item in latest):
                return _parent_decision(
                    snapshot,
                    "complete_parent",
                    "merged local smoke passed",
                )
            return _repair_or_block(snapshot)
        return _parent_decision(
            snapshot,
            "create_smoke_stage",
            "merged candidates require local smoke",
        )

    if not latest:
        return ParentDecision("noop", None, "parent has no completed stage")
    if any(item.status != "done" for item in latest):
        return ParentDecision("noop", None, "latest stage is still active")
    if not _attempt_history_is_consistent(snapshot):
        return _parent_decision(
            snapshot,
            "block_parent",
            "parent attempt conflicts with completed child history",
        )
    if any(item.result not in PHASE_RESULTS for item in latest):
        return _parent_decision(
            snapshot,
            "block_parent",
            "terminal phase evidence is malformed",
        )

    kinds = {item.kind for item in latest}
    if kinds <= {"implementation", "repair"}:
        if kinds not in ({"implementation"}, {"repair"}):
            return _parent_decision(
                snapshot,
                "block_parent",
                "latest stage mixes implementation and repair phases",
            )
        if not all(item.result == "pass" for item in latest):
            return _repair_or_block(snapshot)
        if not _phase_shas_match(snapshot, latest):
            return _parent_decision(
                snapshot,
                "block_parent",
                "implementation evidence does not match current candidates",
            )
        if not _work_repository_coverage(
            snapshot,
            latest,
            require_full_scope=kinds == {"implementation"},
        ):
            return _parent_decision(
                snapshot,
                "block_parent",
                "implementation or repair repository coverage is invalid",
            )
        return _parent_decision(
            snapshot,
            "create_gate_stage",
            "implementation evidence is ready for exact-SHA gates",
        )

    if kinds <= {"review", "qa"}:
        if not _phase_shas_match(snapshot, latest):
            return _parent_decision(
                snapshot,
                "create_gate_stage",
                "candidate SHA changed after the latest gate set",
            )
        if not _gate_coverage(snapshot, latest):
            return _parent_decision(
                snapshot,
                "block_parent",
                "required exact-SHA gate coverage is incomplete",
            )
        if not all(item.result == "pass" for item in latest):
            return _repair_or_block(snapshot, latest)
        if not _pull_requests_ready(snapshot):
            return _parent_decision(
                snapshot,
                "block_parent",
                "current pull request state is not merge-ready",
            )
        return _parent_decision(
            snapshot,
            "merge",
            "all current exact-SHA gates and merge checks passed",
        )

    return _parent_decision(
        snapshot,
        "block_parent",
        "latest stage mixes incompatible phase kinds",
    )


def decide_recovery(snapshot: WorkflowSnapshot) -> RecoveryDecision:
    """Choose at most one safe stalled-work rerun."""

    if snapshot.workflow_version == 1:
        return RecoveryDecision(
            "noop",
            None,
            "version 1 workflow requires explicit migration",
        )
    if snapshot.workflow_version != 2:
        return RecoveryDecision("noop", None, "state is not auto-recoverable")
    if snapshot.has_human_approval_wait:
        return RecoveryDecision("noop", None, "state is not auto-recoverable")
    if (
        type(snapshot.current_stage) is not int
        or snapshot.current_stage < 0
    ):
        return RecoveryDecision(
            "noop",
            None,
            "authoritative current Stage is malformed",
        )
    current_children = tuple(
        child
        for child in snapshot.children
        if child.stage == snapshot.current_stage
    )
    if snapshot.children and not current_children:
        return RecoveryDecision(
            "noop",
            None,
            "authoritative current Stage membership is missing",
        )
    if snapshot.has_malformed_state:
        return RecoveryDecision(
            "noop",
            None,
            "authoritative current Stage membership is malformed",
        )
    stalled_child = snapshot.first_terminal_run_needing_transition()
    if stalled_child is not None:
        return RecoveryDecision(
            "rerun_child",
            stalled_child.identifier,
            "terminal run without terminal phase transition",
        )
    unstarted_child = next(
        (
            child
            for child in sorted(
                current_children,
                key=lambda item: (item.stage, item.identifier),
            )
            if child.issue_status in {"todo", "in_progress", "in_review"}
            and child.latest_run_status is None
            and not child.has_active_run
        ),
        None,
    )
    if unstarted_child is not None:
        return RecoveryDecision(
            "rerun_child",
            unstarted_child.identifier,
            "nonterminal assigned child without an active run",
        )
    if snapshot.latest_stage_finished and not snapshot.has_later_parent_run:
        return RecoveryDecision(
            "rerun_parent",
            snapshot.parent_identifier,
            "finished stage without successor parent run",
        )
    if snapshot.active_parent_has_no_executable_successor:
        return RecoveryDecision(
            "rerun_parent",
            snapshot.parent_identifier,
            "active parent without executable successor",
        )
    return RecoveryDecision("noop", None, "workflow has an active successor")


def recover_once(runner: MulticaRunner, snapshot_loader) -> RecoveryResult:
    """Reread one workflow, rerun once, and require a fresh active task."""

    initial = decide_recovery(snapshot_loader())
    if initial.kind == "noop":
        return RecoveryResult(initial, 0)
    fresh = decide_recovery(snapshot_loader())
    if fresh != initial:
        return RecoveryResult(
            RecoveryDecision("noop", None, "workflow changed before recovery"),
            0,
        )
    issue_key = fresh.issue_key
    if issue_key is None:
        raise RuntimeError("malformed recovery decision")
    detail = parse_issue_detail(
        runner.run(["issue", "get", issue_key, "--output", "json"]),
        issue_key,
    )
    issue_id = str(detail["id"])
    before = parse_issue_runs(
        runner.run(["issue", "runs", issue_key, "--output", "json"]),
        issue_id,
    )
    if any(item["status"] in ACTIVE_RUN_STATUSES for item in before):
        return RecoveryResult(
            RecoveryDecision(
                "noop",
                None,
                "active run appeared before recovery mutation",
            ),
            0,
        )
    before_ids = {item["id"] for item in before}
    runner.run(["issue", "rerun", issue_key, "--output", "json"])
    after = parse_issue_runs(
        runner.run(["issue", "runs", issue_key, "--output", "json"]),
        issue_id,
    )
    if not any(
        item["id"] not in before_ids and item["status"] in ACTIVE_RUN_STATUSES
        for item in after
    ):
        raise RuntimeError("recovery verification failed")
    return RecoveryResult(fresh, 1)


def _parent_metadata(value: dict[str, str]) -> dict[str, object]:
    version = value.get("eventra.workflow.version")
    classification = value.get("eventra.workflow.classification")
    next_stage = value.get("eventra.workflow.next_stage")
    attempt = value.get("eventra.workflow.attempt")
    merge_state = value.get("eventra.workflow.merge_state")
    last_action = value.get("eventra.workflow.last_action")
    frontend_sha = value.get("eventra.workflow.frontend_sha")
    backend_sha = value.get("eventra.workflow.backend_sha")
    authorization_comment_uuid = value.get(REPAIR_AUTHORIZATION_KEY, "")
    consumed_authorization_uuid = value.get(
        REPAIR_AUTHORIZATION_CONSUMED_KEY,
        "",
    )
    reservation_text = value.get(REPAIR_RESERVATION_KEY)
    repair_reservation = (
        None
        if reservation_text is None
        else _decode_repair_reservation(reservation_text)
    )
    if (
        version not in {"1", "2"}
        or classification not in {"frontend-only", "backend-only", "cross-stack"}
        or not isinstance(next_stage, str)
        or not next_stage.isdigit()
        or int(next_stage) < 1
        or not isinstance(attempt, str)
        or not attempt.isdigit()
        or merge_state not in {"not_ready", "ready", "merged", "partial"}
        or not isinstance(last_action, str)
        or (frontend_sha is not None and SHA_PATTERN.fullmatch(frontend_sha) is None)
        or (backend_sha is not None and SHA_PATTERN.fullmatch(backend_sha) is None)
        or (classification == "frontend-only" and (frontend_sha is None or backend_sha is not None))
        or (classification == "backend-only" and (backend_sha is None or frontend_sha is not None))
        or (classification == "cross-stack" and (frontend_sha is None or backend_sha is None))
        or (
            authorization_comment_uuid != ""
            and not _is_uuid(authorization_comment_uuid)
        )
        or (
            consumed_authorization_uuid != ""
            and not _is_uuid(consumed_authorization_uuid)
        )
    ):
        raise RuntimeError("malformed parent workflow metadata")
    return {
        "workflow_version": int(version),
        "classification": classification,
        "attempt": int(attempt),
        "merge_state": merge_state,
        "last_action": last_action or None,
        "frontend_sha": frontend_sha,
        "backend_sha": backend_sha,
        "next_stage": int(next_stage),
        "authorization_comment_uuid": authorization_comment_uuid,
        "consumed_authorization_uuid": consumed_authorization_uuid,
        "repair_reservation": repair_reservation,
    }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode_repair_reservation(value: str) -> dict[str, object]:
    if type(value) is not str or len(value.encode("utf-8")) > MAX_REPAIR_RESERVATION_BYTES:
        raise RuntimeError("malformed repair reservation")
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise RuntimeError("malformed repair reservation") from None
    if (
        not isinstance(decoded, dict)
        or value != _canonical_json(decoded)
        or set(decoded)
        != {
            "action_key",
            "authorizing_comment_uuid",
            "failure_bundle",
            "next_stage",
            "parent_identifier",
            "previous_last_action",
            "prior_consumed_authorization_uuid",
            "repair_round",
            "source_attempt",
            "source_candidates",
            "child_specs",
        }
    ):
        raise RuntimeError("malformed repair reservation")
    action_key = decoded["action_key"]
    authorization_uuid = decoded["authorizing_comment_uuid"]
    bundle = decoded["failure_bundle"]
    next_stage = decoded["next_stage"]
    parent_identifier = decoded["parent_identifier"]
    previous_last_action = decoded["previous_last_action"]
    prior_consumed = decoded["prior_consumed_authorization_uuid"]
    repair_round = decoded["repair_round"]
    source_attempt = decoded["source_attempt"]
    source_candidates = decoded["source_candidates"]
    specs = decoded["child_specs"]
    if (
        type(action_key) is not str
        or not action_key
        or type(parent_identifier) is not str
        or ISSUE_KEY_PATTERN.fullmatch(parent_identifier) is None
        or type(previous_last_action) is not str
        or type(authorization_uuid) is not str
        or type(prior_consumed) is not str
        or type(next_stage) is not int
        or next_stage < 1
        or type(source_attempt) is not int
        or source_attempt not in {0, 1, 2}
        or type(repair_round) is not int
        or repair_round != source_attempt + 1
        or repair_round not in {1, 2, 3}
        or (repair_round == 3) != bool(authorization_uuid)
        or (authorization_uuid and not _is_uuid(authorization_uuid))
        or (prior_consumed and not _is_uuid(prior_consumed))
        or not isinstance(bundle, dict)
        or not isinstance(source_candidates, dict)
        or not isinstance(specs, list)
        or not specs
    ):
        raise RuntimeError("malformed repair reservation")
    digest = bundle.get("digest")
    source_stage = bundle.get("source_stage_ordinal")
    try:
        action_source_candidates = _repair_action_source_candidates(action_key)
        decoded_source_candidates = _decode_source_candidates(
            _canonical_json(source_candidates)
        )
    except RuntimeError:
        raise RuntimeError("malformed repair reservation") from None
    if (
        type(digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or bundle.get("parent_identifier") != parent_identifier
        or bundle.get("repair_round") != repair_round
        or type(source_stage) is not int
        or source_stage < 1
        or bundle.get("candidate_shas") != decoded_source_candidates
        or decoded_source_candidates != action_source_candidates
    ):
        raise RuntimeError("malformed repair reservation")
    try:
        action_next_stage, action_source_stage = _repair_action_stage_identity(
            action_key
        )
    except RuntimeError:
        raise RuntimeError("malformed repair reservation") from None
    if (
        next_stage != source_stage + 1
        or action_next_stage != next_stage
        or action_source_stage != source_stage
    ):
        raise RuntimeError("malformed repair reservation")
    digest_payload = dict(bundle)
    digest_payload.pop("digest", None)
    observed_digest = hashlib.sha256(
        _canonical_json(digest_payload).encode("utf-8")
    ).hexdigest()
    if observed_digest != digest:
        raise RuntimeError("malformed repair reservation")
    repositories: list[str] = []
    for spec in specs:
        if not isinstance(spec, dict) or set(spec) != {
            "assignee_id",
            "candidate_sha",
            "evidence_uuids",
            "project_id",
            "pull_request",
            "repository",
        }:
            raise RuntimeError("malformed repair reservation")
        repository = spec["repository"]
        evidence_uuids = spec["evidence_uuids"]
        if (
            repository not in REPAIR_ASSIGNEES
            or type(spec["assignee_id"]) is not str
            or not _is_uuid(spec["assignee_id"])
            or type(spec["candidate_sha"]) is not str
            or SHA_PATTERN.fullmatch(spec["candidate_sha"]) is None
            or spec["candidate_sha"] != decoded_source_candidates.get(repository)
            or type(spec["project_id"]) is not str
            or not _is_uuid(spec["project_id"])
            or type(spec["pull_request"]) is not str
            or _repository_for_pr(spec["pull_request"]) != repository
            or not isinstance(evidence_uuids, list)
            or not evidence_uuids
            or evidence_uuids != sorted(evidence_uuids)
            or len(evidence_uuids) != len(set(evidence_uuids))
            or any(not _is_uuid(item) for item in evidence_uuids)
        ):
            raise RuntimeError("malformed repair reservation")
        repositories.append(repository)
    if repositories != sorted(repositories) or len(repositories) != len(set(repositories)):
        raise RuntimeError("malformed repair reservation")
    return decoded


def _phase_snapshot(
    issue: dict[str, object],
    metadata: dict[str, str],
) -> PhaseSnapshot:
    kind = metadata.get("eventra.phase.kind", "unknown")
    result = metadata.get("eventra.phase.result")
    attempt_text = metadata.get("eventra.phase.attempt", "0")
    frontend_sha = metadata.get("eventra.phase.sha.frontend")
    backend_sha = metadata.get("eventra.phase.sha.backend")
    version = metadata.get("eventra.workflow.version")
    evidence_comment = metadata.get("eventra.phase.evidence_comment", "")
    evidence_comment_url = metadata.get("eventra.phase.evidence_comment_url")
    phase_pr_url = metadata.get("eventra.phase.pr", "")
    failure_repositories = metadata.get("eventra.phase.failure_repositories")
    responsible_repositories: tuple[str, ...] = ()
    creation_action = metadata.get("eventra.repair.creation_action", "")
    failure_bundle_digest = metadata.get(
        "eventra.repair.failure_bundle_digest",
        "",
    )
    authorizing_comment_uuid = metadata.get(
        "eventra.repair.authorizing_comment_uuid",
        "",
    )
    repair_repository = metadata.get("eventra.repair.repository", "")
    repair_pull_request = metadata.get("eventra.repair.pull_request", "")
    repair_round_text = metadata.get("eventra.repair.round", "0")
    source_candidates_text = metadata.get(
        "eventra.repair.source_candidates",
        "{}",
    )
    evidence_uuids_text = metadata.get(
        "eventra.repair.failure_evidence_uuids",
        "[]",
    )
    failure_evidence_uuids: tuple[str, ...] = ()
    repair_source_candidates: tuple[tuple[str, str], ...] = ()
    if version == "2":
        try:
            decoded_repositories = json.loads(failure_repositories)
        except (json.JSONDecodeError, TypeError):
            raise RuntimeError("malformed child phase metadata") from None
        if (
            not isinstance(decoded_repositories, list)
            or any(
                type(repository) is not str
                or repository not in {"frontend", "backend"}
                for repository in decoded_repositories
            )
            or len(set(decoded_repositories)) != len(decoded_repositories)
            or failure_repositories
            != json.dumps(
                decoded_repositories,
                sort_keys=True,
                separators=(",", ":"),
            )
            or decoded_repositories != sorted(decoded_repositories)
        ):
            raise RuntimeError("malformed child phase metadata")
        responsible_repositories = tuple(decoded_repositories)
    provenance_present = bool(REPAIR_PROVENANCE_KEYS & set(metadata))
    if provenance_present:
        try:
            decoded_evidence_uuids = json.loads(evidence_uuids_text)
            decoded_source_candidates = _decode_source_candidates(
                source_candidates_text
            )
            action_source_candidates = _repair_action_source_candidates(
                creation_action
            )
        except (json.JSONDecodeError, TypeError, RuntimeError):
            raise RuntimeError("malformed child repair provenance") from None
        if (
            not REPAIR_PROVENANCE_KEYS <= set(metadata)
            or kind != "repair"
            or not creation_action
            or re.fullmatch(r"[0-9a-f]{64}", failure_bundle_digest) is None
            or not isinstance(decoded_evidence_uuids, list)
            or not decoded_evidence_uuids
            or decoded_evidence_uuids != sorted(decoded_evidence_uuids)
            or len(decoded_evidence_uuids) != len(set(decoded_evidence_uuids))
            or any(not _is_uuid(item) for item in decoded_evidence_uuids)
            or evidence_uuids_text != _canonical_json(decoded_evidence_uuids)
            or (
                authorizing_comment_uuid
                and not _is_uuid(authorizing_comment_uuid)
            )
            or repair_repository not in REPAIR_ASSIGNEES
            or repair_repository not in decoded_source_candidates
            or decoded_source_candidates != action_source_candidates
            or _repository_for_pr(repair_pull_request) != repair_repository
            or not repair_round_text.isdigit()
            or int(repair_round_text) != int(attempt_text)
        ):
            raise RuntimeError("malformed child repair provenance")
        failure_evidence_uuids = tuple(decoded_evidence_uuids)
        repair_source_candidates = tuple(decoded_source_candidates.items())
    if (
        version not in {"1", "2"}
        or (kind != "unknown" and kind not in PHASE_KINDS)
        or (result is not None and result not in PHASE_RESULTS)
        or not attempt_text.isdigit()
        or (version == "2" and int(attempt_text) > 3)
        or (frontend_sha is not None and SHA_PATTERN.fullmatch(frontend_sha) is None)
        or (backend_sha is not None and SHA_PATTERN.fullmatch(backend_sha) is None)
        or (result is not None and not _is_uuid(evidence_comment))
    ):
        raise RuntimeError("malformed child phase metadata")
    phase_repositories = {
        repository
        for repository, sha in (
            ("frontend", frontend_sha),
            ("backend", backend_sha),
        )
        if sha is not None
    }
    if version == "2" and not _valid_phase_ownership(
        kind,
        result,
        phase_repositories,
        responsible_repositories,
        evidence_comment_url,
    ):
        raise RuntimeError("malformed child phase metadata")
    completed = _has_phase_completion(metadata)
    return PhaseSnapshot(
        issue_key=str(issue["identifier"]),
        stage=int(issue["stage"]),
        kind=kind,
        result=result if completed else None,
        attempt=int(attempt_text),
        status=str(issue["status"]),
        frontend_sha=frontend_sha,
        backend_sha=backend_sha,
        evidence_comment=evidence_comment,
        responsible_repositories=responsible_repositories,
        evidence_comment_url=evidence_comment_url,
        project_id=str(issue["project_id"]),
        creation_action=creation_action,
        failure_bundle_digest=failure_bundle_digest,
        failure_evidence_uuids=failure_evidence_uuids,
        authorizing_comment_uuid=authorizing_comment_uuid,
        repair_repository=repair_repository,
        repair_pull_request=repair_pull_request,
        repair_round=int(repair_round_text),
        repair_source_candidates=repair_source_candidates,
        pr_url=phase_pr_url,
        assignee_id=str(issue["assignee_id"]),
        assignee_type=str(issue["assignee_type"]),
        workflow_version=int(version),
    )


def _repository_for_pr(value: str) -> str:
    _validated_pr_url(value)
    if "/codeExploreHub/Eventra-Backend/pull/" in value:
        return "backend"
    return "frontend"


def _parse_pull_request(
    value: object,
    expected_url: str,
    repository: str,
) -> PullRequestSnapshot:
    if not isinstance(value, dict) or set(value) != {
        "url",
        "headRefOid",
        "state",
        "mergeable",
        "mergeStateStatus",
        "statusCheckRollup",
    }:
        raise RuntimeError("malformed GitHub pull request")
    if value.get("url") != expected_url:
        raise RuntimeError("malformed GitHub pull request")
    head_sha = value.get("headRefOid")
    state = value.get("state")
    mergeable = value.get("mergeable")
    merge_state_status = value.get("mergeStateStatus")
    checks = value.get("statusCheckRollup")
    if (
        not isinstance(head_sha, str)
        or SHA_PATTERN.fullmatch(head_sha) is None
        or state not in {"OPEN", "CLOSED", "MERGED"}
        or mergeable not in {"MERGEABLE", "CONFLICTING", "UNKNOWN"}
        or merge_state_status not in {
            "BEHIND",
            "BLOCKED",
            "CLEAN",
            "DIRTY",
            "DRAFT",
            "HAS_HOOKS",
            "UNKNOWN",
            "UNSTABLE",
        }
        or not isinstance(checks, list)
        or not all(isinstance(item, dict) for item in checks)
    ):
        raise RuntimeError("malformed GitHub pull request")
    conclusions = [item.get("conclusion") for item in checks]
    if any(item is not None and not isinstance(item, str) for item in conclusions):
        raise RuntimeError("malformed GitHub pull request")
    checks_pass = merge_state_status == "CLEAN" and all(
        conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"}
        for conclusion in conclusions
    )
    return PullRequestSnapshot(
        repository=repository,
        url=expected_url,
        head_sha=head_sha,
        state=state.lower(),
        mergeable=mergeable == "MERGEABLE",
        checks_pass=checks_pass,
    )


def load_parent_snapshot(
    runner: MulticaRunner,
    github: GitHubRunner,
    parent_key: str,
) -> ParentSnapshot:
    """Read parent phase metadata and current GitHub heads without mutation."""

    parent = parse_issue_detail(
        runner.run(["issue", "get", parent_key, "--output", "json"]),
        parent_key,
    )
    if parent["parent_issue_id"] is not None:
        raise RuntimeError("parent planning requires a parent issue")
    metadata = _parent_metadata(
        parse_issue_metadata(
            runner.run(
                ["issue", "metadata", "list", parent_key, "--output", "json"]
            )
        )
    )
    authorizing_comment = None
    authorization_comment_uuid = str(metadata["authorization_comment_uuid"])
    if authorization_comment_uuid:
        raw_comment = parse_authorizing_comment(
            runner.run(
                [
                    "issue",
                    "comment",
                    "list",
                    parent_key,
                    "--thread",
                    authorization_comment_uuid,
                    "--full",
                    "--compact",
                    "--output",
                    "json",
                ]
            ),
            authorization_comment_uuid,
        )
        authorizing_comment = AuthorizingComment(
            comment_uuid=raw_comment["comment_uuid"],
            author_type=raw_comment["author_type"],
            content=raw_comment["content"],
        )
    children = parse_issue_children(
        runner.run(["issue", "children", parent_key, "--output", "json"]),
        str(parent["id"]),
    )
    phases: list[PhaseSnapshot] = []
    pr_candidates: dict[str, tuple[int, str]] = {}
    for child in children:
        if child["stage"] is None:
            continue
        child_metadata = parse_issue_metadata(
            runner.run(
                [
                    "issue", "metadata", "list", str(child["identifier"]),
                    "--output", "json",
                ]
            )
        )
        phases.append(_phase_snapshot(child, child_metadata))
        pr_url = child_metadata.get("eventra.phase.pr")
        if pr_url is not None:
            repository = _repository_for_pr(pr_url)
            candidate = (int(child["stage"]), pr_url)
            previous = pr_candidates.get(repository)
            if previous is None or candidate[0] > previous[0]:
                pr_candidates[repository] = candidate
            elif candidate[0] == previous[0] and candidate[1] != previous[1]:
                raise RuntimeError("conflicting phase pull requests")

    pull_requests = []
    for repository, (_, url) in sorted(pr_candidates.items()):
        raw = github.run(
            [
                "pr", "view", url,
                "--json",
                "url,headRefOid,state,mergeable,mergeStateStatus,statusCheckRollup",
            ]
        )
        pull_requests.append(_parse_pull_request(raw, url, repository))
    return ParentSnapshot(
        identifier=parent_key,
        classification=str(metadata["classification"]),
        attempt=int(metadata["attempt"]),
        last_action=metadata["last_action"],
        merge_state=str(metadata["merge_state"]),
        candidate_frontend_sha=metadata["frontend_sha"],
        candidate_backend_sha=metadata["backend_sha"],
        children=tuple(phases),
        pull_requests=tuple(pull_requests),
        workflow_version=int(metadata["workflow_version"]),
        parent_status=str(parent["status"]),
        next_stage=int(metadata["next_stage"]),
        authorization_comment_uuid=authorization_comment_uuid,
        consumed_authorization_uuid=str(
            metadata["consumed_authorization_uuid"]
        ),
        authorizing_comment=authorizing_comment,
        repair_reservation=metadata["repair_reservation"],
        parent_id=str(parent["id"]),
    )


def _repair_child_specs(
    snapshot: ParentSnapshot,
    bundle: dict[str, object],
) -> list[dict[str, object]]:
    failures = bundle.get("failures")
    candidates = bundle.get("candidate_shas")
    if not isinstance(failures, list) or not isinstance(candidates, dict):
        raise RuntimeError("malformed failure bundle")
    owners = sorted(
        {
            repository
            for failure in failures
            if isinstance(failure, dict)
            for repository in failure.get("responsible_repositories", [])
        }
    )
    pull_requests = {item.repository: item.url for item in snapshot.pull_requests}
    specs: list[dict[str, object]] = []
    for repository in owners:
        if repository not in REPAIR_ASSIGNEES:
            raise RuntimeError("failure bundle has an invalid repair owner")
        project_ids = {
            item.project_id
            for item in snapshot.children
            if item.project_id
            and item.kind in {"implementation", "repair"}
            and item.pr_url
            and _repository_for_pr(item.pr_url) == repository
        }
        assignee_ids = {
            item.assignee_id
            for item in snapshot.children
            if item.assignee_type == "agent"
            and item.assignee_id
            and item.kind in {"implementation", "repair"}
            and item.pr_url
            and _repository_for_pr(item.pr_url) == repository
        }
        evidence_uuids = sorted(
            {
                str(failure["evidence_comment_uuid"])
                for failure in failures
                if isinstance(failure, dict)
                and repository in failure.get("responsible_repositories", [])
                and _is_uuid(failure.get("evidence_comment_uuid"))
            }
        )
        candidate_sha = candidates.get(repository)
        pull_request = pull_requests.get(repository)
        if (
            len(project_ids) != 1
            or len(assignee_ids) != 1
            or type(candidate_sha) is not str
            or SHA_PATTERN.fullmatch(candidate_sha) is None
            or type(pull_request) is not str
            or _repository_for_pr(pull_request) != repository
            or not evidence_uuids
        ):
            raise RuntimeError("repair owner routing is not authoritative")
        specs.append(
            {
                "assignee_id": next(iter(assignee_ids)),
                "candidate_sha": candidate_sha,
                "evidence_uuids": evidence_uuids,
                "project_id": next(iter(project_ids)),
                "pull_request": pull_request,
                "repository": repository,
            }
        )
    if not specs:
        raise RuntimeError("failure bundle has no repair owner")
    return specs


def _build_repair_reservation(
    snapshot: ParentSnapshot,
    decision: ParentDecision,
) -> dict[str, object]:
    bundle = decision.failure_bundle
    if decision.kind != "create_repair_stage" or decision.action_key is None:
        raise RuntimeError("repair execution requires an exact repair decision")
    if not isinstance(bundle, dict):
        raise RuntimeError("repair execution requires a failure bundle")
    repair_round = bundle.get("repair_round")
    authorization_uuid = (
        snapshot.authorization_comment_uuid if repair_round == 3 else ""
    )
    reservation: dict[str, object] = {
        "action_key": decision.action_key,
        "authorizing_comment_uuid": authorization_uuid,
        "child_specs": _repair_child_specs(snapshot, bundle),
        "failure_bundle": bundle,
        "next_stage": snapshot.next_stage,
        "parent_identifier": snapshot.identifier,
        "previous_last_action": snapshot.last_action or "",
        "prior_consumed_authorization_uuid": (
            snapshot.consumed_authorization_uuid
        ),
        "repair_round": repair_round,
        "source_attempt": snapshot.attempt,
        "source_candidates": dict(bundle["candidate_shas"]),
    }
    encoded = _canonical_json(reservation)
    if len(encoded.encode("utf-8")) > MAX_REPAIR_RESERVATION_BYTES:
        raise RuntimeError("repair reservation exceeds the local metadata limit")
    return _decode_repair_reservation(encoded)


def _validate_repair_reservation(
    snapshot: ParentSnapshot,
    reservation: dict[str, object],
    expected_action_key: str,
    *,
    committed_children: dict[str, PhaseSnapshot] | None = None,
) -> None:
    if (
        reservation["action_key"] != expected_action_key
        or reservation["parent_identifier"] != snapshot.identifier
    ):
        raise RuntimeError("repair reservation conflicts with expected action")
    source_attempt = int(reservation["source_attempt"])
    repair_round = int(reservation["repair_round"])
    next_stage = int(reservation["next_stage"])
    previous_last_action = str(reservation["previous_last_action"])
    authorization_uuid = str(reservation["authorizing_comment_uuid"])
    prior_consumed = str(reservation["prior_consumed_authorization_uuid"])
    source_candidates = dict(reservation["source_candidates"])
    bundle = reservation["failure_bundle"]
    if not isinstance(bundle, dict):
        raise RuntimeError("malformed repair reservation")
    source_stage = bundle.get("source_stage_ordinal")
    if type(source_stage) is not int or next_stage != source_stage + 1:
        raise RuntimeError("repair reservation Stage identity conflicts")
    if (
        snapshot.attempt not in {source_attempt, repair_round}
        or snapshot.next_stage not in {next_stage, next_stage + 1}
        or (snapshot.last_action or "")
        not in {previous_last_action, expected_action_key}
        or snapshot.consumed_authorization_uuid
        not in {prior_consumed, authorization_uuid}
    ):
        raise RuntimeError("parent state conflicts with repair reservation")
    source_phases = tuple(
        item for item in snapshot.children if item.stage == source_stage
    )
    source_snapshot = _snapshot_with_candidates(
        replace(
            snapshot,
            attempt=source_attempt,
            next_stage=next_stage,
            repair_reservation=None,
        ),
        source_candidates,
    )
    try:
        fresh_bundle = _failure_bundle(source_snapshot, source_phases)
    except ValueError:
        raise RuntimeError("reserved failure evidence no longer matches") from None
    if fresh_bundle != bundle:
        raise RuntimeError("reserved failure bundle no longer matches")
    computed_key = _action_key(
        source_snapshot,
        "create_repair_stage",
        repair_round,
        str(bundle["digest"]),
        authorization_uuid or None,
        int(source_stage),
    )
    if computed_key != expected_action_key:
        raise RuntimeError("reserved repair action identity no longer matches")
    if _repair_child_specs(source_snapshot, bundle) != reservation["child_specs"]:
        raise RuntimeError("reserved repair routing no longer matches")
    if repair_round == 3:
        if snapshot.attempt == source_attempt or (
            snapshot.consumed_authorization_uuid == prior_consumed
        ):
            if not _repair_authorization_matches(source_snapshot, bundle):
                raise RuntimeError("reserved repair authorization no longer matches")
        elif snapshot.consumed_authorization_uuid != authorization_uuid:
            raise RuntimeError("reserved repair authorization was not consumed")
    elif authorization_uuid:
        raise RuntimeError("automatic repair cannot carry authorization")
    expected_heads = dict(source_candidates)
    allowed_parent_candidates = (source_candidates,)
    if committed_children is not None:
        expected_repositories = {
            str(spec["repository"]) for spec in reservation["child_specs"]
        }
        if set(committed_children) != expected_repositories:
            raise RuntimeError("recorded repair child multiset changed")
        all_repairs_passed = True
        for repository, child in committed_children.items():
            if child.status == "done" and child.result == "pass":
                replacement_sha = (
                    child.frontend_sha
                    if repository == "frontend"
                    else child.backend_sha
                )
                if (
                    replacement_sha is None
                    or replacement_sha == source_candidates[repository]
                ):
                    raise RuntimeError(
                        "recorded repair PASS lacks its exact replacement"
                    )
                expected_heads[repository] = replacement_sha
            else:
                all_repairs_passed = False
        if all_repairs_passed:
            allowed_parent_candidates = (source_candidates, expected_heads)
    if _candidate_sha_map(snapshot) not in allowed_parent_candidates:
        raise RuntimeError("reserved repair parent candidates changed")
    observed_heads = {
        item.repository: item.head_sha for item in snapshot.pull_requests
    }
    if observed_heads != expected_heads:
        raise RuntimeError("pull-request head changed during repair execution")


def _repair_child_metadata(
    reservation: dict[str, object],
    spec: dict[str, object],
) -> dict[str, str]:
    repository = str(spec["repository"])
    result = {
        "eventra.workflow.version": "2",
        "eventra.phase.kind": "repair",
        "eventra.phase.attempt": str(reservation["repair_round"]),
        "eventra.phase.failure_repositories": "[]",
        "eventra.phase.pr": str(spec["pull_request"]),
        "eventra.repair.creation_action": str(reservation["action_key"]),
        "eventra.repair.failure_bundle_digest": str(
            reservation["failure_bundle"]["digest"]
        ),
        "eventra.repair.failure_evidence_uuids": _canonical_json(
            spec["evidence_uuids"]
        ),
        "eventra.repair.authorizing_comment_uuid": str(
            reservation["authorizing_comment_uuid"]
        ),
        "eventra.repair.repository": repository,
        "eventra.repair.pull_request": str(spec["pull_request"]),
        "eventra.repair.round": str(reservation["repair_round"]),
        "eventra.repair.source_candidates": _canonical_json(
            reservation["source_candidates"]
        ),
    }
    result[f"eventra.phase.sha.{repository}"] = str(spec["candidate_sha"])
    return result


def _metadata_set_observed(
    runner: MulticaRunner,
    issue_key: str,
    key: str,
    value: str,
) -> int:
    before = parse_issue_metadata(
        runner.run(["issue", "metadata", "list", issue_key, "--output", "json"])
    )
    if before.get(key) == value:
        return 0
    try:
        runner.run(
            [
                "issue", "metadata", "set", issue_key,
                "--key", key,
                "--value", value,
                "--type", "string",
                "--output", "json",
            ]
        )
    except RuntimeError:
        pass
    after = parse_issue_metadata(
        runner.run(["issue", "metadata", "list", issue_key, "--output", "json"])
    )
    if after.get(key) != value:
        raise RuntimeError("metadata mutation effect was not authoritatively observed")
    return 1


def _metadata_delete_observed(
    runner: MulticaRunner,
    issue_key: str,
    key: str,
) -> int:
    before = parse_issue_metadata(
        runner.run(["issue", "metadata", "list", issue_key, "--output", "json"])
    )
    if key not in before:
        return 0
    try:
        runner.run(
            [
                "issue", "metadata", "delete", issue_key,
                "--key", key,
                "--output", "json",
            ]
        )
    except RuntimeError:
        pass
    after = parse_issue_metadata(
        runner.run(["issue", "metadata", "list", issue_key, "--output", "json"])
    )
    if key in after:
        raise RuntimeError("metadata delete effect was not authoritatively observed")
    return 1


def _repair_child_title(
    reservation: dict[str, object],
    spec: dict[str, object],
) -> str:
    title = (
        f"{reservation['parent_identifier']} {spec['repository']} repair round "
        f"{reservation['repair_round']}"
    )
    if len(title.encode("utf-8")) > MAX_REPAIR_TITLE_BYTES:
        raise RuntimeError("repair child title exceeds the local issue limit")
    return title


def _render_repair_handoff(
    reservation: dict[str, object],
    spec: dict[str, object],
) -> str:
    """Render one complete deterministic repository failure partition."""

    validated = _decode_repair_reservation(_canonical_json(reservation))
    matching_specs = [item for item in validated["child_specs"] if item == spec]
    if len(matching_specs) != 1:
        raise RuntimeError("repair handoff partition does not match its reservation")
    repository = str(spec["repository"])
    bundle = validated["failure_bundle"]
    failures = bundle.get("failures")
    candidates = bundle.get("candidate_shas")
    if not isinstance(failures, list) or not isinstance(candidates, dict):
        raise RuntimeError("repair handoff partition is malformed")
    if (
        bundle.get("workflow_version") != 2
        or not candidates
        or set(candidates) - set(REPAIR_ASSIGNEES)
        or any(
            type(name) is not str
            or type(sha) is not str
            or SHA_PATTERN.fullmatch(sha) is None
            for name, sha in candidates.items()
        )
    ):
        raise RuntimeError("repair handoff candidate identity is malformed")
    if candidates.get(repository) != spec["candidate_sha"]:
        raise RuntimeError("repair handoff rejected candidate is malformed")
    assigned: list[dict[str, object]] = []
    expected_failure_keys = {
        "candidate_shas",
        "child_identifier",
        "evidence_comment_url",
        "evidence_comment_uuid",
        "phase",
        "repair_round",
        "responsible_repositories",
        "result",
        "stage_ordinal",
        "suite_key",
    }
    for failure in failures:
        if not isinstance(failure, dict) or set(failure) != expected_failure_keys:
            raise RuntimeError("repair handoff failure identity is malformed")
        owners = failure["responsible_repositories"]
        if (
            failure["candidate_shas"] != candidates
            or failure["phase"] not in {"review", "qa"}
            or failure["result"] not in {"fail", "blocked"}
            or type(failure["suite_key"]) is not str
            or failure["repair_round"] != validated["source_attempt"]
            or failure["stage_ordinal"] != bundle["source_stage_ordinal"]
            or type(failure["child_identifier"]) is not str
            or ISSUE_KEY_PATTERN.fullmatch(failure["child_identifier"]) is None
            or not _is_uuid(failure["evidence_comment_uuid"])
            or not _is_canonical_evidence_url(failure["evidence_comment_url"])
            or not isinstance(owners, list)
            or any(
                type(owner) is not str or owner not in REPAIR_ASSIGNEES
                for owner in owners
            )
            or owners != sorted(owners)
            or len(owners) != len(set(owners))
            or not owners
        ):
            raise RuntimeError("repair handoff failure identity is malformed")
        if repository in owners:
            assigned.append(failure)
    assigned.sort(
        key=lambda item: (
            {"review": 0, "qa": 1}[str(item["phase"])],
            str(item["suite_key"]),
            str(item["child_identifier"]),
            str(item["evidence_comment_uuid"]),
        )
    )
    evidence_uuids = [str(item["evidence_comment_uuid"]) for item in assigned]
    if not assigned or sorted(evidence_uuids) != spec["evidence_uuids"]:
        raise RuntimeError("repair handoff partition is incomplete")
    source_children = sorted({str(item["child_identifier"]) for item in assigned})
    lines = [
        "# Deterministic repair handoff",
        f"Parent: {validated['parent_identifier']}",
        f"Action: {validated['action_key']}",
        f"Failure bundle: {bundle['digest']}",
        f"Workflow: {bundle['workflow_version']}",
        f"Source stage: {bundle['source_stage_ordinal']}",
        f"Next stage: {validated['next_stage']}",
        f"Repair round: {validated['repair_round']}",
        f"Managed PR: {spec['pull_request']}",
        "Candidate SHAs:",
        *(f"- {name}: {candidates[name]}" for name in sorted(candidates)),
        "Rejected candidates:",
        f"- {repository}: {spec['candidate_sha']}",
        "Source children:",
        *(f"- {identifier}" for identifier in source_children),
        "Assigned failure evidence:",
    ]
    for item in assigned:
        suite = str(item["suite_key"])
        suffix = "" if not suite else f" | suite={suite}"
        lines.append(
            "- "
            f"{item['phase']} | {item['child_identifier']} | {item['result']} | "
            f"{item['evidence_comment_uuid']} | {item['evidence_comment_url']}"
            f"{suffix}"
        )
    rendered = "\n".join(lines) + "\n"
    if len(rendered.encode("utf-8")) > MAX_REPAIR_DESCRIPTION_BYTES:
        raise RuntimeError("repair handoff exceeds the local issue description limit")
    return rendered


def _repair_issue_detail(
    runner: MulticaRunner,
    issue_key: str,
) -> tuple[dict[str, object], str, str]:
    raw = runner.run(["issue", "get", issue_key, "--output", "json"])
    detail = parse_issue_detail(raw, issue_key)
    if not isinstance(raw, dict):
        raise RuntimeError("malformed repair child detail")
    title = raw.get("title")
    description = raw.get("description")
    if type(title) is not str or not title or type(description) is not str or not description:
        raise RuntimeError("malformed repair child detail")
    return detail, title, description


def _repair_children_for_reservation(
    runner: MulticaRunner,
    snapshot: ParentSnapshot,
    reservation: dict[str, object],
) -> dict[str, PhaseSnapshot]:
    stage = int(reservation["next_stage"])
    expected_specs = {
        str(spec["repository"]): spec for spec in reservation["child_specs"]
    }
    observed: dict[str, PhaseSnapshot] = {}
    for child in (item for item in snapshot.children if item.stage == stage):
        repository = child.repair_repository
        spec = expected_specs.get(repository)
        if spec is None or repository in observed:
            raise RuntimeError("reserved repair stage has conflicting children")
        expected_metadata = _repair_child_metadata(reservation, spec)
        metadata = parse_issue_metadata(
            runner.run(
                ["issue", "metadata", "list", child.issue_key, "--output", "json"]
            )
        )
        controlled = {
            key: value
            for key, value in metadata.items()
            if key.startswith("eventra.workflow.")
            or key.startswith("eventra.phase.")
            or key.startswith("eventra.repair.")
        }
        exact_metadata = dict(expected_metadata)
        if child.status == "done":
            for key in ("eventra.phase.result", "eventra.phase.evidence_comment"):
                if key not in metadata:
                    raise RuntimeError("reserved repair child completion is incomplete")
                exact_metadata[key] = metadata[key]
            if metadata["eventra.phase.result"] == "pass":
                replacement_key = f"eventra.phase.sha.{repository}"
                replacement_sha = metadata.get(replacement_key, "")
                if (
                    SHA_PATTERN.fullmatch(replacement_sha) is None
                    or replacement_sha == spec["candidate_sha"]
                ):
                    raise RuntimeError(
                        "reserved repair child PASS lacks a replacement SHA"
                    )
                exact_metadata[replacement_key] = replacement_sha
        detail, title, description = _repair_issue_detail(runner, child.issue_key)
        if (
            controlled != exact_metadata
            or child.workflow_version != 2
            or child.kind != "repair"
            or child.attempt != reservation["repair_round"]
            or child.pr_url != spec["pull_request"]
            or child.repair_pull_request != spec["pull_request"]
            or child.project_id != spec["project_id"]
            or child.assignee_id != spec["assignee_id"]
            or child.assignee_type != "agent"
            or child.status
            not in {"backlog", "todo", "in_progress", "in_review", "done"}
            or detail["parent_issue_id"] != snapshot.parent_id
            or detail["stage"] != stage
            or detail["project_id"] != spec["project_id"]
            or detail["assignee_id"] != spec["assignee_id"]
            or detail["assignee_type"] != "agent"
            or title != _repair_child_title(reservation, spec)
            or description != _render_repair_handoff(reservation, spec)
        ):
            raise RuntimeError("reserved repair child provenance conflicts")
        observed[repository] = child
    return observed


def _create_reserved_repair_children(
    runner: MulticaRunner,
    github: GitHubRunner,
    parent_key: str,
    reservation: dict[str, object],
    observed_effects: list[int],
) -> ParentSnapshot:
    snapshot = load_parent_snapshot(runner, github, parent_key)
    if snapshot.repair_reservation != reservation:
        raise RuntimeError("authoritative repair reservation changed before child creation")
    _validate_repair_reservation(snapshot, reservation, str(reservation["action_key"]))
    observed = _repair_children_for_reservation(runner, snapshot, reservation)
    for spec in reservation["child_specs"]:
        repository = str(spec["repository"])
        if repository in observed:
            continue
        before_children = parse_issue_children(
            runner.run(["issue", "children", parent_key, "--output", "json"]),
            snapshot.parent_id,
        )
        before_ids = {str(item["identifier"]) for item in before_children}
        title = _repair_child_title(reservation, spec)
        description = _render_repair_handoff(reservation, spec)
        try:
            runner.run(
                [
                    "issue", "create",
                    "--parent", parent_key,
                    "--stage", str(reservation["next_stage"]),
                    "--project", str(spec["project_id"]),
                    "--assignee-id", str(spec["assignee_id"]),
                    "--status", "backlog",
                    "--title", title,
                    "--description", description,
                    "--output", "json",
                ]
            )
        except RuntimeError:
            pass
        after_children = parse_issue_children(
            runner.run(["issue", "children", parent_key, "--output", "json"]),
            snapshot.parent_id,
        )
        created = [
            item for item in after_children if str(item["identifier"]) not in before_ids
        ]
        if len(created) != 1:
            raise RuntimeError("repair child creation effect is ambiguous")
        child_key = str(created[0]["identifier"])
        observed_effects[0] += 1
        detail, observed_title, observed_description = _repair_issue_detail(
            runner, child_key
        )
        if (
            detail["parent_issue_id"] != snapshot.parent_id
            or detail["stage"] != reservation["next_stage"]
            or detail["project_id"] != spec["project_id"]
            or detail["assignee_id"] != spec["assignee_id"]
            or detail["assignee_type"] != "agent"
            or detail["status"] != "backlog"
            or observed_title != title
            or observed_description != description
        ):
            raise RuntimeError("repair child creation persistence conflicts")
        for key, value in sorted(_repair_child_metadata(reservation, spec).items()):
            observed_effects[0] += _metadata_set_observed(
                runner, child_key, key, value
            )
        detail, observed_title, observed_description = _repair_issue_detail(
            runner, child_key
        )
        metadata = parse_issue_metadata(
            runner.run(
                ["issue", "metadata", "list", child_key, "--output", "json"]
            )
        )
        expected_metadata = _repair_child_metadata(reservation, spec)
        if (
            detail["parent_issue_id"] is None
            or detail["stage"] != reservation["next_stage"]
            or detail["project_id"] != spec["project_id"]
            or detail["assignee_id"] != spec["assignee_id"]
            or detail["assignee_type"] != "agent"
            or detail["status"] != "backlog"
            or any(metadata.get(key) != value for key, value in expected_metadata.items())
            or observed_title != title
            or observed_description != description
        ):
            raise RuntimeError("repair child persistence verification failed")
    fresh = load_parent_snapshot(runner, github, parent_key)
    if fresh.repair_reservation != reservation:
        raise RuntimeError("authoritative repair reservation changed after child creation")
    _validate_repair_reservation(fresh, reservation, str(reservation["action_key"]))
    children = _repair_children_for_reservation(runner, fresh, reservation)
    if set(children) != {
        str(spec["repository"]) for spec in reservation["child_specs"]
    }:
        raise RuntimeError("repair reservation is missing an owner child")
    return fresh


def _promote_repair_child_observed(
    runner: MulticaRunner,
    child: PhaseSnapshot,
    observed_effects: list[int],
) -> None:
    detail, _, _ = _repair_issue_detail(runner, child.issue_key)
    issue_id = str(detail["id"])
    before_runs = parse_issue_runs(
        runner.run(["issue", "runs", child.issue_key, "--output", "json"]),
        issue_id,
    )
    active_before = [
        item for item in before_runs if item["status"] in ACTIVE_RUN_STATUSES
    ]
    if detail["status"] != "backlog":
        if detail["status"] in {"todo", "in_progress", "in_review"}:
            if len(active_before) != 1:
                raise RuntimeError(
                    "promoted repair child must have exactly one active agent run"
                )
        elif detail["status"] == "done":
            if child.result is None or active_before:
                raise RuntimeError("completed repair child state conflicts")
        else:
            raise RuntimeError("promoted repair child status conflicts")
        return
    if active_before:
        raise RuntimeError("backlog repair child unexpectedly has an active run")
    before_ids = {item["id"] for item in before_runs}
    try:
        runner.run(
            ["issue", "status", child.issue_key, "todo", "--output", "json"]
        )
    except RuntimeError:
        pass
    verified, _, _ = _repair_issue_detail(runner, child.issue_key)
    after_runs = parse_issue_runs(
        runner.run(["issue", "runs", child.issue_key, "--output", "json"]),
        issue_id,
    )
    new_active = [
        item
        for item in after_runs
        if item["id"] not in before_ids and item["status"] in ACTIVE_RUN_STATUSES
    ]
    active_after = [
        item for item in after_runs if item["status"] in ACTIVE_RUN_STATUSES
    ]
    if verified["status"] != "backlog" or any(
        item["id"] not in before_ids for item in after_runs
    ):
        observed_effects[0] += 1
    if len(active_after) > 1:
        raise RuntimeError(
            "promoted repair child must have exactly one active agent run"
        )
    if (
        verified["status"] not in {"todo", "in_progress", "in_review"}
        or len(new_active) != 1
        or len(active_after) != 1
    ):
        raise RuntimeError("repair child promotion effect was not observed")


def _commit_reserved_repair(
    runner: MulticaRunner,
    github: GitHubRunner,
    parent_key: str,
    reservation: dict[str, object],
    observed_effects: list[int],
) -> tuple[str, ...]:
    action_key = str(reservation["action_key"])
    source_attempt = int(reservation["source_attempt"])
    repair_round = int(reservation["repair_round"])
    stage = int(reservation["next_stage"])
    previous_last_action = str(reservation["previous_last_action"])
    authorization_uuid = str(reservation["authorizing_comment_uuid"])
    prior_consumed = str(reservation["prior_consumed_authorization_uuid"])

    raw_metadata = parse_issue_metadata(
        runner.run(["issue", "metadata", "list", parent_key, "--output", "json"])
    )
    if raw_metadata.get(REPAIR_RESERVATION_KEY) != _canonical_json(reservation):
        raise RuntimeError("repair reservation changed before parent commit")
    allowed_values = {
        "eventra.workflow.attempt": {str(source_attempt), str(repair_round)},
        "eventra.workflow.next_stage": {str(stage), str(stage + 1)},
        "eventra.workflow.last_action": {previous_last_action, action_key},
        REPAIR_AUTHORIZATION_CONSUMED_KEY: {
            prior_consumed,
            authorization_uuid,
        },
    }
    for key, allowed in allowed_values.items():
        current = raw_metadata.get(key, "")
        if current not in allowed:
            raise RuntimeError("parent commit conflicts with repair reservation")
    desired = {
        "eventra.workflow.attempt": str(repair_round),
        "eventra.workflow.next_stage": str(stage + 1),
        "eventra.workflow.last_action": action_key,
    }
    if authorization_uuid:
        desired[REPAIR_AUTHORIZATION_CONSUMED_KEY] = authorization_uuid
    for key, value in desired.items():
        if raw_metadata.get(key, "") != value:
            observed_effects[0] += _metadata_set_observed(
                runner, parent_key, key, value
            )
            raw_metadata[key] = value
    if authorization_uuid:
        current_authorization = raw_metadata.get(REPAIR_AUTHORIZATION_KEY)
        if current_authorization not in {None, authorization_uuid}:
            raise RuntimeError("parent authorization changed during commit")
        if current_authorization == authorization_uuid:
            observed_effects[0] += _metadata_delete_observed(
                runner,
                parent_key,
                REPAIR_AUTHORIZATION_KEY,
            )

    committed_metadata = parse_issue_metadata(
        runner.run(["issue", "metadata", "list", parent_key, "--output", "json"])
    )
    if (
        any(committed_metadata.get(key) != value for key, value in desired.items())
        or committed_metadata.get(REPAIR_RESERVATION_KEY)
        != _canonical_json(reservation)
        or (
            authorization_uuid
            and REPAIR_AUTHORIZATION_KEY in committed_metadata
        )
    ):
        raise RuntimeError("parent repair commit verification failed")

    fresh = load_parent_snapshot(runner, github, parent_key)
    _validate_repair_reservation(fresh, reservation, action_key)
    children = _repair_children_for_reservation(runner, fresh, reservation)
    expected_repositories = {
        str(spec["repository"]) for spec in reservation["child_specs"]
    }
    if set(children) != expected_repositories:
        raise RuntimeError("repair children changed before promotion")
    for repository in sorted(children):
        child = children[repository]
        _promote_repair_child_observed(runner, child, observed_effects)

    observed_effects[0] += _metadata_delete_observed(
        runner,
        parent_key,
        REPAIR_RESERVATION_KEY,
    )
    final_metadata = parse_issue_metadata(
        runner.run(["issue", "metadata", "list", parent_key, "--output", "json"])
    )
    if REPAIR_RESERVATION_KEY in final_metadata:
        raise RuntimeError("repair reservation clear verification failed")
    return tuple(
        children[repository].issue_key for repository in sorted(children)
    )


def _verified_replayed_repair_children(
    runner: MulticaRunner,
    snapshot: ParentSnapshot,
    expected_action_key: str,
) -> tuple[str, ...]:
    matching = tuple(
        item
        for item in snapshot.children
        if item.creation_action == expected_action_key
    )
    if not matching or len({item.stage for item in matching}) != 1:
        raise RuntimeError("recorded repair action has no exact child set")
    stage = matching[0].stage
    try:
        action_next_stage, source_stage = _repair_action_stage_identity(
            expected_action_key
        )
    except RuntimeError:
        raise RuntimeError("recorded repair action identity conflicts") from None
    rounds = {item.repair_round for item in matching}
    authorizations = {item.authorizing_comment_uuid for item in matching}
    source_candidate_sets = {
        item.repair_source_candidates for item in matching
    }
    if (
        len(rounds) != 1
        or len(authorizations) != 1
        or len(source_candidate_sets) != 1
        or not next(iter(source_candidate_sets), ())
        or stage != action_next_stage
        or action_next_stage != source_stage + 1
        or snapshot.attempt != next(iter(rounds))
        or snapshot.next_stage != stage + 1
    ):
        raise RuntimeError("recorded repair child set conflicts")
    repair_round = next(iter(rounds))
    authorization_uuid = next(iter(authorizations))
    source_candidates = dict(next(iter(source_candidate_sets)))
    if _repair_action_source_candidates(expected_action_key) != source_candidates:
        raise RuntimeError("recorded repair source candidate identity conflicts")
    if repair_round == 3:
        if authorization_uuid != snapshot.consumed_authorization_uuid:
            raise RuntimeError("recorded round-three authorization conflicts")
    elif authorization_uuid:
        raise RuntimeError("recorded automatic repair carries authorization")
    source_snapshot = _snapshot_with_candidates(
        replace(
            snapshot,
            attempt=repair_round - 1,
            next_stage=action_next_stage,
            last_action=None,
            authorization_comment_uuid="",
            authorizing_comment=None,
            repair_reservation=None,
        ),
        source_candidates,
    )
    source_phases = tuple(
        item for item in snapshot.children if item.stage == source_stage
    )
    try:
        bundle = _failure_bundle(source_snapshot, source_phases)
    except ValueError:
        raise RuntimeError("recorded repair bundle cannot be reconstructed") from None
    computed_key = _action_key(
        source_snapshot,
        "create_repair_stage",
        repair_round,
        str(bundle["digest"]),
        authorization_uuid or None,
        int(bundle["source_stage_ordinal"]),
    )
    if computed_key != expected_action_key:
        raise RuntimeError("recorded repair action identity conflicts")
    reservation = _decode_repair_reservation(
        _canonical_json(
            {
                "action_key": expected_action_key,
                "authorizing_comment_uuid": authorization_uuid,
                "child_specs": _repair_child_specs(source_snapshot, bundle),
                "failure_bundle": bundle,
                "next_stage": action_next_stage,
                "parent_identifier": snapshot.identifier,
                "previous_last_action": "",
                "prior_consumed_authorization_uuid": "",
                "repair_round": repair_round,
                "source_attempt": repair_round - 1,
                "source_candidates": source_candidates,
            }
        )
    )
    children = _repair_children_for_reservation(runner, snapshot, reservation)
    _validate_repair_reservation(
        snapshot,
        reservation,
        expected_action_key,
        committed_children=children,
    )
    expected_repositories = {
        str(spec["repository"]) for spec in reservation["child_specs"]
    }
    if set(children) != expected_repositories:
        raise RuntimeError("recorded repair action has an incomplete child multiset")
    for child in children.values():
        if child.status == "backlog":
            raise RuntimeError("recorded repair child promotion conflicts")
        replay_effects = [0]
        _promote_repair_child_observed(runner, child, replay_effects)
        if replay_effects[0]:
            raise RuntimeError("recorded repair child promotion conflicts")
    return tuple(children[name].issue_key for name in sorted(children))


def execute_parent_repair(
    runner: MulticaRunner,
    github: GitHubRunner,
    parent_key: str,
    *,
    expected_action_key: str,
) -> RepairExecutionResult:
    """Execute or resume one exact planned repair under serialized Lead access."""

    if (
        type(expected_action_key) is not str
        or not expected_action_key
        or ISSUE_KEY_PATTERN.fullmatch(parent_key) is None
    ):
        return RepairExecutionResult(
            parent_key,
            "block",
            "repair execution requires an exact parent and action identity",
            expected_action_key if isinstance(expected_action_key, str) else "",
            0,
        )
    observed_effects = [0]
    try:
        detail = parse_issue_detail(
            runner.run(["issue", "get", parent_key, "--output", "json"]),
            parent_key,
        )
        if (
            detail["parent_issue_id"] is not None
            or detail["stage"] is not None
            or detail["status"] not in {"in_progress", "in_review"}
            or detail["assignee_type"] == "member"
        ):
            raise RuntimeError("repair execution requires a mutable parent")
        snapshot = load_parent_snapshot(runner, github, parent_key)
        reservation = snapshot.repair_reservation
        if reservation is None and snapshot.last_action == expected_action_key:
            children = _verified_replayed_repair_children(
                runner,
                snapshot,
                expected_action_key,
            )
            return RepairExecutionResult(
                parent_key,
                "noop",
                "exact repair action is already committed",
                expected_action_key,
                0,
                children,
            )
        if reservation is None:
            decision = decide_parent_action(snapshot)
            if (
                decision.kind != "create_repair_stage"
                or decision.action_key != expected_action_key
            ):
                return RepairExecutionResult(
                    parent_key,
                    "block",
                    "fresh parent plan does not authorize the expected repair action",
                    expected_action_key,
                    0,
                )
            reservation = _build_repair_reservation(snapshot, decision)
            observed_effects[0] += _metadata_set_observed(
                runner,
                parent_key,
                REPAIR_RESERVATION_KEY,
                _canonical_json(reservation),
            )
            persisted = parse_issue_metadata(
                runner.run(
                    ["issue", "metadata", "list", parent_key, "--output", "json"]
                )
            )
            if persisted.get(REPAIR_RESERVATION_KEY) != _canonical_json(reservation):
                raise RuntimeError("repair reservation persistence failed")
        _validate_repair_reservation(snapshot, reservation, expected_action_key)
        _create_reserved_repair_children(
            runner,
            github,
            parent_key,
            reservation,
            observed_effects,
        )
        child_identifiers = _commit_reserved_repair(
            runner,
            github,
            parent_key,
            reservation,
            observed_effects,
        )
        return RepairExecutionResult(
            parent_key,
            "repair",
            "exact reserved repair children were committed and promoted",
            expected_action_key,
            observed_effects[0],
            child_identifiers,
        )
    except (RuntimeError, ValueError, KeyError, TypeError) as error:
        return RepairExecutionResult(
            parent_key,
            "block",
            str(error) or "repair execution failed closed",
            expected_action_key,
            observed_effects[0],
        )


def _has_phase_completion(metadata: dict[str, str]) -> bool:
    version = metadata.get("eventra.workflow.version")
    if version not in {"1", "2"}:
        return False
    if version == "2":
        try:
            repositories = json.loads(
                metadata.get("eventra.phase.failure_repositories", "")
            )
        except (json.JSONDecodeError, TypeError):
            return False
        if (
            not isinstance(repositories, list)
            or any(
                type(repository) is not str
                or repository not in {"frontend", "backend"}
                for repository in repositories
            )
            or len(set(repositories)) != len(repositories)
            or repositories != sorted(repositories)
            or metadata.get("eventra.phase.failure_repositories")
            != json.dumps(repositories, sort_keys=True, separators=(",", ":"))
        ):
            return False
        phase_repositories = {
            repository
            for repository, key in (
                ("frontend", "eventra.phase.sha.frontend"),
                ("backend", "eventra.phase.sha.backend"),
            )
            if key in metadata
        }
        if not _valid_phase_ownership(
            metadata.get("eventra.phase.kind", "unknown"),
            metadata.get("eventra.phase.result"),
            phase_repositories,
            tuple(repositories),
            metadata.get("eventra.phase.evidence_comment_url"),
        ):
            return False
    return (
        metadata.get("eventra.phase.kind") in PHASE_KINDS
        and metadata.get("eventra.phase.result") in PHASE_RESULTS
        and metadata.get("eventra.phase.attempt", "").isdigit()
        and (
            version == "1"
            or int(metadata["eventra.phase.attempt"]) <= 3
        )
        and _is_uuid(metadata.get("eventra.phase.evidence_comment"))
    )


def _is_uuid(value: str | None) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


def _is_human_wait(issue: dict[str, object]) -> bool:
    return (
        issue["assignee_type"] == "member"
        and issue["status"] in {"todo", "in_progress", "in_review"}
    )


def load_workflow_snapshot(
    runner: MulticaRunner,
    parent_key: str,
) -> WorkflowSnapshot:
    """Read one complete parent/child/run view from authoritative CLI reads."""

    parent = parse_issue_detail(
        runner.run(["issue", "get", parent_key, "--output", "json"]),
        parent_key,
    )
    if parent["parent_issue_id"] is not None:
        raise RuntimeError("workflow snapshot requires a parent issue")
    parent_metadata = parse_issue_metadata(
        runner.run(["issue", "metadata", "list", parent_key, "--output", "json"])
    )
    workflow_version = parent_metadata.get("eventra.workflow.version")
    if workflow_version not in {"1", "2"}:
        raise RuntimeError("unsupported workflow metadata")
    children = parse_issue_children(
        runner.run(["issue", "children", parent_key, "--output", "json"]),
        str(parent["id"]),
    )
    parent_runs = parse_issue_runs(
        runner.run(["issue", "runs", parent_key, "--output", "json"]),
        str(parent["id"]),
    )
    snapshots: list[ChildRunSnapshot] = []
    has_human_wait = _is_human_wait(parent)
    for child in children:
        child_key = str(child["identifier"])
        metadata = parse_issue_metadata(
            runner.run(
                ["issue", "metadata", "list", child_key, "--output", "json"]
            )
        )
        runs = parse_issue_runs(
            runner.run(["issue", "runs", child_key, "--output", "json"]),
            str(child["id"]),
        )
        latest = max(runs, key=lambda item: item["activity_at"]) if runs else None
        has_active = any(item["status"] in ACTIVE_RUN_STATUSES for item in runs)
        has_human_wait = has_human_wait or _is_human_wait(child)
        snapshots.append(
            ChildRunSnapshot(
                issue_id=str(child["id"]),
                identifier=child_key,
                stage=0 if child["stage"] is None else int(child["stage"]),
                issue_status=str(child["status"]),
                latest_run_status=None if latest is None else str(latest["status"]),
                latest_run_activity_at=(
                    None if latest is None else str(latest["activity_at"])
                ),
                has_active_run=has_active,
                has_phase_completion=_has_phase_completion(metadata),
            )
        )

    next_stage_text = parent_metadata.get("eventra.workflow.next_stage")
    current_stage = (
        int(next_stage_text) - 1
        if workflow_version == "2"
        and isinstance(next_stage_text, str)
        and next_stage_text.isdigit()
        and int(next_stage_text) >= 1
        else None
    )
    staged = [item for item in children if item["stage"] is not None]
    current_stage_children = [
        item for item in staged if item["stage"] == current_stage
    ]
    malformed_current_stage = workflow_version == "2" and (
        current_stage is None
        or any(int(item["stage"]) > current_stage for item in staged)
        or (bool(staged) and not current_stage_children)
    )
    latest_stage_finished = bool(current_stage_children) and all(
        item["status"] == "done" for item in current_stage_children
    )
    stage_activity = max(
        (str(item["updated_at"]) for item in current_stage_children),
        default="",
    )
    has_later_parent_run = latest_stage_finished and any(
        item["activity_at"] > stage_activity for item in parent_runs
    )
    parent_active = any(
        item["status"] in ACTIVE_RUN_STATUSES for item in parent_runs
    )
    return WorkflowSnapshot(
        parent_issue_id=str(parent["id"]),
        parent_identifier=parent_key,
        has_human_approval_wait=has_human_wait,
        has_malformed_state=malformed_current_stage,
        latest_stage_finished=latest_stage_finished,
        has_later_parent_run=has_later_parent_run,
        active_parent_has_no_executable_successor=(
            parent["status"] == "in_progress"
            and not parent_active
            and not children
        ),
        children=tuple(snapshots),
        workflow_version=int(workflow_version),
        current_stage=current_stage,
    )


def _string_metadata_filter(key: str, value: str) -> str:
    if not isinstance(key, str) or not key or not isinstance(value, str):
        raise ValueError("metadata string filter is invalid")
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="").writerow(
        [f"{key}={json.dumps(value)}"]
    )
    return buffer.getvalue()


def _list_workflow_parents(
    runner: MulticaRunner,
    project_ids: Sequence[str],
) -> list[str]:
    records: dict[str, dict[str, object]] = {}
    for project_id in project_ids:
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("invalid watcher project identifier")
        for status in ("in_progress", "in_review"):
            for workflow_version in ("1", "2"):
                version_filter = _string_metadata_filter(
                    "eventra.workflow.version", workflow_version
                )
                offset = 0
                while True:
                    page = parse_issue_list(
                        runner.run(
                            [
                                "issue",
                                "list",
                                "--project",
                                project_id,
                                "--status",
                                status,
                                "--metadata",
                                version_filter,
                                "--limit",
                                "50",
                                "--offset",
                                str(offset),
                                "--output",
                                "json",
                            ]
                        ),
                        project_id,
                    )
                    for issue in page["issues"]:
                        if issue["parent_issue_id"] is not None:
                            continue
                        previous = records.get(str(issue["id"]))
                        if previous is not None and previous != issue:
                            raise RuntimeError("malformed watcher issue list")
                        records[str(issue["id"])] = issue
                    if not page["has_more"]:
                        break
                    if not page["issues"]:
                        raise RuntimeError("malformed watcher issue list")
                    offset += len(page["issues"])
    ordered = sorted(
        records.values(),
        key=lambda item: (str(item["updated_at"]), str(item["identifier"])),
    )
    return [str(item["identifier"]) for item in ordered]


def watch_projects(
    runner: MulticaRunner,
    project_ids: Sequence[str],
    *,
    apply: bool,
) -> WatchResult:
    """Scan only the configured projects and recover at most one workflow."""

    if len(project_ids) != 2 or len(set(project_ids)) != 2:
        raise ValueError("watcher requires two distinct project identifiers")
    parent_keys = _list_workflow_parents(runner, project_ids)
    candidates: list[tuple[str, RecoveryDecision]] = []
    migrations: list[tuple[str, RecoveryDecision]] = []
    for parent_key in parent_keys:
        decision = decide_recovery(load_workflow_snapshot(runner, parent_key))
        if decision.reason == "version 1 workflow requires explicit migration":
            migrations.append((parent_key, decision))
        elif decision.kind != "noop":
            candidates.append((parent_key, decision))
    if migrations:
        return WatchResult(
            len(parent_keys),
            len(candidates),
            0,
            "noop",
            "version 1 workflow requires explicit migration",
        )
    if not candidates:
        return WatchResult(len(parent_keys), 0, 0, "noop")
    first_parent, first_decision = candidates[0]
    if not apply:
        return WatchResult(
            len(parent_keys), len(candidates), 0, first_decision.kind
        )
    recovered = recover_once(
        runner,
        lambda: load_workflow_snapshot(runner, first_parent),
    )
    return WatchResult(
        len(parent_keys),
        len(candidates),
        recovered.mutation_count,
        recovered.decision.kind,
    )


def _repair_completion_provenance_problem(
    metadata: dict[str, str],
    detail: dict[str, object],
    parent_metadata: dict[str, str],
    parent_identifier: str,
    current_stage: int,
    current_attempt: int,
    value: PhaseCompletion,
    authoritative_repair_snapshot: ParentSnapshot | None = None,
) -> str | None:
    observed_keys = {
        key for key in metadata if key.startswith("eventra.repair.")
    }
    if observed_keys != REPAIR_PROVENANCE_KEYS:
        return "repair completion lacks complete executor provenance"
    try:
        evidence_uuids = json.loads(
            metadata["eventra.repair.failure_evidence_uuids"]
        )
        creation_action = metadata["eventra.repair.creation_action"]
        action_stage, source_stage = _repair_action_stage_identity(
            creation_action
        )
        action_source_candidates = _repair_action_source_candidates(
            creation_action
        )
        source_candidates = _decode_source_candidates(
            metadata["eventra.repair.source_candidates"]
        )
    except (KeyError, RuntimeError, json.JSONDecodeError, TypeError, ValueError):
        return "repair completion executor provenance is malformed"
    repository = metadata["eventra.repair.repository"]
    pull_request = metadata["eventra.repair.pull_request"]
    authorization_uuid = metadata["eventra.repair.authorizing_comment_uuid"]
    round_text = metadata["eventra.repair.round"]
    digest = metadata["eventra.repair.failure_bundle_digest"]
    try:
        pull_request_repository = _repository_for_pr(pull_request)
    except (TypeError, ValueError):
        return "repair completion executor provenance is malformed"
    action_parts = creation_action.split(":")
    expected_scope = {
        "frontend-only": "frontend",
        "backend-only": "backend",
        "cross-stack": "cross-stack",
    }.get(parent_metadata.get("eventra.workflow.classification"))
    parent_candidates = {
        repository: sha
        for repository, sha in (
            (
                "frontend",
                parent_metadata.get("eventra.workflow.frontend_sha", "-"),
            ),
            (
                "backend",
                parent_metadata.get("eventra.workflow.backend_sha", "-"),
            ),
        )
        if sha != "-"
    }
    requested_candidates = {
        repository: sha
        for repository, sha in (
            ("frontend", value.frontend_sha),
            ("backend", value.backend_sha),
        )
        if sha is not None
    }
    sha_keys = {
        key
        for key in ("frontend", "backend")
        if f"eventra.phase.sha.{key}" in metadata
    }
    if (
        metadata.get("eventra.workflow.version") != "2"
        or metadata.get("eventra.phase.kind") != "repair"
        or metadata.get("eventra.phase.attempt") != str(current_attempt)
        or detail["stage"] != current_stage
        or detail["assignee_type"] != "agent"
        or creation_action != parent_metadata.get("eventra.workflow.last_action")
        or action_parts[1] != parent_identifier
        or action_parts[3] != str(current_attempt)
        or action_parts[4] != expected_scope
        or action_source_candidates != source_candidates
        or action_stage != current_stage
        or source_stage != current_stage - 1
        or len(action_parts) not in {13, 15}
        or digest != action_parts[12]
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or not isinstance(evidence_uuids, list)
        or not evidence_uuids
        or evidence_uuids != sorted(evidence_uuids)
        or len(evidence_uuids) != len(set(evidence_uuids))
        or any(not _is_uuid(item) for item in evidence_uuids)
        or metadata["eventra.repair.failure_evidence_uuids"]
        != _canonical_json(evidence_uuids)
        or repository not in REPAIR_ASSIGNEES
        or repository not in source_candidates
        or sha_keys != {repository}
        or set(requested_candidates) != {repository}
        or value.pr_url != pull_request
        or SHA_PATTERN.fullmatch(
            metadata.get(f"eventra.phase.sha.{repository}", "")
        )
        is None
        or pull_request_repository != repository
        or metadata.get("eventra.phase.pr") != pull_request
        or not round_text.isdigit()
        or int(round_text) != current_attempt
        or (current_attempt == 3) != bool(authorization_uuid)
        or (authorization_uuid and not _is_uuid(authorization_uuid))
        or (current_attempt == 3 and len(action_parts) != 15)
        or (
            current_attempt == 3
            and action_parts[14] != authorization_uuid
        )
        or (current_attempt in {1, 2} and len(action_parts) != 13)
        or (
            current_attempt == 3
            and authorization_uuid
            != parent_metadata.get(REPAIR_AUTHORIZATION_CONSUMED_KEY, "")
        )
    ):
        return "repair completion executor provenance conflicts"
    source_sha = source_candidates[repository]
    phase_sha = metadata[f"eventra.phase.sha.{repository}"]
    requested_sha = requested_candidates[repository]
    authoritative_head = None
    if authoritative_repair_snapshot is not None:
        authoritative_head = next(
            (
                item.head_sha
                for item in authoritative_repair_snapshot.pull_requests
                if item.repository == repository
            ),
            None,
        )
    if value.result != "pass":
        if (
            phase_sha != source_sha
            or requested_sha != source_sha
            or (
                authoritative_repair_snapshot is None
                and parent_candidates != source_candidates
            )
            or parent_candidates.get(repository) != source_sha
        ):
            return "nonpassing repair completion cannot replace its seeded SHA"
        return None
    if requested_sha == source_sha:
        return "repair PASS requires a replacement SHA"
    if (
        authoritative_repair_snapshot is not None
        and authoritative_head != requested_sha
    ):
        return "repair replacement does not match its current managed PR head"
    if detail["status"] == "done":
        if (
            phase_sha != requested_sha
            or set(parent_candidates) != set(source_candidates)
            or parent_candidates[repository] not in {source_sha, requested_sha}
        ):
            return "terminal repair replacement conflicts with current authority"
    elif (
        phase_sha != source_sha
        or parent_candidates.get(repository) != source_sha
        or (
            authoritative_repair_snapshot is None
            and parent_candidates != source_candidates
        )
    ):
        return "repair replacement requires the seeded source and parent candidates"
    return None


def _finish_phase_authority_problem(
    runner: MulticaRunner,
    detail: dict[str, object],
    metadata: dict[str, str],
    value: PhaseCompletion,
) -> str | None:
    parent_id = str(detail["parent_issue_id"])
    raw_parent = runner.run(
        ["issue", "get", parent_id, "--output", "json"]
    )
    if (
        not isinstance(raw_parent, dict)
        or type(raw_parent.get("identifier")) is not str
    ):
        return "authoritative parent detail is malformed"
    try:
        parent = parse_issue_detail(raw_parent, str(raw_parent["identifier"]))
        children = parse_issue_children(
            runner.run(
                [
                    "issue",
                    "children",
                    str(parent["identifier"]),
                    "--output",
                    "json",
                ]
            ),
            str(parent["id"]),
        )
        parent_metadata = parse_issue_metadata(
            runner.run(
                [
                    "issue",
                    "metadata",
                    "list",
                    str(parent["identifier"]),
                    "--output",
                    "json",
                ]
            )
        )
        if parent_metadata.get("eventra.workflow.version") == "1":
            return "version 1 workflow requires explicit migration"
        parent_workflow = _parent_metadata(parent_metadata)
    except RuntimeError:
        return "authoritative parent or child relationship is malformed"
    next_stage = int(parent_workflow["next_stage"])
    attempt = int(parent_workflow["attempt"])
    if (
        parent["parent_issue_id"] is not None
        or parent["stage"] is not None
        or parent["status"] not in {"todo", "in_progress", "in_review"}
        or parent_workflow["workflow_version"] != 2
        or next_stage < 2
        or attempt not in {0, 1, 2, 3}
        or parent_workflow["repair_reservation"] is not None
    ):
        return "parent is not a mutable current workflow authority"
    current_stage = next_stage - 1
    matches = [
        child
        for child in children
        if child["id"] == detail["id"]
        and child["identifier"] == detail["identifier"]
    ]
    if (
        len(matches) != 1
        or matches[0] != detail
        or detail["stage"] != current_stage
        or value.attempt != attempt
    ):
        return "phase completion is not for the exact current child"
    if value.kind == "repair":
        authoritative_repair_snapshot = None
        try:
            source_candidates = _decode_source_candidates(
                metadata["eventra.repair.source_candidates"]
            )
            action_source_candidates = _repair_action_source_candidates(
                metadata["eventra.repair.creation_action"]
            )
        except (KeyError, RuntimeError):
            source_candidates = {}
            action_source_candidates = {}
        parent_candidates = {
            repository: sha
            for repository, sha in (
                (
                    "frontend",
                    parent_metadata.get("eventra.workflow.frontend_sha"),
                ),
                (
                    "backend",
                    parent_metadata.get("eventra.workflow.backend_sha"),
                ),
            )
            if sha is not None
        }
        if (
            source_candidates == action_source_candidates
            and parent_candidates == source_candidates
        ):
            local_problem = _repair_completion_provenance_problem(
                metadata,
                detail,
                parent_metadata,
                str(parent["identifier"]),
                current_stage,
                attempt,
                value,
            )
            if local_problem is not None:
                return local_problem
        if (
            source_candidates == action_source_candidates
            and set(parent_candidates) == set(source_candidates)
            and (
                detail["status"] != "done"
                or parent_candidates != source_candidates
            )
        ):
            try:
                authoritative_repair_snapshot = load_parent_snapshot(
                    runner,
                    GitHubRunner(),
                    str(parent["identifier"]),
                )
            except (RuntimeError, TypeError, ValueError):
                return "authoritative current repair snapshot is malformed"
            current_repairs = tuple(
                item
                for item in authoritative_repair_snapshot.children
                if item.stage == current_stage
            )
            repair_problem = _current_repair_provenance_problem(
                authoritative_repair_snapshot,
                current_repairs,
            )
            loaded_target = tuple(
                item
                for item in current_repairs
                if item.issue_key == detail["identifier"]
            )
            if (
                authoritative_repair_snapshot.identifier
                != str(parent["identifier"])
                or authoritative_repair_snapshot.parent_id != str(parent["id"])
                or authoritative_repair_snapshot.attempt != attempt
                or authoritative_repair_snapshot.next_stage != next_stage
                or authoritative_repair_snapshot.last_action
                != parent_metadata.get("eventra.workflow.last_action")
                or _candidate_sha_map(authoritative_repair_snapshot)
                != parent_candidates
                or repair_problem is not None
                or len(loaded_target) != 1
                or loaded_target[0].status != detail["status"]
                or loaded_target[0].repair_repository
                != metadata.get("eventra.repair.repository")
            ):
                return "partial repair parent copy is not current and authoritative"
        return _repair_completion_provenance_problem(
            metadata,
            detail,
            parent_metadata,
            str(parent["identifier"]),
            current_stage,
            attempt,
            value,
            authoritative_repair_snapshot,
        )
    if any(key.startswith("eventra.repair.") for key in metadata):
        return "non-repair completion carries repair provenance"
    return None


def finish_phase(
    runner: MulticaRunner,
    issue_key: str,
    value: PhaseCompletion,
) -> PhaseResult:
    """Write, verify, and terminally finish one staged child phase."""

    wanted = build_phase_metadata(value)
    if not isinstance(issue_key, str) or ISSUE_KEY_PATTERN.fullmatch(issue_key) is None:
        raise ValueError("invalid issue identifier")
    detail = parse_issue_detail(
        runner.run(["issue", "get", issue_key, "--output", "json"]),
        issue_key,
    )
    if detail["parent_issue_id"] is None or detail["stage"] is None:
        raise RuntimeError("phase completion requires a staged child issue")
    before = parse_issue_metadata(
        runner.run(["issue", "metadata", "list", issue_key, "--output", "json"])
    )
    controlled_before = {
        key: item for key, item in before.items() if key in CONTROLLED_PHASE_KEYS
    }
    if detail["status"] == "done":
        if controlled_before == wanted:
            authority_problem = _finish_phase_authority_problem(
                runner,
                detail,
                before,
                value,
            )
            if authority_problem is not None:
                raise RuntimeError(authority_problem)
            return PhaseResult(
                str(detail["id"]), issue_key, "done", value.kind, value.result, 0
            )
        legacy_wanted = dict(wanted)
        legacy_wanted["eventra.workflow.version"] = "1"
        legacy_wanted.pop("eventra.phase.failure_repositories")
        legacy_wanted.pop("eventra.phase.evidence_comment_url", None)
        if controlled_before == legacy_wanted:
            return PhaseResult(
                str(detail["id"]), issue_key, "done", value.kind, value.result, 0
            )
        raise RuntimeError("terminal phase metadata conflicts with request")
    if detail["status"] in {"blocked", "cancelled"}:
        raise RuntimeError("phase issue is not mutable")
    if controlled_before.get("eventra.workflow.version") == "1":
        raise RuntimeError("version 1 workflow requires explicit migration")
    authority_problem = _finish_phase_authority_problem(
        runner,
        detail,
        before,
        value,
    )
    if authority_problem is not None:
        raise RuntimeError(authority_problem)
    allowed_replacement_key = (
        f"eventra.phase.sha.{before['eventra.repair.repository']}"
        if value.kind == "repair" and value.result == "pass"
        else None
    )
    if any(
        key not in wanted
        or (wanted[key] != item and key != allowed_replacement_key)
        for key, item in controlled_before.items()
    ):
        raise RuntimeError("phase metadata conflicts with request")

    for key, item in wanted.items():
        runner.run(
            [
                "issue",
                "metadata",
                "set",
                issue_key,
                "--key",
                key,
                "--value",
                item,
                "--type",
                "string",
                "--output",
                "json",
            ]
        )
    observed = parse_issue_metadata(
        runner.run(["issue", "metadata", "list", issue_key, "--output", "json"])
    )
    controlled_observed = {
        key: item for key, item in observed.items() if key in CONTROLLED_PHASE_KEYS
    }
    if controlled_observed != wanted:
        raise RuntimeError("phase metadata reconciliation failed")
    runner.run(
        [
            "issue",
            "status",
            issue_key,
            "done",
            "--no-start",
            "--output",
            "json",
        ]
    )
    final = parse_issue_detail(
        runner.run(["issue", "get", issue_key, "--output", "json"]),
        issue_key,
    )
    if final["status"] != "done":
        raise RuntimeError("phase completion failed")
    return PhaseResult(
        str(final["id"]),
        issue_key,
        "done",
        value.kind,
        value.result,
        len(wanted) + 1,
    )


def finish_parent(
    runner: MulticaRunner,
    parent_key: str,
    snapshot_loader: Callable[[], ParentSnapshot],
) -> ParentCompletionResult:
    """Finish an unattended parent only after a stable merged-smoke PASS."""

    if (
        not isinstance(parent_key, str)
        or ISSUE_KEY_PATTERN.fullmatch(parent_key) is None
    ):
        raise ValueError("invalid issue identifier")
    detail = parse_issue_detail(
        runner.run(["issue", "get", parent_key, "--output", "json"]),
        parent_key,
    )
    if detail["parent_issue_id"] is not None or detail["stage"] is not None:
        raise RuntimeError("parent completion requires a parent issue")
    if detail["status"] == "done":
        return ParentCompletionResult(str(detail["id"]), parent_key, "done", 0)
    if detail["assignee_type"] == "member":
        raise RuntimeError("parent has a human approval wait")
    if detail["status"] not in {"in_progress", "in_review"}:
        raise RuntimeError("parent issue is not mutable")

    initial_snapshot = snapshot_loader()
    initial_decision = decide_parent_action(initial_snapshot)
    fresh_snapshot = snapshot_loader()
    fresh_decision = decide_parent_action(fresh_snapshot)
    if (
        initial_snapshot.identifier != parent_key
        or fresh_snapshot != initial_snapshot
        or initial_decision != fresh_decision
        or fresh_decision.kind != "complete_parent"
    ):
        raise RuntimeError("parent completion is not authorized")

    before_write = parse_issue_detail(
        runner.run(["issue", "get", parent_key, "--output", "json"]),
        parent_key,
    )
    if (
        before_write["parent_issue_id"] is not None
        or before_write["stage"] is not None
        or before_write["status"] not in {"in_progress", "in_review"}
        or before_write["assignee_type"] == "member"
    ):
        raise RuntimeError("parent completion is not authorized")
    runner.run(
        [
            "issue",
            "status",
            parent_key,
            "done",
            "--no-start",
            "--output",
            "json",
        ]
    )
    final = parse_issue_detail(
        runner.run(["issue", "get", parent_key, "--output", "json"]),
        parent_key,
    )
    if final["status"] != "done":
        raise RuntimeError("parent completion failed")
    return ParentCompletionResult(str(final["id"]), parent_key, "done", 1)


def build_workflow_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Advance Eventra Multica workflow state deterministically."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    finish = subparsers.add_parser("finish-phase")
    finish.add_argument("issue")
    finish.add_argument("--kind", required=True, choices=sorted(PHASE_KINDS))
    finish.add_argument("--result", required=True, choices=sorted(PHASE_RESULTS))
    finish.add_argument("--attempt", required=True, type=int, choices=(0, 1, 2, 3))
    finish.add_argument("--evidence-comment", required=True)
    finish.add_argument("--evidence-comment-url")
    finish.add_argument("--frontend-sha")
    finish.add_argument("--backend-sha")
    finish.add_argument("--pr")
    finish.add_argument(
        "--responsible-repository",
        action="append",
        choices=("frontend", "backend"),
        default=[],
    )
    plan_parent = subparsers.add_parser("plan-parent")
    plan_parent.add_argument("parent")
    execute_repair = subparsers.add_parser("execute-parent-repair")
    execute_repair.add_argument("parent")
    execute_repair.add_argument("--expected-action-key", required=True)
    finish_parent_parser = subparsers.add_parser("finish-parent")
    finish_parent_parser.add_argument("parent")
    watch = subparsers.add_parser("watch")
    watch.add_argument("--project-id", required=True)
    watch.add_argument("--backend-project-id", required=True)
    watch.add_argument("--apply", action="store_true")
    return parser


def print_phase_result(value: PhaseResult) -> None:
    print(
        f"issue={value.issue_key} status={value.status} kind={value.kind} "
        f"result={value.result} mutations={value.mutation_count}"
    )


def print_parent_result(value: ParentCompletionResult) -> None:
    print(
        f"issue={value.issue_key} status={value.status} "
        f"mutations={value.mutation_count}"
    )


def print_watch_result(value: WatchResult) -> None:
    reason = (
        ""
        if not value.reason
        else " reason="
        + json.dumps(value.reason, ensure_ascii=False, separators=(",", ":"))
    )
    print(
        f"scanned={value.scanned} candidates={value.candidates} "
        f"applied={value.applied} decision={value.decision}{reason}"
    )


def print_parent_decision(value: ParentDecision) -> None:
    print(
        json.dumps(
            {
                "decision": value.kind,
                "action_key": value.action_key,
                "reason": value.reason,
                "failure_bundle": value.failure_bundle,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


def print_repair_execution_result(value: RepairExecutionResult) -> None:
    print(
        _canonical_json(
            {
                "action_key": value.action_key,
                "children": list(value.child_identifiers),
                "decision": value.next_action,
                "mutations": value.mutation_count,
                "parent": value.parent_identifier,
                "reason": value.reason,
            }
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_workflow_parser().parse_args(argv)
    runner = MulticaRunner()
    if args.command == "finish-phase":
        completion = PhaseCompletion(
            kind=args.kind,
            result=args.result,
            attempt=args.attempt,
            evidence_comment=args.evidence_comment,
            frontend_sha=args.frontend_sha,
            backend_sha=args.backend_sha,
            pr_url=args.pr,
            responsible_repositories=tuple(args.responsible_repository),
            evidence_comment_url=args.evidence_comment_url,
        )
        print_phase_result(finish_phase(runner, args.issue, completion))
    elif args.command == "plan-parent":
        print_parent_decision(
            decide_parent_action(
                load_parent_snapshot(runner, GitHubRunner(), args.parent)
            )
        )
    elif args.command == "execute-parent-repair":
        print_repair_execution_result(
            execute_parent_repair(
                runner,
                GitHubRunner(),
                args.parent,
                expected_action_key=args.expected_action_key,
            )
        )
    elif args.command == "finish-parent":
        print_parent_result(
            finish_parent(
                runner,
                args.parent,
                lambda: load_parent_snapshot(runner, GitHubRunner(), args.parent),
            )
        )
    elif args.command == "watch":
        print_watch_result(
            watch_projects(
                runner,
                (args.project_id, args.backend_project_id),
                apply=args.apply,
            )
        )
    else:
        raise RuntimeError("unsupported workflow command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
