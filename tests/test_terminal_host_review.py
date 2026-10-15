import json
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from factory.core.isolation.terminal_manager import PtyTerminalBackend, TerminalError, TerminalSpec
from factory.web_ui.server import _Handler


class HostTerminalReviewTest(unittest.TestCase):
    def handler(self, isolation):
        handler = object.__new__(_Handler)
        handler.server = Mock()
        handler.server._terminal_modes = {}
        handler._token = Mock(return_value="paired-device")
        handler._upstream = Mock(return_value=(200, json.dumps({
            "isolation": isolation, "meta": {"cwd": "/tmp"}
        }).encode(), "application/json"))
        return handler

    def test_kvm_never_falls_back_to_host_even_when_host_is_enabled(self):
        with patch.dict(os.environ, {"FACTORY_WEB_HOST_TERMINAL": "1"}):
            for isolation in ("kvm-unavailable", None):
                with self.subTest(isolation=isolation), self.assertRaises(TerminalError):
                    self.handler(isolation)._terminal_workdir("session")
            handler = self.handler("kvm")
            self.assertEqual(handler._terminal_workdir("session"), "/tmp")
            self.assertEqual(handler.server._terminal_modes["session"], "kvm")

    def test_host_requires_explicit_permission(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(TerminalError):
            self.handler("none")._terminal_workdir("session")
        with patch.dict(os.environ, {"FACTORY_WEB_HOST_TERMINAL": "1"}):
            self.assertEqual(self.handler("none")._terminal_workdir("session"), "/tmp")

    def test_revoked_device_cannot_open_shell(self):
        handler = self.handler("none")
        handler._upstream.return_value = (401, b"{}", "application/json")
        with self.assertRaises(TerminalError):
            handler._terminal_workdir("session")

    def test_real_pty_shell_runs_and_does_not_inherit_provider_secret(self):
        with tempfile.TemporaryDirectory() as cwd, patch.dict(os.environ, {"PROVIDER_API_KEY": "sentinel-secret"}):
            backend = PtyTerminalBackend(shell="/bin/bash", env=None)
            try:
                backend.open(TerminalSpec(session_id="test", rows=24, cols=80, workdir=cwd))
                backend.write(b"printf 'REVIEW_%s_%s\\n' OK ${PROVIDER_API_KEY-unset}; exit\n")
                output = bytearray()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    output.extend(backend.read(0.05) or b"")
                    if b"REVIEW_OK_unset" in output:
                        break
                self.assertIn(b"REVIEW_OK_unset", output)
            finally:
                backend.close()
