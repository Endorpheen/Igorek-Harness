#!/usr/bin/env python3
"""In-guest bwrap hardening + bwrap-ready base image (plan todo 14).

Three contracts are locked here, all structurally (never by wording):

1. **Hardened argv**: in guest mode the sandbox argv unshares all namespaces
   (``--unshare-all`` semantics: net/pid/ipc/uts/cgroup/user) and carries an
   explicit environment (``--clearenv`` + ``--setenv`` allowlist). Host mode
   (no guest marker) keeps the pre-existing argv byte-for-byte: the host's
   non-isolated tasks must not change behavior.
2. **bwrap-ready image**: the base image recipe enables unprivileged user
   namespaces for the agent user, ships the ``bwrap-userns-restrict``-class
   AppArmor profile and installs the in-guest probe; the manifest records the
   userns policy and the AppArmor profile name and still validates sha256 and
   the tool version exactly as before.
3. **Fail-closed**: when namespace creation is unavailable the probe reports
   ``SANDBOX_UNAVAILABLE`` and the bash tool refuses to spawn anything - there
   is never an unsandboxed fallback.

The "inside the guest" probe is exercised with a fake ``bwrap`` binary here
(the real in-guest run is part of ``tests/test_isolation_poc.py`` and needs a
KVM host); the fake namespaces are never silently passed off as a real run.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
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
from factory.core.isolation import base_image, guest_bwrap, guest_runner  # noqa: E402

SCRIPT = REPO_ROOT / "factory" / "scripts" / "build_isolation_image.sh"
_SANDBOX_STATUS = "factory.core.bash_sandbox_settings.sandbox_status"
_PROBE = "factory.core.bash_sandbox_settings.probe_bwrap_capability"
_MODE = "factory.core.bash_sandbox_settings.resolve_sandbox_mode"
_ENABLED = "factory.core.bash_sandbox_settings.sandbox_enabled"
_RUN = "factory.core.cc_backend_tools.subprocess.run"

# The exact bwrap userns failure this image must fail closed on (Ubuntu 24.04 /
# Debian kernels with unprivileged userns restricted, research §5).
_USERNS_DENIED_STDERR = (
    "bwrap: No permissions to creating new namespace, likely because the kernel "
    "does not allow non-privileged user namespaces on this system. See "
    "<https://deb.li/bubblewrap-userns>"
)


def _bwrap_present() -> bool:
    return shutil.which("bwrap") is not None


@contextmanager
def _probe(probe: str):
    """Run the REAL `sandbox_status` with a pinned probe result (as in prod)."""
    with (
        patch(_PROBE, return_value=probe),
        patch(_MODE, return_value="on"),
        patch(_ENABLED, return_value=True),
    ):
        yield


class _RunSpy:
    """`subprocess.run` substitute: records every call, returns a fixed result."""

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


def _write_fake_bwrap(directory: Path, *, returncode: int = 0, stderr: str = "") -> Path:
    """A real executable standing in for /usr/bin/bwrap (records its argv)."""
    binary = directory / "bwrap"
    log = directory / "bwrap.argv"
    script = (
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$@\" > '{log}'\n"
        + (f"printf '%s\\n' {_shell_quote(stderr)} >&2\n" if stderr else "")
        + f"exit {int(returncode)}\n"
    )
    binary.write_text(script, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _guest_setup_body(script: str) -> str:
    """Payload of the ``<<'GUEST_SETUP'`` heredoc applied to the guest image."""
    start_marker = "<<'GUEST_SETUP'"
    start = script.index(start_marker) + len(start_marker)
    end = script.index("\nGUEST_SETUP\n", start)
    return script[start:end]


class GuestModeDetectionTests(unittest.TestCase):
    def test_guest_mode_is_off_by_default(self) -> None:
        self.assertFalse(guest_bwrap.guest_mode_requested({}))

    def test_guest_mode_true_values_are_recognized(self) -> None:
        for value in ("1", "true", "yes", "on", "KVM"):
            with self.subTest(value=value):
                self.assertTrue(
                    guest_bwrap.guest_mode_requested({guest_bwrap.GUEST_MODE_ENV: value})
                )

    def test_guest_mode_false_values_are_rejected(self) -> None:
        for value in ("", "0", "false", "no", "off", "disabled"):
            with self.subTest(value=value):
                self.assertFalse(
                    guest_bwrap.guest_mode_requested({guest_bwrap.GUEST_MODE_ENV: value})
                )

    def test_guest_marker_matches_the_channel_env_name_shape(self) -> None:
        self.assertTrue(guest_bwrap.GUEST_MODE_ENV.startswith("IGOREK_ISOLATION_"))

    def test_guest_runner_entrypoint_marks_the_whole_process_in_guest(self) -> None:
        saved = os.environ.pop(guest_bwrap.GUEST_MODE_ENV, None)
        try:
            with patch.object(guest_runner, "run_guest_runner", return_value=0):
                self.assertEqual(guest_runner.main([]), 0)
                self.assertEqual(
                    os.environ.get(guest_bwrap.GUEST_MODE_ENV), "1"
                )
        finally:
            if saved is None:
                os.environ.pop(guest_bwrap.GUEST_MODE_ENV, None)
            else:
                os.environ[guest_bwrap.GUEST_MODE_ENV] = saved


class HardenedArgvTests(unittest.TestCase):
    """Guest argv hardening; the host argv must stay exactly as before."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_guest_bwrap_")
        self.root = Path(self._tmp.name)
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(self.root / "home"),
                "PATH": "/usr/bin:/bin",
                guest_bwrap.GUEST_MODE_ENV: "1",
            }
        )
        env.update(extra)
        return env

    def _argv(self, env: dict[str, str], command: str = "echo hi") -> list[str]:
        with patch.object(bash_scope_guard.shutil, "which", return_value="/usr/bin/bwrap"):
            argv = bash_scope_guard.wrap_with_sandbox_argv(
                command, workdir=str(self.workdir), env=env
            )
        self.assertIsNotNone(argv)
        return list(argv or [])

    def test_guest_argv_isolates_non_network_namespaces_and_keeps_guest_egress(self) -> None:
        argv = self._argv(self._env())
        for flag in (
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
        ):
            with self.subTest(flag=flag):
                self.assertIn(flag, argv)
        self.assertNotIn("--unshare-all", argv)
        self.assertNotIn("--unshare-net", argv)
        self.assertIn("--die-with-parent", argv)
        self.assertIn("--new-session", argv)
        self.assertIn("--cap-drop", argv)
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")

    @staticmethod
    def _setenv(argv: list[str]) -> dict[str, str]:
        setenv: dict[str, str] = {}
        for index, token in enumerate(argv):
            if token == "--setenv":
                setenv[argv[index + 1]] = argv[index + 2]
        return setenv

    def test_guest_argv_clears_and_sets_an_explicit_environment(self) -> None:
        argv = self._argv(
            self._env(
                WORKER_SECRET_TOKEN="must-not-leak",
                IGOREK_ISOLATION_VSOCK_PORT="43011",
            )
        )
        self.assertIn("--clearenv", argv)
        setenv = self._setenv(argv)
        self.assertEqual(setenv.get("PATH"), "/usr/bin:/bin", argv)
        self.assertEqual(setenv.get("IGOREK_ISOLATION_VSOCK_PORT"), "43011")
        self.assertNotIn("WORKER_SECRET_TOKEN", setenv)

    def test_guest_argv_drops_secret_like_variables_matching_old_broad_prefixes(
        self,
    ) -> None:
        """Todo 39 regression: a real secret must not cross by name or value.

        Before the fix the sandbox forwarded every ``IGOREK_``/``UV_``/
        ``XDG_``/``VIRTUAL_ENV``-prefixed name, so a genuine secret such as
        ``IGOREK_SESSION_EMBEDDINGS_API_KEY`` (see
        ``factory/core/session_embeddings.py``) or a credentialed
        ``UV_INDEX_URL`` landed in ``--setenv`` and was readable from the
        guest's ``/proc/<pid>/cmdline``.
        """
        argv = self._argv(
            self._env(
                IGOREK_SESSION_EMBEDDINGS_API_KEY="SUPERSECRET-5",
                IGOREK_OPENROUTER_API_KEY="SUPERSECRET-6",
                UV_INDEX_URL="https://user:pass@x",
                UV_SECRET_TOKEN="SUPERSECRET-7",
                XDG_CONFIG_HOME="/home/agent/.config",
                VIRTUAL_ENV="/home/agent/.venv",
                IGOREK_UNKNOWN_KNOB="1",
            )
        )
        setenv = self._setenv(argv)
        for name in (
            "IGOREK_SESSION_EMBEDDINGS_API_KEY",
            "IGOREK_OPENROUTER_API_KEY",
            "UV_INDEX_URL",
            "UV_SECRET_TOKEN",
            "XDG_CONFIG_HOME",
            "VIRTUAL_ENV",
            "IGOREK_UNKNOWN_KNOB",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, setenv)
        joined = "\x00".join(argv)
        for value in (
            "SUPERSECRET-5",
            "SUPERSECRET-6",
            "SUPERSECRET-7",
            "https://user:pass@x",
        ):
            with self.subTest(value=value):
                self.assertNotIn(value, joined)

    def test_guest_argv_forwards_every_documented_allowlisted_variable(self) -> None:
        """Exact-name forwarding: the whole documented allowlist still crosses."""
        planted = {
            name: f"allowlisted-{index}"
            for index, name in enumerate(guest_bwrap.HARDENED_ENV_ALLOWLIST)
            if name != guest_bwrap.GUEST_MODE_ENV
        }
        argv = self._argv(self._env(**planted))
        setenv = self._setenv(argv)
        for name, value in planted.items():
            with self.subTest(name=name):
                self.assertEqual(setenv.get(name), value, argv)

    def test_guest_argv_denies_every_name_outside_the_allowlist(self) -> None:
        """Deny by default: no ``--setenv`` name may sit outside the allowlist."""
        argv = self._argv(
            self._env(
                IGOREK_WHATEVER="1",
                UV_WHATEVER="2",
                XDG_WHATEVER="3",
                WORKER_SECRET_TOKEN="4",
            )
        )
        setenv = self._setenv(argv)
        self.assertTrue(setenv, argv)
        self.assertLessEqual(set(setenv), set(guest_bwrap.HARDENED_ENV_ALLOWLIST))

    def test_guest_argv_keeps_the_workspace_and_command_shape(self) -> None:
        argv = self._argv(self._env())
        real_workdir = os.path.realpath(str(self.workdir))
        self.assertIn("--chdir", argv)
        self.assertEqual(argv[argv.index("--chdir") + 1], real_workdir)
        self.assertEqual(argv[argv.index("--") + 1], "/bin/bash")
        self.assertEqual(argv[-1], "echo hi")
        self.assertEqual(argv[0], "/usr/bin/bwrap")

    def test_host_argv_is_not_hardened_without_guest_mode(self) -> None:
        env = self._env()
        env.pop(guest_bwrap.GUEST_MODE_ENV)
        argv = self._argv(env)
        self.assertNotIn("--unshare-all", argv)
        self.assertNotIn("--unshare-user", argv)
        self.assertNotIn("--unshare-net", argv)
        self.assertNotIn("--clearenv", argv)
        self.assertNotIn("--setenv", argv)
        unshare_flags = [flag for flag in argv if flag.startswith("--unshare")]
        self.assertEqual(
            unshare_flags,
            ["--unshare-ipc", "--unshare-pid", "--unshare-uts", "--unshare-cgroup-try"],
        )

    def test_explicit_guest_mode_flag_selects_the_hardened_argv(self) -> None:
        env = self._env()
        env.pop(guest_bwrap.GUEST_MODE_ENV)
        with patch.object(bash_scope_guard.shutil, "which", return_value="/usr/bin/bwrap"):
            argv = bash_scope_guard.wrap_with_sandbox_argv(
                "echo hi", workdir=str(self.workdir), env=env, guest_mode=True
            )
        self.assertIn("--unshare-user", list(argv or []))
        self.assertNotIn("--unshare-net", list(argv or []))

    def test_guest_sandbox_string_form_uses_the_hardened_argv(self) -> None:
        env = self._env()
        env[bash_scope_guard.SANDBOX_ENV] = "on"
        with patch.object(bash_scope_guard.shutil, "which", return_value="/usr/bin/bwrap"):
            wrapped = bash_scope_guard.wrap_with_sandbox(
                "echo hi", workdir=str(self.workdir), env=env
            )
        self.assertIsNotNone(wrapped)
        self.assertIn("--unshare-user", str(wrapped))
        self.assertNotIn("--unshare-net", str(wrapped))
        self.assertIn("--clearenv", str(wrapped))


class GuestProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_guest_probe_")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_probe_argv_contains_the_hardened_namespace_flags(self) -> None:
        argv = guest_bwrap.probe_bwrap_argv("/usr/bin/bwrap")
        self.assertEqual(argv[0], "/usr/bin/bwrap")
        for flag in ("--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts"):
            self.assertIn(flag, argv)
        self.assertNotIn("--unshare-all", argv)
        self.assertNotIn("--unshare-net", argv)

    def test_working_bwrap_reports_the_probe_ok_marker(self) -> None:
        binary = _write_fake_bwrap(self.root, returncode=0)
        probe = guest_bwrap.probe_guest_bwrap(bwrap_path=str(binary))
        self.assertTrue(probe.ok, probe.message)
        self.assertEqual(probe.reason, "ok")
        self.assertEqual(probe.message, "bwrap probe ok")
        self.assertEqual(probe.message, guest_bwrap.PROBE_OK_TEXT)
        recorded = (self.root / "bwrap.argv").read_text(encoding="utf-8").split()
        self.assertIn("--unshare-user", recorded)
        self.assertNotIn("--unshare-all", recorded)
        self.assertNotIn("--unshare-net", recorded)
        self.assertNotIn("--unshare-user-try", recorded)

    def test_missing_bwrap_fails_closed_as_unavailable(self) -> None:
        probe = guest_bwrap.probe_guest_bwrap(
            bwrap_path=str(self.root / "absent-bwrap")
        )
        self.assertFalse(probe.ok)
        self.assertEqual(probe.reason, "bwrap_missing")
        self.assertTrue(
            probe.message.startswith(guest_bwrap.SANDBOX_UNAVAILABLE_PREFIX), probe.message
        )

    def test_userns_disabled_fails_closed_with_sandbox_unavailable(self) -> None:
        binary = _write_fake_bwrap(
            self.root, returncode=1, stderr=_USERNS_DENIED_STDERR
        )
        probe = guest_bwrap.probe_guest_bwrap(bwrap_path=str(binary))
        self.assertFalse(probe.ok)
        self.assertEqual(probe.reason, "namespace_blocked")
        self.assertEqual(
            probe.message,
            f"{guest_bwrap.SANDBOX_UNAVAILABLE_PREFIX}: namespace_blocked",
        )

    def test_probe_error_never_reports_ok(self) -> None:
        binary = _write_fake_bwrap(self.root, returncode=2)
        probe = guest_bwrap.probe_guest_bwrap(bwrap_path=str(binary))
        self.assertFalse(probe.ok)
        self.assertNotEqual(probe.reason, "ok")
        self.assertTrue(
            probe.message.startswith(guest_bwrap.SANDBOX_UNAVAILABLE_PREFIX), probe.message
        )

    def test_probe_cli_prints_the_marker_and_exits_zero_when_ok(self) -> None:
        binary = _write_fake_bwrap(self.root, returncode=0)
        lines: list[str] = []
        code = guest_bwrap.probe_cli(
            ["--probe"], bwrap_path=str(binary), emit=lines.append
        )
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines, ["bwrap probe ok"])

    def test_probe_cli_fails_closed_with_sandbox_unavailable(self) -> None:
        binary = _write_fake_bwrap(
            self.root, returncode=1, stderr=_USERNS_DENIED_STDERR
        )
        lines: list[str] = []
        code = guest_bwrap.probe_cli(
            ["--probe"], bwrap_path=str(binary), emit=lines.append
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(
            lines, [f"{guest_bwrap.SANDBOX_UNAVAILABLE_PREFIX}: namespace_blocked"]
        )

    @unittest.skipUnless(_bwrap_present(), "bwrap binary not found")
    def test_real_probe_on_this_kernel_reports_the_marker(self) -> None:
        probe = guest_bwrap.probe_guest_bwrap()
        self.assertTrue(probe.ok, probe.message)
        self.assertIn(guest_bwrap.PROBE_OK_TEXT, probe.message)


class SandboxUnavailablePrefixContractTests(unittest.TestCase):
    def test_prefix_matches_the_bash_scope_guard_contract(self) -> None:
        self.assertEqual(
            guest_bwrap.SANDBOX_UNAVAILABLE_PREFIX,
            bash_scope_guard.SANDBOX_DENIED_PREFIX,
        )

    def test_probe_markers_cover_every_guard_namespace_marker(self) -> None:
        for marker in bash_scope_guard._NAMESPACE_BLOCKED_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, guest_bwrap.PROBE_BLOCKED_MARKERS)


class ImageRecipeTests(unittest.TestCase):
    """The base image recipe itself must carry the bwrap-readiness contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def test_recipe_enables_unprivileged_userns_for_the_agent_user(self) -> None:
        self.assertIn(guest_bwrap.USERNS_SYSCTL_FILE, self.script)
        self.assertIn("kernel.unprivileged_userns_clone = 1", self.script)
        self.assertIn("user.max_user_namespaces", self.script)
        self.assertIn("sysctl -p", self.script)
        self.assertIn(guest_bwrap.USERNS_POLICY_MARKER, self.script)

    def test_recipe_ships_the_apparmor_userns_profile(self) -> None:
        self.assertIn(guest_bwrap.BWRAP_APPARMOR_PROFILE, self.script)
        self.assertIn(guest_bwrap.APPARMOR_PROFILE_PATH, self.script)
        self.assertIn(guest_bwrap.APPARMOR_USENS_RULE, self.script)
        self.assertIn("apparmor_parser", self.script)

    def test_recipe_installs_the_in_guest_probe(self) -> None:
        self.assertIn(guest_bwrap.GUEST_PROBE_BIN, self.script)
        self.assertIn("factory.core.isolation.guest_bwrap", self.script)
        self.assertIn("--probe", self.script)

    def test_recipe_probe_and_manifest_share_the_recorded_userns_policy(self) -> None:
        record = [
            line
            for line in self.script.splitlines()
            if line.startswith("USERNS_POLICY_RECORD=")
        ]
        self.assertEqual(len(record), 1, record)
        self.assertEqual(
            record[0].split("=", 1)[1].strip().strip('"'),
            guest_bwrap.USERNS_POLICY,
        )
        self.assertIn('"$USERNS_POLICY_RECORD"', self.script)

    def test_recipe_relaxes_apparmor_only_inside_the_guest_image(self) -> None:
        """The userns AppArmor relaxation is guest-image-scoped, never global.

        The recipe does write ``kernel.apparmor_restrict_unprivileged_userns = 0``
        and reload a profile - but exclusively inside the guest setup payload
        that is applied to the image (``virt-builder --run-command`` / ``chroot``). The
        host's AppArmor and kernel command line stay untouched: no
        ``apparmor=0``, no ``security=apparmor``, no ``aa-disable``.
        """
        self.assertNotIn("apparmor=0", self.script)
        self.assertNotIn("security=apparmor", self.script)
        self.assertNotIn("aa-disable", self.script)

        body = _guest_setup_body(self.script)
        self.assertIn("kernel.apparmor_restrict_unprivileged_userns = 0", body)
        for marker in (
            "> /etc/sysctl.d/91-igorek-apparmor-userns.conf",
            "sysctl -p",
            "apparmor_parser",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)
                self.assertEqual(
                    self.script.count(marker),
                    body.count(marker),
                    f"{marker} appears outside the guest setup payload",
                )
        self.assertIn(
            f"profile {guest_bwrap.BWRAP_APPARMOR_PROFILE} /usr/bin/bwrap", body
        )

    def test_recipe_applies_the_guest_setup_only_inside_the_image(self) -> None:
        """The guest payload runs with Bash inside the image, never on the host."""
        mkdir = '--mkdir /opt/igorek'
        upload_env = '--upload "$GUEST_ENV_FILE:/opt/igorek/guest-env.sh"'
        upload_setup = '--upload "$GUEST_SETUP_FILE:/opt/igorek/guest-setup.sh"'
        upload_source = '--upload "$REPO_ARCHIVE:/opt/igorek/repo.tar"'
        run_setup = '--run-command "/bin/bash /opt/igorek/guest-setup.sh"'
        for marker in (mkdir, upload_env, upload_setup, upload_source, run_setup):
            self.assertIn(marker, self.script)
        self.assertLess(
            self.script.index(mkdir),
            self.script.index(upload_env),
        )
        self.assertLess(
            self.script.index(upload_source),
            self.script.index(run_setup),
        )
        self.assertIn('git -C "$REPO_ROOT" archive --format=tar', self.script)
        self.assertIn('tar -xf /opt/igorek/repo.tar', self.script)
        self.assertNotIn('git clone', self.script)
        self.assertIn(
            'run_in_chroot "$root_dir" /opt/igorek/guest-setup.sh', self.script
        )

    def test_dry_run_plan_reports_bwrap_readiness(self) -> None:
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--dry-run"],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "IGOREK_ISOLATION_PYTHON": sys.executable},
        )
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, combined)
        self.assertIn(guest_bwrap.BWRAP_APPARMOR_PROFILE, combined)
        self.assertIn(guest_bwrap.USERNS_POLICY_MARKER, combined)
        self.assertNotIn("PASS", combined)


class ManifestBwrapReadinessTests(unittest.TestCase):
    """Manifest provenance records bwrap readiness without weakening validation."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_guest_manifest_")
        self.dir = Path(self._tmp.name)
        self.image = self.dir / "base.qcow2"
        self.image.write_bytes(b"qcow2-fake-image-bytes" * 64)
        self.tool_version = "0.1.750"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _info(self, **overrides: object) -> "base_image.ImageBuildInfo":
        values: dict[str, object] = {"tool_version": self.tool_version}
        values.update(overrides)
        return base_image.ImageBuildInfo(**values)  # type: ignore[arg-type]

    def _write_valid_manifest(self) -> "base_image.BaseImageManifest":
        manifest = base_image.build_manifest(self.image, self._info())
        base_image.write_manifest(manifest, self.image)
        return manifest

    def test_manifest_records_userns_policy_and_apparmor_profile(self) -> None:
        manifest = self._write_valid_manifest()
        self.assertEqual(manifest.userns_policy, guest_bwrap.USERNS_POLICY)
        self.assertEqual(manifest.apparmor_profile, guest_bwrap.BWRAP_APPARMOR_PROFILE)
        payload = json.loads(
            base_image.manifest_path_for(self.image).read_text(encoding="utf-8")
        )
        self.assertEqual(payload["userns_policy"], guest_bwrap.USERNS_POLICY)
        self.assertEqual(
            payload["apparmor_profile"], guest_bwrap.BWRAP_APPARMOR_PROFILE
        )
        loaded = base_image.load_manifest(base_image.manifest_path_for(self.image))
        self.assertEqual(loaded.userns_policy, manifest.userns_policy)
        self.assertEqual(loaded.apparmor_profile, manifest.apparmor_profile)

    def test_verify_result_exposes_bwrap_readiness(self) -> None:
        self._write_valid_manifest()
        result = base_image.verify_image(
            self.image, expected_tool_version=self.tool_version
        )
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.userns_policy, guest_bwrap.USERNS_POLICY)
        self.assertEqual(result.apparmor_profile, guest_bwrap.BWRAP_APPARMOR_PROFILE)
        payload = result.to_dict()
        self.assertEqual(payload["userns_policy"], guest_bwrap.USERNS_POLICY)
        self.assertEqual(
            payload["apparmor_profile"], guest_bwrap.BWRAP_APPARMOR_PROFILE
        )

    def test_manifest_with_foreign_apparmor_profile_is_rejected(self) -> None:
        self._write_valid_manifest()
        path = base_image.manifest_path_for(self.image)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["apparmor_profile"] = "unconfined"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = base_image.verify_image(self.image)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_manifest")

    def test_manifest_without_userns_policy_is_rejected(self) -> None:
        self._write_valid_manifest()
        path = base_image.manifest_path_for(self.image)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("userns_policy")
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = base_image.verify_image(self.image)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_manifest")

    def test_manifest_with_userns_disabled_policy_is_rejected(self) -> None:
        manifest = base_image.build_manifest(
            self.image,
            self._info(userns_policy="kernel.unprivileged_userns_clone=0"),
        )
        base_image.write_manifest(manifest, self.image)
        result = base_image.verify_image(self.image)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_manifest")

    def test_checksum_and_tool_version_validation_still_hold(self) -> None:
        self._write_valid_manifest()
        result = base_image.verify_image(
            self.image, expected_tool_version="0.1.999"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "version_mismatch")
        data = bytearray(self.image.read_bytes())
        data[0] ^= 0xFF
        self.image.write_bytes(bytes(data))
        result = base_image.verify_image(self.image)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "checksum_mismatch")

    def test_scaffolding_script_manifest_records_the_policy(self) -> None:
        self._write_valid_manifest()
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "factory.core.isolation.base_image_cli",
                "verify",
                "--image",
                str(self.image),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload, _end = json.JSONDecoder().raw_decode(proc.stdout)
        self.assertEqual(payload["apparmor_profile"], guest_bwrap.BWRAP_APPARMOR_PROFILE)
        self.assertEqual(payload["userns_policy"], guest_bwrap.USERNS_POLICY)
        self.assertIn("PASS", proc.stdout)


class GuestBashFailClosedTests(unittest.TestCase):
    """With namespaces unavailable the bash tool denies; it never runs unsandboxed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_guest_failclosed_")
        self.workdir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_namespace_blocked_guest_bash_fails_closed_without_spawn(self) -> None:
        spy = _RunSpy(stdout="should-not-run\n")
        with _probe("namespace_blocked"), patch(_RUN, spy):
            result = cc_backend_tools._tool_bash(
                {"command": "echo hi"}, workdir=str(self.workdir)
            )
        self.assertTrue(result.startswith("SANDBOX_UNAVAILABLE"), result)
        self.assertIn("namespace_blocked", result)
        self.assertEqual(spy.calls, [])

    def test_bwrap_missing_guest_bash_fails_closed_without_spawn(self) -> None:
        spy = _RunSpy(stdout="should-not-run\n")
        with _probe("bwrap_missing"), patch(_RUN, spy):
            result = cc_backend_tools._tool_bash(
                {"command": "echo hi"}, workdir=str(self.workdir)
            )
        self.assertTrue(result.startswith("SANDBOX_UNAVAILABLE"), result)
        self.assertIn("bwrap_missing", result)
        self.assertEqual(spy.calls, [])

    def test_guest_mode_probe_uses_the_hardened_argv(self) -> None:
        """`probe_bwrap_capability` must probe the hardened flags in guest mode."""
        binary = _write_fake_bwrap(self.workdir, returncode=0)
        bash_scope_guard.probe_bwrap_capability.cache_clear()
        try:
            with (
                patch.dict(
                    os.environ, {guest_bwrap.GUEST_MODE_ENV: "1"}, clear=False
                ),
                patch.object(
                    bash_scope_guard.shutil, "which", return_value=str(binary)
                ),
            ):
                self.assertEqual(bash_scope_guard.probe_bwrap_capability(), "ok")
        finally:
            bash_scope_guard.probe_bwrap_capability.cache_clear()
        recorded = (self.workdir / "bwrap.argv").read_text(encoding="utf-8")
        self.assertIn("--unshare-user", recorded)
        self.assertNotIn("--unshare-net", recorded)

    def test_guest_mode_probe_fails_closed_without_namespaces(self) -> None:
        binary = _write_fake_bwrap(
            self.workdir, returncode=1, stderr=_USERNS_DENIED_STDERR
        )
        bash_scope_guard.probe_bwrap_capability.cache_clear()
        try:
            with (
                patch.dict(
                    os.environ, {guest_bwrap.GUEST_MODE_ENV: "1"}, clear=False
                ),
                patch.object(
                    bash_scope_guard.shutil, "which", return_value=str(binary)
                ),
            ):
                self.assertEqual(
                    bash_scope_guard.probe_bwrap_capability(), "namespace_blocked"
                )
        finally:
            bash_scope_guard.probe_bwrap_capability.cache_clear()


if __name__ == "__main__":
    unittest.main()
