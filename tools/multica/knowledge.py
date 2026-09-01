"""Verified, read-only repository knowledge retrieval for the Eventra pilot."""

from __future__ import annotations

import argparse
import csv
import functools
import hashlib
import io
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

import yaml

from tools.multica.knowledge_contracts import (
    CurationDecision,
    ContextReceipt,
    KnowledgeCandidate,
    KnowledgeIndexEntry,
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
_BOOTSTRAP_DESIGN_PATH = "docs/superpowers/specs/2026-08-31-eventra-repository-knowledge-loop-design.md"
_BOOTSTRAP_DESIGN_COMMIT = "32a150dfa"
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
    verified_set.update(
        item.knowledge_id
        for item in selected
        if any(candidate_sha == item.last_verified_sha for _, candidate_sha in shas)
    )
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
    if set(knowledge_metadata) != required:
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
    try:
        candidates = extract_candidate_blocks(str(evidence_comment["content"]))
    except ValueError:
        raise RuntimeError("invalid knowledge evidence") from None
    candidate = candidates[0]
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
