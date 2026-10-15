#!/usr/bin/env python3
"""Regression coverage for `guard_mode` propagation through executor/tools/delegate.

Verify the explicit `guard_mode` reaches `enforce_bash_scope` through
`execute_tool` -> `registry.dispatch` -> `_handle_bash` -> `_tool_bash` ->
`_bash_scope_denial`, overrides environment defaults and reaches `delegate_task`
children.

The forwarding regression must fail if `_handle_bash` drops
`guard_mode=kwargs.get("guard_mode")`: the guard would then receive `None`
and use its environment default.

Run from the repository root:
    uv run --no-sync python -m pytest tests/test_guard_mode_plumbing.py -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WORKDIR = os.environ.get("IGOREK_TEST_WORKDIR", "/home/example/AI/workspace")
FOREIGN = "/home/example/AI/IgorekAgentFactory"
GUARD_ENV = "IGOREK_BASH_SCOPE_GUARD"

# External (outside workdir) file write: the real guard catches it, but the command is harmless,
# if executed in allow mode (file is guaranteed absent; `-f` suppresses error).
OUTSIDE_WRITE = f"rm -f {FOREIGN}/__guard_plumbing_probe_nonexistent__"

import factory.core.bash_scope_guard as bash_scope_guard  # noqa: E402
from factory.core.cc_backend_tools import _tool_bash, execute_tool  # noqa: E402

_OFF = {"off", "0", "false", "no", "disabled"}
_WARN = {"warn", "audit"}
_ON = {"on", "1", "true", "yes", "enabled"}


def _normalize(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in _OFF:
        return "off"
    if text in _WARN:
        return "warn"
    if text in _ON:
        return "on"
    return None


def _effective_env() -> str:
    """Environment mode; tests explicitly set env variables, so trial flip is irrelevant."""
    raw = (os.environ.get(GUARD_ENV) or "").strip().lower()
    if raw in _OFF:
        return "off"
    if raw in _WARN:
        return "warn"
    return "on"


def _install_capture_stub() -> tuple[object, object, list[dict]]:
    """Replaces `enforce_bash_scope`: logs the call and replicates its semantics.

    Returns `(real, stub, calls)`. On `allow`, returns `allow` (the test command is harmless); otherwise, returns `deny` with a marker — the real command **does not execute**.
    """
    real = bash_scope_guard.enforce_bash_scope
    calls: list[dict] = []

    def stub(command, guard_mode=None, *, workdir, filesystem_mode="workspace", extra_roots=None):
        explicit = _normalize(guard_mode)
        effective = explicit if explicit is not None else _effective_env()
        calls.append(
            {
                "command": command,
                "guard_mode": guard_mode,
                "effective": effective,
                "filesystem_mode": filesystem_mode,
            }
        )
        if effective == "off":
            return ("allow", "")
        return ("deny", f"POLICY_DENIED: captured guard_mode={guard_mode!r}")

    return real, stub, calls


class _GuardEnvMixin(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.get(GUARD_ENV)

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop(GUARD_ENV, None)
        else:
            os.environ[GUARD_ENV] = self._prev


class GuardModePlumbingTests(_GuardEnvMixin):
    def _with_stub(self):
        real, stub, calls = _install_capture_stub()
        bash_scope_guard.enforce_bash_scope = stub
        self.addCleanup(lambda: setattr(bash_scope_guard, "enforce_bash_scope", real))
        return calls

    # -- 1. `_tool_bash`: Explicit `off` overrides environment `on` -----------------------
    def test_explicit_off_beats_env_on_at_tool_bash(self) -> None:
        os.environ[GUARD_ENV] = "on"
        calls = self._with_stub()

        result = _tool_bash(
            {"command": "true"},
            workdir=WORKDIR,
            filesystem_mode="workspace",
            guard_mode="off",
        )

        self.assertEqual(calls[-1]["guard_mode"], "off")
        self.assertEqual(calls[-1]["effective"], "off")
        self.assertNotIn("POLICY_DENIED", result)

    # -- 2. `_tool_bash`: Explicit `on` overrides environment `off` (real guard) -------
    def test_explicit_on_beats_env_off_with_real_guard(self) -> None:
        os.environ[GUARD_ENV] = "off"

        result = _tool_bash(
            {"command": OUTSIDE_WRITE},
            workdir=WORKDIR,
            filesystem_mode="workspace",
            guard_mode="on",
        )

        self.assertIn("POLICY_DENIED", result)

    # -- 3. regression: Without guard_mode, behavior remains environment-driven --
    def test_env_off_allows_outside_write_when_guard_mode_absent(self) -> None:
        os.environ[GUARD_ENV] = "off"

        result = _tool_bash(
            {"command": OUTSIDE_WRITE},
            workdir=WORKDIR,
            filesystem_mode="workspace",
        )

        self.assertNotIn("POLICY_DENIED", result)

    # -- 4. MUTATION: execute_tool → _handle_bash forwards guard_mode -----
    def test_execute_tool_forwards_guard_mode_through_handle_bash(self) -> None:
        """A missing guard_mode forward must fail this regression.

        Without forwarding, _tool_bash receives None and the stub uses the enabled
        environment default, yielding POLICY_DENIED instead of the requested off mode.
        """
        os.environ[GUARD_ENV] = "on"
        calls = self._with_stub()

        result = execute_tool(
            "Bash",
            {"command": "true"},
            WORKDIR,
            guard_mode="off",
        )

        self.assertEqual(calls[-1]["guard_mode"], "off")
        self.assertEqual(calls[-1]["effective"], "off")
        self.assertNotIn("POLICY_DENIED", result)

    # -- 5. regression: execute_tool without guard_mode retains env-default ----
    def test_execute_tool_default_guard_mode_stays_env_driven(self) -> None:
        os.environ[GUARD_ENV] = "on"
        calls = self._with_stub()

        result = execute_tool("Bash", {"command": "true"}, WORKDIR)

        self.assertIsNone(calls[-1]["guard_mode"])
        self.assertEqual(calls[-1]["effective"], "on")
        self.assertIn("POLICY_DENIED", result)

    # -- 6. delegate_task forwards guard_mode to child -------------------
    def test_delegate_forwards_guard_mode_to_child_task(self) -> None:
        import factory.core.cc_backend_executor as executor
        import factory.core.delegate_tool as delegate_tool

        captured: dict = {}

        def fake_task(**kwargs):
            captured.update(kwargs)
            return {
                "status": "ok",
                "output_text": "done",
                "tool_calls_count": 0,
                "usage": {},
            }

        real = executor.execute_cc_backend_task
        executor.execute_cc_backend_task = fake_task
        self.addCleanup(lambda: setattr(executor, "execute_cc_backend_task", real))

        tmp = tempfile.mkdtemp(prefix="guard_delegate_")
        try:
            delegate_tool.delegate_task(
                {"goal": "noop"},
                workdir=tmp,
                filesystem_mode="workspace",
                guard_mode="off",
                base_url="http://127.0.0.1:9",
                model="test-model",
            )
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(captured.get("guard_mode"), "off")


if __name__ == "__main__":
    unittest.main(verbosity=2)
