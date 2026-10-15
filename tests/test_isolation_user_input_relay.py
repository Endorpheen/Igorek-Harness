#!/usr/bin/env python3
"""TDD suite for the ``request_user_input`` relay across the VM boundary (plan todo 10).

The guest never touches the host store and never opens a terminal: the in-guest
tool call publishes a ``user_input_request`` frame (the form payload inside the
inert ``record`` envelope), and the host creates the request in the EXISTING
``factory/core/user_input_store.py`` - the mobile ``pending_user_input`` /
``POST /tasks/{id}/user-input/{request_id}`` contract is reused verbatim. On a
reply the host sends ``user_input_reply{status:"submitted"}`` and the same guest
run continues; on cancel (or relay timeout) it sends
``user_input_reply{status:"cancelled"}`` and the guest ends cleanly with
``result{status:"canceled"}``.

Adversarial classes covered here:

* misleading success - exact store payload equality, exact reply payloads, exact
  statuses; a request must appear exactly once and a malformed frame must not
  buy a store entry;
* resumable flow - reply resumes the SAME guest run (no re-dispatch), cancel
  ends the run without ever delivering an answer, and a duplicated request frame
  is answered without a second store entry;
* dirty worktree - this suite writes only into pytest temp dirs (the store runs
  against a temp ``IGOREK_FACTORY_HOME``).

No VM and no AF_VSOCK are needed: the transport seam is an in-process
``socketpair``, the manager's libvirt connection is a fake, and the mobile
decision path is the real ``MobileService`` against a temp database.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from factory.core.cc_backend_tools import _handle_request_user_input
from factory.core.isolation.channel import (
    DISPLAY_ENVELOPE_KEY,
    USER_INPUT_CANCELLED,
    USER_INPUT_SUBMITTED,
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
from factory.core.isolation.errors import IsolationError
from factory.core.isolation.guest_runner import GuestRunner, RunnerSeams, RunnerTuning
from factory.core.isolation.egress_enforce import ApplyResult
from factory.core.isolation.guest_task import GuestTask
from factory.core.isolation.user_input import UserInputRelay
from factory.core.isolation.manager import IsolationManager, ManagerSeams, SessionSpec
from factory.core.isolation.preflight import REASON_OK, PreflightResult
from factory.core.isolation.workspace import WorkspacePolicy
from factory.core.isolation import workspace as workspace_module
from factory.core.mobile_bridge.service import MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.core.user_input_store import list_pending_user_input_requests



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
TASK_ID = "10b00c0d-1111-2222-3333-444444444444"
SESSION_ID = "chat-20260922-000000-task10"
FAKE_CID = 61

FORM_TITLE = "Deploy check"
FORM_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "deploy",
        "header": "Confirm",
        "question": "Deploy to prod?",
        "options": [{"label": "yes"}, {"label": "no"}],
    }
]
#: Exactly the payload the store path publishes (``cc_backend_tools``).
FORM_PAYLOAD: dict[str, Any] = {
    "title": FORM_TITLE,
    "item_id": "request_user_input",
    "thread_id": "",
    "turn_id": "",
    "questions": FORM_QUESTIONS,
}
ANSWER: dict[str, Any] = {
    "cancelled": False,
    "answers": {
        "deploy": {
            "question_id": "deploy",
            "selected_options": ["yes"],
            "text_value": "",
            "notes": "",
        }
    },
}
CANCELLED_JSON = json.dumps(
    {"cancelled": True, "error": "User cancelled the input form."}
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every store call in this suite runs against a throwaway data root."""
    home = tmp_path_factory.mktemp("user-input-home")
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(home))
    initialize_database(get_db_path())
    return home


def _pump_replies(
    session: PeerSession, bridge: Any, *, stop: threading.Event
) -> threading.Thread:
    """Mirror the runner's inbound loop so a bare bridge can be driven in tests."""

    def loop() -> None:
        while not stop.is_set():
            try:
                message = session.read(timeout_s=0.1)
            except ChannelError as exc:
                if exc.reason == "read_timeout":
                    continue
                return
            if message is None:
                return
            if message.kind is MessageKind.USER_INPUT_REPLY:
                bridge.deliver(message)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


def _wait_for(predicate: Callable[[], bool], *, timeout_s: float = 2.0) -> None:
    """Poll a cross-thread outcome instead of sleeping a fixed interval."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("the awaited outcome never arrived")


# --------------------------------------------------------------------------- #
# Frames and store queries (the exact wire/DB surfaces under test)
# --------------------------------------------------------------------------- #


def _request_frame(
    guest_request_id: str = "guest-request-1",
    *,
    record: Mapping[str, JsonValue] | None = None,
    seq: int = 1,
) -> ChannelMessage:
    return ChannelMessage(
        kind=MessageKind.USER_INPUT_REQUEST,
        seq=seq,
        payload={
            "request_id": guest_request_id,
            DISPLAY_ENVELOPE_KEY: dict(record if record is not None else FORM_PAYLOAD),
        },
    )


def _result_frame(seq: int, status: str = "ok", output_text: str = "done") -> ChannelMessage:
    return ChannelMessage(
        kind=MessageKind.RESULT,
        seq=seq,
        payload={
            "status": status,
            "output_text": output_text,
            "usage": {"prompt": 1, "completion": 1},
            "tool_calls_count": 1,
        },
    )


def _reply_frame(request_id: str, *, status: str, response: dict[str, Any] | None) -> ChannelMessage:
    return ChannelMessage(
        kind=MessageKind.USER_INPUT_REPLY,
        seq=0,
        payload={"request_id": request_id, "status": status, "response": response},
    )


def _store_rows(task_id: str = TASK_ID) -> list[tuple[str, str, str]]:
    """(request_id, status, payload_json) rows of the real store, raw SQL."""
    with sqlite3.connect(get_db_path()) as conn:
        return list(
            conn.execute(
                "SELECT request_id, status, payload_json FROM user_input_requests"
                " WHERE task_id = ? ORDER BY created_at, request_id",
                (task_id,),
            ).fetchall()
        )


@dataclass
class _Responder:
    """A background thread that answers the pending store request like the phone."""

    captured: dict[str, Any]
    thread: threading.Thread

    def join(self, timeout_s: float = 10.0) -> dict[str, Any]:
        self.thread.join(timeout=timeout_s)
        assert not self.thread.is_alive(), "the responder never saw a pending request"
        return self.captured


def _answer_like_the_phone(
    *,
    response: dict[str, Any] | None = None,
    cancelled: bool = False,
    reject: bool = False,
    service: MobileService | None = None,
    task_id: str = TASK_ID,
    timeout_s: float = 10.0,
) -> _Responder:
    """Resolve the first pending request through the real store (or the real mobile service)."""
    captured: dict[str, Any] = {"count_at_resolve": 0, "seen": []}

    def worker() -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            pending = list_pending_user_input_requests(task_id=task_id)
            if pending:
                captured["count_at_resolve"] = len(pending)
                captured["seen"] = [dict(item) for item in pending]
                first = pending[0]
                if service is not None:
                    if reject:
                        captured["mobile"] = service.resolve_approval(
                            task_id, str(first["request_id"]), "reject"
                        )
                    else:
                        captured["mobile"] = service.resolve_user_input(
                            task_id,
                            str(first["request_id"]),
                            response=response,
                            cancelled=cancelled,
                        )
                else:
                    from factory.core.user_input_store import resolve_user_input_request

                    resolve_user_input_request(
                        str(first["request_id"]), response=response, cancelled=cancelled
                    )
                captured["resolved"] = True
                return
            time.sleep(0.01)
        captured["timeout"] = True

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return _Responder(captured=captured, thread=thread)


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
        "task_id": TASK_ID,
        "profile": "cc-wally",
        "model": "glm-5.3-flash",
        "base_url": "https://inference.runanywhere.ai/v1",
        "api_format": "openai",
        "system_prompt": "You are Igorek.",
        "user_message": "deploy the app",
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
    """Host half of one guest connection; can refuse selected outgoing frames."""

    def __init__(
        self,
        messages: list[ChannelMessage] | None = None,
        *,
        refuse: dict[MessageKind, str] | None = None,
    ) -> None:
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []
        self._messages = list(messages or [])
        self._refuse = dict(refuse or {})
        self.closed = False

    def send(self, kind: MessageKind, payload: Mapping[str, JsonValue]) -> None:
        reason = self._refuse.get(kind)
        if reason is not None:
            raise ChannelError(reason, "refused by the fake endpoint")
        self.sent.append((kind, dict(payload)))

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        self.closed = True


class _FakeRegistry:
    def __init__(self) -> None:
        self.endpoints: dict[str, Any] = {}

    def session_for(self, session_id: SessionId) -> Any:
        return self.endpoints.get(str(session_id))

    def close(self) -> None:
        return None


class _RecordingEndpoint:
    """Wraps a live PeerSession, recording every inbound guest frame."""

    def __init__(self, session: PeerSession) -> None:
        self._session = session
        self.frames: list[ChannelMessage] = []

    def send(self, kind: MessageKind, payload: Mapping[str, JsonValue]) -> None:
        self._session.send(kind, payload)

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        message = self._session.recv_from_guest(timeout_s)
        if message is not None:
            self.frames.append(message)
        return message

    def close(self) -> None:
        self._session.close()


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


def _manager_harness(tmp_path: Path) -> _ManagerHarness:
    root = tmp_path / "root"
    root.mkdir()
    workspace = root / "workspace"
    workspace.mkdir()
    connection = _FakeConnection()
    registry = _FakeRegistry()
    session_map = SessionMap()
    seams = ManagerSeams(
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


def _run_manager(
    harness: _ManagerHarness,
    endpoint: Any,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    harness.registry.endpoints[SESSION_ID] = endpoint
    return harness.manager.run_task(
        {"task_id": TASK_ID, "metadata": {"session": SESSION_ID}},
        str(harness.workspace),
        payload if payload is not None else _dispatch_payload(),
    )


# --------------------------------------------------------------------------- #
# Mobile-side row used by the real MobileService decision path
# --------------------------------------------------------------------------- #


def _seed_processing_task(task_id: str = TASK_ID) -> None:
    metadata = {
        "session": SESSION_ID,
        "platform": "android",
        "source": "mobile_bridge",
        "cwd": "/tmp/ws",
        "original_message_text": "deploy the app",
    }
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, parent_id, stage, status, profile, input_text, metadata,"
            " created_at, updated_at, approval_blocked_tools) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                None,
                "main",
                "processing",
                "cc-test",
                "deploy the app",
                json.dumps(metadata),
                1_700_000_000,
                1_700_000_000,
                "[]",
            ),
        )
        conn.commit()


# --------------------------------------------------------------------------- #
# 1. Wire contract: the inert envelope on user_input_request
# --------------------------------------------------------------------------- #


def test_the_request_frame_carries_the_form_payload_in_the_inert_envelope() -> None:
    host, guest = _pair()
    stop = threading.Event()
    try:
        # Given/When: the guest bridge publishes one form request
        from factory.core.isolation.guest_user_input import UserInputBridge

        bridge = UserInputBridge(guest.send, timeout_s=5.0)
        _pump_replies(guest, bridge, stop=stop)
        result: dict[str, Any] = {}

        def call() -> None:
            result["value"] = bridge.request(FORM_PAYLOAD)

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        message = host.read(timeout_s=2.0)

        # Then: the exact form payload crosses inside the record envelope
        assert message is not None
        assert message.kind is MessageKind.USER_INPUT_REQUEST
        payload = dict(message.payload)
        assert payload[DISPLAY_ENVELOPE_KEY] == FORM_PAYLOAD
        request_id = str(payload["request_id"])
        assert request_id

        # When: the host answers
        host.send(
            MessageKind.USER_INPUT_REPLY,
            {"request_id": request_id, "status": USER_INPUT_SUBMITTED, "response": ANSWER},
        )
        thread.join(timeout=2.0)

        # Then: the bridge hands the exact response back to the executor
        assert not thread.is_alive()
        assert result["value"] == ANSWER
    finally:
        stop.set()
        host.close()
        guest.close()


def test_the_envelope_allows_shell_keys_but_host_paths_stay_denied() -> None:
    host, guest = _pair()
    try:
        # Given: a form whose record holds a shell-named key (the model's form data)
        payload: dict[str, JsonValue] = {
            "request_id": "guest-1",
            DISPLAY_ENVELOPE_KEY: {
                "title": "Bash arguments",
                "questions": [{"id": "q1", "header": "H", "question": "run?", "command": "ls"}],
            },
        }
        # When: it crosses as a real user_input_request frame
        guest.send(MessageKind.USER_INPUT_REQUEST, payload)
        inbound = host.read(timeout_s=2.0)

        # Then: the inert envelope is not denied for shell keys
        assert inbound is not None
        assert dict(inbound.payload) == payload

        # Then: a whole-value host path inside the envelope is still refused
        with pytest.raises(ChannelError) as denied:
            host.send(
                MessageKind.USER_INPUT_REQUEST,
                {
                    "request_id": "guest-2",
                    DISPLAY_ENVELOPE_KEY: {"title": "/home/example/secret"},
                },
            )
        assert denied.value.reason == "payload_host_path_denied"

        # Then: shell keys outside the envelope stay denied
        with pytest.raises(ChannelError) as bare:
            host.send(MessageKind.USER_INPUT_REQUEST, {"command": "id"})
        assert bare.value.reason == "payload_shell_key_denied"
    finally:
        host.close()
        guest.close()


def test_the_guest_bridge_returns_none_and_aborts_on_a_cancelled_reply() -> None:
    host, guest = _pair()
    try:
        from factory.core.isolation.guest_user_input import UserInputBridge, UserInputReply

        bridge = UserInputBridge(guest.send, timeout_s=5.0)
        result: dict[str, Any] = {}

        thread = threading.Thread(target=lambda: result.update(value=bridge.request(FORM_PAYLOAD)), daemon=True)
        thread.start()
        message = host.read(timeout_s=2.0)
        assert message is not None
        request_id = str(dict(message.payload)["request_id"])

        # When: the host cancels the form
        reply = _reply_frame(request_id, status=USER_INPUT_CANCELLED, response=None)
        outcome = bridge.deliver(reply)
        thread.join(timeout=2.0)

        # Then: the tool call wakes with "cancelled" and no answer
        assert outcome is UserInputReply.CANCELLED
        assert result["value"] is None
    finally:
        host.close()
        guest.close()


def test_the_guest_bridge_times_out_without_a_reply() -> None:
    host, guest = _pair()
    try:
        from factory.core.isolation.guest_user_input import UserInputBridge

        bridge = UserInputBridge(guest.send, timeout_s=0.2)
        # When/Then: no reply arrives, the bridge refuses to hang the run forever
        with pytest.raises(TimeoutError):
            bridge.request(FORM_PAYLOAD)
        message = host.read(timeout_s=2.0)
        assert message is not None and message.kind is MessageKind.USER_INPUT_REQUEST
    finally:
        host.close()
        guest.close()


def test_the_guest_bridge_ignores_a_reply_for_an_unknown_request() -> None:
    from factory.core.isolation.guest_user_input import UserInputBridge, UserInputReply

    left, right = socket.socketpair()
    try:
        guest = PeerSession(left, CONFIG, PeerIdentity())
        bridge = UserInputBridge(guest.send, timeout_s=1.0)
        outcome = bridge.deliver(_reply_frame("nobody-waits", status=USER_INPUT_SUBMITTED, response=ANSWER))
        assert outcome is UserInputReply.UNKNOWN
    finally:
        right.close()
        left.close()


# --------------------------------------------------------------------------- #
# 2. The executor seam: provider replaces the store/PTY paths
# --------------------------------------------------------------------------- #


def test_the_provider_seam_publishes_the_exact_store_payload_shape() -> None:
    seen: list[dict[str, Any]] = []

    def provider(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(payload))
        return ANSWER

    # When: the tool runs with an injected provider (the guest path)
    raw = _handle_request_user_input(
        {"title": FORM_TITLE, "questions": FORM_QUESTIONS},
        user_input_provider=provider,
        task_id=TASK_ID,
        timeout_s=30,
    )

    # Then: the provider receives exactly the payload the store path publishes,
    # and the model receives exactly the store-path serialization
    assert seen == [FORM_PAYLOAD]
    assert json.loads(raw) == ANSWER
    # And: no store row was created by the guest path
    assert _store_rows() == []


def test_the_provider_seam_has_parity_with_the_store_path() -> None:
    seen: list[dict[str, Any]] = []
    answers: dict[str, Any] = {}

    def provider(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(payload))
        return ANSWER

    provider_raw = _handle_request_user_input(
        {"title": FORM_TITLE, "questions": FORM_QUESTIONS}, user_input_provider=provider
    )

    # Given: the store path resolving the same form through the real store
    def store_call() -> None:
        answers["raw"] = _handle_request_user_input(
            {"title": FORM_TITLE, "questions": FORM_QUESTIONS}, task_id=TASK_ID, timeout_s=30
        )

    thread = threading.Thread(target=store_call, daemon=True)
    thread.start()
    responder = _answer_like_the_phone(response=ANSWER)
    responder.join()
    thread.join(timeout=10.0)

    # Then: identical payload and identical serialization - the model cannot tell
    # the two transports apart, and the store row carries the exact form payload
    assert seen == [FORM_PAYLOAD]
    rows = _store_rows()
    assert len(rows) == 1
    assert json.loads(rows[0][2]) == FORM_PAYLOAD
    assert json.loads(answers["raw"]) == json.loads(provider_raw) == ANSWER


def test_the_provider_seam_serializes_a_cancel_like_the_store_does() -> None:
    # Given/When: the provider reports a cancelled form
    raw = _handle_request_user_input(
        {"title": FORM_TITLE, "questions": FORM_QUESTIONS},
        user_input_provider=lambda payload: None,
    )
    # Then: the model sees the off-mode cancelled JSON verbatim
    assert raw == CANCELLED_JSON


# --------------------------------------------------------------------------- #
# 3. Host relay: exactly one store entry, one reply, cancel ends the run
# --------------------------------------------------------------------------- #


def test_a_request_creates_exactly_one_store_entry_and_the_reply_resumes_the_guest(
    tmp_path: Path,
) -> None:
    # Given: a guest that asks for input and then answers with the result frame
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint([_request_frame("guest-1", seq=1), _result_frame(2)])
    responder = _answer_like_the_phone(response=ANSWER)

    # When: the host dispatches one task; the relay publishes and answers it
    result = _run_manager(harness, endpoint)
    responder.join()

    # Then: the request appeared exactly once, with the exact form payload
    seen = responder.captured
    assert seen["count_at_resolve"] == 1
    assert seen["seen"][0]["payload"] == FORM_PAYLOAD
    assert seen["seen"][0]["status"] == "pending"
    assert seen["seen"][0]["task_id"] == TASK_ID
    assert len(_store_rows()) == 1
    assert _store_rows()[0][1] == "submitted"

    # Then: the host sent the task and exactly the answered reply
    assert [kind for kind, _ in endpoint.sent] == [
        MessageKind.TASK,
        MessageKind.USER_INPUT_REPLY,
    ]
    assert dict(endpoint.sent[1][1]) == {
        "request_id": "guest-1",
        "status": USER_INPUT_SUBMITTED,
        "response": ANSWER,
    }
    # Then: the guest result is normalized verbatim
    assert result == {
        "status": "ok",
        "output_text": "done",
        "usage": {"prompt": 1, "completion": 1},
        "tool_calls_count": 1,
    }


def test_a_duplicate_request_frame_reuses_the_store_entry_and_the_reply(
    tmp_path: Path,
) -> None:
    # Given: a guest that repeats the same request id (a channel retry)
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint(
        [
            _request_frame("guest-retry", seq=1),
            _request_frame("guest-retry", seq=2),
            _result_frame(3),
        ]
    )
    responder = _answer_like_the_phone(response=ANSWER)

    # When: the host relays both frames
    result = _run_manager(harness, endpoint)
    responder.join()

    # Then: exactly one store entry exists and both frames were answered with it
    assert responder.captured["count_at_resolve"] == 1
    assert len(_store_rows()) == 1
    kinds = [kind for kind, _ in endpoint.sent]
    assert kinds == [MessageKind.TASK, MessageKind.USER_INPUT_REPLY, MessageKind.USER_INPUT_REPLY]
    first = dict(endpoint.sent[1][1])
    second = dict(endpoint.sent[2][1])
    assert first == second
    assert first == {
        "request_id": "guest-retry",
        "status": USER_INPUT_SUBMITTED,
        "response": ANSWER,
    }
    assert result["status"] == "ok"


def test_a_cancelled_request_sends_a_cancelled_reply_and_the_guest_ends_canceled(
    tmp_path: Path,
) -> None:
    # Given: a guest waiting for input while the phone cancels the form
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint([_request_frame("guest-cancel", seq=1), _result_frame(2, "canceled")])
    responder = _answer_like_the_phone(cancelled=True)

    # When: the host relays the cancel
    result = _run_manager(harness, endpoint)
    responder.join()

    # Then: the reply carries no answer, exactly as the plan requires
    reply = dict(endpoint.sent[1][1])
    assert reply == {"request_id": "guest-cancel", "status": USER_INPUT_CANCELLED, "response": None}
    assert _store_rows()[0][1] == "cancelled"

    # Then: the canceled guest run becomes a clean terminal result, never an error
    assert result["status"] == "fail"
    assert result["failure_reason"] == "canceled"
    assert result["error"] == "guest run canceled"


def test_a_user_input_timeout_cancels_the_store_entry_and_the_guest_ends_canceled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a person who never answers while the dispatch timeout elapses
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint([_request_frame("guest-timeout", seq=1), _result_frame(2, "canceled")])
    module = pytest.importorskip("factory.core.isolation.user_input")

    def never_answered(request_id: str, *, timeout_s: float, interval_s: float = 0.1) -> None:
        raise TimeoutError(f"nobody answered {request_id}")

    monkeypatch.setattr(module, "wait_for_user_input_response", never_answered)

    # When: the relay waits on the real store and the timeout fires
    result = _run_manager(harness, endpoint, payload=_dispatch_payload(timeout_s=1))

    # Then: the pending form is cancelled (the phone must not keep showing it)
    rows = _store_rows()
    assert len(rows) == 1 and rows[0][1] == "cancelled"
    reply = dict(endpoint.sent[1][1])
    assert reply == {"request_id": "guest-timeout", "status": USER_INPUT_CANCELLED, "response": None}
    assert result["failure_reason"] == "canceled"


def test_an_answer_that_races_the_timeout_is_not_turned_into_a_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a wait that resolves the row and then reports the timeout anyway
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint([_request_frame("guest-race", seq=1), _result_frame(2)])
    module = pytest.importorskip("factory.core.isolation.user_input")

    def resolved_then_timed_out(request_id: str, *, timeout_s: float, interval_s: float = 0.1) -> None:
        from factory.core.user_input_store import resolve_user_input_request

        resolve_user_input_request(request_id, response=ANSWER)
        raise TimeoutError("the answer landed with the timeout")

    monkeypatch.setattr(module, "wait_for_user_input_response", resolved_then_timed_out)

    # When: the relay handles that timeout
    result = _run_manager(harness, endpoint, payload=_dispatch_payload(timeout_s=1))

    # Then: the person's answer is delivered, not dropped for a cancel
    reply = dict(endpoint.sent[1][1])
    assert reply == {
        "request_id": "guest-race",
        "status": USER_INPUT_SUBMITTED,
        "response": ANSWER,
    }
    assert result["status"] == "ok"


def test_a_malformed_request_frame_is_skipped_and_the_result_still_lands(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    harness = _manager_harness(tmp_path)
    # Given: a request frame without the display envelope between valid frames
    broken = ChannelMessage(kind=MessageKind.USER_INPUT_REQUEST, seq=1, payload={"question": "?"})
    endpoint = _FakeEndpoint([broken, _result_frame(2)])

    with caplog.at_level(logging.WARNING):
        result = _run_manager(harness, endpoint)

    # Then: the run is not corrupted, nothing was stored, and the skip is logged
    assert result["status"] == "ok"
    assert "user input" in caplog.text.lower()
    assert _store_rows() == []
    assert [kind for kind, _ in endpoint.sent] == [MessageKind.TASK]


def test_a_reply_the_channel_refuses_stops_the_vm_and_raises_isolation_error(
    tmp_path: Path,
) -> None:
    # Given: a guest request whose answer the payload policy cannot carry
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint(
        [_request_frame("guest-denied", seq=1), _result_frame(2)],
        refuse={MessageKind.USER_INPUT_REPLY: "payload_host_path_denied"},
    )
    responder = _answer_like_the_phone(response=ANSWER)

    # When: the host tries to deliver the answer
    with pytest.raises(IsolationError) as refused:
        _run_manager(harness, endpoint)
    responder.join()

    # Then: the failure is the canonical structured error and the guest is not
    # left waiting on a live domain
    assert refused.value.code == "user_input_reply_failed"
    assert refused.value.reason.endswith("payload_host_path_denied")
    assert endpoint.closed is True
    assert harness.domain().destroy_calls == 1


def test_the_guest_may_not_send_a_user_input_reply_frame(tmp_path: Path) -> None:
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint([_reply_frame("x", status=USER_INPUT_SUBMITTED, response={"a": 1})])

    with pytest.raises(IsolationError) as refused:
        _run_manager(harness, endpoint)

    assert refused.value.code == "unexpected_guest_frame"


def test_a_canceled_result_frame_is_normalized_to_a_clean_fail(tmp_path: Path) -> None:
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint([_result_frame(1, "canceled")])

    result = _run_manager(harness, endpoint)

    assert result["status"] == "fail"
    assert result["failure_reason"] == "canceled"
    assert result["usage"] == {"prompt": 1, "completion": 1}
    assert result["tool_calls_count"] == 1


def test_a_request_without_the_envelope_is_refused_by_the_relay(tmp_path: Path) -> None:
    # Given: a request frame whose payload is not the inert form envelope
    broken = ChannelMessage(kind=MessageKind.USER_INPUT_REQUEST, seq=1, payload={"question": "?"})

    # When/Then: the relay stores nothing and answers nothing
    assert UserInputRelay(task_id=TASK_ID).handle_request(broken) is None
    assert _store_rows() == []


def test_the_mobile_decision_path_is_reused_verbatim(tmp_path: Path) -> None:
    # Given: a running task and one pending form published by the relay
    _seed_processing_task()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = MobileService(profile="cc-test", workdir=str(workspace))
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint([_request_frame("guest-mobile", seq=1), _result_frame(2)])
    responder = _answer_like_the_phone(response=ANSWER, service=service)

    # When: the mobile POST path answers it while the host relays
    _run_manager(harness, endpoint)
    responder.join()

    # Then: the existing contract answers with resolved=True, resumed=False (in kvm
    # the guest resumes in place, no requeue) and the store row is submitted
    mobile = responder.captured["mobile"]
    assert mobile == {
        "task_id": TASK_ID,
        "request_id": responder.captured["seen"][0]["request_id"],
        "cancelled": False,
        "resumed": False,
    }
    assert _store_rows()[0][1] == "submitted"


def test_the_mobile_reject_button_cancels_the_form(tmp_path: Path) -> None:
    # Given: a running task with one pending form (Android's user_input card)
    _seed_processing_task()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = MobileService(profile="cc-test", workdir=str(workspace))
    harness = _manager_harness(tmp_path)
    endpoint = _FakeEndpoint([_request_frame("guest-reject", seq=1), _result_frame(2, "canceled")])
    responder = _answer_like_the_phone(reject=True, service=service)

    # When: the existing reject decision arrives on the mobile route
    result = _run_manager(harness, endpoint)
    responder.join()

    # Then: the form is cancelled and no answer was delivered to the guest
    assert responder.captured["mobile"]["cancelled"] is True
    reply = dict(endpoint.sent[1][1])
    assert reply == {"request_id": "guest-reject", "status": USER_INPUT_CANCELLED, "response": None}
    assert result["failure_reason"] == "canceled"


# --------------------------------------------------------------------------- #
# 4. Headless e2e: real guest runner + real manager + real store, no PTY
# --------------------------------------------------------------------------- #


class _HostileStdin:
    """Any terminal access on the guest path is a test failure."""

    def fileno(self) -> int:
        raise AssertionError("the guest user-input path touched the terminal")

    def read(self, *_args: Any) -> str:
        raise AssertionError("the guest user-input path read the terminal")

    def isatty(self) -> bool:
        return False


def _live_e2e_executor(captured: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    """A fake guest loop whose only tool call is the REAL request_user_input handler."""

    def executor(**kwargs: Any) -> dict[str, Any]:
        captured["provider"] = kwargs.get("user_input_provider")
        raw = _handle_request_user_input(
            {"title": FORM_TITLE, "questions": FORM_QUESTIONS}, **kwargs
        )
        captured["tool_result"] = raw
        return {"status": "ok", "output_text": raw, "usage": {"prompt": 1}, "tool_calls_count": 1}

    return executor


def test_headless_e2e_reply_resumes_the_guest_and_the_task_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a real guest over the real channel, a real manager and the real store
    monkeypatch.setattr(sys, "stdin", _HostileStdin())
    monkeypatch.setattr(
        workspace_module, "GUEST_WORKSPACE_PATH", str(tmp_path / "guest-workspace")
    )
    captured: dict[str, Any] = {}
    guest = _start_guest(_live_e2e_executor(captured), poll_interval_s=0.01)
    harness = _manager_harness(tmp_path)
    endpoint = _RecordingEndpoint(guest.host)
    responder = _answer_like_the_phone(response=ANSWER)

    try:
        # When: the host dispatches the task and the phone answers the form
        result = _run_manager(harness, endpoint, payload=_dispatch_payload(timeout_s=30))
        responder.join()
    finally:
        guest.finish()

    # Then: the guest got the provider seam, not a terminal
    assert callable(captured["provider"])
    # Then: the request appeared exactly once with the exact payload
    assert responder.captured["count_at_resolve"] == 1
    assert responder.captured["seen"][0]["payload"] == FORM_PAYLOAD
    assert len(_store_rows()) == 1
    # Then: the answer went back to the same run and the task completed
    assert json.loads(captured["tool_result"]) == ANSWER
    assert result["status"] == "ok"
    assert result["output_text"] == json.dumps(ANSWER, ensure_ascii=False)
    kinds = [message.kind for message in endpoint.frames]
    assert kinds == [MessageKind.USER_INPUT_REQUEST, MessageKind.RESULT]
    assert dict(endpoint.frames[-1].payload)["status"] == "ok"


def test_headless_e2e_cancel_ends_the_guest_without_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", _HostileStdin())
    monkeypatch.setattr(
        workspace_module, "GUEST_WORKSPACE_PATH", str(tmp_path / "guest-workspace")
    )
    captured: dict[str, Any] = {}
    guest = _start_guest(_live_e2e_executor(captured), poll_interval_s=0.01, cancel_grace_s=0.5)
    harness = _manager_harness(tmp_path)
    endpoint = _RecordingEndpoint(guest.host)
    responder = _answer_like_the_phone(cancelled=True)

    try:
        # When: the phone cancels the form instead of answering
        result = _run_manager(harness, endpoint, payload=_dispatch_payload(timeout_s=30))
        responder.join()
        _wait_for(lambda: captured.get("tool_result") is not None)
    finally:
        guest.finish()

    # Then: the guest run ended with result{status:"canceled"} and never saw an answer
    assert responder.captured["count_at_resolve"] == 1
    kinds = [message.kind for message in endpoint.frames]
    assert kinds == [MessageKind.USER_INPUT_REQUEST, MessageKind.RESULT]
    assert dict(endpoint.frames[-1].payload)["status"] == "canceled"
    assert captured["tool_result"] == CANCELLED_JSON
    # And: the host normalized the canceled guest run into a clean terminal fail
    assert result["status"] == "fail"
    assert result["failure_reason"] == "canceled"
    assert result["output_text"] == ""


HEADLESS_SCRIPT = textwrap.dedent(
    """
    import json
    import socket
    import sys
    import threading

    class PromptToolkitBlocker:
        def find_spec(self, name, path=None, target=None):
            if name == "prompt_toolkit" or name.startswith("prompt_toolkit."):
                raise ImportError("prompt_toolkit must not be imported on the guest path")
            return None

    sys.meta_path.insert(0, PromptToolkitBlocker())

    class NoTerminal:
        def fileno(self):
            raise AssertionError("the guest user-input path touched the terminal")

        def read(self, *_args):
            raise AssertionError("the guest user-input path read the terminal")

        def isatty(self):
            return False

    sys.stdin = NoTerminal()

    from factory.core.cc_backend_tools import _handle_request_user_input
    from factory.core.isolation.channel import ChannelConfig, MessageKind, PeerIdentity, PeerSession
    from factory.core.isolation.guest_user_input import UserInputBridge

    config = ChannelConfig(max_frame_bytes=65536)
    left, right = socket.socketpair()
    guest = PeerSession(left, config, PeerIdentity())
    host = PeerSession(right, config, PeerIdentity())
    bridge = UserInputBridge(guest.send, timeout_s=10.0)
    form = {
        "title": "Deploy check",
        "item_id": "request_user_input",
        "thread_id": "",
        "turn_id": "",
        "questions": [
            {"id": "q1", "header": "Confirm", "question": "Deploy?", "options": [{"label": "yes"}]}
        ],
    }
    answer = {
        "cancelled": False,
        "answers": {
            "q1": {
                "question_id": "q1",
                "selected_options": ["yes"],
                "text_value": "",
                "notes": "",
            }
        },
    }
    seen = {}

    def pump_replies():
        while True:
            try:
                message = guest.read(timeout_s=0.1)
            except ChannelError as exc:
                if exc.reason == "read_timeout":
                    continue
                return
            if message is None:
                return
            if message.kind is MessageKind.USER_INPUT_REPLY:
                bridge.deliver(message)

    pump = threading.Thread(target=pump_replies, daemon=True)
    pump.start()

    def tool_call():
        seen["result"] = _handle_request_user_input(
            {"title": form["title"], "questions": form["questions"]},
            user_input_provider=bridge.request,
            task_id="task-headless",
            timeout_s=30,
        )

    thread = threading.Thread(target=tool_call)
    thread.start()
    message = host.read(timeout_s=10.0)
    assert message is not None and message.kind is MessageKind.USER_INPUT_REQUEST, message
    frame = dict(message.payload)
    assert frame["record"] == form, frame
    host.send(
        MessageKind.USER_INPUT_REPLY,
        {"request_id": frame["request_id"], "status": "submitted", "response": answer},
    )
    thread.join(timeout=10.0)
    assert not thread.is_alive(), "the tool call never resumed"
    assert json.loads(seen["result"]) == answer, seen
    assert "prompt_toolkit" not in sys.modules, "prompt_toolkit was imported"
    assert (
        "factory.core.cli_chat_user_input" not in sys.modules
    ), "the interactive input module was imported"
    print("HEADLESS_OK")
    """
)


def test_the_guest_user_input_path_needs_no_terminal_and_no_prompt_toolkit() -> None:
    env = dict(os.environ)
    env["IGOREK_FACTORY_HOME"] = str(Path(os.environ.get("TMPDIR", "/tmp")) / "igorek-headless-check")
    completed = subprocess.run(
        [sys.executable, "-c", HEADLESS_SCRIPT],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "HEADLESS_OK" in completed.stdout, completed.stdout + completed.stderr


# --------------------------------------------------------------------------- #
# 5. Guest runner seams
# --------------------------------------------------------------------------- #


def test_the_guest_task_forwards_the_provider_only_when_given(tmp_path: Path) -> None:
    task = GuestTask.from_wire(_guest_task_payload(tmp_path), guest_roots=CONFIG.guest_roots)
    assert "user_input_provider" not in task.executor_kwargs(lambda message: None)
    provider = lambda payload: ANSWER  # noqa: E731 - tiny test seam
    kwargs = task.executor_kwargs(lambda message: None, user_input_provider=provider)
    assert kwargs["user_input_provider"] is provider


def test_the_guest_runner_answers_a_reply_without_a_second_task(tmp_path: Path) -> None:
    # Given: a guest whose executor asks for input exactly once
    seen: dict[str, Any] = {}

    def executor(**kwargs: Any) -> dict[str, Any]:
        seen["answer"] = kwargs["user_input_provider"](FORM_PAYLOAD)
        return {"status": "ok", "output_text": "done", "usage": {}, "tool_calls_count": 1}

    guest = _start_guest(executor, poll_interval_s=0.01)
    try:
        guest.host.send(MessageKind.TASK, _guest_task_payload(tmp_path, task_id=TASK_ID))
        request = guest.host.recv_from_guest(timeout_s=3.0)
        assert request is not None and request.kind is MessageKind.USER_INPUT_REQUEST
        request_id = str(dict(request.payload)["request_id"])

        # When: the host replies on the live channel
        guest.host.send(
            MessageKind.USER_INPUT_REPLY,
            {"request_id": request_id, "status": USER_INPUT_SUBMITTED, "response": ANSWER},
        )
        messages = guest.collect()
    finally:
        guest.finish()

    # Then: the same run continues and completes with the answer
    assert seen["answer"] == ANSWER
    assert [message.kind for message in messages] == [MessageKind.RESULT]
    assert dict(messages[0].payload)["status"] == "ok"
    assert guest.exit_codes == [0]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
