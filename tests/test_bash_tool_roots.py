#!/usr/bin/env python3
"""uv directories needed to reinstall the agent's own tool package.

`uv tool install --force --reinstall .` writes to:
  * uv cache (`UV_CACHE_DIR`, default `~/.cache/uv`): locks, temporary files,
    extracted distributions/wheels and builds;
  * tools (`UV_TOOL_DIR`, default `~/.local/share/uv/tools`): `.lock` and the
    tool package's environment;
  * command shims (`UV_TOOL_BIN_DIR`, default `~/.local/bin`): entry points such
    as `igorek-factory`/`factory-acp` are recreated using unlink+symlink.

Include these directories in the guard/bwrap write roots without making the
rest of `$HOME` writable.
"""

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

_FAKE_BWRAP = "/usr/bin/bwrap"
_WHICH = "factory.core.bash_scope_guard.shutil.which"


class ToolReinstallRootsTests(unittest.TestCase):
    """Path resolution for `uv`: `env` → `XDG` → default home directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_tool_roots_")
        self.home = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_default_paths_follow_home(self) -> None:
        roots = guard.tool_reinstall_roots({"HOME": self.home})
        self.assertEqual(
            roots,
            [
                os.path.join(self.home, ".cache", "uv"),
                os.path.join(self.home, ".local", "share", "uv", "tools"),
                os.path.join(self.home, ".local", "bin"),
            ],
        )

    def test_xdg_dirs_are_honoured(self) -> None:
        cache_home = os.path.join(self.home, "xdg-cache")
        data_home = os.path.join(self.home, "xdg-data")
        roots = guard.tool_reinstall_roots(
            {
                "HOME": self.home,
                "XDG_CACHE_HOME": cache_home,
                "XDG_DATA_HOME": data_home,
            }
        )
        self.assertEqual(
            roots,
            [
                os.path.join(cache_home, "uv"),
                os.path.join(data_home, "uv", "tools"),
                os.path.join(self.home, ".local", "bin"),
            ],
        )

    def test_explicit_uv_env_wins(self) -> None:
        cache = os.path.join(self.home, "uv-cache")
        tools = os.path.join(self.home, "uv-tools")
        bin_dir = os.path.join(self.home, "uv-bin")
        roots = guard.tool_reinstall_roots(
            {
                "HOME": self.home,
                "UV_CACHE_DIR": cache,
                "UV_TOOL_DIR": tools,
                "UV_TOOL_BIN_DIR": bin_dir,
            }
        )
        self.assertEqual(roots, [cache, tools, bin_dir])

    def test_env_override_replaces_defaults(self) -> None:
        custom = os.path.join(self.home, "custom-tools")
        roots = guard.tool_reinstall_roots(
            {"HOME": self.home, guard.TOOL_ROOTS_ENV: custom}
        )
        self.assertEqual(roots, [custom])

    def test_env_off_disables_tool_roots(self) -> None:
        for value in ("off", "OFF", "0", "false", "no", "disabled"):
            with self.subTest(value=value):
                roots = guard.tool_reinstall_roots(
                    {"HOME": self.home, guard.TOOL_ROOTS_ENV: value}
                )
                self.assertEqual(roots, [])

    def test_empty_env_value_keeps_defaults(self) -> None:
        roots = guard.tool_reinstall_roots(
            {"HOME": self.home, guard.TOOL_ROOTS_ENV: "   "}
        )
        self.assertEqual(len(roots), 3)


class AllowedWriteRootsTests(unittest.TestCase):
    """Allowed roots: `workdir` + `uv` tool directories, excluding the rest of `$HOME`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_allowed_roots_")
        self.home = self._tmp.name
        self.workdir = os.path.join(self.home, "repo")
        os.makedirs(self.workdir, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tool_roots_are_allowed(self) -> None:
        roots = guard.allowed_write_roots(self.workdir, env={"HOME": self.home})
        self.assertIn(os.path.realpath(self.workdir), roots)
        for expected in guard.tool_reinstall_roots({"HOME": self.home}):
            self.assertIn(os.path.realpath(expected), roots)

    def test_other_home_paths_are_not_allowed(self) -> None:
        roots = guard.allowed_write_roots(self.workdir, env={"HOME": self.home})
        for forbidden in (
            os.path.join(self.home, ".ssh"),
            os.path.join(self.home, ".bashrc"),
            os.path.join(self.home, ".config"),
            os.path.join(self.home, "Documents"),
            os.path.join(self.home, ".local", "share", "uv", "python"),
            os.path.join(self.home, ".local", "share", "uv", "tools-other"),
        ):
            with self.subTest(path=forbidden):
                self.assertNotIn(os.path.realpath(forbidden), roots)

    def test_env_off_keeps_only_workdir(self) -> None:
        env = {"HOME": self.home, guard.TOOL_ROOTS_ENV: "off"}
        roots = guard.allowed_write_roots(self.workdir, env=env)
        self.assertEqual(roots, [os.path.realpath(self.workdir)])

    def test_guest_tmpfs_is_allowed_only_in_guest_mode(self) -> None:
        host_roots = guard.allowed_write_roots(self.workdir, env={"HOME": self.home})
        self.assertNotIn(os.path.realpath("/tmp"), host_roots)

        guest_roots = guard.allowed_write_roots(
            self.workdir,
            env={"HOME": self.home, "IGOREK_ISOLATION_GUEST": "1"},
        )
        self.assertIn(os.path.realpath("/tmp"), guest_roots)


class ToolRootsArgvTests(unittest.TestCase):
    """In `bwrap-argv`, `uv` tool directories are bind-mounted with `rw` permissions and do not inherit the entire `$HOME`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_tool_argv_")
        self.home = self._tmp.name
        self.workdir = os.path.join(self.home, "repo")
        os.makedirs(self.workdir, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _argv(self, env):
        with patch(_WHICH, return_value=_FAKE_BWRAP):
            return guard.wrap_with_sandbox_argv(
                "uv tool install --force --reinstall .",
                workdir=self.workdir,
                env=env,
            )

    def _bind_pairs(self, argv):
        pairs = []
        for index, token in enumerate(argv):
            if token == "--bind":
                pairs.append((argv[index + 1], argv[index + 2]))
        return pairs

    def test_tool_roots_are_bound_and_created(self) -> None:
        argv = self._argv({"HOME": self.home})
        pairs = dict(self._bind_pairs(argv))
        for root in guard.tool_reinstall_roots({"HOME": self.home}):
            self.assertIn(root, pairs)
            self.assertTrue(os.path.isdir(root), root)
            self.assertEqual(pairs[root], root)

    def test_other_home_paths_are_not_bound(self) -> None:
        argv = self._argv({"HOME": self.home})
        bound = [pair[0] for pair in self._bind_pairs(argv)]
        for forbidden in (
            os.path.join(self.home, ".ssh"),
            os.path.join(self.home, ".local", "share", "uv", "python"),
        ):
            with self.subTest(path=forbidden):
                self.assertNotIn(forbidden, bound)

    def test_env_off_drops_tool_roots_from_argv(self) -> None:
        argv = self._argv({"HOME": self.home, guard.TOOL_ROOTS_ENV: "off"})
        bound = [pair[0] for pair in self._bind_pairs(argv)]
        for root in guard.tool_reinstall_roots({"HOME": self.home}):
            self.assertNotIn(root, bound)
        self.assertIn(os.path.realpath(self.workdir), bound)


class ToolRootsGuardTests(unittest.TestCase):
    """Static guard: Writing to `uv` directories is allowed; the rest of `$HOME` is not."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_tool_guard_")
        self.home = self._tmp.name
        self.workdir = os.path.join(self.home, "repo")
        os.makedirs(self.workdir, exist_ok=True)
        self._env_patch = patch.dict(
            os.environ, {"HOME": self.home}, clear=False
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        for name in ("XDG_CACHE_HOME", "XDG_DATA_HOME", "UV_CACHE_DIR", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR"):
            previous = os.environ.pop(name, None)
            if previous is not None:
                self.addCleanup(os.environ.__setitem__, name, previous)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reinstall_command_is_allowed(self) -> None:
        verdict, _text = guard.enforce_bash_scope(
            "uv tool install --force --reinstall .",
            guard_mode="on",
            workdir=self.workdir,
        )
        self.assertEqual(verdict, guard.VERDICT_ALLOW)

    def test_write_into_uv_cache_is_allowed(self) -> None:
        cache = os.path.join(self.home, ".cache", "uv")
        verdict, _text = guard.enforce_bash_scope(
            f"mkdir -p {cache}/wheels-v6",
            guard_mode="on",
            workdir=self.workdir,
        )
        self.assertEqual(verdict, guard.VERDICT_ALLOW)

    def test_write_into_home_notes_is_denied(self) -> None:
        verdict, text = guard.enforce_bash_scope(
            f"mkdir -p {self.home}/notes",
            guard_mode="on",
            workdir=self.workdir,
        )
        self.assertEqual(verdict, guard.VERDICT_DENY)
        self.assertIn("bash_write_outside_workdir", text)

    def test_write_into_ssh_is_denied(self) -> None:
        verdict, _text = guard.enforce_bash_scope(
            f"touch {self.home}/.ssh/authorized_keys",
            guard_mode="on",
            workdir=self.workdir,
        )
        self.assertEqual(verdict, guard.VERDICT_DENY)

    def test_write_into_uv_python_dir_is_denied(self) -> None:
        verdict, _text = guard.enforce_bash_scope(
            f"touch {self.home}/.local/share/uv/python/evil",
            guard_mode="on",
            workdir=self.workdir,
        )
        self.assertEqual(verdict, guard.VERDICT_DENY)

    def test_write_into_tmp_is_denied_on_host(self) -> None:
        verdict, _text = guard.enforce_bash_scope(
            "mkdir -p /tmp/igorek-host-bash-scope-canary",
            guard_mode="on",
            workdir=self.workdir,
        )
        self.assertEqual(verdict, guard.VERDICT_DENY)

    def test_write_into_guest_tmpfs_is_allowed(self) -> None:
        with patch.dict(os.environ, {"IGOREK_ISOLATION_GUEST": "1"}, clear=False):
            verdict, _text = guard.enforce_bash_scope(
                "mkdir -p /tmp/igorek-guest-bash-scope-canary",
                guard_mode="on",
                workdir=self.workdir,
            )
        self.assertEqual(verdict, guard.VERDICT_ALLOW)


if __name__ == "__main__":
    unittest.main(verbosity=2)
