#!/usr/bin/env python3
"""Bash write scope guard tests for the IgorekAgentFactory repository.

Applied via script `fixes/apply_bash_scope_hardening.py` in `tests/test_bash_scope_guard.py`.

Tests both the guard itself and its integration with `_tool_bash`.

Before applying the patch, the test fails as expected (module is not yet in the repository).
"""

from __future__ import annotations

import os
import sys
import unittest
import tempfile
from pathlib import Path

REPO_ROOT = Path(os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WORKDIR = os.environ.get("IGOREK_TEST_WORKDIR", "/home/example/AI/workspace")
FOREIGN = "/home/example/AI/IgorekAgentFactory"
GUARD_ENV = "IGOREK_BASH_SCOPE_GUARD"


class _GuardEnvOnMixin:
    """Explicitly set to `on` to ensure deny cases do not rely on trial defaults for warnings or flipping."""

    def setUp(self) -> None:  # noqa: D401 - unittest hook
        self._guard_env_prev = os.environ.get(GUARD_ENV)
        os.environ[GUARD_ENV] = "on"

    def tearDown(self) -> None:
        if self._guard_env_prev is None:
            os.environ.pop(GUARD_ENV, None)
        else:
            os.environ[GUARD_ENV] = self._guard_env_prev


from factory.core.bash_scope_guard import (  # noqa: E402
    bash_requires_approval,
    enforce_bash_scope,
    validate_bash_scope,
)

DENY_CASES = (
    f"cp /tmp/evil.py {FOREIGN}/factory/core/evil.py",
    f"mv /tmp/a.txt {FOREIGN}/factory/core/b.txt",
    f"echo x | tee {FOREIGN}/factory/core/evil.py",
    "python3 -c \"open('" + FOREIGN + "/factory/core/evil.py','w').write('x')\"",
    f"python3 /tmp/evil_installer.py --target {FOREIGN}",
    f"bash -c 'cp /tmp/evil.py {FOREIGN}/factory/core/evil.py'",
    f"install -m 644 /tmp/evil.py {FOREIGN}/factory/core/evil.py",
    f"sed -i 's/a/b/' {FOREIGN}/factory/core/cc_backend_executor.py",
    f"find /tmp -name '*.py' -exec cp {{}} {FOREIGN}/factory/core/ \\;",
    f"echo x > {FOREIGN}/factory/core/evil.py",
    f"echo x >> {FOREIGN}/notes.txt",
    "cp a.txt ../../AI/IgorekAgentFactory/factory/x.py",
    # Fix F2: fd redirects should not override the target destination
    f"cp /tmp/nonexist {FOREIGN}/dst 2>/dev/null",
    f"cp /tmp/nonexist {FOREIGN}/dst 2>&1",
    f"echo x &>> {FOREIGN}/f",
    # Fix F2: `cp -t` / `--target-directory[=]` (attached forms)
    f"cp --target-directory={FOREIGN} {WORKDIR}/src",
    f"cp -t{FOREIGN} {WORKDIR}/src",
    # Fix F2: bare-name operands for sed/perl should not use tracked working directory
    f"cd {FOREIGN} && sed -i s/a/b/ file",
    f"cd {FOREIGN} && perl -pi -e 's/a/b/' file",
    # Fix F3: `>&` — writing to a file with non-numeric target
    f"echo x >& {FOREIGN}/f",
    f"echo x >&{FOREIGN}/f",
    f"cp /tmp/x {WORKDIR}/z >& {FOREIGN}/out",
    f"exec 9>& {FOREIGN}/out",
    f"sed -i -e 's/a/b/' {FOREIGN}/file < {WORKDIR}/input",
)

ALLOW_CASES = (
    f"cp /tmp/src.py {WORKDIR}/fixes/tmp_copy.py",
    f"echo hi > {WORKDIR}/fixes/tmp.txt",
    "python3 verify.py --dry-run 2>/dev/null",
    f"git -C {WORKDIR} status --short",
    f"rg --files {WORKDIR} | head -20",
    f"mkdir -p {WORKDIR}/fixes/sub",
    f"sed -i 's/a/b/' {WORKDIR}/fixes/x.py",
    "python3 -c \"print(1+1)\"",
    f"cp -t{WORKDIR}/fixes /tmp/src.py",
    f"cd {WORKDIR}/fixes && sed -i s/a/b/ file",
    # Fix F3: `>&` — fd file descriptor duplication/closing and read redirection in sed
    "echo x >&1",
    "echo x 2>&1",
    "echo x 2>&-",
    f"sed -i -e 's/a/b/' file < {FOREIGN}/input",
    f"sed -i -e 's/a/b/' {WORKDIR}/file < {WORKDIR}/input",
)


class GuardUnitTests(_GuardEnvOnMixin, unittest.TestCase):
    def test_deny_cases(self) -> None:
        for command in DENY_CASES:
            with self.subTest(command=command):
                self.assertIsNotNone(validate_bash_scope(command, workdir=WORKDIR))

    def test_allow_cases(self) -> None:
        for command in ALLOW_CASES:
            with self.subTest(command=command):
                self.assertIsNone(validate_bash_scope(command, workdir=WORKDIR))

    def test_verdict_deny_text(self) -> None:
        verdict, text = enforce_bash_scope(f"cp a {FOREIGN}/b", workdir=WORKDIR)
        self.assertEqual(verdict, "deny")
        self.assertIn("POLICY_DENIED", text)

    def test_full_dangerous_mode_bypasses(self) -> None:
        verdict, _ = enforce_bash_scope(
            f"cp a {FOREIGN}/b", workdir=WORKDIR, filesystem_mode="full-dangerous"
        )
        self.assertEqual(verdict, "allow")

    def test_approval_helper(self) -> None:
        self.assertTrue(bash_requires_approval(f"cp a {WORKDIR}/b"))
        self.assertFalse(bash_requires_approval("git status --short"))


class BashToolIntegrationTests(_GuardEnvOnMixin, unittest.TestCase):
    """The patch is actually applied to `_tool_bash`."""

    def setUp(self) -> None:
        super().setUp()
        from factory.core import cc_backend_tools

        self.tools = cc_backend_tools
        temporary = tempfile.TemporaryDirectory(prefix="igorek-bash-scope-")
        self.addCleanup(temporary.cleanup)
        self.workdir = temporary.name
        (Path(self.workdir) / "fixes").mkdir()

    def test_write_outside_workdir_blocked(self) -> None:
        result = self.tools._tool_bash(
            {"command": f"cp /tmp/x {FOREIGN}/BASH_GUARD_TEST.txt"},
            workdir=self.workdir,
            filesystem_mode="workspace",
        )
        self.assertIn("POLICY_DENIED", result)
        self.assertFalse(os.path.exists(f"{FOREIGN}/BASH_GUARD_TEST.txt"))

    def test_benign_command_still_works(self) -> None:
        result = self.tools._tool_bash(
            {"command": "echo guard-ok"},
            workdir=self.workdir,
            filesystem_mode="workspace",
        )
        self.assertIn("guard-ok", result)

    def test_write_inside_workdir_works(self) -> None:
        target = Path(self.workdir) / "fixes" / "bash_guard_tmp.txt"
        result = self.tools._tool_bash(
            {"command": f"echo inside > {target}"},
            workdir=self.workdir,
            filesystem_mode="workspace",
        )
        self.assertNotIn("POLICY_DENIED", result)
        self.assertTrue(target.exists())
        target.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
