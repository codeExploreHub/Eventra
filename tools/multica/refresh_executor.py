"""Read-only refresh authority adapter. No live mutation entry exists here yet.

Scope is supplied by trusted operator configuration, never by a request payload.
CLI wiring and validation against an approved deployment record belong to release
integration; constructing RefreshScope is not itself human deployment approval.
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


class RefreshAPI:
    def __init__(self, runner, github, control_root, *, scope=None, prerequisite_pr=None):
        self.runner, self.github = runner, github
        self.control_root = Path(control_root).resolve()
        self.scope, self.prerequisite_pr = scope, prerequisite_pr

    def _scope(self):
        _need(type(self.scope) is RefreshScope, "explicit approved scope required")
        _need(type(self.scope.profile) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.scope.profile),
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
        _need(type(key) is str and key.startswith(c.REFRESH_PREFIX), "invalid refresh metadata key")
        c._size(value, c.MAX_COMMENT_BYTES)
        return self._read(["issue", "metadata", "set", issue, "--key", key,
                           "--value", value, "--type", "string"])

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
            c.comment_manifest(records, issue_id)
        except ValueError as exc:
            raise RuntimeError("refresh authority: " + str(exc)) from None
        _need(type(records) is list, "incomplete comment pagination")
        result, seen = [], set()
        required = {"id", "author_id", "author_type", "content", "revision", "type", "created_at"}
        auxiliary = {"issue_id", "parent_id", "updated_at", "reply_count", "last_activity_at"}
        for record in records:
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
                _need(record["parent_id"] in seen, "incomplete comment thread")
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
            comments = self._all_comments(item["identifier"], item["id"])
            evidence_uuid = child_metadata.get("eventra.phase.evidence_comment")
            evidence = [asdict(comment) for comment in comments if comment.comment_uuid == evidence_uuid]
            _need(evidence_uuid is None or len(evidence) == 1, "missing child evidence")
            children.append({"detail": detail, "metadata": child_metadata, "evidence": evidence[0] if evidence else None})
            runs.extend(self._runs(item["identifier"], item["id"]))
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
        _need(base_sha == pr["base"]["sha"] and prerequisite["base"]["ref"] == base_ref, "base drift")
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
                "prepared": None, "parent_projection_digest": c.parent_projection_digest(projected),
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
                  and reservation == {
                      "version": 1, "request_digest": request.digest,
                      "authorization_uuid": grant_uuid, "action_key": action_key,
                      "state": "reserved", "child_id": None, "child_identifier": None,
                      "prepared": None,
                      "parent_projection_digest": reservation.get("parent_projection_digest"),
                  }
                  and reservation["parent_projection_digest"] == c.parent_projection_digest(state),
                  "invalid reserved checkpoint")
            c._shared_authority(payload, state)
            c._request_comment(request, state, feature)
            grants = [item for item in state["comments"]
                      if item.get("comment_uuid") == grant_uuid]
            _need(len(grants) == 1, "missing refresh grant")
            c._grant_in_state(request, state, c.RefreshComment(**grants[0]))

        title = f"{parent}: prepare candidate refresh"
        description = c.canonical_json({"schema_version": 1, "request_digest": request.digest,
                                        "action_key": action_key})
        current = api.snapshot(parent).state()
        later = [item for item in current["children"]
                 if item["detail"]["id"] != payload["source"]["child_id"]]
        _need(len(later) <= 1, "duplicate refresh child")
        if later:
            detail, metadata = later[0]["detail"], later[0]["metadata"]
            expected = {"parent_issue_id": payload["parent"]["id"], "workspace_id": payload["workspace_id"],
                        "stage": 2, "status": "backlog", "project_id": payload["assignment"]["project_id"],
                        "assignee_type": "agent", "assignee_id": payload["assignment"]["engineer_id"],
                        "title": title, "description": description}
            _need(all(detail.get(key) == value for key, value in expected.items())
                  and metadata == {} and later[0]["evidence"] is None,
                  "existing refresh child is not the reserved creation effect")
            child = detail["identifier"]
        else:
            child = api.create_child(
                parent=parent, stage=2, title=title,
                project_id=payload["assignment"]["project_id"],
                assignee_id=payload["assignment"]["engineer_id"], description=description,
            )
            mutations += 1
        c._match(child, c._ISSUE)
        return RefreshExecutionResult(action_key, "child_created", mutations, child)
