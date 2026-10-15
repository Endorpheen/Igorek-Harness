#!/usr/bin/env python3
"""// Behavior of the real bwrap sandbox (runs only when bwrap is active)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.bash_scope_guard import (  # noqa: E402
    probe_bwrap_capability,
    wrap_with_sandbox_argv,
)


def _sandbox_available() -> bool:
    if not shutil.which("bwrap"):
        return False
    return probe_bwrap_capability() == "ok"


@unittest.skipUnless(_sandbox_available(), "bwrap sandbox is not available")
class BashSandboxRealTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_sandbox_real_")
        self.workdir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, command: str) -> subprocess.CompletedProcess:
        argv = wrap_with_sandbox_argv(command, workdir=self.workdir, env={})
        self.assertIsNotNone(argv)
        return subprocess.run(
            argv, shell=False, capture_output=True, text=True, timeout=30
        )

    def test_write_inside_workdir_succeeds(self) -> None:
        proc = self._run("echo inside > inside.txt")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            Path(self.workdir, "inside.txt").read_text(encoding="utf-8").strip(),
            "inside",
        )

    def test_host_home_file_is_unchanged(self) -> None:
        target = Path(os.path.expanduser("~")) / f".igorek_sandbox_{os.getpid()}"
        with target.open("x") as fixture:
            fixture.write("host fixture\n")
        self.addCleanup(target.unlink, missing_ok=True)
        self._run(f"echo x > {target}")
        # A home below /tmp may be shadowed by private tmpfs. Check host bytes,
        # rather than requiring a failure for a write into that disposable copy.
        self.assertEqual(target.read_text(), "host fixture\n")

    def test_write_to_etc_fails(self) -> None:
        target = f"/etc/igorek_sandbox_{os.getpid()}"
        proc = self._run(f"echo x > {target}")
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(target))

    def test_tmp_write_succeeds(self) -> None:
        proc = self._run("echo ok > /tmp/igorek_sandbox_probe.txt && cat /tmp/igorek_sandbox_probe.txt")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "ok")

    def test_getent_localhost_works(self) -> None:
        proc = self._run("getent hosts localhost")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip())

    def test_pid_namespace_is_isolated(self) -> None:
        proc = self._run("ls -d /proc/[0-9]* | wc -l")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        count = int(proc.stdout.strip() or 0)
        self.assertLess(count, 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
