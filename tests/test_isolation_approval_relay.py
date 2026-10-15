#!/usr/bin/env python3
"""TDD suite for the approval relay across the VM boundary (plan todo 9).

The guest never approves its own blocked tool. The in-guest loop stops with the
executor's own ``approval_required`` result; the runner forwards the executor's
blocked-tool payload as an inert ``approval_request`` envelope, and the host:

* **parks** the task - ``waiting_approval`` only for ``source_platform ==
  "android"`` and ``fail`` otherwise (the exact ``cc_backend_executor`` rule for
  an off-mode run) - and stops the session VM, so no domain is held open while a
  human decides;
* **canonicalizes** every guest path (``/workspace/...``) in the payload to its
  host path at this boundary, because the mobile decision stores the signature
  the host will later match against ``approved_tool_calls``;
* **resumes** through the existing mobile requeue: the host respawns the session
  VM and re-dispatches the same task with ``forced_tool_call`` +
  ``approved_tool_calls`` mapped back to guest paths, so the signature the guest
  recomputes equals the approved one (otherwise the agent re-prompts forever);
* **consumes** a decision for one ``tool_use_id`` exactly once - a duplicated
  decision or a replayed approval request changes nothing.

Adversarial classes covered here:

* misleading success - exact statuses/reasons and exact payload equality, never
  truthiness; a guest claiming ``waiting_approval`` for a non-android task is
  forced to ``fail`` by the host;
* stale/deadlock - a duplicated decision is consumed once, a guest that dies
  right after the approval request still parks, and a new identical call after
  ``allow_once`` re-asks instead of looping on a dropped approval;
* dirty worktree - this suite writes only into pytest temp dirs.

Runs without KVM: the channel seam is an in-process ``socketpair``, the manager's
libvirt connection is a fake, and the mobile decision path uses a temp database.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from factory.core.cc_backend_executor import (
    _approval_signature,
    _approved_tool_call,
    _blocked_tool_payload,
    _consume_approved_tool_call,
)
from factory.core.isolation import guest_runner as guest_runner_module
from factory.core.isolation.approval import (
    ApprovalRelay,
    WorkspacePathMapping,
    approval_wait_status,
    canonicalize_blocked_tools,
    resume_wire_metadata,
)
from factory.core.isolation.channel import (
    DISPLAY_ENVELOPE_KEY,
    ChannelConfig,
    ChannelError,
    ChannelMessage,
    ChannelServer,
    JsonValue,
    MessageKind,
    PeerCid,
    PeerIdentity,
    PeerSession,
    SessionId,
    SessionMap,
    guest_connect,
)
from factory.core.isolation.config import MODE_KVM, HostCapabilities, IsolationConfig
from factory.core.isolation.domain import CommandResult, DomainSpec, domain_name
from factory.core.isolation.guest_runner import GuestRunner, RunnerSeams, RunnerTuning
from factory.core.isolation.egress_enforce import ApplyResult
from factory.core.isolation.guest_task import approval_resume_metadata
from factory.core.isolation.manager import IsolationManager, ManagerSeams, SessionSpec
from factory.core.isolation.preflight import REASON_OK, PreflightResult
from factory.core.isolation.workspace import WorkspacePolicy
from factory.core.mobile_bridge.service import MobileConflict, MobileInvalid, MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database



def _fake_apply_egress(ruleset: str) -> ApplyResult:
    """Egress enforcement is covered by tests/test_isolation_egress_enforce.py."""
    return ApplyResult(
        applied=True,
        verified=True,
        table="inet igorek_egress",
        ruleset_sha256="0" * 64,
        rule_count=1,
        listed_rules=1,
    )

CONFIG = ChannelConfig(max_frame_bytes=1_000_000)
TASK_ID = "9b00c0de-1111-2222-3333-444444444444"
SESSION_ID = "chat-20260922-000000-task9"
BLOCKED_TOOL_USE_ID = "call_blocked_1"
SECOND_TOOL_USE_ID = "call_blocked_2"
FAKE_CID = 51
GUEST_WORKSPACE_FILE = "/workspace/src/app.py"


# --------------------------------------------------------------------------- #
# The exact payload shapes the executor produces (never re-invented here)
# --------------------------------------------------------------------------- #


def _blocked_write(
    file_path: str = GUEST_WORKSPACE_FILE, tool_use_id: str = BLOCKED_TOOL_USE_ID
) -> dict[str, Any]:
    blocked = _blocked_tool_payload(
        "Write",
        {"file_path": file_path, "content": "print('hi')\n", "reason": "structural file artifact"},
    )
    blocked["tool_use_id"] = tool_use_id
    return blocked


def _blocked_bash(
    command: str = "rm -rf /workspace/build", tool_use_id: str = BLOCKED_TOOL_USE_ID
) -> dict[str, Any]:
    blocked = _blocked_tool_payload(
        "Bash", {"command": command, "reason": "destructive command"}
    )
    blocked["tool_use_id"] = tool_use_id
    return blocked


def _approval_frame(blocked: Mapping[str, JsonValue], seq: int) -> ChannelMessage:
    """One guest ``approval_request`` frame carrying the executor payload."""
    return ChannelMessage(
        kind=MessageKind.APPROVAL_REQUEST,
        seq=seq,
        payload={DISPLAY_ENVELOPE_KEY: dict(blocked)},
    )


def _result_frame(
    seq: int, status: str, blocked: list[dict[str, Any]] | None = None
) -> ChannelMessage:
    payload: dict[str, JsonValue] = {
        "status": status,
        "output_text": "Для выполнения этого действия требуется подтверждение.",
        "usage": {"prompt": 1, "completion": 1},
        "tool_calls_count": 1,
    }
    if blocked is not None:
        payload["approval_blocked_tools"] = [dict(item) for item in blocked]
    return ChannelMessage(kind=MessageKind.RESULT, seq=seq, payload=payload)


def _mapping(workspace: Path) -> WorkspacePathMapping:
    return WorkspacePathMapping(host_root=os.path.realpath(str(workspace)))


def _host_path(workspace: Path, guest_path: str) -> str:
    mapping = _mapping(workspace)
    return mapping.guest_to_host(guest_path)


# --------------------------------------------------------------------------- #
# Guest harness: a real GuestRunner over a real (socketpair) vsock channel
# --------------------------------------------------------------------------- #

GUEST_TERMINAL_KINDS = (MessageKind.RESULT, MessageKind.ERROR)


@dataclass
class _GuestHarness:
    host: PeerSession
    thread: threading.Thread
    errors: list[BaseException]
    exit_codes: list[int]

    def collect(self, timeout_s: float = 5.0) -> list[ChannelMessage]:
        messages: list[ChannelMessage] = []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            message = self.host.recv_from_guest(
                timeout_s=max(0.05, deadline - time.monotonic())
            )
            if message is None:
                return messages
            messages.append(message)
            if message.kind in GUEST_TERMINAL_KINDS:
                return messages
        return messages

    def finish(self) -> None:
        self.host.close()
        self.thread.join(timeout=5.0)
        assert not self.thread.is_alive()
        assert self.errors == []


def _guest_task_payload(tmp_path: Path, **overrides: JsonValue) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "payload_version": 1,
        "task_id": "task-9",
        "profile": "cc-wally",
        "model": "glm-5.3-flash",
        "base_url": "https://inference.runanywhere.ai/v1",
        "api_format": "openai",
        "system_prompt": "You are Igorek.",
        "user_message": "clean the build dir",
        "skills_text": "",
        "foundation_context": "",
        "filesystem_mode": "workspace",
        "guard_mode": "on",
        "max_tokens": 512,
        "timeout_s": 30,
        "workdir": str(tmp_path),
        "artifact_workdir": str(tmp_path / "artifacts"),
        "trailing_context_text": "",
        "source_platform": "android",
    }
    payload.update(overrides)
    return payload


def _start_guest(executor: Callable[..., dict[str, Any]], **tuning: Any) -> _GuestHarness:
    """Run a guest endpoint in a thread; return the host end of the channel."""
    cid = PeerCid(FAKE_CID)
    sessions = SessionMap()
    sessions.assign(cid, SessionId(SESSION_ID))
    server = ChannelServer(CONFIG, sessions)
    srv_sock, cli_sock = socket.socketpair()
    errors: list[BaseException] = []
    exit_codes: list[int] = []

    def guest() -> None:
        try:
            session = guest_connect(cli_sock, CONFIG)
            runner = GuestRunner(
                session,
                seams=RunnerSeams(executor=executor, apply_egress=_fake_apply_egress),
                tuning=RunnerTuning(guest_roots=CONFIG.guest_roots, **tuning),
            )
            try:
                exit_codes.append(runner.serve_one())
            finally:
                session.close()
        except BaseException as exc:  # reported to the test
            errors.append(exc)

    thread = threading.Thread(target=guest, daemon=True)
    thread.start()
    host = server.handle_connection(srv_sock, cid)
    assert host is not None
    return _GuestHarness(host=host, thread=thread, errors=errors, exit_codes=exit_codes)


def _pair() -> tuple[PeerSession, PeerSession]:
    left, right = socket.socketpair()
    return PeerSession(left, CONFIG, PeerIdentity()), PeerSession(right, CONFIG, PeerIdentity())


# --------------------------------------------------------------------------- #
# Host manager fakes (no hypervisor)
# --------------------------------------------------------------------------- #


class _FakeDomainHandle:
    def __init__(self, name: str, cid: int) -> None:
        self._name = name
        self._cid = cid
        self.active = False
        self.create_calls = 0
        self.destroy_calls = 0
        self.undefine_calls = 0

    def name(self) -> str:
        return self._name

    def create(self) -> int:
        self.create_calls += 1
        self.active = True
        return 0

    def destroy(self) -> int:
        self.destroy_calls += 1
        self.active = False
        return 0

    def shutdown(self) -> int:
        return 0

    def undefine(self) -> int:
        self.undefine_calls += 1
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
    def __init__(self) -> None:
        self.defined: dict[str, _FakeDomainHandle] = {}
        self.define_calls: list[str] = []

    def defineXML(self, xml: str) -> _FakeDomainHandle:
        name = ET.fromstring(xml).findtext("name") or ""
        self.define_calls.append(name)
        handle = _FakeDomainHandle(name, cid=FAKE_CID + len(self.define_calls))
        self.defined[name] = handle
        return handle

    def lookupByName(self, name: str) -> _FakeDomainHandle:
        return self.defined[name]

    def close(self) -> None:
        return None


class _FakeEndpoint:
    def __init__(self, messages: list[ChannelMessage] | None = None) -> None:
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []
        self._messages = list(messages or [])
        self.closed = False

    def send(self, kind: MessageKind, payload: Mapping[str, JsonValue]) -> None:
        self.sent.append((kind, dict(payload)))

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        self.closed = True


class _FakeRegistry:
    def __init__(self) -> None:
        self.endpoints: dict[str, _FakeEndpoint] = {}

    def session_for(self, session_id: SessionId) -> _FakeEndpoint | None:
        return self.endpoints.get(str(session_id))

    def close(self) -> None:
        return None


@dataclass
class _ManagerHarness:
    manager: IsolationManager
    connection: _FakeConnection
    registry: _FakeRegistry
    session_map: SessionMap
    root: Path
    workspace: Path

    def domain(self, session_id: str = SESSION_ID) -> _FakeDomainHandle:
        spec = DomainSpec(
            session_id=session_id,
            base_image=self.root / "base.qcow2",
            workspace_host_path=self.workspace,
            overlay_dir=self.root / "overlays",
        )
        return self.connection.defined[domain_name(spec)]

    def session(self, session_id: str = SESSION_ID) -> SessionSpec:
        return SessionSpec(session_id=session_id, workspace_cwd=str(self.workspace))


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


def _manager_harness(tmp_path: Path, *, guest_stall_timeout_s: float | None = None) -> _ManagerHarness:
    root = tmp_path / "root"
    root.mkdir()
    workspace = root / "workspace"
    workspace.mkdir()
    connection = _FakeConnection()
    registry = _FakeRegistry()
    session_map = SessionMap()
    seams = ManagerSeams(
        guest_stall_timeout_s=guest_stall_timeout_s,
        connect=lambda uri: connection,
        runner=lambda argv: CommandResult(0),
        channel=registry,
        session_map=session_map,
        workspace_policy=WorkspacePolicy(
            home=root / "home", data_root=root / "data", host_uid=os.getuid()
        ),
        preflight_fn=lambda config: PreflightResult(ok=True, mode=config.mode, reason=REASON_OK),
    )
    manager = IsolationManager(_enabled_config(root), seams=seams)
    return _ManagerHarness(
        manager=manager,
        connection=connection,
        registry=registry,
        session_map=session_map,
        root=root,
        workspace=workspace,
    )


def _dispatch_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
        "source_platform": "android",
        "artifact_workdir": "",
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Mobile decision path helpers (real SQLite + real MobileService)
# --------------------------------------------------------------------------- #


def _seed_blocked_task(
    blocked: list[dict[str, Any]],
    *,
    status: str = "waiting_approval",
    platform: str = "android",
) -> None:
    metadata = {
        "session": SESSION_ID,
        "platform": platform,
        "source": "mobile_bridge",
        "cwd": "/tmp/ws",
        "original_message_text": "clean the build dir",
    }
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, parent_id, stage, status, profile, input_text, metadata,"
            " created_at, updated_at, approval_blocked_tools) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                TASK_ID,
                None,
                "main",
                status,
                "cc-test",
                "clean the build dir",
                json.dumps(metadata),
                1_700_000_000,
                1_700_000_000,
                json.dumps(blocked),
            ),
        )
        conn.commit()


def _task_state() -> dict[str, Any]:
    with sqlite3.connect(get_db_path()) as conn:
        row = conn.execute(
            "SELECT status, metadata, approval_blocked_tools, error_message FROM tasks"
            " WHERE task_id = ?",
            (TASK_ID,),
        ).fetchone()
    assert row is not None
    return {
        "status": row[0],
        "metadata": json.loads(row[1]),
        "approval_blocked_tools": row[2],
        "error_message": row[3],
    }


@dataclass
class _MobileHarness:
    service: MobileService
    workspace: Path
    mapping: WorkspacePathMapping
    host_blocked: dict[str, Any]

    def metadata(self) -> dict[str, Any]:
        return _task_state()["metadata"]


def _mobile_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _MobileHarness:
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path / "home"))
    initialize_database(get_db_path())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mapping = _mapping(workspace)
    return _MobileHarness(
        service=MobileService(profile="cc-test", workdir=str(workspace)),
        workspace=workspace,
        mapping=mapping,
        host_blocked=canonicalize_blocked_tools(_blocked_write(), mapping),
    )


# --------------------------------------------------------------------------- #
# 1. The guest forwards the executor payload; the host channel accepts it
# --------------------------------------------------------------------------- #


def test_guest_emits_the_executor_blocked_payload_as_an_approval_envelope(tmp_path: Path) -> None:
    # Given: an in-guest executor that stops on a blocked Bash call
    blocked = _blocked_bash()

    def executor(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "waiting_approval",
            "failure_reason": "approval_required",
            "output_text": "Для выполнения этого действия требуется подтверждение.",
            "usage": {},
            "tool_calls_count": 1,
            "approval_blocked_tools": [blocked],
        }

    harness = _start_guest(executor, poll_interval_s=0.01)
    try:
        # When: the host dispatches one task
        harness.host.send(MessageKind.TASK, _guest_task_payload(tmp_path))
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the exact executor payload crosses in the inert envelope, then the result
    assert [message.kind for message in messages] == [
        MessageKind.APPROVAL_REQUEST,
        MessageKind.RESULT,
    ]
    envelope = dict(messages[0].payload)
    assert dict(envelope[DISPLAY_ENVELOPE_KEY]) == blocked
    result = dict(messages[1].payload)
    assert result["status"] == "waiting_approval"
    # And: the strict result frame cannot carry the blocked input (shell keys) -
    # which is exactly why the dedicated envelope exists
    assert "approval_blocked_tools" not in result
    # And: the guest never emits a decision of its own
    assert MessageKind.APPROVAL_DECISION not in [message.kind for message in messages]
    assert harness.exit_codes == [0]


def test_the_approval_envelope_carries_shell_keys_but_host_paths_stay_denied() -> None:
    host, guest = _pair()
    try:
        # Given: a blocked Bash payload whose input uses the denied shell keys
        payload: dict[str, JsonValue] = {
            DISPLAY_ENVELOPE_KEY: {
                "tool": "Bash",
                "input": {"command": "rm -rf /workspace/build", "argv": ["rm", "-rf"]},
            }
        }
        # When: the guest sends it as a real approval_request frame
        guest.send(MessageKind.APPROVAL_REQUEST, payload)
        inbound = host.read(timeout_s=2.0)

        # Then: the inert envelope crosses unchanged
        assert inbound is not None
        assert dict(inbound.payload) == payload

        # Then: a host path inside the envelope is still refused
        with pytest.raises(ChannelError) as caught:
            host.send(
                MessageKind.APPROVAL_REQUEST,
                {DISPLAY_ENVELOPE_KEY: {"tool": "Write", "target": "/home/example/secret"}},
            )
        assert caught.value.reason == "payload_host_path_denied"

        # Then: the shell-key relaxation does not apply outside the envelope
        with pytest.raises(ChannelError) as bare:
            host.send(MessageKind.APPROVAL_REQUEST, {"command": "id"})
        assert bare.value.reason == "payload_shell_key_denied"
    finally:
        host.close()
        guest.close()


def test_a_blocked_file_body_is_opaque_text_not_a_host_path() -> None:
    """A JS/CSS file body opens with ``/`` but is data, never a host path.

    Regression: every blocked ``Write`` of ``render.js`` was refused with
    ``payload_host_path_denied`` because the body opened with ``/*``. The guest
    drops a refused frame, the host then sees a bare ``waiting_approval``
    result and fails closed with ``approval_request_missing`` - the exact loop
    that killed nine file writes. Only actionable fields stay path-checked.
    """
    host, guest = _pair()
    try:
        body = "/* render.js */\nconst escapeHtml = (s) => s;\n"
        envelope = {
            "tool": "Write",
            "target": "/workspace/factory/web_ui/static/render.js",
            "input": {
                "file_path": "/workspace/factory/web_ui/static/render.js",
                "content": body,
            },
        }
        # When: the guest forwards it as a real approval_request frame
        guest.send(MessageKind.APPROVAL_REQUEST, {DISPLAY_ENVELOPE_KEY: envelope})
        inbound = host.read(timeout_s=2.0)

        # Then: the inert body crosses unchanged
        assert inbound is not None
        assert dict(inbound.payload[DISPLAY_ENVELOPE_KEY]) == envelope

        # And: the actionable file target is still path-checked
        with pytest.raises(ChannelError) as caught:
            host.send(
                MessageKind.APPROVAL_REQUEST,
                {
                    DISPLAY_ENVELOPE_KEY: {
                        "tool": "Write",
                        "target": "/workspace/ok.js",
                        "input": {"file_path": "/home/example/.ssh/id_ed25519", "content": body},
                    }
                },
            )
        assert caught.value.reason == "payload_host_path_denied"
    finally:
        host.close()
        guest.close()


def test_guest_wire_markers_match_the_channel_display_envelope() -> None:
    # Given/When/Then: the guest producer and the host policy share one wire contract
    assert guest_runner_module.TRACE_RECORD_KEY == DISPLAY_ENVELOPE_KEY


# --------------------------------------------------------------------------- #
# 2. Host-side canonicalization: guest paths -> host paths
# --------------------------------------------------------------------------- #


def test_a_guest_path_signature_is_canonicalized_to_the_host_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host_root = os.path.realpath(str(workspace))
    mapping = _mapping(workspace)
    blocked = _blocked_write()
    host_file = f"{host_root}/src/app.py"

    # When: the guest payload is canonicalized at the channel boundary
    host_blocked = canonicalize_blocked_tools(blocked, mapping)

    # Then: every path in target/signature/session_rule names the host file
    assert host_blocked["target"] == host_file
    assert host_blocked["signature"] == _approval_signature(
        "Write", {"file_path": host_file, "content": "print('hi')\n", "reason": "structural file artifact"}
    )
    assert host_blocked["session_rule"] == {
        "rule_type": "file_path",
        "tool": "Write",
        "file_path": host_file,
    }
    # And: the mapping is an exact round trip, so a resumed dispatch matches again
    assert mapping.map_host_value(host_blocked) == blocked
    # And: the park status follows the executor's per-platform rule
    assert approval_wait_status("android") == "waiting_approval"
    assert approval_wait_status("Android") == "waiting_approval"
    assert approval_wait_status("telegram") == "fail"
    assert approval_wait_status(None) == "fail"


def test_a_bash_command_is_canonicalized_inside_the_signature_text(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host_root = os.path.realpath(str(workspace))
    mapping = _mapping(workspace)
    command = "rm -rf /workspace/build"
    blocked = _blocked_bash(command)

    host_blocked = canonicalize_blocked_tools(blocked, mapping)

    host_command = f"rm -rf {host_root}/build"
    assert host_blocked["target"] == host_command
    assert host_blocked["signature"] == _approval_signature(
        "Bash", {"command": host_command, "reason": "destructive command"}
    )
    assert mapping.map_host_value(host_blocked) == blocked


# --------------------------------------------------------------------------- #
# 3. The existing mobile decision consumes the canonicalized approval once
# --------------------------------------------------------------------------- #


def test_the_mobile_decision_consumes_the_canonicalized_approval_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a parked android task whose blocked payload crossed the relay
    mobile = _mobile_harness(tmp_path, monkeypatch)
    _seed_blocked_task([mobile.host_blocked])
    host_file = f"{os.path.realpath(str(mobile.workspace))}/src/app.py"

    # When: the person allows the call once
    decision = mobile.service.resolve_approval(TASK_ID, BLOCKED_TOOL_USE_ID, "allow_once")

    # Then: the existing requeue contract carries the host signature and a forced call
    assert decision == {
        "task_id": TASK_ID,
        "request_id": BLOCKED_TOOL_USE_ID,
        "decision": "allow_once",
        "resumed_task_id": TASK_ID,
    }
    state = _task_state()
    assert state["status"] == "queued"
    assert state["metadata"]["approved_tool_calls"] == [mobile.host_blocked["signature"]]
    assert state["metadata"]["forced_tool_call"]["input"]["file_path"] == host_file
    assert state["metadata"]["forced_tool_call"]["tool_use_id"] == BLOCKED_TOOL_USE_ID

    # When: the same decision arrives a second time (client retry)
    with pytest.raises(MobileConflict):
        mobile.service.resolve_approval(TASK_ID, BLOCKED_TOOL_USE_ID, "allow_once")

    # Then: nothing was consumed twice
    assert _task_state()["metadata"]["approved_tool_calls"] == [
        mobile.host_blocked["signature"]
    ]


def test_a_malformed_decision_leaves_the_task_parked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mobile = _mobile_harness(tmp_path, monkeypatch)
    _seed_blocked_task([mobile.host_blocked])

    with pytest.raises(MobileInvalid):
        mobile.service.resolve_approval(TASK_ID, BLOCKED_TOOL_USE_ID, "allow_everything")

    assert _task_state()["status"] == "waiting_approval"


def test_reject_ends_fail_with_no_forced_call_and_nothing_to_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a parked android task
    mobile = _mobile_harness(tmp_path, monkeypatch)
    _seed_blocked_task([mobile.host_blocked])

    # When: the person rejects the call
    decision = mobile.service.resolve_approval(TASK_ID, BLOCKED_TOOL_USE_ID, "reject")

    # Then: the task ends fail, with no forced call left for a guest run
    assert decision["decision"] == "reject"
    assert decision["resumed_task_id"] is None
    state = _task_state()
    assert state["status"] == "fail"
    assert state["approval_blocked_tools"] == "[]"
    assert "forced_tool_call" not in state["metadata"]
    assert "approved_tool_calls" not in state["metadata"]
    # And: it is not queued, so no worker can dispatch a guest execution
    assert state["status"] != "queued"


# --------------------------------------------------------------------------- #
# 4. allow_once / allow_session consumption semantics (the real executor helpers)
# --------------------------------------------------------------------------- #


def test_allow_once_runs_once_then_a_second_identical_call_re_asks() -> None:
    blocked = _blocked_write()
    guest_input = blocked["input"]
    signature = _approval_signature("Write", guest_input)
    metadata: dict[str, Any] = {"approved_tool_calls": [signature]}

    # Given: the forced call consumes the one-shot approval
    assert (
        _consume_approved_tool_call(
            metadata=metadata,
            tool_name="Write",
            tool_input=guest_input,
            tool_use_id=BLOCKED_TOOL_USE_ID,
        )
        is True
    )
    assert metadata["approved_tool_calls"] == []
    # And: a replay of the same approved call (channel retry) stays approved
    assert (
        _consume_approved_tool_call(
            metadata=metadata,
            tool_name="Write",
            tool_input=guest_input,
            tool_use_id=BLOCKED_TOOL_USE_ID,
        )
        is True
    )
    # And: a second identical call with a new tool_use_id has no approval left
    assert (
        _consume_approved_tool_call(
            metadata=metadata,
            tool_name="Write",
            tool_input=guest_input,
            tool_use_id=SECOND_TOOL_USE_ID,
        )
        is False
    )
    # And: a different input never matches the marker either
    assert (
        _consume_approved_tool_call(
            metadata={**metadata, "approved_tool_calls": [signature]},
            tool_name="Write",
            tool_input={**guest_input, "file_path": "/workspace/other.py"},
            tool_use_id=BLOCKED_TOOL_USE_ID,
        )
        is False
    )


def test_consuming_without_a_tool_use_id_is_behavior_preserving() -> None:
    guest_input = {"file_path": GUEST_WORKSPACE_FILE}
    signature = _approval_signature("Write", guest_input)
    metadata: dict[str, Any] = {"approved_tool_calls": [signature]}

    assert (
        _consume_approved_tool_call(
            metadata=metadata, tool_name="Write", tool_input=guest_input
        )
        is True
    )
    assert metadata["approved_tool_calls"] == []
    assert "consumed_approved_tool_calls" not in metadata
    assert (
        _consume_approved_tool_call(
            metadata=metadata, tool_name="Write", tool_input=guest_input
        )
        is False
    )


def test_allow_session_does_not_re_ask_after_the_one_shot_approval_is_consumed() -> None:
    blocked = _blocked_write()
    guest_input = blocked["input"]
    rule = {"rule_type": "file_path", "tool": "Write", "file_path": GUEST_WORKSPACE_FILE}
    metadata: dict[str, Any] = {
        "approved_tool_calls": [_approval_signature("Write", guest_input)],
        "approved_session_rules": [rule],
    }

    # Given: the forced call consumes the one-shot approval, the session rule remains
    assert (
        _consume_approved_tool_call(
            metadata=metadata,
            tool_name="Write",
            tool_input=guest_input,
            tool_use_id=BLOCKED_TOOL_USE_ID,
        )
        is True
    )
    # Then: the next identical call in the session does not re-ask
    assert _approved_tool_call(
        metadata=metadata, tool_name="Write", tool_input=dict(guest_input)
    )
    other_input = {**guest_input, "file_path": "/workspace/other.py"}
    assert not _approved_tool_call(
        metadata=metadata, tool_name="Write", tool_input=other_input
    )


# --------------------------------------------------------------------------- #
# 5. Manager relay: park the task, keep the session VM warm for resume
# --------------------------------------------------------------------------- #


def test_an_android_blocked_call_parks_and_keeps_the_guest_vm(tmp_path: Path) -> None:
    # Given: a running session VM and a guest that stops on a blocked call
    harness = _manager_harness(tmp_path)
    blocked = _blocked_write()
    endpoint = _FakeEndpoint(
        [_approval_frame(blocked, 1), _result_frame(2, "waiting_approval")]
    )
    harness.registry.endpoints[SESSION_ID] = endpoint
    vm = harness.manager.ensure_vm(harness.session())
    overlay = Path(vm.domain.overlay or "")
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_bytes(b"overlay")

    # When: the task is dispatched from an android client
    result = harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(harness.workspace),
        _dispatch_payload(),
    )

    # Then: the task parks with the canonicalized blocked payload
    assert result["status"] == "waiting_approval"
    assert result["failure_reason"] == "approval_required"
    assert result["approval_blocked_tools"] == [
        canonicalize_blocked_tools(blocked, _mapping(harness.workspace))
    ]
    # And: the host sent the task only - never a decision of its own
    assert [kind for kind, _ in endpoint.sent] == [MessageKind.TASK]
    # And: only the one-shot runner channel closes; domain, overlay and CID stay
    # available so approval resume uses the same session VM.
    domain = harness.domain()
    assert domain.destroy_calls == 0
    assert domain.undefine_calls == 0
    assert domain.isActive() == 1
    assert overlay.exists()
    assert endpoint.closed is True
    assert harness.session_map.session_for_cid(vm.peer_cid) is not None


def test_a_non_android_blocked_call_ends_fail_even_when_the_guest_claims_waiting_approval(
    tmp_path: Path,
) -> None:
    harness = _manager_harness(tmp_path)
    blocked = _blocked_write()
    endpoint = _FakeEndpoint(
        [_approval_frame(blocked, 1), _result_frame(2, "waiting_approval")]
    )
    harness.registry.endpoints[SESSION_ID] = endpoint

    result = harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(harness.workspace),
        _dispatch_payload(source_platform="telegram"),
    )

    # The guest cannot park a non-android task: the host owns the platform rule.
    assert result["status"] == "fail"
    assert result["approval_blocked_tools"] == [
        canonicalize_blocked_tools(blocked, _mapping(harness.workspace))
    ]
    assert harness.domain().destroy_calls == 0
    assert harness.domain().isActive() == 1


def test_waiting_status_without_deliverable_approval_fails_closed(tmp_path: Path) -> None:
    # Given: a guest result whose approval envelope was rejected by channel policy
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint([_result_frame(1, "waiting_approval")])
    harness.registry.endpoints[SESSION_ID] = endpoint

    # When: the host has no actionable request to show or resolve
    result = harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(harness.workspace),
        _dispatch_payload(),
    )

    # Then: the task fails visibly instead of becoming an unresolvable mobile wait
    assert result["status"] == "fail"
    assert result["failure_reason"] == "approval_request_missing"
    assert result["approval_blocked_tools"] == []
    assert "Не удалось безопасно передать запрос подтверждения" in result["output_text"]
    assert harness.domain().destroy_calls == 0
    assert harness.domain().isActive() == 1


def test_an_android_guest_claiming_fail_is_parked_by_the_host(tmp_path: Path) -> None:
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint(
        [_approval_frame(_blocked_write(), 1), _result_frame(2, "fail")]
    )
    harness.registry.endpoints[SESSION_ID] = endpoint

    result = harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(harness.workspace),
        _dispatch_payload(),
    )

    assert result["status"] == "waiting_approval"
    assert result["failure_reason"] == "approval_required"


def test_the_payload_of_a_result_frame_is_canonicalized_too(tmp_path: Path) -> None:
    # Given: a guest whose result frame carries the blocked tools itself (no envelope)
    harness = _manager_harness(tmp_path)
    blocked = _blocked_write()
    endpoint = _FakeEndpoint([_result_frame(1, "waiting_approval", blocked=[blocked])])
    harness.registry.endpoints[SESSION_ID] = endpoint

    result = harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(harness.workspace),
        _dispatch_payload(),
    )

    assert result["approval_blocked_tools"] == [
        canonicalize_blocked_tools(blocked, _mapping(harness.workspace))
    ]


def test_a_stalled_guest_after_the_approval_frame_parks_without_a_running_domain(
    tmp_path: Path,
) -> None:
    # Given: a guest that sends the approval request and then dies before the result
    harness = _manager_harness(tmp_path, guest_stall_timeout_s=0.01)
    blocked = _blocked_bash()
    endpoint = _FakeEndpoint([_approval_frame(blocked, 1)])
    harness.registry.endpoints[SESSION_ID] = endpoint

    # When: the host awaits the result
    result = harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(harness.workspace),
        _dispatch_payload(),
    )

    # Then: the approval is not lost and the same session VM remains warm
    assert result["status"] == "waiting_approval"
    assert result["failure_reason"] == "approval_required"
    assert result["approval_blocked_tools"] == [
        canonicalize_blocked_tools(blocked, _mapping(harness.workspace))
    ]
    assert harness.domain().destroy_calls == 0
    assert harness.domain().isActive() == 1
    assert endpoint.closed is True


def test_a_duplicated_approval_frame_appends_exactly_one_blocked_entry(
    tmp_path: Path,
) -> None:
    harness = _manager_harness(tmp_path)
    blocked = _blocked_write()
    endpoint = _FakeEndpoint(
        [
            _approval_frame(blocked, 1),
            _approval_frame(blocked, 2),
            _result_frame(3, "waiting_approval"),
        ]
    )
    harness.registry.endpoints[SESSION_ID] = endpoint

    result = harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(harness.workspace),
        _dispatch_payload(),
    )

    assert result["approval_blocked_tools"] == [
        canonicalize_blocked_tools(blocked, _mapping(harness.workspace))
    ]


def test_the_resumed_dispatch_maps_host_paths_back_and_the_guest_consumes_the_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a task resumed through the real mobile decision (host paths stored)
    mobile = _mobile_harness(tmp_path, monkeypatch)
    _seed_blocked_task([mobile.host_blocked])
    mobile.service.resolve_approval(TASK_ID, BLOCKED_TOOL_USE_ID, "allow_once")
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint([_result_frame(1, "ok")])
    harness.registry.endpoints[SESSION_ID] = endpoint

    # When: the worker re-dispatches the resumed task to a fresh session VM
    result = harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(mobile.workspace),
        _dispatch_payload(metadata=mobile.metadata(), artifact_workdir=str(tmp_path / "artifacts")),
    )

    # Then: the wire carries the approval mapped back to guest paths
    assert result["status"] == "ok"
    kind, wire = endpoint.sent[0]
    assert kind is MessageKind.TASK
    resume = dict(wire["approval_resume"])
    assert dict(resume["forced_tool_call"])["input"]["file_path"] == GUEST_WORKSPACE_FILE
    assert list(resume["approved_tool_calls"]) == [
        _approval_signature("Write", _blocked_write()["input"])
    ]
    # And: no host path leaks into the guest payload
    assert os.path.realpath(str(mobile.workspace)) not in json.dumps(wire)

    # And: the guest-side parser yields metadata the executor would consume, so the
    # forced call runs instead of re-prompting forever
    guest_metadata = approval_resume_metadata(dict(wire["approval_resume"]))
    assert guest_metadata["forced_tool_call"]["name"] == "Write"
    assert (
        _consume_approved_tool_call(
            metadata=guest_metadata,
            tool_name="Write",
            tool_input=_blocked_write()["input"],
            tool_use_id=BLOCKED_TOOL_USE_ID,
        )
        is True
    )


def test_a_resume_reuses_the_existing_vm_and_workspace(tmp_path: Path) -> None:
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint(
        [_approval_frame(_blocked_write(), 1), _result_frame(2, "waiting_approval")]
    )
    harness.registry.endpoints[SESSION_ID] = endpoint
    first_vm = harness.manager.ensure_vm(harness.session())
    overlay = first_vm.domain.overlay
    harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(harness.workspace),
        _dispatch_payload(),
    )
    assert harness.domain().isActive() == 1

    # When: the approval is resolved and the worker runs the task again
    resumed_endpoint = _FakeEndpoint([_result_frame(1, "ok")])
    harness.registry.endpoints[SESSION_ID] = resumed_endpoint
    result = harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(harness.workspace),
        _dispatch_payload(),
    )

    # Then: the existing domain, disk overlay and host workspace are retained
    assert result["status"] == "ok"
    assert len(harness.connection.define_calls) == 1
    assert harness.manager._vms[SESSION_ID] is first_vm
    assert harness.manager._vms[SESSION_ID].domain.overlay == overlay
    assert harness.domain().create_calls == 1
    assert harness.domain().destroy_calls == 0
    assert harness.domain().isActive() == 1


def test_resume_wire_metadata_is_empty_without_an_approval_resume(tmp_path: Path) -> None:
    mapping = _mapping(tmp_path)
    assert resume_wire_metadata(None, mapping) == {}
    assert resume_wire_metadata({"session": "s"}, mapping) == {}
    assert resume_wire_metadata("not-a-dict", mapping) == {}


def test_a_malformed_approval_frame_is_skipped_and_the_result_still_lands(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    harness = _manager_harness(tmp_path)
    # Given: an approval frame without the inert envelope between two valid frames
    broken = ChannelMessage(kind=MessageKind.APPROVAL_REQUEST, seq=1, payload={"tool": "Write"})
    endpoint = _FakeEndpoint([broken, _result_frame(2, "ok")])
    harness.registry.endpoints[SESSION_ID] = endpoint

    with caplog.at_level(logging.WARNING):
        result = harness.manager.run_task(
            {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
            str(harness.workspace),
            _dispatch_payload(),
        )

    # Then: the run is not corrupted and the uninterpretable frame is warned about
    assert result["status"] == "ok"
    assert "approval" in caplog.text.lower()
    # And: an approval frame without a tool_use_id cannot be resolved, so the
    # relay must not pretend it parked anything
    relay = ApprovalRelay(_mapping(tmp_path))
    assert relay.collect(broken) is False
    assert relay.parked is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
