"""Small, frozen execution manifest; not a grant or a new workflow stage."""

import json
import re
from pathlib import Path


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)


def _workspace(value):
    if (not isinstance(value, str) or not value.startswith("/")
            or any(ord(char) < 32 for char in value)
            or ".." in value.split("/")):
        raise RuntimeError("smoke handoff requires explicit absolute workspace paths")
    path = Path(value).resolve()
    if path == Path(path.anchor):
        raise RuntimeError("smoke handoff cannot use a filesystem root")
    return path


def validate(value, candidates, pull_requests):
    """Validate completeness/bindings; QA must still verify Git and runtime truth."""
    if (not isinstance(value, dict)
            or set(value) != {"control_tool_workspace", "control_tool_sha", "repositories"}
            or not _sha(value["control_tool_sha"])
            or not isinstance(value["repositories"], dict)
            or set(value["repositories"]) != set(candidates)):
        raise RuntimeError("smoke execution requires a complete execution handoff")
    control = _workspace(value["control_tool_workspace"])
    protected = [control]
    inspections = []
    urls = {item["repository"]: item["url"] for item in pull_requests}
    for repository, item in value["repositories"].items():
        if (not isinstance(item, dict)
                or set(item) != {"runtime_workspace", "inspection_workspace",
                                 "candidate_sha", "merged_sha", "pr_url"}
                or item["candidate_sha"] != candidates[repository]
                or not _sha(item["merged_sha"])
                or item["pr_url"] != urls[repository]):
            raise RuntimeError("smoke handoff repository or exact PR/SHA binding is invalid")
        protected.append(_workspace(item["runtime_workspace"]))
        inspections.append(_workspace(item["inspection_workspace"]))
    for index, inspection in enumerate(inspections):
        for other in protected + inspections[:index]:
            if (inspection == other or inspection in other.parents
                    or other in inspection.parents):
                raise RuntimeError("smoke handoff inspection must be isolated from other workspaces")
    return value


def read_file(filename):
    if filename is None:
        return None
    try:
        # Bound input before parsing; the full reservation has its own size cap.
        with Path(filename).open(encoding="utf-8") as stream:
            content = stream.read(8193)
        if len(content) > 8192:
            raise ValueError("oversized input")
        return json.loads(content)
    except (OSError, UnicodeError, ValueError):
        raise RuntimeError("smoke handoff file is unreadable or invalid JSON") from None
