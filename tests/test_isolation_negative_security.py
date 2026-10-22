#!/usr/bin/env python3
"""Negative tests for the session isolation boundary.

One probe and verdict for each boundary item:
 1. ``read_host_home``: read host ``$HOME``;
 2. ``write_host_home``: write host ``$HOME``;
 3. ``reach_ssh``: access host ``~/.ssh``;
 4. ``reach_host_systemd``: access the host systemd manager;
 5. ``create_host_transient_unit``: create a host transient unit;
 6. ``reach_host_dbus``: access host D-Bus;
 7. ``see_host_processes``: see host processes;
 8. ``reach_control_sockets``: access docker/libvirt/ssh-agent sockets;
 9. ``mount_extra_host_paths``: mount additional host paths;
10. ``affect_another_agent_vm``: affect another agent's VM.

Each surface must be denied or absent; only the explicitly exported workspace
is allowed. Structural tests exercise shipped ``host_escape``, ``workspace``,
``guest_task``, ``channel``, ``network`` and ``export_guard`` rules, not prompt
wording. Each probe includes a non-vacuity case: the same guard must reject a
forged artifact carrying the forbidden surface.

Reporting rules:
* Every item has its own ``PASS``/``FAIL``/``SKIP`` and individual failure detail.
* Live checks use real KVM session VMs and an availability probe. Missing KVM
  yields ``SKIP`` with a concrete reason and :class:`unittest.SkipTest`.
* Missing prerequisites are a skip; a report failing its tamper check is a fail.
* Read guest-writable reports through
  :mod:`factory.core.isolation.probe_verification`: a host-issued, single-use
  nonce, freshness against host artifact mtime and host observations that the
  guest cannot fabricate. Reject forged reports with a stable reason.

Run: ``./.venv/bin/python -u -m pytest tests/test_isolation_negative_security.py -q -rs``.

# allow: SIZE_OK - the structural probes, live plans and host-observed verdicts
# form one checklist; keep each assertion beside the boundary it verifies.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Final, Protocol

REPO_ROOT = Path(os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation import broker_ops  # noqa: E402
from factory.core.isolation import broker_seam  # noqa: E402
from factory.core.isolation import channel  # noqa: E402
from factory.core.isolation import domain as domain_module  # noqa: E402
from factory.core.isolation import guest_bwrap  # noqa: E402
from factory.core.isolation import guest_contract  # noqa: E402
from factory.core.isolation import host_escape  # noqa: E402
from factory.core.isolation import network  # noqa: E402
from factory.core.isolation import probe_verification  # noqa: E402
from factory.core.isolation.channel import (  # noqa: E402
    ChannelConfig,
    ChannelServer,
    MessageKind,
    PeerCid,
    SessionId,
    SessionMap,
)
from factory.core.isolation.domain import (  # noqa: E402
    DomainSpec,
    build_domain_xml,
    domain_name,
    overlay_path,
)
from factory.core.isolation.errors import IsolationError  # noqa: E402
from factory.core.isolation.export_guard import WorkspaceExportGuard  # noqa: E402
from factory.core.isolation.guest_task import DEFAULT_GUEST_ROOTS  # noqa: E402
from factory.core.isolation.workspace import (  # noqa: E402
    ExtraExport,
    WorkspacePolicy,
    resolve_workspace_export,
)

# --------------------------------------------------------------------------- #
# Negative acceptance checklist, in order
# --------------------------------------------------------------------------- #

MODE_STRUCTURAL: Final = "structural"
MODE_LIVE: Final = "live"

STATUS_PASS: Final = "PASS"
STATUS_FAIL: Final = "FAIL"
STATUS_SKIP: Final = "SKIP"


@dataclass(frozen=True, slots=True)
class ProbeItem:
    """One acceptance checklist item: a stable key and the requirement it verifies."""

    key: str
    tz: str


PROBE_ITEMS: Final[tuple[ProbeItem, ...]] = (
    ProbeItem("read_host_home", "read host $HOME"),
    ProbeItem("write_host_home", "write host $HOME"),
    ProbeItem("reach_ssh", "reach host ~/.ssh"),
    ProbeItem("reach_host_systemd", "reach host systemd"),
    ProbeItem("create_host_transient_unit", "create a host transient unit"),
    ProbeItem("reach_host_dbus", "reach host D-Bus"),
    ProbeItem("see_host_processes", "see host processes"),
    ProbeItem("reach_control_sockets", "reach control sockets (docker/libvirt/ssh-agent)"),
    ProbeItem("mount_extra_host_paths", "mount extra host paths"),
    ProbeItem("affect_another_agent_vm", "affect another agent VM"),
)
PROBE_KEYS: Final[tuple[str, ...]] = tuple(item.key for item in PROBE_ITEMS)

#: Host-side marker names/sockets the checklist is about (never prompt text).
CONTROL_SOCKET_PATHS: Final[tuple[str, ...]] = (
    "/run/docker.sock",
    "/var/run/docker.sock",
    "/run/libvirt/libvirt-sock",
    "/run/libvirt/libvirt-sock-ro",
    "/run/podman/podman.sock",
)

#: The device tags a session domain may carry: its disposable disk, the single
#: workspace export, the vsock device and the one restricted user-mode NIC.
ALLOWED_DEVICE_TAGS: Final[frozenset[str]] = frozenset(
    {"disk", "filesystem", "vsock", "interface"}
)


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """One probe verdict; a FAIL is loud and names the item it belongs to."""

    key: str
    mode: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_PASS


def outcome_pass(key: str, mode: str, detail: str) -> ProbeOutcome:
    return ProbeOutcome(key, mode, STATUS_PASS, detail)


def outcome_fail(key: str, mode: str, detail: str) -> ProbeOutcome:
    return ProbeOutcome(key, mode, STATUS_FAIL, detail)


def render_outcomes(outcomes: Sequence[ProbeOutcome]) -> list[str]:
    """One line per item; never an aggregate summary that can hide a failure."""
    return [
        f"PROBE {item.mode}/{item.key}: {item.status}"
        + (f" - {item.detail}" if item.detail else "")
        for item in outcomes
    ]


# --------------------------------------------------------------------------- #
# Availability probe (the honesty gate)
# --------------------------------------------------------------------------- #

KVM_AVAILABLE_ENV: Final = "KVM_AVAILABLE"
KVM_AVAILABLE_TRUE: Final = frozenset({"1", "true", "yes", "on", "kvm"})


def kvm_opt_in_requested(env: Mapping[str, str]) -> bool:
    return str(env.get(KVM_AVAILABLE_ENV, "")).strip().lower() in KVM_AVAILABLE_TRUE


def live_probe_skip_reason(env: Mapping[str, str]) -> str | None:
    """``None`` when a real VM run is possible, else the concrete skip reason."""
    if not kvm_opt_in_requested(env):
        return f"{KVM_AVAILABLE_ENV} unset (set it to enable the real VM probes)"
    try:
        reason = domain_module.kvm_unavailable_reason()
    except Exception as exc:  # probe boundary: an unusable probe is "not ready"
        return f"capability probe failed: {exc}"
    if reason is not None:
        return reason
    if not isinstance(getattr(socket, "AF_VSOCK", None), int):
        return "AF_VSOCK is not available on this host"
    return None


# --------------------------------------------------------------------------- #
# Structural half: the boundary artifacts and the per-item guards
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BoundaryFacts:
    """The real host-side artifacts one session boundary is built from."""

    root: Path
    workspace: Path
    base_image: Path
    overlay_dir: Path
    home: Path
    data_root: Path
    uid: int
    policy: WorkspacePolicy
    spec: DomainSpec
    export: Mapping[str, object]
    domain_xml: str
    contract: guest_contract.GuestContract
    audit: guest_contract.ContractAudit

    def home_real(self) -> str:
        return os.path.realpath(str(self.home))

    def export_host_paths(self) -> tuple[str, ...]:
        return self.contract.export_host_paths()


def build_boundary_facts(root: Path) -> BoundaryFacts:
    """Build one real boundary (workspace export, domain XML, guest contract)."""
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "project.txt").write_text("workspace\n", encoding="utf-8")
    base_image = root / "base.qcow2"
    base_image.write_bytes(b"qcow2-base")
    overlay_dir = root / "overlays"
    overlay_dir.mkdir()
    home = root / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE-KEY\n", encoding="utf-8")
    data_root = root / "data"
    data_root.mkdir()
    uid = os.getuid()
    policy = WorkspacePolicy(home=home, data_root=data_root, host_uid=uid)
    spec = DomainSpec(
        session_id="negative-security",
        base_image=base_image,
        workspace_host_path=Path(os.path.realpath(str(workspace))),
        overlay_dir=overlay_dir,
        network=True,
    )
    export = resolve_workspace_export(workspace, policy=policy)
    domain_xml = build_domain_xml(spec)
    contract = guest_contract.build_guest_contract(
        session_id=spec.session_id,
        domain_name=domain_name(spec),
        domain_xml=domain_xml,
        export=export,
        guest_roots=DEFAULT_GUEST_ROOTS,
        env=HOSTILE_ENV,
        uid=uid,
    )
    audit = guest_contract.audit_guest_contract(contract)
    return BoundaryFacts(
        root=root,
        workspace=Path(os.path.realpath(str(workspace))),
        base_image=base_image,
        overlay_dir=overlay_dir,
        home=Path(os.path.realpath(str(home))),
        data_root=Path(os.path.realpath(str(data_root))),
        uid=uid,
        policy=policy,
        spec=spec,
        export=export,
        domain_xml=domain_xml,
        contract=contract,
        audit=audit,
    )


#: Ambient environment that DOES carry live host control connections; every one
#: of them must stay out of the guest env allowlist.
HOSTILE_ENV: Final[dict[str, str]] = {
    "HOME": "/home/agent",
    "PATH": "/usr/bin:/bin",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/run/dbus/system_bus_socket",
    "SSH_AUTH_SOCK": "/run/user/1000/ssh-agent.socket",
    "XDG_RUNTIME_DIR": "/run/user/1000",
}


def _refusal(call: Callable[[], object]) -> IsolationError | None:
    """Run ``call`` and return its :class:`IsolationError`, or ``None`` if it passed."""
    try:
        call()
    except IsolationError as exc:
        return exc
    return None


def _is_within(path: str, root: str) -> bool:
    return host_escape.is_within(os.path.realpath(path), os.path.realpath(root))


def _device_tags(xml: str) -> list[str]:
    devices = ET.fromstring(xml).find("devices")
    if devices is None:
        return []
    return [child.tag for child in devices]


def _filesystem_sources(xml: str) -> tuple[str, ...]:
    sources: list[str] = []
    for filesystem in ET.fromstring(xml).iter("filesystem"):
        source = filesystem.find("source")
        if source is not None and source.get("dir"):
            sources.append(str(source.get("dir")))
    return tuple(sources)


def structural_read_host_home(facts: BoundaryFacts) -> ProbeOutcome:
    key = "read_host_home"
    home = os.path.realpath(str(facts.home))
    refusal = _refusal(lambda: resolve_workspace_export(home, policy=facts.policy))
    if refusal is None or not refusal.reason.startswith("sensitive_path_denied"):
        return outcome_fail(key, MODE_STRUCTURAL, "the resolver accepted host $HOME as a workspace")
    for relative in (".ssh", ".config", ".igorek-agent-factory", ".gnupg", ".aws", ".docker", ".kube"):
        target = os.path.join(home, relative)
        denied = _refusal(lambda target=target: resolve_workspace_export(target, policy=facts.policy))
        if denied is None or not denied.reason.startswith("sensitive_path_denied"):
            return outcome_fail(key, MODE_STRUCTURAL, f"host {target} was accepted as a workspace")
    for exported in facts.export_host_paths():
        if exported == home or _is_within(exported, home):
            return outcome_fail(key, MODE_STRUCTURAL, f"host $HOME is exported: {exported}")
    if home in facts.domain_xml:
        return outcome_fail(key, MODE_STRUCTURAL, "host $HOME path appears in the domain XML")
    accepted = resolve_workspace_export(facts.workspace, policy=facts.policy)
    if accepted["host_path"] != str(facts.workspace):
        return outcome_fail(key, MODE_STRUCTURAL, "the resolver no longer accepts a normal workspace")
    return outcome_pass(
        key,
        MODE_STRUCTURAL,
        f"resolver refused $HOME and its sensitive subtrees ({refusal.reason!r}); "
        "no export path is $HOME; a normal workspace is still accepted",
    )


def structural_write_host_home(facts: BoundaryFacts) -> ProbeOutcome:
    key = "write_host_home"
    home = os.path.realpath(str(facts.home))
    refusal = _refusal(lambda: resolve_workspace_export(home, policy=facts.policy))
    if refusal is None or not refusal.reason.startswith("sensitive_path_denied"):
        return outcome_fail(key, MODE_STRUCTURAL, "a writable host $HOME export was accepted")
    if facts.export["extra_exports"] != []:
        return outcome_fail(key, MODE_STRUCTURAL, "implicit extra exports appeared")
    for exported in facts.export_host_paths():
        if exported == home or _is_within(exported, home):
            return outcome_fail(key, MODE_STRUCTURAL, f"host $HOME is exported: {exported}")
    argv = guest_bwrap.guest_sandbox_env_argv(
        {
            "HOME": "/home/agent",
            "IGOREK_SESSION_EMBEDDINGS_API_KEY": "PLANTED-SECRET",
            "UV_INDEX_URL": "https://user:PLANTED-PASSWORD@example.invalid/simple",
        }
    )
    joined = "\0".join(argv)
    for leaked in ("IGOREK_SESSION_EMBEDDINGS_API_KEY", "PLANTED-SECRET", "PLANTED-PASSWORD"):
        if leaked in joined:
            return outcome_fail(key, MODE_STRUCTURAL, f"the guest sandbox env forwards {leaked!r}")
    if "--setenv" not in argv or "HOME" not in argv:
        return outcome_fail(key, MODE_STRUCTURAL, "the documented HOME allowlist entry stopped crossing")
    return outcome_pass(
        key,
        MODE_STRUCTURAL,
        f"resolver refused $HOME ({refusal.reason!r}); one read/write export only; "
        "the guest sandbox env is an exact allowlist (planted secret names stay out)",
    )


def structural_reach_ssh(facts: BoundaryFacts) -> ProbeOutcome:
    key = "reach_ssh"
    ssh_dir = os.path.realpath(str(facts.home / ".ssh"))
    refusal = _refusal(lambda: resolve_workspace_export(ssh_dir, policy=facts.policy))
    if refusal is None or refusal.reason != "sensitive_path_denied:~/.ssh":
        return outcome_fail(key, MODE_STRUCTURAL, "host ~/.ssh was accepted as a workspace")
    nested = _refusal(
        lambda: resolve_workspace_export(ssh_dir + "/id_ed25519", policy=facts.policy)
    )
    if nested is None:
        return outcome_fail(key, MODE_STRUCTURAL, "a file under ~/.ssh was accepted")
    for exported in facts.export_host_paths():
        if _is_within(exported, ssh_dir):
            return outcome_fail(key, MODE_STRUCTURAL, f"~/.ssh is exported: {exported}")
    if ssh_dir in facts.domain_xml or "SSH_AUTH_SOCK" in facts.domain_xml:
        return outcome_fail(key, MODE_STRUCTURAL, "~/.ssh or the agent socket appears in the XML")
    verdict = facts.audit.surface("host_ssh_agent_socket")
    if verdict is None or not verdict.closed:
        return outcome_fail(key, MODE_STRUCTURAL, "the ssh-agent socket surface is not closed")
    link = facts.workspace / "ssh_link"
    try:
        link.symlink_to(ssh_dir)
        escape = _refusal(lambda: resolve_workspace_export(facts.workspace, policy=facts.policy))
    finally:
        link.unlink(missing_ok=True)
    if escape is None or not str(escape.reason).startswith("symlink_escape"):
        return outcome_fail(key, MODE_STRUCTURAL, "a workspace symlink into ~/.ssh was not refused")
    return outcome_pass(
        key,
        MODE_STRUCTURAL,
        f"~/.ssh refused ({refusal.reason!r}); a symlink escape into it refused "
        f"({escape.reason!r}); SSH_AUTH_SOCK never crosses",
    )


def structural_reach_host_systemd(facts: BoundaryFacts) -> ProbeOutcome:
    key = "reach_host_systemd"
    closed = (
        "host_systemd_private",
        "host_systemd_user_private",
        "host_systemd_system_dir",
        "host_journald_socket",
    )
    for surface_key in closed:
        verdict = facts.audit.surface(surface_key)
        if verdict is None or not verdict.closed:
            return outcome_fail(key, MODE_STRUCTURAL, f"surface {surface_key} is not closed")
        label = verdict.path
        if label and label in facts.domain_xml:
            return outcome_fail(key, MODE_STRUCTURAL, f"{label} appears in the domain XML")
    if host_escape.scan_guest_roots(DEFAULT_GUEST_ROOTS):
        return outcome_fail(key, MODE_STRUCTURAL, "a guest root shadows a control filesystem")
    return outcome_pass(
        key,
        MODE_STRUCTURAL,
        "systemd control sockets, the system dir and journald are absent from the domain "
        "XML/export/guest roots and verified closed per surface",
    )


def structural_create_host_transient_unit(facts: BoundaryFacts) -> ProbeOutcome:
    key = "create_host_transient_unit"
    systemd = facts.audit.systemd_run
    if not systemd.closed or systemd.host_reach != "none":
        return outcome_fail(key, MODE_STRUCTURAL, "the systemd-run --user reach is open")
    policy = broker_seam.HostOpPolicy.from_host_env({})
    for op in ("systemd.run", "systemd.start", "run", "shell", "exec", "command"):
        decision = broker_seam.evaluate_host_op_request(op, policy=policy)
        if decision.allowed or decision.reason != "unknown_operation":
            return outcome_fail(key, MODE_STRUCTURAL, f"host op {op!r} is not denied")
    restart = broker_seam.evaluate_host_op_request(
        "runtime.restart", policy=policy, approval_granted=False
    )
    if restart.allowed or restart.reason != "approval_required":
        return outcome_fail(key, MODE_STRUCTURAL, "a guest could self-approve a host op")
    for payload in (
        {"op": "run", "args": {}},
        {"op": "shell", "args": {}},
        {"op": "systemd.run", "args": {}},
    ):
        reason = channel.payload_policy_violation(
            MessageKind.HOST_OP, payload, ChannelConfig(port=43121)
        )
        if reason is None:
            return outcome_fail(key, MODE_STRUCTURAL, f"host op frame accepted: {payload}")
    kinds = {kind.value for kind in broker_ops.ArgKind}
    if kinds != {"name", "choice", "integer", "boolean"}:
        return outcome_fail(key, MODE_STRUCTURAL, f"a free-text host-op arg kind exists: {kinds}")
    for op in broker_seam.HOST_OP_REGISTRY:
        if op.split(".")[-1] in {"run", "sh", "shell", "exec", "command", "system"}:
            return outcome_fail(key, MODE_STRUCTURAL, f"the host-op registry carries {op!r}")
    return outcome_pass(
        key,
        MODE_STRUCTURAL,
        "systemd-run --user verdict closed: host user bus is not bound/exported/in env; "
        "systemd/shell host ops are outside the registry and the wire policy; "
        "no free-text argument kind exists",
    )


def structural_reach_host_dbus(facts: BoundaryFacts) -> ProbeOutcome:
    key = "reach_host_dbus"
    for surface_key in (
        "host_user_dbus",
        "host_system_dbus",
        "host_dbus_session_env",
        "host_dbus_system_env",
    ):
        verdict = facts.audit.surface(surface_key)
        if verdict is None or not verdict.closed:
            return outcome_fail(key, MODE_STRUCTURAL, f"surface {surface_key} is not closed")
    for name in ("DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS"):
        if name in facts.contract.env_names:
            return outcome_fail(key, MODE_STRUCTURAL, f"{name} crosses into the guest sandbox")
    bus_path = f"/run/user/{facts.uid}/bus"
    if bus_path in facts.domain_xml:
        return outcome_fail(key, MODE_STRUCTURAL, "the host user bus path appears in the domain XML")
    if facts.audit.systemd_run.host_bus_bound or facts.audit.systemd_run.host_bus_exported:
        return outcome_fail(key, MODE_STRUCTURAL, "the host user bus is bound or exported")
    return outcome_pass(
        key,
        MODE_STRUCTURAL,
        f"user/system D-Bus sockets and both D-Bus env names are closed; {bus_path} is neither "
        "bound nor exported",
    )


def structural_see_host_processes(facts: BoundaryFacts) -> ProbeOutcome:
    key = "see_host_processes"
    root = ET.fromstring(facts.domain_xml)
    if root.get("type") != "kvm":
        return outcome_fail(key, MODE_STRUCTURAL, f"domain type is {root.get('type')!r}, not kvm")
    os_type = root.find("os/type")
    if os_type is None or (os_type.text or "").strip() != "hvm":
        return outcome_fail(key, MODE_STRUCTURAL, "the guest is not booted as its own hvm kernel")
    tags = _device_tags(facts.domain_xml)
    unexpected = sorted(set(tags) - ALLOWED_DEVICE_TAGS)
    if unexpected:
        return outcome_fail(key, MODE_STRUCTURAL, f"unexpected host-binding devices: {unexpected}")
    for forbidden in ("hostdev", "channel", "redirdev", "shmem"):
        if root.findall(f".//{forbidden}"):
            return outcome_fail(key, MODE_STRUCTURAL, f"<{forbidden}> shares a host resource")
    if "qemu:commandline" in facts.domain_xml or root.find(".//pid") is not None:
        return outcome_fail(key, MODE_STRUCTURAL, "the domain shares a host namespace")
    interfaces = root.findall("devices/interface")
    if len(interfaces) != 1 or interfaces[0].get("type") != "user":
        return outcome_fail(key, MODE_STRUCTURAL, "the interface set is not the single user-mode NIC")
    return outcome_pass(
        key,
        MODE_STRUCTURAL,
        "the guest is a separate hvm/kvm kernel; the only devices are the disposable disk, "
        "one workspace export, vsock and the restricted user-mode NIC - no host PID sharing",
    )


def structural_reach_control_sockets(facts: BoundaryFacts) -> ProbeOutcome:
    key = "reach_control_sockets"
    for surface_key in (
        "host_docker_socket",
        "host_docker_socket_var_run",
        "host_libvirt_socket",
        "host_libvirt_socket_ro",
        "host_podman_socket",
        "host_podman_socket_rootful",
        "host_ssh_agent_socket",
    ):
        verdict = facts.audit.surface(surface_key)
        if verdict is None or not verdict.closed:
            return outcome_fail(key, MODE_STRUCTURAL, f"surface {surface_key} is not closed")
    for path in CONTROL_SOCKET_PATHS:
        if path in facts.domain_xml:
            return outcome_fail(key, MODE_STRUCTURAL, f"{path} appears in the domain XML")
    sock_path = facts.root / "forged.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(sock_path))
        forged = dict(facts.export)
        forged["host_path"] = str(sock_path)
        codes = {violation.code for violation in host_escape.scan_export(forged)}
        if "export_host_path_socket" not in codes:
            return outcome_fail(key, MODE_STRUCTURAL, "an export pointed at a host socket was not detected")
        raised = _refusal(lambda: host_escape.assert_export_clean(forged))
        if raised is None:
            return outcome_fail(key, MODE_STRUCTURAL, "assert_export_clean accepted a socket export")
    finally:
        server.close()
    return outcome_pass(
        key,
        MODE_STRUCTURAL,
        "docker/podman/libvirt/ssh-agent sockets are closed and absent; exporting a socket "
        "is detected (export_host_path_socket) and refused",
    )


def structural_mount_extra_host_paths(facts: BoundaryFacts) -> ProbeOutcome:
    key = "mount_extra_host_paths"
    filesystems = ET.fromstring(facts.domain_xml).findall(".//filesystem")
    if len(filesystems) != 1:
        return outcome_fail(key, MODE_STRUCTURAL, f"{len(filesystems)} filesystem devices, expected 1")
    sources = _filesystem_sources(facts.domain_xml)
    if sources != (str(facts.workspace),):
        return outcome_fail(key, MODE_STRUCTURAL, f"filesystem sources are {sources!r}")
    if facts.export["extra_exports"] != []:
        return outcome_fail(key, MODE_STRUCTURAL, "implicit extra exports appeared")
    if host_escape.scan_domain_xml(facts.domain_xml, allowed_export_paths=(str(facts.workspace),)):
        return outcome_fail(key, MODE_STRUCTURAL, "the generated XML fails its own scanner")
    for extra in ("/run", str(facts.home / ".ssh")):
        refusal = _refusal(
            lambda extra=extra: resolve_workspace_export(
                facts.workspace,
                policy=replace(
                    facts.policy,
                    extra_exports=(ExtraExport(cwd=extra, guest_path="/vault"),),
                ),
            )
        )
        if refusal is None:
            return outcome_fail(key, MODE_STRUCTURAL, f"an extra export at {extra} was accepted")
    return outcome_pass(
        key,
        MODE_STRUCTURAL,
        "exactly one filesystem (the workspace realpath) is attached; no extra exports by "
        "default; opt-in extras at a control root are refused",
    )


def structural_affect_another_agent_vm(facts: BoundaryFacts) -> ProbeOutcome:
    key = "affect_another_agent_vm"
    guard_a = WorkspaceExportGuard(facts.policy)
    guard_b = WorkspaceExportGuard(facts.policy)
    other_workspace = facts.root / "workspace-other"
    other_workspace.mkdir(exist_ok=True)
    try:
        guard_a.acquire(facts.workspace, "session-a")
        refused = _refusal(lambda: guard_b.acquire(facts.workspace, "session-b"))
        if refused is None or refused.reason != "workspace_busy":
            return outcome_fail(key, MODE_STRUCTURAL, "a second VM could export the same workspace")
        guard_b.acquire(other_workspace, "session-b")
        if len(guard_a.held) != 1 or guard_b.held.get("session-b") != str(other_workspace):
            return outcome_fail(key, MODE_STRUCTURAL, "independent workspaces did not both attach")
    finally:
        guard_a.close()
        guard_b.close()
    sibling = replace(facts.spec, session_id="session-b")
    if domain_name(sibling) == domain_name(facts.spec):
        return outcome_fail(key, MODE_STRUCTURAL, "both sessions share one domain name")
    if overlay_path(sibling) == overlay_path(facts.spec):
        return outcome_fail(key, MODE_STRUCTURAL, "both sessions share one overlay")
    if not overlay_path(sibling).is_relative_to(facts.overlay_dir):
        return outcome_fail(key, MODE_STRUCTURAL, "a sibling overlay escaped the overlay dir")
    sessions = SessionMap()
    sessions.assign(PeerCid(31), SessionId("session-a"))
    if sessions.session_for_cid(PeerCid(31)) != SessionId("session-a"):
        return outcome_fail(key, MODE_STRUCTURAL, "the host CID map lost its own session")
    if sessions.session_for_cid(PeerCid(32)) is not None:
        return outcome_fail(key, MODE_STRUCTURAL, "an unassigned CID resolved to a session")
    server = ChannelServer(ChannelConfig(port=43122), sessions)
    left, right = socket.socketpair()
    try:
        channel.write_frame(left, {"kind": "hello", "version": channel.PROTOCOL_VERSION})
        accepted = server.handle_connection(right, PeerCid(32))
        if accepted is not None:
            accepted.close()
            return outcome_fail(key, MODE_STRUCTURAL, "an unknown peer CID was accepted")
        ack = channel.read_frame(left, max_frame_bytes=4096, timeout_s=2.0)
        if not isinstance(ack, Mapping) or ack.get("accepted") is not False:
            return outcome_fail(key, MODE_STRUCTURAL, f"the unknown CID was not refused: {ack!r}")
        if ack.get("reason") != "unknown_peer_cid":
            return outcome_fail(key, MODE_STRUCTURAL, f"unexpected refusal reason: {ack.get('reason')!r}")
    finally:
        left.close()
        right.close()
    interface_xml = network.domain_interface_xml()
    if 'type="user"' not in interface_xml or "hostfwd" in interface_xml or "<bridge" in interface_xml:
        return outcome_fail(key, MODE_STRUCTURAL, "the NIC is not the restricted user-mode device")
    resolver = lambda host: ("93.184.216.34",)  # noqa: E731 - injected test resolver
    policy = network.build_network_policy(
        "cc-wally",
        base_url="https://api.example.net/v1",
        env={},
        resolver=resolver,
        boltbook_enabled=False,
        local_inference="off",
    )
    ruleset = policy.ruleset()
    for address, port in (
        ("10.0.2.3", 443),
        ("10.0.2.2", 1),
        ("192.168.1.10", 443),
        ("169.254.169.254", 80),
    ):
        probe = network.evaluate_egress(ruleset, address, port)
        if probe.allowed:
            return outcome_fail(key, MODE_STRUCTURAL, f"{address}:{port} is reachable from the guest")
    pinned = policy.pinned_endpoints()
    if not pinned:
        return outcome_fail(key, MODE_STRUCTURAL, "the allowlist pinned no address (vacuous ruleset)")
    allowed = network.evaluate_egress(ruleset, pinned[0][0], pinned[0][1])
    if not allowed.allowed:
        return outcome_fail(key, MODE_STRUCTURAL, "the configured allowlist entry is not reachable")
    return outcome_pass(
        key,
        MODE_STRUCTURAL,
        "one live export per workspace realpath (workspace_busy); distinct domains/overlays; "
        "an unknown peer CID is refused (unknown_peer_cid); the guest subnet, host loopback, "
        "LAN and metadata addresses are denied while the pinned allowlist still works",
    )


STRUCTURAL_PROBES: Final[Mapping[str, Callable[[BoundaryFacts], ProbeOutcome]]] = {
    "read_host_home": structural_read_host_home,
    "write_host_home": structural_write_host_home,
    "reach_ssh": structural_reach_ssh,
    "reach_host_systemd": structural_reach_host_systemd,
    "create_host_transient_unit": structural_create_host_transient_unit,
    "reach_host_dbus": structural_reach_host_dbus,
    "see_host_processes": structural_see_host_processes,
    "reach_control_sockets": structural_reach_control_sockets,
    "mount_extra_host_paths": structural_mount_extra_host_paths,
    "affect_another_agent_vm": structural_affect_another_agent_vm,
}


def run_structural_checklist() -> tuple[ProbeOutcome, ...]:
    """Run every structural probe against one real boundary; per-item verdicts."""
    with tempfile.TemporaryDirectory(prefix="igorek_negative_security_") as tmp:
        facts = build_boundary_facts(Path(os.path.realpath(tmp)))
        outcomes: list[ProbeOutcome] = []
        for item in PROBE_ITEMS:
            probe = STRUCTURAL_PROBES[item.key]
            try:
                outcomes.append(probe(facts))
            except Exception as exc:  # probe boundary: report, never crash
                outcomes.append(
                    outcome_fail(item.key, MODE_STRUCTURAL, f"{type(exc).__name__}: {exc}")
                )
        return tuple(outcomes)


# --------------------------------------------------------------------------- #
# Live half: per-item guest probes (KVM-guarded) with tamper-checked reports
# --------------------------------------------------------------------------- #

LIVE_CONTROL_DIR: Final = "/workspace/.igorek-negative"
DEFAULT_LIVE_PARAMS: Final[dict[str, str]] = {
    "control": LIVE_CONTROL_DIR,
    "host_home": "/home/agent",
    "host_canary": "igorek-negative-canary",
    "host_write_path": "/home/agent/.igorek-negative-probe",
    "host_ssh_dir": "/home/agent/.ssh",
    "host_marker_unit": "igorek-negative-host",
    "guest_unit": "igorek-negative-guest",
    "host_proc_marker": "igorek-negative-host-proc",
    "guest_proc_marker": "igorek-negative-guest-proc",
    "other_workspace": "/workspace-other",
}

#: These templates are POSIX sh, contain no shell braces and print the host-issued
#: nonce as the first report line. The runner formats them with the run-scoped
#: parameters and the nonce; the host then verifies the nonce before reading.
LIVE_SCRIPTS: Final[Mapping[str, str]] = {
    "read_host_home": """#!/bin/sh
D="{control}"
(
  echo "PROBE_NONCE={nonce}"
  echo "HOST_HOME={host_home}"
  if [ -e "{host_home}" ]; then echo "HOST_HOME_STATUS=present"; else echo "HOST_HOME_STATUS=absent"; fi
  if [ -r "{host_home}" ]; then echo "HOST_HOME_READABLE=yes"; else echo "HOST_HOME_READABLE=no"; fi
  if [ -r "{host_home}/{host_canary}" ]; then echo "HOST_CANARY_READABLE=yes"; else echo "HOST_CANARY_READABLE=no"; fi
) > "$D/probe_read_host_home.txt" 2>&1
""",
    "write_host_home": """#!/bin/sh
D="{control}"
(
  echo "PROBE_NONCE={nonce}"
  W="{host_write_path}"
  mkdir -p "$W" 2>/dev/null && echo "written" > "$W/written.txt" 2>/dev/null && echo "HOST_HOME_WRITE=allowed" || echo "HOST_HOME_WRITE=denied"
  mkdir -p "$HOME" 2>/dev/null && echo "written" > "$HOME/.igorek-negative-guest.txt" 2>/dev/null && echo "GUEST_HOME_WRITE=allowed" || echo "GUEST_HOME_WRITE=denied"
) > "$D/probe_write_host_home.txt" 2>&1
""",
    "reach_ssh": """#!/bin/sh
D="{control}"
(
  echo "PROBE_NONCE={nonce}"
  S="{host_ssh_dir}"
  if [ -e "$S" ]; then echo "HOST_SSH_STATUS=present"; else echo "HOST_SSH_STATUS=absent"; fi
  if [ -r "$S/id_ed25519" ] || [ -r "$S/id_rsa" ]; then echo "HOST_SSH_KEY_READABLE=yes"; else echo "HOST_SSH_KEY_READABLE=no"; fi
  if [ -n "$SSH_AUTH_SOCK" ] && [ -S "$SSH_AUTH_SOCK" ]; then echo "SSH_AGENT_SOCKET=present"; else echo "SSH_AGENT_SOCKET=absent"; fi
) > "$D/probe_reach_ssh.txt" 2>&1
""",
    "reach_host_systemd": """#!/bin/sh
D="{control}"
(
  echo "PROBE_NONCE={nonce}"
  systemctl --user list-units --no-legend --no-pager > /tmp/igorek_neg_units 2>&1; echo "SYSTEMCTL_RC=$?"
  echo "HOST_UNIT_IN_GUEST=$(grep -c {host_marker_unit} /tmp/igorek_neg_units 2>/dev/null)"
  systemctl --user cat {host_marker_unit}.service > /dev/null 2>&1; echo "HOST_UNIT_CAT_RC=$?"
) > "$D/probe_reach_host_systemd.txt" 2>&1
""",
    "create_host_transient_unit": """#!/bin/sh
D="{control}"
(
  echo "PROBE_NONCE={nonce}"
  systemd-run --user --wait --pipe --unit={guest_unit} true > /tmp/igorek_neg_run 2>&1; echo "SYSTEMD_RUN_RC=$?"
) > "$D/probe_create_host_transient_unit.txt" 2>&1
""",
    "reach_host_dbus": """#!/bin/sh
D="{control}"
(
  echo "PROBE_NONCE={nonce}"
  busctl --user list > /tmp/igorek_neg_bus 2>&1; echo "BUSCTL_RC=$?"
  echo "HOST_UNIT_IN_BUS=$(grep -c {host_marker_unit} /tmp/igorek_neg_bus 2>/dev/null)"
  if [ -n "$DBUS_SESSION_BUS_ADDRESS" ]; then echo "DBUS_SESSION_ADDR=set"; else echo "DBUS_SESSION_ADDR=unset"; fi
  if [ -e /run/user/$(id -u)/bus ]; then echo "GUEST_BUS_INODE=$(stat -Lc %i /run/user/$(id -u)/bus 2>/dev/null)"; else echo "GUEST_BUS_INODE=none"; fi
) > "$D/probe_reach_host_dbus.txt" 2>&1
""",
    "see_host_processes": """#!/bin/sh
D="{control}"
(
  echo "PROBE_NONCE={nonce}"
  M="{host_proc_marker}"
  C=0
  for p in /proc/[0-9]*/cmdline; do
    if tr '\\0' ' ' < "$p" 2>/dev/null | grep -qF "$M"; then C=$((C+1)); fi
  done
  echo "HOST_PROC_MATCHES=$C"
  G="{guest_proc_marker}"
  if cp /bin/sleep "$D/$G" 2>/dev/null || cp /usr/bin/sleep "$D/$G" 2>/dev/null; then
    "$D/$G" 20 > /dev/null 2>&1 &
    sleep 0.5
    GC=0
    for p in /proc/[0-9]*/cmdline; do
      if tr '\\0' ' ' < "$p" 2>/dev/null | grep -qF "$G"; then GC=$((GC+1)); fi
    done
    echo "GUEST_PROC_MATCHES=$GC"
  else
    echo "GUEST_PROC_MATCHES=control_failed"
  fi
) > "$D/probe_see_host_processes.txt" 2>&1
""",
    "reach_control_sockets": """#!/bin/sh
D="{control}"
(
  echo "PROBE_NONCE={nonce}"
  for P in /run/docker.sock /var/run/docker.sock /run/libvirt/libvirt-sock /run/podman/podman.sock /run/user/$(id -u)/ssh-agent.socket; do
    if [ -e "$P" ]; then echo "CONTROL_SOCKET_PRESENT=$P"; fi
  done
  if [ -n "$SSH_AUTH_SOCK" ]; then echo "SSH_AUTH_SOCK=set"; else echo "SSH_AUTH_SOCK=unset"; fi
  echo "SOCKET_SCAN=done"
) > "$D/probe_reach_control_sockets.txt" 2>&1
""",
    "mount_extra_host_paths": """#!/bin/sh
D="{control}"
(
  echo "PROBE_NONCE={nonce}"
  M=/tmp/igorek-neg-mnt
  mkdir -p "$M" 2>/dev/null
  mount --bind / "$M" > /tmp/igorek_neg_m1 2>&1; echo "MOUNT_BIND_ROOT_RC=$?"
  mount -t tmpfs tmpfs "$M" > /tmp/igorek_neg_m2 2>&1; echo "MOUNT_TMPFS_RC=$?"
  mount --bind /workspace /mnt > /tmp/igorek_neg_m3 2>&1; echo "MOUNT_MNT_RC=$?"
) > "$D/probe_mount_extra_host_paths.txt" 2>&1
""",
    "affect_another_agent_vm": """#!/bin/sh
D="{control}"
(
  echo "PROBE_NONCE={nonce}"
  echo "GUEST_VSOCK_CID=$(cat /proc/self/cid 2>/dev/null || echo unknown)"
  if [ -e "{other_workspace}" ]; then echo "OTHER_WORKSPACE=visible"; else echo "OTHER_WORKSPACE=absent"; fi
  if [ -e "{control}" ] && [ -e "{control}/owner.txt" ] && grep -q {nonce} "{control}/owner.txt" 2>/dev/null; then echo "CONTROL_OWNER=match"; else echo "CONTROL_OWNER=mismatch"; fi
) > "$D/probe_affect_another_agent_vm.txt" 2>&1
""",
}


def _marker_value(report: str, name: str) -> str | None:
    prefix = f"{name}="
    for line in report.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def verdict_read_host_home(report: str, facts: Mapping[str, str]) -> tuple[bool, str]:
    if facts.get("host_canary_present") != "yes":
        return False, "the host canary is missing; the probe would be vacuous"
    status = _marker_value(report, "HOST_HOME_STATUS")
    readable = _marker_value(report, "HOST_HOME_READABLE")
    canary = _marker_value(report, "HOST_CANARY_READABLE")
    if status != "absent" or readable != "no" or canary != "no":
        return False, (
            f"host $HOME is visible inside the guest (status={status} readable={readable} "
            f"canary={canary})"
        )
    return True, "host $HOME and its canary are absent/unreadable inside the guest"


def verdict_write_host_home(report: str, facts: Mapping[str, str]) -> tuple[bool, str]:
    if facts.get("host_write_path_absent") != "yes":
        return False, "the probe write landed in host $HOME (host-observed file exists)"
    host_write = _marker_value(report, "HOST_HOME_WRITE")
    guest_write = _marker_value(report, "GUEST_HOME_WRITE")
    if host_write != "denied":
        return False, f"the guest wrote into host $HOME (rc marker {host_write!r})"
    if guest_write != "allowed":
        return False, "the positive control failed: the guest could not write its own $HOME"
    return True, "host $HOME write denied and host-observed absent; guest $HOME write works"


def verdict_reach_ssh(report: str, facts: Mapping[str, str]) -> tuple[bool, str]:
    if facts.get("host_ssh_dir_present") != "yes":
        return False, "the host ~/.ssh is missing; the probe would be vacuous"
    status = _marker_value(report, "HOST_SSH_STATUS")
    readable = _marker_value(report, "HOST_SSH_KEY_READABLE")
    agent = _marker_value(report, "SSH_AGENT_SOCKET")
    if status == "present" or readable != "no" or agent != "absent":
        return False, (
            f"host ~/.ssh is reachable (status={status} readable={readable} agent={agent})"
        )
    return True, "host ~/.ssh and key material are absent/unreadable; no ssh-agent socket"


def verdict_reach_host_systemd(report: str, facts: Mapping[str, str]) -> tuple[bool, str]:
    if facts.get("host_marker_unit_present") != "yes":
        return False, "the host marker unit is missing; the probe would be vacuous"
    in_guest = _marker_value(report, "HOST_UNIT_IN_GUEST")
    cat_rc = _marker_value(report, "HOST_UNIT_CAT_RC")
    if in_guest != "0" or cat_rc in (None, "", "0"):
        return False, (
            f"the host user manager is visible inside the guest (in_units={in_guest} cat_rc={cat_rc})"
        )
    return True, "the host transient unit is invisible inside the guest (units=0, cat refused)"


def verdict_create_host_transient_unit(
    report: str, facts: Mapping[str, str]
) -> tuple[bool, str]:
    if facts.get("host_transient_unit_absent") != "yes":
        return False, "host-observed: a unit with the probe name exists on the host"
    rc = _marker_value(report, "SYSTEMD_RUN_RC")
    if rc in (None, "", "0"):
        return False, f"systemd-run --user did not fail inside the guest (rc={rc})"
    return True, f"systemd-run --user failed inside the guest (rc={rc}) and no host unit appeared"


def verdict_reach_host_dbus(report: str, facts: Mapping[str, str]) -> tuple[bool, str]:
    host_inode = facts.get("host_bus_inode", "")
    if not host_inode:
        return False, "host bus inode unavailable; the probe would be vacuous"
    in_bus = _marker_value(report, "HOST_UNIT_IN_BUS")
    addr = _marker_value(report, "DBUS_SESSION_ADDR")
    guest_inode = _marker_value(report, "GUEST_BUS_INODE")
    if addr != "unset":
        return False, "DBUS_SESSION_BUS_ADDRESS crosses into the guest"
    if in_bus not in (None, "", "0"):
        return False, "the host marker unit is visible on the guest's bus"
    if guest_inode == host_inode:
        return False, "the guest reports the HOST bus inode: the host bus is reachable"
    return True, (
        f"host user bus is not the guest bus (guest inode={guest_inode or 'none'}, "
        f"host inode={host_inode}); no host unit in the guest bus listing"
    )


def verdict_see_host_processes(report: str, facts: Mapping[str, str]) -> tuple[bool, str]:
    if facts.get("host_marker_process_alive") != "yes":
        return False, "the host marker process is gone; the probe would be vacuous"
    host_matches = _marker_value(report, "HOST_PROC_MATCHES")
    guest_matches = _marker_value(report, "GUEST_PROC_MATCHES")
    if guest_matches != "1":
        return False, f"the positive control failed (guest matches={guest_matches!r})"
    if host_matches != "0":
        return False, f"host processes are visible inside the guest (matches={host_matches})"
    return True, "no host process marker in the guest; the guest control process was seen"


def verdict_reach_control_sockets(report: str, facts: Mapping[str, str]) -> tuple[bool, str]:
    if facts.get("host_control_socket_present") != "yes":
        return False, "no host control socket exists to test against; the probe would be vacuous"
    if _marker_value(report, "SOCKET_SCAN") != "done":
        return False, "the guest socket scan did not complete"
    present = [
        line.split("=", 1)[1]
        for line in report.splitlines()
        if line.startswith("CONTROL_SOCKET_PRESENT=")
    ]
    if present:
        return False, f"host control sockets are visible inside the guest: {present}"
    if _marker_value(report, "SSH_AUTH_SOCK") != "unset":
        return False, "SSH_AUTH_SOCK crosses into the guest"
    return True, "no docker/libvirt/podman/ssh-agent socket and no SSH_AUTH_SOCK in the guest"


def verdict_mount_extra_host_paths(report: str, facts: Mapping[str, str]) -> tuple[bool, str]:
    if facts.get("host_domain_filesystem_count") != "1":
        return False, (
            "host-observed: the libvirt domain does not carry exactly one filesystem "
            f"({facts.get('host_domain_filesystem_count')!r})"
        )
    for marker in ("MOUNT_BIND_ROOT_RC", "MOUNT_TMPFS_RC", "MOUNT_MNT_RC"):
        rc = _marker_value(report, marker)
        if rc in (None, "", "0"):
            return False, f"a guest mount succeeded ({marker}={rc!r})"
    return True, "all guest mounts failed and the host domain has exactly one filesystem"


def verdict_affect_another_agent_vm(report: str, facts: Mapping[str, str]) -> tuple[bool, str]:
    if facts.get("host_export_guard_intact") != "yes":
        return False, "host-observed: the sibling workspace claim was not refused"
    if facts.get("host_sibling_vm_state") != "1":
        return False, (
            "host-observed: more than one session overlay exists in this run "
            f"({facts.get('host_sibling_vm_state')!r})"
        )
    if _marker_value(report, "OTHER_WORKSPACE") != "absent":
        return False, "another session's workspace is visible inside the guest"
    if _marker_value(report, "CONTROL_OWNER") != "match":
        return False, "the probe did not run against this session's own control dir"
    if not _marker_value(report, "GUEST_VSOCK_CID"):
        return False, "the guest could not report its own vsock CID"
    return True, "sibling workspace absent; the host held one overlay and refused a second claim"


LIVE_VERDICTS: Final[Mapping[str, Callable[[str, Mapping[str, str]], tuple[bool, str]]]] = {
    "read_host_home": verdict_read_host_home,
    "write_host_home": verdict_write_host_home,
    "reach_ssh": verdict_reach_ssh,
    "reach_host_systemd": verdict_reach_host_systemd,
    "create_host_transient_unit": verdict_create_host_transient_unit,
    "reach_host_dbus": verdict_reach_host_dbus,
    "see_host_processes": verdict_see_host_processes,
    "reach_control_sockets": verdict_reach_control_sockets,
    "mount_extra_host_paths": verdict_mount_extra_host_paths,
    "affect_another_agent_vm": verdict_affect_another_agent_vm,
}

LIVE_HOST_CHECKS: Final[Mapping[str, tuple[str, ...]]] = {
    "read_host_home": ("host_canary_present",),
    "write_host_home": ("host_write_path_absent",),
    "reach_ssh": ("host_ssh_dir_present",),
    "reach_host_systemd": ("host_marker_unit_present",),
    "create_host_transient_unit": ("host_transient_unit_absent",),
    "reach_host_dbus": ("host_bus_inode",),
    "see_host_processes": ("host_marker_process_alive",),
    "reach_control_sockets": ("host_control_socket_present",),
    "mount_extra_host_paths": ("host_domain_filesystem_count",),
    "affect_another_agent_vm": (
        "host_export_guard_intact",
        "host_sibling_vm_state",
    ),
}

#: The clean value of every host observation; a fact absent from this table is a
#: presence/equality fact the verdict itself interprets (e.g. the bus inode).
LIVE_HOST_FACT_CLEAN: Final[Mapping[str, str]] = {
    "host_canary_present": "yes",
    "host_write_path_absent": "yes",
    "host_ssh_dir_present": "yes",
    "host_marker_unit_present": "yes",
    "host_transient_unit_absent": "yes",
    "host_marker_process_alive": "yes",
    "host_control_socket_present": "yes",
    "host_domain_filesystem_count": "1",
    "host_export_guard_intact": "yes",
    "host_sibling_vm_state": "1",
}


@dataclass(frozen=True, slots=True)
class LiveProbePlan:
    """One concrete live probe: the script the guest runs and how it is verified."""

    key: str
    script: str
    report_name: str
    nonce: str
    issued_at: float

    def verification(self) -> Callable[[str, Mapping[str, str]], tuple[bool, str]]:
        return LIVE_VERDICTS[self.key]


def live_probe_plan(key: str, *, params: Mapping[str, str], nonce: str, issued_at: float) -> LiveProbePlan:
    """Format the item's guest script with this run's parameters and nonce."""
    template = LIVE_SCRIPTS[key]
    values = dict(DEFAULT_LIVE_PARAMS)
    values.update(params)
    values["nonce"] = nonce
    return LiveProbePlan(
        key=key,
        script=template.format(**values),
        report_name=f"probe_{key}.txt",
        nonce=nonce,
        issued_at=float(issued_at),
    )


class LiveProbeUnavailable(Exception):
    """Honest SKIP: this host cannot run the live half (never a fake PASS)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = str(reason)


ProbeReportRejected = probe_verification.ProbeReportRejected


class LiveProbeRunner(Protocol):
    """What the live half needs from a real VM runner (faked in the unit tests)."""

    def build_plan(self, key: str) -> LiveProbePlan: ...

    def run_probe(self, plan: LiveProbePlan) -> str: ...

    def host_facts(self, plan: LiveProbePlan) -> Mapping[str, str]: ...


def run_live_probe(key: str, runner: LiveProbeRunner) -> ProbeOutcome:
    """Run one live item: SKIP only when the runner is unavailable, else PASS/FAIL."""
    try:
        plan = runner.build_plan(key)
    except LiveProbeUnavailable as exc:
        return ProbeOutcome(key, MODE_LIVE, STATUS_SKIP, exc.reason)
    try:
        report = runner.run_probe(plan)
    except ProbeReportRejected as exc:
        return outcome_fail(key, MODE_LIVE, f"tamper check rejected the report: {exc}")
    except LiveProbeUnavailable as exc:
        return ProbeOutcome(key, MODE_LIVE, STATUS_SKIP, exc.reason)
    except Exception as exc:  # probe boundary: report, never crash
        return outcome_fail(key, MODE_LIVE, f"{type(exc).__name__}: {exc}")
    try:
        facts = runner.host_facts(plan)
    except LiveProbeUnavailable as exc:
        return ProbeOutcome(key, MODE_LIVE, STATUS_SKIP, f"host observation unavailable: {exc.reason}")
    except Exception as exc:  # probe boundary: report, never crash
        return outcome_fail(key, MODE_LIVE, f"{type(exc).__name__}: {exc}")
    missing = [name for name in LIVE_HOST_CHECKS[key] if name not in facts]
    if missing:
        return outcome_fail(key, MODE_LIVE, f"host observation missing: {missing}")
    try:
        ok, detail = plan.verification()(report, facts)
    except Exception as exc:  # probe boundary: report, never crash
        return outcome_fail(key, MODE_LIVE, f"{type(exc).__name__}: {exc}")
    return outcome_pass(key, MODE_LIVE, detail) if ok else outcome_fail(key, MODE_LIVE, detail)


# --------------------------------------------------------------------------- #
# The real live runner: the todo-13 PoC harness is the one live VM path
# --------------------------------------------------------------------------- #

POC_MODULE_PATH: Final = Path(__file__).with_name("test_isolation_poc.py")


def _load_poc_module() -> ModuleType:
    """Load the PoC harness by path (``tests/`` is not an importable package)."""
    spec = importlib.util.spec_from_file_location("igorek_isolation_poc_harness", POC_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise LiveProbeUnavailable(f"cannot load the PoC harness at {POC_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


class UnavailableLiveRunner:
    """The honest SKIP runner: every item reports the concrete reason."""

    def __init__(self, reason: str) -> None:
        self._reason = str(reason)

    def build_plan(self, key: str) -> LiveProbePlan:
        raise LiveProbeUnavailable(f"{key}: {self._reason}")

    def run_probe(self, plan: LiveProbePlan) -> str:  # pragma: no cover - never reached
        raise LiveProbeUnavailable(self._reason)

    def host_facts(self, plan: LiveProbePlan) -> Mapping[str, str]:  # pragma: no cover
        return {}


class PocLiveRunner:
    """Real VM path: one PoC session per run, one guest script per item."""

    def __init__(self, *, profile: str, workspace: str, env: Mapping[str, str] | None = None) -> None:
        self._env = dict(os.environ if env is None else env)
        self._run_id = uuid.uuid4().hex[:10]
        self._ledger = probe_verification.ProbeLedger()
        self._marker_proc: subprocess.Popen[str] | None = None
        self._poc = _load_poc_module()
        request = self._poc.PocRequest(
            profile=profile, workspace=workspace,
            vsock_port=int(self._env.get("IGOREK_ISOLATION_VSOCK_PORT") or self._poc.DEFAULT_POC_VSOCK_PORT),
        )
        self._backend = self._poc.RealPocBackend(request, env=self._env)
        self._backend.prepare()
        self._backend.start_session()
        self._facts_cache: dict[str, Mapping[str, str]] = {}

    # -- runner protocol -------------------------------------------------- #

    def build_plan(self, key: str) -> LiveProbePlan:
        challenge = self._ledger.issue(key)
        home = Path.home()
        params = {
            "control": self._backend.guest_control_dir(),
            "host_home": str(home),
            "host_canary": f"igorek-negative-canary-{self._run_id}",
            "host_write_path": str(home / f".igorek-negative-probe-{self._run_id}"),
            "host_ssh_dir": str(home / ".ssh"),
            "host_marker_unit": f"igorek-negative-host-{self._run_id}",
            "guest_unit": f"igorek-negative-guest-{self._run_id}",
            "host_proc_marker": f"igorek-negative-host-proc-{self._run_id}",
            "guest_proc_marker": f"igorek-negative-guest-proc-{self._run_id}",
            "other_workspace": "/workspace-other",
        }
        self._prepare_host_side(key, params)
        return live_probe_plan(
            key, params=params, nonce=challenge.nonce, issued_at=challenge.issued_at
        )

    def _prepare_host_side(self, key: str, params: Mapping[str, str]) -> None:
        """Set up the host-side preconditions BEFORE the guest probe runs."""
        if key == "read_host_home":
            canary = Path(params["host_home"]) / params["host_canary"]
            canary.write_text("NEGATIVE-PROBE-CANARY\n", encoding="utf-8")
        if key == "write_host_home":
            Path(params["host_write_path"]).unlink(missing_ok=True)
        if key in ("reach_host_systemd", "reach_host_dbus"):
            self._ensure_host_marker_unit(params["host_marker_unit"])
        if key == "see_host_processes":
            self._start_host_marker_process(params["host_proc_marker"])
        if key == "affect_another_agent_vm":
            self._backend.write_control_file("owner.txt", f"owner={self._ledger.challenges()[key].nonce}\n")

    def run_probe(self, plan: LiveProbePlan) -> str:
        report = self._backend.run_guest_script(plan.script, report_name=plan.report_name)
        facts = self._observed_facts(plan)
        verdict = self._ledger.verify(
            plan.key,
            report,
            artifact_mtime=self._backend.report_mtime(plan.report_name),
            host_observations=self._observation_bools(facts),
        )
        if not verdict.ok:
            raise ProbeReportRejected(verdict.reason, verdict.detail)
        self._facts_cache[plan.key] = facts
        return report

    def host_facts(self, plan: LiveProbePlan) -> Mapping[str, str]:
        return dict(self._facts_cache.get(plan.key) or self._observed_facts(plan))

    def _observation_bools(self, facts: Mapping[str, str]) -> Mapping[str, bool]:
        return {
            name: (True if name not in LIVE_HOST_FACT_CLEAN else value == LIVE_HOST_FACT_CLEAN[name])
            for name, value in facts.items()
        }

    # -- host observations (all computed on the HOST) --------------------- #

    def _observed_facts(self, plan: LiveProbePlan) -> Mapping[str, str]:
        home = Path.home()
        if plan.key == "read_host_home":
            canary = home / f"igorek-negative-canary-{self._run_id}"
            return {"host_canary_present": "yes" if canary.is_file() else "no"}
        if plan.key == "write_host_home":
            path = home / f".igorek-negative-probe-{self._run_id}"
            return {"host_write_path_absent": "no" if path.exists() else "yes"}
        if plan.key == "reach_ssh":
            return {"host_ssh_dir_present": "yes" if (home / ".ssh").is_dir() else "no"}
        if plan.key in ("reach_host_systemd", "reach_host_dbus"):
            unit = f"igorek-negative-host-{self._run_id}"
            self._ensure_host_marker_unit(unit)
            return {"host_marker_unit_present": "yes" if self._host_unit_exists(unit) else "no"}
        if plan.key == "create_host_transient_unit":
            unit = f"igorek-negative-guest-{self._run_id}"
            return {
                "host_transient_unit_absent": "no" if self._host_unit_exists(unit) else "yes"
            }
        if plan.key == "see_host_processes":
            proc = self._marker_proc
            return {
                "host_marker_process_alive": "yes"
                if proc is not None and proc.poll() is None
                else "no"
            }
        if plan.key == "reach_control_sockets":
            present = any(Path(path).exists() for path in CONTROL_SOCKET_PATHS) or (
                Path(f"/run/user/{os.getuid()}/bus").exists()
            )
            return {"host_control_socket_present": "yes" if present else "no"}
        if plan.key == "mount_extra_host_paths":
            return {"host_domain_filesystem_count": self._host_filesystem_count()}
        if plan.key == "affect_another_agent_vm":
            overlays = list(Path(self._backend.session_spec.overlay_dir).glob("*.qcow2"))
            guard = WorkspaceExportGuard(WorkspacePolicy.from_environment())
            intact = "no"
            try:
                guard.acquire(self._backend.workspace_host_path(), "probe-sibling")
            except IsolationError as exc:
                intact = "yes" if exc.reason == "workspace_busy" else "no"
            else:
                guard.release("probe-sibling")
            finally:
                guard.close()
            return {
                "host_export_guard_intact": intact,
                "host_sibling_vm_state": str(len(overlays)),
            }
        return {}

    def _run_host_command(self, argv: Sequence[str], *, what: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603 - fixed argv, no shell
                list(argv), capture_output=True, text=True, timeout=30.0, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LiveProbeUnavailable(f"{what} unavailable: {exc}") from exc

    def _ensure_host_marker_unit(self, unit: str) -> None:
        if self._host_unit_exists(unit):
            return
        completed = self._run_host_command(
            ["systemd-run", "--user", "--unit", unit, "--collect", "--wait", "--pipe", "true"],
            what="host user systemd",
        )
        if completed.returncode != 0 and not self._host_unit_exists(unit):
            raise LiveProbeUnavailable(f"host user systemd unavailable: {completed.stderr.strip()}")

    def _host_unit_exists(self, unit: str) -> bool:
        completed = self._run_host_command(
            ["systemctl", "--user", "cat", f"{unit}.service"], what="host user systemd"
        )
        return completed.returncode == 0

    def _start_host_marker_process(self, marker: str) -> None:
        if self._marker_proc is not None and self._marker_proc.poll() is None:
            return
        self._marker_proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
            [
                sys.executable,
                "-c",
                "import sys, time; time.sleep(float(sys.argv[1]))",
                "300",
                marker,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _host_filesystem_count(self) -> str:
        completed = self._run_host_command(
            ["virsh", "-c", "qemu:///session", "dumpxml", domain_name(self._backend.session_spec)],
            what="host libvirt dumpxml",
        )
        if completed.returncode != 0:
            raise LiveProbeUnavailable(f"libvirt dumpxml unavailable: {completed.stderr.strip()}")
        return str(completed.stdout.count("<filesystem"))

    # -- lifecycle --------------------------------------------------------- #

    def close(self) -> None:
        if self._marker_proc is not None and self._marker_proc.poll() is None:
            self._marker_proc.terminate()
            try:
                self._marker_proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                self._marker_proc.kill()
            self._marker_proc = None
        self._backend.cleanup()


def default_live_runner(env: Mapping[str, str] | None = None) -> LiveProbeRunner:
    """The real runner when a KVM host is ready, else an honest SKIP runner."""
    source = dict(os.environ if env is None else env)
    reason = live_probe_skip_reason(source)
    if reason is not None:
        return UnavailableLiveRunner(reason)
    profile = str(source.get("IGOREK_NEGATIVE_PROFILE", "cc-wally")).strip()
    workspace = str(source.get("IGOREK_NEGATIVE_WORKSPACE", "")).strip()
    if not workspace:
        return UnavailableLiveRunner(
            "IGOREK_NEGATIVE_WORKSPACE unset (pass an existing project dir to run the live probes)"
        )
    return PocLiveRunner(profile=profile, workspace=workspace, env=source)


# --------------------------------------------------------------------------- #
# Tests: tamper check (T13 residual)
# --------------------------------------------------------------------------- #


class ProbeReportTamperTests(unittest.TestCase):
    """A guest-authored report is only accepted when the host issued its nonce."""

    def setUp(self) -> None:
        self.ledger = probe_verification.ProbeLedger()
        self.now = time.time()

    def _valid_report(self, challenge: probe_verification.ProbeChallenge) -> str:
        return (
            f"PROBE_NONCE={challenge.nonce}\n"
            "POC_PROBE=home\n"
            "POC_HOME_CANARY=absent\n"
        )

    def _verify(
        self,
        report: str,
        *,
        mtime: float | None = None,
        facts: Mapping[str, bool] | None = None,
    ) -> probe_verification.ProbeVerdict:
        return self.ledger.verify(
            "read_host_home",
            report,
            artifact_mtime=self.now + 1.0 if mtime is None else mtime,
            host_observations=facts,
        )

    def test_valid_report_is_accepted_once(self) -> None:
        # Given: a host-issued challenge and the matching guest report
        challenge = self.ledger.issue("read_host_home")
        # When: the host verifies it
        verdict = self._verify(self._valid_report(challenge))
        # Then: the report is accepted (the check is not vacuous)
        self.assertTrue(verdict.ok, verdict.detail)

    def test_report_without_a_nonce_is_rejected(self) -> None:
        self.ledger.issue("read_host_home")

        verdict = self._verify("POC_PROBE=home\nPOC_HOME_CANARY=absent\n")

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, probe_verification.REASON_NONCE_MISSING)

    def test_report_with_a_foreign_nonce_is_rejected(self) -> None:
        self.ledger.issue("read_host_home")

        verdict = self._verify("PROBE_NONCE=deadbeef\nPOC_HOME_CANARY=absent\n")

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, probe_verification.REASON_NONCE_MISMATCH)

    def test_report_with_a_second_nonce_line_is_rejected(self) -> None:
        challenge = self.ledger.issue("read_host_home")

        verdict = self._verify(
            f"PROBE_NONCE={challenge.nonce}\nPROBE_NONCE=forged\nPOC_HOME_CANARY=absent\n"
        )

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, probe_verification.REASON_NONCE_MISMATCH)

    def test_pre_planted_report_is_rejected_as_stale(self) -> None:
        challenge = self.ledger.issue("read_host_home")

        verdict = self._verify(self._valid_report(challenge), mtime=challenge.issued_at - 60.0)

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, probe_verification.REASON_STALE)

    def test_replayed_report_is_rejected(self) -> None:
        challenge = self.ledger.issue("read_host_home")
        first = self._verify(self._valid_report(challenge))
        self.assertTrue(first.ok, first.detail)

        second = self._verify(self._valid_report(challenge))

        self.assertFalse(second.ok)
        self.assertEqual(second.reason, probe_verification.REASON_REPLAYED)

    def test_contradicted_host_observation_is_rejected(self) -> None:
        challenge = self.ledger.issue("read_host_home")

        verdict = self._verify(
            self._valid_report(challenge), facts={"canary_untouched": False}
        )

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, probe_verification.REASON_HOST_OBSERVATION)
        self.assertIn("canary_untouched", verdict.detail)

    def test_unissued_probe_report_is_rejected(self) -> None:
        verdict = self._verify("PROBE_NONCE=deadbeef\n")

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, probe_verification.REASON_UNISSUED)

    def test_a_previous_run_report_cannot_be_replayed_into_a_new_run(self) -> None:
        previous = probe_verification.ProbeLedger()
        stale = self._valid_report(previous.issue("read_host_home"))
        self.ledger.issue("read_host_home")

        verdict = self._verify(stale)

        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, probe_verification.REASON_NONCE_MISMATCH)


# --------------------------------------------------------------------------- #
# Tests: structural checklist (always runs, real guards)
# --------------------------------------------------------------------------- #


class _BoundaryCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="igorek_negative_security_")
        self.addCleanup(tmp.cleanup)
        self.facts = build_boundary_facts(Path(os.path.realpath(tmp.name)))


class ChecklistShapeTests(_BoundaryCase):
    def test_checklist_covers_exactly_the_ten_tz_items(self) -> None:
        self.assertEqual(len(PROBE_ITEMS), 10)
        self.assertEqual(len(PROBE_KEYS), len(set(PROBE_KEYS)))
        self.assertEqual(set(STRUCTURAL_PROBES), set(PROBE_KEYS))
        self.assertEqual(set(LIVE_SCRIPTS), set(PROBE_KEYS))
        self.assertEqual(set(LIVE_VERDICTS), set(PROBE_KEYS))
        self.assertEqual(set(LIVE_HOST_CHECKS), set(PROBE_KEYS))

    def test_every_live_script_is_valid_posix_shell_and_brace_free(self) -> None:
        for key in PROBE_KEYS:
            with self.subTest(item=key):
                plan = live_probe_plan(
                    key, params={}, nonce="0123456789abcdef", issued_at=time.time()
                )
                self.assertIn("PROBE_NONCE=0123456789abcdef", plan.script)
                self.assertNotIn("{", plan.script, "unformatted placeholder left in the script")
                completed = subprocess.run(  # noqa: S603 - sh -n, fixed argv
                    ["sh", "-n", "-c", plan.script],
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_all_structural_probes_deny_and_report_individually(self) -> None:
        outcomes = run_structural_checklist()
        self.assertEqual(tuple(item.key for item in outcomes), PROBE_KEYS)
        for line in render_outcomes(outcomes):
            print(line)
        failed = [item for item in outcomes if not item.ok]
        self.assertEqual(
            failed,
            [],
            "negative probes failed: "
            + ", ".join(f"{item.key} ({item.detail})" for item in failed),
        )


class N1ReadHostHomeTests(_BoundaryCase):
    def test_host_home_is_denied_and_absent(self) -> None:
        outcome = structural_read_host_home(self.facts)
        self.assertTrue(outcome.ok, outcome.detail)

    def test_the_guard_is_not_vacuous_for_a_normal_workspace(self) -> None:
        accepted = resolve_workspace_export(self.facts.workspace, policy=self.facts.policy)
        self.assertEqual(accepted["host_path"], str(self.facts.workspace))

    def test_a_home_cwd_is_refused_by_the_resolver(self) -> None:
        with self.assertRaises(IsolationError) as ctx:
            resolve_workspace_export(str(self.facts.home), policy=self.facts.policy)
        self.assertTrue(ctx.exception.reason.startswith("sensitive_path_denied"))


class N2WriteHostHomeTests(_BoundaryCase):
    def test_host_home_write_is_denied(self) -> None:
        outcome = structural_write_host_home(self.facts)
        self.assertTrue(outcome.ok, outcome.detail)

    def test_the_sandbox_env_allowlist_stays_exact_when_a_secret_is_planted(self) -> None:
        argv = guest_bwrap.guest_sandbox_env_argv(
            {
                "HOME": "/home/agent",
                "IGOREK_ISOLATION_EMBEDDINGS_API_KEY": "SUPERSECRET-1",
                "UV_INDEX_URL": "https://ci:SUPERSECRET-2@example.invalid/simple",
            }
        )
        for name in guest_bwrap.HARDENED_ENV_ALLOWLIST:
            self.assertIsInstance(name, str)
        self.assertNotIn("SUPERSECRET-1", argv)
        self.assertNotIn("SUPERSECRET-2", argv)
        self.assertIn("HOME", argv)
        self.assertNotIn("IGOREK_ISOLATION_EMBEDDINGS_API_KEY", argv)


class N3ReachSshTests(_BoundaryCase):
    def test_ssh_is_denied_and_absent(self) -> None:
        outcome = structural_reach_ssh(self.facts)
        self.assertTrue(outcome.ok, outcome.detail)

    def test_a_workspace_symlink_into_ssh_is_refused(self) -> None:
        link = self.facts.workspace / "escape"
        link.symlink_to(self.facts.home / ".ssh")
        try:
            with self.assertRaises(IsolationError) as ctx:
                resolve_workspace_export(self.facts.workspace, policy=self.facts.policy)
        finally:
            link.unlink(missing_ok=True)
        self.assertTrue(str(ctx.exception.reason).startswith("symlink_escape"))


class N4ReachHostSystemdTests(_BoundaryCase):
    def test_systemd_control_surfaces_are_absent(self) -> None:
        outcome = structural_reach_host_systemd(self.facts)
        self.assertTrue(outcome.ok, outcome.detail)

    def test_a_forged_systemd_channel_is_detected(self) -> None:
        forged_xml = self.facts.domain_xml.replace(
            "</devices>",
            '<channel type="unix"><source mode="bind" path="/run/systemd/private"/>'
            '<target type="virtio" name="org.qemu.guest_agent.0"/></channel>\n</devices>',
            1,
        )
        codes = {violation.code for violation in host_escape.scan_domain_xml(forged_xml)}
        self.assertIn("host_binding", codes)
        self.assertIn("host_control_surface", codes)


class N5CreateHostTransientUnitTests(_BoundaryCase):
    def test_host_transient_units_are_structurally_out_of_reach(self) -> None:
        outcome = structural_create_host_transient_unit(self.facts)
        self.assertTrue(outcome.ok, outcome.detail)

    def test_a_bound_host_bus_reopens_the_escape(self) -> None:
        forged_xml = self.facts.domain_xml.replace(
            "</devices>",
            f'<channel type="unix"><source mode="bind" path="/run/user/{self.facts.uid}/bus"/>'
            '<target type="virtio" name="org.qemu.guest_agent.0"/></channel>\n</devices>',
            1,
        )
        contract = guest_contract.build_guest_contract(
            session_id="forged",
            domain_name="forged",
            domain_xml=forged_xml,
            export=self.facts.export,
            guest_roots=DEFAULT_GUEST_ROOTS,
            env=HOSTILE_ENV,
            uid=self.facts.uid,
        )
        audit = guest_contract.audit_guest_contract(contract)
        self.assertFalse(audit.systemd_run.closed)
        self.assertEqual(audit.systemd_run.host_reach, "host_user_bus")


class N6ReachHostDbusTests(_BoundaryCase):
    def test_host_dbus_surfaces_are_closed(self) -> None:
        outcome = structural_reach_host_dbus(self.facts)
        self.assertTrue(outcome.ok, outcome.detail)

    def test_a_planted_dbus_env_name_is_detected(self) -> None:
        forged = replace(
            self.facts.contract,
            env_names=(*self.facts.contract.env_names, "DBUS_SESSION_BUS_ADDRESS"),
        )
        audit = guest_contract.audit_guest_contract(forged)
        verdict = audit.surface("host_dbus_session_env")
        self.assertIsNotNone(verdict)
        assert verdict is not None
        self.assertFalse(verdict.closed)
        self.assertFalse(audit.closed)


class N7SeeHostProcessesTests(_BoundaryCase):
    def test_host_pid_namespace_is_not_shared(self) -> None:
        outcome = structural_see_host_processes(self.facts)
        self.assertTrue(outcome.ok, outcome.detail)

    def test_an_unexpected_host_binding_device_is_caught(self) -> None:
        forged_xml = self.facts.domain_xml.replace(
            "</devices>",
            '<hostdev mode="subsystem" type="usb"/>\n</devices>',
            1,
        )
        tags = _device_tags(forged_xml)
        self.assertTrue(set(tags) - ALLOWED_DEVICE_TAGS)


class N8ReachControlSocketsTests(_BoundaryCase):
    def test_control_sockets_are_absent_and_socket_exports_refused(self) -> None:
        outcome = structural_reach_control_sockets(self.facts)
        self.assertTrue(outcome.ok, outcome.detail)

    def test_a_forged_docker_socket_channel_is_detected(self) -> None:
        forged_xml = self.facts.domain_xml.replace(
            "</devices>",
            '<channel type="unix"><source mode="bind" path="/run/docker.sock"/>'
            '<target type="virtio" name="org.qemu.guest_agent.0"/></channel>\n</devices>',
            1,
        )
        with self.assertRaises(IsolationError) as ctx:
            host_escape.assert_domain_xml_clean(
                forged_xml, allowed_export_paths=(str(self.facts.workspace),)
            )
        self.assertTrue(ctx.exception.reason.startswith("host_escape_surface"))
        self.assertEqual(ctx.exception.code, "host_escape_surface")


class N9MountExtraHostPathsTests(_BoundaryCase):
    def test_exactly_one_filesystem_export(self) -> None:
        outcome = structural_mount_extra_host_paths(self.facts)
        self.assertTrue(outcome.ok, outcome.detail)

    def test_an_extra_filesystem_is_detected(self) -> None:
        forged_xml = self.facts.domain_xml.replace(
            "</devices>",
            '<filesystem type="mount"><source dir="/tmp"/>'
            '<target dir="extra"/></filesystem>\n</devices>',
            1,
        )
        codes = {violation.code for violation in host_escape.scan_domain_xml(forged_xml)}
        self.assertIn("filesystem_export_not_allowed", codes)

    def test_pass_through_accessmode_is_detected(self) -> None:
        forged_xml = self.facts.domain_xml.replace(
            '<filesystem type="mount">',
            '<filesystem type="mount" accessmode="passthrough">',
            1,
        )
        codes = {violation.code for violation in host_escape.scan_domain_xml(forged_xml)}
        self.assertIn("virtiofs_accessmode_passthrough", codes)


class N10AffectAnotherAgentVmTests(_BoundaryCase):
    def test_cross_vm_reach_is_structurally_closed(self) -> None:
        outcome = structural_affect_another_agent_vm(self.facts)
        self.assertTrue(outcome.ok, outcome.detail)

    def test_the_unknown_cid_refusal_is_not_vacuous(self) -> None:
        sessions = SessionMap()
        sessions.assign(PeerCid(41), SessionId("session-a"))
        server = ChannelServer(ChannelConfig(port=43123), sessions)
        left, right = socket.socketpair()
        try:
            channel.write_frame(left, {"kind": "hello", "version": channel.PROTOCOL_VERSION})
            accepted = server.handle_connection(right, PeerCid(41))
            self.assertIsNotNone(accepted, "the host-issued CID must be accepted")
            ack = channel.read_frame(left, max_frame_bytes=4096, timeout_s=2.0)
            self.assertEqual(ack.get("accepted"), True)
            if accepted is not None:
                accepted.close()
        finally:
            left.close()
            right.close()


# --------------------------------------------------------------------------- #
# Tests: live half - fake-runner unit coverage + honest KVM skip
# --------------------------------------------------------------------------- #


class _FakeLiveRunner:
    def __init__(
        self,
        *,
        reports: Mapping[str, str],
        facts: Mapping[str, Mapping[str, str]],
        raise_at_build: str | None = None,
        raise_probe: Exception | None = None,
    ) -> None:
        self._reports = dict(reports)
        self._facts = {key: dict(value) for key, value in facts.items()}
        self._raise_at_build = raise_at_build
        self._raise_probe = raise_probe

    def build_plan(self, key: str) -> LiveProbePlan:
        if self._raise_at_build == key:
            raise LiveProbeUnavailable("fake runner is unavailable")
        return live_probe_plan(key, params={}, nonce="feedface", issued_at=time.time())

    def run_probe(self, plan: LiveProbePlan) -> str:
        if self._raise_probe is not None:
            raise self._raise_probe
        return self._reports[plan.key]

    def host_facts(self, plan: LiveProbePlan) -> Mapping[str, str]:
        return self._facts.get(plan.key, {})


def _clean_live_reports() -> dict[str, str]:
    """Synthetic reports that the verdicts accept (a positive control per item)."""
    return {
        "read_host_home": "PROBE_NONCE=feedface\nHOST_HOME_STATUS=absent\nHOST_HOME_READABLE=no\nHOST_CANARY_READABLE=no\n",
        "write_host_home": "PROBE_NONCE=feedface\nHOST_HOME_WRITE=denied\nGUEST_HOME_WRITE=allowed\n",
        "reach_ssh": "PROBE_NONCE=feedface\nHOST_SSH_STATUS=absent\nHOST_SSH_KEY_READABLE=no\nSSH_AGENT_SOCKET=absent\n",
        "reach_host_systemd": "PROBE_NONCE=feedface\nSYSTEMCTL_RC=1\nHOST_UNIT_IN_GUEST=0\nHOST_UNIT_CAT_RC=1\n",
        "create_host_transient_unit": "PROBE_NONCE=feedface\nSYSTEMD_RUN_RC=1\n",
        "reach_host_dbus": "PROBE_NONCE=feedface\nBUSCTL_RC=1\nHOST_UNIT_IN_BUS=0\nDBUS_SESSION_ADDR=unset\nGUEST_BUS_INODE=none\n",
        "see_host_processes": "PROBE_NONCE=feedface\nHOST_PROC_MATCHES=0\nGUEST_PROC_MATCHES=1\n",
        "reach_control_sockets": "PROBE_NONCE=feedface\nSOCKET_SCAN=done\nSSH_AUTH_SOCK=unset\n",
        "mount_extra_host_paths": "PROBE_NONCE=feedface\nMOUNT_BIND_ROOT_RC=1\nMOUNT_TMPFS_RC=1\nMOUNT_MNT_RC=1\n",
        "affect_another_agent_vm": "PROBE_NONCE=feedface\nGUEST_VSOCK_CID=42\nOTHER_WORKSPACE=absent\nCONTROL_OWNER=match\n",
    }


def _clean_live_facts() -> dict[str, dict[str, str]]:
    return {
        "read_host_home": {"host_canary_present": "yes"},
        "write_host_home": {"host_write_path_absent": "yes"},
        "reach_ssh": {"host_ssh_dir_present": "yes"},
        "reach_host_systemd": {"host_marker_unit_present": "yes"},
        "create_host_transient_unit": {"host_transient_unit_absent": "yes"},
        "reach_host_dbus": {"host_bus_inode": "12345"},
        "see_host_processes": {"host_marker_process_alive": "yes"},
        "reach_control_sockets": {"host_control_socket_present": "yes"},
        "mount_extra_host_paths": {"host_domain_filesystem_count": "1"},
        "affect_another_agent_vm": {
            "host_export_guard_intact": "yes",
            "host_sibling_vm_state": "1",
        },
    }


class LiveProbeEngineTests(unittest.TestCase):
    def test_clean_reports_pass_every_item(self) -> None:
        runner = _FakeLiveRunner(reports=_clean_live_reports(), facts=_clean_live_facts())
        for key in PROBE_KEYS:
            with self.subTest(item=key):
                outcome = run_live_probe(key, runner)
                self.assertTrue(outcome.ok, f"{key}: {outcome.detail}")

    def test_an_unexpected_success_is_a_loud_failure(self) -> None:
        reports = _clean_live_reports()
        reports["read_host_home"] = (
            "PROBE_NONCE=feedface\nHOST_HOME_STATUS=present\nHOST_HOME_READABLE=yes\n"
            "HOST_CANARY_READABLE=yes\n"
        )
        runner = _FakeLiveRunner(reports=reports, facts=_clean_live_facts())

        outcome = run_live_probe("read_host_home", runner)

        self.assertEqual(outcome.status, STATUS_FAIL)
        self.assertIn("$HOME", outcome.detail)

    def test_a_forged_report_is_rejected_not_skipped(self) -> None:
        runner = _FakeLiveRunner(
            reports=_clean_live_reports(),
            facts=_clean_live_facts(),
            raise_probe=ProbeReportRejected("probe_report_nonce_mismatch", "forged"),
        )

        outcome = run_live_probe("read_host_home", runner)

        self.assertEqual(outcome.status, STATUS_FAIL)
        self.assertIn("tamper check", outcome.detail)

    def test_a_false_host_observation_fails_the_item(self) -> None:
        runner = _FakeLiveRunner(
            reports=_clean_live_reports(),
            facts={"read_host_home": {"host_canary_present": "no"}},
        )

        outcome = run_live_probe("read_host_home", runner)

        self.assertEqual(outcome.status, STATUS_FAIL)
        self.assertIn("vacuous", outcome.detail)

    def test_a_missing_host_observation_fails_loudly(self) -> None:
        runner = _FakeLiveRunner(reports=_clean_live_reports(), facts={})

        outcome = run_live_probe("read_host_home", runner)

        self.assertEqual(outcome.status, STATUS_FAIL)
        self.assertIn("host observation missing", outcome.detail)

    def test_an_unavailable_runner_skips_honestly(self) -> None:
        runner = _FakeLiveRunner(
            reports={}, facts={}, raise_at_build="read_host_home"
        )

        outcome = run_live_probe("read_host_home", runner)

        self.assertEqual(outcome.status, STATUS_SKIP)
        self.assertIn("unavailable", outcome.detail)


class LiveProbeSkipTests(unittest.TestCase):
    """Without KVM every live item reports its own SKIP and never a PASS."""

    def test_the_poc_harness_module_loads_with_the_live_surface(self) -> None:
        module = _load_poc_module()

        for name in (
            "RealPocBackend",
            "PocRequest",
            "ProbeLedger",
            "build_poc_task_payload",
            "live_probe_script",
        ):
            self.assertTrue(hasattr(module, name), f"the PoC harness lost {name}")
        for method in (
            "guest_control_dir",
            "workspace_host_path",
            "write_control_file",
            "run_guest_script",
            "report_mtime",
            "session_spec",
        ):
            self.assertTrue(
                hasattr(module.RealPocBackend, method),
                f"the PoC backend lost the live surface {method}",
            )

    def test_the_poc_live_probe_script_carries_the_issued_nonces(self) -> None:
        module = _load_poc_module()

        script = module.live_probe_script(
            guest_control="/workspace/.x",
            host_marker_unit="u",
            guest_probe_unit="g",
            canary_name="c",
            ssh_canary_name="s",
            canary_content="content",
            nonces={"home": "aaaa", "dbus": "bbbb", "procs": "cccc"},
        )

        for nonce in ("aaaa", "bbbb", "cccc"):
            self.assertIn(f"PROBE_NONCE={nonce}", script)

    def test_every_item_skips_with_the_concrete_reason(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != KVM_AVAILABLE_ENV}
        runner = default_live_runner(env)
        self.assertIsInstance(runner, UnavailableLiveRunner)
        outcomes = [run_live_probe(key, runner) for key in PROBE_KEYS]
        for line in render_outcomes(outcomes):
            print(line)
        self.assertEqual([item.status for item in outcomes], [STATUS_SKIP] * len(PROBE_KEYS))
        for item in outcomes:
            self.assertIn(KVM_AVAILABLE_ENV, item.detail)

    def test_a_kvm_less_host_skips_with_the_capability_reason(self) -> None:
        env = dict(os.environ)
        env[KVM_AVAILABLE_ENV] = "1"
        reason = live_probe_skip_reason(env)
        if reason is None:
            self.skipTest("this host reports KVM capability; the negative skip path is untestable here")
        runner = default_live_runner(env)
        outcome = run_live_probe("read_host_home", runner)
        self.assertEqual(outcome.status, STATUS_SKIP)
        self.assertNotEqual(outcome.status, STATUS_PASS)

    def test_a_real_live_run_requires_an_explicit_workspace(self) -> None:
        env = dict(os.environ)
        env[KVM_AVAILABLE_ENV] = "1"
        env.pop("IGOREK_NEGATIVE_WORKSPACE", None)
        with patch.object(domain_module, "kvm_unavailable_reason", return_value=None), patch.object(socket, "AF_VSOCK", 40, create=True):
            runner = default_live_runner(env)
        outcome = run_live_probe("read_host_home", runner)
        self.assertEqual(outcome.status, STATUS_SKIP)
        self.assertIn("IGOREK_NEGATIVE_WORKSPACE", outcome.detail)


class LiveProbeKvmTests(unittest.TestCase):
    """The real KVM run: one guest probe per item, tamper-checked reports."""

    @classmethod
    def setUpClass(cls) -> None:
        reason = live_probe_skip_reason(os.environ)
        if reason is not None:
            raise unittest.SkipTest(f"SKIP: no KVM ({reason})")
        cls._runner = default_live_runner(os.environ)

    @classmethod
    def tearDownClass(cls) -> None:
        runner = getattr(cls, "_runner", None)
        if hasattr(runner, "close"):
            runner.close()

    def _run_item(self, key: str) -> None:
        outcome = run_live_probe(key, self._runner)
        print(f"LIVE {outcome.key}: {outcome.status} - {outcome.detail}")
        if outcome.status == STATUS_SKIP:
            raise unittest.SkipTest(outcome.detail)
        self.assertEqual(outcome.status, STATUS_PASS, outcome.detail)


def _make_live_test(key: str) -> Callable[[LiveProbeKvmTests], None]:
    def _test(self: LiveProbeKvmTests) -> None:
        self._run_item(key)

    _test.__name__ = f"test_live_{key}"
    _test.__qualname__ = f"LiveProbeKvmTests.test_live_{key}"
    return _test


for _key in PROBE_KEYS:
    setattr(LiveProbeKvmTests, f"test_live_{_key}", _make_live_test(_key))


def tamper_check_demo() -> list[str]:
    """Manual QA: the tamper check accepts the real report and rejects forged ones."""
    fresh_delta = 1.0

    def case(
        label: str,
        report: str,
        *,
        mtime_delta: float,
        observations: Mapping[str, bool] | None = None,
    ) -> tuple[str, str]:
        ledger = probe_verification.ProbeLedger()
        challenge = ledger.issue("read_host_home")
        verdict = ledger.verify(
            "read_host_home",
            report.format(nonce=challenge.nonce),
            artifact_mtime=challenge.issued_at + mtime_delta,
            host_observations=observations,
        )
        outcome = "ACCEPTED" if verdict.ok else f"REJECTED ({verdict.reason})"
        return f"TAMPER {label}: {outcome}", challenge.nonce

    lines: list[str] = []
    valid, _nonce = case(
        "valid report", "PROBE_NONCE={nonce}\nHOST_HOME_STATUS=absent\n", mtime_delta=fresh_delta
    )
    lines.append(valid)
    for label, report, delta, observations in (
        ("forged: no nonce", "HOST_HOME_STATUS=absent\n", fresh_delta, None),
        ("forged: foreign nonce", "PROBE_NONCE=deadbeef\nHOST_HOME_STATUS=absent\n", fresh_delta, None),
        ("forged: two nonce lines", "PROBE_NONCE={nonce}\nPROBE_NONCE=deadbeef\nHOST_HOME_STATUS=absent\n", fresh_delta, None),
        ("forged: pre-planted mtime", "PROBE_NONCE={nonce}\nHOST_HOME_STATUS=absent\n", -60.0, None),
        ("forged: contradicted host fact", "PROBE_NONCE={nonce}\nHOST_HOME_STATUS=absent\n", fresh_delta, {"canary_untouched": False}),
    ):
        line, _ = case(label, report, mtime_delta=delta, observations=observations)
        lines.append(line)
    replay_ledger = probe_verification.ProbeLedger()
    replay_challenge = replay_ledger.issue("read_host_home")
    replay_report = f"{replay_challenge.line}\nHOST_HOME_STATUS=absent\n"
    first = replay_ledger.verify(
        "read_host_home", replay_report, artifact_mtime=replay_challenge.issued_at + fresh_delta
    )
    second = replay_ledger.verify(
        "read_host_home", replay_report, artifact_mtime=replay_challenge.issued_at + fresh_delta
    )
    lines.append(
        f"TAMPER replayed valid report: first={'ACCEPTED' if first.ok else 'REJECTED'} "
        f"second={'ACCEPTED' if second.ok else f'REJECTED ({second.reason})'}"
    )
    return lines


def main() -> int:
    """Manual QA entry: per-item checklist plus the tamper-check demonstration."""
    outcomes = list(run_structural_checklist())
    runner = default_live_runner(os.environ)
    outcomes.extend(run_live_probe(key, runner) for key in PROBE_KEYS)
    for line in render_outcomes(outcomes):
        print(line)
    for line in tamper_check_demo():
        print(line)
    failures = [item for item in outcomes if item.status == STATUS_FAIL]
    print(f"CHECKLIST total={len(outcomes)} pass={sum(i.ok for i in outcomes)} "
          f"skip={sum(i.status == STATUS_SKIP for i in outcomes)} fail={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--checklist":
        raise SystemExit(main())
    unittest.main(verbosity=2)


def test_live_runner_uses_the_configured_guest_channel_port(monkeypatch):
    from types import SimpleNamespace

    captured = {}

    class Backend:
        def __init__(self, request, **_kwargs):
            captured.update(request.__dict__)
        def prepare(self):
            pass
        def start_session(self):
            pass
        def cleanup(self):
            pass

    class Request:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    module = SimpleNamespace(PocRequest=Request, RealPocBackend=Backend, DEFAULT_POC_VSOCK_PORT=43011)
    monkeypatch.setattr(sys.modules[__name__], "_load_poc_module", lambda: module)
    runner = PocLiveRunner(profile="fixture", workspace="/tmp/fixture", env={"IGOREK_ISOLATION_VSOCK_PORT": "5005"})
    assert captured == {"profile": "fixture", "workspace": "/tmp/fixture", "vsock_port": 5005}
    runner.close()
