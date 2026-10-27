"""Versioned framed JSON channel between the host and one isolation guest.

Wire protocol (AF_VSOCK, SOCK_STREAM):

1. Handshake: the guest sends ``{"kind": "hello", "version": <int>}``; the host
   answers ``{"kind": "hello_ack", "accepted": <bool>, "version": <int>,
   "session_id": <str|null>, "reason": <str>}`` and closes every rejection.
2. Messages: ``{"kind": <str>, "seq": <int>, "payload": {...}}`` framed as a
   four-byte big-endian length prefix followed by UTF-8 JSON.

The host maps the VSOCK peer CID it observes to the session id it created for
that VM (:class:`SessionMap`); a guest never chooses its own session id.

The payload policy is structural, never heuristic: shell-command keys are
denied outright (the host never needs a raw shell string across the boundary),
every absolute path in a payload must live under a guest root (the host never
accepts a path that names its own filesystem), and ``host_op`` names must be
dotted lowercase identifiers that are not shell verbs.

One narrow exception (plan todos 8/9/10): the top-level
:data:`DISPLAY_ENVELOPE_KEY` of an ``event``, ``approval_request`` or
``user_input_request`` frame is an inert display envelope. The host appends an
event record to a JSONL trace the mobile client renders, reads an approval
request as the executor's own blocked-tool payload, and reads a user-input
request as the form payload the existing user-input store publishes - in every
case it never executes or interprets anything inside the envelope, so a faithful
record may legitimately contain shell-named tool-input keys (``command``,
``argv``). Shell-key denial is therefore skipped INSIDE that envelope only; depth
and guest-root path rules still apply. Approval history is inert transcript text,
so host-looking paths in that field alone do not invalidate a request; paths in
the actionable tool input/target and every other message kind remain checked.
"""

# Keep framing, payload validation and channel lifecycle at one boundary.

from __future__ import annotations

import json
import logging
import os
import posixpath
import queue
import re
import socket
import struct
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, NewType, Protocol, assert_never

logger = logging.getLogger(__name__)

PROTOCOL_VERSION: Final[int] = 2
MIN_UNPRIVILEGED_PORT: Final[int] = 1024
DEFAULT_VSOCK_PORT: Final[int] = 43001
DEFAULT_MAX_FRAME_BYTES: Final[int] = 4 * 1024 * 1024
DEFAULT_HELLO_TIMEOUT_S: Final[float] = 10.0
DEFAULT_IO_TIMEOUT_S: Final[float] = 300.0
#: Guest liveness cadence: the guest runner emits one ``heartbeat`` this often
#: while a task runs. The host uses it only to tell a frozen guest from a busy
#: one; a heartbeat never carries task data and never resets the result deadline.
HEARTBEAT_INTERVAL_S: Final[float] = 10.0

PeerCid = NewType("PeerCid", int)
SessionId = NewType("SessionId", str)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type Frame = Mapping[str, JsonValue]

#: Payload marker of an ``event`` frame that carries one guest trace record
#: (produced by ``guest_runner``; relayed by ``factory.core.isolation.relay``).
TRACE_EVENT_KIND: Final[str] = "trace"
#: Inert display envelope: the top-level key of an ``event`` payload whose value
#: is a trace record for the mobile client, of an ``approval_request`` payload
#: whose value is the executor's blocked-tool payload (host relay:
#: ``factory.core.isolation.approval``), and of a ``user_input_request`` payload
#: whose value is the form payload the existing user-input store publishes (host
#: relay: ``factory.core.isolation.user_input``). See the module docstring for
#: why shell keys are legitimate here; a unit test pins it to the guest-side
#: ``guest_runner.TRACE_RECORD_KEY`` so the two halves cannot drift.
DISPLAY_ENVELOPE_KEY: Final[str] = "record"
#: ``user_input_reply`` statuses (plan todo 10): ``submitted`` carries the
#: answers back into the waiting tool call, ``cancelled`` ends the guest run with
#: ``result{status:"canceled"}``. The guest bridge and the host relay share these
#: wire values, so they live here instead of in either half.
USER_INPUT_SUBMITTED: Final[str] = "submitted"
USER_INPUT_CANCELLED: Final[str] = "cancelled"

_SHELL_KEYS: Final[frozenset[str]] = frozenset(
    {"argv", "bash", "cmd", "command", "exec", "executable", "script", "shell", "shell_command"}
)
#: Host-authored TASK sections whose nested shell-key names are data: schemas,
#: conversation history and typed task/approval metadata. They remain depth-
#: bounded and path-checked; executable approval payloads are validated again
#: by the guest's typed rehydration and host-issued approval records.
_INERT_TASK_KEYS: Final[frozenset[str]] = frozenset(
    {"enabled_tools", "history_messages", "task_metadata", "approval_resume"}
)
#: Opaque file text: a file body, an edited snippet or its replacement is data,
#: never a host path. The whole string is checked as one path, so a body that
#: opens with ``/*`` or ``//`` (any JS/CSS file) read as a host path and dropped
#: the frame - the guest's blocked call then never reached the host relay and
#: the task failed closed with ``approval_request_missing``. Only actionable
#: fields (``file_path``/``target``/``command``) stay path-checked.
_OPAQUE_TEXT_KEYS: Final[frozenset[str]] = frozenset(
    {"content", "old_string", "new_string"}
)
_SHELL_OP_NAMES: Final[frozenset[str]] = frozenset(
    {"bash", "cmd", "command", "exec", "run", "run_command", "sh", "shell", "system"}
)
_OP_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_WINDOWS_PATH_PATTERN: Final = re.compile(r"^[A-Za-z]:[\\/]")
_MAX_PAYLOAD_DEPTH: Final[int] = 8
#: Tool schemas nest deeper than an ordinary payload; inert TASK sections get a
#: larger (still bounded) depth so a schema cannot be used to blow the stack.
_MAX_INERT_DEPTH: Final[int] = 64
_FRAME_HEADER: Final = struct.Struct(">I")
_GUEST_ROOTS: Final[tuple[str, ...]] = ("/workspace", "/tmp")
_SO_PEER_CID: Final[int] = getattr(socket, "SO_VM_SOCKETS_PEER_HOST_VM_ID", 0)


class ChannelError(Exception):
    """Typed channel failure; ``reason`` is a stable machine-readable code.

    ``fatal`` marks violations that close the connection (framing/parse errors);
    non-fatal ones are answered with an ``error`` frame and keep serving.
    """

    def __init__(self, reason: str, detail: str = "", *, fatal: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.fatal = fatal

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


class SocketLike(Protocol):
    """Minimal socket surface the channel needs (tests use ``socketpair``)."""

    def recv(self, size: int) -> bytes: ...
    def sendall(self, data: bytes) -> None: ...
    def settimeout(self, value: float | None) -> None: ...
    def shutdown(self, how: int) -> None: ...
    def close(self) -> None: ...


def _default_port() -> int:
    """Resolve ``IGOREK_ISOLATION_VSOCK_PORT`` or the built-in non-privileged port."""
    raw = os.environ.get("IGOREK_ISOLATION_VSOCK_PORT", "").strip()
    if not raw:
        return DEFAULT_VSOCK_PORT
    try:
        return int(raw)
    except ValueError:
        logger.warning("IGOREK_ISOLATION_VSOCK_PORT=%r is not an integer; using %d", raw, DEFAULT_VSOCK_PORT)
        return DEFAULT_VSOCK_PORT


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    """Dial settings and payload policy shared by host and guest endpoints."""

    port: int = field(default_factory=_default_port)
    protocol_version: int = PROTOCOL_VERSION
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES
    hello_timeout_s: float = DEFAULT_HELLO_TIMEOUT_S
    io_timeout_s: float = DEFAULT_IO_TIMEOUT_S
    guest_roots: tuple[str, ...] = _GUEST_ROOTS
    forbidden_payload_keys: frozenset[str] = _SHELL_KEYS

    def __post_init__(self) -> None:
        if self.port < MIN_UNPRIVILEGED_PORT:
            raise ChannelError("invalid_port", f"{self.port} is privileged; use >= {MIN_UNPRIVILEGED_PORT}")
        if self.max_frame_bytes <= 0:
            raise ChannelError("invalid_max_frame_bytes", str(self.max_frame_bytes))


class MessageKind(StrEnum):
    """Every post-handshake message kind the channel can carry."""

    TASK = "task"
    EVENT = "event"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_DECISION = "approval_decision"
    USER_INPUT_REQUEST = "user_input_request"
    USER_INPUT_REPLY = "user_input_reply"
    WEB_REQUEST = "web_request"
    WEB_REPLY = "web_reply"
    SESSION_SEARCH_REQUEST = "session_search_request"
    SESSION_SEARCH_REPLY = "session_search_reply"
    GOAL_REQUEST = "goal_request"
    GOAL_REPLY = "goal_reply"
    VAULT_STORE_REQUEST = "vault_store_request"
    VAULT_STORE_REPLY = "vault_store_reply"
    MEMORY_FORGET_REQUEST = "memory_forget_request"
    MEMORY_FORGET_REPLY = "memory_forget_reply"
    STEER_REQUEST = "steer_request"
    STEER_REPLY = "steer_reply"
    CANCEL = "cancel"
    RESULT = "result"
    ERROR = "error"
    HOST_OP = "host_op"
    HEARTBEAT = "heartbeat"


def display_envelope_key(kind: MessageKind) -> str | None:
    """Key whose value is inert display data for this kind (``None`` = strict walk).

    ``event`` carries one trace record; ``approval_request`` (plan todo 9)
    carries the executor's blocked-tool payload; ``user_input_request`` (plan
    todo 10) carries the form payload the existing user-input store publishes.
    All are read, never executed, so shell-key denial is skipped inside that one
    key only.
    """
    match kind:
        case MessageKind.EVENT | MessageKind.APPROVAL_REQUEST | MessageKind.USER_INPUT_REQUEST:
            return DISPLAY_ENVELOPE_KEY
        case (
            MessageKind.TASK
            | MessageKind.APPROVAL_DECISION
            | MessageKind.USER_INPUT_REPLY
            | MessageKind.WEB_REQUEST
            | MessageKind.WEB_REPLY
            | MessageKind.SESSION_SEARCH_REQUEST
            | MessageKind.SESSION_SEARCH_REPLY
            | MessageKind.GOAL_REQUEST
            | MessageKind.GOAL_REPLY
            | MessageKind.VAULT_STORE_REQUEST
            | MessageKind.VAULT_STORE_REPLY
            | MessageKind.MEMORY_FORGET_REQUEST
            | MessageKind.MEMORY_FORGET_REPLY
            | MessageKind.STEER_REQUEST
            | MessageKind.STEER_REPLY
            | MessageKind.CANCEL
            | MessageKind.RESULT
            | MessageKind.ERROR
            | MessageKind.HOST_OP
            | MessageKind.HEARTBEAT
        ):
            return None
        case _ as unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class ChannelMessage:
    """One validated protocol message."""

    kind: MessageKind
    seq: int
    payload: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class PeerIdentity:
    """Who is on the other end: observed VSOCK CID and host-owned session id."""

    peer_cid: PeerCid | None = None
    session_id: SessionId | None = None


def _narrow_json(value: Any) -> JsonValue:
    """Convert a ``json.loads`` product into the typed JSON tree."""
    match value:
        case str() | bool() | int() | float() | None:
            return value
        case list():
            return [_narrow_json(item) for item in value]
        case dict():
            return {str(key): _narrow_json(item) for key, item in value.items()}
        case _ as unreachable:
            raise ChannelError("malformed_json", f"unsupported value {type(unreachable).__name__}")


def _read_exact(sock: SocketLike, size: int, *, idle_timeout_ok: bool = False) -> bytes | None:
    """Read exactly ``size`` bytes; ``None`` on EOF before the first byte.

    A ``TimeoutError`` before *any* byte of this read was consumed is an idle
    window, not a protocol violation: the peer simply sent nothing yet. When
    ``idle_timeout_ok`` is set (only the frame header read does this) it is
    reported as a non-fatal ``read_timeout`` so a caller can keep waiting.

    A timeout *after* some bytes were consumed is a truncated frame (fatal):
    the stream position already moved past a partial frame, so retrying would
    misparse every byte that follows.
    """
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            chunk = sock.recv(remaining)
        except TimeoutError as exc:
            if idle_timeout_ok and remaining == size:
                raise ChannelError("read_timeout", f"after 0 of {size} bytes", fatal=False) from exc
            raise ChannelError("truncated_frame", f"{size - remaining} of {size} bytes (timeout)") from exc
        except OSError as exc:  # peer reset, pipe broken, or socket closed under us
            raise ChannelError("peer_disconnected", str(exc)) from exc
        if not chunk:
            if remaining == size:
                return None
            raise ChannelError("truncated_frame", f"{size - remaining} of {size} bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(
    sock: SocketLike,
    *,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
    timeout_s: float | None = None,
) -> Frame | None:
    """Read one framed JSON object; ``None`` on clean EOF, raises :class:`ChannelError`."""
    if timeout_s is not None:
        try:
            sock.settimeout(timeout_s)
        except OSError as exc:  # socket closed concurrently
            raise ChannelError("peer_disconnected", str(exc)) from exc
    header = _read_exact(sock, _FRAME_HEADER.size, idle_timeout_ok=True)
    if header is None:
        return None
    (size,) = _FRAME_HEADER.unpack(header)
    if size > max_frame_bytes:
        raise ChannelError("frame_too_large", f"{size} > {max_frame_bytes}")
    body = _read_exact(sock, size)
    if body is None:
        raise ChannelError("truncated_frame", f"0 of {size} bytes")
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ChannelError("malformed_json", str(exc)) from exc
    if not isinstance(decoded, dict):
        raise ChannelError("frame_not_object", type(decoded).__name__)
    return {str(key): _narrow_json(value) for key, value in decoded.items()}


def write_frame(sock: SocketLike, frame: Frame, *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> None:
    """Write one length-prefixed JSON frame."""
    body = json.dumps(frame, separators=(",", ":"), ensure_ascii=False).encode()
    if len(body) > max_frame_bytes:
        raise ChannelError("frame_too_large", f"{len(body)} > {max_frame_bytes}")
    try:
        sock.sendall(_FRAME_HEADER.pack(len(body)) + body)
    except OSError as exc:  # peer reset, pipe broken, or socket closed under us
        raise ChannelError("peer_unreachable", str(exc)) from exc


class _PayloadPolicy:
    """Recursive structural allow-list for one payload tree."""

    __slots__ = ("_config",)

    def __init__(self, config: ChannelConfig) -> None:
        self._config = config

    def validate(self, kind: MessageKind, payload: Mapping[str, JsonValue]) -> None:
        """Deny shell keys, host paths and untyped host operations."""
        if kind is MessageKind.HOST_OP:
            self._validate_host_op(payload)
        elif kind is MessageKind.WEB_REQUEST:
            self._validate_web_request(payload)
            return
        elif kind is MessageKind.WEB_REPLY:
            self._validate_web_reply(payload)
            return
        elif kind is MessageKind.SESSION_SEARCH_REQUEST:
            self._validate_session_search_request(payload)
            return
        elif kind is MessageKind.SESSION_SEARCH_REPLY:
            self._validate_session_search_reply(payload)
            return
        elif kind is MessageKind.GOAL_REQUEST:
            self._validate_goal_request(payload)
            return
        elif kind is MessageKind.GOAL_REPLY:
            self._validate_session_search_reply(payload)
            return
        elif kind is MessageKind.VAULT_STORE_REQUEST:
            from ..vault.agent_values import validate_request
            try:
                if set(payload) != {'request_id', 'args'} or not isinstance(payload.get('request_id'), str) \
                        or not re.fullmatch(r'[0-9a-f-]{36}', payload['request_id']):
                    raise ValueError()
                validate_request(payload.get('args'))
            except ValueError as exc:
                raise ChannelError('vault_store_request_invalid', 'invalid request', fatal=False) from exc
            return
        elif kind is MessageKind.VAULT_STORE_REPLY:
            self._validate_session_search_reply(payload)
            if len(str(payload['result']).encode()) > 4096:
                raise ChannelError('vault_store_reply_invalid', 'invalid reply', fatal=False)
            return
        elif kind is MessageKind.MEMORY_FORGET_REQUEST:
            self._validate_memory_forget_request(payload)
            return
        elif kind is MessageKind.MEMORY_FORGET_REPLY:
            self._validate_memory_forget_reply(payload)
            return
        elif kind is MessageKind.STEER_REQUEST:
            self._validate_steer_request(payload)
            return
        elif kind is MessageKind.STEER_REPLY:
            self._validate_steer_reply(payload)
            return
        envelope_key = display_envelope_key(kind)
        for key, value in payload.items():
            if kind is MessageKind.TASK and key in _INERT_TASK_KEYS:
                self._walk_inert(value, depth=0)
            elif envelope_key is not None and key == envelope_key:
                if kind is MessageKind.APPROVAL_REQUEST or (
                    kind is MessageKind.EVENT
                    and isinstance(value, dict)
                    and value.get("kind") == "tool.approval_required"
                ):
                    self._walk_approval_display(value, depth=0)
                else:
                    self._walk_display(value, depth=0)
            else:
                self._walk(key, value, depth=0)

    @staticmethod
    def _validate_web_request(payload: Mapping[str, JsonValue]) -> None:
        """Allow only typed search and extraction calls."""
        if set(payload) != {"request_id", "operation", "args"}:
            raise ChannelError("web_request_invalid", "unexpected request fields", fatal=False)
        request_id, operation, args = payload.get("request_id"), payload.get("operation"), payload.get("args")
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", request_id):
            raise ChannelError("web_request_invalid", "invalid request id", fatal=False)
        if operation == "search":
            if not isinstance(args, dict) or set(args) - {"query", "limit"}:
                raise ChannelError("web_request_invalid", "invalid search arguments", fatal=False)
            query, limit = args.get("query"), args.get("limit", 5)
            if not isinstance(query, str) or not query.strip() or len(query) > 4096:
                raise ChannelError("web_request_invalid", "invalid search query", fatal=False)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
                raise ChannelError("web_request_invalid", "invalid search limit", fatal=False)
        elif operation == "extract":
            if not isinstance(args, dict) or set(args) != {"urls"}:
                raise ChannelError("web_request_invalid", "invalid extract arguments", fatal=False)
            urls = args.get("urls")
            if not isinstance(urls, list) or not 1 <= len(urls) <= 10 or not all(isinstance(url, str) and len(url) <= 2048 for url in urls):
                raise ChannelError("web_request_invalid", "invalid extract URLs", fatal=False)
        else:
            raise ChannelError("web_request_invalid", "unsupported operation", fatal=False)

    @staticmethod
    def _validate_web_reply(payload: Mapping[str, JsonValue]) -> None:
        """Bound host tool output returned to the guest executor."""
        request_id, result = payload.get("request_id"), payload.get("result")
        if set(payload) != {"request_id", "result"} or not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", request_id) or not isinstance(result, str) or len(result.encode("utf-8")) > 512 * 1024:
            raise ChannelError("web_reply_invalid", "invalid web reply", fatal=False)

    @staticmethod
    def _validate_session_search_request(payload: Mapping[str, JsonValue]) -> None:
        request_id, args = payload.get("request_id"), payload.get("args")
        if (
            set(payload) != {"request_id", "args"}
            or not isinstance(request_id, str)
            or not re.fullmatch(r"[0-9a-f-]{36}", request_id)
            or not isinstance(args, dict)
            or set(args) - {"query", "limit", "role_filter", "mode"}
        ):
            raise ChannelError("session_search_request_invalid", "invalid request", fatal=False)
        query, limit = args.get("query", ""), args.get("limit", 3)
        role_filter = args.get("role_filter", "user,assistant")
        mode = args.get("mode", "hybrid")
        if not isinstance(query, str) or len(query) > 4096:
            raise ChannelError("session_search_request_invalid", "invalid query", fatal=False)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
            raise ChannelError("session_search_request_invalid", "invalid limit", fatal=False)
        if not isinstance(role_filter, str) or len(role_filter) > 64:
            raise ChannelError("session_search_request_invalid", "invalid role filter", fatal=False)
        if not isinstance(mode, str) or mode not in {"lexical", "semantic", "hybrid"}:
            raise ChannelError("session_search_request_invalid", "invalid search mode", fatal=False)

    @staticmethod
    def _validate_goal_request(payload: Mapping[str, JsonValue]) -> None:
        request_id, args = payload.get("request_id"), payload.get("args")
        if (set(payload) != {"request_id", "args"} or not isinstance(request_id, str)
                or not re.fullmatch(r"[0-9a-f-]{36}", request_id) or not isinstance(args, dict)):
            raise ChannelError("goal_request_invalid", "invalid request", fatal=False)
        operation = args.get("operation")
        if operation == "get" and set(args) == {"operation"}:
            return
        if (operation == "update" and set(args) == {"operation", "status"}
                and isinstance(args.get("status"), str) and args["status"] in {"complete", "blocked", "paused"}):
            return
        raise ChannelError("goal_request_invalid", "invalid goal action", fatal=False)

    @staticmethod
    def _validate_session_search_reply(payload: Mapping[str, JsonValue]) -> None:
        request_id, result = payload.get("request_id"), payload.get("result")
        if (
            set(payload) != {"request_id", "result"}
            or not isinstance(request_id, str)
            or not re.fullmatch(r"[0-9a-f-]{36}", request_id)
            or not isinstance(result, str)
            or len(result.encode("utf-8")) > 512 * 1024
        ):
            raise ChannelError("session_search_reply_invalid", "invalid reply", fatal=False)

    @staticmethod
    def _validate_memory_forget_request(payload: Mapping[str, JsonValue]) -> None:
        request_id, target = payload.get("request_id"), payload.get("target")
        if (
            set(payload) != {"request_id", "target"}
            or not isinstance(request_id, str)
            or not re.fullmatch(r"[0-9a-f-]{36}", request_id)
            or not isinstance(target, str)
            or not target.strip()
            or len(target) > 500
        ):
            raise ChannelError("memory_forget_request_invalid", "invalid request", fatal=False)

    @staticmethod
    def _validate_memory_forget_reply(payload: Mapping[str, JsonValue]) -> None:
        request_id, result = payload.get("request_id"), payload.get("result")
        if (
            set(payload) != {"request_id", "result"}
            or not isinstance(request_id, str)
            or not re.fullmatch(r"[0-9a-f-]{36}", request_id)
            or not isinstance(result, str)
            or len(result.encode("utf-8")) > 16 * 1024
        ):
            raise ChannelError("memory_forget_reply_invalid", "invalid reply", fatal=False)

    @staticmethod
    def _validate_steer_request(payload: Mapping[str, JsonValue]) -> None:
        request_id = payload.get("request_id")
        if (
            set(payload) not in ({"request_id"}, {"request_id", "prioritized_only"})
            or not isinstance(request_id, str)
            or not re.fullmatch(r"[0-9a-f-]{36}", request_id)
            or ("prioritized_only" in payload and not isinstance(payload["prioritized_only"], bool))
        ):
            raise ChannelError("steer_request_invalid", "invalid request", fatal=False)

    def _validate_steer_reply(self, payload: Mapping[str, JsonValue]) -> None:
        request_id, items = payload.get("request_id"), payload.get("items")
        if (
            set(payload) != {"request_id", "items"}
            or not isinstance(request_id, str)
            or not re.fullmatch(r"[0-9a-f-]{36}", request_id)
            or not isinstance(items, list)
            or len(items) > 100
            or not all(isinstance(item, dict) for item in items)
        ):
            raise ChannelError("steer_reply_invalid", "invalid reply", fatal=False)
        for item in items:
            if set(item) - {"text", "message_id", "input_files", "selected_skills"}:
                raise ChannelError("steer_reply_invalid", "unexpected item fields", fatal=False)
            text, message_id = item.get("text", ""), item.get("message_id", "")
            if not isinstance(text, str) or not isinstance(message_id, str):
                raise ChannelError("steer_reply_invalid", "invalid item text", fatal=False)
            self._validate_string(text)
            self._validate_string(message_id)
            for key in ("input_files", "selected_skills"):
                value = item.get(key, [])
                if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
                    raise ChannelError("steer_reply_invalid", f"invalid {key}", fatal=False)
            for value in item.get("input_files", []):
                self._validate_string(value)

    def _walk_inert(self, value: JsonValue, depth: int) -> None:
        """Walk an inert TASK section: no shell-key rule, bounded depth, path-checked."""
        if depth > _MAX_INERT_DEPTH:
            raise ChannelError("payload_too_deep", f"inert depth {depth}", fatal=False)
        match value:
            case str():
                self._validate_string(value)
            case list():
                for item in value:
                    self._walk_inert(item, depth + 1)
            case dict():
                for child_key, child in value.items():
                    if child_key == "approval_context_messages" and depth <= 1:
                        # Quoted conversation history is not an executable
                        # path-bearing field. Tool output can start with
                        # ``/workspace``; validating the whole text as one path
                        # rejects an otherwise valid approval resume.
                        self._walk_transcript(
                            child, depth + 1, max_depth=_MAX_INERT_DEPTH
                        )
                    elif child_key in _OPAQUE_TEXT_KEYS:
                        # A file body or an edited snippet is opaque data: it is
                        # never interpreted as a path by either side.
                        self._walk_transcript(
                            child, depth + 1, max_depth=_MAX_INERT_DEPTH
                        )
                    else:
                        self._walk_inert(child, depth + 1)
            case bool() | int() | float() | None:
                return
            case _ as unreachable:
                assert_never(unreachable)

    def _walk_display(self, value: JsonValue, depth: int) -> None:
        """Walk inert display data: shell-key rule skipped, depth and paths held."""
        if depth > _MAX_PAYLOAD_DEPTH:
            raise ChannelError("payload_too_deep", f"display depth {depth}", fatal=False)
        match value:
            case str():
                self._validate_string(value)
            case list():
                for item in value:
                    self._walk_display(item, depth + 1)
            case dict():
                for child_key, child in value.items():
                    if child_key in _OPAQUE_TEXT_KEYS:
                        # File bodies and edited snippets are opaque text, not
                        # paths: only actionable fields stay path-checked.
                        self._walk_transcript(child, depth + 1)
                    else:
                        self._walk_display(child, depth + 1)
            case bool() | int() | float() | None:
                return
            case _ as unreachable:
                assert_never(unreachable)

    def _walk_approval_display(self, value: JsonValue, depth: int) -> None:
        """Check actionable approval fields while treating prior chat as text.

        Approval payloads include prior conversation so an allowed decision can
        resume with context. That transcript can mention paths on the host, but
        it is never interpreted as a path or command by this channel. Keep the
        stricter guest-root check on the blocked tool input and every other
        field, and retain bounded recursion for the transcript itself.
        """
        if depth > _MAX_PAYLOAD_DEPTH:
            raise ChannelError("payload_too_deep", f"approval depth {depth}", fatal=False)
        match value:
            case str():
                self._validate_string(value)
            case list():
                for item in value:
                    self._walk_approval_display(item, depth + 1)
            case dict():
                for key, child in value.items():
                    if key == "approval_context_messages":
                        self._walk_transcript(child, depth + 1)
                    else:
                        self._walk_display(child, depth + 1)
            case bool() | int() | float() | None:
                return
            case _ as unreachable:
                assert_never(unreachable)

    def _walk_transcript(
        self, value: JsonValue, depth: int, *, max_depth: int = _MAX_PAYLOAD_DEPTH
    ) -> None:
        """Bound transcript structure without treating quoted text as paths."""
        if depth > max_depth:
            raise ChannelError("payload_too_deep", f"transcript depth {depth}", fatal=False)
        match value:
            case list():
                for item in value:
                    self._walk_transcript(item, depth + 1, max_depth=max_depth)
            case dict():
                for child in value.values():
                    self._walk_transcript(child, depth + 1, max_depth=max_depth)
            case str() | bool() | int() | float() | None:
                return
            case _ as unreachable:
                assert_never(unreachable)

    def _validate_host_op(self, payload: Mapping[str, JsonValue]) -> None:
        op = payload.get("op")
        if not isinstance(op, str) or not _OP_NAME_PATTERN.match(op):
            raise ChannelError("host_op_invalid_name", repr(op), fatal=False)
        if op in _SHELL_OP_NAMES or op.split(".")[-1] in _SHELL_OP_NAMES:
            raise ChannelError("host_op_shell_denied", op, fatal=False)
        args = payload.get("args", {})
        if not isinstance(args, dict):
            raise ChannelError("host_op_args_invalid", type(args).__name__, fatal=False)

    def _walk(self, key: str, value: JsonValue, depth: int) -> None:
        if key.lower() in self._config.forbidden_payload_keys:
            raise ChannelError("payload_shell_key_denied", key, fatal=False)
        if depth > _MAX_PAYLOAD_DEPTH:
            raise ChannelError("payload_too_deep", key, fatal=False)
        match value:
            case str():
                self._validate_string(value)
            case list():
                for item in value:
                    self._walk(key, item, depth + 1)
            case dict():
                for child_key, child in value.items():
                    self._walk(child_key, child, depth + 1)
            case bool() | int() | float() | None:
                return
            case _ as unreachable:
                assert_never(unreachable)

    def _validate_string(self, value: str) -> None:
        if value.startswith("~") or _WINDOWS_PATH_PATTERN.match(value):
            raise ChannelError("payload_host_path_denied", value, fatal=False)
        if not value.startswith("/"):
            return
        normalized = posixpath.normpath(value)
        if normalized == "/" or normalized.startswith(".."):
            raise ChannelError("payload_host_path_denied", value, fatal=False)
        if not any(normalized == root or normalized.startswith(f"{root}/") for root in self._config.guest_roots):
            raise ChannelError("payload_host_path_denied", value, fatal=False)


def payload_policy_violation(
    kind: MessageKind, payload: Mapping[str, JsonValue], config: ChannelConfig
) -> str | None:
    """The policy denial reason for one payload, or ``None`` when it would pass.

    Public seam for host-side pre-flight (plan todo 31): the extension collector
    checks each MCP server spec before the task frame is assembled, so one denied
    spec becomes a visible status instead of failing the whole dispatch.
    """
    try:
        _PayloadPolicy(config).validate(kind, payload)
    except ChannelError as exc:
        return exc.reason
    return None


def parse_message(frame: Frame, config: ChannelConfig) -> ChannelMessage:
    """Validate one post-handshake frame into a typed message."""
    kind_raw = frame.get("kind")
    if not isinstance(kind_raw, str):
        raise ChannelError("missing_kind", fatal=False)
    try:
        kind = MessageKind(kind_raw)
    except ValueError as exc:
        raise ChannelError("unknown_kind", kind_raw, fatal=False) from exc
    seq = frame.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool):
        raise ChannelError("invalid_seq", repr(seq), fatal=False)
    payload = frame.get("payload", {})
    if not isinstance(payload, dict):
        raise ChannelError("payload_not_object", type(payload).__name__, fatal=False)
    _PayloadPolicy(config).validate(kind, payload)
    return ChannelMessage(kind=kind, seq=seq, payload=payload)


class SessionMap:
    """Thread-safe peer-CID <-> session-id mapping owned by the host.

    One CID maps to exactly one session and one session to exactly one CID. A
    CID already owned by another session is refused (``peer_cid_conflict``)
    instead of being silently rebound, so a second session can never take over
    the host's routing entry for a live sibling's guest (todo 23); rebinding the
    *same* session to a new CID drops the stale entry.
    """

    def __init__(self) -> None:
        self._by_cid: dict[PeerCid, SessionId] = {}
        self._by_session: dict[SessionId, PeerCid] = {}
        self._lock = threading.Lock()

    def assign(self, peer_cid: PeerCid, session_id: SessionId) -> None:
        """Bind the CID the host allocated for one VM to its session.

        Raises :class:`ChannelError` with reason ``peer_cid_conflict`` when
        ``peer_cid`` already belongs to a different session; the existing
        binding is left untouched in that case.
        """
        with self._lock:
            owner = self._by_cid.get(peer_cid)
            if owner is not None and owner != session_id:
                raise ChannelError(
                    "peer_cid_conflict",
                    f"vsock CID {peer_cid} is bound to {owner!r}",
                )
            previous = self._by_session.get(session_id)
            if previous is not None and previous != peer_cid:
                self._by_cid.pop(previous, None)
            self._by_cid[peer_cid] = session_id
            self._by_session[session_id] = peer_cid

    def session_for_cid(self, peer_cid: PeerCid) -> SessionId | None:
        """Session the host created for this CID, or ``None`` when unknown."""
        with self._lock:
            return self._by_cid.get(peer_cid)

    def sessions(self) -> tuple[SessionId, ...]:
        """Sessions currently bound to a peer CID (read-only snapshot).

        The reaper uses this to find routing entries no live VM owns any more;
        it never mutates the map.
        """
        with self._lock:
            return tuple(self._by_session.keys())

    def release(self, session_id: SessionId) -> PeerCid | None:
        """Forget a finished session; returns the CID it was bound to."""
        with self._lock:
            peer_cid = self._by_session.pop(session_id, None)
            if peer_cid is not None:
                self._by_cid.pop(peer_cid, None)
            return peer_cid


class _BoundedInbox:
    """Apply backpressure by both frame count and serialized payload bytes."""

    def __init__(self, max_bytes: int, max_messages: int = 64):
        self.max_bytes = max_bytes
        self.maxsize = max_messages
        self._items = deque()
        self._bytes = 0
        self._closed = False
        self._condition = threading.Condition()

    def put(self, message: ChannelMessage) -> bool:
        size = len(json.dumps({'kind': message.kind.value, 'seq': message.seq,
                               'payload': message.payload}, ensure_ascii=False, separators=(',', ':')).encode())
        if size > self.max_bytes:
            raise ChannelError('inbox_frame_too_large')
        with self._condition:
            self._condition.wait_for(lambda: self._closed or
                (len(self._items) < self.maxsize and self._bytes + size <= self.max_bytes))
            if self._closed:
                return False
            self._items.append((message, size))
            self._bytes += size
            self._condition.notify_all()
            return True

    def get(self, timeout=None):
        with self._condition:
            ready = self._condition.wait_for(lambda: self._items or self._closed, timeout=timeout)
            if not ready:
                raise queue.Empty
            if not self._items:
                return None
            message, size = self._items.popleft()
            self._bytes -= size
            self._condition.notify_all()
            return message

    def qsize(self):
        with self._condition:
            return len(self._items)

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class PeerSession:
    """One framed-JSON endpoint: host view of a guest, or the guest client.

    Mutable by design: it owns the socket, a monotonic ``seq`` and the closed
    flag; ``send`` is safe to call from several threads.
    """

    __slots__ = ("_closed", "_config", "_send_lock", "_seq", "_sock", "identity", "inbox")

    def __init__(self, sock: SocketLike, config: ChannelConfig, identity: PeerIdentity = PeerIdentity()) -> None:
        self._sock = sock
        self._config = config
        self.identity = identity
        self.inbox = _BoundedInbox(max(8 * 1024 * 1024, config.max_frame_bytes))
        self._send_lock = threading.Lock()
        self._seq = 0
        self._closed = False

    @property
    def session_id(self) -> SessionId | None:
        return self.identity.session_id

    @property
    def peer_cid(self) -> PeerCid | None:
        return self.identity.peer_cid

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, kind: MessageKind, payload: Mapping[str, JsonValue]) -> None:
        """Validate and write one message with the next sequence number."""
        _PayloadPolicy(self._config).validate(kind, payload)
        with self._send_lock:
            if self._closed:
                raise ChannelError("channel_closed")
            self._seq += 1
            frame: dict[str, JsonValue] = {"kind": str(kind), "seq": self._seq, "payload": dict(payload)}
            write_frame(self._sock, frame, max_frame_bytes=self._config.max_frame_bytes)

    def send_error(self, reason: str) -> None:
        """Reply with a protocol ``error`` frame; never echoes the offending value."""
        self.send(MessageKind.ERROR, {"reason": reason})

    def read(self, timeout_s: float | None = None) -> ChannelMessage | None:
        """Read and validate one inbound message; ``None`` on clean EOF."""
        effective = self._config.io_timeout_s if timeout_s is None else timeout_s
        frame = read_frame(self._sock, max_frame_bytes=self._config.max_frame_bytes, timeout_s=effective)
        if frame is None:
            return None
        return parse_message(frame, self._config)

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        """Host-side consumer: next guest message, or ``None`` after close/timeout."""
        effective = self._config.io_timeout_s if timeout_s is None else timeout_s
        try:
            return self.inbox.get(timeout=effective)
        except queue.Empty:
            return None

    def close(self) -> None:
        """Close the socket and wake any consumer; safe to call twice."""
        with self._send_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # the peer already closed; the close() below is still required
            self._sock.close()
        self.inbox.close()


class ChannelServer:
    """Host-side listener: one reader thread per guest and a peer-CID session map."""

    def __init__(self, config: ChannelConfig, sessions: SessionMap) -> None:
        self._config = config
        self._sessions = sessions
        self._active: dict[SessionId, PeerSession] = {}
        self._lock = threading.Lock()
        self._serve_lock = threading.Lock()
        self._listener_lock = threading.Lock()
        self._listener = None
        self._closed = threading.Event()
        self._handshake_slots = threading.BoundedSemaphore(16)

    def session_for(self, session_id: SessionId) -> PeerSession | None:
        """Live endpoint for a session, or ``None`` when not connected.

        A session whose endpoint was closed (teardown, guest disconnect) is
        evicted here: a parked-and-resumed session must not be handed its own
        dead endpoint, or the next dispatch would be sent into a closed channel.
        """
        with self._lock:
            session = self._active.get(session_id)
            if session is not None and session.closed:
                del self._active[session_id]
                return None
            return session

    def handle_connection(self, sock: SocketLike, peer_cid: PeerCid) -> PeerSession | None:
        """Negotiate the handshake; on accept register the session and start reading."""
        session_id = self._negotiate(sock, peer_cid)
        if session_id is None:
            _close_quietly(sock)
            return None
        session = PeerSession(sock, self._config, PeerIdentity(peer_cid=peer_cid, session_id=session_id))
        with self._lock:
            previous = self._active.get(session_id)
            if previous is None and len(self._active) >= 64:
                session.close()
                return None
            self._active[session_id] = session
        if previous is not None:
            previous.close()
        threading.Thread(
            target=self._read_loop, args=(session,), name=f"isolation-channel-{peer_cid}", daemon=True
        ).start()
        return session

    def reserve_listener(self) -> None:
        """Acquire the port synchronously before admitting any VM."""
        with self._listener_lock:
            if self._closed.is_set():
                raise ChannelError("channel_closed")
            if self._listener is None:
                self._listener = open_vsock_listener(self._config.port)

    def serve_forever(self, stop_event: threading.Event | None = None) -> None:
        """Accept AF_VSOCK guests until ``stop_event`` is set (real vsock only).

        Idempotent: only one caller ever binds the vsock port. The manager starts
        the listener lazily and a PoC/bench harness may also ask for it on the
        same server instance; the second caller returns immediately instead of
        failing the bind.
        """
        if not self._serve_lock.acquire(blocking=False):
            return
        try:
            self._serve_until(stop_event)
        finally:
            self._serve_lock.release()

    def _serve_until(self, stop_event: threading.Event | None) -> None:
        self.reserve_listener()
        with self._listener_lock:
            listener = self._listener
        if listener is None:
            return
        listener.settimeout(1.0)
        try:
            while not self._closed.is_set() and (stop_event is None or not stop_event.is_set()):
                try:
                    conn, _ = listener.accept()
                except TimeoutError:
                    continue  # re-check stop_event
                except OSError as exc:
                    if self._closed.is_set():
                        return
                    raise ChannelError("vsock_accept_failed", str(exc)) from exc
                try:
                    peer_cid = _peer_cid(conn)
                except ChannelError as exc:
                    logger.warning("isolation: rejected VSOCK peer without a kernel CID: %s", exc)
                    conn.close()
                    continue
                if not self._handshake_slots.acquire(blocking=False):
                    conn.close()
                    continue
                threading.Thread(
                    target=self._handle_accepted_connection,
                    args=(conn, peer_cid),
                    name=f"isolation-channel-{peer_cid}",
                    daemon=True,
                ).start()
        finally:
            with self._listener_lock:
                if self._listener is listener:
                    self._listener = None
            listener.close()

    def _handle_accepted_connection(self, sock: SocketLike, peer_cid: PeerCid) -> None:
        try:
            self.handle_connection(sock, peer_cid)
        finally:
            self._handshake_slots.release()

    def close(self) -> None:
        """Release the listener and close every live guest session."""
        self._closed.set()
        with self._listener_lock:
            listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        with self._lock:
            sessions = list(self._active.values())
            self._active.clear()
        for session in sessions:
            session.close()

    def _negotiate(self, sock: SocketLike, peer_cid: PeerCid) -> SessionId | None:
        version = self._config.protocol_version
        try:
            hello = read_frame(
                sock, max_frame_bytes=self._config.max_frame_bytes, timeout_s=self._config.hello_timeout_s
            )
        except ChannelError as exc:
            _write_quietly(sock, {"kind": "error", "seq": 0, "payload": {"reason": exc.reason}})
            return None
        if hello is None:
            return None
        hello_version = hello.get("version")
        if hello.get("kind") != "hello" or not isinstance(hello_version, int) or isinstance(hello_version, bool):
            return self._reject(sock, "hello_invalid")
        if hello_version != version:
            return self._reject(sock, "protocol_version_mismatch")
        session_id = self._sessions.session_for_cid(peer_cid)
        if session_id is None:
            return self._reject(sock, "unknown_peer_cid")
        _write_quietly(
            sock,
            {"kind": "hello_ack", "accepted": True, "version": version, "session_id": str(session_id), "reason": ""},
        )
        return session_id

    def _reject(self, sock: SocketLike, reason: str) -> None:
        _write_quietly(
            sock,
            {
                "kind": "hello_ack",
                "accepted": False,
                "version": self._config.protocol_version,
                "session_id": None,
                "reason": reason,
            },
        )
        return None

    def _read_loop(self, session: PeerSession) -> None:
        try:
            while not session.closed:
                try:
                    message = session.read()
                except ChannelError as exc:
                    if session.closed:
                        break  # closed locally while this read was in flight
                    if exc.reason == "read_timeout":
                        # Idle window: the guest sent nothing yet (e.g. a long
                        # guest-side tool is running). The connection is still
                        # alive, so keep waiting instead of closing the session.
                        logger.debug(
                            "isolation channel peer %s: idle read window elapsed; still connected",
                            session.peer_cid,
                        )
                        continue
                    logger.warning("isolation channel peer %s: %s", session.peer_cid, exc)
                    try:
                        session.send_error(exc.reason)
                    except ChannelError:
                        break  # peer is gone; nothing left to answer
                    if exc.fatal:
                        break
                    continue
                if message is None:
                    break
                if not session.inbox.put(message):
                    break
        finally:
            session.close()
            with self._lock:
                if self._active.get(session.session_id) is session:
                    self._active.pop(session.session_id, None)


def guest_connect(sock: SocketLike, config: ChannelConfig, *, version: int | None = None) -> PeerSession:
    """Guest-side handshake; raises ``ChannelError('handshake_rejected')`` on refusal."""
    requested = config.protocol_version if version is None else version
    write_frame(sock, {"kind": "hello", "version": requested})
    ack = read_frame(sock, max_frame_bytes=config.max_frame_bytes, timeout_s=config.hello_timeout_s)
    if ack is None:
        raise ChannelError("handshake_closed")
    if ack.get("kind") != "hello_ack" or ack.get("accepted") is not True:
        raise ChannelError("handshake_rejected", str(ack.get("reason") or "hello_ack_missing"))
    session_raw = ack.get("session_id")
    identity = PeerIdentity(session_id=SessionId(session_raw)) if isinstance(session_raw, str) else PeerIdentity()
    return PeerSession(sock, config, identity)


def open_vsock_listener(port: int) -> socket.socket:
    """Bind a non-privileged AF_VSOCK listener; fails closed when vsock is absent."""
    if port < MIN_UNPRIVILEGED_PORT:
        raise ChannelError("invalid_port", str(port))
    family = getattr(socket, "AF_VSOCK", None)
    if not isinstance(family, int):
        raise ChannelError("vsock_unavailable", "AF_VSOCK is not available on this host")
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        listener.bind((getattr(socket, "VMADDR_CID_ANY", -1), port))
    except OSError as exc:
        listener.close()
        raise ChannelError("vsock_bind_failed", f"port {port}: {exc}") from exc
    listener.listen(16)
    return listener


def _peer_cid(conn: socket.socket) -> PeerCid:
    """CID of the accepted VSOCK peer (host-observed, never guest-declared)."""
    try:
        address = conn.getpeername()
    except OSError as exc:
        raise ChannelError("vsock_peer_unavailable", str(exc)) from exc
    if (
        not isinstance(address, tuple)
        or len(address) != 2
        or not isinstance(address[0], int)
        or isinstance(address[0], bool)
    ):
        raise ChannelError("vsock_peer_invalid", repr(address))
    return PeerCid(address[0])


def _write_quietly(sock: SocketLike, frame: Frame) -> None:
    try:
        write_frame(sock, frame)
    except ChannelError:
        logger.info("isolation channel: peer closed before the reply was sent")


def _close_quietly(sock: SocketLike) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass
