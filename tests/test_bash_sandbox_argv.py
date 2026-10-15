#!/usr/bin/env python3
"""// argv contract for bwrap wrapper. Actual bwrap is not required here."""

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

from factory.core import bash_scope_guard as guard  # noqa: E402

_COMMAND = "echo hello > result.txt"
_FAKE_BWRAP = "/usr/bin/bwrap"
_WHICH = "factory.core.bash_scope_guard.shutil.which"


class SandboxArgvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_sandbox_argv_")
        self.workdir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _argv(self, *, workdir=None, env=None, extra_roots=None, command=_COMMAND):
        with patch(_WHICH, return_value=_FAKE_BWRAP):
            return guard.wrap_with_sandbox_argv(
                command,
                workdir=self.workdir if workdir is None else workdir,
                extra_roots=extra_roots,
                env={} if env is None else env,
            )

    def test_no_network_unshare_and_no_clearenv(self) -> None:
        argv = self._argv()
        self.assertNotIn("--unshare-net", argv)
        self.assertNotIn("--clearenv", argv)

    def test_double_dash_precedes_bash(self) -> None:
        argv = self._argv()
        idx = argv.index("--")
        self.assertEqual(argv[idx : idx + 4], ["--", "/bin/bash", "-c", _COMMAND])

    def test_read_only_root_precedes_rw_binds(self) -> None:
        argv = self._argv(
            env={"IGOREK_BASH_WRITE_ROOTS": os.path.join(self.workdir, "extra")}
        )
        ro = argv.index("--ro-bind")
        self.assertEqual(argv[ro : ro + 3], ["--ro-bind", "/", "/"])
        self.assertGreater(argv.index("--bind"), ro)
        self.assertIn(self.workdir, argv)

    def test_chdir_is_realpath(self) -> None:
        link = self.workdir + "_link"
        os.symlink(self.workdir, link)
        try:
            argv = self._argv(workdir=link)
            expected = os.path.realpath(link)
        finally:
            os.unlink(link)
        self.assertEqual(argv[argv.index("--chdir") + 1], expected)

    def test_preflight_creates_missing_app_owned_roots(self) -> None:
        missing = os.path.join(self.workdir, "nested", "write_target")
        vault = os.path.join(self.workdir, "vault")
        self.assertFalse(os.path.exists(missing))
        argv = self._argv(
            env={"IGOREK_BASH_WRITE_ROOTS": missing, "OBSIDIAN_VAULT_PATH": vault}
        )
        self.assertTrue(os.path.isdir(missing))
        self.assertTrue(os.path.isdir(vault))
        self.assertIn(missing, argv)
        self.assertIn(vault, argv)

    def test_sandbox_rw_env_is_bound(self) -> None:
        rw = os.path.join(self.workdir, "rw")
        argv = self._argv(env={"IGOREK_BASH_SANDBOX_RW": rw})
        self.assertTrue(os.path.isdir(rw))
        self.assertIn(rw, argv)

    def test_required_hardening_flags(self) -> None:
        argv = self._argv()
        for flag in (
            "--die-with-parent",
            "--new-session",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-cgroup-try",
        ):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")

    def test_returns_none_without_bwrap(self) -> None:
        with patch(_WHICH, return_value=None):
            self.assertIsNone(
                guard.wrap_with_sandbox_argv(_COMMAND, workdir=self.workdir, env={})
            )
            self.assertIsNone(
                guard.wrap_with_sandbox(_COMMAND, workdir=self.workdir, env={})
            )

    def test_shim_returns_quoted_command(self) -> None:
        with patch(_WHICH, return_value=_FAKE_BWRAP):
            result = guard.wrap_with_sandbox(_COMMAND, workdir=self.workdir, env={})
        self.assertIsInstance(result, str)
        self.assertIn("-- /bin/bash -c", result)

    def test_shim_off_and_observe_return_none(self) -> None:
        with patch(_WHICH, return_value=_FAKE_BWRAP):
            self.assertIsNone(
                guard.wrap_with_sandbox(
                    _COMMAND, workdir=self.workdir, env={"IGOREK_BASH_SANDBOX": "off"}
                )
            )
            self.assertIsNone(
                guard.wrap_with_sandbox(
                    _COMMAND,
                    workdir=self.workdir,
                    env={"IGOREK_BASH_SANDBOX": "observe"},
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
