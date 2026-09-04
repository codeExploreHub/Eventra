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
from pathlib import PurePosixPath
from typing import Any

from .knowledge_contracts import ContextReceipt


MAX_REQUEST_BYTES = 16_384
MAX_COMMENT_BYTES = 65_536
STAGING_PREFIX = "refs/heads/eventra-refresh/"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ISSUE = re.compile(r"PRO-[1-9][0-9]*\Z")
_PR = re.compile(r"https://github\.com/codeExploreHub/Eventra/pull/[1-9][0-9]*\Z")


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


def _branch(value: object) -> None:
    _require(type(value) is str and len(value) <= 255)
    _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value) is not None)
    _require(not value.startswith("refs/") and ".." not in value)
    _require(all(part and not part.startswith(".") and not part.endswith((".", ".lock"))
                 for part in value.split("/")), "invalid refresh branch")


def build_request(payload: dict[str, object]) -> RefreshRequest:
    """Validate the entire immutable request before deriving its ref name."""
    value = _object(payload, "schema_version workspace_id parent source pr prerequisite assignment "
                    "refresh_stage refresh_generation staging_ref_prefix tree_transform "
                    "merge_permission control_tool_sha git_version")
    encoded = _size(canonical_json(value), MAX_REQUEST_BYTES)
    _integer(value["schema_version"], 1)
    _integer(value["refresh_stage"], 2)
    _integer(value["refresh_generation"], 1)
    _uuid(value["workspace_id"])
    _match(value["control_tool_sha"], _SHA)
    _require(value["staging_ref_prefix"] == STAGING_PREFIX)
    _require(value["tree_transform"] == "clean-two-parent-merge-v1")
    _require(value["merge_permission"] == "hold")
    _require(type(value["git_version"]) is str and
             re.fullmatch(r"git version [0-9][A-Za-z0-9. ()_-]{0,127}", value["git_version"]) is not None)
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
    assignment = _object(value["assignment"], "project_id squad_id lead_id engineer_id")
    for identity in assignment.values():
        _uuid(identity)
    _require(assignment["lead_id"] != assignment["engineer_id"])
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return RefreshRequest(encoded, digest, STAGING_PREFIX + digest)


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


def _comment(comment: RefreshComment, issue: str, author_type: str) -> None:
    _require(type(comment) is RefreshComment)
    for value in (comment.issue_id, comment.comment_uuid, comment.author_id):
        _uuid(value)
    _integer(comment.revision, 1)
    _require(comment.issue_id == issue and comment.author_type == author_type,
             "refresh comment identity mismatch")
    _size(comment.content, MAX_COMMENT_BYTES)


def _block(text: str, kind: str, *, only: bool = False) -> Any:
    # Reject duplicate, nested and unrelated fences rather than selecting a convenient block.
    _require(text.count("```") == 2, "ambiguous refresh evidence block")
    label = "eventra-candidate-refresh-" + kind + "-v1"
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
    value = _object(_block(comment.content, "grant", only=True),
                    "schema_version request_digest granted_refresh")
    _integer(value["schema_version"], 1)
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


def _commands(raw: object) -> None:
    commands = _object(raw, " ".join((*_COMMANDS, "knowledge")))
    for name, result in commands.items():
        result = _object(result, "argv exit_code")
        _integer(result["exit_code"], 0)
        argv = result["argv"]
        _require(type(argv) is list and all(type(arg) is str for arg in argv))
        if name == "knowledge":
            prefix = ["python3", "-B", "-m", "tools.multica.knowledge", "verify"]
            _require(len(argv) == 9 and argv[:5] == prefix and
                     argv[5] == "--frontend-root" and argv[7] == "--backend-root")
            for root in (argv[6], argv[8]):
                _require(root.startswith("/") and root != "/" and ".." not in PurePosixPath(root).parts
                         and not any(ord(char) < 32 for char in root))
            _require(argv[6] != argv[8])
        else:
            allowed = [_COMMANDS[name]]
            if name == "lint":
                allowed.append(_COMMANDS[name] + ["--", "--ignore-pattern", ".worktrees/**"])
            if name == "python":
                allowed.append(_COMMANDS[name] + ["-v"])
            _require(argv in allowed, "required refresh check substituted")


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
    value = _object(_block(comment.content, "prepared"),
                    "schema_version request_digest child_id source_sha prerequisite_sha target_sha "
                    "tree_sha staging_ref control_tool_sha git_version context_receipt commands")
    _integer(value["schema_version"], 1)
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


@dataclass(frozen=True)
class RefreshSnapshot:
    """Immutable authority projection produced by RefreshAPI, never by a request."""

    canonical_state: str

    def state(self) -> dict[str, Any]:
        return _object(_load_json(self.canonical_state, 4_194_304),
                       "parent metadata children runs comments pr prerequisite assignment tool")


def _snapshot(snapshot: RefreshSnapshot) -> dict[str, Any]:
    _require(type(snapshot) is RefreshSnapshot, "missing refresh authority")
    state = snapshot.state()
    _require(canonical_json(state) == snapshot.canonical_state, "noncanonical refresh authority")
    for key in ("parent", "metadata", "pr", "prerequisite", "assignment", "tool"):
        _require(type(state[key]) is dict, "invalid refresh authority")
    for key in ("children", "runs", "comments"):
        _require(type(state[key]) is list, "invalid refresh authority")
    _require(all(type(k) is str and type(v) is str for k, v in state["metadata"].items()))
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
    child = _object(matches[0], "detail metadata evidence")
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


def _shared_authority(payload: dict, state: dict) -> None:
    parent, assignment = state["parent"], state["assignment"]
    expected = payload["assignment"]
    _require(assignment.get("workspace_id") == payload["workspace_id"]
             and all(assignment.get(k) == v for k, v in expected.items()), "assignment authority mismatch")
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
             and pr.get("state") == "open" and pr.get("merged") is False, "managed PR identity mismatch")
    prerequisite = state["prerequisite"]
    _require(all(prerequisite.get(k) == v for k, v in payload["prerequisite"].items())
             and prerequisite.get("merged") is True
             and prerequisite.get("ancestor_sha") == payload["prerequisite"]["merge_sha"],
             "prerequisite or base identity mismatch")
    _source_authority(payload, state)


def admit_refresh(request: RefreshRequest, snapshot: RefreshSnapshot, grant: RefreshComment) -> None:
    """First entry only. Resumption must validate an exact durable projection separately."""
    payload, state = _request(request), _snapshot(snapshot)
    _shared_authority(payload, state)
    _grant_in_state(request, state, grant)
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
    _require(len(state["children"]) == 1, "refresh requires a unique Stage 1 and no later children")
    _require(state["pr"].get("head_sha") == payload["source"]["sha"], "managed head drift")
    active = [run for run in state["runs"] if run.get("status") in
              {"queued", "dispatched", "running", "waiting_local_directory"}]
    _require(len(active) <= 1 and all(run.get("issue_id") == parent["id"]
             and run.get("agent_id") == payload["assignment"]["lead_id"] for run in active),
             "active child or nonunique Lead writer")
