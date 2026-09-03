"""Read-only diagnostic for Multica API reachability and Go TLS failures."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import asdict, dataclass
from typing import Literal
from urllib.parse import urlparse
from urllib.request import getproxies


@dataclass(frozen=True)
class TLSDiagnosticResult:
    classification: Literal[
        "ok",
        "unreachable",
        "tls_handshake",
        "http_authentication",
        "business_timeout",
        "business_error",
    ]
    reachable: bool
    auth_checked: bool
    guidance: str

    def to_json(self) -> str:
        return json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def diagnose_multica_tls(*, timeout_seconds: float = 10) -> TLSDiagnosticResult:
    """Probe TCP reachability, then the authenticated read-only CLI path."""

    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("diagnostic timeout must be positive")
    proxy = getproxies().get("https")
    parsed_proxy = urlparse(proxy) if proxy else None
    target = (
        (parsed_proxy.hostname, parsed_proxy.port or 443)
        if parsed_proxy is not None and parsed_proxy.hostname
        else ("api.multica.ai", 443)
    )
    try:
        connection = socket.create_connection(target, timeout_seconds)
    except OSError:
        return TLSDiagnosticResult(
            "unreachable",
            False,
            False,
            "api.multica.ai:443 is not reachable; check the process network and proxy path.",
        )
    connection.close()

    try:
        completed = _run_auth_probe(timeout_seconds)
    except subprocess.TimeoutExpired:
        workaround_environment = os.environ.copy()
        current_godebug = workaround_environment.get("GODEBUG", "")
        workaround_environment["GODEBUG"] = ",".join(
            item for item in (current_godebug, "tlsmlkem=0") if item
        )
        try:
            _run_auth_probe(
                timeout_seconds,
                environment=workaround_environment,
            )
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            return TLSDiagnosticResult(
                "business_error",
                True,
                False,
                "The Multica CLI could not be started for the read-only request.",
            )
        else:
            return _tls_handshake_result()
        return TLSDiagnosticResult(
            "business_timeout",
            True,
            True,
            "The read-only authenticated request exceeded the diagnostic timeout.",
        )
    except OSError:
        return TLSDiagnosticResult(
            "business_error",
            True,
            False,
            "The Multica CLI could not be started for the read-only request.",
        )
    if completed.returncode == 0:
        return TLSDiagnosticResult(
            "ok",
            True,
            True,
            "Reachability, TLS negotiation, and the read-only authenticated request succeeded.",
        )

    diagnostic = f"{completed.stderr}\n{completed.stdout}".lower()
    if any(
        marker in diagnostic
        for marker in (
            "tls handshake timeout",
            "tls handshake failure",
            "remote error: tls",
            "x509:",
        )
    ):
        return _tls_handshake_result()
    if any(
        marker in diagnostic
        for marker in ("http 401", "http 403", "unauthorized", "forbidden")
    ):
        return TLSDiagnosticResult(
            "http_authentication",
            True,
            True,
            "TLS completed, but the server rejected authentication for the read-only request.",
        )
    if any(
        marker in diagnostic
        for marker in (
            "context deadline exceeded",
            "client.timeout exceeded",
            "request timed out",
        )
    ):
        return TLSDiagnosticResult(
            "business_timeout",
            True,
            True,
            "TLS completed, but the read-only business request timed out.",
        )
    return TLSDiagnosticResult(
        "business_error",
        True,
        True,
        "The read-only business request failed after the reachability check.",
    )


def _run_auth_probe(
    timeout_seconds: float,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["multica", "auth", "status"],
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
        env=environment,
    )


def _tls_handshake_result() -> TLSDiagnosticResult:
    return TLSDiagnosticResult(
        "tls_handshake",
        True,
        False,
        (
            "TLS negotiation failed. For the Multica 0.4.38 / Go 1.26 "
            "local-proxy ML-KEM compatibility case, retry only that process "
            "with GODEBUG=tlsmlkem=0; do not change global shell or proxy settings."
        ),
    )
