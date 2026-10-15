#!/usr/bin/env python3
"""// Configuration/status of the global bwrap sandbox."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core import bash_sandbox_settings as settings  # noqa: E402
from factory.core.bash_scope_guard import SANDBOX_ENV, resolve_sandbox_mode  # noqa: E402
from factory.core.runtime import get_db_path  # noqa: E402
from factory.core.schema import initialize_database  # noqa: E402

_PROBE = "factory.core.bash_sandbox_settings.probe_bwrap_capability"


class _DatabaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_sandbox_set_")
        self._prev_sandbox = os.environ.get(SANDBOX_ENV)
        os.environ.pop(SANDBOX_ENV, None)
        self._env = patch.dict(
            os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name}, clear=False
        )
        self._env.start()
        initialize_database(get_db_path())

    def tearDown(self) -> None:
        self._env.stop()
        if self._prev_sandbox is None:
            os.environ.pop(SANDBOX_ENV, None)
        else:
            os.environ[SANDBOX_ENV] = self._prev_sandbox
        self._tmp.cleanup()


class SandboxEnabledTests(_DatabaseCase):
    def test_default_is_true(self) -> None:
        self.assertTrue(settings.sandbox_enabled())

    def test_set_false_persists(self) -> None:
        self.assertFalse(settings.set_sandbox_enabled(False))
        self.assertFalse(settings.sandbox_enabled())
        self.assertTrue(settings.set_sandbox_enabled(True))
        self.assertTrue(settings.sandbox_enabled())


class ResolveSandboxModeTests(unittest.TestCase):
    def test_unset_is_on(self) -> None:
        self.assertEqual(resolve_sandbox_mode({}), "on")

    def test_off_aliases(self) -> None:
        for value in ("off", "0", "no", "disabled", "OFF", "Disabled"):
            with self.subTest(value=value):
                self.assertEqual(resolve_sandbox_mode({SANDBOX_ENV: value}), "off")

    def test_observe(self) -> None:
        self.assertEqual(resolve_sandbox_mode({SANDBOX_ENV: "observe"}), "observe")

    def test_legacy_on_values(self) -> None:
        for value in ("on", "1", "true", "yes", "bwrap"):
            with self.subTest(value=value):
                self.assertEqual(resolve_sandbox_mode({SANDBOX_ENV: value}), "on")

    def test_blank_is_on(self) -> None:
        self.assertEqual(resolve_sandbox_mode({SANDBOX_ENV: "   "}), "on")


class SandboxStatusTests(_DatabaseCase):
    def _status(self, probe: str = "ok") -> dict:
        with patch(_PROBE, return_value=probe):
            return settings.sandbox_status()

    def test_default_ok(self) -> None:
        self.assertEqual(
            self._status("ok"),
            {"enabled": True, "active": True, "available": True, "reason": ""},
        )

    def test_user_disabled(self) -> None:
        settings.set_sandbox_enabled(False)
        status = self._status("ok")
        self.assertFalse(status["enabled"])
        self.assertFalse(status["active"])
        self.assertTrue(status["available"])
        self.assertEqual(status["reason"], "user_disabled")

    def test_observe_keeps_enabled_but_inactive(self) -> None:
        os.environ[SANDBOX_ENV] = "observe"
        status = self._status("ok")
        self.assertTrue(status["enabled"])
        self.assertFalse(status["active"])
        self.assertTrue(status["available"])
        self.assertEqual(status["reason"], "observe")

    def test_probe_not_ok_is_fail_closed(self) -> None:
        for probe in ("bwrap_missing", "namespace_blocked"):
            with self.subTest(probe=probe):
                status = self._status(probe)
                self.assertTrue(status["enabled"])
                self.assertFalse(status["active"])
                self.assertFalse(status["available"])
                self.assertEqual(status["reason"], probe)

    def test_env_off_beats_enabled_setting(self) -> None:
        settings.set_sandbox_enabled(True)
        os.environ[SANDBOX_ENV] = "off"
        status = self._status("ok")
        self.assertFalse(status["enabled"])
        self.assertFalse(status["active"])
        self.assertEqual(status["reason"], "env_disabled")

    def test_env_off_beats_user_disabled_setting(self) -> None:
        settings.set_sandbox_enabled(False)
        os.environ[SANDBOX_ENV] = "disabled"
        status = self._status("ok")
        self.assertEqual(status["reason"], "env_disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
