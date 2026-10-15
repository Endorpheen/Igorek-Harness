#!/usr/bin/env python3
"""Integration of the `bwrap-sandbox` with model-issued Bash (`_tool_bash`).

Tests:
- `argv` + `shell=False` when enabled+available; fail-closed mode
- `SANDBOX_UNAVAILABLE` without spawn when enabled+unavailable; explicit disable
- (`user_disabled`/`env_disabled`) preserves legacy `shell=True` behavior; `observe`
  constructs `argv` but runs without wrapping; `full-dangerous` mode bypasses sandbox;
  real filesystem isolation in `tmp-workdir` via the actual `_tool_bash`.

Execution from root of `/home/example/AI/IgorekAgentFactory`:
    .venv/bin/python3 -m unittest tests.test_bash_tool_sandbox -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import factory.core.bash_scope_guard as bash_scope_guard  # noqa: E402
from factory.core import cc_backend_tools  # noqa: E402

_SANDBOX_STATUS = "factory.core.bash_sandbox_settings.sandbox_status"
_PROBE = "factory.core.bash_sandbox_settings.probe_bwrap_capability"
_MODE = "factory.core.bash_sandbox_settings.resolve_sandbox_mode"
_ENABLED = "factory.core.bash_sandbox_settings.sandbox_enabled"
_RUN = "factory.core.cc_backend_tools.subprocess.run"
_GUARD_ENV = "IGOREK_BASH_SCOPE_GUARD"


def _status(
    *, enabled: bool = True, available: bool = True, reason: str = ""
) -> dict[str, object]:
    return {
        "enabled": enabled,
        "active": bool(enabled and available),
        "available": available,
        "reason": reason,
    }


@contextmanager
def _probe(probe: str):
    """Run the REAL `sandbox_status` with a specified probe and database configuration."""
    with (
        patch(_PROBE, return_value=probe),
        patch(_MODE, return_value="on"),
        patch(_ENABLED, return_value=True),
    ):
        yield


class _RunSpy:
    """Mocking `subprocess.run`: logs the command call and returns a predefined result."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self._result = (returncode, stdout, stderr)

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        returncode, stdout, stderr = self._result
        return subprocess.CompletedProcess(
            args=args[0] if args else "",
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @property
    def last(self) -> tuple[tuple, dict]:
        return self.calls[-1]


def _sandbox_available() -> bool:
    if not shutil.which("bwrap"):
        return False
    return bash_scope_guard.probe_bwrap_capability() == "ok"


class _WorkdirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_bash_tool_sandbox_")
        self.workdir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()


class SandboxDecisionTests(_WorkdirCase):
    @unittest.skipUnless(shutil.which("bwrap"), "bwrap binary not found")
    def test_enabled_available_runs_argv_shell_false(self) -> None:
        spy = _RunSpy(stdout="hi\n")
        with _probe("ok"), patch(_RUN, spy):
            result = cc_backend_tools._tool_bash(
                {"command": "echo hi"}, workdir=self.workdir
            )

        self.assertEqual(result, "hi\n")
        self.assertEqual(len(spy.calls), 1)
        args, kwargs = spy.last
        argv = args[0]
        self.assertIsInstance(argv, list)
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["cwd"], self.workdir)
        self.assertNotIn("env", kwargs)
        self.assertEqual(argv[0], shutil.which("bwrap"))
        self.assertIn("--", argv)
        self.assertEqual(argv[argv.index("--") + 1], "/bin/bash")

    def test_enabled_unavailable_fail_closed_no_spawn(self) -> None:
        spy = _RunSpy()
        with _probe("bwrap_missing"), patch(_RUN, spy):
            result = cc_backend_tools._tool_bash(
                {"command": "echo hi"}, workdir=self.workdir
            )

        self.assertTrue(result.startswith("SANDBOX_UNAVAILABLE"), result)
        self.assertIn("bwrap_missing", result)
        self.assertEqual(spy.calls, [])

    def test_namespace_blocked_fail_closed_no_spawn(self) -> None:
        spy = _RunSpy()
        with _probe("namespace_blocked"), patch(_RUN, spy):
            result = cc_backend_tools._tool_bash(
                {"command": "echo hi"}, workdir=self.workdir
            )

        self.assertTrue(result.startswith("SANDBOX_UNAVAILABLE"), result)
        self.assertIn("namespace_blocked", result)
        self.assertEqual(spy.calls, [])

    def test_guest_runs_without_nested_bwrap_but_keeps_scope_guard(self) -> None:
        spy = _RunSpy(stdout="guest\n")
        with (
            patch("factory.core.isolation.guest_bwrap.guest_mode_requested", return_value=True),
            patch(_SANDBOX_STATUS, side_effect=AssertionError("guest must skip bwrap probe")),
            patch(
                "factory.core.cc_backend_tools._bash_sandboxed_argv",
                side_effect=AssertionError("guest must not construct bwrap argv"),
            ),
            patch(_RUN, spy),
        ):
            result = cc_backend_tools._tool_bash(
                {"command": "echo hi"}, workdir=self.workdir
            )

        self.assertEqual(result, "guest\n")
        args, kwargs = spy.last
        self.assertEqual(args[0][0], '/usr/bin/setpriv')
        self.assertIn('--no-new-privs', args[0])
        self.assertEqual(args[0][-3:], ['/bin/sh', '-c', 'echo hi'])
        self.assertFalse(kwargs.get('shell', False))
        self.assertEqual(kwargs["cwd"], self.workdir)

    def test_explicit_disable_keeps_legacy_shell_true(self) -> None:
        for reason in ("user_disabled", "env_disabled"):
            with self.subTest(reason=reason):
                spy = _RunSpy(stdout="legacy\n")
                with patch(_SANDBOX_STATUS, return_value=_status(enabled=False, available=True, reason=reason)), patch(
                    _RUN, spy
                ):
                    result = cc_backend_tools._tool_bash(
                        {"command": "echo hi"}, workdir=self.workdir
                    )
                self.assertEqual(result, "legacy\n")
                args, kwargs = spy.last
                self.assertEqual(args[0], "echo hi")
                self.assertIs(kwargs["shell"], True)
                self.assertEqual(kwargs["cwd"], self.workdir)

    def test_observe_builds_argv_but_runs_legacy(self) -> None:
        spy = _RunSpy(stdout="observed\n")
        built: list[str] = []

        def fake_argv(command, *, workdir, extra_roots=None):
            built.append(command)
            return ["/usr/bin/bwrap", "--", "/bin/bash", "-c", command]

        telemetry: list[str] = []

        def fake_telemetry(verdict, command, violation, workdir, mode):
            telemetry.append(str(verdict))

        with (
            patch(
                _SANDBOX_STATUS,
                return_value=_status(enabled=True, available=True, reason="observe"),
            ),
            patch("factory.core.cc_backend_tools._bash_sandboxed_argv", fake_argv),
            patch.object(bash_scope_guard, "_record_telemetry", fake_telemetry),
            patch(_RUN, spy),
        ):
            result = cc_backend_tools._tool_bash(
                {"command": "echo hi"}, workdir=self.workdir
            )

        self.assertEqual(result, "observed\n")
        self.assertEqual(built, ["echo hi"])
        self.assertIn("sandbox_observe", telemetry)
        _args, kwargs = spy.last
        self.assertIs(kwargs["shell"], True)

    @unittest.skipUnless(shutil.which("bwrap"), "bwrap binary not found")
    def test_full_dangerous_mode_still_sandboxed(self) -> None:
        spy = _RunSpy(stdout="full\n")
        with _probe("ok"), patch(_RUN, spy):
            cc_backend_tools._tool_bash(
                {"command": "echo hi"},
                workdir=self.workdir,
                filesystem_mode="full-dangerous",
            )

        args, kwargs = spy.last
        self.assertIsInstance(args[0], list)
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(args[0][0], shutil.which("bwrap"))


class SandboxRunFailureTests(_WorkdirCase):
    def test_bwrap_spawn_raise_is_fail_closed(self) -> None:
        telemetry: list[str] = []

        def fake_telemetry(verdict, command, violation, workdir, mode):
            telemetry.append(str(verdict))

        def boom(*args, **kwargs):
            raise FileNotFoundError("bwrap vanished")

        with (
            _probe("ok"),
            patch("factory.core.cc_backend_tools._bash_sandboxed_argv", return_value=["/usr/bin/bwrap", "--", "/bin/bash", "-c", "echo hi"]),
            patch.object(bash_scope_guard, "_record_telemetry", fake_telemetry),
            patch(_RUN, boom),
        ):
            result = cc_backend_tools._tool_bash(
                {"command": "echo hi"}, workdir=self.workdir
            )

        self.assertTrue(result.startswith("SANDBOX_UNAVAILABLE"), result)
        self.assertIn("sandbox_error", telemetry)

    def test_bwrap_namespace_stderr_is_fail_closed(self) -> None:
        # Custom bwrap diagnostics (`bwrap:` prefix) — sandbox unavailable.
        spy = _RunSpy(
            returncode=1,
            stderr="bwrap: Creating new namespace failed: Operation not permitted\n",
        )
        telemetry: list[str] = []

        def fake_telemetry(verdict, command, violation, workdir, mode):
            telemetry.append(str(verdict))

        with (
            _probe("ok"),
            patch("factory.core.cc_backend_tools._bash_sandboxed_argv", return_value=["/usr/bin/bwrap", "--", "/bin/bash", "-c", "echo hi"]),
            patch.object(bash_scope_guard, "_record_telemetry", fake_telemetry),
            patch(_RUN, spy),
        ):
            result = cc_backend_tools._tool_bash(
                {"command": "echo hi"}, workdir=self.workdir
            )

        self.assertTrue(result.startswith("SANDBOX_UNAVAILABLE"), result)
        self.assertIn("sandbox_error", telemetry)

    def test_child_operation_not_permitted_is_not_fail_closed(self) -> None:
        """Child process `O_N_P` (without `bwrap:` prefix) — its real failure, not a sandbox issue."""
        spy = _RunSpy(
            returncode=1,
            stdout="unshare: unshare failed: Operation not permitted\n",
            stderr="unshare: unshare failed: Operation not permitted\n",
        )
        telemetry: list[str] = []

        def fake_telemetry(verdict, command, violation, workdir, mode):
            telemetry.append(str(verdict))

        with (
            _probe("ok"),
            patch(
                "factory.core.cc_backend_tools._bash_sandboxed_argv",
                return_value=["/usr/bin/bwrap", "--", "/bin/bash", "-c", "unshare -m true"],
            ),
            patch.object(bash_scope_guard, "_record_telemetry", fake_telemetry),
            patch(_RUN, spy),
        ):
            result = cc_backend_tools._tool_bash(
                {"command": "unshare -m true"}, workdir=self.workdir
            )

        self.assertNotIn("SANDBOX_UNAVAILABLE", result)
        self.assertIn("Operation not permitted", result)
        self.assertIn("(exit code: 1)", result)
        self.assertNotIn("sandbox_error", telemetry)


class _GuardOffMixin:
    def setUp(self) -> None:
        super().setUp()
        self._prev_guard = os.environ.get(_GUARD_ENV)
        os.environ[_GUARD_ENV] = "off"

    def tearDown(self) -> None:
        if self._prev_guard is None:
            os.environ.pop(_GUARD_ENV, None)
        else:
            os.environ[_GUARD_ENV] = self._prev_guard
        super().tearDown()


@unittest.skipUnless(_sandbox_available(), "bwrap sandbox is not available")
class BashToolSandboxRealTests(_GuardOffMixin, _WorkdirCase):
    """Actual isolation via the real `_tool_bash` (guard disabled)."""

    def _bash(self, command: str) -> str:
        with _probe("ok"):
            return cc_backend_tools._tool_bash({"command": command}, workdir=self.workdir)

    def test_write_inside_workdir_succeeds(self) -> None:
        result = self._bash("echo inside > inside.txt")
        self.assertNotIn("SANDBOX_UNAVAILABLE", result)
        self.assertNotIn("(exit code:", result)
        target = Path(self.workdir, "inside.txt")
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8").strip(), "inside")

    def test_host_home_file_is_unchanged(self) -> None:
        target = Path(os.path.expanduser("~")) / f".igorek_bwrap_tool_{os.getpid()}"
        with target.open("x") as fixture:
            fixture.write("host fixture\n")
        self.addCleanup(target.unlink, missing_ok=True)
        result = self._bash(f"echo x > {target}")
        self.assertNotIn("SANDBOX_UNAVAILABLE", result)
        self.assertEqual(target.read_text(), "host fixture\n")

    def test_write_to_etc_fails(self) -> None:
        target = f"/etc/igorek_bwrap_tool_{os.getpid()}"
        result = self._bash(f"echo x > {target}")
        self.assertNotIn("SANDBOX_UNAVAILABLE", result)
        self.assertIn("(exit code:", result)
        self.assertFalse(os.path.exists(target))


if __name__ == "__main__":
    unittest.main(verbosity=2)
