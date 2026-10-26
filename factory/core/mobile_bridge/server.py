"""HTTP/SSE interface for the mobile bridge.

Transport: stdlib `ThreadingHTTPServer` (as in `factory/factory_api.py`), without
external web dependencies. Authentication: device token from `MobileStore`.
Live stream: SSE with resuming support for missed data via `after=<seq>`.
"""

from __future__ import annotations

import json
import queue
import ssl
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, quote

from .events import TraceJournaler
from .service import MobileConflict, MobileInvalid, MobileNotFound, MobileService
from .store import MobileStore, PairingAuthorityError
from .uploads import MAX_UPLOAD_BYTES
from .terminal import MobileTerminals
from .tls_server import TLSHandshakeMixin
from ..isolation.terminal_manager import TerminalError
from ...web_ui.terminal_endpoint import TerminalEndpoint
from ...web_ui.websocket import HandshakeRequest, WebSocket, WebSocketError, handshake_headers, validate_handshake
from ..isolation.host_op_runtime import build_host_op_gate
from ..opencode_quota import OpenCodeUsageError
from ..realtime_voice.service import VoiceService, VoiceUnavailable
from ..sudo_broker import SudoRequestError, deliver_decision, pending_requests
from ..diagnostics import IncidentJournal, classify_error, observation, ActivityTracker
from pathlib import Path
from uuid import uuid4
import re
import hashlib

API_PREFIX = "/mobile/v1"
SSE_HEARTBEAT_S = 15.0
MAX_BODY_BYTES = 1_000_000


@dataclass
class BridgeConfig:
    host: str = "127.0.0.1"
    port: int = 47821
    tls_cert: str = ""
    tls_key: str = ""
    profile: str = ""
    workdir: str = ""
    poll_interval_s: float = 1.5
    max_auth_failures: int = 10
    lockout_s: int = 300


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._empty = threading.Condition(self._lock)

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=2_000)
        with self._lock:
            self._subscribers.add(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(channel)
            self._empty.notify_all()

    def wait_empty(self, timeout: float) -> None:
        with self._empty:
            self._empty.wait_for(lambda: not self._subscribers, timeout=timeout)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            channels = list(self._subscribers)
        for channel in channels:
            try:
                channel.put_nowait(event)
            except queue.Full:
                continue

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


class AuthGuard:
    def __init__(self, *, max_failures: int = 10, lockout_s: int = 300) -> None:
        self.max_failures = max(1, int(max_failures))
        self.lockout_s = max(1, int(lockout_s))
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_locked(self, key: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        with self._lock:
            stamps = [stamp for stamp in self._failures.get(key, []) if current - stamp < self.lockout_s]
            self._failures[key] = stamps
            return len(stamps) >= self.max_failures

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        with self._lock:
            self._failures.setdefault(key, []).append(current)

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


class MobileBridge:
    def __init__(
        self,
        *,
        config: BridgeConfig | None = None,
        store: MobileStore | None = None,
        service: MobileService | None = None,
        broadcaster: Broadcaster | None = None,
        voice: VoiceService | None = None,
    ) -> None:
        self.config = config or BridgeConfig()
        self.store = store or MobileStore()
        self.service = service or MobileService(
            profile=self.config.profile,
            workdir=self.config.workdir,
            store=self.store,
            host_op_gate=build_host_op_gate(),
        )
        self.broadcaster = broadcaster or Broadcaster()
        self.terminals = MobileTerminals(self.service)
        # Voice mode: ephemeral secrets are stored in the bridge’s memory; the browser
        # receives only an opaque call_id.
        self.voice = voice or VoiceService()
        self.voice_devices = {}
        self.voice_devices_lock = threading.RLock()
        from ..mcp_tasks.service import McpTasks
        valid_owner_device = lambda identity: bool((record := self.store.get_device(identity)) and record.get('revoked_at') is None)
        self.mcp_tasks = McpTasks(consumer_valid=valid_owner_device, owner_device_check=valid_owner_device)
        self.journaler = TraceJournaler(self.store)
        self.journaler.add_live_hook(self.broadcaster.publish)
        # Session-message events (queued / prioritized / cancelled) are written
        # straight to the journal by the service, so they also need a live
        # mirror; otherwise the UI would wait for the next poll tick.
        self.service.add_event_hook(self.broadcaster.publish)
        self.auth = AuthGuard(
            max_failures=self.config.max_auth_failures,
            lockout_s=self.config.lockout_s,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen_sudo_requests: set[str] = set()
        self.diagnostics = IncidentJournal(Path(self.store.db_path).with_name('diagnostic-incidents.json') if self.store.db_path != ':memory:' else None)
        self._poll_ready_since = None
        self.activity = ActivityTracker()
        self.journaler.add_live_hook(self._record_diagnostic_event)

    def _record_diagnostic_event(self, event):
        self.activity.observe(event)
        payload = event.get('payload') or {}
        diagnostic = payload.get('diagnostic')
        if not isinstance(diagnostic, dict) or not diagnostic.get('code'):
            return
        if event.get('kind') == 'network.recovered':
            self.diagnostics.recover(owner=diagnostic.get('owner'), proof='operation_completed')
        elif event.get('kind') in {'network.retry', 'task.finish', 'task.error'}:
            self.diagnostics.observe(diagnostic)

    def revoke_device(self,device_id):
        revoked=self.store.revoke_device(device_id)
        with self.voice_devices_lock:
            calls=[call for call,owner in self.voice_devices.items() if owner==device_id]
            for call in calls:self.voice_devices.pop(call,None)
        for call in calls:self.voice.hangup(call)
        return revoked

    def tick(self) -> int:
        pending = pending_requests()
        current_ids = {str(item["request_id"]) for item in pending}
        for item in pending:
            if item["request_id"] not in self._seen_sudo_requests:
                self.broadcaster.publish({
                    "kind": "sudo.required",
                    "payload": {"request_id": item["request_id"]},
                    "session_id": None,
                })
        self._seen_sudo_requests = current_ids
        appended = self.journaler.poll()
        self.service.finalize_turns()
        schedules = getattr(self.service, "schedules", None)
        if schedules is not None:
            schedules.deliver_pending()
        # Requeue the same task when it finished before queued session messages
        # could be delivered to its active turn.
        self.service.resume_pending_steers()
        return appended

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mobile-bridge-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                if self._poll_ready_since is None:
                    self._poll_ready_since = time.monotonic()
                self.diagnostics.recover(owner='bridge-poll', stable_s=time.monotonic()-self._poll_ready_since)
            except Exception as error:
                self._poll_ready_since = None
                self.diagnostics.observe(observation(classify_error(error), component='bridge', operation='poll',
                    endpoint_role='runtime_to_tool', owner='bridge-poll', native_code=type(error).__name__))
            self._stop.wait(max(0.2, float(self.config.poll_interval_s)))


class _BridgeHTTPServer(TLSHandshakeMixin, ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], bridge: MobileBridge) -> None:
        self.bridge = bridge
        self.closing = threading.Event()
        super().__init__(address, handler)

    def server_close(self) -> None:
        self.closing.set()
        self.bridge.terminals.manager.close_all()
        self.bridge.broadcaster.wait_empty(2.0)
        super().server_close()


class MobileRequestHandler(BaseHTTPRequestHandler):
    server_version = "IgorekMobileBridge/1"
    protocol_version = "HTTP/1.1"

    @property
    def bridge(self) -> MobileBridge:
        return self.server.bridge  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_response(self, code, message=None):
        self._diagnostic_response_started = True
        super().send_response(code, message)

    def handle_one_request(self) -> None:
        # A standard disconnection of an SSE connection via the phone should not log a socket server traceback.
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, TimeoutError, ssl.SSLError):
            self.close_connection = True

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def _dispatch(self, method: str) -> None:
        self.http_request_id = 'req_' + uuid4().hex
        self._diagnostic_owner = 'bridge-http:'+hashlib.sha256((method+urlparse(self.path).path).encode()).hexdigest()[:24]
        candidate = str(self.headers.get('X-Client-Operation-Id') or '')
        self.client_operation_id = candidate if re.fullmatch(r'[A-Za-z0-9_-]{8,160}', candidate) else ''
        self._diagnostic_response_started = False
        try:
            self._dispatch_request(method)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, ssl.SSLError):
            self.close_connection = True
        except Exception as error:
            diagnostic = observation(classify_error(error), component='bridge', operation='read' if method in {'GET', 'HEAD'} else 'write',
                endpoint_role='client_to_bridge', owner=self._diagnostic_owner, http_request_id=self.http_request_id,
                client_operation_id=self.client_operation_id, native_code=type(error).__name__)
            self.bridge.diagnostics.observe(diagnostic)
            if not self._diagnostic_response_started:
                self._send_json(500, {'error': 'Internal server error', 'diagnostic': diagnostic})

    def _dispatch_request(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query or "")
        client = self.client_address[0] if self.client_address else "?"

        if path == f"{API_PREFIX}/health" and method == "GET":
            self._send_json(200, {"status": "ok", "subscribers": self.bridge.broadcaster.subscriber_count()})
            return

        if path == f"{API_PREFIX}/pair/complete" and method == "POST":
            self._handle_pair_complete(client)
            return

        if self.bridge.auth.is_locked(client):
            self._send_json(429, {"error": "too many failed attempts, try later"})
            return

        device = self._authenticate()
        if device is None:
            return

        if path == f'{API_PREFIX}/diagnostics' and method == 'GET':
            from importlib.metadata import version
            from ..cc_backend_trace import trace_health
            self._send_json(200, {**self.bridge.diagnostics.snapshot(), 'activity': self.bridge.activity.snapshot(), 'trace_health': trace_health(), 'version': version('igorek-agent-factory')})
            return

        if path == f"{API_PREFIX}/events" and method == "GET":
            self._handle_events(query,str(device["device_id"]))
            return

        parts = [segment for segment in path.split("/") if segment]
        prefix_parts = [segment for segment in API_PREFIX.split("/") if segment]
        if parts[: len(prefix_parts)] != prefix_parts:
            self._send_json(404, {"error": "not found"})
            return
        route = parts[len(prefix_parts) :]

        try:
            if route == ["emojis", "catalog"] and method == "GET":
                from ..pepe_emoji import catalog
                self._send_json(200, catalog())
                return
            if len(route) == 3 and route[:2] == ["emojis", "media"] and method == "GET":
                from ..pepe_emoji import emoji_media
                try:
                    data = emoji_media(route[2])
                except (ValueError, OSError):
                    self._send_json(404, {"error": "Emoji unavailable"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/gif")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "private, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
                return
            if len(route)==2 and route[0]=='operations' and method=='GET':
                if not re.fullmatch(r'[A-Za-z0-9_-]{8,160}',route[1]) or set(query)-{'session_id'}:
                    self._send_json(400,{'error':'Invalid operation reference'});return
                self._send_json(200,self.bridge.service.operation_receipt(route[1],str((query.get('session_id') or [''])[0])))
                return
            if route == ['browser', 'select'] and method == 'POST':
                payload = self._read_json_body()
                if query or not isinstance(payload, dict) or set(payload) != {'tab_id'} or not isinstance(payload['tab_id'], str) or not __import__('re').fullmatch('[-a-zA-Z0-9]{1,64}', payload['tab_id']):
                    self._send_json(400, {'error': 'invalid browser tab request'});return
                provider = getattr(self.bridge.service, 'browser_preview', None)
                if provider is None:
                    self._send_json(404, {'error': 'browser tab selection unavailable'});return
                result = provider.select(payload['tab_id'])
                header = str(self.headers.get('Authorization') or '')
                if self.bridge.store.authenticate(header[7:].strip()) is None:
                    self._send_json(401, {'error': 'device token is missing or invalid'});return
                self._send_json(200, result);return
            if route==['browser','preview'] and method=='GET':
                after=(query.get('after') or [''])[0]
                if set(query)-{'after'} or len(query.get('after',[]))>1 or (after and not __import__('re').fullmatch('[a-f0-9]{64}',after)):
                    self._send_json(400,{'error':'invalid browser preview request'});return
                provider=getattr(self.bridge.service,'browser_preview',None)
                result=provider.read(after) if provider is not None else {'available':False,'ready':False,'reason':'not_desktop'}
                header=str(self.headers.get('Authorization') or '')
                if self.bridge.store.authenticate(header[7:].strip()) is None:
                    self._send_json(401,{'error':'device token is missing or invalid'});return
                self._send_json(200,result);return
            if route and route[0] == 'schedules':
                controller = getattr(self.bridge.service,'schedules',None)
                if controller is None:
                    if route == ['schedules'] and method == 'GET':
                        self._send_json(200,{'available':False,'running':False,'schedules':[]})
                        return
                    self._send_json(409,{'error':'Расписание доступно в запущенном desktop'})
                    return
                try:
                    if len(route)==1 and method=='GET':result=controller.list()
                    elif len(route)==1 and method=='POST':result=controller.save(self._read_json_body())
                    elif len(route)==2 and method=='PATCH':
                        body=self._read_json_body()
                        result=controller.action(route[1],'enabled',body) if set(body)=={'enabled'} else controller.save(body,route[1])
                    elif len(route)==2 and method=='DELETE':result=controller.action(route[1],'delete')
                    elif len(route)==3 and route[2]=='run' and method=='POST':result=controller.action(route[1],'run',self._read_json_body())
                    else:
                        self._send_json(404,{'error':'not found'});return
                    self._send_json(200,result)
                except MobileNotFound as error:self._send_json(404,{'error':str(error)})
                except MobileInvalid as error:self._send_json(400,{'error':str(error)})
                except Exception:self._send_json(500,{'error':'Не удалось обработать расписание'})
                return
            if route == ['mcp'] and method == 'POST':
                body = self._read_json_body()
                if set(body) != {'action','fields'}:
                    self._send_json(400, {'error':'invalid_request'})
                    return
                if body.get('action') in {'configure', 'configure_device'} and not isinstance(self.connection, ssl.SSLSocket):
                    self._send_json(403, {'error':'HTTPS required'})
                    return
                result = self.bridge.mcp_tasks.dispatch(str(device['device_id']),body['action'],body['fields'])
                if not (current := self.bridge.store.get_device(str(device['device_id']))) or current.get('revoked_at') is not None:
                    self._send_json(401, {'error':'device revoked'})
                    return
                self._send_json(200, result)
                return
            if route == ["terminal"] and method == "GET":
                self._handle_terminal_ws(device)
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "terminal" and method == "POST":
                self._read_json_body()
                try:
                    ticket = self.bridge.terminals.ticket(route[1], str(device["device_id"]))
                except TerminalError as exc:
                    self._send_json(409, {"error": exc.reason})
                    return
                self._send_json(200, {"ticket": ticket})
                return
            if route == ["voice", "config"] and method == "GET":
                self._handle_voice_config()
                return
            if route == ["voice", "session"] and method == "POST":
                self._handle_voice_session(str(device["device_id"]))
                return
            if route == ["voice", "sdp"] and method == "POST":
                self._handle_voice_sdp(str(device["device_id"]))
                return
            if route == ["voice", "hangup"] and method == "POST":
                self._handle_voice_hangup(str(device["device_id"]))
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "activity" and method == "GET":
                from .activity import terminal_process
                processes = self.bridge.service.session_processes(route[1])
                processes += terminal_process(self.bridge.terminals.manager, route[1], "mobileterm-")
                self._send_json(200, {"processes": processes})
                return
            if len(route) == 5 and route[0] == "sessions" and route[2] == "activity" and route[4] == "stop" and method == "POST":
                self._read_json_body()
                self.bridge.service._session_workdir(route[1])
                from .activity import stop_terminal
                if route[3].startswith("mobileterm-"):
                    if not stop_terminal(self.bridge.terminals.manager, route[1], route[3], "mobileterm-"):
                        raise MobileNotFound("Терминал уже завершён или недоступен")
                    result = {"id": route[3], "requested": True}
                else:
                    result = self.bridge.service.stop_session_process(route[1], route[3])
                self._send_json(200, result)
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "cursor" and method == "GET":
                # Clients fetch the catch-up point from the bridge log, not directly from the database:
                # the database path and its state remain shared for all clients.
                self._send_json(200, {"seq": self.bridge.store.max_seq(session_id=route[1])})
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "files" and method == "GET":
                self._send_json(200, {"files": self.bridge.service.list_session_files(route[1]),
                                      "partial": bool(self.bridge.service.session_changes(route[1]).get("tracking_partial"))})
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "sources" and method == "GET":
                self._send_json(200, self.bridge.service.session_sources(route[1]))
                return
            if len(route) == 4 and route[0] == "sessions" and route[2] in {"files", "uploads"} and method == "GET":
                opener = self.bridge.service.open_session_upload if route[2] == "uploads" else self.bridge.service.open_session_file
                info, handle = opener(route[1], route[3])
                with handle:
                    from .files import download_range
                    range_header = self.headers.get("Range")
                    try:
                        start, end = download_range(range_header, info["size"])
                    except ValueError:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{info['size']}")
                        self.send_header("Content-Length", "0")
                        self.send_header("Cache-Control", "private, no-store")
                        self.end_headers()
                        return
                    length = max(0, end - start + 1)
                    self.connection.settimeout(45)
                    self.send_response(206 if range_header else 200)
                    self.send_header("Content-Type", info["mime_type"])
                    self.send_header("Content-Length", str(length))
                    self.send_header("Accept-Ranges", "bytes")
                    if range_header:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{info['size']}")
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(info["name"], safe=""))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "private, no-store")
                    self.end_headers()
                    handle.seek(start)
                    remaining = length
                    while remaining:
                        chunk = handle.read(min(65536, remaining))
                        if not chunk:
                            self.close_connection = True
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            if route == ["sessions"] and method == "POST":
                body = self._read_json_body()
                self._send_json(201, self.bridge.service.create_session(
                    profile=str(body.get("profile") or ""), cwd=str(body.get("cwd") or ""),
                    idempotency_key=str(body.get("idempotency_key") or ""),
                    activate_profile=body.get("activate_profile") is True,
                ))
                return
            if route == ["session-options"] and method == "GET":
                self._send_json(200, self.bridge.service.session_options())
                return
            if route == ["external-sessions"] and method == "GET":
                source = (query.get("source") or [""])[0]
                search = (query.get("query") or [""])[0]
                limit = int((query.get("limit") or ["250"])[0] or 250)
                refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
                self._send_json(
                    200,
                    self.bridge.service.external_sessions(
                        source=source,
                        query=search,
                        limit=limit,
                        refresh=refresh,
                    ),
                )
                return
            if len(route) == 3 and route[0] == "external-sessions" and method == "GET":
                offset = int((query.get("offset") or ["0"])[0] or 0)
                limit = int((query.get("limit") or ["400"])[0] or 400)
                self._send_json(
                    200,
                    self.bridge.service.get_external_session(
                        unquote(route[1]),
                        unquote(route[2]),
                        offset=offset,
                        limit=limit,
                    ),
                )
                return
            if (
                len(route) == 4
                and route[0] == "external-sessions"
                and route[3] == "import"
                and method == "POST"
            ):
                body = self._read_json_body()
                self._send_json(
                    201,
                    self.bridge.service.import_external_session(
                        unquote(route[1]),
                        unquote(route[2]),
                        profile=str(body.get("profile") or ""),
                        cwd=str(body.get("cwd") or ""),
                        idempotency_key=str(body.get("idempotency_key") or ""),
                    ),
                )
                return
            if route == ["extensions"] and method == "GET":
                profile = (query.get("profile") or [""])[0] or None
                self._send_json(200, self.bridge.service.extensions(profile=profile))
                return
            if len(route) == 3 and route[:2] == ["extensions", "skills"] and method == "PATCH":
                body = self._read_json_body()
                enabled = body.get("enabled")
                if not isinstance(enabled, bool):
                    raise MobileInvalid("enabled must be boolean")
                self._send_json(
                    200,
                    self.bridge.service.set_skill_enabled(unquote(route[2]), enabled=enabled),
                )
                return
            if route == ["sessions"] and method == "GET":
                limit = int((query.get("limit") or ["50"])[0] or 50)
                active_only = (query.get("active") or ["0"])[0] in {"1", "true", "yes"}
                archived_only = (query.get("archived") or ["0"])[0] in {"1", "true", "yes"}
                self._send_json(200, self.bridge.service.sessions_overview(
                    limit=limit, active_only=active_only, archived_only=archived_only,
                ))
                return
            if route == ["sessions"] and method == "PATCH":
                body = self._read_json_body()
                archived = body.get("archived")
                if not isinstance(archived, bool):
                    raise MobileInvalid("archived must be boolean")
                self._send_json(200, self.bridge.service.archive_sessions(archived=archived))
                return
            if len(route) == 2 and route[0] == "sessions" and method == "GET":
                self._send_json(200, self.bridge.service.get_session(route[1]))
                return
            if len(route) == 3 and route[0] == 'sessions' and route[2] == 'profile' and method == 'POST':
                body = self._read_json_body()
                if set(body) != {'profile'} or not isinstance(body['profile'], str):
                    raise MobileInvalid('profile must be a string')
                self._send_json(200, self.bridge.service.switch_session_profile(route[1], body['profile']))
                return
            if len(route) == 2 and route[0] == "sessions" and method == "PATCH":
                body = self._read_json_body()
                title = body.get("title") if "title" in body else None
                archived_raw = body.get("archived") if "archived" in body else None
                archived = bool(archived_raw) if archived_raw is not None else None
                pinned = body.get('pinned') if 'pinned' in body else None
                if 'pinned' in body and not isinstance(pinned, bool):
                    raise MobileInvalid('pinned must be boolean')
                self._send_json(200, self.bridge.service.update_session(
                    route[1], title=title, archived=archived, pinned=pinned,
                ))
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "events" and method == "GET":
                after_seq = int((query.get("after") or ["0"])[0] or 0)
                limit = int((query.get("limit") or ["500"])[0] or 500)
                self._send_json(
                    200,
                    {
                        "events": self.bridge.service.session_events(
                            route[1], after_seq=after_seq, limit=limit
                        )
                    },
                )
                return
            if len(route) == 4 and route[0] == "sessions" and route[2:4] == ["changes", "review"] and method == "GET":
                self._send_json(200, self.bridge.service.session_changes_review(
                    route[1], mode=(query.get("mode") or ["session"])[0],
                    base=(query.get("base") or [""])[0], path=(query.get("path") or [""])[0], revision=(query.get("revision") or [""])[0], include_untracked=(query.get("include_untracked") or ["0"])[0] == "1",
                ))
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "changes" and method == "GET":
                self._send_json(200, self.bridge.service.session_changes(route[1]))
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "agents" and method == "GET":
                self._send_json(200, self.bridge.service.agent_sessions(route[1]))
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "subagents" and method == "GET":
                limit = int((query.get("limit") or ["200"])[0] or 200)
                self._send_json(
                    200,
                    self.bridge.service.list_subagents(route[1], limit=limit),
                )
                return
            if (
                len(route) == 5
                and route[0] == "sessions"
                and route[2] == "subagents"
                and route[4] == "cancel"
                and method == "POST"
            ):
                self._send_json(
                    200,
                    self.bridge.service.cancel_subagent(
                        route[1], unquote(route[3])
                    ),
                )
                return
            if (
                len(route) == 4
                and route[0] == "sessions"
                and route[2] == "changes"
                and route[3] == "diff"
                and method == "GET"
            ):
                path_arg = (query.get("path") or [""])[0]
                self._send_json(
                    200, self.bridge.service.session_changes_diff(route[1], path_arg)
                )
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "checkpoints" and method == "GET":
                limit = int((query.get("limit") or ["50"])[0] or 50)
                offset = int((query.get("offset") or ["0"])[0] or 0)
                self._send_json(
                    200,
                    self.bridge.service.session_checkpoints(
                        route[1], limit=limit, offset=offset
                    ),
                )
                return
            if (
                len(route) == 5
                and route[0] == "sessions"
                and route[2] == "checkpoints"
                and route[4] == "changes"
                and method == "GET"
            ):
                self._send_json(
                    200, self.bridge.service.session_checkpoint_changes(route[1], route[3])
                )
                return
            if (
                len(route) == 5
                and route[0] == "sessions"
                and route[2] == "checkpoints"
                and route[4] == "diff"
                and method == "GET"
            ):
                path_arg = (query.get("path") or [""])[0]
                self._send_json(
                    200,
                    self.bridge.service.session_checkpoint_diff(route[1], route[3], path_arg),
                )
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "cancel" and method == "POST":
                body = self._read_json_body()
                self._send_json(
                    200,
                    self.bridge.service.cancel_session(
                        route[1], mode=str(body.get("mode") or "cancel")
                    ),
                )
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "messages" and method == "POST":
                self._handle_send_message(route[1])
                return
            if (
                len(route) == 5
                and route[0] == "sessions"
                and route[2] == "messages"
                and route[4] == "send-now"
                and method == "POST"
            ):
                body = self._read_json_body()
                self._send_json(
                    200,
                    self.bridge.service.send_message_now(
                        route[1], str(body.get("task_id") or ""), route[3]
                    ),
                )
                return
            if (
                len(route) == 5
                and route[0] == "sessions"
                and route[2] == "messages"
                and route[4] == "cancel"
                and method == "POST"
            ):
                body = self._read_json_body()
                self._send_json(
                    200,
                    self.bridge.service.cancel_message(
                        route[1], str(body.get("task_id") or ""), unquote(route[3])
                    ),
                )
                return
            if len(route) == 3 and route[0] == "sessions" and route[2] == "uploads" and method == "POST":
                self._handle_upload(route[1])
                return
            if route == ["approvals"] and method == "GET":
                session_id = (query.get("session_id") or [""])[0] or None
                self._send_json(200, {"approvals": self.bridge.service.list_approvals(session_id=session_id)})
                return
            if len(route) == 2 and route[0] == "sudo-requests" and method == "POST":
                if not isinstance(self.connection, ssl.SSLSocket):
                    self._send_json(403, {"error": "sudo password requires HTTPS"})
                    return
                body = self._read_json_body()
                try:
                    result = deliver_decision(
                        route[1],
                        str(body.get("decision") or ""),
                        str(body.get("password") or ""),
                    )
                except SudoRequestError as exc:
                    raise MobileConflict(str(exc)) from exc
                self.bridge.broadcaster.publish({
                    "kind": "sudo.resolved",
                    "payload": {"request_id": route[1]},
                    "session_id": None,
                })
                self._send_json(200, result)
                return
            if route == ["approval-settings"] and method == "GET":
                self._send_json(200, self.bridge.service.approval_settings())
                return
            if route == ["approval-settings"] and method == "PATCH":
                body = self._read_json_body()
                enabled = body.get("enabled")
                if not isinstance(enabled, bool):
                    raise MobileInvalid("enabled must be boolean")
                self._send_json(200, self.bridge.service.set_approvals_enabled(enabled))
                return
            if route == ["security-settings"] and method == "GET":
                self._send_json(200, self.bridge.service.security_settings())
                return
            if route == ["security-settings"] and method == "PATCH":
                body = self._read_json_body()
                self._send_json(
                    200,
                    self.bridge.service.set_security_level(
                        body.get("level"),
                        ttl_s=body.get("ttl_s"),
                        confirm=body.get("confirm"),
                        actor=str(device.get("device_id") or ""),
                    ),
                )
                return
            if route == ["sandbox-settings"] and method == "GET":
                self._send_json(200, self.bridge.service.sandbox_settings())
                return
            if route == ["sandbox-settings"] and method == "PATCH":
                body = self._read_json_body()
                enabled = body.get("enabled")
                if not isinstance(enabled, bool):
                    raise MobileInvalid("enabled must be boolean")
                self._send_json(200, self.bridge.service.set_sandbox_settings(enabled))
                return
            if len(route) == 2 and route[0] == "tasks" and method == "GET":
                self._send_json(200, self.bridge.service.get_task(route[1]))
                return
            if len(route) == 3 and route[0] == "tasks" and route[2] == "cancel" and method == "POST":
                body = self._read_json_body()
                self._send_json(200, self.bridge.service.cancel_task(route[1], mode=str(body.get("mode") or "cancel")))
                return
            if len(route) == 4 and route[0] == "tasks" and route[2] == "approvals" and method == "POST":
                body = self._read_json_body()
                self._send_json(
                    200,
                    self.bridge.service.resolve_approval(route[1], route[3], str(body.get("decision") or "")),
                )
                return
            if len(route) == 4 and route[0] == "tasks" and route[2] == "user-input" and method == "POST":
                body = self._read_json_body()
                self._send_json(
                    200,
                    self.bridge.service.resolve_user_input(
                        route[1],
                        route[3],
                        response=body.get("response") if isinstance(body.get("response"), dict) else None,
                        cancelled=bool(body.get("cancelled")),
                    ),
                )
                return
            if route == ["devices"] and method == "GET":
                self._send_json(
                    200,
                    {
                        "devices": self.bridge.store.list_devices(include_revoked=True),
                        # The client must know which list entry it is itself, to avoid
                        # suggesting deletion or revocation of the current device.
                        "current_device_id": str(device.get("device_id") or ""),
                    },
                )
                return
            if route == ["devices", "purge-revoked"] and method == "POST":
                deleted = self.bridge.store.purge_revoked_devices()
                self._send_json(200, {"deleted": deleted})
                return
            if len(route) == 2 and route[0] == "devices" and method == "DELETE":
                target = route[1]
                if (query.get("purge") or ["0"])[0] in {"1", "true", "yes"}:
                    # Hard deletion: device entries are completely removed.
                    # Self-deletion is not allowed—otherwise, the client would remain with a token without a record.
                    if target == str(device.get("device_id") or ""):
                        self._send_json(
                            409, {"error": "cannot delete the device that is making the request"}
                        )
                        return
                    deleted = self.bridge.store.delete_device(target)
                    self._send_json(
                        200 if deleted else 404, {"deleted": deleted, "device_id": target}
                    )
                    return
                removed = self.bridge.revoke_device(target)
                self._send_json(200 if removed else 404, {"revoked": removed, "device_id": target})
                return
            if route == ["pair", "start"] and method == "POST":
                body = self._read_json_body()
                ttl = int(body.get("ttl_s") or 300)
                self._send_json(201, self.bridge.store.create_pairing_code(
                    ttl_s=ttl, created_by="device:" + str(device["device_id"])))
                return
            if route == ["provider-usage"] and method == "GET":
                profile=str((query.get("profile") or [""])[0])
                if not profile or len(profile)>128:
                    self._send_json(400,{"error":"Invalid profile","code":"profile_unknown"});return
                force=str((query.get("refresh") or ["0"])[0]) in {"1","true"}
                provider=getattr(self.bridge.service,"profile_usage",None)
                if provider is not None:
                    available=self.bridge.service.available_profiles
                    if available is not None and profile not in available():
                        self._send_json(404,{"error":"Profile unavailable","code":"profile_unknown"});return
                    self._send_json(200,provider(profile,force=force))
                else:
                    try:self._send_json(200,{"provider":"opencode",**self.bridge.service.opencode_usage(force=force)})
                    except OpenCodeUsageError as exc:self._send_json(exc.status,{"error":exc.message,"code":exc.code})
                return
            if route == ["opencode-usage"] and method == "GET":
                # OpenCode Go subscription quota: the same key and cache as in the web panel, so the phone and browser
                # do not duplicate requests to upstream. The key remains on the host and does not appear in the response.
                force = str((query.get("refresh") or ["0"])[0]).strip().lower() in {"1", "true", "yes"}
                try:
                    self._send_json(200, self.bridge.service.opencode_usage(force=force))
                except OpenCodeUsageError as exc:
                    self._send_json(exc.status, {"error": exc.message, "code": exc.code})
                return
        except VoiceUnavailable as exc:
            self._send_json(exc.status, {"error": exc.message, "code": exc.code})
            return
        except MobileNotFound as exc:
            self._send_json(404, {"error": str(exc)})
            return
        except MobileConflict as exc:
            self._send_json(409, {"error": str(exc)})
            return
        except MobileInvalid as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except PairingAuthorityError as exc:
            self._send_json(401, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        self._send_json(404, {"error": "not found"})

    def _handle_terminal_ws(self, device: dict[str, Any]) -> None:
        # Native clients authenticate with the same pinned bearer transport.
        # Browser origins must use the existing cookie-bound Web UI endpoint.
        if self.headers.get("Origin"):
            self._send_json(403, {"error": "native terminal only"})
            return
        try:
            accept = validate_handshake(HandshakeRequest.from_headers(self.headers))
        except WebSocketError as exc:
            self._send_json(400, {"error": exc.reason})
            return
        self.send_response(101, "Switching Protocols")
        for name, value in handshake_headers(accept):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.flush()
        self.connection.settimeout(None)
        ws = WebSocket(self.connection)
        token = str(self.headers.get("Authorization") or "")[7:].strip()
        def access(session_id):
            if self.bridge.store.authenticate(token) is None:
                raise TerminalError("device_unauthorized")
            return self.bridge.terminals.workdir(session_id)
        TerminalEndpoint(
            ws, self.bridge.terminals.manager, workdir_provider=access,
            access_validator=access, device_identity=str(device["device_id"]),
        ).serve()
        self.close_connection = True

    def _authenticate(self) -> dict[str, Any] | None:
        client = self.client_address[0] if self.client_address else "?"
        header = str(self.headers.get("Authorization") or "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        device = self.bridge.store.authenticate(token) if token else None
        if device is None:
            self.bridge.auth.record_failure(client)
            self._send_json(401, {"error": "device token is missing or invalid"})
            return None
        self.bridge.auth.record_success(client)
        return device

    def _handle_pair_complete(self, client: str) -> None:
        if self.bridge.auth.is_locked(client):
            self._send_json(429, {"error": "too many failed attempts, try later"})
            return
        try:
            body = self._read_json_body()
            paired = self.bridge.store.complete_pairing(
                code=str(body.get("code") or ""),
                name=str(body.get("name") or "android device"),
                platform=str(body.get("platform") or "android"),
            )
        except ValueError as exc:
            self.bridge.auth.record_failure(client)
            self._send_json(400, {"error": str(exc)})
            return
        self.bridge.auth.record_success(client)
        self._send_json(201, paired)

    def _handle_voice_config(self) -> None:
        """Voice mode settings for the frontend; this key does not apply here."""
        try:
            payload = self.bridge.voice.public_config()
        except Exception:
            payload = {
                "enabled": False, "tier": "mini", "model": "", "voice": "",
                "tiers": ["full", "mini"], "reason": "error",
            }
        self._send_json(200, payload)

    def _handle_voice_session(self,device_id: str) -> None:
        body = self._read_json_body()
        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            raise MobileInvalid("session_id is required")
        # Voice is tied to a specific existing session, not an arbitrary one.
        self.bridge.service.get_session(session_id)
        result=self.bridge.voice.start_session(session_id, body.get("tier"))
        with self.bridge.voice_devices_lock:
            current=self.bridge.store.get_device(device_id)
            if not current or current.get('revoked_at') is not None:
                self.bridge.voice.hangup(result['call_id'])
                self._send_json(401,{'error':'device revoked'});return
            self.bridge.voice_devices[result['call_id']]=device_id
        self._send_json(200,result)

    def _handle_voice_sdp(self,device_id: str) -> None:
        body = self._read_json_body()
        call_id = str(body.get("call_id") or "").strip()
        sdp = body.get("sdp")
        if not call_id or not isinstance(sdp, str) or not sdp.strip():
            raise MobileInvalid("call_id and sdp are required")
        with self.bridge.voice_devices_lock:
            if call_id not in self.bridge.voice_devices:
                raise VoiceUnavailable('call_not_found',404,'Голосовая сессия не найдена или истекла')
            if self.bridge.voice_devices.get(call_id)!=device_id:
                self._send_json(403,{'error':'voice call access denied'});return
            result=self.bridge.voice.relay_sdp(call_id, sdp)
            current=self.bridge.store.get_device(device_id)
            if not current or current.get('revoked_at') is not None:
                self.bridge.voice.hangup(call_id)
                self._send_json(401,{'error':'device revoked'});return
            self._send_json(200,result)

    def _handle_voice_hangup(self,device_id: str) -> None:
        body = self._read_json_body()
        call_id = str(body.get("call_id") or "").strip()
        if not call_id:
            raise MobileInvalid("call_id is required")
        with self.bridge.voice_devices_lock:
            owner=self.bridge.voice_devices.get(call_id)
            if owner is not None and owner!=device_id:
                self._send_json(403,{'error':'voice call access denied'});return
            self.bridge.voice_devices.pop(call_id,None)
        self._send_json(200, self.bridge.voice.hangup(call_id))

    def _handle_send_message(self, session_id: str) -> None:
        body = self._read_json_body()
        raw_ids = body.get("upload_ids")
        upload_ids = (
            [str(item) for item in raw_ids if str(item or "").strip()]
            if isinstance(raw_ids, list)
            else []
        )
        raw_skill_ids = body.get("skill_ids")
        skill_ids = (
            [str(item) for item in raw_skill_ids if str(item or "").strip()]
            if isinstance(raw_skill_ids, list)
            else []
        )
        raw_plan_mode = body.get("plan_mode")
        plan_mode = raw_plan_mode if isinstance(raw_plan_mode, bool) else False
        result = self.bridge.service.send_message(
            session_id,
            str(body.get("text") or ""),
            idempotency_key=str(body.get("idempotency_key") or "") or None,
            cwd=str(body.get("cwd") or "") or None,
            upload_ids=upload_ids,
            skill_ids=skill_ids,
            plan_mode=plan_mode,
            demo_language=str(body.get('demo_language') or 'ru'),
            desktop_ui=body.get('desktop_ui') is True,
            activate_profile=body.get("activate_profile") is True,
        )
        self._send_json(202, result)

    def _handle_upload(self, session_id: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise MobileInvalid("upload body is empty")
        if length > MAX_UPLOAD_BYTES:
            self.close_connection = True
            self._send_json(413, {"error": "upload is too large"})
            return
        data = self._read_exact(length)
        content_type = str(self.headers.get("Content-Type") or "").split(";")[0].strip()
        filename = unquote(str(self.headers.get("X-File-Name") or "")).strip() or "upload"
        descriptor = self.bridge.service.save_upload(
            session_id,
            filename=filename,
            mime_type=content_type or "application/octet-stream",
            data=data,
        )
        self._send_json(201, descriptor)

    def _read_exact(self, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                raise MobileInvalid("upload body is incomplete")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _handle_events(self, query: dict[str, list[str]], device_id: str) -> None:
        try:
            after_seq = max(0, int((query.get("after") or ["0"])[0] or 0))
        except ValueError:
            self._send_json(400, {"error": "after must be an integer"})
            return
        session_id = (query.get("session_id") or [""])[0] or None
        def active():
            current=self.bridge.store.get_device(device_id)
            return current is not None and current.get('revoked_at') is None
        channel = self.bridge.broadcaster.subscribe()
        try:
            self.connection.settimeout(45)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self._write_sse({"kind": "stream.ready", "payload": {
                "after": after_seq, "replay_through": self.bridge.store.max_seq(),
            }})
            last_seq = after_seq
            heartbeat_at = time.monotonic()
            while not self.server.closing.is_set() and active():
                # Durable events come from the journal, including service-side writes
                # and events dropped by a full live queue. Never advance past a gap.
                while True:
                    batch = self.bridge.store.list_events(after_seq=last_seq, session_id=session_id, limit=500)
                    for event in batch:
                        if not active():return
                        self._write_sse(event)
                        last_seq = int(event["seq"])
                    if len(batch) < 500:
                        break
                if time.monotonic() - heartbeat_at >= SSE_HEARTBEAT_S:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    heartbeat_at = time.monotonic()
                try:
                    event = channel.get(timeout=1.0)
                except queue.Empty:
                    continue
                if session_id and event.get("session_id") not in {session_id, None}:
                    continue
                seq = event.get("seq")
                if isinstance(seq, int):
                    continue
                if not active():return
                self._write_sse(event)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, ssl.SSLError):
            pass
        finally:
            self.bridge.broadcaster.unsubscribe(channel)

    def _write_sse(self, payload: dict[str, Any]) -> None:
        chunk = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(chunk)
        self.wfile.flush()

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise MobileInvalid("request body is too large")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise MobileInvalid("request body must be a JSON object")
        return parsed

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        if code >= 400:
            payload = dict(payload)
            diagnostic = payload.get('diagnostic')
            if not isinstance(diagnostic, dict):
                diagnostic = observation(classify_error(status=code), component='bridge', operation='read' if self.command in {'GET', 'HEAD'} else 'write',
                    endpoint_role='client_to_bridge', owner=getattr(self,'_diagnostic_owner','bridge-http'), http_status=code, delivery='rejected',
                    http_request_id=getattr(self, 'http_request_id', ''), client_operation_id=getattr(self, 'client_operation_id', ''))
            payload['diagnostic'] = diagnostic
            payload['http_request_id'] = getattr(self, 'http_request_id', '')
            payload['client_operation_id'] = getattr(self, 'client_operation_id', '')
            self.bridge.diagnostics.observe(diagnostic)
        elif self.command == 'GET':
            self.bridge.diagnostics.recover(owner=getattr(self,'_diagnostic_owner','bridge-http'),proof='operation_completed')
        self._diagnostic_response_started = True
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if getattr(self, 'http_request_id', ''):
            self.send_header('X-Request-Id', self.http_request_id)
        if code >= 400 or urlparse(self.path).path.rstrip('/') in {f'{API_PREFIX}/browser/preview',f'{API_PREFIX}/diagnostics'}:self.send_header('Cache-Control','no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def build_server(bridge: MobileBridge, *, host: str, port: int) -> _BridgeHTTPServer:
    server = _BridgeHTTPServer((host, port), MobileRequestHandler, bridge)
    if bridge.config.tls_cert and bridge.config.tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=bridge.config.tls_cert, keyfile=bridge.config.tls_key)
        server.enable_tls(context)
    return server


def serve_forever(bridge: MobileBridge) -> int:
    server = build_server(bridge, host=bridge.config.host, port=bridge.config.port)
    bridge.start()
    scheme = "https" if bridge.config.tls_cert else "http"
    print(f"mobile bridge: {scheme}://{bridge.config.host}:{server.server_address[1]}{API_PREFIX}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("mobile bridge: stopped")
    finally:
        bridge.stop()
        server.server_close()
    return 0
