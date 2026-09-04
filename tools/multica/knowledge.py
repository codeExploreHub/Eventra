"""Verified, read-only repository knowledge retrieval for the Eventra pilot."""

from __future__ import annotations

import argparse
import csv
import functools
import hashlib
import io
import json
import re
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

import yaml

from tools.multica.knowledge_contracts import (
    CurationDecision,
    ContextReceipt,
    KnowledgeCandidate,
    KnowledgeIndexEntry,
    KnowledgePullRequestState,
    candidate_digest,
    candidate_json,
    parse_candidate_json,
)
from tools.multica.issue_contracts import (
    parse_issue_comments,
    parse_issue_detail,
    parse_issue_list,
    parse_issue_metadata,
)
from tools.multica.provision import MulticaRunner


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ISSUE = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*\Z")
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_REPOSITORIES = frozenset({"frontend", "backend"})
_SCOPES = frozenset({"frontend", "backend", "cross_repo"})
_KNOWLEDGE_PR_BRANCH = re.compile(r"eventra-knowledge/[0-9a-f]{64}\Z")
_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(
        r"eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"
    ),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)(?:password|secret|token|api[_-]?key)\s*[:=]\s*[\"']?[A-Za-z0-9/+_.-]{16,}"
    ),
)
_BOOTSTRAP_DESIGN_PATH = "docs/superpowers/specs/2026-08-31-eventra-repository-knowledge-loop-design.md"
_BOOTSTRAP_DESIGN_COMMIT = "32a150dfa"
# SHA-256 of each entire seed entry's canonical JSON (sorted keys, compact,
# UTF-8). Frozen from frontend 9847b2f2e and backend 5128c7cd1 initial seeds.
# Current backend invariants has since migrated to delivery evidence; its old
# exact seed remains readable for historical checkouts, not arbitrary updates.
# This lives in control code, outside the curator's documentation-only boundary.
_BOOTSTRAP_SEEDS = {
    "frontend-repository-map": "62894f99a5f9a6f9f1e9baf1481deeed78c53c6c4d31a370a829bfd88c287277",
    "frontend-architecture": "66782ba392b2a480591d5358264bea0e36b3b49f4fa15bf2fbaa81c24fbbe021",
    "frontend-invariants": "50f747e94a74c61eea13eb9af9d13451c3bca04a1fe74dd476cdce5b1be45abe",
    "frontend-known-pitfalls": "281ec9a9d9f1e3d331031cda70e5520606bb47e52f01cde499c87d88db6e4c82",
    "frontend-testing-guide": "4496f879a7cd996d3dc354ce60dec8e576ab671628085284e5244f6413840db4",
    "eventra-system-map": "c60487283058a5434e95931486e8717b5075fb2efd993be6a57a80f2748a4fbc",
    "eventra-dependency-graph": "bf535c9a634df0e2d55193a1db7c716c92d7c1269fa5a9e0c82e1a13d777d391",
    "backend-repository-map": "c880077b0d9e691de89721a34b67185331ef139d186c8f383c991dd285ded670",
    "backend-architecture": "c96ff07e58fd23ac0cd70a340110a7f7ecf3ca219222eb2ee326bcee36c84da0",
    "backend-invariants": "8cf55f87e35e1f62746eb2fa426943249930cfd9b00a8739e9897113a4904389",
    "backend-known-pitfalls": "e75466a761130247aa2f50085ae1bc4556364a17d4d6494fa0a64d4a8fa3820c",
    "backend-testing-guide": "10e9455c012c8e0deabca3e80be10925a6b90a4f68c3646c2becf85c07c8be66",
}
_CANDIDATE_FENCE = "eventra-knowledge-candidate-v1"
_CANDIDATE_BLOCK = re.compile(
    rf"(?m)^```{re.escape(_CANDIDATE_FENCE)}\n(?P<body>.*?)\n```[ \t]*$",
    re.DOTALL,
)
_SUMMARY_FENCE = "eventra-knowledge-summary-v1"
_SUMMARY_BLOCK = re.compile(
    rf"(?m)^```{re.escape(_SUMMARY_FENCE)}\n(?P<body>.*?)\n```[ \t]*$",
    re.DOTALL,
)


@dataclass(frozen=True)
class KnowledgeSummaryPointer:
    schema_version: int
    child_identifier: str
    evidence_comment_uuid: str
    candidate_digest: str


@dataclass(frozen=True)
class KnowledgeParentRef:
    issue_id: str
    identifier: str
    project_id: str
    status: Literal["done", "blocked"]
    updated_at: str


@dataclass(frozen=True)
class KnowledgeParentSnapshot:
    issue_id: str
    identifier: str
    project_id: str
    status: Literal["done", "blocked"]
    updated_at: str
    project_ids: tuple[tuple[str, str], ...]
    summary_comment_uuid: str
    candidate_comment_uuid: str
    candidate: KnowledgeCandidate
    dispatch_record: str | None = None
    resolution_record: str | None = None


@dataclass(frozen=True)
class CurationRunResult:
    decision: str
    parent_identifier: str | None
    knowledge_issue_identifier: str | None
    action_key: str | None
    created: int
    mutation_count: int
    reason: str


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            _invalid()
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _invalid() -> None:
    raise ValueError("invalid knowledge index")


def _string(value: Any, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _invalid()
    return value.strip()


def _strings(value: Any, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        _invalid()
    result = tuple(value)
    if nonempty and not result:
        _invalid()
    if len(result) != len(set(result)):
        _invalid()
    return result


def _safe_relative(value: Any) -> str:
    path = _string(value)
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        _invalid()
    return path


def _sha_map(value: Any, *, nonempty: bool = False) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or set(value) - _REPOSITORIES:
        _invalid()
    if nonempty and not value:
        _invalid()
    if any(not isinstance(item, str) or _SHA.fullmatch(item) is None for item in value.values()):
        _invalid()
    return tuple(sorted(value.items()))


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, TypeError, UnicodeError, yaml.YAMLError, ValueError):
        _invalid()


def _resolve_content(root: Path, relative_path: str) -> Path:
    try:
        resolved_root = root.resolve(strict=True)
        resolved = (resolved_root / relative_path).resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        _invalid()
    if not resolved.is_file():
        _invalid()
    return resolved


def _parse_entry(raw: Any, root: Path, owner_repository: str | None, shared_canonical: bool) -> KnowledgeIndexEntry:
    required = {
        "knowledge_id", "title", "purpose", "relative_path", "scope", "repositories",
        "repository_paths", "task_types", "status", "replacement_id", "provenance",
        "last_verified_sha", "last_verified_date", "content_digest",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        _invalid()
    knowledge_id = _string(raw["knowledge_id"])
    if _ID.fullmatch(knowledge_id) is None:
        _invalid()
    relative_path = _safe_relative(raw["relative_path"])
    content = _resolve_content(root, relative_path)
    expected_digest = raw["content_digest"]
    if not isinstance(expected_digest, str) or _DIGEST.fullmatch(expected_digest) is None:
        _invalid()
    if hashlib.sha256(content.read_bytes()).hexdigest() != expected_digest:
        _invalid()

    scope = raw["scope"]
    repositories = _strings(raw["repositories"], nonempty=True)
    if scope not in _SCOPES or set(repositories) - _REPOSITORIES:
        _invalid()
    expected_repositories = {"frontend", "backend"} if scope == "cross_repo" else {scope}
    if set(repositories) != expected_repositories:
        _invalid()
    if scope == "cross_repo" and (owner_repository != "frontend" or not shared_canonical):
        _invalid()
    paths = _strings(raw["repository_paths"], nonempty=True)
    if any(PurePosixPath(item).is_absolute() or ".." in PurePosixPath(item).parts for item in paths):
        _invalid()
    task_types = _strings(raw["task_types"], nonempty=True)
    status = raw["status"]
    replacement_id = _string(raw["replacement_id"], optional=True)
    if status not in {"active", "deprecated"} or (status == "active" and replacement_id is not None):
        _invalid()

    provenance = raw["provenance"]
    if not isinstance(provenance, dict) or provenance.get("kind") not in {"bootstrap_design", "delivery_evidence"}:
        _invalid()
    provenance_kind = provenance["kind"]
    source_issue = source_comment_uuid = design_path = design_commit = None
    source_candidate_shas: tuple[tuple[str, str], ...] = ()
    source_candidate_digest = None
    if provenance_kind == "bootstrap_design":
        if set(provenance) != {"kind", "design_path", "design_commit"}:
            _invalid()
        design_path = _safe_relative(provenance["design_path"])
        design_commit = provenance["design_commit"]
        if design_path != _BOOTSTRAP_DESIGN_PATH or design_commit != _BOOTSTRAP_DESIGN_COMMIT:
            _invalid()
        try:
            fingerprint = hashlib.sha256(json.dumps(
                raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            _invalid()
        owner = "frontend" if scope == "cross_repo" else scope
        if (
            _BOOTSTRAP_SEEDS.get(knowledge_id) != fingerprint
            or (owner_repository is not None and owner_repository != owner)
            or shared_canonical != (scope == "cross_repo")
        ):
            _invalid()
    else:
        if set(provenance) != {
            "kind", "source_issue", "source_comment_uuid",
            "source_candidate_shas", "source_candidate_digest",
        }:
            _invalid()
        source_issue = provenance["source_issue"]
        source_comment_uuid = _string(provenance["source_comment_uuid"])
        if not isinstance(source_issue, str) or _ISSUE.fullmatch(source_issue) is None:
            _invalid()
        try:
            if str(uuid.UUID(source_comment_uuid)) != source_comment_uuid:
                _invalid()
        except (AttributeError, TypeError, ValueError):
            _invalid()
        source_candidate_shas = _sha_map(provenance["source_candidate_shas"], nonempty=True)
        source_candidate_digest = provenance["source_candidate_digest"]
        if not isinstance(source_candidate_digest, str) or _DIGEST.fullmatch(source_candidate_digest) is None:
            _invalid()

    last_verified_sha = raw["last_verified_sha"]
    content_digest = raw["content_digest"]
    if not isinstance(last_verified_sha, str) or _SHA.fullmatch(last_verified_sha) is None:
        _invalid()
    try:
        date.fromisoformat(raw["last_verified_date"])
    except (TypeError, ValueError):
        _invalid()

    return KnowledgeIndexEntry(
        knowledge_id=knowledge_id,
        title=_string(raw["title"]),
        purpose=_string(raw["purpose"]),
        relative_path=relative_path,
        scope=scope,
        repositories=tuple(sorted(repositories)),
        repository_paths=paths,
        task_types=task_types,
        status=status,
        replacement_id=replacement_id,
        provenance_kind=provenance_kind,
        source_issue=source_issue,
        source_comment_uuid=source_comment_uuid,
        source_candidate_shas=source_candidate_shas,
        source_candidate_digest=source_candidate_digest,
        design_path=design_path,
        design_commit=design_commit,
        last_verified_sha=last_verified_sha,
        last_verified_date=raw["last_verified_date"],
        content_digest=content_digest,
    )


def load_index(index_path: Path | str, root: Path | str, owner_repository: str | None = None) -> tuple[KnowledgeIndexEntry, ...]:
    """Load and verify one knowledge index and every referenced exact byte digest."""
    root_path = Path(root)
    if owner_repository is not None and owner_repository not in _REPOSITORIES:
        _invalid()
    try:
        resolved_root = root_path.resolve(strict=True)
        resolved_index = Path(index_path).resolve(strict=True)
        relative_index = resolved_index.relative_to(resolved_root).as_posix()
    except (OSError, RuntimeError, ValueError):
        _invalid()
    if not resolved_index.is_file():
        _invalid()
    value = _read_yaml(resolved_index)
    if not isinstance(value, dict) or set(value) != {"schema_version", "entries"} or value["schema_version"] != 1:
        _invalid()
    if not isinstance(value["entries"], list):
        _invalid()
    shared_canonical = relative_index == "docs/delivery-knowledge/index.yaml"
    entries = tuple(_parse_entry(item, root_path, owner_repository, shared_canonical) for item in value["entries"])
    by_id = {item.knowledge_id: item for item in entries}
    if len(by_id) != len(entries):
        _invalid()
    for item in entries:
        if item.replacement_id is not None:
            replacement = by_id.get(item.replacement_id)
            if replacement is None or replacement.status != "active" or replacement.knowledge_id == item.knowledge_id:
                _invalid()
    return tuple(sorted(entries, key=lambda item: item.knowledge_id))


def verify_indexes(frontend_root: Path | str, backend_root: Path | str) -> tuple[KnowledgeIndexEntry, ...]:
    """Verify both local indexes and the frontend-owned shared canonical index."""
    frontend = Path(frontend_root)
    backend = Path(backend_root)
    entries = (
        *load_index(frontend / "docs/agent-knowledge/index.yaml", frontend, "frontend"),
        *load_index(frontend / "docs/delivery-knowledge/index.yaml", frontend, "frontend"),
        *load_index(backend / "docs/agent-knowledge/index.yaml", backend, "backend"),
    )
    ids = [item.knowledge_id for item in entries]
    if len(ids) != len(set(ids)):
        _invalid()
    return tuple(sorted(entries, key=lambda item: item.knowledge_id))


@functools.lru_cache(maxsize=256)
def _glob_pattern(pattern: str) -> re.Pattern[str]:
    pieces = ["^"]
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            pieces.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            pieces.append(".*")
            index += 2
        elif pattern[index] == "*":
            pieces.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            pieces.append("[^/]")
            index += 1
        else:
            pieces.append(re.escape(pattern[index]))
            index += 1
    pieces.append("$")
    return re.compile("".join(pieces))


def _path_matches(path: str, pattern: str) -> bool:
    return _glob_pattern(pattern).fullmatch(path) is not None


def _match_reason(entry: KnowledgeIndexEntry, repository: str, task_type: str, paths: Sequence[str]) -> str | None:
    if entry.status != "active" or repository not in entry.repositories or task_type not in entry.task_types:
        return None
    if not paths or not any(_path_matches(path, pattern) for path in paths for pattern in entry.repository_paths):
        return None
    return "repository+task_type+path"


def select_knowledge(
    entries: Iterable[KnowledgeIndexEntry], repository: str, task_type: str, repository_paths: Sequence[str]
) -> tuple[KnowledgeIndexEntry, ...]:
    """Select active entries using only indexed repository, task, and path metadata."""
    if repository not in _REPOSITORIES or not isinstance(task_type, str) or not task_type:
        _invalid()
    if any(PurePosixPath(item).is_absolute() or ".." in PurePosixPath(item).parts for item in repository_paths):
        _invalid()
    selected = [item for item in entries if _match_reason(item, repository, task_type, repository_paths)]
    return tuple(sorted(selected, key=lambda item: item.knowledge_id))


def build_context_receipt(
    task_id: str,
    repository: str,
    task_type: str,
    candidate_shas: Mapping[str, str],
    entries: Iterable[KnowledgeIndexEntry],
    repository_paths: Sequence[str],
    verified_ids: Sequence[str] = (),
    conflicts: Sequence[str] = (),
) -> str:
    """Return one compact canonical Context Receipt JSON object."""
    selected = select_knowledge(entries, repository, task_type, repository_paths)
    shas = _sha_map(dict(candidate_shas), nonempty=True)
    selected_ids = {item.knowledge_id for item in selected}
    if (
        len(verified_ids) != len(set(verified_ids))
        or set(verified_ids) - selected_ids
        or not all(isinstance(item, str) and item for item in conflicts)
    ):
        raise ValueError("invalid context receipt")
    verified_set = set(verified_ids)
    # Historical freshness is not an agent's task-time verification receipt.
    verified = tuple(item.knowledge_id for item in selected if item.knowledge_id in verified_set)
    receipt = ContextReceipt(
        schema_version=1,
        task_id=_string(task_id),
        repository=repository,
        task_type=task_type,
        candidate_shas=shas,
        knowledge_ids=tuple(item.knowledge_id for item in selected),
        knowledge_digests=tuple((item.knowledge_id, item.content_digest) for item in selected),
        match_reasons=tuple((item.knowledge_id, _match_reason(item, repository, task_type, repository_paths) or "") for item in selected),
        verified_ids=verified,
        conflicts=tuple(conflicts),
    )
    payload = asdict(receipt)
    payload["candidate_shas"] = dict(receipt.candidate_shas)
    payload["knowledge_digests"] = dict(receipt.knowledge_digests)
    payload["match_reasons"] = dict(receipt.match_reasons)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _candidate_from_input(text: str) -> KnowledgeCandidate:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("invalid knowledge candidate") from None
    if not isinstance(value, dict):
        raise ValueError("invalid knowledge candidate")
    if "digest" not in value:
        value = dict(value)
        value["digest"] = candidate_digest(value)
    candidate = parse_candidate_json(json.dumps(value, ensure_ascii=False))
    if "```" in candidate_json(candidate):
        raise ValueError("invalid knowledge candidate")
    return candidate


def render_candidate_block(text: str) -> str:
    """Validate candidate JSON and render its one canonical evidence block."""
    value = _candidate_from_input(text)
    return f"```{_CANDIDATE_FENCE}\n{candidate_json(value)}\n```"


def extract_candidate_blocks(text: str) -> tuple[KnowledgeCandidate, ...]:
    """Extract exactly one non-nested versioned candidate from evidence."""
    if not isinstance(text, str) or text.count(f"```{_CANDIDATE_FENCE}") != 1:
        raise ValueError("invalid knowledge candidate")
    matches = tuple(_CANDIDATE_BLOCK.finditer(text))
    if len(matches) != 1:
        raise ValueError("invalid knowledge candidate")
    body = matches[0].group("body")
    if "```" in body:
        raise ValueError("invalid knowledge candidate")
    return (_candidate_from_input(body),)


def _canonical_uuid(value: Any) -> str:
    try:
        parsed = str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("invalid knowledge summary") from None
    if parsed != value:
        raise ValueError("invalid knowledge summary")
    return parsed


def render_summary_pointer(
    child_identifier: str, evidence_comment_uuid: str, digest: str
) -> str:
    """Render the only machine-readable parent-to-evidence pointer."""
    if (
        not isinstance(child_identifier, str)
        or _ISSUE.fullmatch(child_identifier) is None
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
    ):
        raise ValueError("invalid knowledge summary")
    pointer = {
        "schema_version": 1,
        "child_identifier": child_identifier,
        "evidence_comment_uuid": _canonical_uuid(evidence_comment_uuid),
        "candidate_digest": digest,
    }
    body = json.dumps(pointer, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"```{_SUMMARY_FENCE}\n{body}\n```"


def extract_summary_pointer(text: str) -> KnowledgeSummaryPointer:
    """Extract exactly one non-nested summary pointer without candidate prose."""
    if not isinstance(text, str) or text.count(f"```{_SUMMARY_FENCE}") != 1:
        raise ValueError("invalid knowledge summary")
    matches = tuple(_SUMMARY_BLOCK.finditer(text))
    if len(matches) != 1 or "```" in matches[0].group("body"):
        raise ValueError("invalid knowledge summary")
    try:
        value = json.loads(matches[0].group("body"))
    except json.JSONDecodeError:
        raise ValueError("invalid knowledge summary") from None
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "child_identifier", "evidence_comment_uuid",
        "candidate_digest",
    } or value.get("schema_version") != 1:
        raise ValueError("invalid knowledge summary")
    child_identifier = value.get("child_identifier")
    digest = value.get("candidate_digest")
    if (
        not isinstance(child_identifier, str)
        or _ISSUE.fullmatch(child_identifier) is None
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
    ):
        raise ValueError("invalid knowledge summary")
    return KnowledgeSummaryPointer(
        schema_version=1,
        child_identifier=child_identifier,
        evidence_comment_uuid=_canonical_uuid(value.get("evidence_comment_uuid")),
        candidate_digest=digest,
    )


def _string_metadata_filter(key: str, value: str) -> str:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="").writerow(
        [f"{key}={json.dumps(value)}"]
    )
    return buffer.getvalue()


def _project_map(value: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    if (
        not isinstance(value, Mapping)
        or set(value) != _REPOSITORIES
        or not all(isinstance(item, str) and item for item in value.values())
        or len(set(value.values())) != 2
    ):
        raise ValueError("invalid knowledge project mapping")
    return tuple((key, value[key]) for key in ("frontend", "backend"))


def list_pending_parents(
    runner: MulticaRunner, project_ids: Mapping[str, str]
) -> tuple[KnowledgeParentRef, ...]:
    """List pending terminal top-level parents from exactly two Projects."""
    configured = _project_map(project_ids)
    records: dict[str, dict[str, Any]] = {}
    identifiers: dict[str, str] = {}
    filters = (
        _string_metadata_filter("eventra.knowledge.version", "1"),
        _string_metadata_filter("eventra.knowledge.status", "pending"),
    )
    for _, project_id in configured:
        for status in ("done", "blocked"):
            offset = 0
            while True:
                raw = runner.run(
                    [
                        "issue", "list",
                        "--project", project_id,
                        "--status", status,
                        "--metadata", filters[0],
                        "--metadata", filters[1],
                        "--limit", "50",
                        "--offset", str(offset),
                        "--output", "json",
                    ]
                )
                try:
                    page = parse_issue_list(raw, project_id)
                except RuntimeError:
                    raise RuntimeError("malformed knowledge parent list") from None
                if page["limit"] != 50 or page["offset"] != offset:
                    raise RuntimeError("malformed knowledge parent list")
                for issue in page["issues"]:
                    if issue["status"] != status:
                        raise RuntimeError("malformed knowledge parent list")
                    if issue["parent_issue_id"] is not None:
                        continue
                    issue_id = str(issue["id"])
                    identifier = str(issue["identifier"])
                    previous = records.get(issue_id)
                    if previous is not None and previous != issue:
                        raise RuntimeError("malformed knowledge parent list")
                    previous_id = identifiers.get(identifier)
                    if previous_id is not None and previous_id != issue_id:
                        raise RuntimeError("malformed knowledge parent list")
                    records[issue_id] = issue
                    identifiers[identifier] = issue_id
                if not page["has_more"]:
                    break
                if not page["issues"]:
                    raise RuntimeError("malformed knowledge parent list")
                offset += len(page["issues"])
    ordered = sorted(
        records.values(),
        key=lambda item: (str(item["updated_at"]), str(item["identifier"])),
    )
    return tuple(
        KnowledgeParentRef(
            issue_id=str(item["id"]),
            identifier=str(item["identifier"]),
            project_id=str(item["project_id"]),
            status=item["status"],
            updated_at=str(item["updated_at"]),
        )
        for item in ordered
    )


def _evidence_detail(value: Any, identifier: str) -> dict[str, Any]:
    try:
        return parse_issue_detail(value, identifier)
    except RuntimeError:
        raise RuntimeError("invalid knowledge evidence") from None


def _evidence_comments(value: Any, identifier: str) -> list[dict[str, Any]]:
    try:
        return parse_issue_comments(value, identifier)
    except RuntimeError:
        raise RuntimeError("invalid knowledge evidence") from None


def _target_comment(
    comments: Sequence[Mapping[str, Any]], comment_uuid: str
) -> Mapping[str, Any]:
    matches = [item for item in comments if item.get("id") == comment_uuid]
    if len(matches) != 1:
        raise RuntimeError("invalid knowledge evidence")
    return matches[0]


def load_candidate_snapshot(
    runner: MulticaRunner,
    parent_ref: KnowledgeParentRef,
    project_ids: Mapping[str, str],
) -> KnowledgeParentSnapshot:
    """Follow a parent summary pointer back to the original candidate comment."""
    configured = _project_map(project_ids)
    parent = _evidence_detail(
        runner.run(["issue", "get", parent_ref.identifier, "--output", "json"]),
        parent_ref.identifier,
    )
    if (
        parent["id"] != parent_ref.issue_id
        or parent["project_id"] != parent_ref.project_id
        or parent["status"] != parent_ref.status
        or parent["updated_at"] != parent_ref.updated_at
        or parent["parent_issue_id"] is not None
        or parent["stage"] is not None
        or parent["status"] not in {"done", "blocked"}
    ):
        raise RuntimeError("invalid knowledge evidence")

    try:
        metadata = parse_issue_metadata(
            runner.run(
                ["issue", "metadata", "list", parent_ref.identifier, "--output", "json"]
            )
        )
    except RuntimeError:
        raise RuntimeError("invalid knowledge evidence") from None
    knowledge_metadata = {
        key: item for key, item in metadata.items()
        if key.startswith("eventra.knowledge.")
    }
    required = {
        "eventra.knowledge.version",
        "eventra.knowledge.status",
        "eventra.knowledge.summary_comment",
        "eventra.knowledge.candidate_digest",
    }
    dispatch_keys = required | {"eventra.knowledge.dispatch"}
    resolution_keys = required | {"eventra.knowledge.resolution"}
    if set(knowledge_metadata) not in (required, dispatch_keys, resolution_keys):
        raise RuntimeError("invalid knowledge evidence")
    summary_uuid = knowledge_metadata["eventra.knowledge.summary_comment"]
    metadata_digest = knowledge_metadata["eventra.knowledge.candidate_digest"]
    try:
        canonical_summary_uuid = _canonical_uuid(summary_uuid)
    except ValueError:
        raise RuntimeError("invalid knowledge evidence") from None
    if (
        knowledge_metadata["eventra.knowledge.version"] != "1"
        or knowledge_metadata["eventra.knowledge.status"] != "pending"
        or _DIGEST.fullmatch(metadata_digest) is None
    ):
        raise RuntimeError("invalid knowledge evidence")

    summary_comments = _evidence_comments(
        runner.run(
            [
                "issue", "comment", "list", parent_ref.identifier,
                "--thread", canonical_summary_uuid,
                "--tail", "30", "--compact", "--output", "json",
            ]
        ),
        parent_ref.identifier,
    )
    summary_comment = _target_comment(summary_comments, canonical_summary_uuid)
    try:
        pointer = extract_summary_pointer(str(summary_comment["content"]))
    except ValueError:
        raise RuntimeError("invalid knowledge evidence") from None
    if pointer.candidate_digest != metadata_digest:
        raise RuntimeError("invalid knowledge evidence")

    child = _evidence_detail(
        runner.run(
            ["issue", "get", pointer.child_identifier, "--output", "json"]
        ),
        pointer.child_identifier,
    )
    if (
        child["parent_issue_id"] != parent_ref.issue_id
        or child["project_id"] not in dict(configured).values()
    ):
        raise RuntimeError("invalid knowledge evidence")
    evidence_comments = _evidence_comments(
        runner.run(
            [
                "issue", "comment", "list", pointer.child_identifier,
                "--thread", pointer.evidence_comment_uuid,
                "--tail", "30", "--compact", "--output", "json",
            ]
        ),
        pointer.child_identifier,
    )
    evidence_comment = _target_comment(
        evidence_comments, pointer.evidence_comment_uuid
    )
    candidate_comments = [
        item for item in evidence_comments
        if f"```{_CANDIDATE_FENCE}" in str(item["content"])
    ]
    if len(candidate_comments) != 1:
        raise RuntimeError("invalid knowledge evidence")
    candidate_comment = candidate_comments[0]
    comments_by_id = {item["id"]: item for item in evidence_comments}
    ancestor_id = candidate_comment["id"]
    while ancestor_id != pointer.evidence_comment_uuid:
        ancestor = comments_by_id.get(ancestor_id)
        if ancestor is None or ancestor.get("parent_id") is None:
            raise RuntimeError("invalid knowledge evidence")
        ancestor_id = ancestor["parent_id"]
    if (
        evidence_comment["author_type"] != "agent"
        or candidate_comment["author_type"] != "agent"
        or candidate_comment["author_id"] != evidence_comment["author_id"]
        or candidate_comment["author_type"] != evidence_comment["author_type"]
    ):
        raise RuntimeError("invalid knowledge evidence")
    try:
        candidate = extract_candidate_blocks(str(candidate_comment["content"]))[0]
    except ValueError:
        raise RuntimeError("invalid knowledge evidence") from None
    if (
        candidate.digest != metadata_digest
        or candidate.evidence.parent_identifier != parent_ref.identifier
        or candidate.evidence.child_identifier != pointer.child_identifier
        or candidate.evidence.comment_uuid != pointer.evidence_comment_uuid
        or candidate.evidence.project_id != child["project_id"]
    ):
        raise RuntimeError("invalid knowledge evidence")
    return KnowledgeParentSnapshot(
        issue_id=parent_ref.issue_id,
        identifier=parent_ref.identifier,
        project_id=parent_ref.project_id,
        status=parent_ref.status,
        updated_at=parent_ref.updated_at,
        project_ids=configured,
        summary_comment_uuid=canonical_summary_uuid,
        candidate_comment_uuid=pointer.evidence_comment_uuid,
        candidate=candidate,
        dispatch_record=knowledge_metadata.get("eventra.knowledge.dispatch"),
        resolution_record=knowledge_metadata.get("eventra.knowledge.resolution"),
    )


def decide_curation(
    snapshot: KnowledgeParentSnapshot,
    entries: Iterable[KnowledgeIndexEntry],
) -> CurationDecision:
    """Return a deterministic pure decision for one verified snapshot."""
    candidate = snapshot.candidate
    project_ids = dict(snapshot.project_ids)
    sha_keys = {key for key, _ in candidate.candidate_shas}
    valid_scope = (
        candidate.sensitivity == "public_repo"
        and (
            (candidate.target_scope in {"frontend", "backend"}
             and candidate.target_repository == candidate.target_scope
             and sha_keys == {candidate.target_scope})
            or (
                candidate.target_scope == "cross_repo"
                and candidate.target_repository == "frontend"
                and sha_keys == _REPOSITORIES
            )
        )
    )
    if not valid_scope or candidate.target_repository not in project_ids:
        return CurationDecision(
            "rejected", candidate.digest, None, None,
            candidate.target_repository, "candidate scope or SHA coverage is invalid",
        )
    ordered_entries = tuple(entries)
    duplicate = next(
        (
            item for item in sorted(ordered_entries, key=lambda item: item.knowledge_id)
            if item.source_candidate_digest == candidate.digest
        ),
        None,
    )
    if duplicate is not None:
        return CurationDecision(
            "deduplicated", candidate.digest, None,
            project_ids[candidate.target_repository],
            candidate.target_repository, "candidate digest already indexed",
            duplicate.knowledge_id,
        )
    known_ids = {item.knowledge_id for item in ordered_entries}
    if set(candidate.related_knowledge_ids) - known_ids:
        return CurationDecision(
            "needs_human", candidate.digest, None,
            project_ids[candidate.target_repository],
            candidate.target_repository, "related knowledge reference is unresolved",
        )
    target_project_id = project_ids[candidate.target_repository]
    action_payload = {
        "candidate_digest": candidate.digest,
        "knowledge_type": candidate.knowledge_type,
        "parent_identifier": snapshot.identifier,
        "target_project_id": target_project_id,
        "target_repository": candidate.target_repository,
    }
    action_key = hashlib.sha256(
        json.dumps(
            action_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    return CurationDecision(
        "create_issue", candidate.digest, action_key, target_project_id,
        candidate.target_repository, "candidate is valid and not indexed",
    )


def stable_curation_decision(
    snapshot_loader: Callable[[], KnowledgeParentSnapshot],
    entries: Iterable[KnowledgeIndexEntry],
) -> CurationDecision:
    """Plan only when two authoritative snapshots and decisions are identical."""
    frozen_entries = tuple(entries)
    first = snapshot_loader()
    first_decision = decide_curation(first, frozen_entries)
    try:
        second = snapshot_loader()
    except RuntimeError:
        return CurationDecision(
            "needs_human", None, None, None, None,
            "knowledge evidence changed during planning",
        )
    second_decision = decide_curation(second, frozen_entries)
    if first != second or first_decision != second_decision:
        return CurationDecision(
            "needs_human", None, None, None, None,
            "knowledge evidence changed during planning",
        )
    return second_decision


def _curation_result(
    runner: MulticaRunner,
    starting_mutations: int,
    decision: str,
    *,
    parent_identifier: str | None = None,
    knowledge_issue_identifier: str | None = None,
    action_key: str | None = None,
    created: int = 0,
    reason: str,
) -> CurationRunResult:
    return CurationRunResult(
        decision=decision,
        parent_identifier=parent_identifier,
        knowledge_issue_identifier=knowledge_issue_identifier,
        action_key=action_key,
        created=created,
        mutation_count=runner.mutation_count - starting_mutations,
        reason=reason,
    )


def _knowledge_issue_title(action_key: str) -> str:
    return f"Eventra knowledge curation {action_key}"


def _knowledge_issue_description(
    snapshot: KnowledgeParentSnapshot, decision: CurationDecision
) -> str:
    shas = "\n".join(
        f"- {repository}={sha}"
        for repository, sha in snapshot.candidate.candidate_shas
    )
    return (
        "# Eventra repository knowledge curation\n\n"
        f"eventra-knowledge-action-key: {decision.action_key}\n"
        f"source-parent: {snapshot.identifier}\n"
        f"source-summary-comment: {snapshot.summary_comment_uuid}\n"
        f"source-evidence-comment: {snapshot.candidate_comment_uuid}\n"
        f"candidate-digest: {snapshot.candidate.digest}\n"
        f"target-repository: {decision.target_repository}\n"
        f"knowledge-type: {snapshot.candidate.knowledge_type}\n"
        "candidate-shas:\n"
        f"{shas}\n\n"
        "Follow the source evidence, verify it against the exact candidate SHA, "
        "and use only the repository knowledge allowlist. Do not modify business "
        "code, merge, deploy, or copy unrestricted source output.\n"
    )


def _issue_description_matches(raw_description: Any, expected: str) -> bool:
    """Match the server's lossless body form apart from terminal newlines."""
    return (
        isinstance(raw_description, str)
        and raw_description.rstrip("\r\n") == expected.rstrip("\r\n")
    )


def _action_issue_matches(
    raw: Any,
    normalized: Mapping[str, Any],
    *,
    project_id: str,
    curator_agent_id: str,
    title: str,
    description: str,
) -> bool:
    return (
        isinstance(raw, dict)
        and raw.get("id") == normalized["id"]
        and raw.get("identifier") == normalized["identifier"]
        and raw.get("title") == title
        and _issue_description_matches(raw.get("description"), description)
        and normalized["project_id"] == project_id
        and normalized["assignee_id"] == curator_agent_id
        and normalized["assignee_type"] == "agent"
        and normalized["parent_issue_id"] is None
        and normalized["stage"] is None
    )


def _find_action_issues(
    runner: MulticaRunner,
    *,
    project_id: str,
    curator_agent_id: str,
    title: str,
    description: str,
) -> tuple[dict[str, Any], ...]:
    matches: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        raw = runner.run(
            [
                "issue", "list",
                "--project", project_id,
                "--assignee-id", curator_agent_id,
                "--limit", "50",
                "--offset", str(offset),
                "--output", "json",
            ]
        )
        try:
            page = parse_issue_list(raw, project_id)
        except RuntimeError:
            raise RuntimeError("malformed knowledge action search") from None
        if page["limit"] != 50 or page["offset"] != offset:
            raise RuntimeError("malformed knowledge action search")
        raw_issues = raw.get("issues") if isinstance(raw, dict) else None
        if not isinstance(raw_issues, list) or len(raw_issues) != len(page["issues"]):
            raise RuntimeError("malformed knowledge action search")
        for raw_issue, issue in zip(raw_issues, page["issues"], strict=True):
            if _action_issue_matches(
                raw_issue,
                issue,
                project_id=project_id,
                curator_agent_id=curator_agent_id,
                title=title,
                description=description,
            ):
                matches[issue["id"]] = dict(issue)
        if not page["has_more"]:
            break
        if not page["issues"]:
            raise RuntimeError("malformed knowledge action search")
        offset += len(page["issues"])
    return tuple(
        sorted(matches.values(), key=lambda item: (item["updated_at"], item["identifier"]))
    )


def _load_action_issue(
    runner: MulticaRunner,
    identifier: str,
    *,
    project_id: str,
    curator_agent_id: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    raw = runner.run(["issue", "get", identifier, "--output", "json"])
    try:
        issue = parse_issue_detail(raw, identifier)
    except RuntimeError:
        raise RuntimeError("invalid knowledge action issue") from None
    if not _action_issue_matches(
        raw,
        issue,
        project_id=project_id,
        curator_agent_id=curator_agent_id,
        title=title,
        description=description,
    ):
        raise RuntimeError("invalid knowledge action issue")
    return issue


def _transition_record(
    snapshot: KnowledgeParentSnapshot,
    decision: CurationDecision,
    knowledge_issue_identifier: str,
    target_status: Literal["issue_created", "dispatched"],
) -> str:
    payload = {
        "schema_version": 1,
        "source_status": "pending",
        "target_status": target_status,
        "candidate_digest": snapshot.candidate.digest,
        "action_key": decision.action_key,
        "object_identifier": knowledge_issue_identifier,
        "source_parent": snapshot.identifier,
        "summary_comment_uuid": snapshot.summary_comment_uuid,
        "evidence_comment_uuid": snapshot.candidate_comment_uuid,
        "target_repository": decision.target_repository,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _resolution_record(
    snapshot: KnowledgeParentSnapshot,
    decision: CurationDecision,
) -> str:
    payload = {
        "schema_version": 1,
        "source_status": "pending",
        "target_status": decision.kind,
        "candidate_digest": snapshot.candidate.digest,
        "action_key": decision.action_key,
        "source_parent": snapshot.identifier,
        "summary_comment_uuid": snapshot.summary_comment_uuid,
        "evidence_comment_uuid": snapshot.candidate_comment_uuid,
        "target_repository": decision.target_repository,
        "existing_knowledge_id": decision.existing_knowledge_id,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _dispatch_identifier(
    value: str,
    snapshot: KnowledgeParentSnapshot,
    decision: CurationDecision,
) -> str:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError("invalid knowledge transition") from None
    required = {
        "schema_version", "source_status", "target_status",
        "candidate_digest", "action_key", "object_identifier",
        "source_parent", "summary_comment_uuid", "evidence_comment_uuid",
        "target_repository",
    }
    identifier = payload.get("object_identifier") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) != value
        or payload.get("schema_version") != 1
        or payload.get("source_status") != "pending"
        or payload.get("target_status") != "dispatched"
        or payload.get("candidate_digest") != snapshot.candidate.digest
        or payload.get("action_key") != decision.action_key
        or not isinstance(identifier, str)
        or _ISSUE.fullmatch(identifier) is None
        or payload.get("source_parent") != snapshot.identifier
        or payload.get("summary_comment_uuid") != snapshot.summary_comment_uuid
        or payload.get("evidence_comment_uuid") != snapshot.candidate_comment_uuid
        or payload.get("target_repository") != decision.target_repository
    ):
        raise RuntimeError("invalid knowledge transition")
    return identifier


def _load_metadata(runner: MulticaRunner, identifier: str) -> dict[str, str]:
    try:
        return parse_issue_metadata(
            runner.run(["issue", "metadata", "list", identifier, "--output", "json"])
        )
    except RuntimeError:
        raise RuntimeError("invalid knowledge transition") from None


def _set_string_metadata(
    runner: MulticaRunner, identifier: str, key: str, value: str
) -> None:
    runner.run(
        [
            "issue", "metadata", "set", identifier,
            "--key", key,
            "--value", value,
            "--type", "string",
            "--output", "json",
        ]
    )


def _controlled_knowledge_metadata(metadata: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in metadata.items()
        if key.startswith("eventra.knowledge.")
    }


def _controlled_issue_transition_metadata(
    metadata: Mapping[str, str],
    *,
    candidate_digest_value: str,
    target_repository: str,
) -> dict[str, str]:
    controlled = _controlled_knowledge_metadata(metadata)
    allowed = {"eventra.knowledge.transition", "eventra.knowledge.pr"}
    if not set(controlled).issubset(allowed):
        raise RuntimeError("invalid knowledge transition")
    pr_record = controlled.get("eventra.knowledge.pr")
    if pr_record is not None:
        try:
            payload = json.loads(pr_record)
        except (TypeError, json.JSONDecodeError):
            raise RuntimeError("invalid knowledge transition") from None
        required = {
            "repository", "candidate_digest", "url", "source_branch",
            "head_sha", "status", "merge_sha",
        }
        repository = payload.get("repository") if isinstance(payload, dict) else None
        digest = payload.get("candidate_digest") if isinstance(payload, dict) else None
        branch = payload.get("source_branch") if isinstance(payload, dict) else None
        head_sha = payload.get("head_sha") if isinstance(payload, dict) else None
        status = payload.get("status") if isinstance(payload, dict) else None
        merge_sha = payload.get("merge_sha") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or set(payload) != required
            or json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ) != pr_record
            or repository != target_repository
            or digest != candidate_digest_value
            or _DIGEST.fullmatch(digest) is None
            or branch != f"eventra-knowledge/{digest}"
            or not isinstance(head_sha, str)
            or _SHA.fullmatch(head_sha) is None
            or not _knowledge_pr_url(repository, payload.get("url"))
            or status
            not in {"pr_open", "rejected", "merged", "verified", "needs_human"}
            or (status in {"merged", "verified"} and merge_sha is None)
            or (status in {"pr_open", "rejected"} and merge_sha is not None)
            or (
                merge_sha is not None
                and (
                    not isinstance(merge_sha, str)
                    or _SHA.fullmatch(merge_sha) is None
                )
            )
        ):
            raise RuntimeError("invalid knowledge transition")
    return {
        key: value
        for key, value in controlled.items()
        if key == "eventra.knowledge.transition"
    }


def _default_backend_root(frontend_root: Path) -> Path:
    resolved = frontend_root.resolve()
    if resolved.parent.name == ".worktrees":
        resolved = resolved.parent.parent
    return resolved.parent / "Eventra-Backend"


def _record_parent_resolution(
    runner: MulticaRunner,
    snapshot: KnowledgeParentSnapshot,
    decision: CurationDecision,
) -> None:
    if decision.kind not in {"deduplicated", "rejected", "needs_human"}:
        raise RuntimeError("invalid knowledge transition")
    resolution = _resolution_record(snapshot, decision)
    base = {
        "eventra.knowledge.version": "1",
        "eventra.knowledge.status": "pending",
        "eventra.knowledge.summary_comment": snapshot.summary_comment_uuid,
        "eventra.knowledge.candidate_digest": snapshot.candidate.digest,
    }
    expected_pending = {
        **base,
        "eventra.knowledge.resolution": resolution,
    }
    expected_terminal = {
        **expected_pending,
        "eventra.knowledge.status": decision.kind,
    }
    controlled = _controlled_knowledge_metadata(
        _load_metadata(runner, snapshot.identifier)
    )
    if controlled not in (base, expected_pending, expected_terminal):
        raise RuntimeError("invalid knowledge transition")
    if controlled == base:
        _set_string_metadata(
            runner,
            snapshot.identifier,
            "eventra.knowledge.resolution",
            resolution,
        )
        controlled = _controlled_knowledge_metadata(
            _load_metadata(runner, snapshot.identifier)
        )
    if controlled == expected_pending:
        _set_string_metadata(
            runner,
            snapshot.identifier,
            "eventra.knowledge.status",
            decision.kind,
        )
    if _controlled_knowledge_metadata(
        _load_metadata(runner, snapshot.identifier)
    ) != expected_terminal:
        raise RuntimeError("invalid knowledge transition")


def curate_once(
    runner: MulticaRunner,
    project_ids: Mapping[str, str],
    curator_agent_id: str,
    apply: bool,
    *,
    frontend_root: Path | None = None,
    backend_root: Path | None = None,
) -> CurationRunResult:
    """Plan or dispatch at most one verified Eventra knowledge candidate."""
    starting_mutations = runner.mutation_count
    if not isinstance(apply, bool):
        raise TypeError("apply must be a bool")
    try:
        canonical_curator_id = _canonical_uuid(curator_agent_id)
        configured = dict(_project_map(project_ids))
        resolved_frontend_root = (
            Path.cwd() if frontend_root is None else frontend_root
        )
        resolved_backend_root = (
            _default_backend_root(resolved_frontend_root)
            if backend_root is None
            else backend_root
        )
        entries = verify_indexes(resolved_frontend_root, resolved_backend_root)
        refs = list_pending_parents(runner, configured)
    except (RuntimeError, ValueError):
        return _curation_result(
            runner, starting_mutations, "needs_human",
            reason="knowledge scan or index verification failed",
        )
    if not refs:
        return _curation_result(
            runner, starting_mutations, "noop",
            reason="no pending knowledge parent",
        )
    parent_ref = refs[0]
    try:
        snapshots: list[KnowledgeParentSnapshot] = []

        def load_snapshot() -> KnowledgeParentSnapshot:
            value = load_candidate_snapshot(runner, parent_ref, configured)
            snapshots.append(value)
            return value

        decision = stable_curation_decision(
            load_snapshot,
            entries,
        )
        snapshot = snapshots[-1]
    except RuntimeError:
        return _curation_result(
            runner, starting_mutations, "needs_human",
            parent_identifier=parent_ref.identifier,
            reason="knowledge evidence is invalid or changed",
        )
    if decision.kind != "create_issue":
        if (
            apply
            and decision.candidate_digest == snapshot.candidate.digest
            and decision.kind in {"deduplicated", "rejected", "needs_human"}
        ):
            try:
                _record_parent_resolution(runner, snapshot, decision)
            except RuntimeError:
                return _curation_result(
                    runner, starting_mutations, "needs_human",
                    parent_identifier=snapshot.identifier,
                    action_key=decision.action_key,
                    reason="knowledge resolution could not be verified",
                )
        return _curation_result(
            runner, starting_mutations, decision.kind,
            parent_identifier=snapshot.identifier,
            action_key=decision.action_key,
            reason=decision.reason,
        )
    if decision.action_key is None or decision.target_project_id is None:
        return _curation_result(
            runner, starting_mutations, "needs_human",
            parent_identifier=snapshot.identifier,
            reason="knowledge decision is incomplete",
        )
    dispatch_identifier = None
    if snapshot.dispatch_record is not None:
        try:
            dispatch_identifier = _dispatch_identifier(
                snapshot.dispatch_record, snapshot, decision
            )
        except RuntimeError:
            return _curation_result(
                runner, starting_mutations, "needs_human",
                parent_identifier=snapshot.identifier,
                action_key=decision.action_key,
                reason="pending knowledge dispatch record is invalid",
            )
    title = _knowledge_issue_title(decision.action_key)
    description = _knowledge_issue_description(snapshot, decision)
    try:
        matches = _find_action_issues(
            runner,
            project_id=decision.target_project_id,
            curator_agent_id=canonical_curator_id,
            title=title,
            description=description,
        )
    except RuntimeError:
        return _curation_result(
            runner, starting_mutations, "needs_human",
            parent_identifier=snapshot.identifier,
            action_key=decision.action_key,
            reason="knowledge action search failed",
        )
    if len(matches) > 1:
        return _curation_result(
            runner, starting_mutations, "needs_human",
            parent_identifier=snapshot.identifier,
            action_key=decision.action_key,
            reason="multiple knowledge action Issues matched",
        )
    if dispatch_identifier is not None and (
        len(matches) != 1 or matches[0]["identifier"] != dispatch_identifier
    ):
        return _curation_result(
            runner, starting_mutations, "needs_human",
            parent_identifier=snapshot.identifier,
            action_key=decision.action_key,
            reason="pending knowledge dispatch does not match an authoritative Issue",
        )
    if not apply:
        return _curation_result(
            runner, starting_mutations, "create_issue",
            parent_identifier=snapshot.identifier,
            knowledge_issue_identifier=(matches[0]["identifier"] if matches else None),
            action_key=decision.action_key,
            reason=(
                "matching knowledge Issue already exists"
                if matches
                else decision.reason
            ),
        )

    issue = matches[0] if matches else None
    created = 0
    if issue is None:
        description_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=".eventra-knowledge-",
                suffix=".md",
                dir=Path.cwd(),
                delete=False,
            ) as handle:
                handle.write(description)
                description_path = Path(handle.name)
            raw_created = runner.run(
                [
                    # The deterministic action-key title plus Multica's default
                    # active-duplicate rejection is the server-side uniqueness
                    # boundary. Never add --allow-duplicate here; the recovery
                    # search below handles the concurrent winner or a lost ack.
                    "issue", "create",
                    "--title", title,
                    "--description-file", str(description_path),
                    "--assignee-id", canonical_curator_id,
                    "--project", decision.target_project_id,
                    "--output", "json",
                ]
            )
            identifier = (
                raw_created.get("identifier")
                if isinstance(raw_created, dict)
                else None
            )
            if not isinstance(identifier, str):
                raise RuntimeError("invalid knowledge action issue")
            issue = _load_action_issue(
                runner,
                identifier,
                project_id=decision.target_project_id,
                curator_agent_id=canonical_curator_id,
                title=title,
                description=description,
            )
            created = 1
        except (OSError, RuntimeError):
            try:
                recovered = _find_action_issues(
                    runner,
                    project_id=decision.target_project_id,
                    curator_agent_id=canonical_curator_id,
                    title=title,
                    description=description,
                )
            except RuntimeError:
                recovered = ()
            if len(recovered) == 1:
                issue = recovered[0]
                created = 1
            else:
                return _curation_result(
                    runner, starting_mutations, "needs_human",
                    parent_identifier=snapshot.identifier,
                    action_key=decision.action_key,
                    reason="knowledge Issue creation acknowledgement was ambiguous",
                )
        finally:
            if description_path is not None:
                try:
                    description_path.unlink(missing_ok=True)
                except OSError:
                    pass
    if issue is None:
        return _curation_result(
            runner, starting_mutations, "needs_human",
            parent_identifier=snapshot.identifier,
            action_key=decision.action_key,
            reason="knowledge Issue was not established",
        )
    identifier = issue["identifier"]
    try:
        authoritative_issue = _load_action_issue(
            runner,
            identifier,
            project_id=decision.target_project_id,
            curator_agent_id=canonical_curator_id,
            title=title,
            description=description,
        )
        if authoritative_issue["id"] != issue["id"]:
            raise RuntimeError("invalid knowledge action issue")
        issue_transition = _transition_record(
            snapshot, decision, identifier, "issue_created"
        )
        target_metadata = _controlled_issue_transition_metadata(
            _load_metadata(runner, identifier),
            candidate_digest_value=snapshot.candidate.digest,
            target_repository=decision.target_repository,
        )
        expected_target = {"eventra.knowledge.transition": issue_transition}
        if target_metadata not in ({}, expected_target):
            raise RuntimeError("invalid knowledge transition")
        if not target_metadata:
            _set_string_metadata(
                runner,
                identifier,
                "eventra.knowledge.transition",
                issue_transition,
            )
        if _controlled_issue_transition_metadata(
            _load_metadata(runner, identifier),
            candidate_digest_value=snapshot.candidate.digest,
            target_repository=decision.target_repository,
        ) != expected_target:
            raise RuntimeError("invalid knowledge transition")

        current_snapshot = load_candidate_snapshot(runner, parent_ref, configured)
        current_decision = decide_curation(current_snapshot, entries)
        if current_snapshot != snapshot or current_decision != decision:
            raise RuntimeError("invalid knowledge transition")
        dispatch_record = _transition_record(
            snapshot, decision, identifier, "dispatched"
        )
        parent_metadata = _load_metadata(runner, snapshot.identifier)
        controlled_parent = _controlled_knowledge_metadata(parent_metadata)
        base_parent = {
            "eventra.knowledge.version": "1",
            "eventra.knowledge.status": "pending",
            "eventra.knowledge.summary_comment": snapshot.summary_comment_uuid,
            "eventra.knowledge.candidate_digest": snapshot.candidate.digest,
        }
        expected_pending = {
            **base_parent,
            "eventra.knowledge.dispatch": dispatch_record,
        }
        expected_dispatched = {
            **expected_pending,
            "eventra.knowledge.status": "dispatched",
        }
        if controlled_parent not in (base_parent, expected_pending, expected_dispatched):
            raise RuntimeError("invalid knowledge transition")
        if controlled_parent == base_parent:
            _set_string_metadata(
                runner,
                snapshot.identifier,
                "eventra.knowledge.dispatch",
                dispatch_record,
            )
            controlled_parent = _controlled_knowledge_metadata(
                _load_metadata(runner, snapshot.identifier)
            )
        if controlled_parent == expected_pending:
            _set_string_metadata(
                runner,
                snapshot.identifier,
                "eventra.knowledge.status",
                "dispatched",
            )
        if _controlled_knowledge_metadata(
            _load_metadata(runner, snapshot.identifier)
        ) != expected_dispatched:
            raise RuntimeError("invalid knowledge transition")
    except RuntimeError:
        return _curation_result(
            runner, starting_mutations, "needs_human",
            parent_identifier=snapshot.identifier,
            knowledge_issue_identifier=identifier,
            action_key=decision.action_key,
            created=created,
            reason="knowledge transition could not be verified",
        )
    return _curation_result(
        runner, starting_mutations, "dispatched",
        parent_identifier=snapshot.identifier,
        knowledge_issue_identifier=identifier,
        action_key=decision.action_key,
        created=created,
        reason="knowledge Issue was verified and parent was dispatched",
    )


def _invalid_knowledge_change() -> None:
    raise ValueError("invalid knowledge change")


def verify_knowledge_change(
    repository: str,
    changed_paths: Iterable[str],
    staged_text: str,
    *,
    repository_root: Path | None = None,
    frontend_root: Path | None = None,
    backend_root: Path | None = None,
) -> tuple[str, ...]:
    """Validate a documentation-only knowledge diff without echoing its text."""
    if repository not in _REPOSITORIES or not isinstance(staged_text, str):
        _invalid_knowledge_change()
    try:
        paths = tuple(changed_paths)
    except TypeError:
        _invalid_knowledge_change()
    if (
        not paths
        or not all(isinstance(item, str) and item for item in paths)
        or len(paths) != len(set(paths))
    ):
        _invalid_knowledge_change()
    normalized: list[str] = []
    for item in paths:
        if "\\" in item:
            _invalid_knowledge_change()
        path = PurePosixPath(item)
        if item != path.as_posix():
            _invalid_knowledge_change()
        allowed = (
            item == "AGENTS.md"
            or (
                len(path.parts) >= 3
                and path.parts[:2] == ("docs", "agent-knowledge")
            )
            or (
                repository == "frontend"
                and len(path.parts) >= 3
                and path.parts[:2] == ("docs", "delivery-knowledge")
            )
        )
        if path.is_absolute() or ".." in path.parts or not allowed:
            _invalid_knowledge_change()
        normalized.append(path.as_posix())
    if len(normalized) != len(set(normalized)):
        _invalid_knowledge_change()
    if repository_root is not None:
        try:
            root = repository_root.resolve(strict=True)
            if not root.is_dir():
                _invalid_knowledge_change()
            for item in normalized:
                target = (root / item).resolve(strict=False)
                target.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            _invalid_knowledge_change()
    if (
        not staged_text.strip()
        or "\x00" in staged_text
        or any(
            line.startswith(("<<<<<<<", "=======", ">>>>>>>"))
            for line in staged_text.splitlines()
        )
        or any(pattern.search(staged_text) for pattern in _SECRET_PATTERNS)
    ):
        _invalid_knowledge_change()
    if (frontend_root is None) != (backend_root is None):
        _invalid_knowledge_change()
    if frontend_root is not None and backend_root is not None:
        try:
            entries = verify_indexes(frontend_root, backend_root)
        except ValueError:
            _invalid_knowledge_change()
        indexed_paths = {item.relative_path for item in entries}
        for item in normalized:
            if (
                item.startswith(("docs/agent-knowledge/", "docs/delivery-knowledge/"))
                and not item.endswith("/index.yaml")
                and item not in indexed_paths
            ):
                _invalid_knowledge_change()
    return tuple(sorted(normalized))


def _knowledge_pr_url(repository: str, value: str) -> bool:
    name = "Eventra-Backend" if repository == "backend" else "Eventra"
    return (
        isinstance(value, str)
        and re.fullmatch(
            rf"https://github\.com/codeExploreHub/{name}/pull/[1-9][0-9]*",
            value,
        )
        is not None
    )


def _read_knowledge_pr(github: Any, url: str) -> dict[str, Any]:
    value = github.run(
        [
            "pr", "view", url,
            "--json", "url,headRefName,headRefOid,state,mergedAt,mergeCommit",
        ]
    )
    required = {
        "url", "headRefName", "headRefOid", "state", "mergedAt", "mergeCommit"
    }
    if not isinstance(value, dict) or set(value) != required or value.get("url") != url:
        raise RuntimeError("invalid knowledge pull request")
    branch = value.get("headRefName")
    head_sha = value.get("headRefOid")
    state = value.get("state")
    merged_at = value.get("mergedAt")
    merge_commit = value.get("mergeCommit")
    if (
        not isinstance(branch, str)
        or _KNOWLEDGE_PR_BRANCH.fullmatch(branch) is None
        or not isinstance(head_sha, str)
        or _SHA.fullmatch(head_sha) is None
        or state not in {"OPEN", "CLOSED", "MERGED"}
    ):
        raise RuntimeError("invalid knowledge pull request")
    merge_sha = None
    if state == "MERGED":
        if (
            not isinstance(merged_at, str)
            or not isinstance(merge_commit, dict)
            or set(merge_commit) != {"oid"}
        ):
            raise RuntimeError("invalid knowledge pull request")
        try:
            timestamp = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
        except ValueError:
            raise RuntimeError("invalid knowledge pull request") from None
        merge_sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
        if timestamp.tzinfo is None or not isinstance(merge_sha, str) or _SHA.fullmatch(merge_sha) is None:
            raise RuntimeError("invalid knowledge pull request")
    elif merged_at is not None or merge_commit is not None:
        raise RuntimeError("invalid knowledge pull request")
    return {
        "branch": branch,
        "head_sha": head_sha,
        "state": state.lower(),
        "merge_sha": merge_sha,
    }


def record_knowledge_pr(
    github: Any,
    repository: str,
    candidate_digest_value: str,
    pr_url: str,
    expected_source_branch: str,
) -> KnowledgePullRequestState:
    """Record an exact open knowledge PR after one authoritative reread."""
    if (
        repository not in _REPOSITORIES
        or not isinstance(candidate_digest_value, str)
        or _DIGEST.fullmatch(candidate_digest_value) is None
        or expected_source_branch != f"eventra-knowledge/{candidate_digest_value}"
        or not _knowledge_pr_url(repository, pr_url)
    ):
        raise ValueError("invalid knowledge pull request")
    value = _read_knowledge_pr(github, pr_url)
    if value["state"] != "open" or value["branch"] != expected_source_branch:
        raise RuntimeError("invalid knowledge pull request")
    return KnowledgePullRequestState(
        repository=repository,
        candidate_digest=candidate_digest_value,
        url=pr_url,
        source_branch=expected_source_branch,
        head_sha=value["head_sha"],
        status="pr_open",
    )


def reconcile_knowledge_pr(
    github: Any,
    current: KnowledgePullRequestState,
    *,
    merged_verifier: Callable[[str, str, str], bool] | None = None,
) -> KnowledgePullRequestState:
    """Converge one recorded PR using only an authoritative GitHub read."""
    if (
        not isinstance(current, KnowledgePullRequestState)
        or current.repository not in _REPOSITORIES
        or not isinstance(current.candidate_digest, str)
        or _DIGEST.fullmatch(current.candidate_digest) is None
        or not _knowledge_pr_url(current.repository, current.url)
        or not isinstance(current.source_branch, str)
        or current.source_branch
        != f"eventra-knowledge/{current.candidate_digest}"
        or not isinstance(current.head_sha, str)
        or _SHA.fullmatch(current.head_sha) is None
        or current.status
        not in {"pr_open", "rejected", "merged", "verified", "needs_human"}
        or (
            current.status in {"merged", "verified"}
            and current.merge_sha is None
        )
        or (
            current.status in {"pr_open", "rejected"}
            and current.merge_sha is not None
        )
        or (
            current.merge_sha is not None
            and (
                not isinstance(current.merge_sha, str)
                or _SHA.fullmatch(current.merge_sha) is None
            )
        )
    ):
        raise ValueError("invalid knowledge pull request")
    value = _read_knowledge_pr(github, current.url)
    if (
        value["branch"] != current.source_branch
        or value["head_sha"] != current.head_sha
    ):
        return replace(current, status="needs_human")
    if current.status == "needs_human":
        return current
    if current.merge_sha is not None and value["merge_sha"] != current.merge_sha:
        return replace(current, status="needs_human")
    if current.status == "rejected":
        return current if value["state"] == "closed" else replace(
            current, status="needs_human"
        )
    if value["state"] == "open":
        return current if current.status == "pr_open" else replace(
            current, status="needs_human"
        )
    if value["state"] == "closed":
        return replace(current, status="rejected", merge_sha=None)
    merged = replace(current, status="merged", merge_sha=value["merge_sha"])
    if merged_verifier is None:
        return current if current.status in {"merged", "verified"} else merged
    try:
        verified = merged_verifier(
            current.repository,
            value["merge_sha"],
            current.candidate_digest,
        )
    except (RuntimeError, ValueError):
        verified = False
    return replace(merged, status="verified" if verified else "needs_human")


def _committed_knowledge_entries(
    root: Path, repository: str, commit_sha: str,
) -> tuple[KnowledgeIndexEntry, ...]:
    """Read raw Git blobs, never the index, worktree, or export filters."""
    root = root.resolve(strict=True)

    def git(*args: str) -> bytes:
        completed = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(root), *args],
            capture_output=True, check=False, timeout=30,
        )
        if completed.returncode != 0:
            raise ValueError("unavailable knowledge object")
        return completed.stdout

    if git("cat-file", "-t", commit_sha).strip() != b"commit":
        raise ValueError("not a knowledge commit")
    directories = ["docs/agent-knowledge"]
    if repository == "frontend":
        directories.append("docs/delivery-knowledge")

    def blob(relative_path: str) -> bytes:
        path = _safe_relative(relative_path)
        if not any(path.startswith(directory + "/") for directory in directories):
            raise ValueError("noncanonical knowledge path")
        record = git("ls-tree", "-z", commit_sha, "--", ":(literal)" + path)
        fields = record.rstrip(b"\0").split(b"\t")
        if len(fields) != 2 or fields[1] != path.encode("utf-8"):
            raise ValueError("missing knowledge blob")
        metadata = fields[0].split()
        if (len(metadata) != 3 or metadata[0] not in {b"100644", b"100755"}
                or metadata[1] != b"blob"):
            raise ValueError("nonregular knowledge blob")
        return git("cat-file", "blob", metadata[2].decode("ascii"))

    # Reuse the strict YAML/digest/provenance validator on an immutable snapshot.
    with tempfile.TemporaryDirectory(prefix="eventra-knowledge-commit-") as directory:
        snapshot = Path(directory)
        indexes = []
        for canonical in directories:
            index = snapshot / canonical / "index.yaml"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_bytes(blob(canonical + "/index.yaml"))
            value = _read_yaml(index)
            if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
                _invalid()
            for item in value["entries"]:
                if not isinstance(item, dict):
                    _invalid()
                path = _safe_relative(item.get("relative_path"))
                content = blob(path)
                destination = snapshot / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            indexes.append(index)
        entries = tuple(
            entry for index in indexes for entry in load_index(index, snapshot, repository)
        )
        if len({entry.knowledge_id for entry in entries}) != len(entries):
            _invalid()
        return entries


def verify_merged_knowledge(
    repository: str,
    merge_sha: str,
    candidate_digest_value: str,
    *,
    frontend_root: Path,
    backend_root: Path,
) -> bool:
    """Verify target-owned canonical knowledge in the exact Git commit tree."""
    if (
        repository not in _REPOSITORIES
        or not isinstance(merge_sha, str)
        or _SHA.fullmatch(merge_sha) is None
        or not isinstance(candidate_digest_value, str)
        or _DIGEST.fullmatch(candidate_digest_value) is None
        or not isinstance(frontend_root, Path)
        or not isinstance(backend_root, Path)
    ):
        raise ValueError("invalid merged knowledge")
    target_root = frontend_root if repository == "frontend" else backend_root
    try:
        entries = _committed_knowledge_entries(target_root, repository, merge_sha)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        raise RuntimeError("merged knowledge verification failed") from None
    matches = [
        item
        for item in entries
        if item.status == "active"
        and item.source_candidate_digest == candidate_digest_value
        and repository in item.repositories
    ]
    if len(matches) != 1:
        raise RuntimeError("merged knowledge verification failed")
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eventra verified knowledge retrieval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--frontend-root", type=Path, default=Path.cwd())
    verify.add_argument("--backend-root", type=Path, default=Path.cwd().parent / "Eventra-Backend")
    context = subparsers.add_parser("context")
    context.add_argument("--frontend-root", type=Path, default=Path.cwd())
    context.add_argument("--backend-root", type=Path, default=Path.cwd().parent / "Eventra-Backend")
    context.add_argument("--task-id", required=True)
    context.add_argument("--repository", choices=sorted(_REPOSITORIES), required=True)
    context.add_argument("--task-type", required=True)
    context.add_argument("--sha", action="append", default=[], metavar="REPOSITORY=SHA")
    context.add_argument("--path", action="append", default=[], dest="paths")
    context.add_argument("--verified-id", action="append", default=[], dest="verified_ids")
    context.add_argument("--conflict", action="append", default=[], dest="conflicts")
    candidate = subparsers.add_parser("candidate")
    candidate.add_argument("--input", required=True, type=Path)
    summary = subparsers.add_parser("summary")
    summary.add_argument("--child", required=True)
    summary.add_argument("--evidence-comment", required=True)
    summary.add_argument("--candidate-digest", required=True)
    scan = subparsers.add_parser("scan")
    scan.add_argument("--project-id", required=True)
    scan.add_argument("--backend-project-id", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--project-id", required=True)
    plan.add_argument("--backend-project-id", required=True)
    plan.add_argument("--frontend-root", type=Path, default=Path.cwd())
    plan.add_argument("--backend-root", type=Path, default=Path.cwd().parent / "Eventra-Backend")
    curate = subparsers.add_parser("curate")
    curate.add_argument("--project-id", required=True)
    curate.add_argument("--backend-project-id", required=True)
    curate.add_argument("--curator-agent-id", required=True)
    curate.add_argument("--frontend-root", type=Path, default=Path.cwd())
    curate.add_argument("--backend-root", type=Path)
    curate.add_argument("--apply", action="store_true")
    change = subparsers.add_parser("check-change")
    change.add_argument("--repository", choices=sorted(_REPOSITORIES), required=True)
    change.add_argument("--changed-path", action="append", required=True)
    change.add_argument("--staged-text-file", type=Path, required=True)
    change.add_argument("--repository-root", type=Path, required=True)
    change.add_argument("--frontend-root", type=Path, required=True)
    change.add_argument("--backend-root", type=Path, required=True)
    pr_state = subparsers.add_parser("pr-state")
    pr_state.add_argument("--repository", choices=sorted(_REPOSITORIES), required=True)
    pr_state.add_argument("--candidate-digest", required=True)
    pr_state.add_argument("--pr-url", required=True)
    pr_state.add_argument("--source-branch", required=True)
    pr_state.add_argument("--recorded-head-sha")
    pr_state.add_argument(
        "--recorded-status",
        choices=["pr_open", "rejected", "merged", "verified", "needs_human"],
    )
    pr_state.add_argument("--recorded-merge-sha")
    pr_state.add_argument("--frontend-root", type=Path, required=True)
    pr_state.add_argument("--backend-root", type=Path, required=True)
    return parser


def _parse_shas(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if value.count("=") != 1:
            _invalid()
        repository, sha = value.split("=", 1)
        if repository in result:
            _invalid()
        result[repository] = sha
    _sha_map(result, nonempty=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "candidate":
        try:
            source = args.input.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ValueError("invalid knowledge candidate") from None
        print(render_candidate_block(source))
        return 0
    if args.command == "summary":
        print(
            render_summary_pointer(
                args.child, args.evidence_comment, args.candidate_digest
            )
        )
        return 0
    if args.command == "check-change":
        try:
            staged_text = args.staged_text_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ValueError("invalid knowledge change") from None
        paths = verify_knowledge_change(
            args.repository,
            args.changed_path,
            staged_text,
            repository_root=args.repository_root,
            frontend_root=args.frontend_root,
            backend_root=args.backend_root,
        )
        print(
            json.dumps(
                {"count": len(paths), "paths": list(paths)},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    if args.command == "pr-state":
        from tools.multica.workflow import GitHubRunner

        github = GitHubRunner()
        if args.recorded_head_sha is None:
            if args.recorded_status is not None or args.recorded_merge_sha is not None:
                raise ValueError("invalid knowledge pull request")
            state = record_knowledge_pr(
                github,
                args.repository,
                args.candidate_digest,
                args.pr_url,
                args.source_branch,
            )
        else:
            current = KnowledgePullRequestState(
                repository=args.repository,
                candidate_digest=args.candidate_digest,
                url=args.pr_url,
                source_branch=args.source_branch,
                head_sha=args.recorded_head_sha,
                status=args.recorded_status or "pr_open",
                merge_sha=args.recorded_merge_sha,
            )
            state = reconcile_knowledge_pr(
                github,
                current,
                merged_verifier=lambda repository, sha, digest: (
                    verify_merged_knowledge(
                        repository,
                        sha,
                        digest,
                        frontend_root=args.frontend_root,
                        backend_root=args.backend_root,
                    )
                ),
            )
        print(
            json.dumps(
                asdict(state),
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    if args.command == "curate":
        result = curate_once(
            MulticaRunner(),
            {
                "frontend": args.project_id,
                "backend": args.backend_project_id,
            },
            args.curator_agent_id,
            args.apply,
            frontend_root=args.frontend_root,
            backend_root=args.backend_root,
        )
        print(
            json.dumps(
                asdict(result),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    if args.command in {"scan", "plan"}:
        project_ids = {
            "frontend": args.project_id,
            "backend": args.backend_project_id,
        }
        runner = MulticaRunner()
        refs = list_pending_parents(runner, project_ids)
        if args.command == "scan":
            print(
                json.dumps(
                    {
                        "count": len(refs),
                        "parents": [item.identifier for item in refs],
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0
        entries = verify_indexes(args.frontend_root, args.backend_root)
        if not refs:
            parent_identifier = None
            decision = CurationDecision(
                "noop", None, None, None, None, "no pending knowledge parent"
            )
        else:
            parent_ref = refs[0]
            parent_identifier = parent_ref.identifier
            decision = stable_curation_decision(
                lambda: load_candidate_snapshot(runner, parent_ref, project_ids),
                entries,
            )
        print(
            json.dumps(
                {
                    "action_key": decision.action_key,
                    "decision": decision.kind,
                    "existing_knowledge_id": decision.existing_knowledge_id,
                    "parent": parent_identifier,
                    "reason": decision.reason,
                    "target_project_id": decision.target_project_id,
                    "target_repository": decision.target_repository,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    entries = verify_indexes(args.frontend_root, args.backend_root)
    if args.command == "verify":
        print(json.dumps({"count": len(entries), "knowledge_ids": [item.knowledge_id for item in entries]}, separators=(",", ":"), sort_keys=True))
        return 0
    print(
        build_context_receipt(
            args.task_id,
            args.repository,
            args.task_type,
            _parse_shas(args.sha),
            entries,
            args.paths,
            verified_ids=args.verified_ids,
            conflicts=args.conflicts,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
