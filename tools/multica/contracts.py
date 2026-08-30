"""Pure, strict parsers for observed Multica CLI read contracts."""

import re
from typing import Any
from urllib.parse import urlsplit


_GITHUB_COMPONENT = re.compile(r"[A-Za-z0-9._-]+\Z")
_COMMIT_REF = re.compile(r"[0-9a-f]{40}\Z")
_CASE_INSENSITIVE_COMMIT_REF = re.compile(r"[0-9A-Fa-f]{40}\Z")


def _error(contract: str) -> None:
    raise RuntimeError(f"malformed {contract}")


def _string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _records(value: Any, contract: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        _error(contract)
    return value


def _id(record: dict[str, Any], contract: str) -> str:
    value = record.get("id")
    if not _string(value):
        _error(contract)
    return value


def _named_records(value: Any, contract: str, name: str) -> list[dict[str, str]]:
    result = []
    ids = set()
    for record in _records(value, contract):
        record_id = _id(record, contract)
        record_name = record.get(name)
        if not _string(record_name) or record_id in ids:
            _error(contract)
        ids.add(record_id)
        result.append({"id": record_id, name: record_name})
    return result


def parse_runtime_list(value: Any, expected_runtime_id: str) -> list[dict[str, Any]]:
    """Normalize runtime records required for worktree-capability checks."""

    contract = "runtime list"
    if not _string(expected_runtime_id):
        _error(contract)
    result = []
    ids = set()
    for record in _records(value, contract):
        record_id = _id(record, contract)
        if record_id in ids:
            _error(contract)
        ids.add(record_id)
        if record_id != expected_runtime_id:
            result.append({"id": record_id})
            continue
        daemon_id = record.get("daemon_id")
        status = record.get("status")
        metadata = record.get("metadata")
        capabilities = metadata.get("capabilities") if isinstance(metadata, dict) else None
        if (
            not _string(daemon_id)
            or not _string(status)
            or not isinstance(metadata, dict)
            or not isinstance(capabilities, list)
            or not all(_string(item) for item in capabilities)
        ):
            _error(contract)
        result.append(
            {
                "id": record_id,
                "daemon_id": daemon_id,
                "status": status,
                "metadata": {"capabilities": list(capabilities)},
            }
        )
    return result


def parse_skill_list(value: Any) -> list[dict[str, str]]:
    return _named_records(value, "skill list", "name")


def parse_github_skill_url(value: Any) -> dict[str, str]:
    """Parse one canonical public GitHub tree URL without normalization."""

    if not _string(value):
        raise ValueError("Skill URL must be a canonical public GitHub tree URL")
    parsed = urlsplit(value)
    parts = parsed.path.split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or len(parts) < 6
        or parts[0] != ""
        or any(
            not part
            or part in {".", ".."}
            or _GITHUB_COMPONENT.fullmatch(part) is None
            for part in parts[1:]
        )
        or parts[3] != "tree"
    ):
        raise ValueError("Skill URL must be a canonical public GitHub tree URL")
    owner, repo, ref = parts[1], parts[2], parts[4]
    path = "/".join(parts[5:])
    if (
        _CASE_INSENSITIVE_COMMIT_REF.fullmatch(ref) is not None
        and _COMMIT_REF.fullmatch(ref) is None
    ):
        raise ValueError("Skill URL must be a canonical public GitHub tree URL")
    canonical = f"https://github.com/{owner}/{repo}/tree/{ref}/{path}"
    if value != canonical:
        raise ValueError("Skill URL must be a canonical public GitHub tree URL")
    return {
        "owner": owner,
        "repo": repo,
        "ref": ref,
        "path": path,
        "source_url": value,
    }


def github_skill_origin_matches(
    desired_url: Any,
    observed_origin: Any,
) -> bool:
    """Compare a desired GitHub Skill URL with structured Multica evidence."""

    if not isinstance(observed_origin, dict):
        return False
    required = ("type", "owner", "repo", "ref", "path", "source_url")
    if any(not _string(observed_origin.get(field)) for field in required):
        return False
    if observed_origin["type"] != "github":
        return False
    try:
        desired = parse_github_skill_url(desired_url)
        observed = parse_github_skill_url(observed_origin["source_url"])
    except ValueError:
        return False
    if any(
        observed_origin[field] != observed[field]
        for field in ("owner", "repo", "ref", "path")
    ):
        return False
    if any(
        desired[field] != observed[field]
        for field in ("owner", "repo", "path")
    ):
        return False
    desired_ref = desired["ref"]
    observed_ref = observed["ref"]
    if _COMMIT_REF.fullmatch(desired_ref) is not None:
        return observed_ref == desired_ref
    return (
        observed_ref == desired_ref
        or _COMMIT_REF.fullmatch(observed_ref) is not None
    )


def parse_skill_detail(value: Any, expected_id: str) -> dict[str, Any]:
    contract = "skill detail"
    if not isinstance(value, dict) or not _string(expected_id):
        _error(contract)
    record_id = _id(value, contract)
    name = value.get("name")
    config = value.get("config")
    origin = config.get("origin") if isinstance(config, dict) else None
    origin_fields = ("type", "owner", "repo", "ref", "path", "source_url")
    if (
        record_id != expected_id
        or not _string(name)
        or not isinstance(origin, dict)
        or any(not _string(origin.get(field)) for field in origin_fields)
    ):
        _error(contract)
    return {
        "id": record_id,
        "name": name,
        "config": {
            "origin": {field: origin[field] for field in origin_fields}
        },
    }


def parse_agent_list(value: Any) -> list[dict[str, str]]:
    return _named_records(value, "agent list", "name")


def parse_agent_detail(value: Any, expected_id: str) -> dict[str, Any]:
    contract = "agent detail"
    if not isinstance(value, dict) or not _string(expected_id):
        _error(contract)
    record_id = _id(value, contract)
    fields = ("name", "runtime_id", "visibility")
    if record_id != expected_id or any(not _string(value.get(field)) for field in fields):
        _error(contract)
    description = value.get("description")
    instructions = value.get("instructions")
    max_concurrent_tasks = value.get("max_concurrent_tasks")
    if (
        not isinstance(description, str)
        or not isinstance(instructions, str)
        or not isinstance(max_concurrent_tasks, int)
        or isinstance(max_concurrent_tasks, bool)
    ):
        _error(contract)
    return {
        "id": record_id,
        "name": value["name"],
        "description": description,
        "instructions": instructions,
        "runtime_id": value["runtime_id"],
        "visibility": value["visibility"],
        "max_concurrent_tasks": max_concurrent_tasks,
    }


def parse_agent_environment(value: Any, expected_id: str) -> dict[str, str]:
    """Parse the exact environment envelope without exposing malformed content."""

    contract = "agent environment"
    if not isinstance(value, dict) or not _string(expected_id) or value.get("agent_id") != expected_id:
        _error(contract)
    custom_env = value.get("custom_env")
    if not isinstance(custom_env, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in custom_env.items()
    ):
        _error(contract)
    return dict(custom_env)


def parse_agent_skill_list(value: Any) -> list[dict[str, str]]:
    return _named_records(value, "agent skill list", "name")


def parse_squad_list(value: Any) -> list[dict[str, str]]:
    return _named_records(value, "squad list", "name")


def parse_squad_detail(value: Any, expected_id: str) -> dict[str, str]:
    contract = "squad detail"
    if not isinstance(value, dict) or not _string(expected_id):
        _error(contract)
    record_id = _id(value, contract)
    name = value.get("name")
    leader_id = value.get("leader_id")
    description = value.get("description")
    instructions = value.get("instructions")
    if (
        record_id != expected_id
        or not _string(name)
        or not _string(leader_id)
        or not isinstance(description, str)
        or not isinstance(instructions, str)
    ):
        _error(contract)
    return {
        "id": record_id,
        "name": name,
        "description": description,
        "instructions": instructions,
        "leader_id": leader_id,
    }


def parse_squad_members(value: Any, expected_squad_id: str) -> list[dict[str, str]]:
    contract = "squad member list"
    if not _string(expected_squad_id):
        _error(contract)
    result = []
    ids = set()
    member_ids = set()
    for record in _records(value, contract):
        record_id = _id(record, contract)
        squad_id = record.get("squad_id")
        member_id = record.get("member_id")
        member_type = record.get("member_type")
        role = record.get("role")
        if (
            record_id in ids
            or member_id in member_ids
            or squad_id != expected_squad_id
            or not _string(member_id)
            or not _string(member_type)
            or not _string(role)
        ):
            _error(contract)
        ids.add(record_id)
        member_ids.add(member_id)
        result.append({"member_id": member_id, "member_type": member_type, "role": role})
    return result


def parse_project_list(value: Any) -> list[dict[str, str]]:
    return _named_records(value, "project list", "title")


def parse_project_detail(value: Any, expected_id: str) -> dict[str, str]:
    contract = "project detail"
    if not isinstance(value, dict) or not _string(expected_id):
        _error(contract)
    record_id = _id(value, contract)
    title = value.get("title")
    description = value.get("description")
    if record_id != expected_id or not _string(title) or not isinstance(description, str):
        _error(contract)
    return {"id": record_id, "title": title, "description": description}


def parse_project_resources(value: Any, expected_project_id: str) -> list[dict[str, Any]]:
    contract = "project resource list"
    if not _string(expected_project_id):
        _error(contract)
    result = []
    ids = set()
    for record in _records(value, contract):
        record_id = _id(record, contract)
        project_id = record.get("project_id")
        resource_type = record.get("resource_type")
        resource_ref = record.get("resource_ref")
        if (
            record_id in ids
            or project_id != expected_project_id
            or not _string(resource_type)
            or not isinstance(resource_ref, dict)
        ):
            _error(contract)
        local_path = resource_ref.get("local_path")
        daemon_id = resource_ref.get("daemon_id")
        execution_mode = resource_ref.get("execution_mode")
        if (
            not _string(local_path)
            or not _string(daemon_id)
            or ("execution_mode" in resource_ref and not _string(execution_mode))
        ):
            _error(contract)
        ids.add(record_id)
        normalized_ref = {"local_path": local_path, "daemon_id": daemon_id}
        if "execution_mode" in resource_ref:
            normalized_ref["execution_mode"] = execution_mode
        result.append(
            {
                "id": record_id,
                "resource_type": resource_type,
                "resource_ref": normalized_ref,
            }
        )
    return result


def _normalize_autopilot(value: Any, contract: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(contract)
    record_id = value.get("id")
    title = value.get("title")
    description = value.get("description")
    execution_mode = value.get("execution_mode")
    project_id = value.get("project_id")
    assignee_id = value.get("assignee_id")
    assignee_type = value.get("assignee_type")
    status = value.get("status")
    if (
        not _string(record_id)
        or not _string(title)
        or not isinstance(description, str)
        or execution_mode not in {"create_issue", "run_only"}
        or (project_id is not None and not _string(project_id))
        or not _string(assignee_id)
        or assignee_type != "agent"
        or status not in {"active", "paused"}
    ):
        _error(contract)
    return {
        "id": record_id,
        "title": title,
        "description": description,
        "execution_mode": execution_mode,
        "project_id": project_id,
        "assignee_id": assignee_id,
        "assignee_type": assignee_type,
        "status": status,
    }


def parse_autopilot_list(value: Any) -> list[dict[str, Any]]:
    """Normalize the Multica 0.4.33 Autopilot list envelope."""

    contract = "autopilot list"
    if (
        not isinstance(value, dict)
        or set(value) != {"autopilots", "total"}
        or not isinstance(value["autopilots"], list)
        or not isinstance(value["total"], int)
        or isinstance(value["total"], bool)
        or value["total"] != len(value["autopilots"])
    ):
        _error(contract)
    result = [_normalize_autopilot(item, contract) for item in value["autopilots"]]
    if (
        len({item["id"] for item in result}) != len(result)
        or len({item["title"] for item in result}) != len(result)
    ):
        _error(contract)
    return result


def parse_autopilot_detail(
    value: Any,
    expected_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize one safe schedule-only Autopilot detail envelope."""

    contract = "autopilot detail"
    if (
        not isinstance(value, dict)
        or set(value) != {"autopilot", "collaborators", "triggers"}
        or not _string(expected_id)
        or value["collaborators"] != []
        or not isinstance(value["triggers"], list)
        or len(value["triggers"]) > 1
    ):
        _error(contract)
    autopilot = _normalize_autopilot(value["autopilot"], contract)
    if autopilot["id"] != expected_id:
        _error(contract)
    if not value["triggers"]:
        return autopilot, []
    trigger = value["triggers"][0]
    if not isinstance(trigger, dict):
        _error(contract)
    trigger_id = trigger.get("id")
    autopilot_id = trigger.get("autopilot_id")
    cron_expression = trigger.get("cron_expression")
    timezone = trigger.get("timezone")
    enabled = trigger.get("enabled")
    label = trigger.get("label")
    sensitive_fields = (
        "provider",
        "signing_secret_hint",
        "webhook_path",
        "webhook_token",
        "webhook_token_hint",
        "webhook_url",
    )
    if (
        not _string(trigger_id)
        or autopilot_id != expected_id
        or trigger.get("kind") != "schedule"
        or not _string(cron_expression)
        or not _string(timezone)
        or len(cron_expression.split()) != 5
        or not isinstance(enabled, bool)
        or (label is not None and not isinstance(label, str))
        or trigger.get("has_signing_secret") is not False
        or trigger.get("has_webhook_token") is not False
        or any(trigger.get(field) is not None for field in sensitive_fields)
    ):
        _error(contract)
    return autopilot, [
        {
            "id": trigger_id,
            "autopilot_id": autopilot_id,
            "kind": "schedule",
            "cron_expression": cron_expression,
            "timezone": timezone,
            "enabled": enabled,
            "label": label,
        }
    ]
