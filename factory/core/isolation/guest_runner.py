"""In-guest cc-backend runner: the guest half of one isolated session turn (todo 7).

The runner is the entrypoint that runs INSIDE the session VM
(``igorek-factory guest-runner`` or ``python -m factory.core.isolation.guest_runner``).
It connects to the host over the framed vsock channel
(:mod:`factory.core.isolation.channel`), waits for one ``task`` frame, runs the
existing cc-backend loop with the attached workspace as workdir, streams the
loop's trace as ``event`` frames, and answers with a final ``result`` frame.

Boundary rules (never relaxed in the guest):

* no host files, no host sockets, no host paths: the workdir/artifact dir are
  validated as guest roots by :class:`factory.core.isolation.guest_task.GuestTask`,
  and every outbound frame passes the channel's payload policy;
* approvals are never bypassed: the guest always runs with the executor's
  approval path enabled, and a blocked tool is forwarded to the host as the
  executor's own blocked-tool payload (an inert ``approval_request`` envelope;
  the host relay is plan todo 9) - the guest never resolves its own approval;
* user input never touches a host store or a terminal: the executor receives
  :class:`~factory.core.isolation.guest_user_input.UserInputBridge` as its
  ``user_input_provider`` (plan todo 10), so a form becomes a
  ``user_input_request`` frame and the same run resumes on the host's reply;
* a malformed/unsupported ``task`` is answered with an ``error`` frame and the
  process exits 0 — never a crash, never a silently dropped task;
* MCP servers and skill assets arrive in the task payload (plan todo 31): the
  runner applies the placements and materializes the assets before the loop
  starts, and every capability it cannot place is emitted as an ``event`` frame
  with an ``isolation.*`` status — the guest never silently degrades;
* the egress policy is ENFORCED, not assumed (plan todo 40): the runner applies
  the boot-time default-deny baseline before it reads a task and the
  host-rendered per-session ruleset before the loop starts, refusing the turn
  (``error`` frame, no executor run) when the ruleset cannot be installed — it
  never falls back to the QEMU user-net defaults. A denial the ruleset logs is
  drained into the trace as ``isolation.blocked_host`` (todo 40b);
* an executor failure that is a real resource exhaustion is reported as a
  classified ``error`` frame (plan todo 24): the runner maps the exception's
  ``errno``/``MemoryError`` onto a documented limit token
  (:func:`factory.core.isolation.limits.limit_token_for_error`) so the host can
  contain the VM, instead of the generic ``executor_error`` result that would
  leave it running. Any other failure keeps the generic result.

Streaming needs no executor seam: the executor already appends its trace to
``<artifact_workdir>/cc_backend_trace.jsonl``, so the runner tails that file
while the loop runs (:class:`factory.core.isolation.guest_trace.TraceTailer`).
The injected executor hooks are ``log_fn``, which doubles as the cooperative
cancel point (once the host sends ``cancel`` the next log line raises
:class:`GuestCanceled` inside the loop, and if the loop stays blocked the runner
answers ``result{status:"canceled"}`` after a short grace anyway - the host's
domain teardown is the hard stop), and ``user_input_provider`` (todo 10).

# allow: SIZE_OK - one cohesive in-guest session loop: channel intake, the
# executor thread, trace tailing and the vsock CLI entrypoint all share the same
# per-run state; splitting them would separate halves of one lifecycle.
"""

from __future__ import annotations

import argparse
import os
import queue
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, assert_never

from .git_gateway_contract import guest_git_environment
from .apk_delivery import guest_delivery_environment
from ..cc_backend_trace import TRACE_FILENAME
from ..trace_taxonomy import CcTraceEvent
from .channel import (
    DISPLAY_ENVELOPE_KEY,
    HEARTBEAT_INTERVAL_S,
    ChannelConfig,
    ChannelError,
    ChannelMessage,
    JsonValue,
    MessageKind,
    PeerSession,
    guest_connect,
)
from .egress_enforce import ApplyResult, apply_egress_ruleset, boot_baseline_ruleset
from .errors import IsolationError
from .extensions import (
    CapabilityStatus,
    apply_guest_mcp_servers,
    materialize_skill_assets,
)
from .guest_bwrap import GUEST_MODE_ENV
from .guest_result import FALLBACK_KEYS, build_result
from .guest_task import (
    DEFAULT_GUEST_ARTIFACT_DIR,
    DEFAULT_GUEST_ROOTS,
    GuestTask,
    GuestTaskError,
    LogFn,
    narrow_json_object,
)
from .guest_trace import TraceTailer, trace_start_offset
from .guest_user_input import (
    DEFAULT_USER_INPUT_TIMEOUT_S,
    REPLY_TIMEOUT_MARGIN_S,
    UserInputBridge,
    UserInputReply,
    SteerBridge,
    SessionSearchBridge,
    GoalBridge,
    VaultStoreBridge,
    MemoryForgetBridge,
    WebToolBridge,
)
from .persistence import durable_data_root
from .limits import limit_token_for_error
from .network_blocked import BlockedHostWatcher, KernelLogSource
__all__ = [
    "DEFAULT_GUEST_ARTIFACT_DIR",
    "DEFAULT_VSOCK_HOST_CID",
    "GuestCanceled",
    "GuestRunner",
    "RunnerSeams",
    "RunnerTuning",
    "TraceTailer",
    "main",
    "open_vsock_client",
    "run_guest_runner",
]

# VMADDR_CID_HOST: the guest's fixed address for its own hypervisor host.
DEFAULT_VSOCK_HOST_CID: Final[int] = 2
# Payload marker for trace records crossing as `event` frames (todo 8 relays
# them into the host's mobile pipeline).
TRACE_EVENT_KIND: Final[str] = "trace"
TRACE_RECORD_KEY: Final[str] = "record"
# Error-frame prefix for a turn the runner refuses because the egress ruleset
# could not be enforced (plan todo 40a): never a silent unrestricted run.
EGRESS_UNENFORCED_PREFIX: Final[str] = "egress_ruleset_unenforced:"

Executor = Callable[..., dict[str, Any]]
EventLog = Callable[[str], None]
EgressApplier = Callable[[str], ApplyResult]
KernelLogLines = Callable[[], tuple[str, ...]]


class GuestCanceled(Exception):
    """Raised inside the executor's log sink once the host canceled the run."""


@dataclass(frozen=True, slots=True)
class RunnerSeams:
    """Injected boundaries; defaults are the real in-guest implementations."""

    executor: Executor | None = None
    log: EventLog | None = None
    apply_egress: EgressApplier | None = None
    kernel_log: KernelLogLines | None = None


@dataclass(frozen=True, slots=True)
class RunnerTuning:
    """Timing and policy knobs for one runner (tests shrink the intervals)."""

    guest_roots: tuple[str, ...] = DEFAULT_GUEST_ROOTS
    poll_interval_s: float = 0.05
    cancel_grace_s: float = 2.0
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S


@dataclass(slots=True)
class _RunState:
    """Mutable per-run wiring shared by the runner's threads."""

    user_input: UserInputBridge
    web_tools: WebToolBridge
    session_search: SessionSearchBridge
    vault_store: VaultStoreBridge
    memory_forget: MemoryForgetBridge
    steer: SteerBridge
    goal_control: GoalBridge | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    inbox: queue.Queue[ChannelMessage | None] = field(default_factory=queue.Queue)


class GuestRunner:
    """One-shot in-guest session runner: task in, ordered events + result out."""

    def __init__(
        self,
        session: PeerSession,
        *,
        seams: RunnerSeams | None = None,
        tuning: RunnerTuning | None = None,
    ) -> None:
        self._session = session
        self._seams = seams if seams is not None else RunnerSeams()
        self._tuning = tuning if tuning is not None else RunnerTuning()
        self._executor: Executor = (
            self._seams.executor if self._seams.executor is not None else _default_executor
        )
        self._log: EventLog = self._seams.log if self._seams.log is not None else _stderr_log
        self._apply_egress: EgressApplier = (
            self._seams.apply_egress
            if self._seams.apply_egress is not None
            else apply_egress_ruleset
        )
        self._kernel_log: KernelLogSource | None = None
        self._durable_root: str | None = None

    def serve_one(self) -> int:
        """Handle exactly one task; returns the process exit code (0 = clean)."""
        if not self._enforce_egress(boot_baseline_ruleset(), source="boot-baseline"):
            return 0
        task = self._read_task()
        if task is None:
            return 0
        if not self._prepare_artifact_dir(task.artifact_workdir):
            return 0
        self._prepare_durable_data_root(task.workdir)
        self._run_task(task)
        return 0

    def _prepare_durable_data_root(self, workdir: str) -> None:
        """Resolve the durable root inside the shared workspace (todo 26).

        The executor is handed this root, so the checkpoint shadow repos and the
        ``session_changes`` records are produced inside the exported workspace
        and land on the host through the single virtiofs device, surviving a VM
        teardown; the in-guest caches and installs stay on the disposable
        overlay. A workspace that cannot host the durable root (a read-only
        tree) does not fail the turn: the guest keeps its disposable data root
        and the degradation is emitted as a visible trace event. An absent
        workspace directory is skipped without an event - the export is
        validated on the host, so this is not a session a guest can do durable
        work in.
        """
        workspace = Path(workdir)
        if not workspace.is_dir():
            self._log(f"durable data root skipped: {workdir} is not a directory")
            return
        root = durable_data_root(workdir)
        try:
            Path(root).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._log(f"durable data root unavailable: {exc}")
            self._emit_capability_status(
                CapabilityStatus(
                    CcTraceEvent.ISOLATION_DURABLE_ROOT_UNAVAILABLE,
                    "durable_data_root",
                    "mkdir_failed",
                    detail=f"{type(exc).__name__}: {root}",
                )
            )
            return
        self._durable_root = root
        self._log(f"durable data root: {root}")

    def _read_task(self) -> GuestTask | None:
        """First inbound frame must be a well-formed ``task``; error frames otherwise."""
        try:
            message = self._session.read()
        except ChannelError as exc:
            self._log(f"channel read failed before a task arrived: {exc}")
            self._send_error(exc.reason)
            return None
        if message is None:
            self._log("host closed the channel before a task arrived")
            return None
        if message.kind is not MessageKind.TASK:
            self._send_error(f"unexpected_frame:{message.kind}")
            return None
        try:
            return GuestTask.from_wire(message.payload, guest_roots=self._tuning.guest_roots)
        except GuestTaskError as exc:
            self._send_error(exc.reason)
            return None

    def _prepare_artifact_dir(self, artifact_workdir: str) -> bool:
        """Create the guest artifact dir the executor writes its trace into."""
        try:
            Path(artifact_workdir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._log(f"artifact dir unavailable: {exc}")
            self._send_error("artifact_dir_unavailable")
            return False
        return True

    def _run_task(self, task: GuestTask) -> None:
        """Enforce the session's egress policy, then run the loop (todo 40a)."""
        if task.egress is not None and not self._enforce_egress(
            task.egress.ruleset, source="session"
        ):
            return
        watcher = self._start_blocked_host_watch(task)
        try:
            self._run_loop(task, watcher)
        finally:
            self._close_blocked_host_watch(watcher)

    def _enforce_egress(self, ruleset: str, *, source: str) -> bool:
        """Apply and verify one ruleset; a failure refuses the turn, never widens."""
        try:
            result = self._apply_egress(ruleset)
        except IsolationError as exc:
            self._log(f"egress ruleset not enforced ({source}): {exc}")
            self._send_error(f"{EGRESS_UNENFORCED_PREFIX}{exc.code}")
            return False
        self._log(
            f"egress ruleset enforced ({source}): table={result.table} "
            f"rules={result.rule_count} sha256={result.ruleset_sha256[:12]}"
        )
        return True

    def _start_blocked_host_watch(self, task: GuestTask) -> BlockedHostWatcher | None:
        """Follow the guest kernel log so a denied probe becomes a trace event."""
        source = self._seams.kernel_log
        if source is None:
            kernel_log = KernelLogSource()
            try:
                kernel_log.start()
            except OSError as exc:
                self._log(f"blocked-host diagnostics unavailable: {exc}")
                return None
            self._kernel_log = kernel_log
            source = kernel_log
        pinned = task.egress.pinned_map() if task.egress is not None else {}
        return BlockedHostWatcher(task.artifact_workdir, source, pinned_hosts=pinned)

    def _close_blocked_host_watch(self, watcher: BlockedHostWatcher | None) -> None:
        if watcher is not None:
            self._drain_blocked_hosts(watcher)
        kernel_log = self._kernel_log
        self._kernel_log = None
        if kernel_log is not None:
            kernel_log.close()

    def _drain_blocked_hosts(self, watcher: BlockedHostWatcher | None) -> None:
        if watcher is None:
            return
        for event in watcher.poll():
            self._log(
                f"blocked egress attempt: {event.destination}:{event.port}"
                f" ({event.protocol})"
            )

    def _run_loop(self, task: GuestTask, watcher: BlockedHostWatcher | None) -> None:
        """Run the loop in a thread while this thread owns the channel."""
        state = _RunState(
            user_input=UserInputBridge(
                self._session.send, timeout_s=_reply_timeout_s(task)
            ),
            web_tools=WebToolBridge(self._session.send, timeout_s=90.0),
            session_search=SessionSearchBridge(self._session.send, timeout_s=90.0),
            goal_control=GoalBridge(self._session.send, timeout_s=90.0),
            vault_store=VaultStoreBridge(self._session.send, timeout_s=90.0),
            memory_forget=MemoryForgetBridge(self._session.send, timeout_s=_reply_timeout_s(task)),
            steer=SteerBridge(self._session.send, timeout_s=90.0),
        )
        threading.Thread(
            target=self._read_frames,
            args=(state,),
            name="guest-runner-channel",
            daemon=True,
        ).start()
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(heartbeat_stop, self._tuning.heartbeat_interval_s),
            name="guest-runner-heartbeat",
            daemon=True,
        )
        heartbeat.start()
        self._prepare_extensions(task)
        outcome: dict[str, Any] = {}
        trace_path = Path(task.artifact_workdir) / TRACE_FILENAME
        start_offset = trace_start_offset(trace_path)
        worker = threading.Thread(
            target=self._execute,
            args=(task, state, outcome),
            name="guest-runner-executor",
            daemon=True,
        )
        worker.start()
        tailer = TraceTailer(trace_path, start_offset=start_offset)
        cancel_deadline: float | None = None
        while worker.is_alive():
            self._handle_inbound(state)
            self._drain_blocked_hosts(watcher)
            self._emit_events(tailer)
            if state.cancel.is_set() and cancel_deadline is None:
                cancel_deadline = time.monotonic() + self._tuning.cancel_grace_s
            if cancel_deadline is not None and time.monotonic() >= cancel_deadline:
                self._log("cancel grace elapsed; abandoning the running executor thread")
                break
            worker.join(timeout=self._tuning.poll_interval_s)
        heartbeat_stop.set()
        heartbeat.join()
        self._handle_inbound(state)
        self._emit_events(tailer, final=True)
        if state.cancel.is_set():
            self._emit_result(
                {"status": "canceled", "output_text": "", "usage": {}, "tool_calls_count": 0}
            )
            return
        limit_token = limit_token_for_error(outcome.get("error"))
        if limit_token is not None:
            self._log(f"executor hit its {limit_token} limit; reporting the breach")
            self._send_error(limit_token)
            return
        self._emit_approval_requests(outcome)
        self._emit_result(build_result(outcome))

    def _execute(self, task: GuestTask, state: _RunState, outcome: dict[str, Any]) -> None:
        """Executor thread body: never raises, the outcome is always reported."""
        try:
            with guest_git_environment(task.git_gateway), guest_delivery_environment(task.apk_delivery_url):
                outcome["value"] = self._executor(
                    **task.executor_kwargs(
                        self._log_sink(state),
                        user_input_provider=state.user_input.request,
                        web_tool_provider=state.web_tools.request,
                        session_search_provider=state.session_search.request,
                        goal_control_provider=state.goal_control.request if state.goal_control else None,
                        vault_store_provider=state.vault_store.request,
                        memory_forget_provider=state.memory_forget.request,
                        pending_steer_provider=state.steer.request,
                        priority_steer_provider=lambda: state.steer.request(prioritized_only=True),
                        data_root=self._durable_root,
                    )
                )
        except GuestCanceled:
            state.cancel.set()
        except Exception as exc:  # thread boundary, reported as `fail`
            outcome["error"] = exc

    def _read_frames(self, state: _RunState) -> None:
        """Reader thread: push inbound frames until cancel or peer close.

        An idle read window (``read_timeout`` with no bytes) only means the host
        sent nothing yet — a long-running guest tool produces no channel traffic
        for minutes — so keep waiting. A real close (EOF) or any other channel
        error still ends the reader and wakes the run loop.
        """
        try:
            while not state.cancel.is_set():
                try:
                    message = self._session.read()
                except ChannelError as exc:
                    if exc.reason == "read_timeout":
                        continue
                    if not self._session.closed:
                        self._log(f"channel read stopped: {exc}")
                    break
                if message is None:
                    break
                state.inbox.put(message)
        finally:
            state.inbox.put(None)

    def _heartbeat_loop(self, stop_event: threading.Event, interval_s: float) -> None:
        """Emit liveness frames until stopped; a channel error ends the beat."""
        interval = max(0.01, interval_s)
        while not stop_event.wait(interval):
            try:
                self._session.send(MessageKind.HEARTBEAT, {"state": "alive"})
            except ChannelError as exc:
                self._log(f"heartbeat stopped: {exc}")
                return

    def _handle_inbound(self, state: _RunState) -> None:
        while True:
            try:
                message = state.inbox.get_nowait()
            except queue.Empty:
                return
            if message is None:
                self._log("host closed the channel; stopping the run")
                state.cancel.set()
                continue
            match message.kind:
                case MessageKind.CANCEL:
                    self._log("cancel requested by the host")
                    state.cancel.set()
                case MessageKind.TASK:
                    self._send_error("task_already_running")
                case MessageKind.HOST_OP:
                    self._send_error("host_op_unsupported")
                case MessageKind.WEB_REPLY:
                    state.web_tools.deliver(message)
                case MessageKind.SESSION_SEARCH_REPLY:
                    state.session_search.deliver(message)
                case MessageKind.GOAL_REPLY:
                    if state.goal_control:
                        state.goal_control.deliver(message)
                case MessageKind.VAULT_STORE_REPLY:
                    state.vault_store.deliver(message)
                case MessageKind.MEMORY_FORGET_REPLY:
                    state.memory_forget.deliver(message)
                case MessageKind.STEER_REPLY:
                    state.steer.deliver(message)
                case MessageKind.USER_INPUT_REPLY:
                    reply = state.user_input.deliver(message)
                    if reply is UserInputReply.CANCELLED:
                        self._log("user input cancelled by the host")
                        state.cancel.set()
                case MessageKind.APPROVAL_DECISION:
                    # The host parks the task and respawns a fresh guest to resume
                    # (todo 9), so a decision on a live channel must not be applied
                    # here; it can only mean the host is out of sync.
                    self._log(f"ignored frame until its relay lands: {message.kind}")
                case (
                    MessageKind.EVENT
                    | MessageKind.APPROVAL_REQUEST
                    | MessageKind.USER_INPUT_REQUEST
                    | MessageKind.STEER_REQUEST
                    | MessageKind.RESULT
                    | MessageKind.ERROR
                    | MessageKind.HEARTBEAT
                ):
                    self._send_error(f"unexpected_frame:{message.kind}")
                case _ as unreachable:
                    assert_never(unreachable)

    def _log_sink(self, state: _RunState) -> LogFn:
        """Executor log sink that aborts the loop once the host canceled."""

        def log(message: str) -> None:
            if state.cancel.is_set():
                raise GuestCanceled()
            self._log(message)

        return log

    def _prepare_extensions(self, task: GuestTask) -> None:
        """Place MCP servers and materialize skill assets before the loop starts."""
        statuses = materialize_skill_assets(task.skill_assets, task.artifact_workdir)
        statuses += apply_guest_mcp_servers(task.mcp_servers)
        for status in statuses:
            self._emit_capability_status(status)

    def _emit_capability_status(self, status: CapabilityStatus) -> None:
        """Report a capability that is not fully available (never a silent skip)."""
        self._try_send(
            MessageKind.EVENT,
            {
                "kind": TRACE_EVENT_KIND,
                DISPLAY_ENVELOPE_KEY: status.to_trace_record(),
            },
        )

    def _emit_events(self, tailer: TraceTailer, *, final: bool = False) -> None:
        for record in tailer.read_new(final=final):
            self._try_send(
                MessageKind.EVENT, {"kind": TRACE_EVENT_KIND, TRACE_RECORD_KEY: record}
            )

    def _emit_approval_requests(self, outcome: Mapping[str, Any]) -> None:
        """Forward the executor's blocked-tool payload as inert approval envelopes.

        A blocked call is the executor's own decision (todo 9 keeps the approval
        path enabled in the guest and the guest never resolves it): this only
        reports which call waits. The envelope is required because a strict
        ``result`` payload cannot carry a blocked ``Bash`` input - its shell keys
        are denied - so the result frame loses ``approval_blocked_tools`` and the
        host would never see a resolvable approval.
        """
        value = outcome.get("value")
        if not isinstance(value, dict):
            return
        blocked = value.get("approval_blocked_tools")
        if not isinstance(blocked, list):
            return
        for entry in blocked:
            if not isinstance(entry, dict):
                continue
            self._try_send(
                MessageKind.APPROVAL_REQUEST,
                {DISPLAY_ENVELOPE_KEY: narrow_json_object(entry)},
            )

    def _send_error(self, reason: str) -> None:
        self._try_send(MessageKind.ERROR, {"reason": reason})

    def _emit_result(self, result: Mapping[str, JsonValue]) -> None:
        """Send the final result; a policy rejection must surface, not hang."""
        reason: str | None = None
        candidate: dict[str, JsonValue] = dict(result)
        for key in (None, *FALLBACK_KEYS):
            if key is not None:
                if key not in candidate:
                    continue
                candidate = {name: value for name, value in candidate.items() if name != key}
                self._log(f"retrying the result frame without {key!r}")
            reason = self._try_send(MessageKind.RESULT, candidate)
            if reason is None:
                return
        self._send_error(f"result_frame_rejected:{reason}")

    def _try_send(self, kind: MessageKind, payload: Mapping[str, JsonValue]) -> str | None:
        """Send one frame; returns the rejection reason instead of raising."""
        try:
            self._session.send(kind, payload)
        except ChannelError as exc:
            self._log(f"dropped {kind} frame: {exc}")
            return exc.reason
        return None


def _reply_timeout_s(task: GuestTask) -> float:
    """Guest backstop for one user-input reply: the host relay times out first."""
    requested = float(task.timeout_s)
    if requested <= 0:
        return DEFAULT_USER_INPUT_TIMEOUT_S + REPLY_TIMEOUT_MARGIN_S
    return requested + REPLY_TIMEOUT_MARGIN_S


def _default_executor(**kwargs: Any) -> dict[str, Any]:
    """The real cc-backend loop, imported lazily to keep this module light."""
    from ..cc_backend_executor import execute_cc_backend_task

    return execute_cc_backend_task(**kwargs)


def _stderr_log(message: str) -> None:
    print(f"[guest-runner] {message}", file=sys.stderr, flush=True)


def open_vsock_client(cid: int = DEFAULT_VSOCK_HOST_CID, port: int | None = None) -> socket.socket:
    """Connect to the host over AF_VSOCK; fails closed when vsock is absent."""
    effective_port = ChannelConfig().port if port is None else port
    family = getattr(socket, "AF_VSOCK", None)
    if not isinstance(family, int):
        raise ChannelError("vsock_unavailable", "AF_VSOCK is not available in this guest")
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.connect((cid, effective_port))
    except OSError as exc:
        sock.close()
        raise ChannelError(
            "vsock_connect_failed", f"cid {cid} port {effective_port}: {exc}"
        ) from exc
    return sock


def run_guest_runner(
    *, vsock_cid: int = DEFAULT_VSOCK_HOST_CID, vsock_port: int | None = None
) -> int:
    """Connect, handshake and serve exactly one task; returns the process exit code."""
    config = ChannelConfig() if vsock_port is None else ChannelConfig(port=vsock_port)
    try:
        sock = open_vsock_client(vsock_cid, config.port)
    except ChannelError as exc:
        _stderr_log(f"cannot reach the host: {exc}")
        return 1
    try:
        session = guest_connect(sock, config)
    except ChannelError as exc:
        _stderr_log(f"handshake failed: {exc}")
        sock.close()
        return 1
    try:
        runner = GuestRunner(session, tuning=RunnerTuning(guest_roots=config.guest_roots))
        return runner.serve_one()
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``igorek-factory guest-runner [--vsock-cid N] [--vsock-port N]``."""
    parser = argparse.ArgumentParser(
        prog="igorek-factory guest-runner",
        description="Run the cc-backend loop inside a session VM (host<->guest vsock).",
    )
    parser.add_argument(
        "--vsock-cid",
        type=int,
        default=DEFAULT_VSOCK_HOST_CID,
        help=f"Host vsock CID (VMADDR_CID_HOST is {DEFAULT_VSOCK_HOST_CID})",
    )
    parser.add_argument(
        "--vsock-port",
        type=int,
        default=None,
        help="Host vsock port (default: IGOREK_ISOLATION_VSOCK_PORT or 5005)",
    )
    args = parser.parse_args(argv)
    # Arbitrary-code descendants receive no_new_privs; the immutable controller
    # retains only the explicit policy and owner-approved privilege paths.
    os.environ[GUEST_MODE_ENV] = "1"
    from .guest_process import protect_controller
    protect_controller()
    return run_guest_runner(vsock_cid=args.vsock_cid, vsock_port=args.vsock_port)


if __name__ == "__main__":
    sys.exit(main())
