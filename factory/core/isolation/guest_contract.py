"""The auditable guest contract: per-surface verdicts for one session VM (todo 15).

:class:`GuestContract` collects the raw artifacts of one session boundary (the
generated domain XML, the workspace export set, the guest roots, the in-guest
sandbox environment names) and :func:`audit_guest_contract` turns them into a
:class:`ContractAudit`: one :class:`SurfaceVerdict` per named host control
surface plus the :class:`SystemdRunVerdict` for the documented
``systemd-run --user`` escape. :func:`render_contract` prints the contract for
manual QA and the PoC report.

The verdicts are computed from the same scanners
:mod:`factory.core.isolation.host_escape` enforces with, so the report can never
disagree with what the generators refuse. Every flag of every verdict must be
``False``; a single open surface is a violation and :func:`assert_no_host_escape`
raises the canonical :class:`IsolationError` with reason
``host_escape_surface:<first-code>``.

The ``systemd-run --user`` verdict is closed only when the host user bus
(``/run/user/<uid>/bus``) is neither bound into the domain XML, nor covered by
an export host path, nor passed as ``DBUS_SESSION_BUS_ADDRESS`` in the sandbox
environment names - that is exactly the structural reason the escape documented
in ``handoff.md:45-48`` cannot reach the host.
"""

# allow: SIZE_OK - one auditable view: the verdict model, the systemd-run
# closure and the report renderer describe the same contract; splitting them
# would let the printed report drift from the audit it claims to show.

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from .errors import IsolationError
from .guest_bwrap import guest_sandbox_env_argv
from .host_escape import (
    HOST_ESCAPE_REASON_PREFIX,
    HostControlSurface,
    HostEscapeViolation,
    is_within,
    resolve_surfaces,
    scan_domain_xml,
    scan_export,
    scan_guest_roots,
)

__all__ = [
    "SYSTEMD_RUN_COMMAND",
    "ContractAudit",
    "GuestContract",
    "SurfaceVerdict",
    "SystemdRunVerdict",
    "assert_no_host_escape",
    "audit_guest_contract",
    "build_guest_contract",
    "render_contract",
]

SYSTEMD_RUN_COMMAND: Final[str] = "systemd-run --user"


@dataclass(frozen=True, slots=True)
class GuestContract:
    """The raw facts of one session's guest boundary (pre-audit)."""

    session_id: str
    domain_xml: str
    export: Mapping[str, object]
    guest_roots: tuple[str, ...]
    env_names: tuple[str, ...]
    uid: int
    domain_name: str = ""
    allowed_virtiofs_socket_paths: tuple[str, ...] = ()

    def export_host_paths(self) -> tuple[str, ...]:
        """Realpaths of every host path the export set would attach."""
        paths: list[str] = []
        host = _text(self.export.get("host_path"))
        if host:
            paths.append(os.path.realpath(host))
        extras = self.export.get("extra_exports")
        if isinstance(extras, list):
            for extra in extras:
                if not isinstance(extra, Mapping):
                    continue
                extra_host = _text(extra.get("host_path"))
                if extra_host:
                    paths.append(os.path.realpath(extra_host))
        return tuple(paths)


@dataclass(frozen=True, slots=True)
class SurfaceVerdict:
    """Per-artifact verdict for one surface (each flag must be ``False``)."""

    surface: HostControlSurface
    path: str
    in_domain_xml: bool
    exported: bool
    in_guest_roots: bool
    in_env: bool

    @property
    def closed(self) -> bool:
        return not (self.in_domain_xml or self.exported or self.in_guest_roots or self.in_env)

    @property
    def exposed_via(self) -> tuple[str, ...]:
        return tuple(
            where
            for where, flag in (
                ("domain_xml", self.in_domain_xml),
                ("export", self.exported),
                ("guest_roots", self.in_guest_roots),
                ("env", self.in_env),
            )
            if flag
        )

    @property
    def detail(self) -> str:
        label = self.path or self.surface.env_name
        if self.closed:
            return f"absent/refused ({label})"
        return f"EXPOSED via {', '.join(self.exposed_via)} ({label})"


@dataclass(frozen=True, slots=True)
class SystemdRunVerdict:
    """Whether ``systemd-run --user`` can reach the HOST user manager."""

    user_bus_path: str
    host_bus_bound: bool
    host_bus_exported: bool
    host_bus_in_env: bool

    @property
    def host_reach(self) -> str:
        if self.host_bus_bound or self.host_bus_exported or self.host_bus_in_env:
            return "host_user_bus"
        return "none"

    @property
    def closed(self) -> bool:
        return self.host_reach == "none"

    @property
    def reason(self) -> str:
        if self.closed:
            return (
                f"host user bus {self.user_bus_path} is not bound, not exported "
                "and not passed in the environment"
            )
        return f"host user bus {self.user_bus_path} is reachable ({self.host_reach})"


@dataclass(frozen=True, slots=True)
class ContractAudit:
    """The audited view: verdicts plus every violation found."""

    verdicts: tuple[SurfaceVerdict, ...]
    systemd_run: SystemdRunVerdict
    violations: tuple[HostEscapeViolation, ...]

    @property
    def closed(self) -> bool:
        return not self.violations

    @property
    def exposed_control_paths(self) -> tuple[str, ...]:
        return tuple(
            verdict.path or verdict.surface.env_name
            for verdict in self.verdicts
            if not verdict.closed
        )

    def surface(self, key: str) -> SurfaceVerdict | None:
        for verdict in self.verdicts:
            if verdict.surface.key == key:
                return verdict
        return None


def build_guest_contract(
    *,
    session_id: str,
    domain_xml: str,
    export: Mapping[str, object],
    guest_roots: Sequence[str],
    env: Mapping[str, str] | None = None,
    uid: int | None = None,
    domain_name: str = "",
    allowed_virtiofs_socket_paths: Sequence[str] = (),
) -> GuestContract:
    """Assemble the contract from the real artifacts (XML, export, roots, env).

    The keyword list mirrors :class:`GuestContract`'s fields one-for-one: these
    are the raw artifacts of a single boundary, so a wrapper value object would
    only rename them. The environment names are read back from the in-guest
    sandbox argv builder
    (:func:`factory.core.isolation.guest_bwrap.guest_sandbox_env_argv`), so the
    contract always lists exactly what the guest sandbox would forward - never a
    hand-maintained copy.
    """
    source_env = os.environ if env is None else env
    return GuestContract(
        session_id=str(session_id),
        domain_xml=str(domain_xml),
        export=dict(export),
        guest_roots=tuple(str(root) for root in guest_roots),
        env_names=_env_names_from_argv(guest_sandbox_env_argv(source_env)),
        uid=os.getuid() if uid is None else int(uid),
        domain_name=str(domain_name),
        allowed_virtiofs_socket_paths=tuple(str(path) for path in allowed_virtiofs_socket_paths),
    )


def _env_names_from_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """The ``--setenv NAME VALUE`` names of a bwrap argv, in order."""
    names: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] == "--setenv" and index + 2 < len(argv):
            names.append(argv[index + 1])
            index += 3
        else:
            index += 1
    return tuple(names)


def audit_guest_contract(contract: GuestContract) -> ContractAudit:
    """Audit the contract; every open surface becomes a violation."""
    export_paths = contract.export_host_paths()
    violations: list[HostEscapeViolation] = list(
        scan_domain_xml(
            contract.domain_xml,
            allowed_export_paths=export_paths,
            allowed_virtiofs_socket_paths=contract.allowed_virtiofs_socket_paths,
        )
    )
    violations.extend(scan_export(contract.export))
    violations.extend(scan_guest_roots(contract.guest_roots))
    verdicts = tuple(
        _surface_verdict(surface, contract, export_paths)
        for surface in resolve_surfaces(contract.uid)
    )
    for verdict in verdicts:
        if not verdict.closed:
            violations.append(
                HostEscapeViolation(
                    "host_control_surface", "surface", f"{verdict.surface.key}: {verdict.detail}"
                )
            )
    systemd = _systemd_run_verdict(contract, export_paths)
    if not systemd.closed:
        violations.append(
            HostEscapeViolation("systemd_run_reachable", "systemd_run", systemd.reason)
        )
    return ContractAudit(verdicts=verdicts, systemd_run=systemd, violations=tuple(violations))


def _surface_verdict(
    surface: HostControlSurface, contract: GuestContract, export_paths: Sequence[str]
) -> SurfaceVerdict:
    in_env = bool(surface.env_name) and surface.env_name in contract.env_names
    if not surface.path:
        return SurfaceVerdict(
            surface=surface,
            path="",
            in_domain_xml=False,
            exported=False,
            in_guest_roots=False,
            in_env=in_env,
        )
    return SurfaceVerdict(
        surface=surface,
        path=surface.path,
        in_domain_xml=surface.path in contract.domain_xml,
        exported=any(is_within(surface.path, host) for host in export_paths),
        in_guest_roots=any(is_within(surface.path, root) for root in contract.guest_roots),
        in_env=in_env,
    )


def _systemd_run_verdict(
    contract: GuestContract, export_paths: Sequence[str]
) -> SystemdRunVerdict:
    bus = next(
        surface.path
        for surface in resolve_surfaces(contract.uid)
        if surface.key == "host_user_dbus"
    )
    return SystemdRunVerdict(
        user_bus_path=bus,
        host_bus_bound=bus in contract.domain_xml,
        host_bus_exported=any(is_within(bus, host) for host in export_paths),
        host_bus_in_env="DBUS_SESSION_BUS_ADDRESS" in contract.env_names,
    )


def assert_no_host_escape(contract: GuestContract) -> ContractAudit:
    """Audit and refuse with a canonical :class:`IsolationError` on any violation."""
    audit = audit_guest_contract(contract)
    if audit.violations:
        raise IsolationError(
            f"{HOST_ESCAPE_REASON_PREFIX}:{audit.violations[0].code}",
            "; ".join(f"{violation.code}@{violation.where}" for violation in audit.violations),
        )
    return audit


def render_contract(contract: GuestContract) -> str:
    """The printable contract (manual QA + PoC report); one line per surface."""
    audit = audit_guest_contract(contract)
    export = contract.export
    extras = export.get("extra_exports")
    extra_count = len(extras) if isinstance(extras, list) else 0
    lines = [
        f"GUEST CONTRACT session={contract.session_id} "
        f"domain={contract.domain_name or '-'} uid={contract.uid}",
        "host_control_surfaces:",
    ]
    for verdict in audit.verdicts:
        state = "yes" if verdict.closed else "no"
        label = verdict.path or verdict.surface.env_name
        lines.append(
            f"  {verdict.surface.key} {label}: closed={state} "
            f"in_domain_xml={'yes' if verdict.in_domain_xml else 'no'} "
            f"exported={'yes' if verdict.exported else 'no'} "
            f"in_guest_roots={'yes' if verdict.in_guest_roots else 'no'} "
            f"in_env={'yes' if verdict.in_env else 'no'}"
        )
    systemd = audit.systemd_run
    lines.append(
        f"{SYSTEMD_RUN_COMMAND}: closed={'yes' if systemd.closed else 'no'} "
        f"host_reach={systemd.host_reach} ({systemd.reason})"
    )
    lines.append(
        f"exports: {export.get('host_path')} -> {export.get('guest_path')} "
        f"(sandbox={export.get('sandbox')}, readonly={export.get('readonly')}, "
        f"extras={extra_count})"
    )
    lines.append(f"guest_roots: {', '.join(contract.guest_roots)}")
    lines.append(f"env_allowlist: {', '.join(contract.env_names)}")
    if audit.violations:
        lines.append(
            "violations: "
            + ", ".join(f"{violation.code}@{violation.where}" for violation in audit.violations)
        )
    else:
        lines.append("violations: none")
    return "\n".join(lines)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
