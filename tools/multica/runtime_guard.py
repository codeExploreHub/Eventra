"""Cross-process, cross-worktree guards for Eventra workflow executors."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class SingleFlightClaim:
    acquired: bool
    outcome: str
    parent: str
    action_key: str
    recovered_stale: bool = False
    holder_action_key: str = ""


def shared_single_flight_root() -> Path:
    """Return a lease directory shared by every worktree of this repository."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("workflow lease root could not be resolved") from None
    common = completed.stdout.strip()
    if completed.returncode != 0 or not common:
        raise RuntimeError("workflow lease root could not be resolved")
    path = Path(common)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise RuntimeError("workflow lease root could not be resolved") from None
    return resolved / "eventra-workflow-leases"


def _read_record(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"action_key", "parent", "pid", "started_at", "token"}
        or type(value["action_key"]) is not str
        or type(value["parent"]) is not str
        or type(value["pid"]) is not int
        or type(value["started_at"]) not in {int, float}
        or type(value["token"]) is not str
    ):
        return None
    return value


def _write_record(path: Path, record: dict[str, object]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@contextmanager
def file_single_flight(
    parent: str,
    action_key: str,
    *,
    root: Path | None = None,
) -> Iterator[SingleFlightClaim]:
    """Claim one parent/action without waiting or writing as a contender.

    The kernel lock is the liveness authority. A record left behind without a
    lock is an explicit stale lease and is replaced by the next owner.
    """

    if type(parent) is not str or not parent or type(action_key) is not str or not action_key:
        raise RuntimeError("workflow lease identity is invalid")
    lease_root = shared_single_flight_root() if root is None else Path(root)
    lease_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        lease_root.chmod(0o700)
    except OSError:
        pass
    digest = hashlib.sha256(parent.encode("utf-8")).hexdigest()
    lock_path = lease_root / f"{digest}.lock"
    record_path = lease_root / f"{digest}.json"
    lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(lock_descriptor, "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = _read_record(record_path)
            holder_action = "" if holder is None else str(holder["action_key"])
            outcome = "wait" if holder_action == action_key else "conflict"
            yield SingleFlightClaim(
                False,
                outcome,
                parent,
                action_key,
                holder_action_key=holder_action,
            )
            return

        stale = record_path.exists()
        token = str(uuid.uuid4())
        record = {
            "action_key": action_key,
            "parent": parent,
            "pid": os.getpid(),
            "started_at": time.time(),
            "token": token,
        }
        _write_record(record_path, record)
        try:
            yield SingleFlightClaim(
                True,
                "acquired",
                parent,
                action_key,
                recovered_stale=stale,
            )
        finally:
            current = _read_record(record_path)
            if current is not None and current["token"] == token:
                try:
                    record_path.unlink()
                except FileNotFoundError:
                    pass
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
