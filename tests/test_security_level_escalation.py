#!/usr/bin/env python3
"""Negative tests for access-level self-escalation boundaries.

Verify a model cannot weaken access controls:
(a) Ignore `task_metadata={'security_level': 'full'}`; resolve the level only
    through server-owned `security_settings`.
(b) Ignore `filesystem_mode`/`guard_mode` inside tool arguments; use explicit
    `execute_tool` runtime parameters.
(c) Delegate exactly the parent's modes, including when child args forge weaker
    `filesystem_mode`/`guard_mode` values.
(d) `security_settings.resolve_security_level` has no metadata parameter and
    reads only the `settings` table.
(e) A session A override is not returned for session B; a forged
    `task_metadata['session']` does not grant another session's override.

Exercise real modules and a temporary database, not assertions that merely
repeat implementation constants. Run in the locked development environment:
    uv run --no-sync python -m pytest tests/test_security_level_escalation.py -v
"""

from __future__ import annotations

import importlib.util
import inspect
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WORKDIR = os.environ.get("IGOREK_TEST_WORKDIR", "/home/example/AI/workspace")
GUARD_ENV = "IGOREK_BASH_SCOPE_GUARD"

from factory.core import security_settings as ss  # noqa: E402
from factory.core.runtime import get_db_path  # noqa: E402
from factory.core.schema import initialize_database  # noqa: E402
from factory.core.cc_backend_tools import execute_tool  # noqa: E402

_STANDARD_RUNTIME = {
    "filesystem_mode": "workspace",
    "guard_mode": "on",
    "security_mode": "observe",
}
_FULL_RUNTIME = {
    "filesystem_mode": "full-dangerous",
    "guard_mode": "off",
    "security_mode": "disabled",
}


def _install_minimal_pydantic_shim() -> None:
    """Allows importing `factory.core.worker` without installing Pydantic.

    Heavy import of the worker pulls in the Vault layer, which requires Pydantic. Tests
    for Pydantic boundary conditions are unnecessary; the shim is inserted ONLY if
    real Pydantic is not present and does not affect the level resolver (security_settings/levels).
    """
    import types

    shim = types.ModuleType("pydantic")

    class BaseModel:  # Minimum required for vault.models at import time.
        def __init__(self, **kwargs: object) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def Field(*_args: object, **_kwargs: object) -> None:
        return None

    shim.BaseModel = BaseModel  # type: ignore[attr-defined]
    shim.Field = Field  # type: ignore[attr-defined]
    sys.modules["pydantic"] = shim


def _import_worker():
    """Imports the worker, inserting a Pydantic shim only when it is absent.

    The worker wraps the Pydantic-dependent Vault layer import in a `try/except` block,
    so the original cause (`No module named 'pydantic'`) may be hidden in the context chain.
    We check for the package presence via `find_spec`, not text.
    """
    try:
        from factory.core import worker  # noqa: PLC0415

        return worker
    except (ImportError, ModuleNotFoundError):
        if importlib.util.find_spec("pydantic") is not None:
            raise
    _install_minimal_pydantic_shim()
    from factory.core import worker  # noqa: PLC0415

    return worker


try:
    _worker = _import_worker()
except Exception:  # pragma: no cover — environment without worker dependencies.
    _worker = None


def _normalize_guard(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"off", "0", "false", "no", "disabled"}:
        return "off"
    if text in {"warn", "audit"}:
        return "warn"
    if text in {"on", "1", "true", "yes", "enabled"}:
        return "on"
    return None


def _effective_env() -> str:
    raw = (os.environ.get(GUARD_ENV) or "").strip().lower()
    if raw in {"off", "0", "false", "no", "disabled"}:
        return "off"
    if raw in {"warn", "audit"}:
        return "warn"
    return "on"


def _install_guard_capture_stub() -> list[dict]:
    """Replaces `enforce_bash_scope` by recording the effective guard arguments.

    Actual commands are not executed: `on` → `deny` with the `POLICY_DENIED` marker.
    """
    import factory.core.bash_scope_guard as bash_scope_guard

    real = bash_scope_guard.enforce_bash_scope
    calls: list[dict] = []

    def stub(
        command,
        guard_mode=None,
        *,
        workdir,
        filesystem_mode="workspace",
        extra_roots=None,
    ):
        explicit = _normalize_guard(guard_mode)
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

    bash_scope_guard.enforce_bash_scope = stub
    return calls


class _GuardEnvMixin(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_guard = os.environ.get(GUARD_ENV)

    def tearDown(self) -> None:
        if self._prev_guard is None:
            os.environ.pop(GUARD_ENV, None)
        else:
            os.environ[GUARD_ENV] = self._prev_guard


class SecurityLevelEscalationTest(_GuardEnvMixin):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()
        super().tearDown()

    def _with_guard_stub(self) -> list[dict]:
        import factory.core.bash_scope_guard as bash_scope_guard

        real = bash_scope_guard.enforce_bash_scope
        calls = _install_guard_capture_stub()
        self.addCleanup(lambda: setattr(bash_scope_guard, "enforce_bash_scope", real))
        return calls

    # -- (a) task_metadata['security_level'] is ignored --
    @unittest.skipIf(_worker is None, "worker импортируется только с зависимостями")
    def test_a_metadata_security_level_is_ignored(self) -> None:
        ss.set_security_level("standard")  # Actual global standard

        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _worker._resolve_level_runtime(
                {},
                {"security_level": "full", "session": "srv-1"},
            )

        # The manually set 'full' mode had no effect: runtime remained standard.
        self.assertEqual(result, _STANDARD_RUNTIME)
        self.assertNotEqual(result["filesystem_mode"], "full-dangerous")
        self.assertEqual(result["guard_mode"], "on")
        # It did not create either a global- or session-level override.
        self.assertEqual(ss.resolve_security_level()["level"], "standard")
        self.assertEqual(ss.resolve_security_level(session_id="srv-1")["level"], "standard")
        # The fact of being ignored is logged in the trace.
        self.assertIn("ignoring task_metadata.security_level='full'", buf.getvalue())

    @unittest.skipIf(_worker is None, "worker импортируется только с зависимостями")
    def test_a_metadata_full_without_global_level_stays_baseline(self) -> None:
        # Even without an explicit global-level metadata setting, 'full' mode is not enabled.
        with redirect_stdout(io.StringIO()):
            result = _worker._resolve_level_runtime({}, {"security_level": "full"})
        self.assertEqual(result, _STANDARD_RUNTIME)

    # -- (b) filesystem_mode/guard_mode arguments within tool arguments are ignored --
    def test_b_tool_input_modes_are_not_consulted(self) -> None:
        os.environ[GUARD_ENV] = "on"
        calls = self._with_guard_stub()

        result = execute_tool(
            "Bash",
            {
                "command": "echo hi",
                "filesystem_mode": "full-dangerous",
                "guard_mode": "off",
            },
            WORKDIR,
        )

        # Tool arguments do not set guard or filesystem: guard=None (environment), fs=default.
        self.assertIsNone(calls[-1]["guard_mode"])
        self.assertEqual(calls[-1]["effective"], "on")
        self.assertEqual(calls[-1]["filesystem_mode"], "workspace")
        self.assertIn("POLICY_DENIED", result)

    def test_b_explicit_runtime_wins_over_forged_tool_input(self) -> None:
        os.environ[GUARD_ENV] = "off"
        calls = self._with_guard_stub()

        execute_tool(
            "Bash",
            {
                "command": "echo hi",
                "filesystem_mode": "full-dangerous",
                "guard_mode": "off",
            },
            WORKDIR,
            filesystem_mode="workspace",
            guard_mode="on",
        )

        # Explicit runtime arguments take precedence over forged values; forged values are ignored.
        self.assertEqual(calls[-1]["guard_mode"], "on")
        self.assertEqual(calls[-1]["filesystem_mode"], "workspace")

    # --------------------------------
    # (c) Inheritance delegates parent modes (non-weaker)
    # --
    def _capture_delegate(self) -> dict:
        import factory.core.cc_backend_executor as executor

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
        return captured

    def _run_delegate(self, args: dict, filesystem_mode: str, guard_mode: str | None) -> dict:
        import factory.core.delegate_tool as delegate_tool

        captured = self._capture_delegate()
        tmp = tempfile.mkdtemp(prefix="security_delegate_")
        try:
            delegate_tool.delegate_task(
                args,
                workdir=tmp,
                filesystem_mode=filesystem_mode,
                guard_mode=guard_mode,
                base_url="http://127.0.0.1:9",
                model="test-model",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return captured

    def test_c_forged_child_args_do_not_weaken_parent(self) -> None:
        captured = self._run_delegate(
            {
                "goal": "noop",
                "filesystem_mode": "full-dangerous",
                "guard_mode": "off",
            },
            filesystem_mode="workspace",
            guard_mode="on",
        )
        self.assertEqual(captured.get("filesystem_mode"), "workspace")
        self.assertEqual(captured.get("guard_mode"), "on")

    def test_c_parent_full_is_inherited_exactly(self) -> None:
        captured = self._run_delegate(
            {"goal": "noop"},
            filesystem_mode="full-dangerous",
            guard_mode="off",
        )
        self.assertEqual(captured.get("filesystem_mode"), "full-dangerous")
        self.assertEqual(captured.get("guard_mode"), "off")

    @unittest.skipIf(_worker is None, "worker импортируется только с зависимостями")
    def test_c_child_inherits_resolved_parent_level(self) -> None:
        ss.set_security_level("trusted", ttl_s=60)  # global trusted -> workspace/warn
        parent = _worker._resolve_level_runtime({}, None)
        self.assertEqual(parent, {"filesystem_mode": "workspace", "guard_mode": "warn", "security_mode": "observe"})

        captured = self._run_delegate(
            {"goal": "noop", "guard_mode": "off", "filesystem_mode": "full-dangerous"},
            filesystem_mode=parent["filesystem_mode"],
            guard_mode=parent["guard_mode"],
        )
        self.assertEqual(captured.get("filesystem_mode"), "workspace")
        self.assertEqual(captured.get("guard_mode"), "warn")

    # --------------------------------
    # (d) resolve_security_level does not accept/read metadata
    # --
    def test_d_resolve_security_level_has_no_metadata_parameter(self) -> None:
        signature = inspect.signature(ss.resolve_security_level)
        names = set(signature.parameters)
        self.assertEqual(names, {"session_id", "when_no_level"})
        self.assertNotIn("metadata", names)
        self.assertNotIn("task_metadata", names)
        for parameter in signature.parameters.values():
            self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

        # Attempting to pass a metadata-like dictionary is rejected by the API itself.
        with self.assertRaises(TypeError):
            ss.resolve_security_level(metadata={"security_level": "full"})
        with self.assertRaises(TypeError):
            ss.resolve_security_level(task_metadata={"security_level": "full"})

        # The resolver body does not mention metadata; only the settings table is read.
        self.assertNotIn("metadata", inspect.getsource(ss.resolve_security_level))

    def test_d_resolver_reads_only_settings_table(self) -> None:
        ss.set_security_level("strict")
        result = ss.resolve_security_level()
        self.assertEqual(result["level"], "strict")
        self.assertEqual(result["source"], "global")

    # --------------------------------
    # (e) forged session does not open a foreign session override
    # --
    def test_e_session_override_is_key_exact(self) -> None:
        ss.set_security_level("standard")
        ss.set_security_level("full", session_id="session-A", ttl_s=60)

        other = ss.resolve_security_level(session_id="session-B")
        self.assertEqual(other["level"], "standard")
        self.assertNotEqual(other["source"], "session")
        self.assertIsNone(ss.get_session_level("session-B"))

        owner = ss.resolve_security_level(session_id="session-A")
        self.assertEqual(owner["level"], "full")
        self.assertEqual(owner["source"], "session")

    @unittest.skipIf(_worker is None, "worker импортируется только с зависимостями")
    def test_e_worker_resolves_session_exactly(self) -> None:
        ss.set_security_level("standard")
        ss.set_security_level("full", session_id="session-A", ttl_s=60)

        foreign = _worker._resolve_level_runtime({}, {"session": "session-B"})
        self.assertEqual(foreign, _STANDARD_RUNTIME)

        own = _worker._resolve_level_runtime({}, {"session": "session-A"})
        self.assertEqual(own, _FULL_RUNTIME)


if __name__ == "__main__":
    unittest.main(verbosity=2)
