"""Guarded refresh authority adapter.

Scope is supplied by trusted operator configuration, never by a request payload.
The CLI remains disabled unless it validates an explicit approved deployment
record; constructing RefreshScope is not itself human deployment approval.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

from . import candidate_refresh as c
from .blueprint import build_multi_repo_blueprint
from .contracts import parse_agent_list, parse_project_list, parse_squad_list, parse_squad_detail, parse_squad_members
from .issue_contracts import parse_issue_detail, parse_issue_children, parse_issue_metadata, parse_issue_runs


_PROFILE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_-]|\.(?=[A-Za-z0-9])){0,63}\Z"
)


@dataclass(frozen=True)
class RefreshScope:
    profile: str
    workspace_id: str
    approved_control_sha: str


@dataclass(frozen=True)
class RefreshExecutionResult:
    action_key: str
    status: str
    mutation_count: int
    child_identifier: str


def _need(condition, reason):
    if not condition:
        raise RuntimeError("refresh authority: " + reason)


def valid_profile_name(value: object) -> bool:
    return type(value) is str and _PROFILE.fullmatch(value) is not None


class RefreshAPI:
    def __init__(self, runner, github, control_root, *, scope=None, prerequisite_pr=None):
        self.runner, self.github = runner, github
        self.control_root = Path(control_root).resolve()
        self.scope, self.prerequisite_pr = scope, prerequisite_pr

    def _scope(self):
        _need(type(self.scope) is RefreshScope, "explicit approved scope required")
        _need(valid_profile_name(self.scope.profile),
              "invalid profile")
        c._uuid(self.scope.workspace_id)
        c._match(self.scope.approved_control_sha, c._SHA)

    def _read(self, args):
        self._scope()
        return self.runner.run([*args, "--output", "json", "--profile", self.scope.profile,
                                "--workspace-id", self.scope.workspace_id])

    @contextmanager
    def parent_lock(self, parent):
        """Non-blocking process lock scoped to the configured workspace and parent."""
        self._scope()
        c._match(parent, c._ISSUE)
        result = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
             "rev-parse", "--git-common-dir"],
            cwd=self.control_root, text=True, capture_output=True, timeout=15,
        )
        _need(result.returncode == 0, "control common directory unavailable")
        common = Path(result.stdout.strip())
        if not common.is_absolute():
            common = (self.control_root / common).resolve()
        _need(common.is_dir(), "control common directory unavailable")
        lock_dir = common / "eventra-refresh-locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = hashlib.sha256((self.scope.workspace_id + "\0" + parent).encode()).hexdigest() + ".lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_dir / name, flags, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("refresh authority: parent executor lock is busy") from None
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def set_metadata(self, issue, key, value):
        c._match(issue, c._ISSUE)
        _need(type(key) is str and key.startswith((c.REFRESH_PREFIX, "eventra.workflow.", "eventra.phase.")),
              "invalid refresh metadata key")
        c._size(value, c.MAX_COMMENT_BYTES)
        return self._read(["issue", "metadata", "set", issue, "--key", key,
                           "--value", value, "--type", "string"])

    def delete_metadata(self, issue, key):
        c._match(issue, c._ISSUE)
        _need(key == c.REFRESH_PREFIX + "reservation",
              "invalid refresh metadata deletion")
        return self._read(["issue", "metadata", "delete", issue, "--key", key])

    def create_child(self, *, parent, stage, title, project_id, assignee_id, description):
        c._match(parent, c._ISSUE)
        _need(stage == 2, "invalid refresh stage")
        for value in (project_id, assignee_id):
            c._uuid(value)
        c._size(title, 512)
        c._size(description, c.MAX_COMMENT_BYTES)
        raw = self._read([
            "issue", "create", "--parent", parent, "--stage", str(stage),
            "--project", project_id, "--assignee-id", assignee_id,
            "--status", "backlog", "--title", title, "--description", description,
        ])
        _need(type(raw) is dict, "invalid child creation response")
        identifier = raw.get("identifier")
        c._match(identifier, c._ISSUE)
        return identifier

    def set_status(self, issue, status, *, start, position=None):
        c._match(issue, c._ISSUE)
        _need(status in {"todo", "in_progress", "done"} and type(start) is bool,
              "invalid refresh status transition")
        _need(position is None or (type(position) is int and not start),
              "invalid refresh position preservation")
        if position is not None:
            args = ["issue", "update", issue, "--status", status,
                    "--position", str(position), "--no-start"]
        else:
            args = ["issue", "status", issue, status]
            if not start:
                args.append("--no-start")
        return self._read(args)

    def _tool(self):
        self._scope()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        def git(*args):
            try:
                result = subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false", *args],
                                        cwd=self.control_root, env=env, text=True, capture_output=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                raise RuntimeError("refresh authority: control checkout unavailable") from None
            _need(result.returncode == 0, "control checkout unavailable")
            return result.stdout.strip()
        sha = git("rev-parse", "HEAD")
        _need(sha == self.scope.approved_control_sha, "checkout is not the configured approved control SHA")
        _need(not git("status", "--porcelain", "--untracked-files=no"), "dirty control checkout")
        return {"sha": sha, "git_version": git("version")}

    def _detail(self, issue):
        c._match(issue, c._ISSUE)
        raw = self._read(["issue", "get", issue])
        parsed = parse_issue_detail(raw, issue)
        for key in ("id", "project_id", "assignee_id", "workspace_id"):
            c._uuid(raw.get(key))
        _need(raw["workspace_id"] == self.scope.workspace_id, "cross-workspace issue")
        _need(type(raw.get("revision")) is int and raw["revision"] >= 1, "missing issue revision")
        return {**raw, **parsed}

    @staticmethod
    def _comments(records, issue_id):
        c._uuid(issue_id)
        try:
            manifest = c.comment_manifest(records, issue_id)
        except ValueError as exc:
            raise RuntimeError("refresh authority: " + str(exc)) from None
        _need(type(records) is list, "incomplete comment pagination")
        manifest_ids = {item["comment_uuid"] for item in manifest}
        result, seen = [], set()
        required = {"id", "author_id", "author_type", "content", "revision", "type", "created_at"}
        auxiliary = {"issue_id", "parent_id", "updated_at", "reply_count", "last_activity_at"}
        for record in records:
            if record["type"] != "comment":
                continue
            _need(type(record) is dict and "revision" in record, "missing comment revision")
            _need(required <= set(record) <= required | auxiliary, "unknown or folded comment fields")
            _need(record.get("issue_id", issue_id) == issue_id, "cross-scope comment")
            _need(record["id"] not in seen, "duplicate comment")
            for key in ("id", "author_id"):
                c._uuid(record[key])
            _need(record["type"] == "comment" and record["author_type"] in {"member", "agent"}, "comment identity")
            _need(type(record["revision"]) is int and record["revision"] >= 1, "comment revision")
            c._size(record["content"], c.MAX_COMMENT_BYTES)
            if record.get("parent_id") is not None:
                _need(record["parent_id"] in manifest_ids, "incomplete comment thread")
            if "reply_count" in record:
                _need(type(record["reply_count"]) is int and record["reply_count"] >= 0, "reply count")
            seen.add(record["id"])
            result.append(c.RefreshComment(issue_id, record["id"], record["author_id"], record["author_type"],
                                           record["revision"], record["content"]))
        # A declared reply count cannot be satisfied by a truncated root-only read.
        for record in records:
            if "reply_count" in record:
                descendants = {record["id"]}
                for child in records:
                    if child.get("parent_id") in descendants:
                        descendants.add(child["id"])
                _need(record["reply_count"] == len(descendants) - 1, "incomplete replies")
        return result

    def parse_scoped_comment(self, records, comment_uuid, scoped_issue_id):
        c._uuid(comment_uuid)
        comments = self._comments(records, scoped_issue_id)
        matches = [comment for comment in comments if comment.comment_uuid == comment_uuid]
        _need(len(matches) == 1, "missing scoped comment")
        return matches[0]

    def _all_comments(self, identifier, issue_id):
        # CLI default + --full returns all complete threads. Do not use --recent,
        # --tail, --summary or --roots-only; their cursors live on stderr.
        return self._all_comment_state(identifier, issue_id)[1]

    def _all_comment_state(self, identifier, issue_id):
        records = self._read(["issue", "comment", "list", identifier, "--full", "--compact"])
        return records, self._comments(records, issue_id)

    def comment(self, issue, comment_uuid):
        before = self._detail(issue)
        first = self._all_comments(issue, before["id"])
        after = self._detail(issue)
        second = self._all_comments(issue, after["id"])
        _need(before == after and first == second, "comment authority changed during read")
        matches = [comment for comment in first if comment.comment_uuid == comment_uuid]
        _need(len(matches) == 1, "missing scoped comment")
        return matches[0]

    def parent_for_child(self, child):
        detail = self._detail(child)
        parent_id = detail.get("parent_issue_id")
        _need(type(parent_id) is str, "refresh completion requires a child issue")
        c._uuid(parent_id)
        raw = self._read(["issue", "get", parent_id])
        _need(type(raw) is dict, "invalid parent lookup")
        parent = raw.get("identifier")
        c._match(parent, c._ISSUE)
        verified = self._detail(parent)
        _need(verified["id"] == parent_id and verified["parent_issue_id"] is None,
              "child parent lookup changed")
        return parent

    def _assignment(self):
        blueprint = build_multi_repo_blueprint("Eventra")
        agents = parse_agent_list(self._read(["agent", "list"]))
        projects = parse_project_list(self._read(["project", "list"]))
        squads = parse_squad_list(self._read(["squad", "list"]))
        def unique(records, key, name):
            matches = [item["id"] for item in records if item[key] == name]
            _need(len(matches) == 1, "ambiguous assignment mapping")
            c._uuid(matches[0])
            return matches[0]
        roles = {agent.role: unique(agents, "name", agent.name) for agent in blueprint.agents}
        project_ids = {repository: unique(projects, "title", title) for repository, title in
                       {"frontend": "Eventra Local Development", "backend": "Eventra Backend Local Development"}.items()}
        squad_id = unique(squads, "name", blueprint.squad_name)
        squad = parse_squad_detail(self._read(["squad", "get", squad_id]), squad_id)
        members = parse_squad_members(self._read(["squad", "member", "list", squad_id]), squad_id)
        expected = [{"member_id": agent_id, "member_type": "agent", "role": "leader" if role == blueprint.leader_role else role}
                    for role, agent_id in roles.items()]
        order = lambda item: (item["role"], item["member_id"])
        _need(squad["name"] == blueprint.squad_name and squad["leader_id"] == roles[blueprint.leader_role]
              and sorted(members, key=order) == sorted(expected, key=order), "squad assignment conflict")
        _need(len(set(roles.values())) == len(roles) and len(set(project_ids.values())) == 2, "aliased assignments")
        return {"workspace_id": self.scope.workspace_id, "project_id": project_ids["frontend"],
                "squad_id": squad_id, "lead_id": roles[blueprint.leader_role], "engineer_id": roles["frontend_engineer"],
                "roles": roles, "projects": project_ids, "members": sorted(members, key=order)}

    def _runs(self, identifier, issue_id):
        raw = self._read(["issue", "runs", identifier])
        parsed = parse_issue_runs(raw, issue_id)
        for original, normalized in zip(raw, parsed, strict=True):
            c._uuid(original.get("agent_id"))
            _need(original.get("workspace_id") == self.scope.workspace_id, "run workspace mismatch")
            normalized["agent_id"] = original["agent_id"]
        return parsed

    def _pr(self, url):
        c._match(url, c._PR)
        raw = self.github.run(["api", "--method", "GET", "repos/codeExploreHub/Eventra/pulls/" + url.rsplit("/", 1)[1]])
        _need(type(raw) is dict and raw.get("html_url") == url, "PR URL mismatch")
        for side in ("head", "base"):
            record = raw.get(side)
            _need(type(record) is dict and type(record.get("repo")) is dict
                  and record["repo"].get("full_name") == "codeExploreHub/Eventra", "PR repository mismatch")
            c._match(record.get("sha"), c._SHA)
            c._branch(record.get("ref"))
        _need(type(raw.get("merged")) is bool and raw.get("state") in {"open", "closed"}, "PR state")
        return raw

    def _read_once(self, parent_key):
        tool = self._tool()  # Fail before network when control scope/checkout is not approved.
        parent = self._detail(parent_key)
        _need(parent["parent_issue_id"] is None, "not a parent issue")
        metadata = parse_issue_metadata(self._read(["issue", "metadata", "list", parent_key]))
        _need(parent.get("metadata") == metadata, "parent metadata echo conflict")
        assignment = self._assignment()
        listed = parse_issue_children(self._read(["issue", "children", parent_key]), parent["id"])
        children, runs = [], self._runs(parent_key, parent["id"])
        for item in listed:
            _need(item["project_id"] == assignment["project_id"], "non-frontend child")
            detail = self._detail(item["identifier"])
            _need(all(detail[k] == v for k, v in item.items()), "child detail differs from parent relation")
            child_metadata = parse_issue_metadata(self._read(["issue", "metadata", "list", item["identifier"]]))
            _need(detail.get("metadata") == child_metadata, "child metadata echo conflict")
            comment_records, comments = self._all_comment_state(
                item["identifier"], item["id"])
            evidence_uuid = child_metadata.get("eventra.phase.evidence_comment")
            evidence = [asdict(comment) for comment in comments if comment.comment_uuid == evidence_uuid]
            _need(evidence_uuid is None or len(evidence) == 1, "missing child evidence")
            children.append({
                "detail": detail, "metadata": child_metadata,
                "evidence": evidence[0] if evidence else None,
                "comment_manifest": c.comment_manifest(comment_records, item["id"]),
            })
            runs.extend(self._runs(item["identifier"], item["id"]))
        observed_issue_ids = {parent["id"], *(child["detail"]["id"] for child in children)}
        _need(all(run["issue_id"] in observed_issue_ids for run in runs),
              "run is not bound to an observed issue")
        _need(len({run["id"] for run in runs}) == len(runs),
              "duplicate run identity across observed issues")
        source = [child for child in children if child["detail"]["stage"] == 1]
        _need(len(source) == 1, "source Stage 1 membership")
        url = source[0]["metadata"].get("eventra.phase.pr")
        pr = self._pr(url)
        prerequisite_url = self.prerequisite_pr
        if prerequisite_url is None and "eventra.refresh.request" in metadata:
            prerequisite_url = c.parse_request(metadata["eventra.refresh.request"]).payload()["prerequisite"]["pr_url"]
        prerequisite = self._pr(prerequisite_url)
        merge = prerequisite.get("merge_commit_sha")
        c._match(merge, c._SHA)
        base_ref = pr["base"]["ref"]
        base = self.github.run(["api", "--method", "GET", "repos/codeExploreHub/Eventra/git/ref/heads/" + quote(base_ref, safe="/")])
        _need(type(base) is dict and base.get("ref") == "refs/heads/" + base_ref
              and type(base.get("object")) is dict and base["object"].get("type") == "commit", "invalid base ref")
        base_sha = base["object"].get("sha")
        c._match(base_sha, c._SHA)
        _need(prerequisite["base"]["ref"] == base_ref, "base drift")
        compare = self.github.run(["api", "--method", "GET", f"repos/codeExploreHub/Eventra/compare/{merge}...{base_sha}"])
        _need(type(compare) is dict and compare.get("status") in {"ahead", "identical"}
              and type(compare.get("merge_base_commit")) is dict
              and compare["merge_base_commit"].get("sha") == merge, "prerequisite is not a base ancestor")
        parent_records, parent_comments = self._all_comment_state(parent_key, parent["id"])
        return {"parent": parent, "metadata": metadata, "children": children, "runs": runs,
                "comments": [asdict(comment) for comment in parent_comments],
                "comment_manifest": c.comment_manifest(parent_records, parent["id"]),
                "pr": {"url": url, "repository": "codeExploreHub/Eventra", "head_ref": pr["head"]["ref"],
                       "base_ref": base_ref, "head_sha": pr["head"]["sha"], "state": pr["state"], "merged": pr["merged"]},
                "prerequisite": {"pr_url": prerequisite_url, "merge_sha": merge, "base_sha": base_sha,
                                 "merged": prerequisite["merged"] and prerequisite["state"] == "closed", "ancestor_sha": merge},
                "assignment": assignment, "tool": tool}

    def snapshot(self, parent):
        before, after = self._read_once(parent), self._read_once(parent)
        _need(before == after, "authority changed during double read; refreeze required")
        return c.RefreshSnapshot(c.canonical_json(before))


def load_refresh_snapshot(runner, github, parent, *, control_root=None, scope=None, prerequisite_pr=None):
    """No ambient profile or request-supplied deployment approval fallback."""
    _need(control_root is not None, "explicit control checkout required")
    return RefreshAPI(runner, github, control_root, scope=scope, prerequisite_pr=prerequisite_pr).snapshot(parent)


def stage_refresh_request(api, parent: str, request: c.RefreshRequest) -> RefreshExecutionResult:
    """Persist only the four-key pause prefix, reconciling every observed effect."""
    payload = c._request(request)
    _need(parent == payload["parent"]["identifier"], "request parent mismatch")
    envelope = c.canonical_json({"payload": payload, "digest": request.digest,
                                 "staging_ref": request.staging_ref})
    prefix = [
        (c.REFRESH_PREFIX + "request", envelope),
        (c.REFRESH_PREFIX + "version", "1"),
        (c.REFRESH_PREFIX + "merge_permission", "hold"),
        (c.REFRESH_PREFIX + "request_digest", request.digest),
    ]
    mutations = 0
    with api.parent_lock(parent):
        while True:
            before = c.validate_initial_refresh_progress(request, api.snapshot(parent))
            _need(before.metadata_writes <= len(prefix), "request staging already passed pause prefix")
            _need(before.metadata_writes == len(prefix) or before.comment_writes == 0,
                  "refresh comments precede complete pause metadata")
            if before.metadata_writes == len(prefix):
                return RefreshExecutionResult(c.refresh_action(request), "request_staged", mutations, "")
            key, value = prefix[before.metadata_writes]
            failure = None
            try:
                api.set_metadata(parent, key, value)
            except RuntimeError as exc:
                failure = exc
            after = c.validate_initial_refresh_progress(request, api.snapshot(parent))
            if (after.metadata_writes, after.comment_writes) == (
                    before.metadata_writes + 1, before.comment_writes):
                mutations += 1
                continue
            if (after.metadata_writes, after.comment_writes) == (
                    before.metadata_writes, before.comment_writes) and failure is not None:
                raise failure
            raise RuntimeError("refresh authority: metadata effect was not uniquely observed")


_ACTIVE_RUN_STATUSES = {"queued", "dispatched", "running", "waiting_local_directory"}


def _refresh_child_prefix(request: c.RefreshRequest) -> list[tuple[str, str]]:
    payload = c._request(request)
    return [
        ("eventra.workflow.version", "2"),
        ("eventra.phase.kind", "refresh"),
        ("eventra.phase.attempt", "0"),
        ("eventra.phase.target", "repository:frontend"),
        ("eventra.phase.role", "frontend_engineer"),
        ("eventra.phase.creation_action", c.refresh_action(request)),
        ("eventra.phase.pr", payload["pr"]["url"]),
        (c.REFRESH_PREFIX + "version", "1"),
        (c.REFRESH_PREFIX + "request_digest", request.digest),
        (c.REFRESH_PREFIX + "source_sha", payload["source"]["sha"]),
        ("eventra.phase.sha.frontend", payload["source"]["sha"]),
    ]


def _exact_prefix(actual: dict[str, str], expected: list[tuple[str, str]]) -> int:
    _need(type(actual) is dict and all(type(key) is str and type(value) is str
                                      for key, value in actual.items()),
          "invalid refresh child metadata")
    _need(actual == dict(expected[:len(actual)]),
          "refresh child metadata is not the exact write prefix")
    return len(actual)


def _initialization_progress(request: c.RefreshRequest, state: dict, reservation: dict) -> dict:
    """Classify only the fixed write prefix following one durable checkpoint."""
    payload = c._request(request)
    action_key = c.refresh_action(request)
    reservation_fields = {
        "version", "request_digest", "authorization_uuid", "action_key", "state",
        "child_id", "child_identifier", "prepared", "parent_status_category",
        "parent_position", "child_position", "parent_projection_digest",
    }
    _need(type(reservation) is dict and set(reservation) == reservation_fields,
          "invalid initialization checkpoint shape")
    feature = c.refresh_metadata(state["metadata"])
    _need(feature is not None and feature.get("request_comment") is not None
          and feature.get("authorization_comment") == reservation["authorization_uuid"]
          and feature.get("reservation") == reservation,
          "refresh checkpoint identity changed")
    _need(reservation["version"] == 1 and reservation["request_digest"] == request.digest
          and reservation["action_key"] == action_key and reservation["prepared"] is None
          and type(reservation.get("parent_status_category")) is str
          and type(reservation.get("parent_position")) is int
          and state["parent"].get("position") == reservation["parent_position"]
          and reservation["state"] in {"reserved", "child_initialized", "child_dispatched"},
          "invalid initialization checkpoint")
    c._shared_authority(payload, state)
    c._request_comment(request, state, feature)
    grants = [item for item in state["comments"]
              if item.get("comment_uuid") == reservation["authorization_uuid"]]
    _need(len(grants) == 1, "missing refresh grant")
    c._grant_in_state(request, state, c.RefreshComment(**grants[0]))

    title = f"{payload['parent']['identifier']}: prepare candidate refresh"
    description = c.canonical_json({"schema_version": 1, "request_digest": request.digest,
                                    "action_key": action_key})
    later = [item for item in state["children"]
             if item["detail"]["id"] != payload["source"]["child_id"]]
    _need(len(later) <= 1, "duplicate refresh child")
    child = later[0] if later else None
    child_prefix = 0
    if child is not None:
        detail = child["detail"]
        expected = {
            "parent_issue_id": payload["parent"]["id"], "workspace_id": payload["workspace_id"],
            "stage": 2, "project_id": payload["assignment"]["project_id"],
            "assignee_type": "agent", "assignee_id": payload["assignment"]["engineer_id"],
            "title": title, "description": description,
        }
        _need(all(detail.get(key) == value for key, value in expected.items())
              and child["evidence"] is None, "existing refresh child conflicts")
        child_prefix = _exact_prefix(child["metadata"], _refresh_child_prefix(request))
        _need(detail.get("revision") == 1 + child_prefix + int(detail.get("status") != "backlog"),
              "refresh child revision is not explained by exact writes")
        _need(detail.get("status") in {"backlog", "todo", "in_progress", "in_review"},
              "refresh child status conflicts")

    metadata = state["metadata"]
    parent = state["parent"]
    base_next = str(payload["parent"]["next_stage"])
    base_action = payload["parent"]["last_action"]
    parent_cases = [
        (base_next, base_action, payload["parent"]["status"]),
        ("3", base_action, payload["parent"]["status"]),
        ("3", action_key, payload["parent"]["status"]),
        ("3", action_key, "in_progress"),
    ]
    observed_parent = (metadata.get("eventra.workflow.next_stage"),
                       metadata.get("eventra.workflow.last_action"), parent.get("status"))
    _need(observed_parent in parent_cases, "parent initialization is not an exact write prefix")
    parent_prefix = parent_cases.index(observed_parent)
    _need(parent.get("status_category") == (
        reservation["parent_status_category"] if parent_prefix < 3 else "in_progress"),
        "parent status category does not match the verified transition")

    if reservation["state"] == "reserved":
        _need(reservation["child_id"] is None and reservation["child_identifier"] is None,
              "reserved child binding is premature")
        _need(reservation["child_position"] is None,
              "reserved child position is premature")
        restored = c._load_json(c.canonical_json(state), 4_194_304)
        restored["metadata"]["eventra.workflow.next_stage"] = base_next
        restored["metadata"]["eventra.workflow.last_action"] = base_action
        restored["parent"]["metadata"] = restored["metadata"]
        restored["parent"]["status"] = payload["parent"]["status"]
        restored["parent"]["status_category"] = reservation["parent_status_category"]
        restored["parent"]["revision"] -= parent_prefix
        _need(c.parent_projection_digest(restored) == reservation["parent_projection_digest"],
              "reserved parent projection cannot explain initialization prefix")
    else:
        _need(child is not None and reservation["child_id"] == child["detail"]["id"]
              and reservation["child_identifier"] == child["detail"]["identifier"]
              and reservation["child_position"] == child["detail"]["position"]
              and child_prefix == len(_refresh_child_prefix(request)) and parent_prefix == 3
              and reservation["parent_projection_digest"] == c.parent_projection_digest(state),
              "initialized checkpoint projection mismatch")

    active = [run for run in state["runs"] if run["status"] in _ACTIVE_RUN_STATUSES]
    parent_runs = [run for run in active if run["issue_id"] == payload["parent"]["id"]]
    child_runs = [] if child is None else [run for run in state["runs"]
                                           if run["issue_id"] == child["detail"]["id"]]
    active_child_runs = [run for run in child_runs if run["status"] in _ACTIVE_RUN_STATUSES]
    _need(len(parent_runs) <= 1 and all(run["agent_id"] == payload["assignment"]["lead_id"]
                                       for run in parent_runs), "nonunique Lead writer")
    _need(len(child_runs) <= 1 and all(run["agent_id"] == payload["assignment"]["engineer_id"]
                                      for run in child_runs)
          and len(active) == len(parent_runs) + len(active_child_runs), "unexpected refresh run")
    if reservation["state"] == "reserved":
        _need(not child_runs and (child is None or child["detail"]["status"] == "backlog"),
              "reserved child started before initialization checkpoint")
    elif reservation["state"] == "child_initialized":
        _need((not child_runs and child["detail"]["status"] == "backlog")
              or (len(child_runs) == 1 and child["detail"]["status"] in {"todo", "in_progress", "in_review"}),
              "child dispatch effect is ambiguous")
    else:
        _need(len(child_runs) == 1 and child["detail"]["status"] in {"todo", "in_progress", "in_review"},
              "dispatched checkpoint lacks one owner run")
    return {"child": child, "child_prefix": child_prefix, "parent_prefix": parent_prefix,
            "child_runs": child_runs}


def _checkpoint_value(request: c.RefreshRequest, authorization_uuid: str, state: dict,
                      checkpoint: str, child: dict | None, previous: dict) -> str:
    projected = c._load_json(c.canonical_json(state), 4_194_304)
    projected["parent"]["revision"] += 1
    reservation = {
        "version": 1, "request_digest": request.digest,
        "authorization_uuid": authorization_uuid, "action_key": c.refresh_action(request),
        "state": checkpoint,
        "child_id": None if child is None else child["detail"]["id"],
        "child_identifier": None if child is None else child["detail"]["identifier"],
        "child_position": None if child is None else child["detail"]["position"],
        "prepared": None,
        "parent_status_category": previous["parent_status_category"],
        "parent_position": previous["parent_position"],
        "parent_projection_digest": c.parent_projection_digest(projected),
    }
    return c.canonical_json(reservation)


def _finish_progress(request: c.RefreshRequest, state: dict, reservation: dict,
                     child_identifier: str, evidence: c.RefreshComment,
                     result: str, target_sha: str) -> tuple[int, dict, list[tuple[str, str]]]:
    payload = c._request(request)
    matches = [item for item in state["children"]
               if item["detail"]["identifier"] == child_identifier]
    _need(len(matches) == 1, "refresh completion child changed")
    child = matches[0]
    _need(reservation["state"] == "child_dispatched"
          and reservation["child_id"] == child["detail"]["id"]
          and reservation["child_identifier"] == child_identifier,
          "refresh completion reservation mismatch")
    base = dict(_refresh_child_prefix(request))
    candidates = [dict(base)]
    writes = []
    if result == "pass":
        writes.append(("eventra.phase.sha.frontend", target_sha))
    writes.extend((
        ("eventra.phase.result", result),
        ("eventra.phase.evidence_comment", evidence.comment_uuid),
        ("eventra.phase.failure_repositories", "[]" if result == "pass" else '["frontend"]'),
    ))
    for key, value in writes:
        candidates.append({**candidates[-1], key: value})
    _need(child["metadata"] in candidates, "refresh completion metadata is not an exact prefix")
    progress = candidates.index(child["metadata"])
    evidence_index = next(index for index, (key, _) in enumerate(writes, 1)
                          if key == "eventra.phase.evidence_comment")
    _need((progress < evidence_index and child["evidence"] is None)
          or (progress >= evidence_index and child["evidence"] == asdict(evidence)),
          "refresh completion evidence binding mismatch")
    _need((progress < len(writes) and child["detail"]["status"] in {"todo", "in_progress", "in_review"})
          or (progress == len(writes)
              and child["detail"]["status"] in {"todo", "in_progress", "in_review", "done"}),
          "refresh completion status is not recoverable")

    restored = c._load_json(c.canonical_json(state), 4_194_304)
    restored_child = next(item for item in restored["children"]
                          if item["detail"]["identifier"] == child_identifier)
    restored_child["metadata"] = base
    restored_child["detail"]["metadata"] = base
    restored_child["evidence"] = None
    restored_child["detail"]["revision"] -= progress
    if restored_child["detail"]["status"] == "done":
        restored_child["detail"]["status"] = "todo"
        restored_child["detail"]["status_category"] = "todo"
        restored_child["detail"]["revision"] -= 1
    _initialization_progress(request, restored, reservation)
    _need(state["pr"]["head_sha"] == payload["source"]["sha"]
          and state["metadata"].get("eventra.workflow.frontend_sha") == payload["source"]["sha"]
          and not {c.REFRESH_PREFIX + "adoption", c.REFRESH_PREFIX + "consumed"} & set(state["metadata"]),
          "refresh preparation cannot publish or adopt the candidate")
    return progress, child, writes


RESERVATION_FIELDS = frozenset({
    "version", "request_digest", "authorization_uuid", "action_key", "state",
    "child_id", "child_identifier", "child_position", "prepared",
    "parent_status_category", "parent_position", "parent_projection_digest",
})


def _adoption_receipts(request: c.RefreshRequest, prepared: c.PreparedCandidate,
                       authorization_uuid: str) -> tuple[str, str]:
    common = {
        "version": 1, "request_digest": request.digest,
        "authorization_uuid": authorization_uuid,
        "child_id": prepared.child_id, "target_sha": prepared.target_sha,
    }
    consumed = c.canonical_json(common)
    adoption = c.canonical_json({
        **common, "source_sha": prepared.source_sha,
        "prerequisite_sha": prepared.prerequisite_sha,
        "evidence_uuid": prepared.evidence_uuid,
        "evidence_digest": prepared.evidence_digest,
        "stage": 2, "control_tool_sha": request.payload()["control_tool_sha"],
    })
    return adoption, consumed


def _adoption_progress(request: c.RefreshRequest, state: dict, reservation: dict,
                       prepared: c.PreparedCandidate) -> int:
    metadata = state["metadata"]
    adoption, consumed = _adoption_receipts(
        request, prepared, reservation["authorization_uuid"])
    cases = [
        (None, prepared.source_sha, None),
        (adoption, prepared.source_sha, None),
        (adoption, prepared.target_sha, None),
        (adoption, prepared.target_sha, consumed),
    ]
    observed = (
        metadata.get(c.REFRESH_PREFIX + "adoption"),
        metadata.get("eventra.workflow.frontend_sha"),
        metadata.get(c.REFRESH_PREFIX + "consumed"),
    )
    _need(observed in cases, "adoption metadata is not an exact write prefix")
    progress = cases.index(observed)
    feature = c.refresh_metadata(metadata)
    c._receipt_match(feature, request, prepared, partial=True)
    if reservation["state"] == "published":
        restored = c._load_json(c.canonical_json(state), 4_194_304)
        restored_metadata = restored["metadata"]
        if progress >= 1:
            del restored_metadata[c.REFRESH_PREFIX + "adoption"]
        if progress >= 2:
            restored_metadata["eventra.workflow.frontend_sha"] = prepared.source_sha
        if progress >= 3:
            del restored_metadata[c.REFRESH_PREFIX + "consumed"]
        restored["parent"]["metadata"] = restored_metadata
        _need(type(restored["parent"].get("revision")) is int
              and restored["parent"]["revision"] > progress,
              "adoption revision is not recoverable")
        restored["parent"]["revision"] -= progress
        _need(c.parent_projection_digest(restored) ==
              reservation["parent_projection_digest"],
              "published checkpoint cannot explain adoption prefix")
    else:
        _need(reservation["state"] == "adopted" and progress == len(cases) - 1
              and c.parent_projection_digest(state) ==
                  reservation["parent_projection_digest"],
              "adopted checkpoint is incomplete")
        c._receipt_match(feature, request, prepared)
    return progress


def _publication_authority(request: c.RefreshRequest, state: dict,
                           reservation: dict) -> tuple[dict, c.PreparedCandidate, int]:
    payload = c._request(request)
    _need(type(reservation) is dict and set(reservation) == RESERVATION_FIELDS,
          "invalid publication checkpoint shape")
    feature = c.refresh_metadata(state["metadata"])
    _need(feature is not None and feature.get("reservation") == reservation
          and feature.get("request_comment") is not None
          and feature.get("authorization_comment") ==
              reservation["authorization_uuid"],
          "publication checkpoint identity changed")
    _need(reservation["version"] == 1
          and reservation["request_digest"] == request.digest
          and reservation["action_key"] == c.refresh_action(request)
          and reservation["state"] in {
              "child_dispatched", "candidate_registered", "published", "adopted"
          }
          and type(reservation["parent_status_category"]) is str
          and type(reservation["parent_position"]) is int
          and state["parent"].get("position") == reservation["parent_position"],
          "invalid publication checkpoint")
    c._shared_authority(payload, state)
    c._request_comment(request, state, feature)
    grants = [item for item in state["comments"]
              if item.get("comment_uuid") == reservation["authorization_uuid"]]
    _need(len(grants) == 1, "missing publication grant")
    c._grant_in_state(request, state, c.RefreshComment(**grants[0]))
    child, prepared = c._refresh_child(request, state)
    _need(prepared is not None
          and reservation["child_id"] == child["detail"]["id"]
          and reservation["child_identifier"] == child["detail"]["identifier"]
          and reservation["child_position"] == child["detail"]["position"]
          and state["metadata"].get("eventra.workflow.next_stage") == "3"
          and state["metadata"].get("eventra.workflow.last_action") ==
              c.refresh_action(request)
          and state["metadata"].get("eventra.workflow.attempt") == "0"
          and state["metadata"].get("eventra.workflow.merge_state") == "not_ready"
          and feature.get("merge_permission") == "hold"
          and state["parent"].get("status") == "in_progress"
          and state["parent"].get("status_category") == "in_progress",
          "completed preparation authority changed")
    active = [run for run in state["runs"] if run["status"] in _ACTIVE_RUN_STATUSES]
    _need(len(active) <= 1 and all(
        run["issue_id"] == payload["parent"]["id"]
        and run["agent_id"] == payload["assignment"]["lead_id"]
        for run in active
    ), "publication has an unexpected active writer")

    if reservation["state"] == "child_dispatched":
        _need(reservation["prepared"] is None
              and not {"adoption", "consumed"} & set(feature)
              and state["pr"]["head_sha"] == prepared.source_sha
              and state["metadata"].get("eventra.workflow.frontend_sha") ==
                  prepared.source_sha
              and c.parent_projection_digest(state) ==
                  reservation["parent_projection_digest"],
              "preparation is not ready for registration")
        return child, prepared, 0

    _need(reservation["prepared"] == asdict(prepared)
          and state["pr"]["head_sha"] in {
              prepared.source_sha, prepared.target_sha
          }, "registered preparation changed")
    if reservation["state"] == "candidate_registered":
        _need(not {"adoption", "consumed"} & set(feature)
              and state["metadata"].get("eventra.workflow.frontend_sha") ==
                  prepared.source_sha
              and c.parent_projection_digest(state) ==
                  reservation["parent_projection_digest"],
              "candidate registration drift")
        return child, prepared, 0
    _need(state["pr"]["head_sha"] == prepared.target_sha,
          "published managed head mismatch")
    return child, prepared, _adoption_progress(request, state, reservation, prepared)


def _reservation_value(request: c.RefreshRequest, state: dict, reservation: dict,
                       checkpoint: str, prepared: c.PreparedCandidate) -> str:
    _need(checkpoint in {"candidate_registered", "published", "adopted"},
          "invalid publication checkpoint")
    projected = c._load_json(c.canonical_json(state), 4_194_304)
    projected["parent"]["revision"] += 1
    updated = {
        **reservation, "state": checkpoint, "prepared": asdict(prepared),
        "parent_projection_digest": c.parent_projection_digest(projected),
    }
    value = c.canonical_json(updated)
    prospective = dict(state["metadata"])
    prospective[c.REFRESH_PREFIX + "reservation"] = value
    c.validate_metadata_budget(prospective, request=request)
    return value


def _write_publication_checkpoint(api, request: c.RefreshRequest,
                                  prepared: c.PreparedCandidate,
                                  expected: str, checkpoint: str) -> bool:
    parent = request.payload()["parent"]["identifier"]
    state = api.snapshot(parent).state()
    feature = c.refresh_metadata(state["metadata"])
    reservation = feature["reservation"]
    _, observed_prepared, _ = _publication_authority(request, state, reservation)
    _need(observed_prepared == prepared, "publication evidence changed")
    if reservation["state"] == checkpoint:
        return False
    _need(reservation["state"] == expected, "publication checkpoint order changed")
    value = _reservation_value(request, state, reservation, checkpoint, prepared)
    failure = None
    try:
        api.set_metadata(parent, c.REFRESH_PREFIX + "reservation", value)
    except RuntimeError as exc:
        failure = exc
    after = api.snapshot(parent).state()
    after_feature = c.refresh_metadata(after["metadata"])
    after_reservation = after_feature["reservation"]
    _, after_prepared, _ = _publication_authority(
        request, after, after_reservation)
    if after_reservation["state"] == checkpoint and after_prepared == prepared:
        return True
    _need(after_reservation == reservation and failure is not None,
          "publication checkpoint effect was not uniquely observed")
    raise failure


def register_candidate(api, request: c.RefreshRequest,
                       prepared: c.PreparedCandidate) -> None:
    """Persist immutable preparation identity before any managed-branch push."""
    _write_publication_checkpoint(
        api, request, prepared, "child_dispatched", "candidate_registered")


def adopt_candidate(api, request: c.RefreshRequest,
                    prepared: c.PreparedCandidate) -> None:
    """Recover the fixed adoption prefix, checkpoint it, then clear reservation."""
    parent = request.payload()["parent"]["identifier"]
    while True:
        state = api.snapshot(parent).state()
        feature = c.refresh_metadata(state["metadata"])
        if feature is not None and "reservation" not in feature:
            decision = c.plan_refresh(request, c.RefreshSnapshot(c.canonical_json(state)))
            _need(decision.kind in {"create_gate_stage", "wait"},
                  "cleared adoption is not a valid workflow state")
            return
        reservation = feature["reservation"]
        _, observed_prepared, progress = _publication_authority(
            request, state, reservation)
        _need(observed_prepared == prepared
              and reservation["state"] in {"published", "adopted"},
              "adoption checkpoint changed")
        adoption, consumed = _adoption_receipts(
            request, prepared, reservation["authorization_uuid"])
        writes = [
            (c.REFRESH_PREFIX + "adoption", adoption),
            ("eventra.workflow.frontend_sha", prepared.target_sha),
            (c.REFRESH_PREFIX + "consumed", consumed),
        ]
        if reservation["state"] == "published" and progress < len(writes):
            key, value = writes[progress]
            failure = None
            try:
                api.set_metadata(parent, key, value)
            except RuntimeError as exc:
                failure = exc
            after = api.snapshot(parent).state()
            after_feature = c.refresh_metadata(after["metadata"])
            _, after_prepared, observed = _publication_authority(
                request, after, after_feature["reservation"])
            if after_prepared == prepared and observed == progress + 1:
                continue
            _need(observed == progress and failure is not None,
                  "adoption metadata effect was not uniquely observed")
            raise failure
        if reservation["state"] == "published":
            _write_publication_checkpoint(
                api, request, prepared, "published", "adopted")
            continue

        before_delete = state
        failure = None
        try:
            api.delete_metadata(parent, c.REFRESH_PREFIX + "reservation")
        except RuntimeError as exc:
            failure = exc
        after = api.snapshot(parent)
        after_state = after.state()
        after_feature = c.refresh_metadata(after_state["metadata"])
        if after_feature is not None and "reservation" not in after_feature:
            expected = c._load_json(c.canonical_json(before_delete), 4_194_304)
            del expected["metadata"][c.REFRESH_PREFIX + "reservation"]
            expected["parent"]["metadata"] = expected["metadata"]
            expected["parent"]["revision"] += 1
            for key in ("updated_at", "last_activity_at"):
                expected["parent"][key] = after_state["parent"][key]
            _need(after_state == expected,
                  "reservation deletion observed concurrent authority drift")
            decision = c.plan_refresh(request, after)
            _need(decision.kind in {"create_gate_stage", "wait"},
                  "adoption clearance did not expose a valid next state")
            return
        _need(after_feature.get("reservation") == reservation and failure is not None,
              "reservation deletion effect was not uniquely observed")
        raise failure


def _resume_publication(api, git, request: c.RefreshRequest,
                        reservation: dict) -> RefreshExecutionResult:
    parent = request.payload()["parent"]["identifier"]
    mutations = 0
    state = api.snapshot(parent).state()
    child, prepared, _ = _publication_authority(request, state, reservation)
    if reservation["state"] == "child_dispatched":
        tree = git.verify_candidate(request, prepared.target_sha)
        _need(tree == prepared.tree_sha, "registered candidate tree mismatch")
        revision = state["parent"]["revision"]
        register_candidate(api, request, prepared)
        state = api.snapshot(parent).state()
        mutations += state["parent"]["revision"] - revision
        reservation = c.refresh_metadata(state["metadata"])["reservation"]

    if reservation["state"] == "candidate_registered":
        tree = git.verify_candidate(request, prepared.target_sha)
        _need(tree == prepared.tree_sha, "published candidate tree mismatch")
        state = api.snapshot(parent).state()
        _, current_prepared, _ = _publication_authority(
            request, state, c.refresh_metadata(state["metadata"])["reservation"])
        _need(current_prepared == prepared, "publication authority changed before push")
        if git.publish_candidate(request, prepared):
            mutations += 1
        after_push = api.snapshot(parent).state()
        after_reservation = c.refresh_metadata(after_push["metadata"])["reservation"]
        _, after_prepared, _ = _publication_authority(
            request, after_push, after_reservation)
        _need(after_prepared == prepared
              and after_push["pr"]["head_sha"] == prepared.target_sha,
              "managed publication was not authoritatively observed")
        revision = after_push["parent"]["revision"]
        _write_publication_checkpoint(
            api, request, prepared, "candidate_registered", "published")
        state = api.snapshot(parent).state()
        mutations += state["parent"]["revision"] - revision
        reservation = c.refresh_metadata(state["metadata"])["reservation"]

    _need(reservation["state"] in {"published", "adopted"},
          "publication did not reach adoption")
    before = api.snapshot(parent).state()["parent"]["revision"]
    adopt_candidate(api, request, prepared)
    after = api.snapshot(parent)
    mutations += after.state()["parent"]["revision"] - before
    decision = c.plan_refresh(request, after)
    _need(decision.kind in {"create_gate_stage", "wait"},
          "adopted candidate did not expose the next workflow state")
    return RefreshExecutionResult(c.refresh_action(request), "adopted", mutations,
                                  child["detail"]["identifier"])


def execute_refresh(api, git, parent: str, request_uuid: str, grant_uuid: str,
                    expected_action_key: str) -> RefreshExecutionResult:
    """Initialize an authorized refresh under the durable parent reservation."""
    for value in (request_uuid, grant_uuid):
        c._uuid(value)
    mutations = 0
    with api.parent_lock(parent):
        initial_snapshot = api.snapshot(parent)
        raw_request = initial_snapshot.state()["metadata"].get(c.REFRESH_PREFIX + "request")
        request = c.parse_request(raw_request)
        payload = c._request(request)
        action_key = c.refresh_action(request)
        _need(parent == payload["parent"]["identifier"] and expected_action_key == action_key,
              "refresh execution identity mismatch")
        feature = c.refresh_metadata(initial_snapshot.state()["metadata"])
        if (feature is not None and "reservation" not in feature
                and {"adoption", "consumed"} <= set(feature)):
            _need(feature.get("request_comment") == request_uuid
                  and feature.get("authorization_comment") == grant_uuid,
                  "completed refresh authorization changed")
            decision = c.plan_refresh(request, initial_snapshot)
            _need(decision.kind in {"create_gate_stage", "wait"},
                  "completed refresh state is inconsistent")
            matches = [item for item in initial_snapshot.state()["children"]
                       if item["detail"].get("stage") == 2]
            _need(len(matches) == 1, "completed refresh child changed")
            return RefreshExecutionResult(action_key, "adopted", 0,
                                          matches[0]["detail"]["identifier"])
        if feature is None or "reservation" not in feature:
            progress = c.validate_initial_refresh_progress(request, initial_snapshot)
            _need(progress.metadata_writes >= 4 and progress.comment_writes == 2
                  and progress.request_comment is not None and progress.grant_comment is not None
                  and progress.request_comment.comment_uuid == request_uuid
                  and progress.grant_comment.comment_uuid == grant_uuid,
                  "refresh authorization comments mismatch")
            bindings = [
                (c.REFRESH_PREFIX + "request_comment", request_uuid),
                (c.REFRESH_PREFIX + "authorization_comment", grant_uuid),
            ]
            while progress.metadata_writes < 6:
                key, value = bindings[progress.metadata_writes - 4]
                try:
                    api.set_metadata(parent, key, value)
                except RuntimeError:
                    pass
                after = c.validate_initial_refresh_progress(request, api.snapshot(parent))
                _need((after.metadata_writes, after.comment_writes) ==
                      (progress.metadata_writes + 1, progress.comment_writes),
                      "authorization binding effect was not uniquely observed")
                mutations += 1
                progress = after
            c.admit_refresh(request, api.snapshot(parent), progress.grant_comment)

            state = api.snapshot(parent).state()
            projected = c._load_json(c.canonical_json(state), 4_194_304)
            projected["parent"]["revision"] += 1
            reservation = {
                "version": 1, "request_digest": request.digest,
                "authorization_uuid": grant_uuid, "action_key": action_key,
                "state": "reserved", "child_id": None, "child_identifier": None,
                "child_position": None, "prepared": None,
                "parent_status_category": state["parent"]["status_category"],
                "parent_position": state["parent"]["position"],
                "parent_projection_digest": c.parent_projection_digest(projected),
            }
            reservation_value = c.canonical_json(reservation)
            prospective = dict(state["metadata"])
            prospective[c.REFRESH_PREFIX + "reservation"] = reservation_value
            c.validate_metadata_budget(prospective, request=request)
            api.set_metadata(parent, c.REFRESH_PREFIX + "reservation", reservation_value)
            mutations += 1
            reserved_snapshot = api.snapshot(parent)
            feature = c.refresh_metadata(reserved_snapshot.state()["metadata"])
            _need(feature is not None and feature.get("reservation") == reservation
                  and c.parent_projection_digest(reserved_snapshot.state()) ==
                      reservation["parent_projection_digest"],
                  "reserved checkpoint was not authoritatively observed")
            decision = c.plan_refresh(request, reserved_snapshot)
            _need(decision.kind == "resume_refresh", "reserved checkpoint is not recoverable")
        else:
            state = initial_snapshot.state()
            reservation = feature["reservation"]
            _need(feature.get("request_comment") == request_uuid
                  and feature.get("authorization_comment") == grant_uuid
                  and reservation.get("request_digest") == request.digest
                  and reservation.get("authorization_uuid") == grant_uuid
                  and reservation.get("action_key") == action_key,
                  "invalid refresh initialization checkpoint")
            if reservation.get("state") in {
                    "candidate_registered", "published", "adopted"}:
                return _resume_publication(api, git, request, reservation)
            if reservation.get("state") == "child_dispatched":
                matches = [item for item in state["children"]
                           if item["detail"].get("identifier") ==
                               reservation.get("child_identifier")]
                if len(matches) == 1 and matches[0]["detail"].get("status") == "done":
                    return _resume_publication(api, git, request, reservation)
            _initialization_progress(request, state, reservation)

        state = api.snapshot(parent).state()
        feature = c.refresh_metadata(state["metadata"])
        reservation = feature["reservation"]
        progress = _initialization_progress(request, state, reservation)
        if reservation["state"] == "child_dispatched":
            return RefreshExecutionResult(action_key, "child_dispatched", 0,
                                          progress["child"]["detail"]["identifier"])
        if progress["child"] is None:
            title = f"{parent}: prepare candidate refresh"
            description = c.canonical_json({"schema_version": 1, "request_digest": request.digest,
                                            "action_key": action_key})
            failure = None
            try:
                api.create_child(
                    parent=parent, stage=2, title=title,
                    project_id=payload["assignment"]["project_id"],
                    assignee_id=payload["assignment"]["engineer_id"], description=description,
                )
            except RuntimeError as exc:
                failure = exc
            after = api.snapshot(parent).state()
            observed = _initialization_progress(request, after, reservation)
            if observed["child"] is None:
                _need(failure is not None, "child creation effect was not observed")
                raise failure
            if failure is not None:
                # Creation is reconciled by the next locked execution so that an
                # acknowledgement loss never silently becomes a full dispatch.
                raise failure
            mutations += 1
            state, progress = after, observed

        expected_child_metadata = _refresh_child_prefix(request)
        while progress["child_prefix"] < len(expected_child_metadata):
            before_prefix = progress["child_prefix"]
            key, value = expected_child_metadata[before_prefix]
            failure = None
            try:
                api.set_metadata(progress["child"]["detail"]["identifier"], key, value)
            except RuntimeError as exc:
                failure = exc
            after = api.snapshot(parent).state()
            observed = _initialization_progress(request, after, reservation)
            if observed["child_prefix"] == before_prefix + 1:
                mutations += 1
                state, progress = after, observed
                continue
            _need(observed["child_prefix"] == before_prefix and failure is not None,
                  "child metadata effect was not uniquely observed")
            raise failure

        parent_writes = [
            ("metadata", "eventra.workflow.next_stage", "3"),
            ("metadata", "eventra.workflow.last_action", action_key),
            ("status", "in_progress", "no-start"),
        ]
        while progress["parent_prefix"] < len(parent_writes):
            before_prefix = progress["parent_prefix"]
            operation, key, value = parent_writes[before_prefix]
            failure = None
            try:
                if operation == "metadata":
                    api.set_metadata(parent, key, value)
                else:
                    api.set_status(parent, key, start=False,
                                   position=reservation["parent_position"])
            except RuntimeError as exc:
                failure = exc
            after = api.snapshot(parent).state()
            observed = _initialization_progress(request, after, reservation)
            if observed["parent_prefix"] == before_prefix + 1:
                mutations += 1
                state, progress = after, observed
                continue
            _need(observed["parent_prefix"] == before_prefix and failure is not None,
                  "parent initialization effect was not uniquely observed")
            raise failure

        if reservation["state"] == "reserved":
            initialized_value = _checkpoint_value(
                request, grant_uuid, state, "child_initialized", progress["child"], reservation)
            initialized = c._load_json(initialized_value, c.MAX_COMMENT_BYTES)
            failure = None
            try:
                api.set_metadata(parent, c.REFRESH_PREFIX + "reservation", initialized_value)
            except RuntimeError as exc:
                failure = exc
            after = api.snapshot(parent).state()
            observed_feature = c.refresh_metadata(after["metadata"])
            if observed_feature["reservation"] == initialized:
                mutations += 1
                state, reservation = after, initialized
                progress = _initialization_progress(request, state, reservation)
            else:
                _need(observed_feature["reservation"] == reservation and failure is not None,
                      "child_initialized checkpoint effect was not uniquely observed")
                raise failure

        if not progress["child_runs"]:
            failure = None
            try:
                api.set_status(progress["child"]["detail"]["identifier"], "todo", start=True)
            except RuntimeError as exc:
                failure = exc
            after = api.snapshot(parent).state()
            observed = _initialization_progress(request, after, reservation)
            if len(observed["child_runs"]) == 1:
                mutations += 1
                state, progress = after, observed
            else:
                _need(not observed["child_runs"] and failure is not None,
                      "child dispatch effect was not uniquely observed")
                raise failure

        dispatched_value = _checkpoint_value(
            request, grant_uuid, state, "child_dispatched", progress["child"], reservation)
        dispatched = c._load_json(dispatched_value, c.MAX_COMMENT_BYTES)
        failure = None
        try:
            api.set_metadata(parent, c.REFRESH_PREFIX + "reservation", dispatched_value)
        except RuntimeError as exc:
            failure = exc
        after = api.snapshot(parent).state()
        observed_feature = c.refresh_metadata(after["metadata"])
        if observed_feature["reservation"] == dispatched:
            mutations += 1
            progress = _initialization_progress(request, after, dispatched)
        else:
            _need(observed_feature["reservation"] == reservation and failure is not None,
                  "child_dispatched checkpoint effect was not uniquely observed")
            raise failure
        return RefreshExecutionResult(action_key, "child_dispatched", mutations,
                                      progress["child"]["detail"]["identifier"])


def finish_refresh(api, git, child: str, evidence_uuid: str,
                   result: str) -> RefreshExecutionResult:
    """Record one verified preparation outcome without publishing the managed PR."""
    c._match(child, c._ISSUE)
    c._uuid(evidence_uuid)
    _need(result in {"pass", "fail", "blocked"}, "invalid refresh completion result")
    parent = api.parent_for_child(child)
    mutations = 0
    with api.parent_lock(parent):
        state = api.snapshot(parent).state()
        request = c.parse_request(state["metadata"].get(c.REFRESH_PREFIX + "request"))
        feature = c.refresh_metadata(state["metadata"])
        _need(feature is not None and type(feature.get("reservation")) is dict,
              "missing refresh completion reservation")
        reservation = feature["reservation"]
        evidence = api.comment(child, evidence_uuid)
        prepared = None
        if result == "pass":
            prepared = c.parse_prepared(evidence, request, reservation.get("child_id"))
            body = c._block(evidence.content, "prepared")
            _need(body["context_receipt"]["task_id"] == child,
                  "prepared context is not scoped to the authoritative child")
            target_sha = prepared.target_sha
            status = "prepared"
        else:
            outcome = c.parse_outcome(evidence, request, reservation.get("child_id"), result)
            target_sha = outcome.source_sha
            status = result
        progress, current_child, writes = _finish_progress(
            request, state, reservation, child, evidence, result, target_sha)
        if progress == len(writes) and current_child["detail"]["status"] == "done":
            return RefreshExecutionResult(c.refresh_action(request), status, 0, child)
        if prepared is not None:
            tree = git.verify_candidate(request, prepared.target_sha)
            _need(tree == prepared.tree_sha, "prepared candidate tree mismatch")

        while progress < len(writes):
            key, value = writes[progress]
            failure = None
            try:
                api.set_metadata(child, key, value)
            except RuntimeError as exc:
                failure = exc
            after = api.snapshot(parent).state()
            observed, current_child, _ = _finish_progress(
                request, after, reservation, child, evidence, result, target_sha)
            if observed == progress + 1:
                mutations += 1
                state, progress = after, observed
                continue
            _need(observed == progress and failure is not None,
                  "refresh completion metadata effect was not uniquely observed")
            raise failure

        if current_child["detail"]["status"] != "done":
            failure = None
            try:
                api.set_status(child, "done", start=False,
                               position=current_child["detail"]["position"])
            except RuntimeError as exc:
                failure = exc
            after = api.snapshot(parent).state()
            observed, completed_child, _ = _finish_progress(
                request, after, reservation, child, evidence, result, target_sha)
            if observed == len(writes) and completed_child["detail"]["status"] == "done":
                mutations += 1
            else:
                _need(current_child["detail"]["status"] != "done" and failure is not None,
                      "refresh completion status effect was not uniquely observed")
                raise failure
        return RefreshExecutionResult(c.refresh_action(request), status, mutations, child)
