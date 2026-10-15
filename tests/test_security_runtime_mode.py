import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core.security_runtime import SecurityRuntime
from factory.core.security_store import SecurityStore


class SecurityRuntimeModeTest(unittest.TestCase):
    """`execute_command(security_mode=...)` must override the `env` mode entirely.

    `Env` is always `disabled`: the passed `security_mode` must affect branches
    `observe/enforce` and all `mode` in `details/events`, without mutating `self.mode`.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"FACTORY_SECURITY_RUNTIME_MODE": "disabled"})
        self._env.start()
        self.db_path = str(Path(self._tmp.name) / "factory.db")
        self.store = SecurityStore(self.db_path)
        self.runtime = SecurityRuntime(store=self.store)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def _call(self, security_mode: str | None):
        return self.runtime.execute_command(
            command=["/bin/echo", "hello"],
            input_payload="",
            env=dict(os.environ),
            cwd=self._tmp.name,
            timeout_s=30,
            task_id="task-1",
            profile="standard",
            backend="weird",
            security_mode=security_mode,
        )

    def _details(self, event_type: str) -> dict | None:
        for event in self.store.list_events(limit=50):
            if event["event_type"] == event_type:
                return event["details"]
        return None

    def test_override_enforces_when_env_disabled(self):
        # Environment mode is disabled, but the call requires enforcement. An unenforced observation blocks.
        self.assertEqual(self.runtime.mode, "disabled")
        with self.assertRaises(PermissionError):
            self._call("enforce")

        self.assertEqual(self._details("session_created")["mode"], "enforce")
        # `self.mode` is not mutated; `status()` remains process-level (env).
        self.assertEqual(self.runtime.mode, "disabled")
        self.assertEqual(self.runtime.status()["mode"], "disabled")

    def test_none_uses_env_mode(self):
        proc, _ = self._call(None)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self._details("session_created")["mode"], "disabled")

    def test_unknown_mode_falls_back_to_env(self):
        proc, _ = self._call("bogus")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self._details("session_created")["mode"], "disabled")

    def test_observe_override_records_effective_mode_everywhere(self):
        proc, _ = self._call("observe")
        self.assertEqual(proc.returncode, 0)

        # All events within the call reflect the passed mode, not `env`.
        self.assertEqual(self._details("session_created")["mode"], "observe")
        self.assertEqual(self._details("policy_evaluated")["mode"], "observe")
        self.assertEqual(self._details("approval_observed")["mode"], "observe")
        self.assertEqual(self._details("command_executed")["mode"], "observe")

        # `self.mode` is not mutated.
        self.assertEqual(self.runtime.mode, "disabled")
        self.assertEqual(self.runtime.status()["mode"], "disabled")

    def test_security_mode_is_keyword_only(self):
        with self.assertRaises(TypeError):
            self.runtime.execute_command(
                ["/bin/echo", "hello"],
                "",
                dict(os.environ),
                self._tmp.name,
                30,
                None,
                "standard",
                "weird",
                "",
                "",
                None,
                None,
                "enforce",
            )


if __name__ == "__main__":
    unittest.main()
