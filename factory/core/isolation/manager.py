"""Per-session VM orchestration facade for the isolation layer (todo 6).

The manager is the single host-side entry point the worker seam talks to. It
composes, and never reimplements, the committed isolation modules:

* :mod:`factory.core.isolation.config` - the resolved feature flag/limits;
* :mod:`factory.core.isolation.preflight` - the fail-closed start gate;
* :mod:`factory.core.isolation.domain` - one libvirt domain per session;
* :mod:`factory.core.isolation.workspace` - the single allow-listed export;
* :mod:`factory.core.isolation.channel` - the framed vsock protocol;
* :mod:`factory.core.isolation.errors` - the canonical ``IsolationError``.

Lifecycle: ``ensure_vm`` is idempotent per session id, ``run_task`` dispatches
one turn to that VM and normalizes the guest ``result`` frame into the same
shape ``execute_cc_backend_task`` returns, ``stop`` tears a session down and
``reap_orphans`` removes leftovers from a previous host process. Every VM is
defined with the single restricted QEMU user-mode interface (todo 16,
:mod:`factory.core.isolation.network`), never a host bridge.

Fail-closed rules:

* mode ``off`` must never create a VM here, so ``ensure_vm`` refuses before any
  probe when the config is not ``kvm``;
* a refused preflight is surfaced verbatim (``isolation_unavailable:<detail>``),
  never degraded to a host run;
* a session whose vsock CID libvirt does not report gets no VM: an unmappable
  guest could never be authenticated by ``SessionMap``;
* a frame the manager cannot safely interpret (guest ``host_op`` frames,
  malformed results) raises ``IsolationError`` instead of being dropped or
  treated as success.

Guest ``event`` frames are relayed into the host trace file by
:class:`factory.core.isolation.relay.TraceRelay` (todo 8), so the existing
mobile pipeline sees the same records an off-mode run writes. Guest
``approval_request`` frames are the approval relay (todo 9,
:class:`factory.core.isolation.approval.ApprovalRelay`): the one-shot guest runner
process exits after parking the task, while its VM and overlay remain warm. A
resumed task reconnects to that VM with approved calls mapped back to guest paths.

Guest ``user_input_request`` frames are the user-input relay (todo 10,
:class:`factory.core.isolation.user_input.UserInputRelay`): the host publishes
the person's form in the existing user-input store, waits for the mobile
decision, and answers the SAME guest run on the live channel; a cancel - or the
relay timeout - ends it with ``result{status:"canceled"}``, which
``_normalize_result`` folds into a terminal ``fail``.

Stop propagation (todo 11): a mobile Stop keeps its existing task transition
(the row flips to ``cancelling``) and reaches the VM through the host stop
signal the worker passes as ``cancel_check``. On an acknowledged
``result{status:"canceled"}``, only that task and its one-shot runner channel
end; the VM, overlay and workspace export remain warm for the next message. If
the guest does not acknowledge before the bounded timeout, its state is treated
as unrecoverable and the VM is destroyed. ``cancel`` explicitly ends the whole
session VM. A parked ``waiting_approval`` task retains its VM for resume.

Still owned by later todos: the host-op broker (todo 19). MCP servers, Vault
materialization and skill assets are placed by todo 31
(:mod:`factory.core.isolation.extensions`): the manager collects the placements
for each dispatch, writes every missing-capability status into the host trace
and carries the bounded sections in the same versioned payload.

Parallel sessions (todo 23): the VM table is keyed by session id and mutated
under one lock, so two profiles run side by side with distinct domains,
overlays, vsock CIDs and exports. Cross-session access is refused structurally:
each live VM leases its overlay for as long as it runs
(:mod:`factory.core.isolation.overlay_guard`), so a sibling manager - in this
process or another one - can neither adopt a live session's overlay
(``overlay_busy``) nor reap it, and a dispatch is refused
(``peer_cid_conflict``) when the host's CID map routes that session's CID to a
different session. Nothing writable is shared between two VMs.

Resource limits (todo 24): every VM carries the configured CPU/memory cgroup
caps in its domain XML and its overlay is created at the configured virtual-size
cap, so fork/memory/disk/FD abuse inside one guest cannot reach the host or a
sibling. A guest that reports a breach (``enospc``, ``oom``, a fork/FD
exhaustion) is contained here - the VM is destroyed, the overlay removed and the
breach written to the host trace as ``isolation.limit_exceeded`` - and the task
gets a terminal ``fail`` whose ``failure_reason`` names the breached class.

Egress enforcement (todo 40) is decided here and applied in the guest: the
manager renders the per-session policy (``network``) into the payload's
``egress`` section, so the guest applies exactly this session's allowlist before
its loop starts, and it refuses a dispatch whose guest-bound env carries a
provider secret (``secret_scan``) before any frame is sent. The manager is
unit-testable without a hypervisor: every boundary above is an injected seam
with a real production default (``ManagerSeams``).

Teardown policy (todo 26) is owned by :mod:`factory.core.isolation.lifecycle`
and driven here: every session gets a lifecycle record
(``cold -> starting -> warm -> idle -> stopped -> reaped``), a dispatch marks the
VM warm and in flight, and its end marks the VM idle. A VM whose idle TTL
expires is torn down through the same host-state release the stop/cancel path
uses (:meth:`IsolationManager.reap_idle`), so a warm/idle VM with no in-flight
task is never kept alive forever; a dispatch that is still in flight is never a
sweep candidate, which is what keeps a checkpoint write from being cut in half.
:meth:`IsolationManager.start_idle_sweeper` is the production driver (started by
:meth:`IsolationManager.from_env` for an enabled config) and ``off`` mode never
starts one. Persistent data stays on the host: the workspace (with the durable
``.igorek-host-data`` artifacts the guest writes through the export) and the
host trace survive every teardown, while the overlay - which carries the guest
installs, temp files and caches - is disposable by design.

Fail-safe behaviour (todo 25) is contained here as well:

* a dispatch whose VMM died (libvirt reports the domain not running) or whose
  guest channel broke is *contained*, never left half-registered: the domain is
  destroyed/undefined, the overlay removed, the endpoint closed and the CID
  binding, overlay lease and workspace export released, and the task gets a
  terminal ``fail`` with a clear ``error``/``failure_reason`` (``vm_lost`` or
  ``channel_lost``) - never a success;
* ``stop``/``cancel``/limit containment share one session teardown
  (:meth:`IsolationManager._contain_session`), which logs a teardown failure and
  releases the host state anyway so a sibling session and the host stay healthy;
* :meth:`IsolationManager.reap_orphans` removes what no live session owns -
  leftover domains, overlays *and* stale channel endpoints/CID bindings - and
  :meth:`IsolationManager.owns_overlay` answers the cross-process liveness
  question (a live lease) the gateway-start recovery uses to tell an orphaned
  task from a running one (``factory.core.isolation.startup``).

# allow: SIZE_OK - one cohesive facade (lifecycle + dispatch + cleanup) over the
# same per-session state; splitting it would separate halves of one invariant.
"""

from __future__ import annotations

from .task_transfer import pack_task

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Protocol, assert_never
from urllib.parse import urlsplit, urlunsplit

from .admission import (
    ADMISSION_DIRNAME,
    AdmissionController,
    CapacityLimits,
    CapacitySnapshot,
    CapacityUsage,
    CapacityWait,
    emit_capacity_wait,
)
from .approval import ApprovalRelay, WorkspacePathMapping, resume_wire_metadata
from .artifact_map import (
    ARTIFACT_DIRNAME,
    guest_artifact_dir,
    host_artifact_dir,
    write_artifact_route,
)
from .channel import (
    HEARTBEAT_INTERVAL_S,
    ChannelConfig,
    ChannelError,
    ChannelMessage,
    ChannelServer,
    JsonValue,
    MessageKind,
    PeerCid,
    SessionId,
    SessionMap,
)
from .config import DEFAULT_VSOCK_PORT, MODE_KVM, IsolationConfig
from .domain import (
    CommandRunner,
    DomainSpec,
    LibvirtConnection,
    SessionDomain,
    domain_name,
    open_libvirt_connection,
    overlay_for_session,
    overlay_path,
    virtiofs_socket_for_session,
)
from ..trace_taxonomy import CcTraceEvent
from .egress_auth import ProviderAuthInjector
from .git_gateway import GitGateway, GitRepositoryGrant, repository_grants, grant_for_workspace
from .git_gateway_contract import GitGatewayRoute
from .apk_delivery import ApkDelivery, ENV_DELIVERY
from .errors import IsolationError
from .export_guard import WorkspaceExportGuard
from .session_egress import SessionEgress, build_session_egress
from .extensions import (
    MCP_CREDENTIAL_EPHEMERAL_ENV,
    MCP_PLACEMENT_HOST_BROKERED,
    CapabilityStatus,
    McpSources,
    TaskExtensions,
    collect_task_extensions,
    emit_capability_statuses,
    rewrite_skill_runtime_paths,
)
from .guest_user_input import DEFAULT_USER_INPUT_TIMEOUT_S
from .history import (
    HostTaskInput,
    RehydratedTaskMetadata,
    build_wire_task_payload,
)
from .limits import (
    LimitBreach,
    classify_limit_breach,
    emit_limit_breach,
    limit_failure_result,
    memory_hard_limit_mib,
)
from .lifecycle import (
    LifecycleState,
    LifecycleTracker,
    TeardownTrigger,
)
from .host_op_runtime import HostOpApprovalRuntime
from .preflight import PreflightResult, preflight
from .network import HOST_LOOPBACK_ALIAS, is_host_loopback
from .network_sources import HostResolver, system_resolver
from .overlay_guard import OverlayLeaseGuard
from .relay import TraceRelay
from .secret_policy import ENV_EGRESS_AUTH, MODE_PROXY, ProviderSecretPlan
from .secret_scan import (
    GUEST_ENV_LEAK_REASON,
    SECRET_KEY_NAMES,
    GuestEnvLeakError,
    assert_guest_env_clean,
)
from .user_input import UserInputRelay
from .workspace import (
    GUEST_WORKSPACE_PATH,
    WorkspaceExportSpec,
    WorkspacePolicy,
    resolve_workspace_export,
)

logger = logging.getLogger(__name__)
MAX_WEB_REQUESTS_PER_TASK = 20
MAX_WEB_RESULT_BYTES = 512 * 1024
MAX_SESSION_SEARCH_REQUESTS_PER_TASK = 20
MAX_SESSION_SEARCH_RESULT_BYTES = 512 * 1024
MAX_MEMORY_FORGET_REQUESTS_PER_TASK = 10


class MemoryForgetRelay:
    """Relay explicit, exact fact deletions to the host-owned memory file."""

    def __init__(self, user_message: str, *, task_id: str = '', timeout_s: float = DEFAULT_USER_INPUT_TIMEOUT_S) -> None:
        self._user_message = user_message
        self._task_id = task_id
        self._timeout_s = timeout_s
        self._replies: dict[str, dict[str, JsonValue]] = {}
        self._request_count = 0

    def handle_request(self, message: ChannelMessage) -> dict[str, JsonValue] | None:
        if message.kind is not MessageKind.MEMORY_FORGET_REQUEST:
            return None
        request_id, target = message.payload.get("request_id"), message.payload.get("target")
        if not isinstance(request_id, str) or not isinstance(target, str):
            return None
        cached = self._replies.get(request_id)
        if cached is not None:
            return dict(cached)
        self._request_count += 1
        if self._request_count > MAX_MEMORY_FORGET_REQUESTS_PER_TASK:
            result = '{"changed":false,"rejected":true,"reason":"request_limit"}'
        else:
            try:
                from ..cc_backend_tools import execute_isolated_memory_forget
                result = execute_isolated_memory_forget(
                    {"target": target}, user_message=self._user_message,
                    task_id=self._task_id, timeout_s=self._timeout_s,
                )
            except Exception as exc:
                logger.warning("isolation memory forget relay failed (%s)", type(exc).__name__)
                result = '{"changed":false,"rejected":true,"reason":"host_memory_operation_failed"}'
        if len(result.encode("utf-8")) > 16 * 1024:
            result = '{"changed":false,"rejected":true,"reason":"response_too_large"}'
        reply: dict[str, JsonValue] = {"request_id": request_id, "result": result}
        self._replies[request_id] = reply
        return dict(reply)


class SessionSearchRelay:
    """Relay bounded recall queries to the host-owned session index."""

    def __init__(self, current_session: str | None = None) -> None:
        self._current_session = current_session
        self._replies: dict[str, dict[str, JsonValue]] = {}
        self._request_count = 0

    def handle_request(self, message: ChannelMessage) -> dict[str, JsonValue] | None:
        if message.kind is not MessageKind.SESSION_SEARCH_REQUEST:
            return None
        request_id, args = message.payload.get("request_id"), message.payload.get("args")
        if not isinstance(request_id, str) or not isinstance(args, dict):
            return None
        cached = self._replies.get(request_id)
        if cached is not None:
            return dict(cached)
        self._request_count += 1
        if self._request_count > MAX_SESSION_SEARCH_REQUESTS_PER_TASK:
            result = '{"success":false,"error":"session search request limit reached for this task"}'
        else:
            try:
                from ..cc_backend_tools import execute_isolated_session_search
                result = execute_isolated_session_search(
                    dict(args), current_session=self._current_session
                )
            except Exception as exc:
                logger.warning("isolation session search relay failed (%s)", type(exc).__name__)
                result = '{"success":false,"error":"host session search failed"}'
        if len(result.encode("utf-8")) > MAX_SESSION_SEARCH_RESULT_BYTES:
            result = '{"success":false,"error":"session search response exceeded size limit"}'
        reply: dict[str, JsonValue] = {"request_id": request_id, "result": result}
        self._replies[request_id] = reply
        return dict(reply)


class WebToolRelay:
    """Relay bounded web operations through host-owned services."""

    def __init__(self) -> None:
        self._replies: dict[str, dict[str, JsonValue]] = {}
        self._request_count = 0

    def handle_request(self, message: ChannelMessage) -> dict[str, JsonValue] | None:
        if message.kind is not MessageKind.WEB_REQUEST:
            return None
        request_id = message.payload.get("request_id")
        operation = message.payload.get("operation")
        args = message.payload.get("args")
        if not isinstance(request_id, str) or not isinstance(operation, str) or not isinstance(args, dict):
            return None
        cached = self._replies.get(request_id)
        if cached is not None:
            return dict(cached)
        self._request_count += 1
        if self._request_count > MAX_WEB_REQUESTS_PER_TASK:
            result = '{"success":false,"error":"web request limit reached for this task"}'
        else:
            try:
                from ..cc_backend_tools import execute_isolated_web_tool
                result = execute_isolated_web_tool(operation, dict(args))
            except Exception as exc:  # host boundary: fail without disclosing env/secrets
                logger.warning("isolation web relay failed (%s)", type(exc).__name__)
                result = json.dumps({"success": False, "error": "host web relay failed"})
        if len(result.encode("utf-8")) > MAX_WEB_RESULT_BYTES:
            result = json.dumps({"success": False, "error": "web response exceeded size limit"})
        reply: dict[str, JsonValue] = {"request_id": request_id, "result": result}
        self._replies[request_id] = reply
        return dict(reply)

__all__ = [
    "CANCEL_FRAME_REASON",
    "CANCEL_POLL_S",
    "DEFAULT_CANCEL_ACK_TIMEOUT_S",
    "DEFAULT_GUEST_STALL_TIMEOUT_S",
    "DOMAIN_NAME_PREFIX",
    "FAILURE_REASON_CHANNEL_LOST",
    "FAILURE_REASON_GUEST_STALLED",
    "FAILURE_REASON_RESULT_TIMEOUT",
    "FAILURE_REASON_VM_LOST",
    "GUEST_ARTIFACT_DIR",
    "STOP_REASON_CANCEL_TIMEOUT",
    "STOP_REASON_CANCELED",
    "STOP_REASON_IDLE_TTL",
    "STOP_REASON_NO_VM",
    "EndpointRegistry",
    "HostEndpoint",
    "IsolationManager",
    "ManagerSeams",
    "SessionSpec",
    "SessionVm",
    "StopReport",
    "resolve_vsock_cid",
]

# Mirrors ``domain.domain_name``; a unit test pins the two together.
DOMAIN_NAME_PREFIX: Final[str] = "igorek-session-"
# Legacy guest-side artifact default (``/tmp`` is a channel-allowed root); the
# constant stays as the guest contract value mirrored by
# ``guest_task.DEFAULT_GUEST_ARTIFACT_DIR``. A dispatch no longer sends it: the
# manager routes each task's artifact dir into the exported workspace
# (``<guest workdir>/.igorek-artifacts/<task8>``, see
# :mod:`factory.core.isolation.artifact_map`) so the host can read the subagent
# journal through the single virtiofs share.
GUEST_ARTIFACT_DIR: Final[str] = "/tmp/igorek-artifacts"
# Statuses the task table accepts (``worker.update_task`` vocabulary).
_RESULT_STATUSES: Final[frozenset[str]] = frozenset(
    {"ok", "fail", "retry", "waiting_approval"}
)
# Guest-runner status for a run the host aborted; folded into a terminal ``fail``.
_CANCELED_STATUSES: Final[frozenset[str]] = frozenset({"canceled"})
_CANCELED_DETAILS: Final[str] = "guest run canceled"
_RESULT_TIMEOUT_MARGIN_S: Final[float] = 30.0
_CID_IN_XML: Final = re.compile(r"<cid[^>]*\baddress=['\"](\d+)['\"]")
# How long the host waits for a guest to acknowledge a ``cancel`` before tearing
# the domain down anyway. Deliberately short: the teardown is the guarantee, the
# acknowledgement only shortens the wait.
DEFAULT_CANCEL_ACK_TIMEOUT_S: Final[float] = 5.0
CANCEL_FRAME_REASON: Final[str] = "user_stop"
CANCEL_POLL_S: Final[float] = 0.25
STOP_REASON_CANCELED: Final[str] = "canceled"
STOP_REASON_CANCEL_TIMEOUT: Final[str] = "cancel_timeout"
STOP_REASON_NO_VM: Final[str] = "no_vm"
# Idle-TTL teardown (todo 26): the VM was destroyed because no dispatch was in
# flight for longer than the configured idle TTL.
STOP_REASON_IDLE_TTL: Final[str] = "idle_ttl"
# Idle sweeper cadence: at least this often, and at least four sweeps per TTL,
# so an expired VM is reaped promptly without a busy loop.
IDLE_SWEEP_MIN_INTERVAL_S: Final[float] = 0.5
_IDLE_SWEEP_TTL_FRACTION: Final[float] = 0.25
# Terminal failure codes for a dispatch whose VM/guest disappeared (todo 25).
FAILURE_REASON_VM_LOST: Final[str] = "vm_lost"
FAILURE_REASON_CHANNEL_LOST: Final[str] = "channel_lost"
# Terminal failure codes for a dispatch that exceeded its own budget or whose
# guest stopped emitting frames (heartbeat-driven fast stall detection).
FAILURE_REASON_RESULT_TIMEOUT: Final[str] = "result_timeout"
FAILURE_REASON_GUEST_STALLED: Final[str] = "guest_stalled"
#: How long the guest may stay completely silent (no frame, not even a
#: heartbeat) before the host declares it stalled and destroys the VM. Bounded,
#: far below a typical task timeout, so a frozen guest fails in ~90s instead of
#: holding the task for the full ``limits_timeout_s``.
DEFAULT_GUEST_STALL_TIMEOUT_S: Final[float] = 90.0


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """Host facts one VM is created for: which session, which workspace."""

    session_id: str
    workspace_cwd: str


@dataclass(frozen=True, slots=True)
class SessionVm:
    """A live per-session VM and its single allow-listed workspace export."""

    session_id: str
    spec: DomainSpec
    domain: SessionDomain
    export: WorkspaceExportSpec
    peer_cid: PeerCid


@dataclass(frozen=True, slots=True)
class StopReport:
    """What one stop observed and tore down (todo 11).

    ``reason`` is :data:`STOP_REASON_CANCELED` when the guest acknowledged the
    stop with ``result{status:"canceled"}``, :data:`STOP_REASON_CANCEL_TIMEOUT`
    when it did not answer within the TTL (the VM is destroyed anyway),
    :data:`STOP_REASON_IDLE_TTL` when the teardown came from the idle policy
    (todo 26, no in-flight dispatch, so nothing was asked to acknowledge) and
    :data:`STOP_REASON_NO_VM` when the session held no VM - a parked
    ``waiting_approval`` task has none by design.
    """

    session_id: str
    reason: str
    acknowledged: bool
    domain_destroyed: bool
    overlay_removed: bool


class HostEndpoint(Protocol):
    """Host half of one guest connection (``channel.PeerSession`` in production)."""

    def send(
        self, kind: MessageKind, payload: Mapping[str, JsonValue]
    ) -> None: ...
    def recv_from_guest(
        self, timeout_s: float | None = None
    ) -> ChannelMessage | None: ...
    def close(self) -> None: ...


class EndpointRegistry(Protocol):
    """Channel surface the manager needs: endpoint lookup and shutdown."""

    def session_for(self, session_id: SessionId) -> HostEndpoint | None: ...
    def close(self) -> None: ...


class CidResolver(Protocol):
    """Finds the vsock CID libvirt assigned to a started domain."""

    def __call__(
        self, connection: LibvirtConnection, name: str
    ) -> PeerCid | None: ...


def resolve_vsock_cid(
    connection: LibvirtConnection, name: str
) -> PeerCid | None:
    """Read the CID from the live domain XML; ``None`` when libvirt omits it.

    ``auto='yes'`` leaves the CID to QEMU, so the host can only learn it after
    the domain is running. When the live XML carries no positive address the
    manager refuses the VM (fail-closed): an unmappable guest could never pass
    the channel handshake.
    """
    try:
        domain = connection.lookupByName(name)
    except Exception:  # libvirt boundary: an absent domain/connection is "unknown"
        logger.debug("isolation: cannot look up domain %s while resolving its CID", name)
        return None
    xml_desc = getattr(domain, "XMLDesc", None)
    if xml_desc is None:
        return None
    try:
        xml = xml_desc(0)
    except Exception:  # libvirt boundary: cannot read the live XML -> unknown
        return None
    return _parse_vsock_cid(str(xml or ""))


def _parse_vsock_cid(xml: str) -> PeerCid | None:
    match = _CID_IN_XML.search(xml)
    if match is None:
        return None
    cid = int(match.group(1))
    return PeerCid(cid) if cid > 0 else None


Preflight = Callable[[IsolationConfig], PreflightResult]
ExtensionsCollector = Callable[[Mapping[str, Any]], TaskExtensions]


class EgressBuilder(Protocol):
    """Renders the per-session egress decision for one dispatch (plan todo 40a)."""

    def __call__(
        self, profile: str, base_url: str, *, host_local_ports: tuple[int, ...] = ()
    ) -> SessionEgress: ...


def _profile_mcp_servers(profile: str) -> tuple[str, ...]:
    """MCP servers enabled for the profile; their endpoints join the allowlist."""
    name = (profile or "").strip()
    if not name:
        return ()
    try:
        from ..mcp_config import get_profile_mcp_servers
    except ImportError:  # pragma: no cover - direct script fallback
        return ()
    try:
        return tuple(str(item).strip() for item in get_profile_mcp_servers(name) if str(item).strip())
    except Exception as exc:  # host config boundary: a missing allow is fail-closed
        logger.warning("isolation: profile %s MCP list unavailable for egress (%s)", name, exc)
        return ()


def build_dispatch_egress(
    profile: str,
    base_url: str,
    *,
    host_local_ports: tuple[int, ...] = (),
    env: Mapping[str, str] | None = None,
    resolver: HostResolver | None = None,
) -> SessionEgress:
    """Production renderer: the host's env and resolver own the allowlist."""
    source = os.environ if env is None else env
    resolve = system_resolver if resolver is None else resolver
    return build_session_egress(
        profile,
        base_url=base_url,
        env=source,
        resolver=resolve,
        host_local_ports=host_local_ports,
        extra_mcp_servers=_profile_mcp_servers(profile),
    )


@dataclass(frozen=True, slots=True)
class ManagerSeams:
    """External boundaries; production defaults are the real host implementations."""

    connect: Callable[[str], LibvirtConnection] = open_libvirt_connection
    runner: CommandRunner | None = None
    channel: EndpointRegistry | None = None
    session_map: SessionMap | None = None
    workspace_policy: WorkspacePolicy | None = None
    overlay_leases: OverlayLeaseGuard | None = None
    preflight_fn: Preflight = preflight
    cid_resolver: CidResolver = resolve_vsock_cid
    extensions: ExtensionsCollector | None = None
    provider_secrets: ProviderAuthInjector | None = None
    git_repositories: tuple[GitRepositoryGrant, ...] = ()
    apk_delivery_enabled: bool = False
    vault_value_writer: Any = None
    egress: EgressBuilder | None = None
    host_ops: HostOpApprovalRuntime | None = None
    admission: AdmissionController | None = None
    lifecycle_clock: Callable[[], float] | None = None
    """Idle-TTL clock seam (todo 26); production uses ``time.monotonic``."""
    endpoint_wait_s: float | None = None
    guest_stall_timeout_s: float | None = None
    """Fast-stall window override; a value ``> 0`` wins, otherwise the default."""


class IsolationManager:
    """Owns one VM per session: lifecycle, dispatch and orphan cleanup."""

    def __init__(
        self,
        config: IsolationConfig,
        *,
        seams: ManagerSeams | None = None,
        cancel_ack_timeout_s: float = DEFAULT_CANCEL_ACK_TIMEOUT_S,
    ) -> None:
        self._config = config
        self._seams = seams if seams is not None else ManagerSeams()
        self._cancel_ack_timeout_s = max(0.0, float(cancel_ack_timeout_s))
        stall_override = self._seams.guest_stall_timeout_s
        self._guest_stall_timeout_s = (
            float(stall_override)
            if stall_override is not None and stall_override > 0
            else DEFAULT_GUEST_STALL_TIMEOUT_S
        )
        self._vms: dict[str, SessionVm] = {}
        self._terminal_leases: dict[str, tuple[str, SessionVm, float]] = {}
        self._vm_lock = threading.Lock()
        self._session_map = (
            self._seams.session_map
            if self._seams.session_map is not None
            else SessionMap()
        )
        self._channel_server: ChannelServer | None = None
        self._listener_thread: threading.Thread | None = None
        self._listener_stop = threading.Event()
        self._connection: LibvirtConnection | None = None
        self._export_guard: WorkspaceExportGuard | None = None
        self._leases_guard: OverlayLeaseGuard | None = self._seams.overlay_leases
        self._admission_controller: AdmissionController | None = self._seams.admission
        self._session_locks: dict[str, threading.Lock] = {}
        self._lifecycle = LifecycleTracker(
            ttl_s=float(config.idle_ttl_s),
            clock=(
                self._seams.lifecycle_clock
                if self._seams.lifecycle_clock is not None
                else time.monotonic
            ),
        )
        self._sweeper_lock = threading.Lock()
        self._sweeper: threading.Thread | None = None
        self._sweeper_stop = threading.Event()

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, seams: ManagerSeams | None = None
    ) -> IsolationManager:
        """Build the manager from ``IGOREK_ISOLATION_*`` (fail-closed config).

        Provider auth injection is opt-in: ``IGOREK_ISOLATION_EGRESS_AUTH``
        enables the host-side proxy seam (todo 18). Without it the manager
        behaves exactly as before and no payload field changes.
        """
        source = os.environ if env is None else env
        resolved = seams if seams is not None else ManagerSeams()
        if resolved.provider_secrets is None and str(source.get(ENV_EGRESS_AUTH) or "").strip():
            resolved = replace(
                resolved, provider_secrets=ProviderAuthInjector.from_env(source)
            )
        if not resolved.git_repositories:
            resolved = replace(resolved, git_repositories=repository_grants(source))
        resolved = replace(resolved, apk_delivery_enabled=resolved.apk_delivery_enabled or source.get(ENV_DELIVERY, "off").lower() == "on")
        manager = cls(IsolationConfig.from_env(env), seams=resolved)
        if manager._config.enabled:
            # Production driver for the idle TTL (todo 26): a worker process that
            # holds a warm VM must release it without waiting for the next task.
            manager.start_idle_sweeper()
        return manager

    @property
    def channel_server(self) -> ChannelServer:
        """Host-side registry/listener; run ``serve_forever`` to accept guests."""
        if self._channel_server is None:
            self._channel_server = ChannelServer(
                ChannelConfig(port=self._config.vsock_port), self._session_map
            )
        return self._channel_server

    def _ensure_channel_listener(self) -> None:
        """Run the AF_VSOCK listener once, before the first VM is started.

        The channel server is only a registry until ``serve_forever`` runs; the
        PoC driver started it explicitly, but the production worker/manager had
        no caller, so a session VM booted while nothing listened and the guest
        runner failed to connect on every restart. Started lazily (first VM) as
        one daemon thread bound to the configured vsock port.
        """
        if self._listener_thread is not None:
            return
        if self._seams.channel is not None:
            # An injected registry owns endpoint lookup (tests/PoC); the real
            # AF_VSOCK listener is only needed when this process is the server.
            return
        server = self.channel_server
        self._listener_stop.clear()

        def _serve() -> None:
            try:
                server.serve_forever(self._listener_stop)
            except Exception as exc:  # surfaced via the task failure
                logger.warning("isolation: channel listener stopped: %s", exc)

        self._listener_thread = threading.Thread(
            target=_serve, name="isolation-channel-listener", daemon=True
        )
        self._listener_thread.start()

    def _maybe_take_over_export(self, host_path: str, requester: str) -> None:
        """Free the requested workspace from a quiescent warm VM of another session.

        One live export per realpath is required, so a fresh session on the same
        folder would be refused ``workspace_busy`` until the idle TTL reaps the
        previous one. Stopping that idle holder immediately keeps the invariant
        (still one live export) while letting the user start a new chat on the
        same folder without waiting; an in-flight holder is never touched.
        """
        real = os.path.realpath(str(host_path))
        with self._vm_lock:
            holders = [
                sid
                for sid, vm in self._vms.items()
                if sid != requester
                and str(vm.export.get("host_path") or "")
                and os.path.realpath(str(vm.export["host_path"])) == real
            ]
        for sid in holders:
            if self.terminal_held(sid):
                continue
            record = self._lifecycle.record_of(sid)
            if record is not None and record.in_flight > 0:
                continue
            self.stop(sid, trigger=TeardownTrigger.SESSION_STOP)

    def ensure_vm(
        self,
        session: SessionSpec,
        *,
        on_capacity_wait: Callable[[CapacityWait], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> SessionVm:
        """Return the live VM for ``session``, starting it on first use.

        The workspace realpath is claimed before any host side effect: a second
        session (or another process) that already exports the same realpath is
        refused with ``workspace_busy``, so two guests can never write the same
        host tree at once (todo 32). The overlay is then leased (todo 23): a
        second manager - another profile's worker process, or a resumed session
        whose VM is still alive - cannot take over a live session's overlay
        (``overlay_busy``), and the lease is what keeps a sibling's
        ``reap_orphans`` from destroying this VM.

        Admission control (todo 33) runs before the VM is defined: the session
        claims one host-wide slot within the RAM/disk budget and, when the
        budget is full, waits while ``on_capacity_wait`` receives the visible
        status records (``should_abort`` ends that wait when the task is
        stopped). A failed start releases the slot, so the accounting follows
        the VM lifecycle and never leaks capacity.

        The whole table mutation runs under one lock, so two profiles starting
        concurrently each get their own domain/overlay/CID/export; the lock is
        never held while waiting for a guest, so running sessions stay parallel.
        """
        session_id = _session_id_of(session)
        if self._config.mode != MODE_KVM:
            raise IsolationError(
                "isolation_not_enabled",
                f"isolation mode is {self._config.mode!r}; refusing to start a VM",
            )
        policy = self._workspace_policy()
        workspace = resolve_workspace_export(session.workspace_cwd, policy=policy)
        host_path = Path(workspace["host_path"])

        with self._session_lock(session_id):
            with self._vm_lock:
                existing = self._vms.get(session_id)
            if existing is not None:
                self._existing_vm(existing, host_path, session_id)
                if existing.domain.is_active():
                    return existing
                logger.warning(
                    "isolation: session %s VM is stopped; rebuilding only after "
                    "libvirt confirmed it is inactive",
                    session_id,
                )
                self._contain_session(
                    session_id,
                    self._endpoint_for(session_id),
                    trigger=TeardownTrigger.VM_LOST,
                )

            self._lifecycle.begin_start(session_id)
            self._admission.acquire(
                session_id,
                memory_mib=memory_hard_limit_mib(self._config.session_memory_mb),
                disk_gib=self._config.session_disk_gb,
                on_wait=on_capacity_wait,
                should_abort=should_abort,
            )
            with self._vm_lock:
                existing = self._vms.get(session_id)
            if existing is not None:
                return self._existing_vm(existing, host_path, session_id)
            self._maybe_take_over_export(host_path, session_id)
            try:
                with self._vm_lock:
                    self._exports.acquire(host_path, session_id)
                    try:
                        result = self._seams.preflight_fn(self._config)
                        if not result.ok:
                            raise IsolationError(
                                result.reason or "isolation_unavailable",
                                "preflight refused the VM",
                            )
                        base_image = self._config.base_image
                        if base_image is None:
                            raise IsolationError(
                                "base_image_missing", "config carries no base image"
                            )

                        spec = DomainSpec(
                            session_id=session_id,
                            base_image=base_image,
                            workspace_host_path=host_path,
                            overlay_dir=self._config.overlay_dir,
                            vcpus=self._config.session_vcpus,
                            memory_mib=self._config.session_memory_mb,
                            disk_gib=self._config.session_disk_gb,
                            network=True,
                            guest_agent_uid=policy.guest_agent_uid,
                            host_uid=policy.host_uid,
                            guest_agent_gid=policy.guest_agent_gid,
                            host_gid=policy.host_gid,
                            virtiofs_socket=(
                                virtiofs_socket_for_session(session_id)
                                if self._config.libvirt_uri == "qemu:///system"
                                else None
                            ),
                        )
                        self._leases.acquire(overlay_path(spec), session_id)
                        self._ensure_channel_listener()
                        connection = self._ensure_connection()
                        domain = SessionDomain(connection, self._seams.runner)
                        try:
                            domain.define(spec)
                            domain.start()
                            peer_cid = self._seams.cid_resolver(connection, domain_name(spec))
                            if peer_cid is None:
                                raise IsolationError(
                                    "vsock_cid_unavailable",
                                    f"libvirt reported no vsock CID for {domain_name(spec)}",
                                )
                            owner = self._session_map.session_for_cid(peer_cid)
                            if owner is not None and str(owner) != session_id:
                                raise IsolationError(
                                    "peer_cid_conflict",
                                    f"vsock CID {peer_cid} already belongs to session {owner!r}",
                                )
                            try:
                                self._session_map.assign(peer_cid, SessionId(session_id))
                            except ChannelError as exc:
                                raise IsolationError(
                                    f"peer_cid_conflict:{exc.reason}", exc.detail
                                ) from exc
                        except Exception:
                            self._teardown_quietly(domain)
                            raise
                    except Exception:
                        self._leases.release(session_id)
                        self._exports.release(session_id)
                        raise

                    vm = SessionVm(
                        session_id=session_id,
                        spec=spec,
                        domain=domain,
                        export=workspace,
                        peer_cid=peer_cid,
                    )
                    self._vms[session_id] = vm
            except Exception:
                self._admission.release(session_id)
                self._lifecycle.mark_stopped(
                    session_id, TeardownTrigger.START_FAILED
                )
                self._lifecycle.mark_reaped(session_id)
                raise
        self._lifecycle.mark_warm(session_id)
        logger.info(
            "isolation: session %s VM ready (cid=%s, workspace=%s)",
            session_id,
            peer_cid,
            host_path,
        )
        return vm

    def capacity_snapshot(self) -> CapacitySnapshot:
        """The host-wide admission budget and current usage (todo 33).

        Off mode reports the resolved budget with zero usage and touches no
        admission state, so a status read can never create VM bookkeeping.
        """
        limits = CapacityLimits.from_config(self._config)
        if self._config.mode != MODE_KVM:
            return CapacitySnapshot(limits=limits, usage=CapacityUsage())
        return self._admission.snapshot()

    @property
    def lifecycle(self) -> LifecycleTracker:
        """The per-session lifecycle tracker (todo 26)."""
        return self._lifecycle

    def lifecycle_state(self, session: SessionSpec | str) -> LifecycleState:
        """The lifecycle state of one session; unknown sessions are ``cold``."""
        return self._lifecycle.state_of(_session_id_of(session))

    @property
    def sweeper_running(self) -> bool:
        """True while the idle-TTL sweeper thread is alive."""
        thread = self._sweeper
        return thread is not None and thread.is_alive()

    def start_idle_sweeper(self, *, interval_s: float | None = None) -> None:
        """Start the daemon that applies the idle TTL (todo 26).

        The sweeper is the production driver: without it a warm VM would stay
        alive until the next dispatch or a gateway restart. Off mode never
        starts one (a status read or a stray call must not create host state),
        and a second call for a live sweeper is a no-op.
        """
        if self._config.mode != MODE_KVM:
            return
        with self._sweeper_lock:
            if self.sweeper_running:
                return
            self._sweeper_stop.clear()
            interval = self._sweep_interval(interval_s)
            thread = threading.Thread(
                target=self._sweep_forever,
                args=(interval,),
                name="isolation-idle-sweeper",
                daemon=True,
            )
            self._sweeper = thread
            thread.start()

    def stop_idle_sweeper(self) -> None:
        """Stop the sweeper thread and wait for it to leave (idempotent)."""
        with self._sweeper_lock:
            thread = self._sweeper
            self._sweeper = None
            self._sweeper_stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(1.0, 2.0 * self._sweep_interval(None)))

    def reap_idle(self, *, now: float | None = None) -> tuple[StopReport, ...]:
        """Tear down every session VM whose idle TTL expired (todo 26).

        Only sessions the lifecycle policy marks ``stop_and_reap`` are touched:
        a VM with an in-flight dispatch is never a candidate, so this can run
        while a turn is writing a checkpoint without cutting it in half. The
        teardown is the shared one (:meth:`IsolationManager._contain_session`),
        so a swept VM releases exactly what a stopped/canceled VM releases.
        """
        moment = self._lifecycle.now() if now is None else float(now)
        reports: list[StopReport] = []
        for session_id in self._lifecycle.idle_expired(moment):
            if self.terminal_held(session_id):
                continue
            with self._vm_lock:
                vm = self._vms.get(session_id)
            if vm is None:
                self._lifecycle.mark_stopped(session_id, TeardownTrigger.IDLE_TTL)
                self._lifecycle.mark_reaped(session_id)
                continue
            self._contain_session(
                session_id,
                self._endpoint_for(session_id),
                trigger=TeardownTrigger.IDLE_TTL,
            )
            overlay = _overlay_of(vm)
            overlay_removed = overlay is None or not overlay.exists()
            logger.info(
                "isolation: session %s VM reaped after the idle TTL "
                "(overlay_removed=%s)",
                session_id,
                overlay_removed,
            )
            reports.append(
                StopReport(
                    session_id=session_id,
                    reason=STOP_REASON_IDLE_TTL,
                    acknowledged=False,
                    domain_destroyed=True,
                    overlay_removed=overlay_removed,
                )
            )
        return tuple(reports)

    def _sweep_forever(self, interval_s: float) -> None:
        while not self._sweeper_stop.is_set():
            if self._sweeper_stop.wait(interval_s):
                return
            try:
                reaped = self.reap_idle()
            except Exception as exc:  # thread boundary, keep sweeping
                logger.warning("isolation idle sweeper: sweep failed: %s", exc)
                continue
            if reaped:
                logger.info(
                    "isolation idle sweeper: reaped %d expired session VM(s)",
                    len(reaped),
                )

    def _sweep_interval(self, interval_s: float | None) -> float:
        if interval_s is not None:
            return max(0.01, float(interval_s))
        return max(IDLE_SWEEP_MIN_INTERVAL_S, self._lifecycle.ttl_s * _IDLE_SWEEP_TTL_FRACTION)

    @staticmethod
    def _existing_vm(existing: SessionVm, host_path: Path, session_id: str) -> SessionVm:
        if existing.spec.workspace_host_path != host_path:
            raise IsolationError(
                "workspace_changed",
                f"session {session_id!r} already runs on "
                f"{existing.spec.workspace_host_path}, not {host_path}",
            )
        return existing

    def _session_lock(self, session_id: str) -> threading.Lock:
        """Per-session creation lock: one creator per session inside a process."""
        with self._vm_lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

    @property
    def _admission(self) -> AdmissionController:
        if self._admission_controller is None:
            self._admission_controller = AdmissionController(
                CapacityLimits.from_config(self._config),
                directory=Path(self._config.overlay_dir) / ADMISSION_DIRNAME,
                wait_timeout_s=float(self._config.admission_wait_s),
            )
        return self._admission_controller

    def run_task(
        self,
        task: Mapping[str, Any],
        workdir: str,
        payload: Mapping[str, Any],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Dispatch one turn to the session VM and normalize the guest result.

        ``payload`` is the worker's dispatch payload (the executor kwargs plus
        ``skills_text``/``foundation_context``); only its wire-safe subset is
        sent, and an unmappable guest answer raises instead of returning a
        fabricated success.

        A dispatch that loses its VM or guest channel is contained instead of
        being raised away: the session is torn down and the returned result is a
        terminal ``fail`` (``vm_lost``/``channel_lost``) with a clear ``error`` -
        a failure, never a success.

        ``cancel_check`` is the host stop signal (the worker passes the same
        ``cancelling`` rule the off-mode executor uses): while this method waits
        for the guest it polls it and sends ``cancel`` over the channel. An ack
        ends only the task and keeps the warm VM; a missed ack means guest state
        cannot be recovered and tears the VM down. The returned result is a
        terminal ``fail`` with ``failure_reason`` ``canceled`` or
        ``cancel_timeout``.

        The lifecycle (todo 26) marks the VM warm and in flight for the whole
        wait and idle when the turn ends; a turn that tears the VM down
        (explicit stop, cancel timeout, VM loss, limit breach) leaves the record
        terminal, so the idle sweeper can never act on it.
        """
        session_id = _task_session_id(task, payload)
        vm = self.ensure_vm(
            SessionSpec(session_id=session_id, workspace_cwd=str(workdir)),
            on_capacity_wait=lambda wait: self._emit_capacity_wait(payload, wait),
            should_abort=cancel_check,
        )
        self._lifecycle.begin_dispatch(session_id)
        git_gateway = None
        apk_delivery = None
        try:
            self._assert_dispatch_binding(vm, session_id)
            endpoint = self._await_endpoint(session_id, vm)
            if endpoint is None:
                raise IsolationError(
                    "channel_unavailable",
                    f"no guest endpoint for session {session_id!r}",
                )
            extensions = (
                self._seams.extensions(payload)
                if self._seams.extensions is not None
                else self._collect_extensions(payload)
            )
            self._emit_extension_statuses(payload, extensions.statuses)
            secret_plan = self._secret_plan_for(payload)
            self._assert_guest_env_clean(secret_plan, extensions)
            grant = grant_for_workspace(self._seams.git_repositories, str(workdir))
            if grant is not None:
                git_gateway = GitGateway(grant, session_id)
            if self._seams.apk_delivery_enabled:
                from ..runtime import get_data_root
                apk_delivery = ApkDelivery(self._path_mapping(vm).host_root, get_data_root() / "apk_delivery")
            egress = self._session_egress(payload, secret_plan, git_port=git_gateway.port if git_gateway else None, apk_port=apk_delivery.port if apk_delivery else None)
            frame = self._wire_task_payload(
                vm,
                payload,
                extensions=extensions,
                secret_plan=secret_plan,
                egress=egress,
                git_gateway=git_gateway.route if git_gateway else None,
                apk_delivery_url=apk_delivery.endpoint if apk_delivery else "",
            )
            try:
                endpoint.send(MessageKind.TASK, frame)
            except ChannelError as exc:
                if exc.reason != "channel_closed":
                    raise IsolationError(
                        f"channel_send_failed:{exc.reason}", exc.detail
                    ) from exc
                endpoint = self._send_task_after_reconnect(
                    session_id, vm, endpoint, frame
                )
            mapping = self._path_mapping(vm)
            relay = ApprovalRelay(
                mapping,
                source_platform=str(payload.get("source_platform") or ""),
            )
            user_input = UserInputRelay(
                task_id=_payload_text(payload, "task_id"),
                timeout_s=_user_input_timeout(payload),
            )
            web_relay = WebToolRelay()
            session_search_relay = SessionSearchRelay(
                _optional_text(payload, "current_session")
            )
            memory_forget_relay = MemoryForgetRelay(
                _payload_text(payload, "user_message"), task_id=_payload_text(payload, 'task_id'),
                timeout_s=_user_input_timeout(payload),
            )
            result = self._await_result(
                session_id,
                endpoint,
                payload,
                relay,
                user_input,
                web_relay,
                session_search_relay,
                memory_forget_relay,
                mapping,
                cancel_check,
            )
            return _rewrite_result_guest_paths(result, mapping)
        finally:
            if apk_delivery is not None:
                apk_delivery.close()
            if git_gateway is not None:
                git_gateway.close()
            self._lifecycle.finish_dispatch(session_id)

    def cancel(
        self,
        session: SessionSpec | str,
        *,
        reason: str = CANCEL_FRAME_REASON,
        timeout_s: float | None = None,
    ) -> StopReport:
        """Send ``cancel`` and guarantee the session VM is torn down (todo 11).

        The task transition is already the caller's (the row is ``cancelling``
        when a mobile Stop arrives); this owns the VM half: ask the guest to
        stop, wait at most the ack TTL for ``result{status:"canceled"}`` and
        destroy the domain and remove the overlay. A session with no VM because
        it was explicitly stopped or its long idle TTL expired is a clean no-op,
        never an error.
        """
        session_id = _session_id_of(session)
        with self._vm_lock:
            vm = self._vms.pop(session_id, None)
        if vm is None:
            logger.info(
                "isolation: stop for session %s with no VM (already stopped)",
                session_id,
            )
            return StopReport(
                session_id=session_id,
                reason=STOP_REASON_NO_VM,
                acknowledged=False,
                domain_destroyed=False,
                overlay_removed=False,
            )
        endpoint = self._endpoint_for(session_id)
        ttl_s = self._cancel_ack_timeout_s if timeout_s is None else max(0.0, timeout_s)
        acknowledged = self._send_cancel(endpoint, reason) and self._await_cancel_ack(
            endpoint, ttl_s
        )
        self._close_endpoint(endpoint)
        self._session_map.release(SessionId(session_id))
        overlay = _overlay_of(vm)
        vm.domain.teardown()
        self._leases.release(session_id)
        self._exports.release(session_id)
        self._admission.release(session_id)
        self._lifecycle.mark_stopped(session_id, TeardownTrigger.SESSION_STOP)
        self._lifecycle.mark_reaped(session_id)
        overlay_removed = overlay is None or not overlay.exists()
        outcome = STOP_REASON_CANCELED if acknowledged else STOP_REASON_CANCEL_TIMEOUT
        logger.info(
            "isolation: session %s VM stopped (%s, overlay_removed=%s)",
            session_id,
            outcome,
            overlay_removed,
        )
        return StopReport(
            session_id=session_id,
            reason=outcome,
            acknowledged=acknowledged,
            domain_destroyed=True,
            overlay_removed=overlay_removed,
        )

    def stop(
        self,
        session: SessionSpec | str,
        *,
        trigger: TeardownTrigger = TeardownTrigger.SESSION_STOP,
    ) -> None:
        """Tear the session VM down and forget it; unknown sessions are a no-op."""
        session_id = _session_id_of(session)
        with self._vm_lock:
            vm = self._vms.get(session_id)
        if vm is None:
            return
        self._contain_session(session_id, self._endpoint_for(session_id), trigger=trigger)
        logger.info("isolation: session %s VM stopped (%s)", session_id, trigger)

    def owns_overlay(self, session_id: str) -> bool:
        """True when a live VM holds this session's overlay lease.

        The cross-process liveness question ("is some process still running this
        session's VM?") is answered by the todo-23 lease, never by this manager's
        own session table. A probe error is reported as *live* on purpose
        (fail-closed): an unreadable lease state must not fail a task that may
        still be running.
        """
        text = _session_id_of(session_id)
        try:
            return self._leases.is_leased(
                overlay_for_session(text, self._config.overlay_dir)
            )
        except IsolationError as exc:
            logger.warning(
                "isolation: cannot probe the overlay lease for session %s (%s); "
                "treating it as live",
                text,
                exc,
            )
            return True

    def reap_orphans(self) -> int:
        """Remove domains/overlays/endpoints no live session owns; returns how many.

        A sibling manager's live VM is not an orphan: its overlay carries a live
        lease (todo 23), so this reaper skips it even though the sibling's
        session table is invisible here. Only overlays no live process leases -
        and none of this manager's own sessions own - are destroyed and removed.
        A routing entry the host still holds for a session with no live VM and no
        live lease (a dispatch that failed before its endpoint closed, or a
        previous gateway run) is released too, so a later dispatch cannot be
        routed to a dead guest.
        """
        overlay_dir = Path(self._config.overlay_dir)
        errors: list[IsolationError] = []
        reaped = 0
        if overlay_dir.is_dir():
            active_overlays = {
                Path(vm.domain.overlay)
                for vm in list(self._vms.values())
                if vm.domain.overlay is not None
            }
            for overlay in sorted(overlay_dir.glob("*.qcow2")):
                if overlay in active_overlays:
                    continue
                try:
                    if self._leases.is_leased(overlay):
                        logger.info(
                            "isolation reap: %s is leased by a live session (%s); skipping",
                            overlay,
                            self._leases.holder(overlay) or "unknown",
                        )
                        continue
                except IsolationError as exc:
                    errors.append(exc)
                    continue
                try:
                    self._destroy_orphan_domain(f"{DOMAIN_NAME_PREFIX}{overlay.stem}")
                except IsolationError as exc:
                    errors.append(exc)
                try:
                    overlay.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    errors.append(
                        IsolationError("overlay_remove_failed", f"{overlay}: {exc}")
                    )
                reaped += 1
        self._release_stale_routes(errors)
        if errors:
            raise errors[0]
        return reaped

    def _release_stale_routes(self, errors: list[IsolationError]) -> None:
        """Close endpoints and free CID bindings no live VM owns.

        Only a session that is neither in this manager's table nor leased by a
        live process (this one or a sibling) is stale; everything else keeps its
        routing entry untouched.
        """
        for session_id in self._session_map.sessions():
            if session_id in self._vms:
                continue
            overlay = overlay_for_session(session_id, self._config.overlay_dir)
            try:
                if self._leases.is_leased(overlay):
                    continue
            except IsolationError as exc:
                errors.append(exc)
                continue
            logger.info(
                "isolation reap: releasing the stale channel route for session %s",
                session_id,
            )
            self._close_endpoint(self._endpoint_for(str(session_id)))
            self._session_map.release(session_id)

    def close(self) -> None:
        """Stop every tracked session and close the host-side handles."""
        self.stop_idle_sweeper()
        for session_id in list(self._vms):
            self.stop(session_id, trigger=TeardownTrigger.HOST_SHUTDOWN)
        if self._channel_server is not None:
            self._listener_stop.set()
            self._channel_server.close()
            self._channel_server = None
        if self._seams.channel is not None:
            self._seams.channel.close()
        if self._seams.provider_secrets is not None:
            self._seams.provider_secrets.close()
        if self._leases_guard is not None:
            self._leases_guard.close()
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _secret_plan_for(self, payload: Mapping[str, Any]) -> ProviderSecretPlan | None:
        """The provider credential decision for this dispatch (todo 18).

        Returns ``None`` when the seam is off or the base URL has no known
        provider route; the proxy start failure propagates as a canonical
        ``IsolationError`` before any frame is sent, so a failed injection can
        never degrade into a guest-held key.
        """
        injector = self._seams.provider_secrets
        if injector is None:
            return None
        plan = injector.plan(_payload_text(payload, "base_url"))
        if plan is None:
            return None
        self._emit_extension_statuses(payload, (plan.capability_status(),))
        return plan

    def _wire_task_payload(
        self,
        vm: SessionVm,
        payload: Mapping[str, Any],
        *,
        extensions: TaskExtensions | None = None,
        secret_plan: ProviderSecretPlan | None = None,
        egress: SessionEgress | None = None,
        git_gateway: GitGatewayRoute | None = None,
        apk_delivery_url: str = "",
    ) -> dict[str, JsonValue]:
        """Build the JSON-safe task frame through the shared contract builder.

        Host-only values (host-path ``metadata``, ``log_fn``, auth headers) are
        narrowed into ``HostTaskInput``; the wire schema lives only in ``history``.
        The todo-31 capability sections ride the same contract, and the
        ``skills_text`` runtime root is rewritten host->guest so the model reads
        the assets where the guest actually writes them. The todo-40 per-session
        egress section rides it too: the guest applies that ruleset before the
        loop starts, so the allowlist is enforced, not merely generated.

        The guest artifact dir of this task is routed into the exported workspace
        (``<guest workdir>/.igorek-artifacts/<task8>``) instead of the disposable
        guest ``/tmp``, and the matching host mirror is written as a route file
        into the host artifact workdir, so the mobile bridge can read the
        ``subagents.jsonl`` journal through the one virtiofs share. The guest
        creates the directory itself; no guest-side code changes.
        """
        placed = extensions if extensions is not None else TaskExtensions()
        base_url = (
            secret_plan.guest_base_url
            if secret_plan is not None
            else _payload_text(payload, "base_url")
        )
        if secret_plan is None and egress is not None and egress.local_inference_target:
            try:
                parts = urlsplit(base_url)
                host = parts.hostname or ""
                port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
                if (parts.scheme not in {"http", "https"} or parts.username is not None
                        or parts.password is not None or ":" in host or not is_host_loopback(host)
                        or not 1 <= port <= 65535 or f"{host}:{port}" != egress.local_inference_target):
                    raise ValueError("local provider URL and route do not match")
            except ValueError as exc:
                raise IsolationError("local_inference_route_invalid", "Local provider URL and route do not match") from exc
            base_url = urlunsplit((parts.scheme, f"{HOST_LOOPBACK_ALIAS}:{port}", parts.path, parts.query, parts.fragment))
        mapping = self._path_mapping(vm)
        task_id = _payload_text(payload, "task_id")
        guest_artifact = guest_artifact_dir(str(vm.export["guest_path"]), task_id)
        host_artifact = payload.get("artifact_workdir")
        if isinstance(host_artifact, str) and host_artifact.strip():
            write_artifact_route(
                host_artifact, host_artifact_dir(mapping.host_root, task_id)
            )
        skills_text = _payload_text(payload, "skills_text")
        if isinstance(host_artifact, str) and host_artifact.strip():
            skills_text = rewrite_skill_runtime_paths(
                skills_text,
                host_artifact,
                guest_artifact_workdir=guest_artifact,
            )
        wire_payload = build_wire_task_payload(
            HostTaskInput(
                task_id=task_id,
                profile=_payload_text(payload, "profile"),
                model=_payload_text(payload, "model"),
                base_url=base_url,
                api_format=_payload_text(payload, "api_format"),
                system_prompt=mapping.host_to_guest(_payload_text(payload, "system_prompt")),
                user_message=mapping.host_to_guest(_payload_text(payload, "user_message")),
                skills_text=skills_text,
                foundation_context=_payload_text(payload, "foundation_context"),
                filesystem_mode=_payload_text(payload, "filesystem_mode"),
                guard_mode=str(payload.get("guard_mode") or ""),
                max_tokens=_payload_int(payload, "max_tokens"),
                timeout_s=_payload_int(payload, "timeout_s"),
                workdir=str(vm.export["guest_path"]),
                artifact_workdir=guest_artifact,
                trailing_context_text=mapping.host_to_guest(
                    str(payload.get("trailing_context_text") or "")
                ),
                current_session=_optional_text(payload, "current_session"),
                source_platform=_optional_text(payload, "source_platform"),
                source_chat_id=_optional_text(payload, "source_chat_id"),
                input_files=tuple(
                    _guest_input_file(item, mapping)
                    for item in _json_items(payload.get("input_files"), path="input_files")
                ),
                enabled_tools=_json_items(payload.get("enabled_tools"), path="enabled_tools"),
                history_messages=tuple(
                    mapping.map_host_value(item)
                    for item in _json_items(
                        payload.get("history_messages"), path="history_messages"
                    )
                ),
                checkpoint_config=_json_mapping(
                    payload.get("checkpoint_config"), path="checkpoint_config"
                ),
                task_metadata=_host_task_metadata(
                    payload.get("metadata"), self._path_mapping(vm)
                ),
                mcp_servers=placed.mcp_servers,
                skill_assets=placed.skill_assets,
                egress=egress,
                git_gateway=git_gateway,
                apk_delivery_url=apk_delivery_url,
            )
        )

        return pack_task(wire_payload, host_root=mapping.host_root)

    def _session_egress(
        self,
        payload: Mapping[str, Any],
        secret_plan: ProviderSecretPlan | None,
        *, git_port: int | None = None, apk_port: int | None = None,
    ) -> SessionEgress:
        """Render this session's egress decision (plan todo 40a).

        The allowlist comes from the session's own provider URL, so the rendered
        ruleset is regenerated per session; the host-local ports carry the
        todo-18 egress-auth proxy endpoint, which the guest reaches through the
        gateway alias. Scoped Git and APK grants add only their dispatch-local ports.
        """
        builder = self._seams.egress if self._seams.egress is not None else build_dispatch_egress
        return builder(
            _payload_text(payload, "profile"),
            _payload_text(payload, "base_url"),
            host_local_ports=_proxy_ports(secret_plan) + tuple(port for port in (git_port, apk_port) if port is not None),
        )

    def _assert_guest_env_clean(
        self,
        secret_plan: ProviderSecretPlan | None,
        extensions: TaskExtensions,
    ) -> None:
        """Refuse a secret-tainted guest env before the frame is sent (todo 40c).

        The provider plan's guest env is the host->guest credential path; an MCP
        placement that is not the documented ephemeral-env residual must carry
        no secret either, and a host-brokered spec must carry no env value at
        all (that is what the broker is for). A violation is a canonical
        ``IsolationError`` raised before ``send``, so a tainted env is refused,
        never dispatched.
        """
        env: dict[str, object] = {}
        brokered: list[str] = []
        if secret_plan is not None:
            env.update(secret_plan.guest_env_map())
        for server in extensions.mcp_servers:
            if server.credential_source is MCP_CREDENTIAL_EPHEMERAL_ENV:
                continue
            for key, value in server.env:
                name = f"{server.name}.{key}"
                if server.placement is MCP_PLACEMENT_HOST_BROKERED:
                    brokered.append(name)
                else:
                    env[name] = value
        if brokered:
            raise IsolationError(GUEST_ENV_LEAK_REASON, ",".join(brokered))
        if not env:
            return
        try:
            assert_guest_env_clean(env, secret_values=_host_secret_values(os.environ))
        except GuestEnvLeakError as exc:
            raise IsolationError(exc.code, ",".join(exc.keys)) from exc

    def _collect_extensions(self, payload: Mapping[str, Any]) -> TaskExtensions:
        """Resolve this dispatch's MCP placements and skill assets (todo 31).

        The host is the only side that can read the MCP registry and the
        materialized skill tree; both stay host-side and only the bounded
        placements/assets cross the wire.
        """
        artifact = payload.get("artifact_workdir")
        if not isinstance(artifact, str) or not artifact.strip():
            return TaskExtensions()
        registry, profile_servers, statuses = self._mcp_config_sources(
            str(payload.get("profile") or "")
        )
        from ..skills_manager import skills_manager
        if not skills_manager.is_loaded:
            skills_manager.load_skills()
        skill_sources = {skill.skill_id: skill.package_dir for skill in skills_manager.skills.values()
                         if skill.package_dir}
        collected = collect_task_extensions(
            artifact,
            skill_sources=skill_sources,
            mcp=McpSources(
                env=os.environ,
                registry=registry,
                profile_servers=profile_servers,
            ),
        )
        if not statuses:
            return collected
        return TaskExtensions(
            mcp_servers=collected.mcp_servers,
            skill_assets=collected.skill_assets,
            statuses=(*statuses, *collected.statuses),
        )

    @staticmethod
    def _mcp_config_sources(
        profile: str,
    ) -> tuple[dict[str, JsonValue], tuple[str, ...], tuple[CapabilityStatus, ...]]:
        """The registry payload and the profile's enabled names, or a visible refusal."""
        try:
            from ..mcp_config import get_profile_mcp_servers, list_mcp_servers
        except ImportError:  # pragma: no cover - direct script fallback
            return {}, (), ()
        statuses: list[CapabilityStatus] = []
        try:
            registry = {
                str(name): _as_json(entry, path=f"mcp.{name}")
                for name, entry in list_mcp_servers().items()
            }
        except Exception as exc:  # host config boundary: an unreadable registry is visible
            logger.warning("isolation: MCP registry unavailable (%s)", exc)
            registry = {}
            statuses.append(
                CapabilityStatus(
                    CcTraceEvent.ISOLATION_MCP_UNAVAILABLE,
                    "mcp_registry",
                    "registry_unavailable",
                    detail=f"{type(exc).__name__}",
                )
            )
        servers: tuple[str, ...] = ()
        if profile:
            try:
                servers = tuple(get_profile_mcp_servers(profile))
            except Exception as exc:  # host config boundary: an unreadable profile is visible
                logger.warning("isolation: profile %s MCP list unavailable (%s)", profile, exc)
                statuses.append(
                    CapabilityStatus(
                        CcTraceEvent.ISOLATION_MCP_UNAVAILABLE,
                        f"profile:{profile}",
                        "profile_unavailable",
                        detail=f"{type(exc).__name__}",
                    )
                )
        return registry, servers, tuple(statuses)

    @staticmethod
    def _emit_extension_statuses(
        payload: Mapping[str, Any], statuses: tuple[CapabilityStatus, ...]
    ) -> None:
        """Write host-side capability statuses into the authoritative trace."""
        if not statuses:
            return
        workdir = payload.get("artifact_workdir")
        if not isinstance(workdir, str) or not workdir.strip():
            logger.warning(
                "isolation: %d capability status(es) dropped (no artifact_workdir)",
                len(statuses),
            )
            return
        emit_capability_statuses(workdir, statuses)

    @staticmethod
    def _path_mapping(vm: SessionVm) -> WorkspacePathMapping:
        """Guest <-> host mapping of the session's single workspace export."""
        return WorkspacePathMapping(
            host_root=str(vm.export["host_path"]),
            guest_root=str(vm.export["guest_path"]),
        )

    def _await_result(
        self,
        session_id: str,
        endpoint: HostEndpoint,
        payload: Mapping[str, Any],
        approval: ApprovalRelay,
        user_input: UserInputRelay,
        web_relay: WebToolRelay,
        session_search_relay: SessionSearchRelay,
        memory_forget_relay: MemoryForgetRelay,
        mapping: WorkspacePathMapping,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Collect the guest answer; a blocked call parks the task but keeps its VM."""
        timeout_s = _result_timeout(payload)
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        polling = cancel_check is not None
        relay = self._relay_for(payload)
        from .vault_values import VaultStoreRelay
        vault_store_relay = VaultStoreRelay(self._seams.vault_value_writer, workdir=mapping.host_root,
            task_id=_payload_text(payload, 'task_id'), session_id=_optional_text(payload, 'current_session') or session_id,
            profile=_optional_text(payload, 'profile') or '')
        last_activity = time.monotonic()
        while True:
            if cancel_check is not None and cancel_check():
                return self._cancel_in_flight(session_id, endpoint, relay)
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                return self._settle_unresponsive(
                    session_id,
                    endpoint,
                    approval,
                    reason=FAILURE_REASON_RESULT_TIMEOUT,
                    detail=f"no result within {timeout_s:.0f}s",
                )
            if now - last_activity > self._guest_stall_timeout_s:
                return self._settle_unresponsive(
                    session_id,
                    endpoint,
                    approval,
                    reason=FAILURE_REASON_GUEST_STALLED,
                    detail=(
                        f"no guest frame for {now - last_activity:.0f}s "
                        f"(heartbeat interval {HEARTBEAT_INTERVAL_S:.0f}s)"
                    ),
                )
            wait_s = _recv_wait_s(deadline, now, polling=polling)
            try:
                message = endpoint.recv_from_guest(wait_s)
            except ChannelError as exc:
                return self._contain_vm_failure(
                    session_id,
                    endpoint,
                    reason=FAILURE_REASON_CHANNEL_LOST,
                    detail=str(exc),
                )
            if message is None:
                if not polling and not self._vm_is_active(session_id):
                    return self._contain_vm_failure(
                        session_id,
                        endpoint,
                        reason=FAILURE_REASON_VM_LOST,
                        detail="libvirt reports the domain is not running",
                    )
                continue
            last_activity = time.monotonic()
            match message.kind:
                case MessageKind.RESULT:
                    result = _normalize_result(message.payload)
                    parked = approval.park(result)
                    if parked is None:
                        if self._seams.channel is None:
                            self._close_endpoint(endpoint)
                        return result
                    # Pause the task, close its one-shot runner channel, and keep
                    # the session VM/overlay warm for approval resume.
                    self._close_endpoint(endpoint)
                    return parked
                case MessageKind.APPROVAL_REQUEST:
                    approval.collect(message)
                case MessageKind.USER_INPUT_REQUEST:
                    reply = user_input.handle_request(message)
                    if reply is not None:
                        self._send_user_input_reply(session_id, endpoint, reply)
                case MessageKind.WEB_REQUEST:
                    reply = web_relay.handle_request(message)
                    if reply is not None:
                        endpoint.send(MessageKind.WEB_REPLY, reply)
                case MessageKind.SESSION_SEARCH_REQUEST:
                    reply = session_search_relay.handle_request(message)
                    if reply is not None:
                        endpoint.send(MessageKind.SESSION_SEARCH_REPLY, reply)
                case MessageKind.VAULT_STORE_REQUEST:
                    reply = vault_store_relay.handle_request(message)
                    if reply is not None:
                        endpoint.send(MessageKind.VAULT_STORE_REPLY, reply)
                case MessageKind.MEMORY_FORGET_REQUEST:
                    reply = memory_forget_relay.handle_request(message)
                    if reply is not None:
                        endpoint.send(MessageKind.MEMORY_FORGET_REPLY, reply)
                case MessageKind.STEER_REQUEST:
                    request_id = message.payload.get("request_id")
                    items: list[JsonValue] = []
                    provider_key = (
                        "priority_steer_provider"
                        if message.payload.get("prioritized_only") is True
                        else "pending_steer_provider"
                    )
                    provider = payload.get(provider_key)
                    if isinstance(request_id, str) and callable(provider):
                        try:
                            queued = provider() or []
                        except Exception as exc:
                            logger.warning(
                                "isolation: steer queue read failed (%s)",
                                type(exc).__name__,
                            )
                            queued = []
                        if isinstance(queued, list):
                            for item in queued:
                                mapped = _guest_steer_item(item, mapping)
                                if mapped is not None:
                                    items.append(mapped)
                    if isinstance(request_id, str):
                        endpoint.send(
                            MessageKind.STEER_REPLY,
                            {"request_id": request_id, "items": items},
                        )
                case MessageKind.ERROR:
                    reason = _frame_reason(message.payload)
                    breach = classify_limit_breach(reason)
                    if breach is not None:
                        self._emit_limit_breach(payload, breach, session_id)
                        return self._contain_limit_breach(session_id, endpoint, breach)
                    raise IsolationError("guest_error", reason)
                case MessageKind.EVENT:
                    if relay is not None:
                        relay.handle_event(message)
                    else:
                        logger.debug(
                            "isolation: session %s event frame dropped (no artifact workdir)",
                            session_id,
                        )
                case MessageKind.HOST_OP:
                    self._handle_guest_host_op(
                        session_id, endpoint, message, approval, payload
                    )
                case MessageKind.HEARTBEAT:
                    continue
                case (
                    MessageKind.TASK
                    | MessageKind.APPROVAL_DECISION
                    | MessageKind.USER_INPUT_REPLY
                    | MessageKind.STEER_REQUEST
                    | MessageKind.STEER_REPLY
                    | MessageKind.SESSION_SEARCH_REPLY
                    | MessageKind.CANCEL
                ):
                    raise IsolationError(
                        "unexpected_guest_frame",
                        f"session {session_id!r}: {message.kind}",
                    )
                case _ as unreachable:
                    assert_never(unreachable)

    def _handle_guest_host_op(
        self,
        session_id: str,
        endpoint: HostEndpoint,
        message: ChannelMessage,
        approval: ApprovalRelay,
        payload: Mapping[str, Any],
    ) -> None:
        """Route one guest host-op frame through the typed host broker (todo 22).

        Without a host-op runtime the frame keeps the default fail-closed
        posture (``unexpected_guest_frame``); with one, the broker validates the
        op against the typed catalog, audits the decision and either answers the
        guest or parks the request as a host-op approval record on the task.
        """
        runtime = self._seams.host_ops
        if runtime is None:
            raise IsolationError(
                "unexpected_guest_frame", f"session {session_id!r}: {message.kind}"
            )
        intake = runtime.handle_request(
            message,
            task_id=_payload_text(payload, "task_id"),
            session_id=session_id,
            reason="guest host operation",
        )
        if intake.response is not None:
            endpoint.send(MessageKind.HOST_OP, intake.response.to_payload())
        if intake.parked_request is not None:
            approval.collect_host_op(intake.parked_request.to_blocked_record())

    def _vm_is_active(self, session_id: str) -> bool:
        """Whether libvirt still reports this session's domain running."""
        with self._vm_lock:
            vm = self._vms.get(session_id)
        return vm is not None and vm.domain.is_active()

    def _contain_session(
        self,
        session_id: str,
        endpoint: HostEndpoint | None,
        *,
        trigger: TeardownTrigger,
    ) -> None:
        """Unregister one failed session and destroy its VM; fail-safe.

        Shared by the stop, cancel-adjacent, limit-breach, VM-loss and idle-TTL
        paths, so there is exactly one definition of "this session is over": the
        domain is destroyed if still running, undefined and its overlay removed;
        the endpoint is closed and the CID binding, overlay lease and workspace
        export are released. A teardown failure is logged and the host state is
        released anyway - the reaper is the backstop, and a failing sibling must
        never inherit a half-registered session. The lifecycle record is marked
        stopped/reaped here too (todo 26), with ``trigger`` naming why, so no
        path can release the host state and leave the record looking live.
        """
        with self._vm_lock:
            vm = self._vms.pop(session_id, None)
        self._close_endpoint(endpoint)
        self._session_map.release(SessionId(session_id))
        if vm is None:
            return
        try:
            vm.domain.teardown()
        except IsolationError as exc:
            logger.warning(
                "isolation: session %s teardown during containment failed: %s",
                session_id,
                exc,
            )
        finally:
            self._leases.release(session_id)
            self._exports.release(session_id)
            self._admission.release(session_id)
            self._lifecycle.mark_stopped(session_id, trigger)
            self._lifecycle.mark_reaped(session_id)

    def _contain_vm_failure(
        self,
        session_id: str,
        endpoint: HostEndpoint,
        *,
        reason: str,
        detail: str,
    ) -> dict[str, Any]:
        """Contain a lost VM/guest: fail the turn and leave no host state behind.

        Called when the VMM died (the domain is not running) or the guest
        channel broke. The session is torn down through :meth:`_contain_session`
        so a sibling session and the next dispatch for this session are
        unaffected, and the returned result is always the terminal ``fail`` the
        worker maps to ``error_message`` - a lost VM is never reported as
        success.
        """
        self._contain_session(session_id, endpoint, trigger=TeardownTrigger.VM_LOST)
        logger.warning(
            "isolation: session %s lost its VM (%s: %s); VM destroyed and task failed",
            session_id,
            reason,
            detail,
        )
        return _vm_failure_result(session_id, reason=reason, detail=detail)

    def _contain_unresponsive_vm(
        self,
        session_id: str,
        endpoint: HostEndpoint,
        *,
        reason: str,
        detail: str,
    ) -> dict[str, Any]:
        """Contain a frozen or over-budget guest: tear the VM down and fail the turn."""
        self._contain_session(session_id, endpoint, trigger=TeardownTrigger.VM_LOST)
        logger.warning(
            "isolation: session %s unresponsive (%s: %s); VM destroyed and task failed",
            session_id,
            reason,
            detail,
        )
        return _vm_failure_result(session_id, reason=reason, detail=detail)

    def _settle_unresponsive(
        self,
        session_id: str,
        endpoint: HostEndpoint,
        approval: ApprovalRelay,
        *,
        reason: str,
        detail: str,
    ) -> dict[str, Any]:
        """Park a collected approval, else contain the unresponsive guest."""
        parked = approval.park_after_timeout()
        if parked is not None:
            self._close_endpoint(endpoint)
            return parked
        return self._contain_unresponsive_vm(
            session_id, endpoint, reason=reason, detail=detail
        )

    def _cancel_in_flight(
        self,
        session_id: str,
        endpoint: HostEndpoint,
        relay: TraceRelay | None,
    ) -> dict[str, Any]:
        """Cancel this task; keep the VM on acknowledgement, tear down on timeout."""
        with self._vm_lock:
            vm = self._vms.get(session_id)
        if vm is None:
            raise IsolationError(
                "cancel_without_vm", f"session {session_id!r} has no live VM to cancel"
            )
        sent = self._send_cancel(endpoint, CANCEL_FRAME_REASON)
        logger.warning(
            "isolation.cancel_frame_sent session_id=%s source=host_stop_signal "
            "reason=%s sent=%s",
            session_id,
            CANCEL_FRAME_REASON,
            sent,
        )
        result = self._await_cancel_result(endpoint, relay) if sent else None
        logger.warning(
            "isolation.cancel_ack session_id=%s acknowledged=%s",
            session_id,
            result is not None,
        )
        if result is not None:
            # GuestRunner is one-shot and systemd starts a fresh listener in the
            # same VM. Closing this channel forces the next task to wait for it.
            self._close_endpoint(endpoint)
            logger.info(
                "isolation: session %s task canceled; warm VM and workspace retained",
                session_id,
            )
            return result

        logger.warning(
            "isolation: session %s did not acknowledge cancel within %ss; "
            "destroying unrecoverable VM state",
            session_id,
            self._cancel_ack_timeout_s,
        )
        result = _cancel_timeout_result()
        self._contain_session(
            session_id, endpoint, trigger=TeardownTrigger.SESSION_STOP
        )
        return result

    def _contain_limit_breach(
        self,
        session_id: str,
        endpoint: HostEndpoint,
        breach: LimitBreach,
    ) -> dict[str, Any]:
        """Stop a guest that hit a hard limit; the breach is already reported.

        The limit itself is enforced by the domain cgroup and the overlay cap;
        this is the host half: the abusive VM is destroyed and its overlay
        removed, so a session that filled its disk or exhausted its memory stops
        instead of degrading the host or its siblings, and the task receives a
        terminal failure naming the breached class. A teardown failure is
        reported but never hides the breach, and the leases are released either
        way so the session can start a fresh VM.
        """
        self._contain_session(
            session_id, endpoint, trigger=TeardownTrigger.LIMIT_BREACH
        )
        logger.warning(
            "isolation: session %s hit its %s limit (%s); VM destroyed, host unaffected",
            session_id,
            breach.kind,
            breach.reason,
        )
        return limit_failure_result(breach, session_id=session_id)

    @staticmethod
    def _emit_capacity_wait(payload: Mapping[str, Any], wait: CapacityWait) -> None:
        """Write the queue status into the authoritative host trace (todo 33)."""
        workdir = payload.get("artifact_workdir")
        emit_capacity_wait(
            workdir if isinstance(workdir, str) and workdir.strip() else None,
            wait,
            subject=wait.session_id,
        )

    @staticmethod
    def _emit_limit_breach(
        payload: Mapping[str, Any], breach: LimitBreach, session_id: str
    ) -> None:
        """Write the breach into the authoritative host trace, or say why not."""
        workdir = payload.get("artifact_workdir")
        if not isinstance(workdir, str) or not workdir.strip():
            logger.warning(
                "isolation: %s limit breach for session %s dropped (no artifact_workdir)",
                breach.kind,
                session_id,
            )
            return
        emit_limit_breach(workdir, breach, subject=session_id)

    @staticmethod
    def _send_cancel(endpoint: HostEndpoint | None, reason: str) -> bool:
        """Deliver one ``cancel`` frame; a dead channel is an unacknowledged stop."""
        if endpoint is None:
            return False
        try:
            endpoint.send(MessageKind.CANCEL, {"reason": reason})
        except ChannelError as exc:
            logger.warning("isolation: cancel frame not delivered (%s)", exc)
            return False
        return True

    @staticmethod
    def _await_cancel_ack(endpoint: HostEndpoint | None, ttl_s: float) -> bool:
        """True only when the guest answered ``cancel`` with a canceled result."""
        if endpoint is None:
            return False
        deadline = time.monotonic() + ttl_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                message = endpoint.recv_from_guest(remaining)
            except ChannelError as exc:
                logger.warning("isolation: cancel acknowledgement read failed (%s)", exc)
                return False
            if message is None:
                return False
            if message.kind is MessageKind.RESULT:
                return _is_canceled_status(message.payload.get("status"))
            if message.kind is MessageKind.APPROVAL_REQUEST:
                logger.debug("isolation: approval frame ignored while stopping")

    def _await_cancel_result(
        self, endpoint: HostEndpoint, relay: TraceRelay | None
    ) -> dict[str, Any] | None:
        """The normalized canceled result, or ``None`` when the guest did not ack."""
        deadline = time.monotonic() + self._cancel_ack_timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                message = endpoint.recv_from_guest(remaining)
            except ChannelError as exc:
                logger.warning("isolation: cancel acknowledgement read failed (%s)", exc)
                return None
            if message is None:
                return None
            match message.kind:
                case MessageKind.RESULT:
                    if not _is_canceled_status(message.payload.get("status")):
                        return None
                    try:
                        return _normalize_result(message.payload)
                    except IsolationError as exc:
                        logger.warning(
                            "isolation: malformed cancel acknowledgement ignored (%s: %s)",
                            exc.reason,
                            exc.detail,
                        )
                        return None
                case MessageKind.EVENT:
                    if relay is not None:
                        relay.handle_event(message)
                case (
                    MessageKind.APPROVAL_REQUEST
                    | MessageKind.USER_INPUT_REQUEST
                    | MessageKind.TASK
                    | MessageKind.APPROVAL_DECISION
                    | MessageKind.USER_INPUT_REPLY
                    | MessageKind.STEER_REQUEST
                    | MessageKind.STEER_REPLY
                    | MessageKind.SESSION_SEARCH_REPLY
                    | MessageKind.MEMORY_FORGET_REQUEST
                    | MessageKind.MEMORY_FORGET_REPLY
                    | MessageKind.HEARTBEAT
                    | MessageKind.CANCEL
                    | MessageKind.HOST_OP
                    | MessageKind.ERROR
                ):
                    logger.debug(
                        "isolation: %s frame ignored while stopping", message.kind
                    )
                case _ as unreachable:
                    assert_never(unreachable)

    @staticmethod
    def _close_endpoint(endpoint: HostEndpoint | None) -> None:
        if endpoint is not None:
            endpoint.close()

    def _send_user_input_reply(
        self,
        session_id: str,
        endpoint: HostEndpoint,
        reply: Mapping[str, JsonValue],
    ) -> None:
        """Deliver one relay reply; a refused answer fails closed, never hangs."""
        try:
            endpoint.send(MessageKind.USER_INPUT_REPLY, reply)
        except ChannelError as exc:
            self.stop(session_id)
            raise IsolationError(
                f"user_input_reply_failed:{exc.reason}", exc.detail
            ) from exc

    def terminal_held(self, session_id: str) -> bool:
        with self._vm_lock:
            return any(vm.session_id == session_id and expiry > time.monotonic()
                       and self._vms.get(session_id) is vm
                       for _token, vm, expiry in self._terminal_leases.values())

    def terminal_acquire(self, session: SessionSpec) -> dict:
        import secrets
        vm = self.ensure_vm(session)
        token = secrets.token_hex(16)
        with self._vm_lock:
            self._terminal_leases[token] = (token, vm, time.monotonic() + 30)
        return {"lease": token, "cid": int(vm.peer_cid), "session_id": vm.session_id}

    def terminal_renew(self, token: str) -> bool:
        with self._vm_lock:
            item = self._terminal_leases.get(token)
            if item is None:
                return False
            _, vm, _expiry = item
            if self._vms.get(vm.session_id) is not vm or not vm.domain.is_active():
                self._terminal_leases.pop(token, None)
                return False
            self._terminal_leases[token] = (token, vm, time.monotonic() + 30)
            return True

    def terminal_release(self, token: str) -> None:
        with self._vm_lock:
            self._terminal_leases.pop(token, None)

    def _endpoint_for(self, session_id: str) -> HostEndpoint | None:
        return self._registry().session_for(SessionId(session_id))

    def _await_endpoint(
        self,
        session_id: str,
        vm: SessionVm,
        *,
        previous: HostEndpoint | None = None,
        wait_s: float | None = None,
    ) -> HostEndpoint | None:
        if wait_s is None:
            wait_s = self._seams.endpoint_wait_s
            if wait_s is None:
                wait_s = 60.0 if self._seams.channel is None else 0.0
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            endpoint = self._endpoint_for(session_id)
            if (
                endpoint is not None
                and endpoint is not previous
                and not bool(getattr(endpoint, "closed", False))
            ):
                return endpoint
            if time.monotonic() >= deadline or not vm.domain.is_active():
                return None
            time.sleep(min(0.1, deadline - time.monotonic()))

    def _send_task_after_reconnect(
        self,
        session_id: str,
        vm: SessionVm,
        endpoint: HostEndpoint,
        frame: Mapping[str, JsonValue],
    ) -> HostEndpoint:
        """Retry a TASK only after a closed endpoint is replaced by a new peer.

        ``PeerSession.send`` raises ``channel_closed`` before writing any bytes,
        so this retry cannot duplicate a task already received by the guest.
        The same live VM, overlay, and workspace export remain owned throughout.
        """
        wait_s = self._seams.endpoint_wait_s
        if wait_s is None:
            wait_s = 60.0 if self._seams.channel is None else 0.0
        deadline = time.monotonic() + max(0.0, wait_s)
        previous = endpoint
        while True:
            if not vm.domain.is_active():
                raise IsolationError(
                    "vm_stopped_before_task_delivery",
                    f"session {session_id!r} VM is no longer active",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IsolationError(
                    "guest_channel_reconnect_timeout",
                    f"session {session_id!r} VM is alive but guest channel did not reconnect",
                )
            endpoint = self._await_endpoint(
                session_id, vm, previous=previous, wait_s=remaining
            )
            if endpoint is None:
                raise IsolationError(
                    "guest_channel_reconnect_timeout",
                    f"session {session_id!r} VM is alive but guest channel did not reconnect",
                )
            try:
                endpoint.send(MessageKind.TASK, frame)
                logger.info(
                    "isolation: session %s delivered task after guest-channel reconnect",
                    session_id,
                )
                return endpoint
            except ChannelError as exc:
                if exc.reason != "channel_closed":
                    raise IsolationError(
                        f"channel_send_failed:{exc.reason}", exc.detail
                    ) from exc
                previous = endpoint

    def _relay_for(self, payload: Mapping[str, Any]) -> TraceRelay | None:
        """Host trace relay for this dispatch, or ``None`` when it cannot be built.

        The worker payload always carries the host ``artifact_workdir``; a
        dispatch without it keeps the pre-relay behaviour (frames are dropped)
        instead of failing the turn over a trace concern.
        """
        workdir = payload.get("artifact_workdir")
        if not isinstance(workdir, str) or not workdir.strip():
            logger.warning(
                "isolation: dispatch carries no artifact_workdir; guest trace events are dropped"
            )
            return None
        return TraceRelay(workdir)

    def _registry(self) -> EndpointRegistry:
        if self._seams.channel is not None:
            return self._seams.channel
        return self.channel_server

    def _workspace_policy(self) -> WorkspacePolicy:
        if self._seams.workspace_policy is not None:
            return self._seams.workspace_policy
        return WorkspacePolicy.from_environment()

    @property
    def _exports(self) -> WorkspaceExportGuard:
        if self._export_guard is None:
            self._export_guard = WorkspaceExportGuard(self._workspace_policy())
        return self._export_guard

    @property
    def _leases(self) -> OverlayLeaseGuard:
        if self._leases_guard is None:
            self._leases_guard = OverlayLeaseGuard()
        return self._leases_guard

    def _assert_dispatch_binding(self, vm: SessionVm, session_id: str) -> None:
        """Refuse a dispatch whose CID the host map routes to a different session.

        A live VM's CID must stay bound to its own session for the whole
        dispatch; when the map names another session, sending the task would
        hand one session's turn to a sibling's guest. A CID the map does not
        know at all is left to the channel handshake (an unmapped peer is
        refused there), so this guard only catches a *conflicting* binding.
        """
        owner = self._session_map.session_for_cid(vm.peer_cid)
        if owner is not None and str(owner) != session_id:
            raise IsolationError(
                "peer_cid_conflict",
                f"vsock CID {vm.peer_cid} is bound to {owner!r}, not {session_id!r}",
            )

    def _ensure_connection(self) -> LibvirtConnection:
        if self._connection is None:
            self._connection = self._seams.connect(self._config.libvirt_uri)
        return self._connection

    def _destroy_orphan_domain(self, name: str) -> None:
        connection = self._ensure_connection()
        try:
            domain = connection.lookupByName(name)
        except Exception:  # libvirt boundary: no such domain is the common case
            logger.debug("isolation reap: no libvirt domain named %s", name)
            return
        try:
            if domain.isActive():
                domain.destroy()
            domain.undefine()
        except Exception as exc:  # libvirt boundary
            raise IsolationError("orphan_cleanup_failed", f"{name}: {exc}") from exc

    @staticmethod
    def _teardown_quietly(domain: SessionDomain) -> None:
        try:
            domain.teardown()
        except IsolationError:
            logger.warning(
                "isolation: failed to tear down a half-started domain", exc_info=True
            )


def _session_id_of(session: SessionSpec | str) -> str:
    raw = session.session_id if isinstance(session, SessionSpec) else session
    text = str(raw).strip()
    if not text:
        raise IsolationError("invalid_session_id", "session id must be non-empty")
    return text


def _task_session_id(task: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    metadata = task.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    for candidate in (payload.get("current_session"), metadata.get("session"), task.get("task_id")):
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text:
            return text
    raise IsolationError("invalid_session_id", "task carries no session/task id")


def _proxy_ports(plan: ProviderSecretPlan | None) -> tuple[int, ...]:
    """Host-local ports the guest needs for this dispatch's host services.

    Only the todo-18 egress-auth proxy qualifies, and only when the guest
    addresses it at the QEMU user-net gateway alias: any other guest host
    renders no route (the LAN/host denies stay in force) and is reported
    instead of being silently widened.
    """
    if plan is None or plan.mode != MODE_PROXY or plan.proxy_bind is None:
        return ()
    parts = urlsplit(plan.guest_base_url)
    host = (parts.hostname or "").strip().lower()
    if parts.port is None or host != HOST_LOOPBACK_ALIAS:
        logger.warning(
            "isolation: egress-auth proxy guest host %r is not the user-net "
            "gateway alias; no host-local egress route is rendered",
            host,
        )
        return ()
    return (parts.port,)


def _host_secret_values(env: Mapping[str, str]) -> tuple[str, ...]:
    """Values of the provider-token env names, for the guest-env leak check."""
    return tuple(
        str(env[name]) for name in SECRET_KEY_NAMES if str(env.get(name, "")).strip()
    )


def _payload_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None:
        raise IsolationError(f"payload_missing:{key}", f"{key} is required for dispatch")
    if not isinstance(value, str):
        raise IsolationError(
            f"payload_invalid:{key}", f"{key} must be str, got {type(value).__name__}"
        )
    return value


def _payload_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if value is None:
        raise IsolationError(f"payload_missing:{key}", f"{key} is required for dispatch")
    if isinstance(value, bool) or not isinstance(value, int):
        raise IsolationError(
            f"payload_invalid:{key}", f"{key} must be int, got {type(value).__name__}"
        )
    return value


def _as_json(value: object, *, path: str) -> JsonValue:
    """Narrow a host payload value into the JSON tree; refuse anything else."""
    match value:
        case str() | bool() | int() | float() | None:
            return value
        case list():
            return [_as_json(item, path=f"{path}[]") for item in value]
        case dict():
            return {
                str(key): _as_json(item, path=f"{path}.{key}")
                for key, item in value.items()
            }
        case _ as unreachable:
            raise IsolationError(
                "payload_not_serializable",
                f"{path}: {type(unreachable).__name__}",
            )


def _optional_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _json_items(value: object, *, path: str) -> tuple[JsonValue, ...]:
    if not isinstance(value, list) or not value:
        return ()
    return tuple(_as_json(item, path=path) for item in value)


def _json_mapping(value: object, *, path: str) -> JsonValue:
    if not isinstance(value, dict) or not value:
        return None
    return _as_json(value, path=path)


def _host_task_metadata(
    metadata: object, mapping: WorkspacePathMapping
) -> RehydratedTaskMetadata:
    """Narrow host task metadata into the rehydration section (paths mapped)."""
    if not isinstance(metadata, Mapping):
        return RehydratedTaskMetadata()
    resume = resume_wire_metadata(metadata, mapping)
    calls = resume.get("approved_tool_calls")
    rules = resume.get("approved_session_rules")
    return RehydratedTaskMetadata(
        must_write_skills=tuple(
            item
            for item in _metadata_items(metadata, "must_write_skills")
            if isinstance(item, str)
        ),
        approved_tool_calls=tuple(str(item) for item in calls) if isinstance(calls, list) else (),
        approved_session_rules=tuple(rules) if isinstance(rules, list) else (),
        forced_tool_call=resume.get("forced_tool_call"),
        approval_context_messages=tuple(
            item
            for item in _metadata_items(metadata, "approval_context_messages")
            if isinstance(item, dict)
        ),
        model_context_window=_metadata_positive_int(metadata, "model_context_window"),
        restrict_tools_to=tuple(
            item
            for item in _metadata_items(metadata, "restrict_tools_to")
            if isinstance(item, str)
        ),
        block_tool_names=tuple(
            item
            for item in _metadata_items(metadata, "block_tool_names")
            if isinstance(item, str)
        ),
    )


def _metadata_items(metadata: Mapping[str, Any], key: str) -> list[Any]:
    value = metadata.get(key)
    return value if isinstance(value, list) else []


def _metadata_positive_int(metadata: Mapping[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return int(value)


def _guest_input_file(value: JsonValue, mapping: WorkspacePathMapping) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IsolationError("input_file_invalid", "attachment path is invalid")
    try:
        root = Path(mapping.host_root).resolve(strict=True)
        source = Path(value).expanduser().resolve(strict=True)
        relative = source.relative_to(root)
    except (OSError, ValueError):
        raise IsolationError(
            "input_file_outside_workspace",
            "attachments must be inside the exported workspace",
        ) from None
    if not source.is_file():
        raise IsolationError("input_file_invalid", "attachment is not a file")
    return f"{mapping.guest_root}/{relative.as_posix()}"


def _guest_steer_item(value: object, mapping: WorkspacePathMapping) -> JsonValue | None:
    if not isinstance(value, Mapping):
        return None
    text = value.get("text")
    message_id = value.get("message_id")
    files = value.get("input_files")
    skills = value.get("selected_skills")
    if not isinstance(text, str) or not isinstance(message_id, str):
        return None
    if not isinstance(files, list) or not all(isinstance(path, str) for path in files):
        files = []
    if not isinstance(skills, list) or not all(isinstance(skill, str) for skill in skills):
        skills = []
    guest_files: list[str] = []
    unavailable = 0
    for path in files:
        try:
            guest_files.append(_guest_input_file(path, mapping))
        except IsolationError:
            text = text.replace(path, "[вложение недоступно вне рабочей папки VM]")
            unavailable += 1
    if unavailable:
        text = (
            f"{text}\n[Не удалось передать {unavailable} вложение(й): "
            "файлы должны находиться в рабочей папке сессии.]"
        )
    return {
        "text": mapping.host_to_guest(text),
        "message_id": message_id,
        "input_files": guest_files,
        "selected_skills": skills,
    }


def _frame_reason(payload: Mapping[str, JsonValue]) -> str:
    reason = payload.get("reason")
    return str(reason) if isinstance(reason, str) and reason else "unknown"


def _recv_wait_s(deadline: float | None, now: float, *, polling: bool) -> float:
    """One bounded ``recv_from_guest`` slice so the loop re-checks its deadlines."""
    if polling:
        if deadline is None:
            return CANCEL_POLL_S
        return max(0.0, min(CANCEL_POLL_S, deadline - now))
    if deadline is None:
        return HEARTBEAT_INTERVAL_S
    return max(0.0, min(HEARTBEAT_INTERVAL_S, deadline - now))


def _is_canceled_status(value: object) -> bool:
    return isinstance(value, str) and value in _CANCELED_STATUSES


def _overlay_of(vm: SessionVm) -> Path | None:
    overlay = vm.domain.overlay
    return Path(overlay) if overlay is not None else None


def _cancel_timeout_result() -> dict[str, Any]:
    """Terminal ``fail`` for a stop the guest never acknowledged (todo 11).

    The DB vocabulary is untouched: this is the ``fail`` the worker already
    maps, with ``cancel_timeout`` recorded in ``failure_reason``/``error``.
    """
    detail = (
        "cancel_timeout: guest did not acknowledge the cancel within the TTL; "
        "the VM was destroyed anyway"
    )
    return {
        "status": "fail",
        "output_text": "",
        "usage": {},
        "tool_calls_count": 0,
        "failure_reason": STOP_REASON_CANCEL_TIMEOUT,
        "failure_details": detail,
        "error": detail,
    }


def _vm_failure_result(session_id: str, *, reason: str, detail: str) -> dict[str, Any]:
    """Terminal ``fail`` for a dispatch whose VM/guest disappeared (todo 25).

    The ``error`` text is what the worker copies into ``error_message``; the
    ``failure_reason`` stays a machine token (``vm_lost``/``channel_lost``), and
    a contained failure is never normalized into an ``ok``.
    """
    text = (
        f"session {session_id} lost its VM ({reason}: {detail}); "
        "the VM was destroyed, the task failed and the host is unaffected"
    )
    return {
        "status": "fail",
        "output_text": "",
        "usage": {},
        "tool_calls_count": 0,
        "failure_reason": reason,
        "failure_details": text,
        "error": text,
    }


def _result_timeout(payload: Mapping[str, Any]) -> float | None:
    value = payload.get("timeout_s")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return float(value) + _RESULT_TIMEOUT_MARGIN_S


def _user_input_timeout(payload: Mapping[str, Any]) -> float:
    """How long the relay waits for a person, mirroring the store path default."""
    value = payload.get("timeout_s")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_USER_INPUT_TIMEOUT_S
    return float(value)


def _normalize_result(payload: Mapping[str, JsonValue]) -> dict[str, Any]:
    """Validate a guest ``result`` frame into the executor's result shape.

    ``canceled`` is the guest runner's documented shape for a run the host
    aborted (a cancelled user-input form, ``cancel``, a closed peer). It is not
    a task-table status, so it folds into the terminal ``fail`` the worker
    already knows, keeping the cancel visible in ``failure_reason``.
    """
    status = payload.get("status")
    if not isinstance(status, str) or status not in _RESULT_STATUSES | _CANCELED_STATUSES:
        raise IsolationError("invalid_result_status", repr(status))
    output_text = payload.get("output_text")
    if not isinstance(output_text, str):
        raise IsolationError(
            "invalid_result_output", f"output_text is {type(output_text).__name__}"
        )
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise IsolationError(
            "invalid_result_usage", f"usage is {type(usage).__name__}"
        )
    result: dict[str, Any] = {
        "status": "fail" if status in _CANCELED_STATUSES else status,
        "output_text": output_text,
        "usage": dict(usage),
        "tool_calls_count": _int_or_zero(payload.get("tool_calls_count")),
    }
    if status in _CANCELED_STATUSES:
        result["failure_reason"] = status
        result["failure_details"] = _CANCELED_DETAILS
        result["error"] = _CANCELED_DETAILS
    for key in ("failure_reason", "failure_details"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            result[key] = value
    blocked = payload.get("approval_blocked_tools")
    if isinstance(blocked, list):
        result["approval_blocked_tools"] = list(blocked)
    return result


_RESULT_PATH_KEYS: Final[tuple[str, ...]] = ("output_text",)


def _rewrite_result_guest_paths(
    result: dict[str, Any], mapping: WorkspacePathMapping
) -> dict[str, Any]:
    """Map guest workspace paths in the answer text back to host paths.

    The attachment collector resolves host paths out of the answer; a guest
    ``/workspace/...`` path never exists on the host, so without this rewrite an
    isolated task attaches nothing while an off-mode task attaches its files.
    The replacement is boundary-aware on both sides, so ``/workspacefoo`` and a
    ``/workspace`` segment inside a longer host path both stay untouched.

    The runtime artifact subtree (``<guest root>/.igorek-artifacts/...``,
    trace/tool results/skill assets/subagent journal) is deliberately left as a
    guest path: it is not a user file, and rewriting it would let the attachment
    collector ship runtime internals to the client.
    """
    guest_root = mapping.guest_root
    if not guest_root:
        return result
    artifact_segment = re.escape("/" + ARTIFACT_DIRNAME)
    pattern = re.compile(
        r"(?<![\w.-])"
        + re.escape(guest_root)
        + r"(?![\w.-]|"
        + artifact_segment
        + r"(?![\w.-]))"
    )
    rewritten = dict(result)
    for key in _RESULT_PATH_KEYS:
        text = rewritten.get(key)
        if isinstance(text, str) and guest_root in text:
            rewritten[key] = pattern.sub(lambda _match: mapping.host_root, text)
    return rewritten


def _int_or_zero(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value
