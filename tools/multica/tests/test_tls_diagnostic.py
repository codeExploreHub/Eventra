"""Behavior tests for the read-only Multica TLS diagnostic."""

import subprocess
import unittest
from unittest.mock import patch


class TLSDiagnosticTests(unittest.TestCase):
    def _diagnose(self, completed=None, *, connect_error=None, timeout=False):
        from tools.multica.tls_diagnostic import diagnose_multica_tls

        def connect(_address, _timeout):
            if connect_error is not None:
                raise connect_error
            return _Socket()

        def run(*_args, **_kwargs):
            if timeout:
                raise subprocess.TimeoutExpired("multica", 5)
            return completed

        with patch("tools.multica.tls_diagnostic.socket.create_connection", connect), patch(
            "tools.multica.tls_diagnostic.subprocess.run", run
        ):
            return diagnose_multica_tls(timeout_seconds=5)

    def test_unreachable_api_is_distinct_from_authentication(self):
        result = self._diagnose(connect_error=OSError("network down"))
        self.assertEqual(result.classification, "unreachable")
        self.assertFalse(result.auth_checked)

    def test_go_tls_handshake_timeout_names_process_only_workaround(self):
        result = self._diagnose(
            subprocess.CompletedProcess(
                ["multica"], 1, "", "net/http: TLS handshake timeout"
            )
        )
        self.assertEqual(result.classification, "tls_handshake")
        self.assertIn("GODEBUG=tlsmlkem=0", result.guidance)
        self.assertNotIn("token", result.guidance.lower())

    def test_http_authentication_failure_is_not_reported_as_tls(self):
        result = self._diagnose(
            subprocess.CompletedProcess(["multica"], 1, "", "HTTP 401 Unauthorized")
        )
        self.assertEqual(result.classification, "http_authentication")
        self.assertTrue(result.auth_checked)

    def test_business_request_timeout_is_distinct_from_handshake_timeout(self):
        result = self._diagnose(timeout=True)
        self.assertEqual(result.classification, "business_timeout")
        self.assertTrue(result.auth_checked)

    def test_default_timeout_with_process_only_mlkem_success_is_tls_handshake(self):
        from tools.multica.tls_diagnostic import diagnose_multica_tls

        calls = []

        def run(*_args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired("multica", 5)
            return subprocess.CompletedProcess(["multica"], 0, "{}", "")

        with patch(
            "tools.multica.tls_diagnostic.socket.create_connection",
            return_value=_Socket(),
        ), patch("tools.multica.tls_diagnostic.subprocess.run", run):
            result = diagnose_multica_tls(timeout_seconds=5)

        self.assertEqual(result.classification, "tls_handshake")
        self.assertEqual(len(calls), 2)
        self.assertIn("tlsmlkem=0", calls[1]["env"]["GODEBUG"])

    def test_successful_read_only_auth_probe_reports_ok_without_payload(self):
        result = self._diagnose(
            subprocess.CompletedProcess(["multica"], 0, '{"authenticated":true}', "")
        )
        self.assertEqual(result.classification, "ok")
        self.assertNotIn('{"authenticated":true}', result.to_json())


class _Socket:
    def close(self):
        pass


if __name__ == "__main__":
    unittest.main()
