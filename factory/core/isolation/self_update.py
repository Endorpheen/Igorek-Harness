"""Self-update split: in-guest environment update vs host runtime update (todo 21).

``igorek-factory`` is installed with ``uv tool install`` and the agent may want
to update it. Under KVM session isolation there are two very different update
paths, and this module is the single place that names, audits and enforces the
split between them.

**In-guest environment update (guest-local, disposable).** Inside a session VM
the agent may run ``uv tool install --force --reinstall .`` exactly as before.
That command writes only the three uv roots of the *guest's own* environment -
``UV_CACHE_DIR``, ``UV_TOOL_DIR`` and ``UV_TOOL_BIN_DIR`` (defaults
``$HOME/.cache/uv``, ``$HOME/.local/share/uv/tools`` and ``$HOME/.local/bin``) -
resolved by :func:`factory.core.bash_scope_guard.tool_reinstall_roots` from the
guest process environment, where ``$HOME`` is the guest user's home
(``/home/agent`` in the base image; see ``build_isolation_image.sh``). Those
directories live in the per-session qcow2 overlay, so the update is recreated
per session and thrown away with the VM.

**A guest reinstall never touches the host.** This is not a convention, it is
the resource model: the only host directory a session VM ever receives is the
single virtiofs workspace export (``/workspace``, compiled by
:func:`factory.core.isolation.workspace.resolve_workspace_export`), and the host
runtime's uv roots can never be part of it. The resolver calls
:func:`assert_export_excludes_host_roots`, which refuses any export whose host
path overlaps a host uv root - including one smuggled in as an extra export -
with the stable reason ``export_contains_host_uv_root:<path>``. The guest has no
other host mount, no host sockets and no host bus (todos 15/32), so there is no
second route by which a guest ``uv`` could reach the host roots.

**Host runtime update (typed broker op only).** Updating the installed tool on
the host is a privileged host action and happens only through the typed
``runtime.update`` broker op from todo 19/20
(:mod:`factory.core.isolation.host_actions`): host policy decides, an explicit
approval is required, the decision and the outcome are audited, and the action
runs the host-configured safe update script with a fixed argv. No guest shell
text, argv, path or unit name can be expressed on that interface - the argument
schema of the op is empty and unknown ops are denied. :func:`host_update_request`
builds the only legal request frame; a guest cannot reach the host update any
other way.

Structural assertions in this module:

* :func:`build_self_update_split` - purely computes both write sets from a guest
  and a host environment and reports whether the guest reinstall is confined to
  the guest's own roots and disjoint from every host root;
* :func:`assert_guest_reinstall_confined` - fail-closed form: raises
  :class:`IsolationError` for a missing/leaked guest home
  (``guest_home_missing`` / ``guest_home_is_host_home``), a guest root outside
  the guest home (``guest_root_outside_guest_home:<root>``) or a guest root
  that overlaps a host root (``guest_root_overlaps_host_root:<root>``);
* :func:`assert_export_excludes_host_roots` - production guard called by the
  workspace resolver (``export_contains_host_uv_root:<path>``).

Host uv roots are resolved from the host environment (with any ``HOME``
override the caller supplies); guest roots are guest-namespace strings, so they
are normalized but deliberately never realpath'd on the host.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ..bash_scope_guard import tool_reinstall_roots
from .errors import IsolationError
from .host_escape import is_within

__all__ = [
    "GUEST_REINSTALL_ARGV",
    "HOST_UPDATE_OP",
    "REASON_GUEST_HOME_IS_HOST_HOME",
    "REASON_GUEST_HOME_MISSING",
    "REASON_GUEST_ROOT_OUTSIDE_HOME",
    "REASON_GUEST_ROOT_OVERLAPS_HOST",
    "REASON_HOST_HOME_MISSING",
    "REASON_HOST_ROOT_EXPORTED",
    "SelfUpdateSplit",
    "assert_export_excludes_host_roots",
    "assert_guest_reinstall_confined",
    "build_self_update_split",
    "guest_uv_roots",
    "host_update_request",
    "host_uv_roots",
]

#: The command the agent runs for an in-guest environment update.
GUEST_REINSTALL_ARGV: Final[tuple[str, ...]] = (
    "uv",
    "tool",
    "install",
    "--force",
    "--reinstall",
    ".",
)
#: The only host operation that may update the HOST runtime (todo 19/20).
HOST_UPDATE_OP: Final[str] = "runtime.update"

REASON_GUEST_HOME_MISSING: Final[str] = "guest_home_missing"
REASON_HOST_HOME_MISSING: Final[str] = "host_home_missing"
REASON_GUEST_HOME_IS_HOST_HOME: Final[str] = "guest_home_is_host_home"
REASON_GUEST_ROOT_OUTSIDE_HOME: Final[str] = "guest_root_outside_guest_home"
REASON_GUEST_ROOT_OVERLAPS_HOST: Final[str] = "guest_root_overlaps_host_root"
REASON_HOST_ROOT_EXPORTED: Final[str] = "export_contains_host_uv_root"


@dataclass(frozen=True, slots=True)
class SelfUpdateSplit:
    """The audited split between the two self-update paths.

    ``confined`` is the verdict: ``True`` only when every guest uv root lives
    under the guest's own home and overlaps no host uv root. ``reason`` carries
    the stable refusal code (empty when confined).
    """

    guest_home: str
    guest_roots: tuple[str, ...]
    host_home: str
    host_roots: tuple[str, ...]
    reason: str = ""

    @property
    def confined(self) -> bool:
        return not self.reason


def host_uv_roots(
    *,
    home: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """The HOST runtime's uv write roots as realpaths on the host.

    ``home`` overrides the ``HOME`` the defaults resolve against (the ambient
    process environment is used when omitted); explicit ``UV_*``/XDG overrides
    in ``env`` keep winning, exactly as uv and the bash scope guard resolve them.
    """
    source: dict[str, str] = dict(os.environ if env is None else env)
    if home is not None:
        source["HOME"] = str(home)
    return tuple(os.path.realpath(path) for path in tool_reinstall_roots(source))


def guest_uv_roots(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The GUEST environment's uv write roots, from the guest process env.

    These are guest-namespace paths (inside the session VM's overlay), so they
    are normalized but never resolved against the host filesystem.
    """
    source: dict[str, str] = dict(os.environ if env is None else env)
    home = _home_of(source)
    return tuple(
        os.path.normpath(_expand_home(path, home)) for path in tool_reinstall_roots(source)
    )


def build_self_update_split(
    *, guest_env: Mapping[str, str], host_env: Mapping[str, str]
) -> SelfUpdateSplit:
    """Compute both write sets and report whether the guest path is confined."""
    guest_home = _home_of(guest_env)
    host_home = _home_of(host_env)
    guest_roots = guest_uv_roots(guest_env)
    host_roots = host_uv_roots(env=host_env)
    reason = ""
    if not guest_home:
        reason = REASON_GUEST_HOME_MISSING
    elif not host_home:
        reason = REASON_HOST_HOME_MISSING
    elif guest_home == os.path.normpath(host_home):
        reason = REASON_GUEST_HOME_IS_HOST_HOME
    else:
        for root in guest_roots:
            if any(_overlaps(root, host_root) for host_root in host_roots):
                reason = f"{REASON_GUEST_ROOT_OVERLAPS_HOST}:{root}"
                break
            if not is_within(root, guest_home):
                reason = f"{REASON_GUEST_ROOT_OUTSIDE_HOME}:{root}"
                break
    return SelfUpdateSplit(
        guest_home=guest_home,
        guest_roots=guest_roots,
        host_home=host_home,
        host_roots=host_roots,
        reason=reason,
    )


def assert_guest_reinstall_confined(
    *, guest_env: Mapping[str, str], host_env: Mapping[str, str]
) -> SelfUpdateSplit:
    """Fail-closed form of :func:`build_self_update_split`.

    Raises :class:`IsolationError` with the split's stable code when the guest
    reinstall is not provably confined to the guest environment.
    """
    split = build_self_update_split(guest_env=guest_env, host_env=host_env)
    if not split.confined:
        raise IsolationError(
            split.reason,
            "guest roots: "
            + (", ".join(split.guest_roots) or "(none)")
            + "; host roots: "
            + (", ".join(split.host_roots) or "(none)"),
        )
    return split


def assert_export_excludes_host_roots(
    export: Mapping[str, object],
    *,
    home: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Refuse a workspace export that overlaps any host runtime uv root.

    Called by :func:`factory.core.isolation.workspace.resolve_workspace_export`
    before a session VM is defined, so a guest ``uv tool install`` can never
    write through the single workspace export into the host runtime's
    environment. Returns the checked host roots for diagnostics/tests.
    """
    host_roots = host_uv_roots(home=home, env=env)
    for host_path in _exported_host_paths(export):
        real = os.path.realpath(host_path)
        for root in host_roots:
            if _overlaps(real, root):
                raise IsolationError(
                    f"{REASON_HOST_ROOT_EXPORTED}:{real}",
                    f"{real} overlaps host uv root {root}",
                )
    return host_roots


def host_update_request() -> dict[str, object]:
    """The only request frame that may update the HOST runtime.

    The op takes no arguments, so nothing a guest sends can widen it; the
    broker validates this frame against the typed catalog before any host
    action, and the approval gate runs before execution.
    """
    from .broker_ops import BROKER_VERSION

    return {"version": BROKER_VERSION, "op": HOST_UPDATE_OP, "args": {}}


def _exported_host_paths(export: Mapping[str, object]) -> tuple[str, ...]:
    """Every host path the export set would attach (main workspace + extras)."""
    paths: list[str] = []
    main = export.get("host_path")
    if isinstance(main, str) and main:
        paths.append(main)
    extras = export.get("extra_exports")
    if isinstance(extras, list):
        for extra in extras:
            if not isinstance(extra, Mapping):
                continue
            extra_host = extra.get("host_path")
            if isinstance(extra_host, str) and extra_host:
                paths.append(extra_host)
    return tuple(paths)


def _home_of(env: Mapping[str, str]) -> str:
    raw = str(env.get("HOME") or "").strip()
    return os.path.normpath(raw) if raw else ""


def _expand_home(path: str, home: str) -> str:
    """Expand a leading ``~`` against the environment's home, not the process's."""
    text = str(path).strip()
    if text == "~":
        return home
    if text.startswith("~/"):
        return os.path.join(home, text[2:])
    return text


def _overlaps(left: str, right: str) -> bool:
    return is_within(left, right) or is_within(right, left)
