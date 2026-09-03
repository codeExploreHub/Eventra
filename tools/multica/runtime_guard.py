"""Local cross-process guards for Eventra workflow executors."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
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


@contextmanager
def file_single_flight(
    parent: str,
    action_key: str,
    *,
    root: Path,
) -> Iterator[SingleFlightClaim]:
    """Acquire the first local claim for one exact workflow action."""

    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(parent.encode("utf-8")).hexdigest()
    path = root / f"{digest}.json"
    handle = path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    record = {
        "action_key": action_key,
        "parent": parent,
        "pid": os.getpid(),
        "started_at": time.time(),
        "token": str(uuid.uuid4()),
    }
    handle.seek(0)
    handle.truncate()
    json.dump(record, handle, sort_keys=True, separators=(",", ":"))
    handle.flush()
    try:
        yield SingleFlightClaim(True, "acquired", parent, action_key)
    finally:
        handle.seek(0)
        handle.truncate()
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
