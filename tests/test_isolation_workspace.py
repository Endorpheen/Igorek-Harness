"""Regression coverage for the single exported isolation workspace.

The guest receives exactly one export: the selected folder's canonical path.
HOME, secret directories, system roots, the data root and links into secret
locations are rejected with distinct reasons. Ordinary external links are
resolved in the guest namespace and do not grant access to host paths.
Each denial test checks its specific reason, rather than any exception.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory.core.isolation.workspace import (
    GUEST_WORKSPACE_PATH,
    ExtraExport,
    IsolationError,
    WorkspacePolicy,
    resolve_workspace_export,
)


class WorkspaceResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.home = self.root / "home"
        self.data_root = self.root / "data"
        self.projects = self.root / "projects"
        for path in (self.home, self.data_root, self.projects):
            path.mkdir(parents=True, exist_ok=True)
        self.policy = WorkspacePolicy(
            home=self.home,
            data_root=self.data_root,
            host_uid=4242,
            guest_agent_uid=1000,
        )

    # -- helpers ---------------------------------------------------------

    def _project(self, name: str = "project") -> Path:
        path = self.projects / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _denied_reason(self, path: str | Path) -> str:
        with self.assertRaises(IsolationError) as ctx:
            resolve_workspace_export(path, policy=self.policy)
        reason = ctx.exception.reason
        self.assertTrue(reason, "IsolationError должен нести машинно-читаемый reason")
        self.assertNotIn(" ", reason, f"reason не должен содержать пробелов: {reason!r}")
        return reason

    def _assert_denied(self, path: str | Path, expected_reason: str) -> None:
        self.assertEqual(self._denied_reason(path), expected_reason)

    # -- allow -----------------------------------------------------------

    def test_normal_project_dir_yields_single_export_equal_to_realpath(self) -> None:
        project = self._project()
        spec = resolve_workspace_export(str(project), policy=self.policy)
        self.assertEqual(spec["host_path"], os.path.realpath(project))
        self.assertEqual(spec["guest_path"], GUEST_WORKSPACE_PATH)
        self.assertEqual(spec["uid_map"], {4242: 1000})
        self.assertEqual(spec["sandbox"], "namespace")
        self.assertFalse(spec["readonly"])
        self.assertEqual(spec["extra_exports"], [], "без opt-in список доп. экспортов пуст")

    def test_symlinked_cwd_is_normalized_to_the_realpath(self) -> None:
        real = self._project("real")
        link = self.projects / "link"
        link.symlink_to(real, target_is_directory=True)
        spec = resolve_workspace_export(link, policy=self.policy)
        self.assertEqual(spec["host_path"], os.path.realpath(real))

    def test_symlink_inside_the_workspace_is_allowed(self) -> None:
        project = self._project()
        (project / "inner").mkdir()
        (project / "inner_link").symlink_to(project / "inner", target_is_directory=True)
        spec = resolve_workspace_export(project, policy=self.policy)
        self.assertEqual(spec["host_path"], os.path.realpath(project))

    # -- deny: sensitive paths ------------------------------------------

    def test_home_itself_is_denied(self) -> None:
        self._assert_denied(self.home, "sensitive_path_denied:~")

    def test_ssh_directory_is_denied(self) -> None:
        ssh = self.home / ".ssh"
        ssh.mkdir()
        self._assert_denied(ssh, "sensitive_path_denied:~/.ssh")

    def test_path_below_ssh_is_denied(self) -> None:
        nested = self.home / ".ssh" / "keys"
        nested.mkdir(parents=True)
        self._assert_denied(nested, "sensitive_path_denied:~/.ssh")

    def test_config_directory_is_denied(self) -> None:
        config = self.home / ".config"
        config.mkdir()
        self._assert_denied(config, "sensitive_path_denied:~/.config")

    def test_igorek_data_directory_is_denied(self) -> None:
        data_home = self.home / ".igorek-agent-factory"
        data_home.mkdir()
        self._assert_denied(data_home, "sensitive_path_denied:~/.igorek-agent-factory")

    def test_root_is_denied(self) -> None:
        self._assert_denied("/", "sensitive_path_denied:/")

    def test_etc_is_denied(self) -> None:
        self._assert_denied("/etc", "sensitive_path_denied:/etc")

    def test_path_below_etc_is_denied(self) -> None:
        self._assert_denied("/etc/default", "sensitive_path_denied:/etc")

    def test_var_is_denied(self) -> None:
        self._assert_denied("/var", "sensitive_path_denied:/var")

    def test_configured_data_root_is_denied(self) -> None:
        vault = self.data_root / "vault"
        vault.mkdir()
        self._assert_denied(vault, f"data_root_denied:{vault}")

    # -- deny: malformed input ------------------------------------------

    def test_empty_cwd_is_denied(self) -> None:
        self._assert_denied("   ", "invalid_cwd_empty")

    def test_relative_cwd_is_denied(self) -> None:
        self._assert_denied("relative/project", "invalid_cwd_relative")

    def test_missing_directory_is_denied(self) -> None:
        self._assert_denied(self.projects / "ghost", "invalid_cwd_missing")

    def test_file_instead_of_directory_is_denied(self) -> None:
        target = self.projects / "file.txt"
        target.write_text("not a directory", encoding="utf-8")
        self._assert_denied(target, "invalid_cwd_not_a_directory")

    # -- symlink escape: only secret targets are denied ------------------

    def test_symlink_escaping_to_a_non_secret_target_is_allowed(self) -> None:
        project = self._project()
        outside = self.root / "outside"
        outside.mkdir()
        link = project / "escape"
        link.symlink_to(outside, target_is_directory=True)
        spec = resolve_workspace_export(project, policy=self.policy)
        self.assertEqual(spec["host_path"], os.path.realpath(project))

    def test_venv_style_symlink_to_a_system_interpreter_is_allowed(self) -> None:
        project = self._project()
        bindir = project / ".venv" / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python").symlink_to("/usr/bin/python3")
        spec = resolve_workspace_export(project, policy=self.policy)
        self.assertEqual(spec["host_path"], os.path.realpath(project))

    def test_symlink_escaping_to_a_sensitive_path_is_denied(self) -> None:
        project = self._project()
        link = project / "ssh_link"
        link.symlink_to(self.home / ".ssh", target_is_directory=True)
        reason = self._denied_reason(project)
        self.assertTrue(reason.startswith("symlink_escape:"), reason)

    def test_symlink_escaping_to_the_data_root_is_denied(self) -> None:
        project = self._project()
        link = project / "data_link"
        link.symlink_to(self.data_root, target_is_directory=True)
        reason = self._denied_reason(project)
        self.assertTrue(reason.startswith("symlink_escape:"), reason)

    def test_symlink_chain_into_ssh_is_denied(self) -> None:
        project = self._project()
        ssh = self.home / ".ssh"
        ssh.mkdir()
        (project / "first-link").symlink_to(ssh, target_is_directory=True)
        (project / "chain-link").symlink_to("first-link")

        reason = self._denied_reason(project)

        self.assertTrue(reason.startswith("symlink_escape:"), reason)

    def test_hardlink_to_a_file_outside_the_workspace_is_denied(self) -> None:
        project = self._project()
        secret_dir = self.home / ".ssh"
        secret_dir.mkdir()
        canary = secret_dir / "fake-key.txt"
        canary.write_text("not a real key", encoding="utf-8")
        os.link(canary, project / "linked-canary.txt")

        reason = self._denied_reason(project)

        self.assertTrue(reason.startswith("hardlink_escape:"), reason)

    def test_hardlinks_wholly_inside_the_workspace_are_allowed(self) -> None:
        project = self._project()
        source = project / "source.txt"
        source.write_text("workspace data", encoding="utf-8")
        os.link(source, project / "same-inode.txt")

        spec = resolve_workspace_export(project, policy=self.policy)

        self.assertEqual(spec["host_path"], os.path.realpath(project))

    def test_uv_cache_hardlink_requires_copy_before_writable_export(self) -> None:
        project = self._project()
        source = project / "editable_finder.py"
        source.write_text("package finder", encoding="utf-8")
        cache = self.home / ".cache" / "uv" / "archive-v0" / "package"
        tools = self.home / ".local" / "share" / "uv" / "tools" / "package"
        cache.mkdir(parents=True)
        tools.mkdir(parents=True)
        os.link(source, cache / source.name)
        os.link(source, tools / source.name)

        self.assertTrue(self._denied_reason(project).startswith('hardlink_escape:'))
        copied = project / 'copied.py'
        shutil.copy2(source, copied)
        os.replace(copied, source)
        spec = resolve_workspace_export(project, policy=self.policy)
        self.assertEqual(spec['host_path'], os.path.realpath(project))
        source.write_text('changed only inside the workspace')
        self.assertEqual((cache / source.name).read_text(), 'package finder')
        self.assertEqual((tools / source.name).read_text(), 'package finder')

    def test_uv_cache_does_not_hide_another_external_hardlink(self) -> None:
        project = self._project()
        outside = self.projects / "outside"
        outside.mkdir()
        source = outside / "canary.txt"
        source.write_text("fake canary", encoding="utf-8")
        os.link(source, project / "canary.txt")
        cache = self.home / ".cache" / "uv" / "archive-v0" / "package"
        cache.mkdir(parents=True)
        os.link(source, cache / source.name)

        reason = self._denied_reason(project)

        self.assertTrue(reason.startswith("hardlink_escape:"), reason)

    def test_incomplete_scan_fails_closed(self) -> None:
        project = self._project()
        for index in range(5):
            (project / f"file-{index}.txt").write_text("x", encoding="utf-8")
        with mock.patch("factory.core.isolation.workspace.MAX_SCAN_ENTRIES", 2):
            reason = self._denied_reason(project)
        self.assertTrue(reason.startswith("workspace_scan_limit_exceeded:"), reason)

    # -- extra exports (explicit opt-in, empty by default) --------------

    def test_explicit_extra_export_is_included(self) -> None:
        project = self._project()
        vault = self.root / "vault"
        vault.mkdir()
        policy = WorkspacePolicy(
            home=self.home,
            data_root=self.data_root,
            host_uid=4242,
            guest_agent_uid=1000,
            extra_exports=(ExtraExport(cwd=str(vault), guest_path="/vault"),),
        )
        spec = resolve_workspace_export(project, policy=policy)
        self.assertEqual(
            [item["host_path"] for item in spec["extra_exports"]],
            [os.path.realpath(vault)],
        )
        self.assertEqual(spec["extra_exports"][0]["guest_path"], "/vault")
        self.assertTrue(spec["extra_exports"][0]["readonly"])
        self.assertEqual(spec["host_path"], os.path.realpath(project))

    def test_extra_export_cannot_bypass_sensitive_deny(self) -> None:
        project = self._project()
        ssh = self.home / ".ssh"
        ssh.mkdir()
        policy = WorkspacePolicy(
            home=self.home,
            data_root=self.data_root,
            host_uid=4242,
            extra_exports=(ExtraExport(cwd=str(ssh), guest_path="/ssh"),),
        )
        with self.assertRaises(IsolationError) as ctx:
            resolve_workspace_export(project, policy=policy)
        self.assertEqual(ctx.exception.reason, "sensitive_path_denied:~/.ssh")

    def test_extra_export_guest_path_must_not_shadow_workspace(self) -> None:
        project = self._project()
        vault = self.root / "vault"
        vault.mkdir()
        policy = WorkspacePolicy(
            home=self.home,
            data_root=self.data_root,
            host_uid=4242,
            extra_exports=(ExtraExport(cwd=str(vault), guest_path=GUEST_WORKSPACE_PATH),),
        )
        with self.assertRaises(IsolationError) as ctx:
            resolve_workspace_export(project, policy=policy)
        self.assertEqual(ctx.exception.reason, "invalid_extra_guest_path:/workspace")

    def test_duplicate_extra_export_is_denied(self) -> None:
        project = self._project()
        policy = WorkspacePolicy(
            home=self.home,
            data_root=self.data_root,
            host_uid=4242,
            extra_exports=(ExtraExport(cwd=str(project), guest_path="/project-copy"),),
        )
        with self.assertRaises(IsolationError) as ctx:
            resolve_workspace_export(project, policy=policy)
        self.assertTrue(ctx.exception.reason.startswith("extra_export_duplicate:"), ctx.exception.reason)

    # -- policy from environment ----------------------------------------

    def test_policy_from_environment_resolves_home_at_call_time(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            policy = WorkspacePolicy.from_environment()
        self.assertEqual(policy.home, self.home)
        self.assertIsInstance(policy.data_root, Path)
        self.assertIsInstance(policy.host_uid, int)


if __name__ == "__main__":
    unittest.main()
