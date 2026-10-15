#!/usr/bin/env python3
"""TDD suite for the host-operation approval class (plan todo 22).

A host op is not a tool call: the guest may never approve it, and the host must
never let one run merely because an approval-shaped record exists. This suite
pins the whole contract:

* **distinct class** - one host-op request is listed with ``kind == "host_op"``
  (mobile) and a distinct TUI prompt, while existing tool approvals keep their
  exact payload and resolution semantics (``signature`` / ``forced_tool_call``);
* **four distinguishable classes** - workspace action vs network action vs
  dangerous op vs host-op, computed host-side from the typed op catalog;
* **resolution drives the broker** - an allow executes the typed op through the
  todo-19/20 broker exactly once, a reject performs no host action and leaves an
  audit trail entry, and a decision is consumed exactly once;
* **fail closed** - an unknown/malformed host-op record cannot execute anything,
  and a runtime without a broker cannot resolve a host op at all.

Adversarial classes covered here:

* misleading success - every test asserts the action call list and the audit
  trail, never that the request merely "resolved";
* regression - the existing tool-approval payload is asserted by exact equality
  and its resolution path is asserted to bypass the host-op broker;
* dirty worktree - this suite writes only into pytest temp dirs.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from factory.core.cli_chat_approval import (
    _approval_request,
    _blocked_tool_session_rule,
    _blocked_tool_target,
)
from factory.core.cli_tasks import create_task
from factory.core.isolation import host_op_resume, host_op_runtime
from factory.core.isolation.broker import (
    HostOpBroker,
    HostOpBrokerAuthority,
    HostOpCapabilityGrant,
    HostOpStatus,
    JsonlHostOpAudit,
)
from factory.core.isolation.broker_ops import (
    BROKER_VERSION,
    HostOpRegistry,
    default_host_op_registry,
)
from factory.core.isolation.broker_seam import ENV_HOST_OPS_ALLOW, HostOpPolicy
from factory.core.isolation.channel import ChannelMessage, MessageKind
from factory.core.isolation.host_op_approval import (
    DANGEROUS_HOST_OPS,
    HOST_OP_APPROVAL_KIND,
    REASON_ALREADY_RESOLVED,
    HostOpApprovalClass,
    HostOpApprovalError,
    classify_host_op,
    is_host_op_blocked_record,
    parse_host_op_blocked_record,
)
from factory.core.isolation.host_op_gate import HostOpApprovalGate, HostOpApprovalOutcome
from factory.core.isolation.host_op_park import JsonlHostOpParkStore
from factory.core.mobile_bridge.server import BridgeConfig, MobileBridge
from factory.core.mobile_bridge.service import MobileConflict, MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.core.trace_taxonomy import CcTraceEvent

SESSION_ID = "chat-20260923-000000-hostop22"
RESTART_OP = "runtime.restart"
STATUS_OP = "runtime.status"

#: Fixed so the exact tool-approval payload equality assertion stays stable.
SEEDED_UPDATED_AT = 1_700_000_100


class FakeHostAction:
    """Host action layer stand-in: records every call, returns one typed result."""

    def __init__(self, result: Mapping[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result: dict[str, Any] = dict(result or {"restarted": True})

    def __call__(self, args: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append({str(key): value for key, value in args.items()})
        return dict(self.result)


# --------------------------------------------------------------------------- #
# Host-side fixtures: a real broker over a fake action layer + a JSONL trail
# --------------------------------------------------------------------------- #


def _audit_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _gate(
    tmp_path: Path,
    *,
    ops: tuple[str, ...] = (RESTART_OP,),
    session_id: str = SESSION_ID,
    park_path: Path | None = None,
) -> tuple[HostOpApprovalGate, dict[str, FakeHostAction], Path]:
    actions = {op: FakeHostAction({"op": op}) for op in ops}
    trail_path = tmp_path / "host_op_audit.jsonl"
    trail = JsonlHostOpAudit(trail_path)
    specs = tuple(spec for spec in default_host_op_registry().all_specs() if spec.op in ops)
    broker = HostOpBroker(
        HostOpCapabilityGrant(
            registry=HostOpRegistry(specs),
            actions=dict(actions),
        ),
        HostOpBrokerAuthority(
            policy=HostOpPolicy(allowed_ops=frozenset(ops)),
            trail=trail,
            session_id=session_id,
        ),
        host_uid=1000,
    )
    park_store = JsonlHostOpParkStore(park_path) if park_path is not None else None
    return HostOpApprovalGate(broker, trail, park_store=park_store), actions, trail_path


def _host_op_message(op: str = RESTART_OP, args: Mapping[str, Any] | None = None, *, seq: int = 7) -> ChannelMessage:
    return ChannelMessage(
        kind=MessageKind.HOST_OP,
        seq=seq,
        payload={
            "version": BROKER_VERSION,
            "op": op,
            "args": {"target": "gateway"} if args is None else dict(args),
        },
    )


def _parked_request(
    gate: HostOpApprovalGate,
    *,
    op: str = RESTART_OP,
    args: Mapping[str, Any] | None = None,
    seq: int = 7,
):
    """Drive the real broker to ``approval_required`` and park that request."""
    if args is None:
        args = {"target": "gateway"} if op == RESTART_OP else {}
    message = _host_op_message(op, args, seq=seq)
    response = gate.broker.handle(message, approval_granted=False)
    assert response.status is HostOpStatus.APPROVAL_REQUIRED
    return gate.park(message, response, task_id="task-22", session_id=SESSION_ID, reason="host-side restart")


# --------------------------------------------------------------------------- #
# Mobile fixtures: temp database, one session, one parked task
# --------------------------------------------------------------------------- #


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path))
    initialize_database(get_db_path())
    session_path = tmp_path / "chat_sessions" / f"{SESSION_ID}.jsonl"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "type": "meta",
                "session_id": SESSION_ID,
                "created_at": 1_700_000_000,
                "profile": "cc-local-8081",
                "cwd": str(tmp_path),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _service(home: Path, gate: HostOpApprovalGate | None = None) -> MobileService:
    return MobileService(profile="cc-local-8081", workdir=str(home), host_op_gate=gate)


def _seed_blocked_task(
    home: Path,
    blocked: list[dict[str, Any]],
    *,
    status: str = "waiting_approval",
    updated_at: int = SEEDED_UPDATED_AT,
) -> str:
    created = create_task(
        text="промпт задачи",
        profile="cc-local-8081",
        metadata={
            "source": "mobile_bridge",
            "platform": "android",
            "session": SESSION_ID,
            "cwd": str(home),
        },
    )
    task_id = str(created["task_id"])
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, approval_blocked_tools = ?, output_text = ?, updated_at = ? "
            "WHERE task_id = ?",
            (
                status,
                json.dumps(blocked, ensure_ascii=False),
                "Для выполнения этого действия требуется подтверждение.",
                updated_at,
                task_id,
            ),
        )
    return task_id


def _task_row(task_id: str) -> dict[str, Any]:
    with sqlite3.connect(get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return dict(row) if row else {}


def _task_metadata(task_id: str) -> dict[str, Any]:
    raw = _task_row(task_id).get("metadata")
    loaded = json.loads(raw) if isinstance(raw, str) and raw else {}
    return loaded if isinstance(loaded, dict) else {}


def _seed_resumed_task(home: Path, resume: dict[str, Any]) -> str:
    """A queued task as the TUI creates it on resume: the decision rides in metadata."""
    created = create_task(
        text="промпт задачи",
        profile="cc-local-8081",
        metadata={
            "source": "cli_chat_rich",
            "platform": "android",
            "session": SESSION_ID,
            "cwd": str(home),
            **resume,
        },
    )
    return str(created["task_id"])


def _pending_handoff(gate: HostOpApprovalGate, *, op: str, decision: str, seq: int) -> dict[str, Any]:
    """The exact record ``chat_session`` stores for a TUI-approved host op."""
    request = _parked_request(gate, op=op, args={}, seq=seq)
    return HostOpApprovalOutcome.pending_broker(
        request, decision=decision, resolved_by="tui"
    ).to_record()


TOOL_SIGNATURE = 'Bash:{"command":"rm -rf build"}'


def _tool_blocked_record(request_id: str = "call_blocked_1") -> dict[str, Any]:
    return {
        "tool": "Bash",
        "input": {"command": "rm -rf build"},
        "target": "rm -rf build",
        "signature": TOOL_SIGNATURE,
        "tool_use_id": request_id,
        "source": "tool_call",
        "reason": "деструктивная команда",
        "session_rule": {
            "rule_type": "exact_tool_call",
            "tool": "Bash",
            "signature": TOOL_SIGNATURE,
        },
    }


# --------------------------------------------------------------------------- #
# The class taxonomy and the blocked-record codec
# --------------------------------------------------------------------------- #


def test_plan_taxonomy_is_four_distinct_host_side_classes() -> None:
    # Given: the plan's four host-side approval classes
    values = {member.value for member in HostOpApprovalClass}
    assert values == {"workspace_action", "network_action", "dangerous_op", "host_op"}
    assert HOST_OP_APPROVAL_KIND == "host_op"

    # Then: the host-side classifier maps each class deterministically
    assert classify_host_op("workspace.export") is HostOpApprovalClass.WORKSPACE_ACTION
    assert classify_host_op("network.egress_allow") is HostOpApprovalClass.NETWORK_ACTION
    assert RESTART_OP in DANGEROUS_HOST_OPS
    assert classify_host_op(RESTART_OP) is HostOpApprovalClass.DANGEROUS_OP
    assert classify_host_op(STATUS_OP) is HostOpApprovalClass.HOST_OP

    # And: it is total for unregistered/unsafe labels (never raises, never guesses)
    assert classify_host_op("shell rm -rf /") is HostOpApprovalClass.HOST_OP


def test_blocked_record_round_trips_and_rejects_malformed(tmp_path: Path) -> None:
    # Given: a parked host-op request
    gate, _actions, _trail = _gate(tmp_path)
    request = _parked_request(gate)
    record = request.to_blocked_record()

    # Then: the stored record is marked as a host-op with the typed class
    assert record["kind"] == HOST_OP_APPROVAL_KIND
    assert is_host_op_blocked_record(record) is True
    assert record["op"] == RESTART_OP
    assert record["args"] == {"target": "gateway"}
    assert record["approval_class"] == "dangerous_op"
    assert record["request_id"] == request.request_id

    # And: it parses back into the identical typed request
    parsed = parse_host_op_blocked_record(record)
    assert parsed.to_blocked_record() == record
    assert parsed.request_id == request.request_id
    assert parsed.args == {"target": "gateway"}

    # Then: malformed host-op records are refused, never half-accepted
    for broken in (
        {**record, "request_id": ""},
        {**record, "approval_class": "totally_unknown"},
        {**record, "args": ["not", "a", "mapping"]},
        {**record, "op": ""},
    ):
        with pytest.raises(HostOpApprovalError):
            parse_host_op_blocked_record(broken)

    # And: a tool record is not a host-op record at all
    assert is_host_op_blocked_record(_tool_blocked_record()) is False


# --------------------------------------------------------------------------- #
# The gate: one decision per request, driving the todo-19/20 broker
# --------------------------------------------------------------------------- #


def test_allow_executes_the_typed_op_once_and_audits(tmp_path: Path) -> None:
    # Given: a parked runtime.restart request with a recording host action
    gate, actions, trail_path = _gate(tmp_path)
    request = _parked_request(gate)
    assert actions[RESTART_OP].calls == []
    assert [entry["phase"] for entry in _audit_entries(trail_path)] == ["decision"]

    # When: the person allows it once
    outcome = gate.resolve(request.request_id, decision="allow_once", resolved_by="mobile_bridge")

    # Then: the typed op ran exactly once with its validated typed args
    assert actions[RESTART_OP].calls == [{"target": "gateway"}]
    assert outcome.executed is True
    assert outcome.status == "ok"
    assert outcome.reason == "approved"
    assert outcome.decision == "allow_once"
    assert dict(outcome.result) == {"op": RESTART_OP}

    # And: the audit trail holds the resolution plus the broker decision/outcome
    phases = [entry["phase"] for entry in _audit_entries(trail_path)]
    assert phases == ["decision", "resolution", "decision", "outcome"]
    resolution = _audit_entries(trail_path)[1]
    assert resolution == {
        "kind": CcTraceEvent.ISOLATION_HOST_OP,
        "phase": "resolution",
        "op": RESTART_OP,
        "approval_class": "dangerous_op",
        "allowed": True,
        "status": "approved",
        "reason": "approved",
        "session": SESSION_ID,
        "request_seq": 7,
        "resolved_by": "mobile_bridge",
    }

    # Then: the same decision cannot run the op a second time
    with pytest.raises(HostOpApprovalError):
        gate.resolve(request.request_id, decision="allow_once", resolved_by="mobile_bridge")
    assert actions[RESTART_OP].calls == [{"target": "gateway"}]


def test_reject_performs_no_host_action_and_is_audited(tmp_path: Path) -> None:
    # Given: a parked request
    gate, actions, trail_path = _gate(tmp_path)
    request = _parked_request(gate)

    # When: the person rejects it
    outcome = gate.resolve(request.request_id, decision="reject", resolved_by="mobile_bridge")

    # Then: no host action ran and the rejection is audited
    assert actions[RESTART_OP].calls == []
    assert outcome.executed is False
    assert outcome.status == "denied"
    assert outcome.reason == "rejected_by_user"
    entries = _audit_entries(trail_path)
    assert [entry["phase"] for entry in entries] == ["decision", "resolution"]
    assert entries[1]["allowed"] is False
    assert entries[1]["status"] == "denied"
    assert entries[1]["reason"] == "rejected_by_user"
    assert entries[1]["resolved_by"] == "mobile_bridge"

    # And: a rejected decision is consumed exactly once
    with pytest.raises(HostOpApprovalError):
        gate.resolve(request.request_id, decision="reject", resolved_by="mobile_bridge")
    assert actions[RESTART_OP].calls == []


def test_allow_session_grants_the_op_for_the_session(tmp_path: Path) -> None:
    gate, actions, _trail = _gate(tmp_path)
    request = _parked_request(gate)

    outcome = gate.resolve(request.request_id, decision="allow_session", resolved_by="mobile_bridge")

    assert outcome.decision == "allow_session"
    assert actions[RESTART_OP].calls == [{"target": "gateway"}]
    assert gate.session_granted(RESTART_OP, session_id=request.session_id) is True
    assert gate.session_granted(RESTART_OP, session_id="other-session") is False
    assert gate.session_granted(RESTART_OP) is False
    assert gate.session_granted("runtime.update", session_id=request.session_id) is False

    from factory.core.isolation.host_op_runtime import HostOpApprovalRuntime
    runtime = HostOpApprovalRuntime(gate)
    other = runtime.handle_request(request.channel_message(), task_id='other-task', session_id='other-session')
    assert other.parked_request is not None
    assert actions[RESTART_OP].calls == [{"target": "gateway"}]
    own = runtime.handle_request(request.channel_message(), task_id='own-task', session_id=request.session_id)
    assert own.parked_request is None
    assert actions[RESTART_OP].calls == [{"target": "gateway"}, {"target": "gateway"}]


def test_parking_a_replayed_request_changes_nothing(tmp_path: Path) -> None:
    gate, _actions, _trail = _gate(tmp_path)
    message = _host_op_message()
    response = gate.broker.handle(message, approval_granted=False)

    first = gate.park(message, response, task_id="task-22", session_id=SESSION_ID)
    replay = gate.park(message, response, task_id="task-22", session_id=SESSION_ID)

    assert replay == first
    assert gate.pending_ids == (first.request_id,)


def test_gate_refuses_to_park_anything_but_a_pending_approval(tmp_path: Path) -> None:
    gate, _actions, _trail = _gate(tmp_path)
    allowed = gate.broker.handle(_host_op_message(), approval_granted=True)
    denied = gate.broker.handle(_host_op_message("runtime.update"), approval_granted=True)

    assert allowed.status is HostOpStatus.OK
    assert denied.status is HostOpStatus.DENIED
    for response in (allowed, denied):
        with pytest.raises(HostOpApprovalError):
            gate.park(_host_op_message(), response, task_id="task-22", session_id=SESSION_ID)


def test_tui_handoff_record_is_a_pending_broker_resolution(tmp_path: Path) -> None:
    # Given: a parked request the TUI approved in-process (no broker there)
    gate, _actions, _trail = _gate(tmp_path)
    request = _parked_request(gate)

    outcome = HostOpApprovalOutcome.pending_broker(request, decision="allow_once", resolved_by="tui")
    record = outcome.to_record()

    # Then: the handoff is explicit - pending, not executed, and routable by op
    assert record["request_id"] == request.request_id
    assert record["op"] == RESTART_OP
    assert record["decision"] == "allow_once"
    assert record["status"] == "pending_broker"
    assert record["executed"] is False
    assert record["resolved_by"] == "tui"


# --------------------------------------------------------------------------- #
# Exactly-once across gate instances: the shared park index is the authority
# --------------------------------------------------------------------------- #


def test_gate_refuses_its_own_request_once_another_instance_claimed_it(tmp_path: Path) -> None:
    """The verifier's repro: B claims first, A still holds the request in memory.

    Two gates over the same data root share the host-owned park index; the
    instance that parked the request must refuse it after another instance has
    claimed the record, even though the request is still in its pending table.
    """
    # Given: two gate instances over one data root (shared audit + park index)
    park_path = tmp_path / "host_op_parked.json"
    gate_a, actions_a, trail_path = _gate(tmp_path, park_path=park_path)
    gate_b, actions_b, _ = _gate(tmp_path, park_path=park_path)

    # And: one request parked through gate A, indexed in the shared park index
    request = _parked_request(gate_a)
    handoff = HostOpApprovalOutcome.pending_broker(
        request, decision="allow_once", resolved_by="tui"
    ).to_record()

    # When: instance B claims the record first and executes the typed op
    executed = gate_b.resolve_stored(handoff, resolved_by="worker")

    # Then: instance A refuses its stale in-memory entry instead of re-running
    with pytest.raises(HostOpApprovalError) as refusal:
        gate_a.resolve(request.request_id, decision="allow_once", resolved_by="mobile_bridge")
    assert refusal.value.code == REASON_ALREADY_RESOLVED

    # And: the typed op ran exactly once, in instance B only
    assert executed.executed is True
    assert executed.status == "ok"
    assert actions_b[RESTART_OP].calls == [{"target": "gateway"}]
    assert actions_a[RESTART_OP].calls == []

    # And: the shared trail holds exactly one resolution and one outcome
    phases = [entry["phase"] for entry in _audit_entries(trail_path)]
    assert phases.count("resolution") == 1
    assert phases.count("outcome") == 1


def test_gate_refuses_a_stored_resolution_of_an_already_claimed_request(
    tmp_path: Path,
) -> None:
    """The worker resume form (``resolve_stored``) shares the same once-only claim."""
    # Given: two gates over one data root and one request parked through gate A
    park_path = tmp_path / "host_op_parked.json"
    gate_a, actions_a, trail_path = _gate(tmp_path, park_path=park_path)
    gate_b, actions_b, _ = _gate(tmp_path, park_path=park_path)
    request = _parked_request(gate_a)
    handoff = HostOpApprovalOutcome.pending_broker(
        request, decision="allow_once", resolved_by="tui"
    ).to_record()
    assert gate_b.resolve_stored(handoff, resolved_by="worker").executed is True

    # When: instance A replays the same stored decision
    with pytest.raises(HostOpApprovalError) as refusal:
        gate_a.resolve_stored(handoff, resolved_by="worker")

    # Then: it is refused and no host action ran a second time
    assert refusal.value.code == REASON_ALREADY_RESOLVED
    assert actions_a[RESTART_OP].calls == []
    assert actions_b[RESTART_OP].calls == [{"target": "gateway"}]
    phases = [entry["phase"] for entry in _audit_entries(trail_path)]
    assert phases.count("resolution") == 1
    assert phases.count("outcome") == 1


def test_gate_refuses_a_mobile_resolution_of_an_already_claimed_request(
    tmp_path: Path,
) -> None:
    """The mobile blocked-record form (``resolve_request``) shares the same claim."""
    # Given: two gates over one data root and one request parked through gate A
    park_path = tmp_path / "host_op_parked.json"
    gate_a, actions_a, trail_path = _gate(tmp_path, park_path=park_path)
    gate_b, actions_b, _ = _gate(tmp_path, park_path=park_path)
    request = _parked_request(gate_a)
    handoff = HostOpApprovalOutcome.pending_broker(
        request, decision="allow_once", resolved_by="tui"
    ).to_record()
    assert gate_b.resolve_stored(handoff, resolved_by="worker").executed is True

    # When: instance A resolves the same request from a mobile blocked record
    with pytest.raises(HostOpApprovalError) as refusal:
        gate_a.resolve_request(
            request.to_blocked_record(), decision="allow_once", resolved_by="mobile_bridge"
        )

    # Then: it is refused and no host action ran a second time
    assert refusal.value.code == REASON_ALREADY_RESOLVED
    assert actions_a[RESTART_OP].calls == []
    assert actions_b[RESTART_OP].calls == [{"target": "gateway"}]
    phases = [entry["phase"] for entry in _audit_entries(trail_path)]
    assert phases.count("resolution") == 1
    assert phases.count("outcome") == 1


# --------------------------------------------------------------------------- #
# Mobile listing: a distinct kind, tool payload byte-identical
# --------------------------------------------------------------------------- #


def test_host_op_request_is_listed_with_a_distinct_kind(home: Path, tmp_path: Path) -> None:
    # Given: a task parked on a host-op request
    gate, _actions, _trail = _gate(tmp_path)
    request = _parked_request(gate)
    task_id = _seed_blocked_task(home, [request.to_blocked_record()])

    # When: the mobile approval center lists pending decisions
    approvals = _service(home, gate).list_approvals(session_id=SESSION_ID)

    # Then: the request is a host-op, not a tool
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval["kind"] == "host_op"
    assert approval["request_id"] == request.request_id
    assert approval["task_id"] == task_id
    assert approval["session_id"] == SESSION_ID
    assert approval["tool"] == RESTART_OP
    assert approval["approval_class"] == "dangerous_op"
    assert approval["dangerous"] is True
    assert approval["signature"] == ""
    assert approval["session_rule"] is None
    assert approval["input"] == {"target": "gateway"}
    assert approval["created_at"] == SEEDED_UPDATED_AT


def test_tool_approval_payload_is_exactly_unchanged(home: Path, tmp_path: Path) -> None:
    # Given: an ordinary blocked tool call (no host-op gate involved)
    gate, actions, _trail = _gate(tmp_path)
    task_id = _seed_blocked_task(home, [_tool_blocked_record()])

    approvals = _service(home, gate).list_approvals(session_id=SESSION_ID)

    # Then: the existing contract is byte-identical, including its exact field set
    assert approvals == [
        {
            "kind": "tool",
            "request_id": "call_blocked_1",
            "task_id": task_id,
            "session_id": SESSION_ID,
            "tool": "Bash",
            "target": "rm -rf build",
            "input": {"command": "rm -rf build"},
            "signature": TOOL_SIGNATURE,
            "session_rule": {
                "rule_type": "exact_tool_call",
                "tool": "Bash",
                "signature": TOOL_SIGNATURE,
            },
            "reason": "деструктивная команда",
            "dangerous": True,
            "created_at": SEEDED_UPDATED_AT,
        }
    ]
    assert actions[RESTART_OP].calls == []


def test_malformed_host_op_record_is_not_listed_as_a_tool(home: Path, tmp_path: Path) -> None:
    # Given: a host-op record with an unknown class (a corrupted/hostile record)
    gate, _actions, _trail = _gate(tmp_path)
    request = _parked_request(gate)
    broken = {**request.to_blocked_record(), "approval_class": "not_a_class"}
    task_id = _seed_blocked_task(home, [broken])

    service = _service(home, gate)

    # Then: it is never mislabelled as a tool approval and never resolvable
    assert service.list_approvals(session_id=SESSION_ID) == []
    with pytest.raises(MobileConflict):
        service.resolve_approval(task_id, request.request_id, "allow_once")


def test_unknown_host_op_request_executes_nothing(home: Path, tmp_path: Path) -> None:
    # Given: a well-formed record whose request the broker never issued
    gate, actions, _trail = _gate(tmp_path)
    request = _parked_request(gate)
    foreign = request.to_blocked_record()
    foreign["request_id"] = "host-op:forged:1"
    task_id = _seed_blocked_task(home, [foreign])

    service = _service(home, gate)

    # Then: the mobile sees it, but resolving it fails closed
    assert [item["kind"] for item in service.list_approvals(session_id=SESSION_ID)] == ["host_op"]
    with pytest.raises(MobileConflict):
        service.resolve_approval(task_id, "host-op:forged:1", "allow_once")
    assert actions[RESTART_OP].calls == []
    assert gate.pending_ids == (request.request_id,)


# --------------------------------------------------------------------------- #
# Mobile resolution: allow drives the broker, reject never does
# --------------------------------------------------------------------------- #


def test_resolve_allow_drives_the_broker_once(home: Path, tmp_path: Path) -> None:
    # Given: a task parked on a host-op request
    gate, actions, trail_path = _gate(tmp_path)
    request = _parked_request(gate)
    task_id = _seed_blocked_task(home, [request.to_blocked_record()])
    service = _service(home, gate)

    # When: the person allows it
    result = service.resolve_approval(task_id, request.request_id, "allow_once")

    # Then: the typed op ran exactly once through the broker
    assert actions[RESTART_OP].calls == [{"target": "gateway"}]
    assert result["decision"] == "allow_once"
    assert result["resumed_task_id"] == task_id
    assert result["kind"] == "host_op"
    assert result["host_op"]["executed"] is True
    assert result["host_op"]["status"] == "ok"

    # And: the task resumes without any tool-approval metadata
    row = _task_row(task_id)
    metadata = json.loads(row["metadata"])
    assert row["status"] == "queued"
    assert json.loads(row["approval_blocked_tools"]) == []
    assert metadata["mobile_resolved_approvals"][request.request_id] == "allow_once"
    assert metadata["host_op_resolutions"][0]["executed"] is True
    assert metadata["host_op_resolutions"][0]["op"] == RESTART_OP
    assert "forced_tool_call" not in metadata
    assert "approved_tool_calls" not in metadata

    # And: the decision is consumed once - a second attempt cannot re-run the op
    with pytest.raises(MobileConflict):
        service.resolve_approval(task_id, request.request_id, "allow_once")
    assert actions[RESTART_OP].calls == [{"target": "gateway"}]

    # And: the audit trail and the session event stream both record the decision
    assert _audit_entries(trail_path)[1]["resolved_by"] == "mobile_bridge"
    events = service.session_events(SESSION_ID)
    resolved = [event for event in events if event["kind"] == "approval.resolved"]
    assert resolved[-1]["payload"]["decision"] == "allow_once"
    assert resolved[-1]["payload"]["kind"] == "host_op"


def test_resolve_reject_performs_no_host_action_and_is_audited(home: Path, tmp_path: Path) -> None:
    # Given: a task parked on a host-op request
    gate, actions, trail_path = _gate(tmp_path)
    request = _parked_request(gate)
    task_id = _seed_blocked_task(home, [request.to_blocked_record()])
    service = _service(home, gate)

    # When: the person rejects it
    result = service.resolve_approval(task_id, request.request_id, "reject")

    # Then: no host action ran, and the rejection is audited
    assert actions[RESTART_OP].calls == []
    assert result["resumed_task_id"] is None
    assert result["host_op"]["executed"] is False
    assert result["host_op"]["reason"] == "rejected_by_user"
    audit = _audit_entries(trail_path)
    assert [entry["phase"] for entry in audit] == ["decision", "resolution"]
    assert audit[1]["reason"] == "rejected_by_user"

    # And: the task ends exactly like a rejected tool approval, without tool metadata
    row = _task_row(task_id)
    metadata = json.loads(row["metadata"])
    assert row["status"] == "fail"
    assert row["error_message"] == "approval rejected by user"
    assert json.loads(row["approval_blocked_tools"]) == []
    assert metadata["mobile_resolved_approvals"][request.request_id] == "reject"
    assert metadata["last_approval_rejected"] == request.request_id
    assert "forced_tool_call" not in metadata


def test_resolve_allow_session_records_the_grant(home: Path, tmp_path: Path) -> None:
    gate, actions, _trail = _gate(tmp_path)
    request = _parked_request(gate)
    task_id = _seed_blocked_task(home, [request.to_blocked_record()])
    service = _service(home, gate)

    result = service.resolve_approval(task_id, request.request_id, "allow_session")

    assert actions[RESTART_OP].calls == [{"target": "gateway"}]
    assert result["host_op"]["decision"] == "allow_session"
    assert gate.session_granted(RESTART_OP, session_id=request.session_id) is True
    metadata = json.loads(_task_row(task_id)["metadata"])
    assert metadata["host_op_session_grants"] == [RESTART_OP]


def test_disabling_mobile_approvals_never_auto_runs_a_host_op(home: Path, tmp_path: Path) -> None:
    # Given: one parked host op and one parked tool call, both awaiting a decision
    gate, actions, _trail = _gate(tmp_path)
    host_request = _parked_request(gate, seq=21)
    host_task = _seed_blocked_task(home, [host_request.to_blocked_record()])
    tool_task = _seed_blocked_task(
        home, [_tool_blocked_record("call_blocked_2")], updated_at=SEEDED_UPDATED_AT - 10
    )
    service = _service(home, gate)

    # When: mobile approvals are disabled (the existing auto-resume switch)
    result = service.set_approvals_enabled(False)

    # Then: the tool approval auto-resumes exactly as before, but the privileged
    # host op still waits for an explicit decision and no host action runs
    assert result == {"enabled": False, "resumed": 1}
    assert _task_row(tool_task)["status"] == "queued"
    assert _task_row(host_task)["status"] == "waiting_approval"
    assert actions[RESTART_OP].calls == []
    assert gate.pending_ids == (host_request.request_id,)


def test_host_op_without_a_gate_is_visible_but_not_executable(home: Path, tmp_path: Path) -> None:
    # Given: a host-op record but a runtime with no broker wired in
    gate, actions, _trail = _gate(tmp_path)
    request = _parked_request(gate)
    task_id = _seed_blocked_task(home, [request.to_blocked_record()])
    service = _service(home, None)

    # Then: the request stays visible, and resolving it fails closed
    assert [item["kind"] for item in service.list_approvals(session_id=SESSION_ID)] == ["host_op"]
    with pytest.raises(MobileConflict):
        service.resolve_approval(task_id, request.request_id, "allow_once")
    assert actions[RESTART_OP].calls == []


def test_tool_approval_resolution_bypasses_the_host_op_gate(home: Path, tmp_path: Path) -> None:
    # Given: an ordinary blocked tool call and a live host-op gate
    gate, actions, trail_path = _gate(tmp_path)
    task_id = _seed_blocked_task(home, [_tool_blocked_record()])
    service = _service(home, gate)

    approvals = service.list_approvals(session_id=SESSION_ID)
    assert approvals[0]["kind"] == "tool"

    # When: the person allows it, exactly as before this change
    result = service.resolve_approval(task_id, "call_blocked_1", "allow_once")

    # Then: the tool path is byte-identical - signatures and the forced call
    assert result["resumed_task_id"] == task_id
    row = _task_row(task_id)
    metadata = json.loads(row["metadata"])
    assert row["status"] == "queued"
    assert metadata["approved_tool_calls"] == [TOOL_SIGNATURE]
    assert metadata["forced_tool_call"]["name"] == "Bash"
    assert metadata["forced_tool_call"]["input"] == {"command": "rm -rf build"}
    assert "host_op_resolutions" not in metadata

    # And: the host-op broker was never consulted
    assert actions[RESTART_OP].calls == []
    assert _audit_entries(trail_path) == []


# --------------------------------------------------------------------------- #
# TUI prompt: distinct host-op rendering, tool rendering unchanged
# --------------------------------------------------------------------------- #


def test_tui_prompt_distinguishes_a_host_op_request(tmp_path: Path) -> None:
    # Given: a host-op blocked record
    gate, _actions, _trail = _gate(tmp_path)
    request = _parked_request(gate)
    record = request.to_blocked_record()

    # When: the TUI builds its approval prompt
    prompt = _approval_request(record)

    # Then: it is a host-op prompt, labelled with the op and its class
    assert prompt.tool_name == "host-op"
    assert prompt.target == f"{RESTART_OP} [{HostOpApprovalClass.DANGEROUS_OP.value}]"
    assert prompt.reasoning == "host-side restart"
    assert prompt.signature == ""
    assert _blocked_tool_target(record) == f"{RESTART_OP} [{HostOpApprovalClass.DANGEROUS_OP.value}]"

    # And: no tool-style session rule is ever derived from a host op
    assert _blocked_tool_session_rule(record) is None


def test_tui_tool_prompt_is_unchanged() -> None:
    # Given: the exact blocked-tool payload the executor produces
    record = _tool_blocked_record()

    # Then: its prompt keeps the previous shape and signature
    prompt = _approval_request(record)
    assert prompt.tool_name == "Bash"
    assert prompt.target == "rm -rf build"
    assert prompt.signature == TOOL_SIGNATURE
    assert prompt.reasoning == "деструктивная команда"
    assert _blocked_tool_target(record) == "rm -rf build"
    assert _blocked_tool_session_rule(record) == {
        "rule_type": "exact_tool_call",
        "tool": "Bash",
        "signature": TOOL_SIGNATURE,
    }


# --------------------------------------------------------------------------- #
# Runtime wiring (the verifier's re-open): the gate must exist in the REAL path
# --------------------------------------------------------------------------- #


def test_mobile_bridge_builds_the_gate_and_executes_a_host_op_once(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production mobile runtime, not an injected test gate, resolves a host op."""
    # Given: the real mobile bridge runtime with a host-allowed read-only op
    monkeypatch.setenv(ENV_HOST_OPS_ALLOW, STATUS_OP)
    bridge = MobileBridge(
        config=BridgeConfig(profile="cc-local-8081", workdir=str(home))
    )
    gate = bridge.service._host_op_gate
    assert gate is not None

    # And: one host-op request parked through that runtime's own broker
    request = _parked_request(gate, op=STATUS_OP, args={}, seq=11)
    task_id = _seed_blocked_task(home, [request.to_blocked_record()])

    # When: the person allows it through the real MobileService
    result = bridge.service.resolve_approval(task_id, request.request_id, "allow_once")

    # Then: the typed op ran exactly once through the todo-19/20 broker
    assert result["kind"] == "host_op"
    assert result["host_op"]["executed"] is True
    assert result["host_op"]["status"] == "ok"
    audit = _audit_entries(host_op_runtime.host_op_audit_path())
    assert [entry["phase"] for entry in audit] == [
        "decision",
        "resolution",
        "decision",
        "outcome",
    ]
    assert audit[1]["resolved_by"] == "mobile_bridge"
    assert audit[3]["op"] == STATUS_OP
    assert audit[3]["executed"] is True

    # And: the same decision cannot execute it a second time
    with pytest.raises(MobileConflict):
        bridge.service.resolve_approval(task_id, request.request_id, "allow_once")
    assert len(_audit_entries(host_op_runtime.host_op_audit_path())) == 4
    assert gate.pending_ids == ()


def test_tui_pending_broker_is_consumed_on_resume_exactly_once(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TUI handoff record executes on resume, and only once across restarts."""
    # Given: a request parked by the host, then approved in the TUI (no broker there)
    monkeypatch.setenv(ENV_HOST_OPS_ALLOW, STATUS_OP)
    parking = host_op_runtime.build_host_op_runtime()
    assert parking is not None
    handoff = _pending_handoff(parking.gate, op=STATUS_OP, decision="allow_once", seq=13)
    task_id = _seed_resumed_task(home, {"host_op_resolutions": [handoff]})

    # When: the queued resume reaches the host runtime consumer
    consumption = host_op_resume.consume_pending_host_op_resolutions(task_id=task_id)

    # Then: the approved op ran once and the consumed record is no longer pending
    assert consumption.refused_reason == ""
    assert [item["status"] for item in consumption.executed] == ["ok"]
    assert consumption.executed[0]["op"] == STATUS_OP
    assert consumption.executed[0]["executed"] is True
    assert _task_metadata(task_id)["host_op_resolutions"][0]["status"] == "ok"
    audit = _audit_entries(host_op_runtime.host_op_audit_path())
    assert [entry["phase"] for entry in audit] == [
        "decision",
        "resolution",
        "decision",
        "outcome",
    ]

    # And: a second resume (a restarted process) cannot run it again
    again = host_op_resume.consume_pending_host_op_resolutions(task_id=task_id)
    assert again.executed == ()
    assert len(_audit_entries(host_op_runtime.host_op_audit_path())) == 4


def test_allow_session_makes_a_later_same_op_request_not_park(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored session grant is consumed via ``session_granted()`` by the park path."""
    # Given: an allow_session decision the host persisted on the resumed task
    monkeypatch.setenv(ENV_HOST_OPS_ALLOW, STATUS_OP)
    parking = host_op_runtime.build_host_op_runtime()
    assert parking is not None
    _parked_request(parking.gate, op=STATUS_OP, args={}, seq=21)
    task_id = _seed_resumed_task(home, {"host_op_session_grants": [STATUS_OP]})

    # When: the host runtime consumes the grants
    runtime = host_op_runtime.build_host_op_runtime()
    assert runtime is not None
    consumption = host_op_resume.consume_pending_host_op_resolutions(
        task_id=task_id, runtime=runtime
    )

    # Then: the op is session-granted and a later same-op request does not park
    assert consumption.refused_reason == ""
    assert consumption.executed == ()
    assert runtime.gate.session_granted(STATUS_OP, session_id=SESSION_ID) is True
    assert runtime.gate.session_granted(STATUS_OP, session_id="other-session") is False
    later = runtime.handle_request(
        _host_op_message(STATUS_OP, {}, seq=22),
        task_id="task-22b",
        session_id=SESSION_ID,
    )
    assert later.parked_request is None
    assert later.response is not None
    assert later.response.status is HostOpStatus.OK

    # And: without the grant the very same request still parks for a decision
    ungranted = host_op_runtime.build_host_op_runtime()
    assert ungranted is not None
    parked = ungranted.handle_request(
        _host_op_message(STATUS_OP, {}, seq=23),
        task_id="task-22c",
        session_id=SESSION_ID,
    )
    assert parked.parked_request is not None
    assert parked.response is not None
    assert parked.response.status is HostOpStatus.APPROVAL_REQUIRED


def test_unresolvable_host_op_yields_zero_host_actions(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forged or malformed handoff never reaches a host action; no gate, no run."""
    # Given: a well-formed record this host never parked, plus a malformed one
    monkeypatch.setenv(ENV_HOST_OPS_ALLOW, STATUS_OP)
    parking = host_op_runtime.build_host_op_runtime()
    assert parking is not None
    handoff = _pending_handoff(parking.gate, op=STATUS_OP, decision="allow_once", seq=31)
    forged = {**handoff, "request_id": "host-op:forged:1"}
    malformed = {**forged, "request_id": "host-op:forged:2", "op": "shell rm -rf /"}
    task_id = _seed_resumed_task(home, {"host_op_resolutions": [forged, malformed]})

    # When: the resume consumer tries to execute them
    before = _audit_entries(host_op_runtime.host_op_audit_path())
    consumption = host_op_resume.consume_pending_host_op_resolutions(task_id=task_id)

    # Then: nothing executed, and no new audit entry appeared
    assert consumption.executed == ()
    assert _audit_entries(host_op_runtime.host_op_audit_path()) == before
    records = _task_metadata(task_id)["host_op_resolutions"]
    assert [item["status"] for item in records] == ["refused", "refused"]
    assert all(item["executed"] is False for item in records)

    # And: a runtime without a usable gate refuses with a clear reason, zero actions
    second = _seed_resumed_task(home, {"host_op_resolutions": [handoff]})
    monkeypatch.setattr(
        host_op_resume, "build_host_op_runtime", lambda **_kwargs: None
    )
    refused = host_op_resume.consume_pending_host_op_resolutions(task_id=second)
    assert refused.refused_reason == host_op_resume.REASON_GATE_UNAVAILABLE
    assert refused.executed == ()
    assert _audit_entries(host_op_runtime.host_op_audit_path()) == before
    assert _task_metadata(second)["host_op_resolutions"][0]["status"] == "pending_broker"
