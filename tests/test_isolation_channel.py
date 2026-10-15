"""TDD suite for the versioned vsock host<->guest channel (plan todo 5).

The transport seam is an in-process ``socketpair``: no AF_VSOCK device is
required. A real-vsock smoke test is guarded by env and skips loudly.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from factory.core.isolation.channel import (
    PROTOCOL_VERSION,
    ChannelConfig,
    ChannelError,
    ChannelMessage,
    ChannelServer,
    Frame,
    JsonValue,
    MessageKind,
    PeerCid,
    PeerIdentity,
    PeerSession,
    SessionId,
    SessionMap,
    guest_connect,
    open_vsock_listener,
    read_frame,
    write_frame,
    _peer_cid,
)

CONFIG = ChannelConfig(max_frame_bytes=4096)
CID = PeerCid(3)
SESSION = SessionId("sess-a")

KIND_PAYLOADS: list[tuple[MessageKind, dict[str, JsonValue]]] = [
    (MessageKind.TASK, {"text": "hello"}),
    (MessageKind.EVENT, {"event": "tool.result"}),
    (MessageKind.APPROVAL_REQUEST, {"tool": "Write", "target": "/workspace/a.txt"}),
    (MessageKind.APPROVAL_DECISION, {"decision": "allow_once"}),
    (MessageKind.USER_INPUT_REQUEST, {"question": "ok?"}),
    (MessageKind.USER_INPUT_REPLY, {"answer": "yes"}),
    (MessageKind.WEB_REQUEST, {"request_id": "12345678-1234-1234-1234-123456789abc", "operation": "search", "args": {"query": "safe web lookup", "limit": 5}}),
    (MessageKind.WEB_REPLY, {"request_id": "12345678-1234-1234-1234-123456789abc", "result": "{\"success\":true}"}),
    (MessageKind.SESSION_SEARCH_REQUEST, {"request_id": "12345678-1234-1234-1234-123456789abc", "args": {"query": "workspace mount", "limit": 3, "role_filter": "user,assistant", "mode": "hybrid"}}),
    (MessageKind.SESSION_SEARCH_REPLY, {"request_id": "12345678-1234-1234-1234-123456789abc", "result": "{\"success\":true,\"semantic_available\":true}"}),
    (MessageKind.VAULT_STORE_REQUEST, {"request_id": "12345678-1234-1234-1234-123456789abc", "args": {"source_path": "token.txt", "name": "Service token", "kind": "secret"}}),
    (MessageKind.VAULT_STORE_REPLY, {"request_id": "12345678-1234-1234-1234-123456789abc", "result": "{\"ok\":true}"}),
    (MessageKind.MEMORY_FORGET_REQUEST, {"request_id": "12345678-1234-1234-1234-123456789abc", "target": "Вес Игоря — 103.4 кг"}),
    (MessageKind.MEMORY_FORGET_REPLY, {"request_id": "12345678-1234-1234-1234-123456789abc", "result": "{\"changed\":true}"}),
    (MessageKind.STEER_REQUEST, {"request_id": "12345678-1234-1234-1234-123456789abc"}),
    (MessageKind.STEER_REPLY, {"request_id": "12345678-1234-1234-1234-123456789abc", "items": [{"text": "continue", "message_id": "m1", "input_files": ["/workspace/.igorek_uploads/x.png"], "selected_skills": []}]}),
    (MessageKind.CANCEL, {"reason": "user"}),
    (MessageKind.RESULT, {"status": "ok", "output": "done"}),
    (MessageKind.ERROR, {"reason": "boom"}),
    (MessageKind.HOST_OP, {"op": "runtime.status", "args": {}}),
    (MessageKind.HEARTBEAT, {"state": "alive"}),
]


def test_protocol_version_carries_the_heartbeat_wire_change() -> None:
    # A mixed host/guest pair must fail closed at the handshake, so the new
    # heartbeat kind shipped under a bumped protocol version.
    assert PROTOCOL_VERSION == 2


def test_peer_cid_comes_from_kernel_peer_address() -> None:
    class KernelPeer:
        def getpeername(self) -> tuple[int, int]:
            return (3, 5005)

        def getsockopt(self, *_args: object) -> None:
            raise AssertionError("getsockopt must not be used for VSOCK peer CID")

    assert _peer_cid(KernelPeer()) == PeerCid(3)  # type: ignore[arg-type]


def test_peer_cid_rejects_malformed_kernel_address() -> None:
    class BadPeer:
        def getpeername(self) -> str:
            return "guest-supplied"

    with pytest.raises(ChannelError, match="vsock_peer_invalid"):
        _peer_cid(BadPeer())  # type: ignore[arg-type]


def _pair(config: ChannelConfig = CONFIG) -> tuple[PeerSession, PeerSession]:
    """Two endpoints over one socketpair (the transport seam)."""
    left, right = socket.socketpair()
    return PeerSession(left, config, PeerIdentity()), PeerSession(right, config, PeerIdentity())


def _run(target: Callable[[], Any]) -> tuple[threading.Thread, list[BaseException]]:
    """Run target in a thread, surfacing (never swallowing) its exceptions."""
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            target()
        except BaseException as exc:  # reported to the test
            errors.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread, errors


def _read_raw(sock: socket.socket) -> Frame | None:
    return read_frame(sock, max_frame_bytes=CONFIG.max_frame_bytes, timeout_s=2.0)


def _session_harness(
    guest_action: Callable[[socket.socket], None],
) -> tuple[PeerSession, list[BaseException]]:
    """Accept one guest in the caller's thread, run its action, return the host session."""
    sessions = SessionMap()
    sessions.assign(CID, SESSION)
    server = ChannelServer(CONFIG, sessions)
    srv_sock, cli_sock = socket.socketpair()
    thread, errors = _run(lambda: guest_action(cli_sock))
    host = server.handle_connection(srv_sock, CID)
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert host is not None
    return host, errors


@pytest.mark.parametrize(("kind", "payload"), KIND_PAYLOADS)
def test_each_kind_round_trips_both_directions(kind: MessageKind, payload: dict[str, JsonValue]) -> None:
    # Given: two endpoints over a socketpair
    host, guest = _pair()
    try:
        # When: each side sends one message of this kind
        host.send(kind, payload)
        inbound = guest.read(timeout_s=2.0)
        guest.send(kind, payload)
        back = host.read(timeout_s=2.0)
        # Then: kind and payload survive the frame codec in both directions
        assert inbound is not None and back is not None
        assert (inbound.kind, dict(inbound.payload)) == (kind, payload)
        assert (back.kind, dict(back.payload)) == (kind, payload)
    finally:
        host.close()
        guest.close()


def test_hello_with_future_version_is_rejected_and_closed() -> None:
    # Given: a host listener with a mapped peer CID
    sessions = SessionMap()
    sessions.assign(CID, SESSION)
    server = ChannelServer(CONFIG, sessions)
    srv_sock, cli_sock = socket.socketpair()
    replies: list[Frame | None] = []

    def guest() -> None:
        write_frame(cli_sock, {"kind": "hello", "version": PROTOCOL_VERSION + 98})
        replies.append(_read_raw(cli_sock))
        replies.append(_read_raw(cli_sock))

    # When: the guest greets with an unsupported protocol version
    thread, errors = _run(guest)
    session = server.handle_connection(srv_sock, CID)
    thread.join(timeout=3.0)

    # Then: hello_ack{accepted:false} arrives, then a clean EOF, no server error
    assert session is None
    assert errors == []
    ack = replies[0]
    assert ack is not None
    assert (ack["kind"], ack["accepted"], ack["reason"]) == (
        "hello_ack",
        False,
        "protocol_version_mismatch",
    )
    assert ack["version"] == PROTOCOL_VERSION
    assert replies[1] is None


def test_unmapped_peer_cid_is_rejected() -> None:
    # Given: a listener whose SessionMap has no entry for the observed CID
    server = ChannelServer(CONFIG, SessionMap())
    srv_sock, cli_sock = socket.socketpair()
    replies: list[Frame | None] = []

    def guest() -> None:
        write_frame(cli_sock, {"kind": "hello", "version": PROTOCOL_VERSION})
        replies.append(_read_raw(cli_sock))

    # When: the unknown peer completes the handshake
    thread, errors = _run(guest)
    session = server.handle_connection(srv_sock, PeerCid(9))
    thread.join(timeout=3.0)

    # Then: the host refuses the peer instead of guessing a session
    assert session is None
    assert errors == []
    ack = replies[0]
    assert ack is not None and ack["accepted"] is False
    assert ack["reason"] == "unknown_peer_cid"


def test_peer_cid_and_session_id_are_mapped_both_ways() -> None:
    # Given: a peer CID assigned to one session
    sessions = SessionMap()
    sessions.assign(CID, SESSION)

    # When/Then: the mapping answers from the CID and back from the session id
    assert sessions.session_for_cid(CID) == SESSION
    assert sessions.session_for_cid(PeerCid(77)) is None

    server = ChannelServer(CONFIG, sessions)
    srv_sock, cli_sock = socket.socketpair()
    observed: list[SessionId | None] = []

    def guest() -> None:
        endpoint = guest_connect(cli_sock, CONFIG)
        observed.append(endpoint.session_id)

    thread, errors = _run(guest)
    host = server.handle_connection(srv_sock, CID)
    thread.join(timeout=3.0)

    # The guest learns the session id the host assigned to its CID, never its own
    assert errors == []
    assert host is not None and host.session_id == SESSION
    assert observed == [SESSION]
    assert server.session_for(SESSION) is host
    host.close()


def test_closed_session_is_evicted_so_a_resumed_session_reconnects_fresh() -> None:
    # Given: a registered session whose endpoint a teardown closed
    sessions = SessionMap()
    sessions.assign(CID, SESSION)
    server = ChannelServer(CONFIG, sessions)
    srv_sock, cli_sock = socket.socketpair()

    def guest() -> None:
        guest_connect(cli_sock, CONFIG)

    thread, errors = _run(guest)
    host = server.handle_connection(srv_sock, CID)
    thread.join(timeout=3.0)
    assert host is not None
    host.close()

    # Then: the dead endpoint is never handed out as a live connection
    assert server.session_for(SESSION) is None

    # When: the resumed session reconnects on the same session id
    srv2, cli2 = socket.socketpair()

    def resumed_guest() -> None:
        guest_connect(cli2, CONFIG)

    thread2, errors2 = _run(resumed_guest)
    resumed = server.handle_connection(srv2, CID)
    thread2.join(timeout=3.0)

    # Then: the fresh endpoint is live and registered, not the closed one
    assert resumed is not None and resumed is not host
    assert server.session_for(SESSION) is resumed
    assert errors == [] and errors2 == []
    resumed.close()


def test_non_json_frame_is_answered_with_error_and_closed() -> None:
    # Given: a listener and a peer that sends bytes that are not JSON
    server = ChannelServer(CONFIG, SessionMap())
    srv_sock, cli_sock = socket.socketpair()
    replies: list[Frame | None] = []

    def guest() -> None:
        body = b"definitely not json"
        cli_sock.sendall(struct.pack(">I", len(body)) + body)
        replies.append(_read_raw(cli_sock))
        replies.append(_read_raw(cli_sock))

    # When: the malformed frame is processed
    thread, errors = _run(guest)
    session = server.handle_connection(srv_sock, CID)
    thread.join(timeout=3.0)

    # Then: an error frame explains it and the connection closes cleanly
    assert session is None
    assert errors == []
    reply = replies[0]
    assert reply is not None and reply["kind"] == "error"
    payload = reply["payload"]
    assert isinstance(payload, dict) and payload["reason"] == "malformed_json"
    assert replies[1] is None


def test_oversized_frame_is_rejected_before_the_body_is_read() -> None:
    # Given: a listener configured with a small frame ceiling
    server = ChannelServer(ChannelConfig(max_frame_bytes=64), SessionMap())
    srv_sock, cli_sock = socket.socketpair()
    replies: list[Frame | None] = []

    def guest() -> None:
        cli_sock.sendall(struct.pack(">I", 100_000))  # header only, no body
        replies.append(_read_raw(cli_sock))
        replies.append(_read_raw(cli_sock))

    # When: the frame header announces a frame above the ceiling
    thread, errors = _run(guest)
    session = server.handle_connection(srv_sock, CID)
    thread.join(timeout=3.0)

    # Then: the host refuses without buffering the body and closes
    assert session is None
    assert errors == []
    reply = replies[0]
    assert reply is not None and reply["kind"] == "error"
    payload = reply["payload"]
    assert isinstance(payload, dict) and payload["reason"] == "frame_too_large"
    assert replies[1] is None


def test_truncated_frame_closes_cleanly_without_server_error() -> None:
    # Given: a peer that dies in the middle of a declared frame
    server = ChannelServer(CONFIG, SessionMap())
    srv_sock, cli_sock = socket.socketpair()

    def guest() -> None:
        cli_sock.sendall(struct.pack(">I", 128))
        cli_sock.shutdown(socket.SHUT_WR)

    # When: the body never arrives
    thread, errors = _run(guest)
    session = server.handle_connection(srv_sock, CID)
    thread.join(timeout=3.0)

    # Then: the host gives up on the peer without raising
    assert session is None
    assert errors == []


@pytest.mark.parametrize("kind", [MessageKind.APPROVAL_REQUEST, MessageKind.EVENT])
def test_approval_context_can_mention_host_paths_but_actionable_paths_stay_scoped(
    kind: MessageKind,
) -> None:
    # Given: a Bash approval for a guest /tmp canary whose prior transcript
    # happens to mention an absolute host path
    host, guest = _pair()
    record: dict[str, JsonValue] = {
        "tool": "Bash",
        "tool_use_id": "call_canary",
        "target": "mkdir -p /tmp/kvm-approval-smoke && git add canary.txt",
        "input": {"command": "mkdir -p /tmp/kvm-approval-smoke && git add canary.txt"},
        "signature": "approval-signature",
        "approval_context_messages": [
            {"role": "assistant", "content": "/home/example/AI/IgorekAgentFactory"}
        ],
    }
    if kind is MessageKind.EVENT:
        record["kind"] = "tool.approval_required"
    payload: dict[str, JsonValue] = {"record": record}
    try:
        # When: the guest sends the real approval request or matching trace event
        guest.send(kind, payload)
        received = host.read(timeout_s=2.0)

        # Then: inert transcript text crosses, but is not interpreted as a host path
        assert received is not None
        assert received.kind is kind
        assert dict(received.payload) == payload

        # And: moving the host path into the actionable tool input is still denied
        unsafe_record = dict(record)
        unsafe_record["input"] = {"file_path": "/home/example/.ssh/id_ed25519"}
        with pytest.raises(ChannelError) as caught:
            guest.send(kind, {"record": unsafe_record})
        assert caught.value.reason == "payload_host_path_denied"

        # And: ordinary trace records keep the original whole-envelope path check
        if kind is MessageKind.EVENT:
            with pytest.raises(ChannelError) as trace_caught:
                guest.send(
                    MessageKind.EVENT,
                    {"record": {"kind": "tool.result", "path": "/home/example/.ssh/id_ed25519"}},
                )
            assert trace_caught.value.reason == "payload_host_path_denied"
    finally:
        host.close()
        guest.close()


def test_unknown_kind_is_answered_with_error_and_the_connection_continues() -> None:
    # Given: a handshaken guest that sends an unknown message kind
    observed: list[Frame | None] = []

    def guest(sock: socket.socket) -> None:
        endpoint = guest_connect(sock, CONFIG)
        write_frame(sock, {"kind": "bogus_kind", "seq": 1, "payload": {}})
        observed.append(_read_raw(sock))
        endpoint.send(MessageKind.TASK, {"text": "after error"})

    # When: the host reader processes the hostile frame
    host, errors = _session_harness(guest)

    # Then: an error frame is returned and later valid traffic still flows
    assert errors == []
    reply = observed[0]
    assert reply is not None and reply["kind"] == "error"
    payload = reply["payload"]
    assert isinstance(payload, dict) and payload["reason"] == "unknown_kind"
    follow_up = host.recv_from_guest(timeout_s=3.0)
    assert follow_up is not None and follow_up.kind is MessageKind.TASK
    assert follow_up.payload["text"] == "after error"


@pytest.mark.parametrize(
    ("kind", "payload", "reason"),
    [
        (MessageKind.TASK, {"command": "rm -rf /"}, "payload_shell_key_denied"),
        (MessageKind.APPROVAL_REQUEST, {"tool": "Bash", "input": {"cmd": "id"}}, "payload_shell_key_denied"),
        (MessageKind.TASK, {"cwd": "/home/example/secret"}, "payload_host_path_denied"),
        (MessageKind.TASK, {"cwd": "~/secret"}, "payload_host_path_denied"),
        (MessageKind.TASK, {"cwd": "/workspace/../etc/passwd"}, "payload_host_path_denied"),
        (MessageKind.HOST_OP, {"op": "shell.exec", "args": {}}, "host_op_shell_denied"),
        (MessageKind.HOST_OP, {"op": "runtime status", "args": {}}, "host_op_invalid_name"),
        (MessageKind.HOST_OP, {"op": "exec", "args": {}}, "host_op_shell_denied"),
    ],
)
def test_outbound_payload_policy_denies_shell_and_host_paths(
    kind: MessageKind, payload: dict[str, JsonValue], reason: str
) -> None:
    # Given: a connected endpoint
    host, guest = _pair()
    try:
        # When/Then: a forbidden payload is refused with a stable reason code
        with pytest.raises(ChannelError) as caught:
            host.send(kind, payload)
        assert caught.value.reason == reason
    finally:
        host.close()
        guest.close()


def test_steer_reply_rejects_host_paths_in_user_text() -> None:
    host, guest = _pair()
    try:
        with pytest.raises(ChannelError) as caught:
            host.send(
                MessageKind.STEER_REPLY,
                {
                    "request_id": "12345678-1234-1234-1234-123456789abc",
                    "items": [{"text": "/home/example/.ssh/id_ed25519"}],
                },
            )
        assert caught.value.reason == "payload_host_path_denied"
    finally:
        host.close()
        guest.close()


def test_task_tool_schemas_and_history_are_not_shell_keys() -> None:
    # Given: a task whose inert sections carry a Bash tool schema and history
    host, guest = _pair()
    payload = {
        "enabled_tools": [
            {
                "name": "Bash",
                "description": "Executes a bash command.",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            }
        ],
        "history_messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
                ],
            }
        ],
    }
    try:
        # When: the task is sent
        host.send(MessageKind.TASK, payload)

        # Then: it crosses (a parameter name is not a shell instruction) ...
        frame = guest.read(timeout_s=3.0)
        assert frame is not None and frame.kind is MessageKind.TASK
        # ... while a bare shell key is still refused
        with pytest.raises(ChannelError) as caught:
            host.send(MessageKind.TASK, {"command": "rm -rf /"})
        assert caught.value.reason == "payload_shell_key_denied"
    finally:
        host.close()
        guest.close()


def test_approved_bash_resume_metadata_crosses_the_channel() -> None:
    # Given: a host-authorized Bash call and its resume context
    host, guest = _pair()
    try:
        # When: the host resumes the approved call through the ordinary TASK path
        host.send(
            MessageKind.TASK,
            {
                "task_metadata": {
                    "approved_tool_calls": ['{"name":"Bash","input":{"command":"cd /workspace && pytest"}}'],
                    "forced_tool_call": {"name": "Bash", "input": {"command": "cd /workspace && pytest"}},
                    "approval_context_messages": [
                        {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}}]}
                    ],
                }
            },
        )

        # Then: it is delivered to the guest intact
        frame = guest.read(timeout_s=3.0)
        assert frame is not None and frame.kind is MessageKind.TASK
        assert frame.payload["task_metadata"]["forced_tool_call"]["input"]["command"] == "cd /workspace && pytest"

        # A host path in the same inert section remains forbidden
        with pytest.raises(ChannelError) as caught:
            host.send(MessageKind.TASK, {"task_metadata": {"cwd": "/home/example/.ssh"}})
        assert caught.value.reason == "payload_host_path_denied"
    finally:
        host.close()
        guest.close()


def test_approved_write_resume_carries_an_opaque_file_body() -> None:
    # Given: an approved Write whose body opens with a comment marker
    host, guest = _pair()
    body = "/* app.css */\nbody { margin: 0; }\n"
    try:
        host.send(
            MessageKind.TASK,
            {
                "task_metadata": {
                    "forced_tool_call": {
                        "name": "Write",
                        "input": {
                            "file_path": "/workspace/factory/web_ui/static/render.js",
                            "content": body,
                        },
                    }
                }
            },
        )
        frame = guest.read(timeout_s=3.0)
        assert frame is not None and frame.kind is MessageKind.TASK
        assert frame.payload["task_metadata"]["forced_tool_call"]["input"]["content"] == body

        # An actionable file target outside the guest roots is still denied
        with pytest.raises(ChannelError) as caught:
            host.send(
                MessageKind.TASK,
                {
                    "task_metadata": {
                        "forced_tool_call": {
                            "name": "Write",
                            "input": {"file_path": "/home/example/.ssh/id_ed25519", "content": body},
                        }
                    }
                },
            )
        assert caught.value.reason == "payload_host_path_denied"
    finally:
        host.close()
        guest.close()


def test_approval_resume_treats_workspace_prefixed_tool_output_as_transcript() -> None:
    # Given: an approval context whose tool output starts with a workspace path.
    # The whole transcript is text; executable paths remain checked separately.
    host, guest = _pair()
    try:
        host.send(
            MessageKind.TASK,
            {
                "task_metadata": {
                    "forced_tool_call": {
                        "name": "Bash",
                        "input": {"command": "cd /workspace && pytest"},
                        "approval_context_messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "content": [
                                            [[[[[[[[[["/workspace\nPWD=/workspace\n"]]]]]]]]]]
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                }
            },
        )
        frame = guest.read(timeout_s=3.0)
        assert frame is not None and frame.kind is MessageKind.TASK

        # A path used as an actionable file target is still denied.
        with pytest.raises(ChannelError) as caught:
            host.send(
                MessageKind.TASK,
                {
                    "task_metadata": {
                        "forced_tool_call": {
                            "name": "Read",
                            "input": {"file_path": "/home/example/.ssh/id_ed25519"},
                        }
                    }
                },
            )
        assert caught.value.reason == "payload_host_path_denied"

        # The transcript exemption is specific to the typed metadata location;
        # the same key nested in actionable tool input remains path-checked.
        with pytest.raises(ChannelError) as caught:
            host.send(
                MessageKind.TASK,
                {
                    "task_metadata": {
                        "forced_tool_call": {
                            "name": "Custom",
                            "input": {
                                "approval_context_messages": "/home/example/.ssh/id_ed25519"
                            },
                        }
                    }
                },
            )
        assert caught.value.reason == "payload_host_path_denied"
    finally:
        host.close()
        guest.close()


def test_approved_bash_resume_legacy_section_crosses_channel_but_stays_path_checked() -> None:
    # Given: the production builder's legacy approval_resume shape for an
    # explicitly host-approved Bash invocation.
    host, guest = _pair()
    try:
        host.send(
            MessageKind.TASK,
            {
                "approval_resume": {
                    "forced_tool_call": {
                        "name": "Bash",
                        "input": {"command": "cd /workspace && pytest"},
                        "tool_use_id": "call-approved",
                        "reason": "approved by the user",
                    },
                    "approved_tool_calls": [
                        '{"name":"Bash","input":{"command":"cd /workspace && pytest"}}'
                    ],
                }
            },
        )
        frame = guest.read(timeout_s=3.0)
        assert frame is not None and frame.kind is MessageKind.TASK
        assert frame.payload["approval_resume"]["forced_tool_call"]["input"]["command"] == (
            "cd /workspace && pytest"
        )

        # A host path remains denied even in this typed resume section.
        with pytest.raises(ChannelError) as caught:
            host.send(
                MessageKind.TASK,
                {
                    "approval_resume": {
                        "forced_tool_call": {
                            "name": "Read",
                            "input": {"file_path": "/home/example/.ssh/id_ed25519"},
                        }
                    }
                },
            )
        assert caught.value.reason == "payload_host_path_denied"

        # An untyped top-level command is still refused.
        with pytest.raises(ChannelError) as caught:
            host.send(MessageKind.TASK, {"command": "id"})
        assert caught.value.reason == "payload_shell_key_denied"
    finally:
        host.close()
        guest.close()


def test_shell_keys_in_active_task_fields_remain_denied() -> None:
    host, guest = _pair()
    try:
        with pytest.raises(ChannelError) as caught:
            host.send(MessageKind.TASK, {"system_prompt": {"command": "run on host"}})
        assert caught.value.reason == "payload_shell_key_denied"
    finally:
        host.close()
        guest.close()


def test_inbound_host_path_is_rejected_without_dropping_the_connection() -> None:
    # Given: a hostile guest that writes a raw (unvalidated) host-path payload
    observed: list[Frame | None] = []

    def guest(sock: socket.socket) -> None:
        guest_connect(sock, CONFIG)
        write_frame(sock, {"kind": "task", "seq": 1, "payload": {"cwd": "/home/example/secret"}})
        observed.append(_read_raw(sock))
        write_frame(sock, {"kind": "task", "seq": 2, "payload": {"text": "still alive"}})

    # When: the receiving side validates the frame
    host, errors = _session_harness(guest)

    # Then: the host rejects the path and keeps serving valid traffic
    assert errors == []
    reply = observed[0]
    assert reply is not None and reply["kind"] == "error"
    payload = reply["payload"]
    assert isinstance(payload, dict) and payload["reason"] == "payload_host_path_denied"
    follow_up = host.recv_from_guest(timeout_s=3.0)
    assert follow_up is not None and follow_up.payload["text"] == "still alive"


def test_task_round_trip_yields_ordered_event_then_result() -> None:
    # Given: a handshaken guest that submits a task
    sessions = SessionMap()
    sessions.assign(CID, SESSION)
    server = ChannelServer(CONFIG, sessions)
    srv_sock, cli_sock = socket.socketpair()
    received: list[tuple[ChannelMessage, ChannelMessage]] = []

    def guest() -> None:
        endpoint = guest_connect(cli_sock, CONFIG)
        endpoint.send(MessageKind.TASK, {"text": "do it"})
        first = endpoint.read(timeout_s=5.0)
        second = endpoint.read(timeout_s=5.0)
        assert first is not None and second is not None
        received.append((first, second))

    # When: the guest submits the task and the host answers event then result
    thread, errors = _run(guest)
    host = server.handle_connection(srv_sock, CID)
    assert host is not None
    task = host.recv_from_guest(timeout_s=3.0)
    assert task is not None and task.kind is MessageKind.TASK
    assert task.payload["text"] == "do it"
    host.send(MessageKind.EVENT, {"event": "tool.result", "step": 1})
    host.send(MessageKind.RESULT, {"status": "ok", "output": "done"})
    thread.join(timeout=5.0)

    # Then: the guest observes them in order, not merely without an exception
    assert not thread.is_alive()
    assert errors == []
    first, second = received[0]
    assert [first.kind, second.kind] == [MessageKind.EVENT, MessageKind.RESULT]
    assert first.seq < second.seq
    assert second.payload == {"status": "ok", "output": "done"}
    host.close()


def test_client_disconnect_mid_stream_ends_the_host_session_without_hanging() -> None:
    # Given: a guest that sends one task and vanishes
    def guest(sock: socket.socket) -> None:
        endpoint = guest_connect(sock, CONFIG)
        endpoint.send(MessageKind.TASK, {"text": "then vanish"})
        sock.close()

    # When: the host consumes the stream after the abrupt disconnect
    host, errors = _session_harness(guest)
    first = host.recv_from_guest(timeout_s=3.0)
    closed = host.recv_from_guest(timeout_s=3.0)

    # Then: the task arrives, the reader thread terminates and closes the session
    assert errors == []
    assert first is not None and first.kind is MessageKind.TASK
    assert closed is None
    assert host.closed is True


def test_privileged_and_invalid_ports_are_refused() -> None:
    with pytest.raises(ChannelError) as caught:
        ChannelConfig(port=80)
    assert caught.value.reason == "invalid_port"
    with pytest.raises(ChannelError) as caught:
        ChannelConfig(max_frame_bytes=0)
    assert caught.value.reason == "invalid_max_frame_bytes"


def test_port_defaults_to_non_privileged_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IGOREK_ISOLATION_VSOCK_PORT", raising=False)
    assert ChannelConfig().port >= 1024
    monkeypatch.setenv("IGOREK_ISOLATION_VSOCK_PORT", "45000")
    assert ChannelConfig().port == 45000
    monkeypatch.setenv("IGOREK_ISOLATION_VSOCK_PORT", "not-a-port")
    assert ChannelConfig().port >= 1024


@pytest.mark.skipif(
    os.environ.get("IGOREK_ISOLATION_VSOCK_SMOKE") != "1",
    reason="real AF_VSOCK smoke; set IGOREK_ISOLATION_VSOCK_SMOKE=1 inside a virtio-vsock guest",
)
def test_real_vsock_listener_smoke() -> None:
    if not hasattr(socket, "AF_VSOCK"):
        pytest.skip("AF_VSOCK is not available on this host")
    listener = open_vsock_listener(ChannelConfig().port)
    try:
        assert listener.fileno() >= 0
    finally:
        listener.close()


def test_read_frame_reports_an_idle_window_as_a_non_fatal_timeout() -> None:
    # Given: a connected peer that stays silent
    left, right = socket.socketpair()
    try:
        # When/Then: the header read times out with nothing consumed -> idle, non-fatal
        with pytest.raises(ChannelError) as caught:
            read_frame(left, max_frame_bytes=CONFIG.max_frame_bytes, timeout_s=0.1)
        assert caught.value.reason == "read_timeout"
        assert caught.value.fatal is False
        assert caught.value.detail == "after 0 of 4 bytes"
    finally:
        left.close()
        right.close()


def test_read_frame_treats_a_mid_frame_timeout_as_a_fatal_truncated_frame() -> None:
    # Given: a peer that sends a partial header then freezes
    left, right = socket.socketpair()
    try:
        right.sendall(b"\x00\x00")
        # When/Then: retrying would desync the stream, so the timeout stays fatal
        with pytest.raises(ChannelError) as caught:
            read_frame(left, max_frame_bytes=CONFIG.max_frame_bytes, timeout_s=0.1)
        assert caught.value.reason == "truncated_frame"
        assert caught.value.fatal is True
    finally:
        left.close()
        right.close()


def test_host_read_loop_survives_an_idle_window_and_delivers_the_next_frame() -> None:
    # Given: a guest that is silent for longer than the channel io timeout
    config = ChannelConfig(max_frame_bytes=4096, io_timeout_s=0.15)

    def guest(sock: socket.socket) -> None:
        endpoint = guest_connect(sock, config)
        time.sleep(0.4)
        endpoint.send(MessageKind.EVENT, {"event": "tool.result"})

    sessions = SessionMap()
    sessions.assign(CID, SESSION)
    server = ChannelServer(config, sessions)
    srv_sock, cli_sock = socket.socketpair()
    thread, errors = _run(lambda: guest(cli_sock))
    host = server.handle_connection(srv_sock, CID)

    # When: the host reads across the idle window
    try:
        thread.join(timeout=3.0)
        # Then: the session stays open and the later frame is delivered
        assert not thread.is_alive() and errors == []
        assert host is not None
        message = host.recv_from_guest(timeout_s=2.0)
        assert message is not None and message.kind is MessageKind.EVENT
        assert host.closed is False
    finally:
        host.close()
        srv_sock.close()
        cli_sock.close()


def test_host_read_loop_still_closes_on_a_real_disconnect() -> None:
    # Given: a guest that completes the handshake then genuinely disconnects
    config = ChannelConfig(max_frame_bytes=4096, io_timeout_s=0.15)

    def guest(sock: socket.socket) -> None:
        guest_connect(sock, config)
        sock.close()

    sessions = SessionMap()
    sessions.assign(CID, SESSION)
    server = ChannelServer(config, sessions)
    srv_sock, cli_sock = socket.socketpair()
    thread, errors = _run(lambda: guest(cli_sock))
    host = server.handle_connection(srv_sock, CID)
    thread.join(timeout=3.0)

    try:
        # When/Then: the reader sees EOF, not a timeout, and closes the session
        deadline = time.monotonic() + 2.0
        while host is not None and not host.closed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert errors == []
        assert host is not None and host.closed is True
        assert host.recv_from_guest(timeout_s=1.0) is None
    finally:
        if host is not None:
            host.close()
        srv_sock.close()
        cli_sock.close()


def test_concurrent_senders_keep_frame_sequence_and_boundaries_intact() -> None:
    left, right = socket.socketpair()
    session = PeerSession(left, CONFIG)
    writers = [
        threading.Thread(
            target=lambda label=label: [
                session.send(MessageKind.CANCEL, {"reason": f"{label}:{index}"})
                for index in range(40)
            ],
            daemon=True,
        )
        for label in range(4)
    ]
    for writer in writers:
        writer.start()
    received = [read_frame(right, max_frame_bytes=CONFIG.max_frame_bytes, timeout_s=5.0) for _ in range(160)]
    for writer in writers:
        writer.join(timeout=5.0)
        assert not writer.is_alive()
    right.close()
    session.close()
    assert all(frame is not None for frame in received)
    assert [frame["seq"] for frame in received if frame is not None] == list(range(1, 161))


def test_listener_reservation_is_exclusive_and_released_on_close(monkeypatch):
    from factory.core.isolation import channel
    from unittest.mock import Mock
    listener = Mock()
    factory = Mock(return_value=listener)
    monkeypatch.setattr(channel, 'open_vsock_listener', factory)
    server = ChannelServer(CONFIG, SessionMap())
    server.reserve_listener()
    server.reserve_listener()
    factory.assert_called_once_with(CONFIG.port)
    server.close()
    server.close()
    listener.close.assert_called_once()
    with pytest.raises(ChannelError, match='channel_closed'):
        server.reserve_listener()


def test_listener_bind_failure_is_synchronous_and_retryable(monkeypatch):
    from factory.core.isolation import channel
    from unittest.mock import Mock
    listener = Mock()
    factory = Mock(side_effect=[ChannelError('vsock_bind_failed'), listener])
    monkeypatch.setattr(channel, 'open_vsock_listener', factory)
    server = ChannelServer(CONFIG, SessionMap())
    with pytest.raises(ChannelError, match='vsock_bind_failed'):
        server.reserve_listener()
    assert server._listener is None
    server.reserve_listener()
    assert server._listener is listener
    server.close()
