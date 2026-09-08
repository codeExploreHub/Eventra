"""Fail-closed validation for business candidates before publication."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


REPOSITORIES = frozenset(("frontend", "backend"))
FORBIDDEN_ROOTS = frozenset((".worktrees", ".multica", ".agent_context"))
ORIGIN_URLS = {
    "frontend": "https://github.com/codeExploreHub/Eventra.git",
    "backend": "https://github.com/codeExploreHub/Eventra-Backend.git",
}


def _git(root: Path, args: list[str], *, valid_codes: tuple[int, ...] = (0,)):
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("candidate validation git command timed out") from None
    except OSError:
        raise RuntimeError("candidate validation git command could not start") from None
    if completed.returncode not in valid_codes:
        raise RuntimeError("candidate validation git command failed")
    return completed


def _commit_sha(root: Path, value: str, label: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{label} must be a full lowercase commit SHA")
    completed = _git(root, ["rev-parse", "--verify", f"{value}^{{commit}}"])
    resolved = completed.stdout.decode("ascii", errors="strict").strip()
    if resolved != value:
        raise RuntimeError(f"{label} does not resolve to the exact commit")
    return resolved


def _is_forbidden(path: str) -> bool:
    parts = path.split("/")
    return path == "AGENTS.md" or bool(parts and parts[0] in FORBIDDEN_ROOTS)


def validate_candidate(
    repository_root: Path | str,
    repository: str,
    base_sha: str,
    candidate_sha: str,
) -> dict[str, object]:
    """Validate the complete base-to-candidate diff and return a bound receipt."""
    if repository not in REPOSITORIES:
        raise ValueError("repository must be frontend or backend")
    root = Path(repository_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("repository root must be a directory")
    if _git(root, ["rev-parse", "--is-inside-work-tree"]).stdout.strip() != b"true":
        raise RuntimeError("repository root is not a git worktree")
    try:
        origin = _git(root, ["remote", "get-url", "origin"]).stdout.decode(
            "utf-8", errors="strict"
        ).strip()
    except UnicodeDecodeError:
        raise RuntimeError("repository origin is not valid UTF-8") from None
    if origin != ORIGIN_URLS[repository]:
        raise RuntimeError("repository origin does not match the declared repository")

    base = _commit_sha(root, base_sha, "base SHA")
    candidate = _commit_sha(root, candidate_sha, "candidate SHA")
    ancestry = _git(
        root,
        ["merge-base", "--is-ancestor", base, candidate],
        valid_codes=(0, 1),
    )
    if ancestry.returncode != 0:
        raise RuntimeError("candidate is not a descendant of the declared base")

    raw_paths = _git(
        root,
        ["diff", "--name-only", "--no-renames", "-z", f"{base}..{candidate}"],
    ).stdout
    try:
        changed_paths = sorted(
            path.decode("utf-8", errors="strict")
            for path in raw_paths.split(b"\0")
            if path
        )
    except UnicodeDecodeError:
        raise RuntimeError("candidate contains a non-UTF-8 path") from None
    if not changed_paths:
        raise RuntimeError("candidate contains no business changes")
    forbidden = sorted(path for path in changed_paths if _is_forbidden(path))
    if forbidden:
        roots = sorted({path.split("/", 1)[0] for path in forbidden})
        raise RuntimeError(
            "candidate contains forbidden runtime paths: " + ",".join(roots)
        )

    payload: dict[str, object] = {
        "base_sha": base,
        "candidate_sha": candidate,
        "changed_paths": changed_paths,
        "origin": origin,
        "repository": repository,
        "schema_version": 1,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    payload["receipt_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload
