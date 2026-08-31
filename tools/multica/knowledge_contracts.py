"""Immutable, redacted contracts for Eventra repository knowledge."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit


_ISSUE = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SCOPES = frozenset({"frontend", "backend", "cross_repo"})
_REPOSITORIES = frozenset({"frontend", "backend"})
_KNOWLEDGE_TYPES = frozenset(
    {"repository_map", "architecture", "invariant", "pitfall", "testing", "playbook", "decision", "incident", "contract"}
)


@dataclass(frozen=True)
class KnowledgeEvidenceRef:
    project_id: str
    parent_identifier: str
    child_identifier: str
    comment_uuid: str
    comment_url: str


@dataclass(frozen=True)
class KnowledgeCandidate:
    schema_version: int
    digest: str
    evidence: KnowledgeEvidenceRef
    candidate_shas: tuple[tuple[str, str], ...]
    target_scope: Literal["frontend", "backend", "cross_repo"]
    target_repository: Literal["frontend", "backend"]
    knowledge_type: str
    claim: str
    task_types: tuple[str, ...]
    repository_paths: tuple[str, ...]
    verification_refs: tuple[str, ...]
    sensitivity: Literal["public_repo"]
    related_knowledge_ids: tuple[str, ...]
    submitted_role: str
    submitted_at: str


@dataclass(frozen=True)
class ContextReceipt:
    schema_version: int
    task_id: str
    repository: Literal["frontend", "backend"]
    task_type: str
    candidate_shas: tuple[tuple[str, str], ...]
    knowledge_ids: tuple[str, ...]
    knowledge_digests: tuple[tuple[str, str], ...]
    match_reasons: tuple[tuple[str, str], ...]
    verified_ids: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeIndexEntry:
    knowledge_id: str
    title: str
    purpose: str
    relative_path: str
    scope: Literal["frontend", "backend", "cross_repo"]
    repositories: tuple[str, ...]
    repository_paths: tuple[str, ...]
    task_types: tuple[str, ...]
    status: Literal["active", "deprecated"]
    replacement_id: str | None
    provenance_kind: Literal["delivery_evidence", "bootstrap_design"]
    source_issue: str | None
    source_comment_uuid: str | None
    source_candidate_shas: tuple[tuple[str, str], ...]
    design_path: str | None
    design_commit: str | None
    last_verified_sha: str
    last_verified_date: str
    content_digest: str


@dataclass(frozen=True)
class CurationDecision:
    kind: Literal["noop", "deduplicated", "rejected", "needs_human", "create_issue"]
    candidate_digest: str | None
    action_key: str | None
    target_project_id: str | None
    target_repository: str | None
    reason: str
    existing_knowledge_id: str | None = None


def _invalid() -> None:
    raise ValueError("invalid knowledge candidate")


def _canonical_payload(payload: Mapping[str, Any]) -> bytes:
    normalized = dict(payload)
    normalized.pop("digest", None)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def candidate_digest(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        _invalid()
    return hashlib.sha256(_canonical_payload(payload)).hexdigest()


def _strings(value: Any, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        _invalid()
    result = tuple(value)
    if nonempty and (not result or any(not item for item in result)):
        _invalid()
    return result


def _sha_map(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or not value or set(value) - _REPOSITORIES:
        _invalid()
    if any(not isinstance(item, str) or _SHA.fullmatch(item) is None for item in value.values()):
        _invalid()
    return tuple(sorted(value.items()))


def parse_candidate_json(text: str) -> KnowledgeCandidate:
    try:
        raw = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        _invalid()
    required = {
        "schema_version", "digest", "evidence", "candidate_shas", "target_scope",
        "target_repository", "knowledge_type", "claim", "task_types",
        "repository_paths", "verification_refs", "sensitivity",
        "related_knowledge_ids", "submitted_role", "submitted_at",
    }
    if not isinstance(raw, dict) or set(raw) != required or raw.get("schema_version") != 1:
        _invalid()
    digest = raw.get("digest")
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None or digest != candidate_digest(raw):
        _invalid()
    evidence = raw.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != {"project_id", "parent_identifier", "child_identifier", "comment_uuid", "comment_url"}:
        _invalid()
    try:
        comment_uuid = str(uuid.UUID(evidence["comment_uuid"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        _invalid()
    url = urlsplit(evidence.get("comment_url"))
    if (
        not isinstance(evidence.get("project_id"), str) or not evidence["project_id"]
        or _ISSUE.fullmatch(evidence.get("parent_identifier", "")) is None
        or _ISSUE.fullmatch(evidence.get("child_identifier", "")) is None
        or comment_uuid != evidence["comment_uuid"] or comment_uuid not in url.path
        or url.scheme != "https" or not url.netloc
    ):
        _invalid()
    shas = _sha_map(raw["candidate_shas"])
    scope = raw["target_scope"]
    repository = raw["target_repository"]
    if scope not in _SCOPES or repository not in _REPOSITORIES:
        _invalid()
    if scope == "cross_repo" and {key for key, _ in shas} != _REPOSITORIES:
        _invalid()
    if raw["knowledge_type"] not in _KNOWLEDGE_TYPES or raw["sensitivity"] != "public_repo":
        _invalid()
    if not isinstance(raw["claim"], str) or not raw["claim"].strip() or not isinstance(raw["submitted_role"], str) or not raw["submitted_role"]:
        _invalid()
    task_types = _strings(raw["task_types"], nonempty=True)
    paths = _strings(raw["repository_paths"], nonempty=True)
    if any(PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts for path in paths):
        _invalid()
    refs = _strings(raw["verification_refs"], nonempty=True)
    related = _strings(raw["related_knowledge_ids"])
    try:
        timestamp = datetime.fromisoformat(raw["submitted_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        _invalid()
    if timestamp.tzinfo is None:
        _invalid()
    return KnowledgeCandidate(
        1, digest,
        KnowledgeEvidenceRef(evidence["project_id"], evidence["parent_identifier"], evidence["child_identifier"], comment_uuid, evidence["comment_url"]),
        shas, scope, repository, raw["knowledge_type"], raw["claim"].strip(),
        task_types, paths, refs, "public_repo", related, raw["submitted_role"], raw["submitted_at"],
    )


def candidate_json(value: KnowledgeCandidate) -> str:
    if not isinstance(value, KnowledgeCandidate):
        _invalid()
    payload = asdict(value)
    payload["candidate_shas"] = dict(value.candidate_shas)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
