"""Read-only refresh authority adapter. No live mutation entry exists here yet.

Scope is supplied by trusted operator configuration, never by a request payload.
CLI wiring and validation against an approved deployment record belong to release
integration; constructing RefreshScope is not itself human deployment approval.
"""

from __future__ import annotations

import os
import re
import subprocess
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
        return self._comments(self._read(["issue", "comment", "list", identifier, "--full", "--compact"]), issue_id)

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
        assignment = self._assignment()
        listed = parse_issue_children(self._read(["issue", "children", parent_key]), parent["id"])
        children, runs = [], self._runs(parent_key, parent["id"])
        for item in listed:
            _need(item["project_id"] == assignment["project_id"], "non-frontend child")
            detail = self._detail(item["identifier"])
            _need(all(detail[k] == v for k, v in item.items()), "child detail differs from parent relation")
            child_metadata = parse_issue_metadata(self._read(["issue", "metadata", "list", item["identifier"]]))
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
        return {"parent": parent, "metadata": metadata, "children": children, "runs": runs,
                "comments": [asdict(comment) for comment in self._all_comments(parent_key, parent["id"])],
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
