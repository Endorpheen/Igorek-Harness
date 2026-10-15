#!/usr/bin/env python3
"""TDD suite for the host-side guest-trace relay (plan todo 8).

Proves that guest ``event`` frames become the SAME normalized mobile event
stream an off-mode run produces for the same logical run:

* golden/parity - a recorded guest frame sequence crosses the real channel
  policy and the host relay into ``<artifact_workdir>/cc_backend_trace.jsonl``;
  the existing :class:`TraceJournaler` then yields the identical
  ``(kind, seq, payload)`` stream as the same records written the off-mode way;
* the host is the sole writer - the trace file appears only because the relay
  appended to it, exactly once per accepted frame, in frame order;
* resilience - an event frame the relay cannot interpret is skipped with a
  warning, never raising into the awaiting host loop, and the frames after it
  are still relayed;
* payload policy - the channel's inert display envelope (the top-level
  ``record`` of an ``event`` frame) lets faithful tool input (``command``,
  ``argv``) cross, while the same keys outside the envelope and whole-value
  host paths inside it stay denied.

Adversarial classes: stale state (a skipped frame must not corrupt the events
after it), misleading success (exact kinds/order/payloads, never truthiness),
dirty worktree (this suite writes only into pytest temp dirs).

Runs without KVM: the channel seam is an in-process ``socketpair`` and the
guest frame sequence is a recorded fixture, so the suite needs no hypervisor.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest

from factory.core.cc_backend_trace import TRACE_FILENAME, append_cc_backend_trace
from factory.core.isolation import guest_runner
from factory.core.isolation.channel import (
    DISPLAY_ENVELOPE_KEY,
    TRACE_EVENT_KIND,
    ChannelConfig,
    ChannelError,
    ChannelMessage,
    JsonValue,
    MessageKind,
    PeerCid,
    PeerIdentity,
    PeerSession,
    SessionId,
    SessionMap,
)
from factory.core.isolation.config import MODE_KVM, HostCapabilities, IsolationConfig
from factory.core.isolation.domain import CommandResult
from factory.core.isolation.manager import IsolationManager, ManagerSeams
from factory.core.isolation.preflight import REASON_OK, PreflightResult
from factory.core.isolation.relay import TraceRelay
from factory.core.isolation.workspace import WorkspacePolicy
from factory.core.mobile_bridge.events import TraceJournaler, trace_path_for_task
from factory.core.mobile_bridge.store import MobileStore
from factory.core.schema import initialize_database

CONFIG = ChannelConfig(max_frame_bytes=1_000_000)
TASK_ID = "8c0ffee0-1111-2222-3333-444444444444"
SESSION_ID = "chat-20260922-000000-task8"

# One recorded guest run: the exact records the in-guest executor appends to its
# trace file for a Write-style turn that ends with an approval-blocked command.
RECORDED_GUEST_RUN: list[dict[str, Any]] = [
    {
        "ts": 1_000,
        "kind": "task.start",
        "model": "glm-5.3-flash",
        "filesystem_mode": "workspace",
        "current_session": SESSION_ID,
        "round": 0,
    },
    {"ts": 1_001, "kind": "model.reasoning_delta", "round": 1, "text": "Нужно записать файл. "},
    {"ts": 1_002, "kind": "model.text_delta", "round": 1, "text": "Записываю"},
    {
        "ts": 1_003,
        "kind": "model.round_response",
        "round": 1,
        "text_full": "Записываю файл.",
        "usage": {"prompt": 10, "completion": 5},
        "latency_ms": 120,
        "content_types": ["text"],
    },
    {
        "ts": 1_004,
        "kind": "tool.request_batch",
        "round": 1,
        "tools": [
            {
                "id": "call_1",
                "name": "Bash",
                "input": {"command": "printf 'hi' > /workspace/out.txt", "argv": ["printf", "hi"]},
            }
        ],
    },
    {
        "ts": 1_005,
        "kind": "tool.result",
        "round": 1,
        "tool_use_id": "call_1",
        "tool_name": "Bash",
        "tool_input": {"command": "printf 'hi' > /workspace/out.txt"},
        "raw_result_preview": "written (exit code: 0)",
        "raw_result_len": 21,
        "duration_ms": 42,
    },
    {
        "ts": 1_006,
        "kind": "tool.approval_required",
        "round": 2,
        "tool_use_id": "call_2",
        "tool": "Bash",
        "input": {"command": "rm -rf /workspace/tmp"},
        "target": "rm -rf /workspace/tmp",
        "signature": 'Bash:{"command":"rm -rf /workspace/tmp"}',
        "session_rule": {"rule_type": "exact_tool_call", "tool": "Bash", "signature": "Bash"},
        "reason": "destructive",
    },
    {
        "ts": 1_007,
        "kind": "task.finish",
        "status": "ok",
        "reason": "",
        "rounds": 2,
        "tool_calls_count": 2,
        "output_preview": "Готово.",
        "approval_blocked_tools": [],
    },
]

GOLDEN_JOURNAL_KINDS = [
    "task.start",
    "model.reasoning",
    "model.round_response",
    "tool.request_batch",
    "tool.result",
    "approval.required",
    "task.finish",
    "task.status",
]
GOLDEN_LIVE_KINDS = [
    "task.start",
    "model.reasoning_delta",
    "model.reasoning",
    "model.text_delta",
    "model.round_response",
    "tool.request_batch",
    "tool.result",
    "approval.required",
    "task.finish",
    "task.status",
]


def _pair() -> tuple[PeerSession, PeerSession]:
    """Two validated endpoints over one socketpair (the transport seam)."""
    left, right = socket.socketpair()
    return PeerSession(left, CONFIG, PeerIdentity()), PeerSession(right, CONFIG, PeerIdentity())


def _event_payload(record: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """The wire shape the guest runner sends for one trace record."""
    return {"kind": TRACE_EVENT_KIND, DISPLAY_ENVELOPE_KEY: dict(record)}


def _relay_through_channel(records: Iterable[dict[str, Any]], relay: TraceRelay) -> None:
    """Send every record as a real guest frame, then relay what the host read."""
    host, guest = _pair()
    try:
        for index, record in enumerate(records, start=1):
            guest.send(MessageKind.EVENT, _event_payload(record))
            message = host.read(timeout_s=2.0)
            assert message is not None
            assert message.seq == index
            assert relay.handle_event(message) is True
    finally:
        host.close()
        guest.close()


def _write_off_mode_trace(records: Iterable[dict[str, Any]], trace_path: Path) -> None:
    """Write the trace exactly as the off-mode executor does."""
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    for record in records:
        append_cc_backend_trace(str(trace_path.parent), record)


def _run_journal(root: Path, db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Poll the real mobile pipeline over one trace file; returns journal + live stream."""
    initialize_database(str(db_path))
    store = MobileStore(db_path=str(db_path))
    live: list[dict[str, Any]] = []
    task: dict[str, Any] = {
        "task_id": TASK_ID,
        "status": "processing",
        "metadata": {"session": SESSION_ID},
        "created_at": 0,
    }
    journaler = TraceJournaler(store, workspace_root=root, active_task_provider=lambda: [task])
    journaler.add_live_hook(live.append)
    journaler.poll()
    task["status"] = "waiting_approval"
    journaler.poll()
    return store.list_events(limit=500), live


def test_recorded_guest_frames_produce_the_identical_mobile_stream_as_off_mode(tmp_path: Path) -> None:
    # Given: the same logical run recorded once per mode
    off_root = tmp_path / "off"
    kvm_root = tmp_path / "kvm"
    off_trace = trace_path_for_task(TASK_ID, off_root)
    _write_off_mode_trace(RECORDED_GUEST_RUN, off_trace)
    kvm_trace = trace_path_for_task(TASK_ID, kvm_root)
    relay = TraceRelay(str(kvm_trace.parent))

    # When: the guest frames cross the real channel policy into the host relay
    _relay_through_channel(RECORDED_GUEST_RUN, relay)

    # Then: the host trace is byte-identical to the off-mode executor's file
    assert kvm_trace.read_text(encoding="utf-8") == off_trace.read_text(encoding="utf-8")

    # When: the existing mobile pipeline polls both files
    off_events, off_live = _run_journal(off_root, tmp_path / "off.db")
    kvm_events, kvm_live = _run_journal(kvm_root, tmp_path / "kvm.db")

    # Then: identical normalized mobile events: same kinds, same seq order, same payloads
    assert [event["kind"] for event in kvm_events] == GOLDEN_JOURNAL_KINDS
    assert [(event["kind"], event["seq"]) for event in kvm_events] == [
        (event["kind"], event["seq"]) for event in off_events
    ]
    assert [(event["kind"], event["payload"]) for event in kvm_events] == [
        (event["kind"], event["payload"]) for event in off_events
    ]
    # And: the live/SSE stream (deltas included) matches in kind and order too
    assert [event["kind"] for event in kvm_live] == GOLDEN_LIVE_KINDS
    assert [event["kind"] for event in kvm_live] == [event["kind"] for event in off_live]


def test_the_relay_is_the_only_writer_and_appends_verbatim_in_order(tmp_path: Path) -> None:
    # Given: guest frames sent over the channel before any host-side relay runs
    trace = trace_path_for_task(TASK_ID, tmp_path)
    relay = TraceRelay(str(trace.parent))
    records = RECORDED_GUEST_RUN[:3]
    host, guest = _pair()
    try:
        for record in records:
            guest.send(MessageKind.EVENT, _event_payload(record))
        # Then: the guest half alone cannot create or write the host trace file
        assert not trace.exists()

        # When: the host relays each frame it reads
        for expected_seq, _record in enumerate(records, start=1):
            message = host.read(timeout_s=2.0)
            assert message is not None and message.seq == expected_seq
            assert relay.handle_event(message) is True
    finally:
        host.close()
        guest.close()

    # Then: exactly one appended line per accepted frame, in frame order, faithfully
    assert relay.written == len(records)
    lines = trace.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"ts": record["ts"], **record} for record in records]


def test_unknown_event_kind_is_skipped_with_a_warning_and_the_stream_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Given: a relay and a frame sequence with two uninterpretable frames between valid ones
    trace = trace_path_for_task(TASK_ID, tmp_path)
    relay = TraceRelay(str(trace.parent))
    frames = [
        ChannelMessage(
            kind=MessageKind.EVENT,
            seq=1,
            payload=_event_payload({"ts": 1, "kind": "task.start", "current_session": SESSION_ID}),
        ),
        ChannelMessage(
            kind=MessageKind.EVENT,
            seq=2,
            payload={"kind": "telemetry", DISPLAY_ENVELOPE_KEY: {"kind": "task.start"}},
        ),
        ChannelMessage(
            kind=MessageKind.EVENT,
            seq=3,
            payload={"kind": TRACE_EVENT_KIND, DISPLAY_ENVELOPE_KEY: {"kind": "not a trace kind"}},
        ),
        ChannelMessage(
            kind=MessageKind.EVENT,
            seq=4,
            payload=_event_payload(
                {
                    "ts": 4,
                    "kind": "tool.result",
                    "round": 1,
                    "tool_use_id": "call_1",
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo hi"},
                    "raw_result_preview": "ok (exit code: 0)",
                    "raw_result_len": 18,
                    "duration_ms": 2,
                }
            ),
        ),
    ]

    # When: the relay handles every frame
    with caplog.at_level(logging.WARNING):
        outcomes = [relay.handle_event(frame) for frame in frames]

    # Then: only the valid frames are written, each skip is warned, nothing raises
    assert outcomes == [True, False, False, True]
    assert relay.written == 2
    assert "unknown kind" in caplog.text
    assert "invalid kind" in caplog.text

    # Then: the frames after a skip are intact in the pipeline (no stale/corrupt state)
    events, _live = _run_journal(tmp_path, tmp_path / "events.db")
    assert [event["kind"] for event in events] == ["task.start", "tool.result", "task.status"]


def test_display_envelope_carries_tool_input_with_denied_shell_keys_across_the_channel() -> None:
    # Given: a faithful tool-call record whose input uses the denied shell keys
    record: dict[str, JsonValue] = {
        "kind": "tool.request_batch",
        "round": 1,
        "tools": [
            {
                "id": "call_1",
                "name": "Bash",
                "input": {"command": "rm -rf /workspace/tmp", "argv": ["rm", "-rf"]},
            }
        ],
    }
    payload = _event_payload(record)
    host, guest = _pair()
    try:
        # When: the guest sends it as a real event frame
        guest.send(MessageKind.EVENT, payload)
        inbound = host.read(timeout_s=2.0)
    finally:
        host.close()
        guest.close()

    # Then: the inert display envelope crosses unchanged, in both directions
    assert inbound is not None
    assert dict(inbound.payload) == payload


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"kind": TRACE_EVENT_KIND, "command": "rm -rf /"}, "payload_shell_key_denied"),
        (
            {"kind": TRACE_EVENT_KIND, DISPLAY_ENVELOPE_KEY: {"kind": "task.finish"}, "argv": ["sh", "-c"]},
            "payload_shell_key_denied",
        ),
        (
            {
                "kind": TRACE_EVENT_KIND,
                DISPLAY_ENVELOPE_KEY: {"kind": "tool.result", "raw_result_preview": "/home/example/secret"},
            },
            "payload_host_path_denied",
        ),
        (
            {
                "kind": TRACE_EVENT_KIND,
                DISPLAY_ENVELOPE_KEY: {"kind": "tool.result", "raw_result_preview": "~/secret"},
            },
            "payload_host_path_denied",
        ),
    ],
    ids=["bare-command", "bare-argv", "host-path-inside-envelope", "home-path-inside-envelope"],
)
def test_shell_keys_outside_and_host_paths_inside_the_envelope_stay_denied(
    payload: dict[str, JsonValue], reason: str
) -> None:
    # Given: a connected endpoint and a violating payload
    host, guest = _pair()
    try:
        # When/Then: the channel refuses it with a stable reason code
        with pytest.raises(ChannelError) as caught:
            host.send(MessageKind.EVENT, payload)
        assert caught.value.reason == reason
    finally:
        host.close()
        guest.close()


def test_the_envelope_is_scoped_to_event_frames() -> None:
    # Given: the same display record under a non-event message kind
    host, guest = _pair()
    try:
        # When/Then: the relaxation does not apply outside the event stream
        with pytest.raises(ChannelError) as caught:
            host.send(
                MessageKind.TASK,
                {DISPLAY_ENVELOPE_KEY: {"kind": "tool.result", "tool_input": {"command": "id"}}},
            )
        assert caught.value.reason == "payload_shell_key_denied"
    finally:
        host.close()
        guest.close()


def test_guest_wire_markers_match_the_channel_display_envelope() -> None:
    # Given/When/Then: the guest producer and the host policy share one wire contract
    assert guest_runner.TRACE_EVENT_KIND == TRACE_EVENT_KIND
    assert guest_runner.TRACE_RECORD_KEY == DISPLAY_ENVELOPE_KEY


# --------------------------------------------------------------------------- #
# Manager wiring: EVENT frames reach the trace file the mobile pipeline polls
# --------------------------------------------------------------------------- #


class _FakeDomain:
    """Minimal ``libvirt.virDomain`` stand-in for the manager wiring test."""

    def __init__(self, name: str, cid: int) -> None:
        self._name = name
        self._cid = cid
        self.active = False

    def name(self) -> str:
        return self._name

    def create(self) -> int:
        self.active = True
        return 0

    def destroy(self) -> int:
        self.active = False
        return 0

    def shutdown(self) -> int:
        return 0

    def undefine(self) -> int:
        return 0

    def isActive(self) -> int:
        return 1 if self.active else 0

    def XMLDesc(self, flags: int = 0) -> str:
        return (
            "<domain><devices><vsock model='virtio'>"
            f"<cid auto='yes' address='{self._cid}'/>"
            "</vsock></devices></domain>"
        )


class _FakeConnection:
    """Fake libvirt connection: remembers defined domains by their XML name."""

    def __init__(self) -> None:
        self.defined: dict[str, _FakeDomain] = {}

    def defineXML(self, xml: str) -> _FakeDomain:
        name = ET.fromstring(xml).findtext("name") or ""
        handle = _FakeDomain(name, cid=7 + len(self.defined))
        self.defined[name] = handle
        return handle

    def lookupByName(self, name: str) -> _FakeDomain:
        return self.defined[name]

    def close(self) -> None:
        return None


class _FakeEndpoint:
    """Fake host half of one guest connection (``PeerSession`` stand-in)."""

    def __init__(self, messages: list[ChannelMessage]) -> None:
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []
        self._messages = list(messages)

    def send(self, kind: MessageKind, payload: Mapping[str, JsonValue]) -> None:
        self.sent.append((kind, dict(payload)))

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        return None


class _FakeRegistry:
    """Fake channel registry keyed by the host-owned session id."""

    def __init__(self, endpoints: dict[str, _FakeEndpoint]) -> None:
        self.endpoints = dict(endpoints)

    def session_for(self, session_id: SessionId) -> _FakeEndpoint | None:
        return self.endpoints.get(str(session_id))

    def close(self) -> None:
        return None


def _enabled_config(root: Path) -> IsolationConfig:
    base = root / "base.qcow2"
    base.write_bytes(b"qcow2-placeholder")
    config = IsolationConfig.from_env(
        {
            "IGOREK_ISOLATION_MODE": MODE_KVM,
            "IGOREK_ISOLATION_BASE_IMAGE": str(base),
            "IGOREK_ISOLATION_OVERLAY_DIR": str(root / "overlays"),
            "IGOREK_ISOLATION_VSOCK_PORT": "5005",
        },
        data_root=root,
        capability_probe=lambda uri: HostCapabilities(True, True, True, True),
    )
    assert config.enabled, config.reason
    return config


def _build_manager(root: Path, registry: _FakeRegistry) -> IsolationManager:
    connection = _FakeConnection()
    seams = ManagerSeams(
        connect=lambda uri: connection,
        runner=lambda argv: CommandResult(0),
        channel=registry,
        session_map=SessionMap(),
        workspace_policy=WorkspacePolicy(home=root / "home", data_root=root / "data", host_uid=os.getuid()),
        preflight_fn=lambda config: PreflightResult(ok=True, mode=config.mode, reason=REASON_OK),
    )
    return IsolationManager(_enabled_config(root), seams=seams)


def _dispatch_payload(artifact_workdir: str) -> dict[str, Any]:
    return {
        "task_id": TASK_ID,
        "profile": "cc-test",
        "model": "test-model",
        "base_url": "http://127.0.0.1:8001",
        "api_format": "anthropic",
        "system_prompt": "system",
        "user_message": "user",
        "skills_text": "skills",
        "foundation_context": "foundation",
        "filesystem_mode": "workspace",
        "guard_mode": "on",
        "max_tokens": 4000,
        "timeout_s": 1200,
        "trailing_context_text": "",
        "current_session": SESSION_ID,
        "artifact_workdir": artifact_workdir,
    }


def test_run_task_relays_guest_event_frames_into_the_host_trace_file(tmp_path: Path) -> None:
    # Given: a kvm-enabled manager whose guest answers with one event and a result
    root = tmp_path / "root"
    root.mkdir()
    workspace = root / "workspace"
    workspace.mkdir()
    artifact = tmp_path / "host-artifacts"
    record: dict[str, Any] = {
        "ts": 5,
        "kind": "tool.request_batch",
        "round": 1,
        "tools": [{"id": "call_1", "name": "Bash", "input": {"command": "echo hi"}}],
    }
    endpoint = _FakeEndpoint(
        [
            ChannelMessage(kind=MessageKind.EVENT, seq=1, payload=_event_payload(record)),
            ChannelMessage(
                kind=MessageKind.RESULT,
                seq=2,
                payload={"status": "ok", "output_text": "done", "usage": {}, "tool_calls_count": 1},
            ),
        ]
    )
    manager = _build_manager(root, _FakeRegistry({SESSION_ID: endpoint}))

    # When: the task is dispatched to the session VM
    result = manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(workspace),
        _dispatch_payload(str(artifact)),
    )

    # Then: the result is normalized and the guest frame landed in the host trace file
    assert result["status"] == "ok"
    lines = (artifact / TRACE_FILENAME).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"ts": record["ts"], **record}]
    assert endpoint.sent[0][0] is MessageKind.TASK
