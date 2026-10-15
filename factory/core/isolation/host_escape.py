"""Structural closure of the host control surface for one session VM (todo 15).

The guest must have NO host user D-Bus, NO systemd control socket, NO
``docker.sock``, NO libvirt socket and NO ssh-agent socket, and the documented
``systemd-run --user`` escape (``handoff.md:45-48``: bwrap restricted the file
system but the host user bus stayed reachable) must be impossible.

What is absent from the guest, by construction:

* host user D-Bus - ``/run/user/<uid>/bus`` (the ``systemd-run --user`` door);
* host system D-Bus - ``/run/dbus/system_bus_socket``;
* systemd control paths - ``/run/systemd/private``,
  ``/run/user/<uid>/systemd/private``, ``/run/systemd/system``,
  ``/run/systemd/journal/socket``;
* container control sockets - ``/run/docker.sock``, ``/var/run/docker.sock``,
  ``/run/user/<uid>/podman/podman.sock``, ``/run/podman/podman.sock``;
* libvirt sockets - ``/run/libvirt/libvirt-sock`` and ``-sock-ro``;
* ssh-agent sockets - ``/run/user/<uid>/ssh-agent.socket`` and the
  ``SSH_AUTH_SOCK`` environment name;
* the D-Bus address environment names (``DBUS_SESSION_BUS_ADDRESS``,
  ``DBUS_SYSTEM_BUS_ADDRESS``).

Why ``systemd-run --user`` cannot reach the host:

1. the domain XML binds no guest-reachable host control socket (no
   ``<channel>``/``<hostdev>``, no ``hostfwd``, no unrestricted interface).
   System libvirt uses one exact host-side virtiofs transport socket, which is
   allowlisted separately and is not mounted inside the guest;
2. the only export host path is the resolved workspace realpath: the workspace
   resolver refuses ``/run``, ``$HOME`` and the other protected roots, and this
   module independently re-checks every export path (defense in depth);
3. the in-guest sandbox argv clears the environment and re-adds an explicit name
   allowlist: no D-Bus address and no ``SSH_AUTH_SOCK`` can cross, so even the
   guest's own session bus is not handed to the sandboxed tool.

Enforcement points (the closure is not test-only):

* :func:`factory.core.isolation.domain.build_domain_xml` calls
  :func:`assert_domain_xml_clean` - a domain XML that ever gains a host socket
  binding, a ``/run`` mount or an unrestricted interface is refused before
  libvirt sees it;
* :func:`factory.core.isolation.workspace.resolve_workspace_export` calls
  :func:`assert_export_clean` - an export whose host path is a control path or a
  socket, or whose guest path shadows a guest control root, is refused.

This is the enforcement half: the surface registry plus the structural scanners
and assertions. The auditable view built from the same scanners
(:class:`~factory.core.isolation.guest_contract.GuestContract` with per-surface
verdicts and the ``systemd-run --user`` verdict) lives in
:mod:`factory.core.isolation.guest_contract`, so the report can never disagree
with what is enforced. The module is deliberately a host-side leaf (stdlib +
:mod:`factory.core.isolation.errors`) so ``domain`` and ``workspace`` can import
it without an import cycle.
"""

# allow: SIZE_OK - one closure in one file: the surface/deny table and the
# scanners that enforce it must not drift apart, and the module stays a
# host-side leaf (stdlib + errors) so domain/workspace can import it safely.

from __future__ import annotations

import os
import posixpath
import stat
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from .errors import IsolationError

__all__ = [
    "FORBIDDEN_DEVICE_TAGS",
    "FORBIDDEN_HOST_ROOTS",
    "GUEST_CONTROL_ROOTS",
    "HOST_CONTROL_SURFACES",
    "HOST_ESCAPE_REASON_PREFIX",
    "HostControlSurface",
    "HostEscapeViolation",
    "assert_domain_xml_clean",
    "assert_export_clean",
    "is_within",
    "resolve_surfaces",
    "scan_domain_xml",
    "scan_export",
    "scan_guest_roots",
    "surface_for_path",
]

HOST_ESCAPE_REASON_PREFIX: Final[str] = "host_escape_surface"

# Host roots a domain device/export must never resolve into. ``/var/run`` is the
# traditional alias of ``/run``; both are checked (realpath collapses them).
FORBIDDEN_HOST_ROOTS: Final[tuple[str, ...]] = (
    "/run",
    "/var/run",
    "/proc",
    "/sys",
    "/dev",
)
# Guest-side roots a mount root or an extra export guest path must never shadow.
GUEST_CONTROL_ROOTS: Final[tuple[str, ...]] = (
    "/run",
    "/var/run",
    "/proc",
    "/sys",
    "/dev",
)
# Device elements that bind a host resource into the guest. ``filesystem`` is
# checked separately (exactly the allow-listed export dirs); ``interface`` is
# allowed only as the restricted QEMU user-mode device.
FORBIDDEN_DEVICE_TAGS: Final[tuple[str, ...]] = (
    "hostdev",
    "channel",
    "redirdev",
    "smartcard",
    "rng",
    "tpm",
    "shmem",
    "audio",
    "graphics",
    "input",
)


@dataclass(frozen=True, slots=True)
class HostControlSurface:
    """One named host control surface the guest must not reach.

    ``path`` may carry a ``{uid}`` template resolved by :func:`resolve_surfaces`;
    env-carried surfaces (``DBUS_SESSION_BUS_ADDRESS``, ``SSH_AUTH_SOCK``) carry
    ``env_name`` instead of a path.
    """

    key: str
    family: str
    kind: str  # "socket" | "dir" | "env"
    path: str = ""
    env_name: str = ""


HOST_CONTROL_SURFACES: Final[tuple[HostControlSurface, ...]] = (
    HostControlSurface("host_user_dbus", "dbus", "socket", "/run/user/{uid}/bus"),
    HostControlSurface("host_system_dbus", "dbus", "socket", "/run/dbus/system_bus_socket"),
    HostControlSurface("host_dbus_session_env", "dbus", "env", env_name="DBUS_SESSION_BUS_ADDRESS"),
    HostControlSurface("host_dbus_system_env", "dbus", "env", env_name="DBUS_SYSTEM_BUS_ADDRESS"),
    HostControlSurface("host_systemd_private", "systemd", "socket", "/run/systemd/private"),
    HostControlSurface(
        "host_systemd_user_private", "systemd", "socket", "/run/user/{uid}/systemd/private"
    ),
    HostControlSurface("host_systemd_system_dir", "systemd", "dir", "/run/systemd/system"),
    HostControlSurface(
        "host_journald_socket", "systemd", "socket", "/run/systemd/journal/socket"
    ),
    HostControlSurface("host_docker_socket", "container", "socket", "/run/docker.sock"),
    HostControlSurface(
        "host_docker_socket_var_run", "container", "socket", "/var/run/docker.sock"
    ),
    HostControlSurface(
        "host_podman_socket", "container", "socket", "/run/user/{uid}/podman/podman.sock"
    ),
    HostControlSurface(
        "host_podman_socket_rootful", "container", "socket", "/run/podman/podman.sock"
    ),
    HostControlSurface("host_libvirt_socket", "libvirt", "socket", "/run/libvirt/libvirt-sock"),
    HostControlSurface(
        "host_libvirt_socket_ro", "libvirt", "socket", "/run/libvirt/libvirt-sock-ro"
    ),
    HostControlSurface(
        "host_ssh_agent_socket", "ssh_agent", "socket", "/run/user/{uid}/ssh-agent.socket"
    ),
    HostControlSurface("host_ssh_agent_env", "ssh_agent", "env", env_name="SSH_AUTH_SOCK"),
)


@dataclass(frozen=True, slots=True)
class HostEscapeViolation:
    """One structural violation; ``code`` is stable and payload-free."""

    code: str
    where: str  # domain_xml | export | guest_roots
    detail: str = ""


def resolve_surfaces(uid: int | None = None) -> tuple[HostControlSurface, ...]:
    """The named surfaces with ``{uid}`` resolved (default: the current host uid)."""
    resolved_uid = os.getuid() if uid is None else int(uid)
    return tuple(
        replace(surface, path=surface.path.format(uid=resolved_uid))
        for surface in HOST_CONTROL_SURFACES
    )


def surface_for_path(path: str, uid: int | None = None) -> HostControlSurface | None:
    """The surface a host path names, matched on the raw and the realpath."""
    real = os.path.realpath(path)
    for surface in resolve_surfaces(uid):
        if surface.path and (surface.path == path or os.path.realpath(surface.path) == real):
            return surface
    return None


def is_within(path: str, root: str) -> bool:
    """True when ``path`` equals ``root`` or lives below it (string containment)."""
    if not path or not root:
        return False
    if path == root:
        return True
    return path.startswith(root.rstrip("/") + "/")


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _forbidden_host_root(path: str) -> str | None:
    real = os.path.realpath(path)
    for root in FORBIDDEN_HOST_ROOTS:
        root_real = os.path.realpath(root)
        if real == root_real or is_within(real, root_real):
            return root
    return None


def _guest_control_root(path: str) -> str | None:
    normalized = posixpath.normpath(path)
    for root in GUEST_CONTROL_ROOTS:
        if is_within(normalized, root):
            return root
    return None


def _path_bound_violation(path: str, where: str) -> HostEscapeViolation | None:
    """A host path used by the domain/export must not name a control surface."""
    surface = surface_for_path(path)
    if surface is not None:
        return HostEscapeViolation("host_control_surface", where, f"{surface.key} ({path})")
    root = _forbidden_host_root(path)
    if root is not None:
        return HostEscapeViolation("host_control_path", where, f"{path} under {root}")
    return None


def scan_domain_xml(
    xml: str, *, allowed_export_paths: Sequence[str] = (), allowed_virtiofs_socket_paths: Sequence[str] = ()
) -> tuple[HostEscapeViolation, ...]:
    """Every structural way a domain XML could bind a host control surface."""
    violations: list[HostEscapeViolation] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return (HostEscapeViolation("xml_unparseable", "domain_xml", str(exc)),)
    for tag in FORBIDDEN_DEVICE_TAGS:
        for _element in root.iter(tag):
            violations.append(
                HostEscapeViolation(
                    "host_binding", "domain_xml", f"<{tag}> binds a host resource"
                )
            )
    for source in root.iter("source"):
        for value in sorted(source.attrib.values()):
            if not value.startswith("/"):
                continue
            violation = _path_bound_violation(value, "domain_xml")
            if violation is not None:
                violations.append(violation)
    allowed = {os.path.realpath(path) for path in allowed_export_paths}
    allowed_sockets = {os.path.realpath(path) for path in allowed_virtiofs_socket_paths}
    for filesystem in root.iter("filesystem"):
        source = filesystem.find("source")
        source_dir = _text(source.get("dir")) if source is not None else ""
        source_socket = _text(source.get("socket")) if source is not None else ""
        if source_socket:
            if (
                os.path.realpath(source_socket) not in allowed_sockets
                or set(source.attrib) != {"socket"}
                or filesystem.find("binary") is not None
                or filesystem.find("idmap") is not None
            ):
                violations.append(
                    HostEscapeViolation("filesystem_socket_not_allowed", "domain_xml", source_socket)
                )
            continue
        if not source_dir:
            violations.append(
                HostEscapeViolation(
                    "filesystem_export_missing_source",
                    "domain_xml",
                    ET.tostring(filesystem, encoding="unicode"),
                )
            )
            continue
        if os.path.realpath(source_dir) not in allowed:
            violations.append(
                HostEscapeViolation("filesystem_export_not_allowed", "domain_xml", source_dir)
            )
    for interface in root.iter("interface"):
        interface_type = _text(interface.get("type"))
        if interface_type != "user":
            violations.append(
                HostEscapeViolation(
                    "network_interface_not_restricted",
                    "domain_xml",
                    f"interface type={interface_type!r} (only the QEMU user-mode device is allowed)",
                )
            )
    for element in root.iter():
        if _text(element.get("accessmode")) == "passthrough":
            violations.append(
                HostEscapeViolation(
                    "virtiofs_accessmode_passthrough",
                    "domain_xml",
                    f"<{element.tag} accessmode=passthrough>",
                )
            )
    if "hostfwd" in xml:
        violations.append(HostEscapeViolation("host_forward", "domain_xml", "hostfwd"))
    return tuple(violations)


def scan_export(export: Mapping[str, object]) -> tuple[HostEscapeViolation, ...]:
    """Every structural way an export set could carry a host control surface."""
    violations: list[HostEscapeViolation] = []
    host = _text(export.get("host_path"))
    guest = _text(export.get("guest_path"))
    if not host or not os.path.isabs(host):
        violations.append(HostEscapeViolation("invalid_export_host_path", "export", repr(host)))
    else:
        violations.extend(_export_host_violations(host))
    violations.extend(_export_guest_violations(guest))
    sandbox = _text(export.get("sandbox"))
    if sandbox != "namespace":
        violations.append(
            HostEscapeViolation(
                "export_sandbox_not_namespace", "export", repr(sandbox or "unset")
            )
        )
    extras = export.get("extra_exports")
    if isinstance(extras, list):
        for extra in extras:
            if not isinstance(extra, Mapping):
                violations.append(
                    HostEscapeViolation("invalid_extra_export", "export", repr(extra))
                )
                continue
            extra_host = _text(extra.get("host_path"))
            if not extra_host or not os.path.isabs(extra_host):
                violations.append(
                    HostEscapeViolation("invalid_export_host_path", "export", repr(extra_host))
                )
            else:
                violations.extend(_export_host_violations(extra_host))
            violations.extend(_export_guest_violations(_text(extra.get("guest_path"))))
    return tuple(violations)


def _export_host_violations(host_path: str) -> list[HostEscapeViolation]:
    violations: list[HostEscapeViolation] = []
    bound = _path_bound_violation(host_path, "export")
    if bound is not None:
        violations.append(HostEscapeViolation("export_host_path_control", "export", bound.detail))
    real = os.path.realpath(host_path)
    try:
        mode = os.stat(real).st_mode
    except OSError as exc:
        violations.append(
            HostEscapeViolation("export_host_path_missing", "export", f"{real}: {exc}")
        )
        return violations
    if stat.S_ISSOCK(mode):
        violations.append(HostEscapeViolation("export_host_path_socket", "export", real))
    elif not stat.S_ISDIR(mode):
        violations.append(HostEscapeViolation("export_host_path_not_directory", "export", real))
    return violations


def _export_guest_violations(guest_path: str) -> list[HostEscapeViolation]:
    if not guest_path:
        return [HostEscapeViolation("invalid_export_guest_path", "export", repr(guest_path))]
    if not guest_path.startswith("/"):
        return [HostEscapeViolation("invalid_export_guest_path", "export", guest_path)]
    root = _guest_control_root(guest_path)
    if root is not None:
        return [
            HostEscapeViolation(
                "export_guest_path_control", "export", f"{guest_path} shadows {root}"
            )
        ]
    return []


def scan_guest_roots(roots: Iterable[str]) -> tuple[HostEscapeViolation, ...]:
    """No guest root (mount path) may shadow a guest control filesystem."""
    violations: list[HostEscapeViolation] = []
    for root in roots:
        text = str(root)
        control = _guest_control_root(text)
        if control is not None:
            violations.append(
                HostEscapeViolation(
                    "guest_root_control", "guest_roots", f"{text} shadows {control}"
                )
            )
    return tuple(violations)


def _refuse(violations: Sequence[HostEscapeViolation]) -> None:
    if not violations:
        return
    raise IsolationError(
        f"{HOST_ESCAPE_REASON_PREFIX}:{violations[0].code}",
        "; ".join(f"{violation.code}@{violation.where}" for violation in violations),
    )


def assert_domain_xml_clean(
    xml: str, *, allowed_export_paths: Sequence[str] = (), allowed_virtiofs_socket_paths: Sequence[str] = ()
) -> None:
    """Called by ``domain.build_domain_xml`` before libvirt ever sees the XML."""
    _refuse(scan_domain_xml(
        xml,
        allowed_export_paths=allowed_export_paths,
        allowed_virtiofs_socket_paths=allowed_virtiofs_socket_paths,
    ))


def assert_export_clean(export: Mapping[str, object]) -> None:
    """Called by ``workspace.resolve_workspace_export`` before the spec is returned."""
    _refuse(scan_export(export))
