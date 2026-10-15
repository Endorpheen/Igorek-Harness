"""Guest-side bwrap hardening: readiness contract, hardened argv, runtime probe.

This module is the single source of truth for how bubblewrap behaves INSIDE a
KVM session VM (plan todo 14, research §5 - Ubuntu unprivileged-userns
restriction, ``bwrap-userns-restrict``, CVE-2020-5291):

* the **base image contract** (``factory/scripts/build_isolation_image.sh``):
  unprivileged user namespaces enabled for the agent user, the
  ``bwrap-userns-restrict``-class AppArmor profile at
  :data:`APPARMOR_PROFILE_PATH`, and the installed probe
  :data:`GUEST_PROBE_BIN` - all recorded in the image manifest by
  :mod:`factory.core.isolation.base_image`;
* the **hardened argv**: ``wrap_with_sandbox_argv`` swaps its flag block for
  :func:`hardened_argv_prefix` when :data:`GUEST_MODE_ENV` marks the process as
  in-guest. User/pid/ipc/uts/cgroup namespaces are unshared while the bwrap
  process shares only its parent guest's egress-filtered network namespace; an
  explicit environment (``--clearenv`` + a ``--setenv`` allowlist) replaces
  inherited host-style flags. Host tasks without the marker keep their
  byte-identical argv;
* the **fail-closed probe** (:func:`probe_guest_bwrap`): a working hardened
  ``bwrap`` reports ``bwrap probe ok``; a missing binary or a kernel/AppArmor
  policy that forbids namespace creation reports
  ``SANDBOX_UNAVAILABLE: <reason>`` and ``ok=False``. Callers must treat a
  non-``ok`` probe as "no sandbox" and refuse to run - this module never
  produces an unsandboxed fallback.

No host imports: the module is stdlib-only so it can run inside the guest.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "GUEST_MODE_ENV",
    "BWRAP_PATH_ENV",
    "GUEST_MODE_TRUE_VALUES",
    "BWRAP_APPARMOR_PROFILE",
    "APPARMOR_PROFILE_PATH",
    "APPARMOR_USENS_RULE",
    "USERNS_SYSCTL_FILE",
    "USERNS_POLICY",
    "USERNS_POLICY_MARKER",
    "USERNS_SYSCTL_ENTRIES",
    "GUEST_PROBE_BIN",
    "HARDENED_UNSHARE_FLAGS",
    "HARDENED_ENV_ALLOWLIST",
    "PROBE_OK_TEXT",
    "PROBE_BLOCKED_MARKERS",
    "SANDBOX_UNAVAILABLE_PREFIX",
    "PROBE_TIMEOUT_S",
    "GuestBwrapProbe",
    "guest_mode_requested",
    "resolve_bwrap_path",
    "guest_unshare_flags",
    "hardened_argv_prefix",
    "guest_sandbox_env_argv",
    "probe_bwrap_argv",
    "probe_guest_bwrap",
    "probe_cli",
    "main",
]

# Process-level marker: set by the in-guest runner, read by bash_scope_guard.
GUEST_MODE_ENV: Final = "IGOREK_ISOLATION_GUEST"
# Explicit binary override for diagnostics/tests (resolution order: argument,
# this variable, then PATH).
BWRAP_PATH_ENV: Final = "IGOREK_ISOLATION_BWRAP"
GUEST_MODE_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on", "kvm"})

# --- base image contract (recipe + manifest record) ------------------------ #
BWRAP_APPARMOR_PROFILE: Final = "bwrap-userns-restrict"
APPARMOR_PROFILE_PATH: Final = f"/etc/apparmor.d/{BWRAP_APPARMOR_PROFILE}"
APPARMOR_USENS_RULE: Final = "userns,"
USERNS_SYSCTL_FILE: Final = "/etc/sysctl.d/90-igorek-userns.conf"
USERNS_SYSCTL_ENTRIES: Final = (
    ("kernel.unprivileged_userns_clone", "1"),
    ("user.max_user_namespaces", "10000"),
)
USERNS_POLICY: Final = ",".join(f"{key}={value}" for key, value in USERNS_SYSCTL_ENTRIES)
# Marker the manifest validator requires: an image without it is not bootable.
USERNS_POLICY_MARKER: Final = "kernel.unprivileged_userns_clone=1"
GUEST_PROBE_BIN: Final = "/usr/local/bin/igorek-guest-bwrap-probe"

# --- hardened in-guest argv ------------------------------------------------ #
# Keep the network namespace inherited from the guest runner: the per-session
# nftables policy is already installed there and enforces the guest allowlist.
# Unsharing it here would remove even permitted network access. Every other
# namespace below stays isolated; cgroup remains try-only as on the host path.
HARDENED_UNSHARE_FLAGS: Final = (
    "--unshare-user",
    "--unshare-pid",
    "--unshare-ipc",
    "--unshare-uts",
    "--unshare-cgroup-try",
)
# Explicit in-guest sandbox environment allowlist: only these exact names may
# cross into the bwrap sandbox via ``--setenv``. Anything not listed is dropped
# by default, so a secret is never forwarded merely because its name matches a
# prefix. Each entry has a concrete in-guest consumer:
#   GIT_CONFIG_COUNT, GIT_CONFIG_KEY_0/1, GIT_CONFIG_VALUE_0/1 - the
#       non-secret repository gateway rewrite and redirect policy for Git;
#   HOME, LOGNAME, USER, SHELL - login identity and the per-user config/caches
#       of the tools the agent runs (`bash -l`, git, uv, python);
#   LANG, LC_ALL, LC_CTYPE - locale of the sandboxed shell; a non-UTF-8 default
#       breaks Python text I/O on non-ASCII command output;
#   PATH - the sandboxed `/bin/bash -c` resolves every command through it;
#   TERM - terminal-aware output of the tools the agent runs;
#   TMPDIR - the sandbox's only writable scratch space (`--tmpfs /tmp`);
#   IGOREK_ISOLATION_GUEST - guest-mode marker; a nested sandboxed invocation
#       must stay on the hardened argv (see ``guest_mode_requested``);
#   IGOREK_ISOLATION_VSOCK_PORT - port the in-guest runtime dials the host
#       channel on (``isolation/channel.py::_default_port``);
#   IGOREK_BASH_SANDBOX, IGOREK_BASH_SCOPE_GUARD, IGOREK_BASH_SANDBOX_RW,
#   IGOREK_BASH_WRITE_ROOTS, IGOREK_BASH_TOOL_ROOTS - bash guard policy; a
#       nested guarded invocation must resolve the same mode and roots the
#       guest runtime resolved for this command (``bash_scope_guard``);
#   UV_CACHE_DIR, UV_TOOL_DIR, UV_TOOL_BIN_DIR - uv roots; ``bash_scope_guard``
#       binds exactly these as writable, so a sandboxed `uv` must see the same
#       paths or its cache/tool writes land outside the bind;
#   XDG_CACHE_HOME, XDG_DATA_HOME - fallback bases for the uv roots above.
HARDENED_ENV_ALLOWLIST: Final = (
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_KEY_0",
    "GIT_CONFIG_VALUE_0",
    "GIT_CONFIG_KEY_1",
    "GIT_CONFIG_VALUE_1",
    "HOME",
    "LOGNAME",
    "USER",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "TERM",
    "TMPDIR",
    "IGOREK_ISOLATION_GUEST",
    "XDG_RUNTIME_DIR",
    "AGENT_BROWSER_SESSION",
    "AGENT_BROWSER_NAMESPACE",
    "AGENT_BROWSER_SOCKET_DIR",
    "IGOREK_ISOLATION_VSOCK_PORT",
    "IGOREK_BASH_SANDBOX",
    "IGOREK_BASH_SCOPE_GUARD",
    "IGOREK_BASH_SANDBOX_RW",
    "IGOREK_BASH_WRITE_ROOTS",
    "IGOREK_BASH_TOOL_ROOTS",
    "UV_CACHE_DIR",
    "UV_TOOL_DIR",
    "UV_TOOL_BIN_DIR",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
)

PROBE_OK_TEXT: Final = "bwrap probe ok"
SANDBOX_UNAVAILABLE_PREFIX: Final = "SANDBOX_UNAVAILABLE"
PROBE_TIMEOUT_S: Final = 3.0

_REASON_OK: Final = "ok"
_REASON_BWRAP_MISSING: Final = "bwrap_missing"
_REASON_NAMESPACE_BLOCKED: Final = "namespace_blocked"
_REASON_PROBE_FAILED: Final = "probe_failed"

# Superset of the bash-scope-guard markers: a probe must recognize the
# Ubuntu/Debian unprivileged-userns denial too.
PROBE_BLOCKED_MARKERS: Final = (
    "Operation not permitted",
    "Failed to create new namespace",
    "Creating new namespace failed",
    "setting up uid map",
    "No permissions to create new namespace",
    "No permissions to creating new namespace",
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class GuestBwrapProbe:
    """Structured probe outcome; ``message`` is what a caller may print."""

    ok: bool
    reason: str
    message: str
    argv: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "message": self.message,
            "argv": list(self.argv),
        }


def guest_mode_requested(env: Mapping[str, str] | None = None) -> bool:
    """True when this process runs inside a session VM (guest marker set)."""
    source = os.environ if env is None else env
    raw = source.get(GUEST_MODE_ENV)
    if raw is None:
        return False
    return str(raw).strip().lower() in GUEST_MODE_TRUE_VALUES


def resolve_bwrap_path(
    env: Mapping[str, str] | None = None, bwrap_path: str | None = None
) -> str | None:
    """Resolve the binary to probe: argument, then env override, then PATH."""
    if bwrap_path:
        return bwrap_path
    source = os.environ if env is None else env
    override = str(source.get(BWRAP_PATH_ENV) or "").strip()
    if override:
        return override
    return shutil.which("bwrap")


def guest_unshare_flags() -> tuple[str, ...]:
    """Namespace flags of the hardened in-guest argv."""
    return HARDENED_UNSHARE_FLAGS


def hardened_argv_prefix(bwrap_path: str) -> list[str]:
    """Hardened flags before the content mounts (keeps the mount logic shared)."""
    return [
        bwrap_path,
        "--die-with-parent",
        "--new-session",
        *HARDENED_UNSHARE_FLAGS,
        "--cap-drop",
        "ALL",
    ]


def guest_sandbox_env_argv(env: Mapping[str, str]) -> list[str]:
    """``--clearenv`` plus the explicit ``--setenv`` allowlist for the sandbox.

    Only :data:`HARDENED_ENV_ALLOWLIST` names cross; every other variable is
    dropped by default, so no value can leak through the guest's
    ``/proc/<pid>/cmdline`` merely because its name matches a prefix.
    """
    argv: list[str] = ["--clearenv"]
    for name in HARDENED_ENV_ALLOWLIST:
        value = env.get(name)
        if value is None:
            continue
        argv += ["--setenv", name, str(value)]
    return argv


def probe_bwrap_argv(bwrap_path: str) -> list[str]:
    """The exact hardened probe command: namespaces + minimal mounts + true."""
    return [
        *hardened_argv_prefix(bwrap_path),
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "/bin/true",
    ]


def _probe_result(
    ok: bool, reason: str, argv: Sequence[str]
) -> GuestBwrapProbe:
    message = PROBE_OK_TEXT if ok else f"{SANDBOX_UNAVAILABLE_PREFIX}: {reason}"
    return GuestBwrapProbe(ok=ok, reason=reason, message=message, argv=tuple(argv))


def probe_guest_bwrap(
    *,
    runner: Runner | None = None,
    bwrap_path: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float = PROBE_TIMEOUT_S,
) -> GuestBwrapProbe:
    """Prove the hardened bwrap really runs; fail closed for every other answer."""
    resolved = resolve_bwrap_path(env, bwrap_path)
    if resolved is None:
        return _probe_result(False, _REASON_BWRAP_MISSING, ())
    argv = probe_bwrap_argv(resolved)
    run = subprocess.run if runner is None else runner
    try:
        result = run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return _probe_result(False, _REASON_BWRAP_MISSING, argv)
    except subprocess.TimeoutExpired:
        return _probe_result(False, _REASON_NAMESPACE_BLOCKED, argv)
    except OSError:
        return _probe_result(False, _REASON_PROBE_FAILED, argv)
    if result.returncode == 0:
        return _probe_result(True, _REASON_OK, argv)
    output = f"{result.stdout or ''}{result.stderr or ''}"
    reason = (
        _REASON_NAMESPACE_BLOCKED
        if any(marker in output for marker in PROBE_BLOCKED_MARKERS)
        else _REASON_PROBE_FAILED
    )
    return _probe_result(False, reason, argv)


def probe_cli(
    argv: Sequence[str] | None = None,
    *,
    bwrap_path: str | None = None,
    emit: Callable[[str], None] = print,
) -> int:
    """Print the probe verdict; exit 0 only for ``bwrap probe ok``."""
    parser = argparse.ArgumentParser(
        prog="guest_bwrap",
        description="Probe the hardened in-guest bwrap (plan todo 14).",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="run the hardened bwrap probe and print its verdict",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.probe:
        parser.error("nothing to do: pass --probe")
    probe = probe_guest_bwrap(bwrap_path=bwrap_path)
    emit(probe.message)
    return 0 if probe.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    return probe_cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
