"""Pure refresh wire contracts; parsed claims alone are never live authority.

API scope, current revisions, assignment and consumption are checked by the
executor. This module performs no I/O and does not authorize a push or merge.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from .knowledge_contracts import ContextReceipt


MAX_REQUEST_BYTES = 3_072
MAX_COMMENT_BYTES = 65_536
STAGING_PREFIX = "refs/heads/eventra-refresh/"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ISSUE = re.compile(r"PRO-[1-9][0-9]*\Z")
_PR = re.compile(r"https://github\.com/codeExploreHub/Eventra/pull/[1-9][0-9]*\Z")
_SYSTEM_AUTHOR_ID = "00000000-0000-0000-0000-000000000000"
_HISTORY_IDENTITIES = frozenset({
    ("member", "comment"),
    ("agent", "comment"),
    ("agent", "system"),
    ("system", "system"),
    ("system", "progress_update"),
})


@dataclass(frozen=True)
class RefreshRequest:
    canonical_payload: str
    digest: str
    staging_ref: str

    def payload(self) -> dict[str, Any]:
        return _load_json(self.canonical_payload, MAX_REQUEST_BYTES)


@dataclass(frozen=True)
class RefreshComment:
    issue_id: str
    comment_uuid: str
    author_id: str
    author_type: str
    revision: int
    content: str


@dataclass(frozen=True)
class PreparedCandidate:
    request_digest: str
    child_id: str
    source_sha: str
    prerequisite_sha: str
    target_sha: str
    tree_sha: str
    evidence_uuid: str
    evidence_digest: str
    staging_ref: str


@dataclass(frozen=True)
class RefreshOutcome:
    request_digest: str
    child_id: str
    source_sha: str
    prerequisite_sha: str
    result: str
    evidence_uuid: str
    evidence_digest: str


@dataclass(frozen=True)
class InitialRefreshProgress:
    request: RefreshRequest
    metadata_writes: int
    comment_writes: int
    request_comment: RefreshComment | None
    grant_comment: RefreshComment | None


def _require(condition: bool, reason: str = "invalid refresh contract") -> None:
    if not condition:
        raise ValueError(reason)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("invalid refresh JSON") from None


def _size(text: object, limit: int) -> str:
    _require(type(text) is str, "invalid refresh text")
    try:
        _require(len(text.encode("utf-8")) <= limit, "refresh text too large")
    except UnicodeError:
        raise ValueError("invalid refresh text encoding") from None
    return text


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate refresh JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite refresh JSON number")


def _load_json(text: str, limit: int) -> Any:
    _size(text, limit)
    try:
        return json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise ValueError("invalid refresh JSON") from None


def _object(value: object, names: str) -> dict[str, Any]:
    _require(type(value) is dict and set(value) == set(names.split()), "invalid refresh fields")
    return value


def _match(value: object, pattern: re.Pattern[str]) -> None:
    _require(type(value) is str and pattern.fullmatch(value) is not None)


def _uuid(value: object) -> None:
    _require(type(value) is str)
    try:
        parsed = uuid.UUID(value)
        _require(str(parsed) == value and parsed.int != 0)
    except (ValueError, AttributeError):
        raise ValueError("invalid refresh UUID") from None


def _integer(value: object, expected: int | None = None) -> None:
    _require(type(value) is int and (value >= 1 if expected is None else value == expected))


def _history_identity(author_id: object, author_type: object,
                      record_type: object) -> None:
    _require((author_type, record_type) in _HISTORY_IDENTITIES,
             "invalid refresh comment identity")
    if author_type == "system":
        _require(author_id == _SYSTEM_AUTHOR_ID,
                 "invalid refresh comment identity")
    else:
        _uuid(author_id)


def _branch(value: object) -> None:
    _require(type(value) is str and len(value) <= 255)
    _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value) is not None)
    _require(not value.startswith("refs/") and ".." not in value)
    _require(all(part and not part.startswith(".") and not part.endswith((".", ".lock"))
                 for part in value.split("/")), "invalid refresh branch")


def comment_manifest(records: object, issue_id: str) -> list[dict[str, object]]:
    """Canonical immutable identities for one complete parent comment history."""
    _uuid(issue_id)
    _require(type(records) is list, "incomplete refresh comments")
    required = {"id", "author_id", "author_type", "content", "revision", "type", "created_at"}
    auxiliary = {"issue_id", "parent_id", "updated_at", "reply_count", "last_activity_at"}
    result, seen = [], set()
    for record in records:
        _require(type(record) is dict and "revision" in record,
                 "missing refresh comment revision")
        _require(type(record) is dict and required <= set(record) <= required | auxiliary,
                 "invalid refresh comment fields")
        comment_id, parent_id = record["id"], record.get("parent_id")
        _uuid(comment_id)
        _require(comment_id not in seen, "duplicate refresh comment")
        _history_identity(record["author_id"], record["author_type"],
                          record["type"])
        _require(record.get("issue_id", issue_id) == issue_id, "cross-scope refresh comment")
        _integer(record["revision"])
        if parent_id is not None:
            _uuid(parent_id)
            _require(parent_id in seen, "incomplete refresh comment thread")
        created_at = record["created_at"]
        _require(type(created_at) is str, "invalid refresh comment time")
        try:
            parsed_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid refresh comment time") from None
        _require(parsed_time.tzinfo is not None, "invalid refresh comment time")
        content = _size(record["content"], MAX_COMMENT_BYTES)
        if "reply_count" in record:
            _require(type(record["reply_count"]) is int and record["reply_count"] >= 0,
                     "invalid refresh reply count")
        seen.add(comment_id)
        result.append({"issue_id": issue_id, "comment_uuid": comment_id,
                       "author_id": record["author_id"], "author_type": record["author_type"],
                       "type": record["type"], "revision": record["revision"], "parent_id": parent_id,
                       "created_at": created_at,
                       "content_digest": hashlib.sha256(content.encode("utf-8")).hexdigest()})
    for record in records:
        if "reply_count" in record:
            descendants = {record["id"]}
            for child in records:
                if child.get("parent_id") in descendants:
                    descendants.add(child["id"])
            _require(record["reply_count"] == len(descendants) - 1,
                     "incomplete refresh comment replies")
    return sorted(result, key=lambda item: item["comment_uuid"])


def comment_manifest_digest(records: object, issue_id: str) -> str:
    return hashlib.sha256(canonical_json(comment_manifest(records, issue_id)).encode("utf-8")).hexdigest()


def build_request(payload: dict[str, object]) -> RefreshRequest:
    """Validate the entire immutable request before deriving its ref name."""
    _require(type(payload) is dict, "invalid refresh fields")
    version = payload.get("schema_version")
    _integer(version)
    _require(version in {1, 2}, "unsupported refresh request version")
    names = ("schema_version workspace_id parent source pr prerequisite assignment baseline "
             "refresh_stage refresh_generation staging_ref_prefix tree_transform "
             "merge_permission control_tool_sha git_version")
    if version == 2:
        names += " fresh_gate_stage supersession"
    value = _object(payload, names)
    encoded = _size(canonical_json(value), MAX_REQUEST_BYTES)
    _integer(value["schema_version"], version)
    _integer(value["refresh_stage"], 2 if version == 1 else 3)
    if version == 2:
        _integer(value["fresh_gate_stage"], 4)
    _integer(value["refresh_generation"], 1)
    _uuid(value["workspace_id"])
    _match(value["control_tool_sha"], _SHA)
    _require(value["staging_ref_prefix"] == STAGING_PREFIX)
    _require(value["tree_transform"] == "clean-two-parent-merge-v1")
    _require(value["merge_permission"] == "hold")
    _require(type(value["git_version"]) is str and
             re.fullmatch(r"git version [0-9][A-Za-z0-9. ()_-]{0,127}", value["git_version"]) is not None)
    baseline = _object(value["baseline"], "authority_digest comments_digest")
    for digest in baseline.values():
        _match(digest, _DIGEST)
    parent = _object(value["parent"], "id identifier revision stage attempt next_stage status merge_state last_action")
    _uuid(parent["id"])
    _match(parent["identifier"], _ISSUE)
    _integer(parent["revision"])
    _integer(parent["stage"], 1)
    _integer(parent["attempt"], 0)
    _integer(parent["next_stage"], 2)
    _require(parent["status"] in ("blocked", "in_progress", "in_review", "todo"))
    _require(parent["merge_state"] == "not_ready")
    _match(parent["last_action"], re.compile(
        rf"2:{re.escape(parent['identifier'])}:create_implementation_stage:0:frontend:"
        r"[0-9a-f]{40}:-:next-stage:1\Z"))
    source = _object(value["source"], "child_id child_identifier sha evidence_uuid evidence_revision evidence_digest")
    for key in ("child_id", "evidence_uuid"):
        _uuid(source[key])
    _require(source["child_id"] != parent["id"])
    _match(source["child_identifier"], _ISSUE)
    _require(source["child_identifier"] != parent["identifier"])
    _match(source["sha"], _SHA)
    _integer(source["evidence_revision"], 1)
    _match(source["evidence_digest"], _DIGEST)
    pr = _object(value["pr"], "url repository head_ref base_ref")
    _match(pr["url"], _PR)
    _require(pr["repository"] == "codeExploreHub/Eventra")
    for key in ("head_ref", "base_ref"):
        _branch(pr[key])
    _require(pr["head_ref"] != pr["base_ref"])
    prerequisite = _object(value["prerequisite"], "pr_url merge_sha base_sha")
    _match(prerequisite["pr_url"], _PR)
    _require(prerequisite["pr_url"] != pr["url"])
    for key in ("merge_sha", "base_sha"):
        _match(prerequisite[key], _SHA)
    _require(prerequisite["merge_sha"] != source["sha"])
    assignment_fields = "project_id squad_id lead_id engineer_id"
    if version == 2:
        assignment_fields += " reviewer_id integration_qa_id"
    assignment = _object(value["assignment"], assignment_fields)
    for identity in assignment.values():
        _uuid(identity)
    if version == 1:
        _require(assignment["lead_id"] != assignment["engineer_id"])
    else:
        role_ids = [assignment[key] for key in (
            "lead_id", "engineer_id", "reviewer_id", "integration_qa_id")]
        _require(len(set(role_ids)) == len(role_ids)
                 and assignment["project_id"] != assignment["squad_id"],
                 "aliased refresh assignments")
    if version == 2:
        supersession = _object(value["supersession"], "gate_stage mode gates")
        _integer(supersession["gate_stage"], 2)
        _require(supersession["mode"] == "cancel-pristine-gates-v1")
        gates = supersession["gates"]
        _require(type(gates) is list and len(gates) == 2,
                 "invalid supersession gates")
        roles = ("independent_reviewer", "integration_qa")
        seen_ids, seen_identifiers, seen_digests = set(), set(), set()
        for gate, role in zip(gates, roles, strict=True):
            gate = _object(gate, "role id identifier title revision status authority_digest")
            _require(gate["role"] == role)
            _uuid(gate["id"])
            _match(gate["identifier"], _ISSUE)
            _require(gate["id"] not in {parent["id"], source["child_id"]}
                     and gate["id"] not in seen_ids
                     and gate["identifier"] not in {
                         parent["identifier"], source["child_identifier"]}
                     and gate["identifier"] not in seen_identifiers,
                     "duplicate supersession gate")
            expected_title = (f"{parent['identifier']} frontend review"
                              if role == "independent_reviewer"
                              else f"{parent['identifier']} frontend QA")
            _require(gate["title"] == expected_title)
            _integer(gate["revision"], 1)
            _require(gate["status"] == "backlog")
            _match(gate["authority_digest"], _DIGEST)
            _require(gate["authority_digest"] not in seen_digests,
                     "duplicate supersession authority")
            seen_ids.add(gate["id"])
            seen_identifiers.add(gate["identifier"])
            seen_digests.add(gate["authority_digest"])
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    staging_ref = STAGING_PREFIX + digest
    _size(canonical_json({"payload": value, "digest": digest, "staging_ref": staging_ref}),
          MAX_REQUEST_BYTES)
    return RefreshRequest(encoded, digest, staging_ref)


def parse_request(raw: object) -> RefreshRequest:
    if type(raw) is str:
        raw = _load_json(raw, MAX_REQUEST_BYTES + 1024)
    value = _object(raw, "payload digest staging_ref")
    request = build_request(value["payload"])
    _require(value["digest"] == request.digest and value["staging_ref"] == request.staging_ref,
             "refresh request digest or ref mismatch")
    return request


def _request(request: RefreshRequest) -> dict[str, Any]:
    _require(type(request) is RefreshRequest)
    checked = build_request(request.payload())
    _require(checked == request, "unvalidated refresh request")
    return checked.payload()


def refresh_protocol(request: RefreshRequest) -> int:
    """Return the validated wire protocol version for one immutable request."""
    return _request(request)["schema_version"]


def supersession_preview(request: RefreshRequest) -> tuple[dict[str, object], ...]:
    """Return non-authorizing display fields already bound by a v2 request."""
    payload = _request(request)
    if payload["schema_version"] == 1:
        return ()
    source_sha = payload["source"]["sha"]
    return tuple({
        "identifier": gate["identifier"], "role": gate["role"],
        "title": gate["title"], "status": gate["status"],
        "revision": gate["revision"], "stale_candidate_sha": source_sha,
    } for gate in payload["supersession"]["gates"])


def validate_metadata_budget(metadata: object, *, request: RefreshRequest | None = None) -> None:
    """Conservative preflight; the server remains the final 8KB/50-key authority."""
    _require(type(metadata) is dict and all(type(key) is str and type(value) is str
                                            for key, value in metadata.items()),
             "invalid refresh metadata map")
    _require(len(metadata) <= 50, "refresh metadata exceeds key budget")
    prospective = dict(metadata)
    if request is not None:
        payload = _request(request)
        _require(len(payload["parent"]["identifier"]) <= 64
                 and len(payload["source"]["child_identifier"]) <= 64,
                 "refresh identifier exceeds budget")
        envelope = canonical_json({"payload": payload, "digest": request.digest,
                                   "staging_ref": request.staging_ref})
        _size(envelope, MAX_REQUEST_BYTES)
        current = prospective.get(REFRESH_PREFIX + "request")
        _require(current in {None, envelope}, "different refresh request already stored")
        prospective[REFRESH_PREFIX + "request"] = envelope
    _require(len(prospective) <= 50, "refresh metadata exceeds key budget")
    try:
        encoded = json.dumps(prospective, sort_keys=True, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("invalid refresh metadata map") from None
    _require(len(encoded.encode("utf-8")) <= 6_144, "refresh metadata exceeds byte budget")


def _comment(comment: RefreshComment, issue: str, author_type: str) -> None:
    _require(type(comment) is RefreshComment)
    for value in (comment.issue_id, comment.comment_uuid, comment.author_id):
        _uuid(value)
    _integer(comment.revision, 1)
    _require(comment.issue_id == issue and comment.author_type == author_type,
             "refresh comment identity mismatch")
    _size(comment.content, MAX_COMMENT_BYTES)


def _block(text: str, kind: str, *, only: bool = False,
           protocol: int = 1) -> Any:
    # Reject duplicate, nested and unrelated fences rather than selecting a convenient block.
    _require(text.count("```") == 2, "ambiguous refresh evidence block")
    _require(type(protocol) is int and protocol in {1, 2},
             "invalid refresh evidence protocol")
    label = "eventra-candidate-refresh-" + kind + f"-v{protocol}"
    pattern = re.compile(r"^```" + re.escape(label) + r"\n([^`]+)\n```$", re.MULTILINE)
    match = pattern.search(text)
    _require(match is not None, "missing refresh evidence block")
    if only:
        _require(text == match.group(0), "grant must contain only its canonical block")
    return _load_json(match.group(1), MAX_COMMENT_BYTES)


def validate_grant(comment: RefreshComment, request: RefreshRequest) -> None:
    """Validate a scoped member claim; live lookup/consumption is a separate gate."""
    payload = _request(request)
    _comment(comment, payload["parent"]["id"], "member")
    value = _object(_block(comment.content, "grant", only=True,
                           protocol=payload["schema_version"]),
                    "schema_version request_digest granted_refresh")
    _integer(value["schema_version"], payload["schema_version"])
    _integer(value["granted_refresh"], 1)
    _require(value["request_digest"] == request.digest, "grant request mismatch")


_COMMANDS = {
    "python": ["python3", "-B", "-m", "unittest", "discover", "-s", "tools/multica/tests", "-p", "test_*.py"],
    "local_contract": ["npm", "run", "test:local-contract"],
    "footer": ["npm", "run", "test:footer-meta"],
    "hydration": ["npm", "run", "test:layout-hydration"],
    "dashboard": ["npm", "run", "test:dashboard-profile"],
    "lint": ["npm", "run", "lint"],
    "build": ["npm", "run", "build"],
}


def _command_argv(name: str, argv: object) -> None:
    _require(type(argv) is list and all(type(arg) is str for arg in argv))
    if name == "knowledge":
        prefix = ["python3", "-B", "-m", "tools.multica.knowledge", "verify"]
        _require(len(argv) == 9 and argv[:5] == prefix and
                 argv[5] == "--frontend-root" and argv[7] == "--backend-root")
        for root in (argv[6], argv[8]):
            _require(root.startswith("/") and root != "/" and ".." not in PurePosixPath(root).parts
                     and not any(ord(char) < 32 for char in root))
        _require(argv[6] != argv[8])
        return
    allowed = [_COMMANDS[name]]
    if name == "lint":
        allowed.append(_COMMANDS[name] + ["--", "--ignore-pattern", ".worktrees/**"])
    if name == "python":
        allowed.append(_COMMANDS[name] + ["-v"])
    _require(argv in allowed, "required refresh check substituted")


def _commands(raw: object) -> None:
    commands = _object(raw, " ".join((*_COMMANDS, "knowledge")))
    for name, result in commands.items():
        result = _object(result, "argv exit_code")
        _integer(result["exit_code"], 0)
        _command_argv(name, result["argv"])


def _outcome_commands(raw: object) -> None:
    _require(type(raw) is dict and set(raw) <= set((*_COMMANDS, "knowledge")),
             "invalid refresh outcome commands")
    for name, result in raw.items():
        result = _object(result, "argv exit_code")
        code = result["exit_code"]
        _require(type(code) is int and 0 <= code <= 255,
                 "invalid refresh outcome exit code")
        _command_argv(name, result["argv"])


def _context(raw: object, payload: dict[str, Any], target: str) -> None:
    receipt = _object(raw, " ".join(field.name for field in fields(ContextReceipt)))
    _integer(receipt["schema_version"], 1)
    _match(receipt["task_id"], _ISSUE)
    _require(receipt["task_id"] not in (payload["parent"]["identifier"], payload["source"]["child_identifier"]))
    _require(receipt["repository"] == "frontend" and receipt["task_type"] == "implementation")
    _require(receipt["candidate_shas"] == {"frontend": target})
    ids = receipt["knowledge_ids"]
    _require(type(ids) is list and bool(ids) and all(type(item) is str and item for item in ids))
    _require(ids == sorted(set(ids)))
    digests = receipt["knowledge_digests"]
    reasons = receipt["match_reasons"]
    _require(type(digests) is dict and set(digests) == set(ids))
    _require(type(reasons) is dict and set(reasons) == set(ids))
    for digest in digests.values():
        _match(digest, _DIGEST)
    _require(all(reason == "repository+task_type+path" for reason in reasons.values()))
    verified = receipt["verified_ids"]
    _require(type(verified) is list and all(type(item) is str for item in verified))
    _require(verified == sorted(set(verified)) and set(verified) <= set(ids))
    _require(receipt["conflicts"] == [], "conflicting knowledge cannot support preparation PASS")
    # Do not infer verified_ids from historical SHA or silently promote selected knowledge.
    # The executor must additionally bind task_id to the authoritative child's identifier.


def parse_prepared(comment: RefreshComment, request: RefreshRequest, child_id: str) -> PreparedCandidate:
    payload = _request(request)
    _uuid(child_id)
    _require(child_id not in (payload["parent"]["id"], payload["source"]["child_id"]))
    _comment(comment, child_id, "agent")
    _require(comment.author_id == payload["assignment"]["engineer_id"])
    value = _object(_block(comment.content, "prepared",
                           protocol=payload["schema_version"]),
                    "schema_version request_digest child_id source_sha prerequisite_sha target_sha "
                    "tree_sha staging_ref control_tool_sha git_version context_receipt commands")
    _integer(value["schema_version"], payload["schema_version"])
    expected = {
        "request_digest": request.digest, "child_id": child_id,
        "source_sha": payload["source"]["sha"],
        "prerequisite_sha": payload["prerequisite"]["merge_sha"],
        "staging_ref": request.staging_ref, "control_tool_sha": payload["control_tool_sha"],
        "git_version": payload["git_version"],
    }
    _require(all(value[key] == wanted for key, wanted in expected.items()), "prepared identity mismatch")
    for key in ("target_sha", "tree_sha"):
        _match(value[key], _SHA)
    _require(value["target_sha"] not in (value["source_sha"], value["prerequisite_sha"]))
    _commands(value["commands"])
    _context(value["context_receipt"], payload, value["target_sha"])
    return PreparedCandidate(request.digest, child_id, value["source_sha"], value["prerequisite_sha"],
                             value["target_sha"], value["tree_sha"], comment.comment_uuid,
                             hashlib.sha256(comment.content.encode("utf-8")).hexdigest(), request.staging_ref)


def parse_outcome(comment: RefreshComment, request: RefreshRequest, child_id: str,
                  expected_result: str) -> RefreshOutcome:
    payload = _request(request)
    _uuid(child_id)
    _require(child_id not in (payload["parent"]["id"], payload["source"]["child_id"]))
    _require(expected_result in {"fail", "blocked"}, "invalid refresh outcome result")
    _comment(comment, child_id, "agent")
    _require(comment.author_id == payload["assignment"]["engineer_id"])
    value = _object(_block(comment.content, "outcome",
                           protocol=payload["schema_version"]),
                    "schema_version request_digest child_id source_sha prerequisite_sha result commands reason")
    _integer(value["schema_version"], payload["schema_version"])
    _require(value["request_digest"] == request.digest and value["child_id"] == child_id
             and value["source_sha"] == payload["source"]["sha"]
             and value["prerequisite_sha"] == payload["prerequisite"]["merge_sha"]
             and value["result"] == expected_result,
             "refresh outcome identity mismatch")
    _outcome_commands(value["commands"])
    reason = _size(value["reason"], 2048)
    _require(bool(reason.strip()), "refresh outcome reason is empty")
    return RefreshOutcome(request.digest, child_id, value["source_sha"],
                          value["prerequisite_sha"], expected_result,
                          comment.comment_uuid,
                          hashlib.sha256(comment.content.encode("utf-8")).hexdigest())


@dataclass(frozen=True)
class RefreshSnapshot:
    """Immutable authority projection produced by RefreshAPI, never by a request."""

    canonical_state: str

    def state(self) -> dict[str, Any]:
        return _object(_load_json(self.canonical_state, 4_194_304),
                       "parent metadata children runs comments comment_manifest pr prerequisite assignment tool")


_ISSUE_DETAIL_FIELDS = frozenset({
    "assignee_id", "assignee_type", "created_at", "creator_id", "creator_type",
    "description", "due_date", "id", "identifier", "labels", "last_activity_at",
    "metadata", "number", "parent_issue_id", "position", "priority", "project_id",
    "properties", "revision", "stage", "start_date", "status", "status_category",
    "title", "updated_at", "workspace_id",
})
_OPTIONAL_ISSUE_DETAIL_FIELDS = frozenset({"status_name"})
_VOLATILE_DETAIL_FIELDS = frozenset({"metadata", "updated_at", "last_activity_at"})


def _issue_detail(value: object, reason: str) -> dict[str, object]:
    _require(type(value) is dict, reason)
    detail = value
    fields = set(detail)
    _require(_ISSUE_DETAIL_FIELDS <= fields <=
             _ISSUE_DETAIL_FIELDS | _OPTIONAL_ISSUE_DETAIL_FIELDS, reason)
    if "status_name" in detail:
        _require(type(detail["status_name"]) is str, "invalid issue status name")
    return detail


def _pristine_gate_records(state: dict[str, Any]) -> list[dict[str, object]]:
    parent, assignment = state["parent"], state["assignment"]
    sources = [child for child in state["children"]
               if child.get("detail", {}).get("stage") == 1]
    _require(len(sources) == 1 and len(state["children"]) == 3,
             "refresh requires one source and two pristine gates")
    source = sources[0]
    source_sha = source.get("metadata", {}).get("eventra.phase.sha.frontend")
    evidence_uuid = source.get("metadata", {}).get("eventra.phase.evidence_comment")
    _match(source_sha, _SHA)
    _uuid(evidence_uuid)
    pr_url = state.get("pr", {}).get("url")
    _match(pr_url, _PR)
    action = (f"2:{parent.get('identifier')}:create_gate_stage:0:frontend:"
              f"{source_sha}:-:next-stage:2")
    roles = assignment.get("roles")
    _require(type(roles) is dict, "missing gate assignments")
    result = []
    for role, suffix in (("independent_reviewer", "review"),
                         ("integration_qa", "QA")):
        assignee_id = roles.get(role)
        _uuid(assignee_id)
        matches = [child for child in state["children"]
                   if child.get("detail", {}).get("stage") == 2
                   and child.get("detail", {}).get("assignee_id") == assignee_id]
        _require(len(matches) == 1, "missing or duplicate pristine gate role")
        gate = _object(matches[0], "detail metadata evidence comment_manifest")
        detail = _issue_detail(gate["detail"], "unknown pristine gate field")
        expected_title = f"{parent.get('identifier')} frontend {suffix}"
        expected = {
            "parent_issue_id": parent.get("id"),
            "workspace_id": parent.get("workspace_id"),
            "stage": 2,
            "project_id": assignment.get("project_id"),
            "assignee_type": "agent",
            "assignee_id": assignee_id,
            "revision": 1,
            "status": "backlog",
            "status_category": "backlog",
            "title": expected_title,
        }
        _require(all(type(detail.get(key)) is type(value)
                     and detail.get(key) == value for key, value in expected.items()),
                 "pristine gate authority mismatch")
        _require(gate["metadata"] == {} and gate["evidence"] is None
                 and gate["comment_manifest"] == [],
                 "pristine gate has history")
        description = detail.get("description")
        _require(type(description) is str, "invalid pristine gate description")
        markers = (
            f"- Parent: {parent.get('identifier')}",
            f"- Candidate SHA: `{source_sha}`",
            f"- Managed PR: `{pr_url}`",
            f"- Stage action: `{action}`",
            f"- Implementation evidence: comment `{evidence_uuid}`",
        )
        _require(all(marker in description for marker in markers),
                 "pristine gate description mismatch")
        gate_runs = [run for run in state["runs"]
                     if run.get("issue_id") == detail["id"]]
        _require(not gate_runs, "pristine gate has a run")
        authority = {
            "detail": {key: value for key, value in detail.items()
                       if key not in _VOLATILE_DETAIL_FIELDS},
            "metadata": gate["metadata"], "evidence": gate["evidence"],
            "comment_manifest": gate["comment_manifest"], "runs": gate_runs,
        }
        result.append({
            "role": role, "id": detail["id"],
            "identifier": detail["identifier"], "title": detail["title"],
            "revision": detail["revision"], "status": detail["status"],
            "authority_digest": hashlib.sha256(
                canonical_json(authority).encode("utf-8")).hexdigest(),
        })
    return result


def authority_projection(snapshot: RefreshSnapshot, *,
                         supersede_pristine_gates: bool = False) -> dict[str, object]:
    """Return the complete stable authority that a new refresh request freezes."""
    state = _snapshot(snapshot)
    parent = _issue_detail(state["parent"], "unknown parent authority field")
    _require(parent["metadata"] == state["metadata"], "parent metadata echo conflict")
    _require(parent["parent_issue_id"] is None and parent["status"] == "blocked",
             "refresh freeze requires blocked parent")
    sources = [child for child in state["children"]
               if child.get("detail", {}).get("stage") == 1]
    _require(len(sources) == 1, "refresh freeze requires one source child")
    if not supersede_pristine_gates:
        _require(len(state["children"]) == 1,
                 "refresh freeze requires one source child")
    source = _object(sources[0], "detail metadata evidence comment_manifest")
    detail = _issue_detail(source["detail"], "unknown source authority field")
    _require(detail["metadata"] == source["metadata"], "source metadata echo conflict")
    _require(detail["parent_issue_id"] == parent["id"] and detail["stage"] == 1
             and detail["status"] == "done", "invalid source freeze state")
    _require(type(source["metadata"]) is dict and
             all(type(k) is str and type(v) is str for k, v in source["metadata"].items()),
             "invalid source metadata")
    _require(type(source["evidence"]) is dict, "missing source evidence")
    _require(not any(key.startswith(("eventra.refresh.", "eventra.repair.", "eventra.smoke."))
                     for key in state["metadata"]), "refresh freeze has active reservation")
    projection = {
        "parent": {key: value for key, value in parent.items() if key not in _VOLATILE_DETAIL_FIELDS},
        "metadata": state["metadata"],
        "source": {
            "detail": {key: value for key, value in detail.items() if key not in _VOLATILE_DETAIL_FIELDS},
            "metadata": source["metadata"], "evidence": source["evidence"],
        },
        "assignment": state["assignment"], "pr": state["pr"],
        "prerequisite": state["prerequisite"], "tool": state["tool"],
    }
    if supersede_pristine_gates:
        projection["source"]["comment_manifest"] = source["comment_manifest"]
        projection["runs"] = state["runs"]
        projection["supersession"] = {
            "mode": "cancel-pristine-gates-v1",
            "gates": _pristine_gate_records(state),
        }
    return _load_json(canonical_json(projection), 4_194_304)


def authority_digest(snapshot: RefreshSnapshot, *,
                     supersede_pristine_gates: bool = False) -> str:
    projection = authority_projection(
        snapshot, supersede_pristine_gates=supersede_pristine_gates)
    return hashlib.sha256(canonical_json(projection).encode("utf-8")).hexdigest()


def _snapshot(snapshot: RefreshSnapshot) -> dict[str, Any]:
    _require(type(snapshot) is RefreshSnapshot, "missing refresh authority")
    state = snapshot.state()
    _require(canonical_json(state) == snapshot.canonical_state, "noncanonical refresh authority")
    for key in ("parent", "metadata", "pr", "prerequisite", "assignment", "tool"):
        _require(type(state[key]) is dict, "invalid refresh authority")
    for key in ("children", "runs", "comments", "comment_manifest"):
        _require(type(state[key]) is list, "invalid refresh authority")
    _require(all(type(k) is str and type(v) is str for k, v in state["metadata"].items()))
    _require(state["parent"].get("metadata") == state["metadata"],
             "parent metadata echo conflict")
    manifest_fields = {"issue_id", "comment_uuid", "author_id", "author_type", "type",
                       "revision", "parent_id", "created_at", "content_digest"}
    observed_issue_ids = {state["parent"].get("id")}
    for raw_child in state["children"]:
        child = _object(raw_child, "detail metadata evidence comment_manifest")
        _require(type(child["detail"]) is dict and type(child["metadata"]) is dict
                 and child["detail"].get("metadata") == child["metadata"],
                 "child metadata echo conflict")
        issue_id, evidence = child["detail"].get("id"), child["evidence"]
        _uuid(issue_id)
        _require(issue_id not in observed_issue_ids,
                 "duplicate refresh issue authority")
        observed_issue_ids.add(issue_id)
        _require(type(child["comment_manifest"]) is list,
                 "invalid child comment manifest")
        child_seen, child_parents = set(), {}
        for item in child["comment_manifest"]:
            _require(type(item) is dict and set(item) == manifest_fields,
                     "invalid child comment manifest")
            _uuid(item["issue_id"]); _uuid(item["comment_uuid"])
            _history_identity(item["author_id"], item["author_type"], item["type"])
            _require(item["issue_id"] == issue_id,
                     "invalid child comment manifest identity")
            _integer(item["revision"])
            if item["parent_id"] is not None:
                _uuid(item["parent_id"])
            _require(type(item["created_at"]) is str,
                     "invalid child comment manifest time")
            try:
                created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("invalid child comment manifest time") from None
            _require(created.tzinfo is not None,
                     "invalid child comment manifest time")
            _match(item["content_digest"], _DIGEST)
            _require(item["comment_uuid"] not in child_seen,
                     "duplicate child comment manifest")
            child_seen.add(item["comment_uuid"])
            child_parents[item["comment_uuid"]] = item["parent_id"]
        _require(child["comment_manifest"] == sorted(
            child["comment_manifest"], key=lambda item: item["comment_uuid"]),
            "noncanonical child comment manifest")
        for comment_uuid, parent_id in child_parents.items():
            _require(parent_id is None or parent_id in child_seen,
                     "incomplete child comment manifest thread")
            lineage, current = set(), comment_uuid
            while current is not None:
                _require(current not in lineage,
                         "cyclic child comment manifest thread")
                lineage.add(current)
                current = child_parents[current]
        evidence_uuid = child["metadata"].get("eventra.phase.evidence_comment")
        _require((evidence_uuid is None) == (evidence is None),
                 "child comment manifest mismatch")
        if evidence is not None:
            try:
                comment = RefreshComment(**evidence)
            except TypeError:
                raise ValueError("child comment manifest mismatch") from None
            _comment(comment, issue_id, comment.author_type)
            matches = [item for item in child["comment_manifest"]
                       if item["comment_uuid"] == comment.comment_uuid]
            _require(len(matches) == 1 and evidence_uuid == comment.comment_uuid,
                     "child comment manifest mismatch")
            item = matches[0]
            _require(item["type"] == "comment"
                     and item["author_id"] == comment.author_id
                     and item["author_type"] == comment.author_type
                     and item["revision"] == comment.revision
                     and item["content_digest"] == hashlib.sha256(
                         comment.content.encode("utf-8")).hexdigest(),
                     "child comment manifest mismatch")
    seen_runs = set()
    for run in state["runs"]:
        _require(type(run) is dict and run.get("issue_id") in observed_issue_ids,
                 "unbound refresh run")
        _uuid(run.get("id")); _uuid(run.get("agent_id"))
        _require(run["id"] not in seen_runs, "duplicate refresh run")
        seen_runs.add(run["id"])
    parent_id, seen, manifest_comment_ids = state["parent"].get("id"), set(), set()
    normalized = {}
    for raw in state["comments"]:
        try:
            comment = RefreshComment(**raw)
        except TypeError:
            raise ValueError("invalid refresh comment authority") from None
        _comment(comment, parent_id, comment.author_type)
        _require(comment.comment_uuid not in normalized, "duplicate refresh comment authority")
        normalized[comment.comment_uuid] = comment
    for item in state["comment_manifest"]:
        _require(type(item) is dict and set(item) == manifest_fields,
                 "invalid refresh comment manifest")
        _uuid(item["issue_id"]); _uuid(item["comment_uuid"])
        _history_identity(item["author_id"], item["author_type"], item["type"])
        _require(item["issue_id"] == parent_id,
                 "invalid refresh comment manifest identity")
        _integer(item["revision"])
        if item["parent_id"] is not None:
            _uuid(item["parent_id"])
        _require(type(item["created_at"]) is str, "invalid refresh comment manifest time")
        try:
            created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid refresh comment manifest time") from None
        _require(created.tzinfo is not None, "invalid refresh comment manifest time")
        _match(item["content_digest"], _DIGEST)
        _require(item["comment_uuid"] not in seen, "duplicate refresh comment manifest")
        seen.add(item["comment_uuid"])
        comment = normalized.get(item["comment_uuid"])
        if item["type"] == "comment":
            manifest_comment_ids.add(item["comment_uuid"])
            _require(comment is not None and item["author_id"] == comment.author_id
                     and item["author_type"] == comment.author_type
                     and item["revision"] == comment.revision
                     and item["content_digest"] == hashlib.sha256(
                         comment.content.encode("utf-8")).hexdigest(),
                     "refresh comment manifest mismatch")
        else:
            _require(comment is None, "system history became an authorizing comment")
    _require(manifest_comment_ids == set(normalized),
             "refresh comment manifest mismatch")
    _require(state["comment_manifest"] == sorted(state["comment_manifest"],
                                                  key=lambda item: item["comment_uuid"]),
             "noncanonical refresh comment manifest")
    parents = {item["comment_uuid"]: item["parent_id"] for item in state["comment_manifest"]}
    for item in state["comment_manifest"]:
        _require(item["parent_id"] is None or item["parent_id"] in seen,
                 "incomplete refresh comment manifest thread")
        lineage, current = set(), item["comment_uuid"]
        while current is not None:
            _require(current not in lineage, "cyclic refresh comment manifest thread")
            lineage.add(current)
            current = parents[current]
    return state


def _grant_in_state(request: RefreshRequest, state: dict, grant: RefreshComment) -> None:
    validate_grant(grant, request)
    matches = [item for item in state["comments"] if item.get("comment_uuid") == grant.comment_uuid]
    _require(matches == [asdict(grant)], "grant is not the authoritative scoped comment")
    granting = []
    for record in state["comments"]:
        try:
            candidate = RefreshComment(**record)
            validate_grant(candidate, request)
        except (ValueError, TypeError):
            continue
        granting.append(candidate.comment_uuid)
    _require(granting == [grant.comment_uuid], "ambiguous refresh grants")


def _source_authority(payload: dict, state: dict) -> dict:
    parent, source, assignment = payload["parent"], payload["source"], payload["assignment"]
    children = state["children"]
    matches = [child for child in children if child.get("detail", {}).get("id") == source["child_id"]]
    _require(len(matches) == 1, "missing or duplicate source child")
    child = _object(matches[0], "detail metadata evidence comment_manifest")
    detail, metadata, evidence = child["detail"], child["metadata"], child["evidence"]
    _require(type(detail) is dict and type(metadata) is dict and type(evidence) is dict, "invalid source")
    expected = {"id": source["child_id"], "identifier": source["child_identifier"],
                "parent_issue_id": parent["id"], "workspace_id": payload["workspace_id"],
                "stage": 1, "status": "done", "project_id": assignment["project_id"],
                "assignee_type": "agent", "assignee_id": assignment["engineer_id"]}
    _require(all(type(detail.get(k)) is type(v) and detail[k] == v for k, v in expected.items()),
             "source child authority mismatch")
    expected_metadata = {"eventra.workflow.version": "2", "eventra.phase.kind": "implementation",
                         "eventra.phase.attempt": "0", "eventra.phase.result": "pass",
                         "eventra.phase.sha.frontend": source["sha"], "eventra.phase.pr": payload["pr"]["url"],
                         "eventra.phase.evidence_comment": source["evidence_uuid"],
                         "eventra.phase.creation_action": parent["last_action"],
                         "eventra.phase.target": "repository:frontend", "eventra.phase.role": "frontend_engineer"}
    _require(all(metadata.get(k) == v for k, v in expected_metadata.items()), "source metadata mismatch")
    _require(not any(k.startswith(("eventra.repair.", "eventra.refresh.")) for k in metadata)
             and "eventra.phase.sha.backend" not in metadata, "source provenance mismatch")
    allowed = set(expected_metadata) | {"eventra.phase.failure_repositories"}
    _require(not any(k.startswith(("eventra.phase.", "eventra.workflow.")) and k not in allowed for k in metadata),
             "unknown source authority field")
    _require(metadata.get("eventra.phase.failure_repositories", "[]") == "[]", "source failure ownership")
    try:
        comment = RefreshComment(**evidence)
    except TypeError:
        raise ValueError("invalid source evidence") from None
    _comment(comment, source["child_id"], "agent")
    _require(comment.author_id == assignment["engineer_id"] and comment.comment_uuid == source["evidence_uuid"]
             and comment.revision == source["evidence_revision"]
             and hashlib.sha256(comment.content.encode()).hexdigest() == source["evidence_digest"],
             "source evidence changed")
    return child


def _shared_authority(payload: dict, state: dict, *,
                      allow_managed_merge: bool = False) -> None:
    parent, assignment = state["parent"], state["assignment"]
    expected = payload["assignment"]
    base_assignment = {key: expected[key] for key in (
        "project_id", "squad_id", "lead_id", "engineer_id")}
    _require(assignment.get("workspace_id") == payload["workspace_id"]
             and all(assignment.get(k) == v for k, v in base_assignment.items()),
             "assignment authority mismatch")
    if payload["schema_version"] == 2:
        roles = assignment.get("roles")
        _require(type(roles) is dict
                 and roles.get("independent_reviewer") == expected["reviewer_id"]
                 and roles.get("integration_qa") == expected["integration_qa_id"],
                 "gate assignment authority mismatch")
    _require(parent.get("workspace_id") == payload["workspace_id"]
             and parent.get("id") == payload["parent"]["id"]
             and parent.get("identifier") == payload["parent"]["identifier"]
             and parent.get("parent_issue_id") is None
             and parent.get("project_id") == expected["project_id"]
             and parent.get("assignee_type") == "squad"
             and parent.get("assignee_id") == expected["squad_id"], "parent assignment mismatch")
    _require(state["tool"] == {"sha": payload["control_tool_sha"], "git_version": payload["git_version"]},
             "control tool identity mismatch")
    pr = state["pr"]
    _require(all(pr.get(k) == v for k, v in payload["pr"].items())
             and ((pr.get("state") == "open" and pr.get("merged") is False)
                  or (allow_managed_merge and pr.get("state") == "merged"
                      and pr.get("merged") is True)),
             "managed PR identity mismatch")
    prerequisite = state["prerequisite"]
    _require(all(prerequisite.get(k) == v for k, v in payload["prerequisite"].items())
             and prerequisite.get("merged") is True
             and prerequisite.get("ancestor_sha") == payload["prerequisite"]["merge_sha"],
             "prerequisite or base identity mismatch")
    _source_authority(payload, state)


def _entry_authority(payload: dict, state: dict) -> None:
    _shared_authority(payload, state)
    parent, expected = state["parent"], payload["parent"]
    _require(type(parent.get("revision")) is int and parent["revision"] == expected["revision"]
             and parent.get("status") == expected["status"], "frozen parent revision or status changed")
    metadata = state["metadata"]
    required = {"eventra.workflow.version": "2", "eventra.workflow.classification": "frontend-only",
                "eventra.workflow.attempt": "0", "eventra.workflow.next_stage": "2",
                "eventra.workflow.merge_state": "not_ready", "eventra.workflow.last_action": expected["last_action"],
                "eventra.workflow.frontend_sha": payload["source"]["sha"]}
    _require(all(metadata.get(k) == v for k, v in required.items()), "refresh entry metadata mismatch")
    _require(not any(k.startswith(("eventra.refresh.", "eventra.repair.", "eventra.smoke."))
                     or (k.startswith("eventra.workflow.") and k not in required) for k in metadata),
             "refresh entry has reservations, consumption or unknown authority")
    if payload["schema_version"] == 1:
        _require(len(state["children"]) == 1,
                 "refresh requires a unique Stage 1 and no later children")
    else:
        expected_gates = payload["supersession"]["gates"]
        _require(_pristine_gate_records(state) == expected_gates,
                 "pristine gate authority changed")
    _require(state["pr"].get("head_sha") == payload["source"]["sha"], "managed head drift")
    active = [run for run in state["runs"] if run.get("status") in
              {"queued", "dispatched", "running", "waiting_local_directory"}]
    _require(len(active) <= 1 and all(run.get("issue_id") == parent["id"]
             and run.get("agent_id") == payload["assignment"]["lead_id"] for run in active),
             "active child or nonunique Lead writer")


def freeze_refresh_request(snapshot: RefreshSnapshot, *,
                           supersede_pristine_gates: bool = False) -> RefreshRequest:
    """Build a versioned request from one trusted pre-registration snapshot."""
    state = _snapshot(snapshot)
    _require(type(supersede_pristine_gates) is bool,
             "invalid supersession selection")
    authority = authority_digest(
        snapshot, supersede_pristine_gates=supersede_pristine_gates)
    sources = [child for child in state["children"]
               if child.get("detail", {}).get("stage") == 1]
    _require(len(sources) == 1, "refresh freeze requires one source child")
    source = _object(sources[0], "detail metadata evidence comment_manifest")
    detail, source_metadata, evidence = source["detail"], source["metadata"], source["evidence"]
    _require(type(evidence) is dict, "missing source evidence")
    parent, metadata, assignment = state["parent"], state["metadata"], state["assignment"]
    required_metadata = {"eventra.workflow.attempt", "eventra.workflow.next_stage",
                         "eventra.workflow.merge_state", "eventra.workflow.last_action"}
    _require(required_metadata <= set(metadata), "refresh entry metadata mismatch")
    protocol = 2 if supersede_pristine_gates else 1
    payload = {
        "schema_version": protocol,
        "workspace_id": parent["workspace_id"],
        "parent": {"id": parent["id"], "identifier": parent["identifier"],
                   "revision": parent["revision"], "stage": 1,
                   "attempt": int(metadata["eventra.workflow.attempt"]),
                   "next_stage": int(metadata["eventra.workflow.next_stage"]),
                   "status": parent["status"], "merge_state": metadata["eventra.workflow.merge_state"],
                   "last_action": metadata["eventra.workflow.last_action"]},
        "source": {"child_id": detail["id"], "child_identifier": detail["identifier"],
                   "sha": source_metadata["eventra.phase.sha.frontend"],
                   "evidence_uuid": evidence["comment_uuid"], "evidence_revision": evidence["revision"],
                   "evidence_digest": hashlib.sha256(evidence["content"].encode("utf-8")).hexdigest()},
        "pr": {key: state["pr"][key] for key in ("url", "repository", "head_ref", "base_ref")},
        "prerequisite": {key: state["prerequisite"][key] for key in ("pr_url", "merge_sha", "base_sha")},
        "assignment": {key: assignment[key] for key in (
            "project_id", "squad_id", "lead_id", "engineer_id")},
        "baseline": {"authority_digest": authority,
                     "comments_digest": hashlib.sha256(canonical_json(state["comment_manifest"]).encode("utf-8")).hexdigest()},
        "refresh_stage": 2 if protocol == 1 else 3,
        "refresh_generation": 1,
        "staging_ref_prefix": STAGING_PREFIX, "tree_transform": "clean-two-parent-merge-v1",
        "merge_permission": "hold", "control_tool_sha": state["tool"]["sha"],
        "git_version": state["tool"]["git_version"],
    }
    if protocol == 2:
        payload["assignment"].update({
            "reviewer_id": assignment["roles"]["independent_reviewer"],
            "integration_qa_id": assignment["roles"]["integration_qa"],
        })
        payload["fresh_gate_stage"] = 4
        payload["supersession"] = {
            "gate_stage": 2, "mode": "cancel-pristine-gates-v1",
            "gates": _pristine_gate_records(state),
        }
    request = build_request(payload)
    _entry_authority(payload, state)
    validate_metadata_budget(metadata, request=request)
    return request


def validate_initial_refresh_progress(request: RefreshRequest,
                                      snapshot: RefreshSnapshot) -> InitialRefreshProgress:
    """Prove the exact pre-reservation prefix against the request's frozen baselines."""
    payload, state = _request(request), _snapshot(snapshot)
    metadata = state["metadata"]
    envelope = canonical_json({"payload": payload, "digest": request.digest,
                               "staging_ref": request.staging_ref})
    prefix = [
        (REFRESH_PREFIX + "request", envelope),
        (REFRESH_PREFIX + "version", str(payload["schema_version"])),
        (REFRESH_PREFIX + "merge_permission", "hold"),
        (REFRESH_PREFIX + "request_digest", request.digest),
        (REFRESH_PREFIX + "request_comment", None),
        (REFRESH_PREFIX + "authorization_comment", None),
    ]
    refresh_keys = {key for key in metadata if key.startswith(REFRESH_PREFIX)}
    _require(refresh_keys <= {key for key, _ in prefix}, "unknown initial refresh metadata")
    metadata_writes, missing = 0, False
    for key, expected in prefix:
        if key not in metadata:
            missing = True
            continue
        _require(not missing, "initial refresh metadata is not an exact prefix")
        if expected is None:
            _uuid(metadata[key])
        else:
            _require(metadata[key] == expected, "initial refresh metadata value mismatch")
        metadata_writes += 1

    manifest = state["comment_manifest"]
    comments = {item["comment_uuid"]: RefreshComment(**item) for item in state["comments"]}
    manifest_by_id = {item["comment_uuid"]: item for item in manifest}
    request_candidates, grant_candidates = [], []
    for comment_id, comment in comments.items():
        identity = manifest_by_id[comment_id]
        if comment.revision != 1 or identity["parent_id"] is not None:
            continue
        try:
            parsed = parse_request(_block(
                comment.content, "request", only=True,
                protocol=payload["schema_version"]))
            if (parsed == request and (comment.author_type == "member" or
                    (comment.author_type == "agent" and
                     comment.author_id == payload["assignment"]["lead_id"]))):
                request_candidates.append(comment)
        except (ValueError, TypeError):
            pass
        try:
            validate_grant(comment, request)
            grant_candidates.append(comment)
        except (ValueError, TypeError):
            pass

    baseline_digest = payload["baseline"]["comments_digest"]
    possibilities: list[tuple[int, RefreshComment | None, RefreshComment | None]] = []
    def remaining_digest(removed: set[str]) -> str:
        remaining = [item for item in manifest if item["comment_uuid"] not in removed]
        return hashlib.sha256(canonical_json(remaining).encode("utf-8")).hexdigest()
    if remaining_digest(set()) == baseline_digest:
        possibilities.append((0, None, None))
    for request_comment in request_candidates:
        if remaining_digest({request_comment.comment_uuid}) == baseline_digest:
            possibilities.append((1, request_comment, None))
        for grant_comment in grant_candidates:
            if grant_comment.comment_uuid == request_comment.comment_uuid:
                continue
            if remaining_digest({request_comment.comment_uuid, grant_comment.comment_uuid}) == baseline_digest:
                request_time = datetime.fromisoformat(
                    manifest_by_id[request_comment.comment_uuid]["created_at"].replace("Z", "+00:00"))
                grant_time = datetime.fromisoformat(
                    manifest_by_id[grant_comment.comment_uuid]["created_at"].replace("Z", "+00:00"))
                _require(request_time <= grant_time, "grant predates refresh request")
                possibilities.append((2, request_comment, grant_comment))
    _require(len(possibilities) == 1, "initial refresh comments do not match frozen baseline")
    comment_writes, request_comment, grant_comment = possibilities[0]
    _require(metadata_writes >= 4 or comment_writes == 0,
             "refresh comments precede complete pause metadata")
    if metadata_writes >= 5:
        _require(request_comment is not None
                 and metadata[prefix[4][0]] == request_comment.comment_uuid,
                 "request comment binding mismatch")
    if metadata_writes >= 6:
        _require(grant_comment is not None
                 and metadata[prefix[5][0]] == grant_comment.comment_uuid,
                 "grant comment binding mismatch")

    frozen = _load_json(canonical_json(state), 4_194_304)
    for key, _ in prefix[:metadata_writes]:
        del frozen["metadata"][key]
    frozen["parent"]["revision"] = payload["parent"]["revision"]
    frozen["parent"]["metadata"] = frozen["metadata"]
    frozen_snapshot = RefreshSnapshot(canonical_json(frozen))
    _require(authority_digest(
        frozen_snapshot,
        supersede_pristine_gates=payload["schema_version"] == 2,
    ) == payload["baseline"]["authority_digest"],
             "frozen refresh authority changed")
    _entry_authority(payload, frozen_snapshot.state())
    _require(state["parent"].get("revision") == payload["parent"]["revision"]
             + metadata_writes + comment_writes,
             "initial refresh revision is not explained by exact writes")
    validate_metadata_budget(metadata, request=request)
    return InitialRefreshProgress(request, metadata_writes, comment_writes,
                                  request_comment, grant_comment)


def admit_refresh(request: RefreshRequest, snapshot: RefreshSnapshot, grant: RefreshComment) -> None:
    """Admit only the complete pre-reservation prefix and its exact member grant."""
    progress = validate_initial_refresh_progress(request, snapshot)
    _require(progress.metadata_writes == 6 and progress.comment_writes == 2
             and progress.grant_comment == grant, "incomplete refresh admission prefix")


REFRESH_PREFIX = "eventra.refresh."
REFRESH_FIELDS = frozenset({"version", "request", "request_comment", "request_digest", "authorization_comment",
                            "reservation", "consumed", "adoption", "merge_permission"})
V2_REFRESH_FIELDS = REFRESH_FIELDS | {"supersession"}
RESERVATION_STATES = ("reserved", "child_initialized", "child_dispatched", "candidate_registered", "published", "adopted")
V2_RESERVATION_STATES = ("reserved", "review_cancelled", "gates_cancelled",
                         "child_initialized", "child_dispatched",
                         "candidate_registered", "published", "adopted")
V2_RESERVATION_FIELDS = frozenset({
    "version", "request_digest", "request_uuid", "authorization_uuid",
    "action_key", "state", "parent_id", "parent_identifier", "source", "pr",
    "prerequisite", "control_tool_sha", "gates", "refresh_stage",
    "fresh_gate_stage", "child_id", "child_identifier", "child_position",
    "prepared", "parent_status_category", "parent_position",
    "parent_projection_digest",
})
V2_PUBLICATION_RESERVATION_FIELDS = frozenset({
    "version", "request_digest", "request_uuid", "authorization_uuid",
    "action_key", "state", "child_id", "child_identifier",
    "child_position", "prepared", "parent_status_category",
    "parent_position", "parent_projection_digest",
})


@dataclass(frozen=True)
class SupersessionProgress:
    cancelled_roles: tuple[str, ...]
    next_role: str | None


@dataclass(frozen=True)
class RefreshDecision:
    kind: str
    action_key: str | None
    reason: str


def refresh_action(request: RefreshRequest) -> str:
    payload = _request(request)
    return (f"2:{payload['parent']['identifier']}:create_refresh_stage:0:frontend:"
            f"{payload['source']['sha']}:next-stage:{payload['refresh_stage']}:"
            f"refresh:{payload['schema_version']}:{request.digest}")


def _refresh_stage(request: RefreshRequest) -> int:
    return _request(request)["refresh_stage"]


def _fresh_gate_stage(request: RefreshRequest) -> int:
    payload = _request(request)
    return payload.get("fresh_gate_stage", payload["refresh_stage"] + 1)


def _prepared_checkpoint(
        request: RefreshRequest, prepared: PreparedCandidate) -> dict[str, str]:
    encoded = canonical_json(asdict(prepared))
    if refresh_protocol(request) == 1:
        return asdict(prepared)
    return {"digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}


def _v2_publication_reservation(
        request: RefreshRequest, state: dict, reservation: object) -> dict:
    _require(type(reservation) is dict
             and set(reservation) == V2_PUBLICATION_RESERVATION_FIELDS,
             "invalid v2 publication reservation shape")
    value = reservation
    _integer(value["version"], 2)
    for key in ("request_uuid", "authorization_uuid", "child_id"):
        _uuid(value[key])
    _match(value["child_identifier"], _ISSUE)
    _match(value["parent_projection_digest"], _DIGEST)
    _require(value["request_digest"] == request.digest
             and value["action_key"] == refresh_action(request)
             and value["state"] in {
                 "candidate_registered", "published", "adopted"}
             and type(value["child_position"]) is int
             and type(value["parent_status_category"]) is str
             and type(value["parent_position"]) is int
             and state["parent"].get("position") == value["parent_position"],
             "invalid v2 publication reservation identity")
    prepared = _object(value["prepared"], "digest")
    _match(prepared["digest"], _DIGEST)
    feature = refresh_metadata(state["metadata"])
    _require(feature is not None and feature.get("reservation") == value
             and feature.get("request_comment") == value["request_uuid"]
             and feature.get("authorization_comment") ==
                 value["authorization_uuid"],
             "v2 publication reservation binding mismatch")
    return value


def _v2_reservation(request: RefreshRequest, state: dict,
                    reservation: object, *, require_projection: bool = True) -> dict:
    payload = _request(request)
    _require(payload["schema_version"] == 2, "supersession requires protocol v2")
    _require(type(reservation) is dict
             and set(reservation) == V2_RESERVATION_FIELDS,
             "invalid v2 reservation shape")
    value = reservation
    _integer(value["version"], 2)
    for key in ("request_uuid", "authorization_uuid", "parent_id"):
        _uuid(value[key])
    _match(value["parent_identifier"], _ISSUE)
    _match(value["control_tool_sha"], _SHA)
    _match(value["parent_projection_digest"], _DIGEST)
    _require(value["request_digest"] == request.digest
             and value["action_key"] == refresh_action(request)
             and value["state"] in V2_RESERVATION_STATES,
             "invalid v2 reservation identity")
    _require(value["parent_id"] == payload["parent"]["id"]
             and value["parent_identifier"] == payload["parent"]["identifier"]
             and value["source"] == payload["source"]
             and value["pr"] == payload["pr"]
             and value["prerequisite"] == payload["prerequisite"]
             and value["control_tool_sha"] == payload["control_tool_sha"]
             and value["gates"] == payload["supersession"]["gates"]
             and value["refresh_stage"] == payload["refresh_stage"] == 3
             and value["fresh_gate_stage"] == payload["fresh_gate_stage"] == 4,
             "v2 reservation authority mismatch")
    _require(type(value["parent_status_category"]) is str
             and type(value["parent_position"]) is int
             and state["parent"].get("position") == value["parent_position"],
             "v2 reservation parent layout mismatch")
    feature = refresh_metadata(state["metadata"])
    _require(feature is not None and feature.get("reservation") == value
             and feature.get("request_comment") == value["request_uuid"]
             and feature.get("authorization_comment") == value["authorization_uuid"],
             "v2 reservation comment binding mismatch")
    if require_projection:
        _require(value["parent_projection_digest"] == parent_projection_digest(state),
                 "v2 reservation parent projection drift")
    if value["state"] in {"reserved", "review_cancelled", "gates_cancelled"}:
        _require(value["child_id"] is None
                 and value["child_identifier"] is None
                 and value["child_position"] is None
                 and value["prepared"] is None,
                 "v2 cancellation checkpoint created child authority")
    else:
        _uuid(value["child_id"])
        _match(value["child_identifier"], _ISSUE)
        _require(type(value["child_position"]) is int,
                 "v2 lifecycle checkpoint lacks child authority")
        if value["state"] in {"child_initialized", "child_dispatched"}:
            _require(value["prepared"] is None,
                     "v2 initialization checkpoint has prepared authority")
        else:
            _require(type(value["prepared"]) is dict,
                     "v2 publication checkpoint lacks prepared authority")
    return value


def validate_supersession_progress(
        request: RefreshRequest, snapshot: RefreshSnapshot,
        reservation: object) -> SupersessionProgress:
    """Accept only the three ordered, request-bound gate cancellation prefixes."""
    payload, state = _request(request), _snapshot(snapshot)
    value = _v2_reservation(request, state, reservation)
    _require(value["state"] in {"reserved", "review_cancelled", "gates_cancelled"},
             "supersession cancellation phase is complete")
    _shared_authority(payload, state)
    feature = refresh_metadata(state["metadata"])
    _request_comment(request, state, feature)
    grants = [item for item in state["comments"]
              if item.get("comment_uuid") == value["authorization_uuid"]]
    _require(len(grants) == 1, "missing supersession grant")
    _grant_in_state(request, state, RefreshComment(**grants[0]))
    metadata = state["metadata"]
    _require(metadata.get("eventra.workflow.next_stage") == "2"
             and metadata.get("eventra.workflow.last_action") ==
                 payload["parent"]["last_action"]
             and metadata.get("eventra.workflow.frontend_sha") ==
                 payload["source"]["sha"]
             and metadata.get("eventra.workflow.attempt") == "0"
             and metadata.get("eventra.workflow.merge_state") == "not_ready"
             and state["parent"].get("status") == "blocked"
             and state["parent"].get("status_category") ==
                 value["parent_status_category"]
             and state["pr"].get("head_sha") == payload["source"]["sha"],
             "supersession parent authority changed")
    _require(len(state["children"]) == 3,
             "supersession child membership changed")

    observed = []
    for expected in value["gates"]:
        matches = [item for item in state["children"]
                   if item.get("detail", {}).get("id") == expected["id"]]
        _require(len(matches) == 1, "missing or duplicate supersession gate")
        gate = _object(matches[0], "detail metadata evidence comment_manifest")
        detail = _issue_detail(gate["detail"], "unknown supersession gate field")
        _require(detail.get("identifier") == expected["identifier"]
                 and detail.get("title") == expected["title"]
                 and detail.get("metadata") == gate["metadata"]
                 and gate["metadata"] == {}
                 and gate["evidence"] is None
                 and gate["comment_manifest"] == [],
                 "supersession gate history changed")
        gate_runs = [run for run in state["runs"]
                     if run.get("issue_id") == expected["id"]]
        _require(not gate_runs, "supersession gate acquired a run")
        status = (detail.get("status"), detail.get("status_category"),
                  detail.get("revision"))
        _require(status in {("backlog", "backlog", 1),
                            ("cancelled", "cancelled", 2)},
                 "supersession gate status is not an exact cancellation effect")
        restored_detail = {
            **detail, "status": expected["status"],
            "status_category": "backlog", "revision": expected["revision"],
        }
        authority = {
            "detail": {key: item for key, item in restored_detail.items()
                       if key not in _VOLATILE_DETAIL_FIELDS},
            "metadata": gate["metadata"], "evidence": gate["evidence"],
            "comment_manifest": gate["comment_manifest"], "runs": gate_runs,
        }
        _require(hashlib.sha256(canonical_json(authority).encode("utf-8")).hexdigest()
                 == expected["authority_digest"],
                 "supersession gate authority digest changed")
        observed.append(status[0] == "cancelled")

    _require(observed in ([False, False], [True, False], [True, True]),
             "gate cancellation is not an ordered prefix")
    count = observed.count(True)
    minimum = {"reserved": 0, "review_cancelled": 1, "gates_cancelled": 2}.get(
        value["state"], 2)
    maximum = {"reserved": 1, "review_cancelled": 2, "gates_cancelled": 2}.get(
        value["state"], 2)
    _require(minimum <= count <= maximum,
             "gate cancellation contradicts its durable checkpoint")

    frozen = _load_json(canonical_json(state), 4_194_304)
    del frozen["metadata"][REFRESH_PREFIX + "reservation"]
    frozen["parent"]["metadata"] = frozen["metadata"]
    frozen["parent"]["revision"] = payload["parent"]["revision"] + 8
    for expected in value["gates"]:
        gate = next(item for item in frozen["children"]
                    if item["detail"]["id"] == expected["id"])
        gate["detail"]["status"] = expected["status"]
        gate["detail"]["status_category"] = "backlog"
        gate["detail"]["revision"] = expected["revision"]
    initial = validate_initial_refresh_progress(
        request, RefreshSnapshot(canonical_json(frozen)))
    _require(initial.metadata_writes == 6 and initial.comment_writes == 2
             and initial.request_comment is not None
             and initial.request_comment.comment_uuid == value["request_uuid"]
             and initial.grant_comment is not None
             and initial.grant_comment.comment_uuid == value["authorization_uuid"],
             "complete frozen authority changed during supersession")
    roles = ("independent_reviewer", "integration_qa")
    return SupersessionProgress(roles[:count], None if count == 2 else roles[count])


def validate_supersession_initialization_authority(
        request: RefreshRequest, snapshot: RefreshSnapshot,
        reservation: object) -> None:
    """Rebuild the gates-cancelled baseline beneath an initialization prefix."""
    payload, state = _request(request), _snapshot(snapshot)
    value = _v2_reservation(
        request, state, reservation, require_projection=False)
    _require(value["state"] in {
        "gates_cancelled", "child_initialized", "child_dispatched"},
             "invalid v2 initialization state")
    base_ids = {payload["source"]["child_id"],
                *(gate["id"] for gate in payload["supersession"]["gates"])}
    refresh_children = [item for item in state["children"]
                        if item["detail"]["id"] not in base_ids]
    _require(len(refresh_children) <= 1,
             "duplicate v2 refresh initialization child")

    restored = _load_json(canonical_json(state), 4_194_304)
    refresh_ids = {item["detail"]["id"] for item in refresh_children}
    restored["children"] = [item for item in restored["children"]
                            if item["detail"]["id"] not in refresh_ids]
    restored["runs"] = [run for run in restored["runs"]
                        if run["issue_id"] not in refresh_ids]
    restored["metadata"]["eventra.workflow.next_stage"] = str(
        payload["parent"]["next_stage"])
    restored["metadata"]["eventra.workflow.last_action"] = (
        payload["parent"]["last_action"])
    restored["parent"]["status"] = payload["parent"]["status"]
    restored["parent"]["status_category"] = value["parent_status_category"]
    restored["parent"]["revision"] = payload["parent"]["revision"] + 11
    base_reservation = {
        **value, "state": "gates_cancelled", "child_id": None,
        "child_identifier": None, "child_position": None, "prepared": None,
    }
    restored["metadata"].pop(REFRESH_PREFIX + "supersession", None)
    base_reservation["parent_projection_digest"] = parent_projection_digest(restored)
    restored["metadata"][REFRESH_PREFIX + "reservation"] = canonical_json(
        base_reservation)
    restored["parent"]["metadata"] = restored["metadata"]
    validate_supersession_progress(
        request, RefreshSnapshot(canonical_json(restored)), base_reservation)


def refresh_metadata(metadata: dict[str, str]) -> dict[str, Any] | None:
    """Reject partial/unknown feature markers instead of falling back to legacy gates."""
    _require(type(metadata) is dict and all(type(k) is str and type(v) is str for k, v in metadata.items()))
    values = {k.removeprefix(REFRESH_PREFIX): v for k, v in metadata.items() if k.startswith(REFRESH_PREFIX)}
    if not values:
        return None
    _require({"version", "request", "request_digest", "merge_permission"}
             <= set(values), "incomplete refresh feature metadata")
    request = parse_request(values["request"])
    allowed = V2_REFRESH_FIELDS if refresh_protocol(request) == 2 else REFRESH_FIELDS
    _require(set(values) <= allowed, "incomplete refresh feature metadata")
    _require(values["version"] == str(refresh_protocol(request))
             and values["merge_permission"] == "hold",
             "refresh merge hold is immutable")
    _require(values["request"] == canonical_json({"payload": request.payload(), "digest": request.digest,
                                                  "staging_ref": request.staging_ref}), "noncanonical refresh request")
    _require(values["request_digest"] == request.digest, "refresh feature request mismatch")
    for key in ("request_comment", "authorization_comment"):
        if key in values:
            _uuid(values[key])
    for key in ("reservation", "consumed", "adoption", "supersession"):
        if key in values:
            raw = _load_json(values[key], MAX_COMMENT_BYTES)
            _require(type(raw) is dict and values[key] == canonical_json(raw), "noncanonical refresh receipt")
            values[key] = raw
    return values


def parent_projection_digest(state: dict) -> str:
    """Bind the last observed parent effect; never ignore revision drift globally.

    Reservation excludes itself and the issue detail's echoed KV map to avoid
    digest recursion. Server-maintained activity clocks are not business effects;
    revision and all other authority remain bound. The separate metadata read is
    bound exactly once. The writer must compute
    an expected projection for each effect and re-read it (Task 5); this hash alone
    does not prove that an external writer was authorized.
    """
    metadata = {k: v for k, v in state["metadata"].items() if k != REFRESH_PREFIX + "reservation"}
    parent = {k: v for k, v in state["parent"].items() if k not in {"metadata", "updated_at", "last_activity_at"}}
    return hashlib.sha256(canonical_json({"parent": parent, "metadata": metadata}).encode()).hexdigest()


def _request_comment(request: RefreshRequest, state: dict, feature: dict) -> None:
    wanted = feature.get("request_comment")
    _require(wanted is not None, "request comment missing")
    matches = [item for item in state["comments"] if item.get("comment_uuid") == wanted]
    _require(len(matches) == 1, "request comment missing or duplicate")
    comment = RefreshComment(**matches[0])
    payload = request.payload()
    _comment(comment, payload["parent"]["id"], comment.author_type)
    _require(comment.author_type == "member" or (comment.author_type == "agent"
             and comment.author_id == payload["assignment"]["lead_id"]), "request author mismatch")
    parsed = parse_request(_block(
        comment.content, "request", only=True,
        protocol=payload["schema_version"]))
    _require(parsed == request, "request comment changed")


def _refresh_child(request: RefreshRequest, state: dict) -> tuple[dict, PreparedCandidate | None]:
    payload = request.payload()
    stage = _refresh_stage(request)
    matches = [child for child in state["children"]
               if child["detail"]["stage"] == stage
               and child["metadata"].get("eventra.phase.kind") == "refresh"]
    _require(len(matches) == 1, "refresh stage membership mismatch")
    child = _object(matches[0], "detail metadata evidence comment_manifest")
    detail, metadata = child["detail"], child["metadata"]
    _uuid(detail["id"])
    _match(detail["identifier"], _ISSUE)
    _require(detail["id"] not in (payload["parent"]["id"], payload["source"]["child_id"])
             and detail["identifier"] not in (payload["parent"]["identifier"], payload["source"]["child_identifier"])
             and detail["workspace_id"] == payload["workspace_id"] and detail["parent_issue_id"] == payload["parent"]["id"]
             and detail["project_id"] == payload["assignment"]["project_id"]
             and detail["assignee_type"] == "agent" and detail["assignee_id"] == payload["assignment"]["engineer_id"],
             "refresh child assignment mismatch")
    expected = {"eventra.workflow.version": "2", "eventra.phase.kind": "refresh", "eventra.phase.attempt": "0",
                "eventra.phase.target": "repository:frontend", "eventra.phase.role": "frontend_engineer",
                "eventra.phase.creation_action": refresh_action(request), "eventra.phase.pr": payload["pr"]["url"],
                "eventra.refresh.version": str(payload["schema_version"]),
                "eventra.refresh.request_digest": request.digest,
                "eventra.refresh.source_sha": payload["source"]["sha"]}
    _require(all(metadata.get(k) == v for k, v in expected.items()), "refresh child provenance mismatch")
    allowed = set(expected) | {"eventra.phase.sha.frontend", "eventra.phase.result", "eventra.phase.evidence_comment",
                              "eventra.phase.failure_repositories"}
    _require(not any(k.startswith(("eventra.phase.", "eventra.workflow.", REFRESH_PREFIX, "eventra.repair."))
                     and k not in allowed for k in metadata), "unknown refresh child authority")
    _require(metadata.get("eventra.phase.failure_repositories", "[]") == "[]", "refresh is not repair")
    result = metadata.get("eventra.phase.result")
    _require(result not in {"fail", "blocked"}, "refresh preparation failed; no automatic repair")
    _require(detail["status"] in {"backlog", "todo", "in_progress", "in_review", "done"}, "refresh child status")
    if detail["status"] != "done":
        _require(result is None and child["evidence"] is None
                 and metadata.get("eventra.phase.sha.frontend") == payload["source"]["sha"], "partial preparation requires executor")
        return child, None
    _require(result == "pass" and type(child["evidence"]) is dict, "missing prepared PASS")
    comment = RefreshComment(**child["evidence"])
    prepared = parse_prepared(comment, request, detail["id"])
    body = _block(comment.content, "prepared", protocol=payload["schema_version"])
    _require(body["context_receipt"]["task_id"] == detail["identifier"]
             and metadata.get("eventra.phase.sha.frontend") == prepared.target_sha
             and metadata.get("eventra.phase.evidence_comment") == prepared.evidence_uuid, "prepared completion mismatch")
    return child, prepared


def _reserved_refresh_child(request: RefreshRequest, state: dict) -> None:
    """Accept only the exact child-create/metadata prefix recoverable by the executor."""
    payload = request.payload()
    base_ids = {payload["source"]["child_id"]}
    if payload["schema_version"] == 2:
        base_ids.update(gate["id"] for gate in payload["supersession"]["gates"])
    base_count = len(base_ids)
    if len(state["children"]) == base_count:
        return
    _require(len(state["children"]) == base_count + 1,
             "reserved refresh child membership mismatch")
    matches = [item for item in state["children"]
               if item["detail"]["id"] not in base_ids]
    _require(len(matches) == 1, "reserved refresh child membership mismatch")
    child = _object(matches[0], "detail metadata evidence comment_manifest")
    detail, metadata = child["detail"], child["metadata"]
    action = refresh_action(request)
    expected_detail = {
        "parent_issue_id": payload["parent"]["id"], "workspace_id": payload["workspace_id"],
        "stage": _refresh_stage(request), "status": "backlog",
        "project_id": payload["assignment"]["project_id"],
        "assignee_type": "agent", "assignee_id": payload["assignment"]["engineer_id"],
        "title": f"{payload['parent']['identifier']}: prepare candidate refresh",
        "description": canonical_json({"schema_version": payload["schema_version"],
                                       "request_digest": request.digest,
                                       "action_key": action}),
    }
    prefix = [
        ("eventra.workflow.version", "2"), ("eventra.phase.kind", "refresh"),
        ("eventra.phase.attempt", "0"), ("eventra.phase.target", "repository:frontend"),
        ("eventra.phase.role", "frontend_engineer"), ("eventra.phase.creation_action", action),
        ("eventra.phase.pr", payload["pr"]["url"]),
        (REFRESH_PREFIX + "version", str(payload["schema_version"])),
        (REFRESH_PREFIX + "request_digest", request.digest),
        (REFRESH_PREFIX + "source_sha", payload["source"]["sha"]),
        ("eventra.phase.sha.frontend", payload["source"]["sha"]),
    ]
    _require(all(detail.get(key) == value for key, value in expected_detail.items())
             and child["evidence"] is None and metadata == dict(prefix[:len(metadata)])
             and detail.get("revision") == 1 + len(metadata)
             and not any(run["issue_id"] == detail["id"] for run in state["runs"]),
             "reserved refresh child is not an exact initialization prefix")


def _receipt_match(feature: dict, request: RefreshRequest, prepared: PreparedCandidate, *, partial: bool = False) -> None:
    protocol = refresh_protocol(request)
    stage = _refresh_stage(request)
    expected = {"version": protocol, "request_digest": request.digest,
                "authorization_uuid": feature["authorization_comment"], "child_id": prepared.child_id, "target_sha": prepared.target_sha}
    if not partial or "consumed" in feature:
        consumed = _object(feature.get("consumed"), "version request_digest authorization_uuid child_id target_sha")
        _integer(consumed["version"], protocol)
        _require(consumed == expected and "adoption" in feature, "refresh consumption mismatch")
    if not partial or "adoption" in feature:
        adoption = _object(feature.get("adoption"), "version request_digest authorization_uuid child_id target_sha "
                           "source_sha prerequisite_sha evidence_uuid evidence_digest stage control_tool_sha")
        _integer(adoption["version"], protocol)
        _integer(adoption["stage"], stage)
        _require(adoption == {**expected, "source_sha": prepared.source_sha, "prerequisite_sha": prepared.prerequisite_sha,
                              "evidence_uuid": prepared.evidence_uuid, "evidence_digest": prepared.evidence_digest,
                              "stage": stage, "control_tool_sha": request.payload()["control_tool_sha"]}, "refresh adoption mismatch")


def _build_supersession_receipt(
        request: RefreshRequest, prepared: PreparedCandidate,
        request_uuid: str, authorization_uuid: str,
        child_identifier: str) -> dict[str, object]:
    payload = _request(request)
    _require(payload["schema_version"] == 2,
             "supersession receipt requires protocol v2")
    for value in (request_uuid, authorization_uuid):
        _uuid(value)
    _match(child_identifier, _ISSUE)
    return {
        "version": 2,
        "request_digest": request.digest,
        "request_uuid": request_uuid,
        "authorization_uuid": authorization_uuid,
        "gates": [{
            "id": gate["id"],
            "identifier": gate["identifier"],
            "role": gate["role"],
            "authority_digest": gate["authority_digest"],
            "cancelled_revision": gate["revision"] + 1,
        } for gate in payload["supersession"]["gates"]],
        "source_sha": prepared.source_sha,
        "target_sha": prepared.target_sha,
        "pr": payload["pr"],
        "prerequisite_sha": prepared.prerequisite_sha,
        "control_tool_sha": payload["control_tool_sha"],
        "child_id": prepared.child_id,
        "child_identifier": child_identifier,
        "evidence_uuid": prepared.evidence_uuid,
        "evidence_digest": prepared.evidence_digest,
        "refresh_stage": payload["refresh_stage"],
        "fresh_gate_stage": payload["fresh_gate_stage"],
    }


def _validate_superseded_gates(
        request: RefreshRequest, state: dict) -> tuple[dict[str, object], ...]:
    payload = _request(request)
    _require(payload["schema_version"] == 2,
             "superseded gates require protocol v2")
    records = tuple({
        "id": gate["id"],
        "identifier": gate["identifier"],
        "role": gate["role"],
        "authority_digest": gate["authority_digest"],
        "cancelled_revision": gate["revision"] + 1,
    } for gate in payload["supersession"]["gates"])
    for frozen, recorded in zip(payload["supersession"]["gates"],
                                records, strict=True):
        matches = [item for item in state["children"]
                   if item.get("detail", {}).get("id") == recorded["id"]]
        _require(len(matches) == 1, "superseded gate is missing or duplicate")
        gate = _object(matches[0], "detail metadata evidence comment_manifest")
        detail = _issue_detail(gate["detail"], "unknown superseded gate field")
        gate_runs = [run for run in state["runs"]
                     if run.get("issue_id") == recorded["id"]]
        _require(detail.get("identifier") == recorded["identifier"]
                 and detail.get("status") == "cancelled"
                 and detail.get("status_category") == "cancelled"
                 and detail.get("revision") == recorded["cancelled_revision"]
                 and gate["metadata"] == {}
                 and gate["evidence"] is None
                 and gate["comment_manifest"] == []
                 and not gate_runs,
                 "superseded gate cancellation changed")
        restored = {
            **detail,
            "status": frozen["status"],
            "status_category": "backlog",
            "revision": frozen["revision"],
        }
        authority = {
            "detail": {key: value for key, value in restored.items()
                       if key not in _VOLATILE_DETAIL_FIELDS},
            "metadata": gate["metadata"],
            "evidence": gate["evidence"],
            "comment_manifest": gate["comment_manifest"],
            "runs": gate_runs,
        }
        _require(hashlib.sha256(canonical_json(authority).encode("utf-8")).hexdigest()
                 == recorded["authority_digest"],
                 "superseded gate authority changed")
    return records


def _supersession_receipt(
        feature: dict, request: RefreshRequest, prepared: PreparedCandidate,
        state: dict) -> dict[str, object]:
    """Validate the permanent receipt and its still-visible cancelled gates."""
    refresh_children = [item for item in state["children"]
                        if item.get("detail", {}).get("id") == prepared.child_id]
    _require(len(refresh_children) == 1,
             "refresh receipt child is missing or duplicate")
    child_identifier = refresh_children[0]["detail"].get("identifier")
    expected = _build_supersession_receipt(
        request, prepared, feature["request_comment"],
        feature["authorization_comment"], child_identifier)
    receipt = _object(
        feature.get("supersession"),
        "version request_digest request_uuid authorization_uuid gates source_sha "
        "target_sha pr prerequisite_sha control_tool_sha child_id child_identifier "
        "evidence_uuid evidence_digest refresh_stage fresh_gate_stage",
    )
    _integer(receipt["version"], 2)
    _integer(receipt["refresh_stage"], 3)
    _integer(receipt["fresh_gate_stage"], 4)
    _require(type(receipt["gates"]) is list,
             "refresh supersession gates are malformed")
    for gate in receipt["gates"]:
        parsed_gate = _object(
            gate,
            "id identifier role authority_digest cancelled_revision")
        _integer(parsed_gate["cancelled_revision"], 2)
    _require(receipt == expected, "refresh supersession receipt mismatch")
    _require(tuple(receipt["gates"]) == _validate_superseded_gates(request, state),
             "refresh supersession gates changed")
    return receipt


def _plan_refresh(request: RefreshRequest, snapshot: RefreshSnapshot) -> RefreshDecision:
    payload, state = _request(request), _snapshot(snapshot)
    pre_feature = refresh_metadata(state["metadata"])
    allow_managed_merge = bool(
        payload["schema_version"] == 2
        and pre_feature is not None
        and "supersession" in pre_feature
        and "reservation" not in pre_feature
    )
    _shared_authority(
        payload, state, allow_managed_merge=allow_managed_merge)
    initial = None
    if (state["parent"].get("status") == "blocked"
            and not any(key in state["metadata"] for key in
                        (REFRESH_PREFIX + "reservation", REFRESH_PREFIX + "consumed",
                         REFRESH_PREFIX + "adoption",
                         REFRESH_PREFIX + "supersession"))):
        initial = validate_initial_refresh_progress(request, snapshot)
        if initial.metadata_writes < 4:
            return RefreshDecision("wait", None, "refresh request awaits explicit pause registration")
    feature = pre_feature
    key = refresh_action(request)
    if feature is None:
        _require(initial is not None and initial.metadata_writes == 0
                 and initial.comment_writes == 0, "invalid unstaged refresh request")
        return RefreshDecision("wait", None, "refresh request awaits explicit pause registration")
    _require(feature["request_digest"] == request.digest, "refresh request changed")
    metadata = state["metadata"]
    _require(metadata.get("eventra.workflow.version") == "2"
             and metadata.get("eventra.workflow.classification") == "frontend-only"
             and "eventra.workflow.backend_sha" not in metadata, "invalid refresh scope")
    if "authorization_comment" not in feature:
        _require(initial is not None and initial.metadata_writes in {4, 5}
                 and initial.comment_writes in {0, 1, 2}, "invalid paused refresh prefix")
        _require(not {"reservation", "consumed", "adoption", "supersession"}
                 & set(feature)
                 and metadata.get("eventra.workflow.attempt") == "0"
                 and metadata.get("eventra.workflow.next_stage") == "2"
                 and metadata.get("eventra.workflow.last_action") == payload["parent"]["last_action"]
                 and metadata.get("eventra.workflow.merge_state") == "not_ready"
                 and metadata.get("eventra.workflow.frontend_sha") == payload["source"]["sha"]
                 and state["pr"]["head_sha"] == payload["source"]["sha"]
                 and state["parent"]["status"] == "blocked", "invalid paused refresh intent")
        return RefreshDecision("wait", None, "refresh intent awaits exact member authorization")
    if initial is not None:
        _require(initial.metadata_writes == 6 and initial.comment_writes == 2
                 and initial.grant_comment is not None, "incomplete refresh admission prefix")
        admit_refresh(request, snapshot, initial.grant_comment)
        return RefreshDecision("create_refresh_stage", key, "exact refresh request admitted")
    _request_comment(request, state, feature)
    grant = [item for item in state["comments"] if item.get("comment_uuid") == feature["authorization_comment"]]
    _require(len(grant) == 1, "missing refresh grant")
    _grant_in_state(request, state, RefreshComment(**grant[0]))
    reservation = feature.get("reservation")
    if reservation is None:
        child, prepared = _refresh_child(request, state)
        _require(prepared is not None, "refresh has no completed preparation")
        if payload["schema_version"] == 2:
            _require(not {"adoption", "consumed"} & set(feature),
                     "v2 refresh has duplicate legacy receipts")
            _supersession_receipt(feature, request, prepared, state)
        else:
            _receipt_match(feature, request, prepared)
        _require(metadata.get("eventra.workflow.attempt") in {"0", "1", "2", "3"}, "refresh attempt mismatch")
        _require(state["parent"].get("status") == "in_progress"
                 and state["parent"].get("status_category") == "in_progress",
                 "adopted parent status changed")
        # Later exact gates/repairs are checked by the existing workflow planner.
        # The adoption stays tied to its request stage, never rewritten to a later repair SHA.
        base_ids = {payload["source"]["child_id"], prepared.child_id}
        if payload["schema_version"] == 2:
            base_ids.update(gate["id"] for gate in payload["supersession"]["gates"])
        fresh_gate_stage = _fresh_gate_stage(request)
        if len(state["children"]) > len(base_ids):
            later = [item for item in state["children"]
                     if item["detail"]["id"] not in base_ids]
            _require(len(later) == len(state["children"]) - len(base_ids)
                     and all(type(item["detail"]["stage"]) is int
                             and item["detail"]["stage"] >= fresh_gate_stage
                             for item in later),
                     "invalid post-refresh history")
            gates = [item for item in later
                     if item["detail"]["stage"] == fresh_gate_stage]
            _require(sorted(item["metadata"].get("eventra.phase.kind", "") for item in gates) == ["qa", "review"],
                     "missing fresh request-bound gates")
            for gate in gates:
                role = "independent_reviewer" if gate["metadata"]["eventra.phase.kind"] == "review" else "integration_qa"
                expected_action = (f"2:{payload['parent']['identifier']}:"
                                   "create_gate_stage:0:frontend:"
                                   f"{prepared.target_sha}:-:next-stage:"
                                   f"{fresh_gate_stage}")
                _require(gate["metadata"].get("eventra.phase.sha.frontend") == prepared.target_sha
                         and gate["metadata"].get("eventra.phase.attempt") == "0"
                         and gate["metadata"].get("eventra.phase.creation_action") == expected_action
                         and gate["metadata"].get("eventra.phase.target") == "repository:frontend"
                         and gate["metadata"].get("eventra.phase.role") == role
                         and gate["detail"]["parent_issue_id"] == payload["parent"]["id"]
                         and gate["detail"]["project_id"] == payload["assignment"]["project_id"]
                         and gate["detail"]["assignee_type"] == "agent"
                         and gate["detail"]["assignee_id"] == state["assignment"]["roles"][role], "fresh gate provenance mismatch")
            return RefreshDecision("wait", None, "refresh adopted; validate normal gate or repair history")
        _require(metadata.get("eventra.workflow.attempt") == "0"
                 and metadata.get("eventra.workflow.next_stage") == str(fresh_gate_stage)
                 and metadata.get("eventra.workflow.last_action") == key
                 and metadata.get("eventra.workflow.frontend_sha") == prepared.target_sha
                 and metadata.get("eventra.workflow.merge_state") == "not_ready"
                 and state["pr"]["head_sha"] == prepared.target_sha
                 and state["pr"].get("state") == "open"
                 and state["pr"].get("merged") is False,
                 "adopted candidate mismatch")
        active = [run for run in state["runs"] if run.get("status") in {
            "queued", "dispatched", "running", "waiting_local_directory"
        }]
        _require(len(active) <= 1 and all(
            run.get("issue_id") == payload["parent"]["id"]
            and run.get("agent_id") == payload["assignment"]["lead_id"]
            for run in active
        ), "adopted refresh has an unexpected active writer")
        return RefreshDecision(
            "create_gate_stage", None,
            f"adopted refresh requires fresh Stage {fresh_gate_stage} gates")
    if payload["schema_version"] == 2:
        reservation_state = reservation.get("state")
        if reservation_state in {
                "candidate_registered", "published", "adopted"}:
            reservation = _v2_publication_reservation(
                request, state, reservation)
            _validate_superseded_gates(request, state)
        else:
            reservation = _v2_reservation(request, state, reservation)
        if reservation_state in {"reserved", "review_cancelled"}:
            progress = validate_supersession_progress(request, snapshot, reservation)
            reason = ("resume ordered pristine-gate cancellation"
                      if progress.next_role is not None
                      else "pristine gates cancelled; refresh initialization pending")
            return RefreshDecision("resume_refresh", key, reason)
        if reservation_state in {
                "gates_cancelled", "child_initialized", "child_dispatched"}:
            validate_supersession_initialization_authority(
                request, snapshot, reservation)
        base_state = "gates_cancelled"
    else:
        reservation = _object(reservation, "version request_digest authorization_uuid action_key state child_id "
                              "child_identifier child_position prepared parent_status_category parent_position "
                              "parent_projection_digest")
        _integer(reservation["version"], 1)
        base_state = "reserved"
    _require(type(reservation["parent_status_category"]) is str
             and type(reservation["parent_position"]) is int
             and state["parent"].get("position") == reservation["parent_position"],
             "reservation parent layout mismatch")
    states = V2_RESERVATION_STATES if payload["schema_version"] == 2 else RESERVATION_STATES
    _require(reservation["request_digest"] == request.digest and reservation["authorization_uuid"] == feature["authorization_comment"]
             and reservation["action_key"] == key and reservation["state"] in states,
             "reservation identity mismatch")
    projected_state = state
    if (payload["schema_version"] == 2
            and reservation["state"] == "adopted"
            and "supersession" in feature):
        projected_state = _load_json(canonical_json(state), 4_194_304)
        del projected_state["metadata"][REFRESH_PREFIX + "supersession"]
        projected_state["parent"]["metadata"] = projected_state["metadata"]
        projected_state["parent"]["revision"] -= 1
    _require(reservation["parent_projection_digest"] ==
             parent_projection_digest(projected_state),
             "reservation parent projection drift")
    _require(metadata.get("eventra.workflow.attempt") == "0"
             and metadata.get("eventra.workflow.merge_state") == "not_ready"
             and not any(k in metadata for k in ("eventra.workflow.repair_reservation", "eventra.workflow.smoke_reservation")),
             "conflicting refresh reservation")
    active = [run for run in state["runs"] if run["status"] in {"queued", "dispatched", "running", "waiting_local_directory"}]
    parent_runs = [run for run in active if run["issue_id"] == payload["parent"]["id"]]
    _require(len(parent_runs) <= 1 and all(run["agent_id"] == payload["assignment"]["lead_id"] for run in parent_runs),
             "nonunique Lead writer")
    if reservation["state"] == base_state:
        _reserved_refresh_child(request, state)
        _require(reservation["child_id"] is None
                 and reservation["child_identifier"] is None and reservation["child_position"] is None
                 and reservation["prepared"] is None
                 and not {"adoption", "consumed", "supersession"} & set(feature)
                 and metadata.get("eventra.workflow.next_stage") == str(payload["parent"]["next_stage"])
                 and metadata.get("eventra.workflow.last_action") == payload["parent"]["last_action"]
                 and metadata.get("eventra.workflow.frontend_sha") == payload["source"]["sha"]
                 and state["pr"]["head_sha"] == payload["source"]["sha"] and active == parent_runs,
                 "reserved initialization requires exact executor recovery")
        return RefreshDecision("resume_refresh", key, "resume request-bound refresh initialization")
    child, prepared = _refresh_child(request, state)
    base_children = 3 if payload["schema_version"] == 2 else 1
    _require(len(state["children"]) == base_children + 1
             and reservation["child_id"] == child["detail"]["id"]
             and reservation["child_identifier"] == child["detail"]["identifier"]
             and reservation["child_position"] == child["detail"]["position"]
             and metadata.get("eventra.workflow.next_stage") == str(_fresh_gate_stage(request))
             and metadata.get("eventra.workflow.last_action") == key,
             "reservation child mismatch")
    child_runs = [run for run in active if run not in parent_runs]
    _require(len(child_runs) <= 1 and all(run["issue_id"] == child["detail"]["id"]
             and run["agent_id"] == payload["assignment"]["engineer_id"] for run in child_runs), "unexpected active child")
    if reservation["state"] in {"child_initialized", "child_dispatched"}:
        _require(reservation["prepared"] is None
                 and not {"adoption", "consumed", "supersession"} & set(feature)
                 and state["pr"]["head_sha"] == payload["source"]["sha"]
                 and metadata.get("eventra.workflow.frontend_sha") == payload["source"]["sha"], "unregistered head drift")
        if prepared is not None:
            _require(not child_runs, "preparation still has an active writer")
        return RefreshDecision("resume_refresh" if prepared or reservation["state"] == "child_initialized" else "wait",
                               key, "resume preparation registration" if prepared else "refresh preparation pending")
    _require(prepared is not None and not child_runs
             and reservation["prepared"] ==
                 _prepared_checkpoint(request, prepared),
             "registered preparation mismatch")
    _require(state["pr"]["head_sha"] in {prepared.source_sha, prepared.target_sha}, "unregistered head drift")
    if reservation["state"] == "candidate_registered":
        _require(not {"adoption", "consumed", "supersession"} & set(feature)
                 and metadata.get("eventra.workflow.frontend_sha") == prepared.source_sha, "premature adoption")
        return RefreshDecision("publish_refresh", key, "publish or reconcile registered candidate")
    _require(state["pr"]["head_sha"] == prepared.target_sha, "published head mismatch")
    _require(metadata.get("eventra.workflow.frontend_sha") in {prepared.source_sha, prepared.target_sha}, "adoption drift")
    if payload["schema_version"] == 2:
        _require(not {"adoption", "consumed"} & set(feature),
                 "v2 refresh has duplicate legacy receipts")
        if reservation["state"] == "adopted":
            if "supersession" in feature:
                _supersession_receipt(feature, request, prepared, state)
            _require(metadata["eventra.workflow.frontend_sha"] == prepared.target_sha,
                     "adoption is incomplete")
        else:
            _require("supersession" not in feature,
                     "supersession receipt precedes adoption")
    else:
        _receipt_match(feature, request, prepared, partial=True)
        _require((metadata["eventra.workflow.frontend_sha"] != prepared.target_sha or "adoption" in feature)
                 and ("consumed" not in feature or metadata["eventra.workflow.frontend_sha"] == prepared.target_sha),
                 "illegal partial adoption order")
        if reservation["state"] == "adopted":
            _receipt_match(feature, request, prepared)
            _require(metadata["eventra.workflow.frontend_sha"] == prepared.target_sha,
                     "adoption is incomplete")
    return RefreshDecision("resume_refresh", key, "resume adoption; gates wait for reservation clearance")


def plan_refresh(request: RefreshRequest, snapshot: RefreshSnapshot) -> RefreshDecision:
    """A pure, fail-closed recommendation; no decision authorizes an I/O side effect."""
    try:
        return _plan_refresh(request, snapshot)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return RefreshDecision("block", None, "refresh authority, provenance or state is inconsistent")
