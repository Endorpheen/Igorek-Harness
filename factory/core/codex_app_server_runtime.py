from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .agent_foundation import foundation_prompt_context
from .approval_runtime import ApprovalRuntimeBridge
from .codex_app_server_adapter import CodexAppServerSession
from .codex_provider import resolve_codex_launch_settings
from .codex_native_policy import describe_app_server_request, is_known_app_server_request_method
from .runtime import get_data_root, get_profile_config, profile_backend_name, profile_model_name


def _request_summary(method: str, params: dict[str, Any]) -> str:
    return describe_app_server_request(method, params).summary


class _ManagedSession:
    def __init__(
        self,
        session_id: str,
        profile: str,
        chat_session: str | None,
        provider: str,
        model: str,
        adapter: CodexAppServerSession,
    ) -> None:
        self.session_id = session_id
        self.profile = profile
        self.chat_session = chat_session
        self.provider = provider
        self.model = model
        self.adapter = adapter
        self.created_at = int(time.time())
        self.updated_at = self.created_at
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._next_seq = 1
        self._pending_requests: dict[str, dict[str, Any]] = {}

    @property
    def thread_id(self) -> str | None:
        return self.adapter.thread_id

    def append_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            event = {
                "seq": self._next_seq,
                "ts": int(time.time()),
                **payload,
            }
            self._next_seq += 1
            self.updated_at = event["ts"]
            self._events.append(event)
            if len(self._events) > 500:
                self._events = self._events[-500:]
            return event

    def list_events(self, after_seq: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._events if int(item.get("seq") or 0) > after_seq][:limit]

    def pending_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._pending_requests.values()]

    def register_request(
        self,
        request_id: str,
        raw_request_id: int | str,
        method: str,
        params: dict[str, Any],
        *,
        security_approval_id: str | None = None,
    ) -> None:
        descriptor = describe_app_server_request(method, params)
        with self._lock:
            self._pending_requests[request_id] = {
                "request_id": request_id,
                "raw_request_id": raw_request_id,
                "security_approval_id": security_approval_id,
                "method": method,
                "params": params,
                "summary": descriptor.summary,
                "native_policy_type": descriptor.policy_type,
                "ui_type": descriptor.ui_type,
                "target": descriptor.target,
                "default_scope": descriptor.default_scope,
            }

    def pop_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._pending_requests.pop(request_id, None)

    def remove_resolved_request(self, request_id: str) -> None:
        with self._lock:
            self._pending_requests.pop(request_id, None)


class CodexAppServerRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, _ManagedSession] = {}
        self._unknown_methods_path = get_data_root() / "app_server" / "unknown_methods.json"
        self._approval_bridge = ApprovalRuntimeBridge()

    def _record_unknown_method(self, *, method: str, params: dict[str, Any]) -> None:
        try:
            path = self._unknown_methods_path
            path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {}
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            methods = payload.get("methods")
            if not isinstance(methods, dict):
                methods = {}
                payload["methods"] = methods
            now_ts = int(time.time())
            existing = methods.get(method)
            if not isinstance(existing, dict):
                existing = {"count": 0}
            existing["count"] = int(existing.get("count") or 0) + 1
            existing["last_seen_ts"] = now_ts
            existing["last_params_sample"] = params
            methods[method] = existing
            if len(methods) > 50:
                ordered = sorted(
                    methods.items(),
                    key=lambda item: int((item[1] if isinstance(item[1], dict) else {}).get("last_seen_ts") or 0),
                    reverse=True,
                )
                methods = dict(ordered[:50])
                payload["methods"] = methods
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    def start_session(
        self,
        *,
        profile: str,
        cwd: str | None = None,
        thread_id: str | None = None,
        chat_session: str | None = None,
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        profile_config = get_profile_config(profile)
        backend = profile_backend_name(profile_config)
        if backend != "codex":
            raise ValueError(f"app-server is supported only for codex profiles, got: {backend}")

        workdir = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
        settings = resolve_codex_launch_settings(
            profile_config,
            provider_override=provider_override,
            model_override=model_override,
        )
        model = str(settings.get("model") or profile_model_name(profile_config) or "").strip() or None
        # The codex app-server correctly resolves the provider from the model; a raw factory provider
        # such as `openai-codex` does not fit as a modelProvider.
        provider = None
        provider_display = str(settings.get("provider_display") or "")
        developer_instructions = foundation_prompt_context(get_data_root())
        session_id = str(uuid.uuid4())

        holder: dict[str, _ManagedSession] = {}

        def on_notification(method: str, params: dict[str, Any]) -> None:
            session = holder.get("session")
            if session is None:
                return
            if method == "serverRequest/resolved":
                request_id = params.get("id")
                if request_id is not None:
                    session.remove_resolved_request(str(request_id))
            session.append_event(
                {
                    "kind": "notification",
                    "method": method,
                    "params": params,
                }
            )

        def on_server_request(method: str, request_id: int | str, params: dict[str, Any]) -> None:
            session = holder.get("session")
            if session is None:
                return
            normalized_request_id = str(request_id)
            if not is_known_app_server_request_method(method):
                print(f"[app-server] unknown server_request method: {method}")
                self._record_unknown_method(method=method, params=params)
            envelope = self._approval_bridge.observe_native_request(
                session_id=session.session_id,
                task_id=session.chat_session,
                requested_by=f"app_server:{session.session_id}",
                method=method,
                params=params,
                native_request_id=normalized_request_id,
            )
            session.register_request(normalized_request_id, request_id, method, params, security_approval_id=envelope.approval_id)
            session.append_event(
                {
                    "kind": "server_request",
                    "request_id": normalized_request_id,
                    "security_approval_id": envelope.approval_id,
                    "method": method,
                    "summary": envelope.summary,
                    "native_policy_type": envelope.policy_type,
                    "default_scope": envelope.default_scope,
                    "params": params,
                }
            )

        def on_error(message: str) -> None:
            session = holder.get("session")
            if session is None:
                return
            kind = "diagnostic" if message.startswith("codex app-server stderr:") else "error"
            session.append_event(
                {
                    "kind": kind,
                    "message": message,
                }
            )

        adapter = CodexAppServerSession(
            cwd=workdir,
            model=model,
            model_provider=provider,
            env=settings.get("env") if isinstance(settings.get("env"), dict) else {},
            developer_instructions=developer_instructions,
            notification_handler=on_notification,
            server_request_handler=on_server_request,
            error_handler=on_error,
        )
        managed = _ManagedSession(
            session_id=session_id,
            profile=profile,
            chat_session=chat_session,
            provider=provider_display,
            model=str(model or ""),
            adapter=adapter,
        )
        holder["session"] = managed
        adapter.start(thread_id=thread_id)
        managed.append_event(
            {
                "kind": "lifecycle",
                "event": "session_started" if not thread_id else "session_resumed",
                "thread_id": managed.thread_id,
                "profile": profile,
                "provider": provider_display,
                "model": str(model or ""),
                "cwd": workdir,
            }
        )
        with self._lock:
            self._sessions[session_id] = managed
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        return {
            "session_id": session.session_id,
            "profile": session.profile,
            "provider": session.provider,
            "model": session.model,
            "chat_session": session.chat_session,
            "thread_id": session.thread_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "pending_requests": session.pending_requests(),
        }

    def list_events(self, session_id: str, *, after_seq: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        session = self._require_session(session_id)
        return session.list_events(after_seq=after_seq, limit=limit)

    def send_message(self, session_id: str, text: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        session.append_event(
            {
                "kind": "client_message",
                "text": text,
            }
        )
        result = session.adapter.send_user_message(text)
        session.append_event(
            {
                "kind": "client_result",
                "method": "turn/start",
                "result": result,
            }
        )
        return {"ok": True, "thread_id": session.thread_id}

    def resolve_request(
        self,
        session_id: str,
        *,
        request_id: str,
        approved: bool,
        remember_for_session: bool,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        pending = session.pop_request(request_id)
        if pending is None:
            raise KeyError(f"request not found: {request_id}")
        security_approval_id = str(pending.get("security_approval_id") or "")
        if security_approval_id:
            resolution_scope = "session" if approved and remember_for_session else "turn"
            resolved_by = "app_server:session" if approved and remember_for_session else "app_server:user"
            resolution_note = "approved via app-server runtime"
            if not approved:
                resolution_note = "rejected via app-server runtime"
            else:
                resolution_note = f"approved via app-server runtime ({resolution_scope})"
            self._approval_bridge.resolve_native_request(
                approval_id=security_approval_id,
                approved=approved,
                resolved_by=resolved_by,
                resolution_note=resolution_note,
            )
        session.adapter.resolve_server_request(
            request_id=pending.get("raw_request_id", request_id),
            method=str(pending.get("method") or ""),
            params=pending.get("params") if isinstance(pending.get("params"), dict) else {},
            remember_for_session=remember_for_session,
            approved=approved,
        )
        session.append_event(
            {
                "kind": "client_resolution",
                "request_id": request_id,
                "security_approval_id": security_approval_id,
                "approved": approved,
                "remember_for_session": remember_for_session,
                "native_policy_type": pending.get("native_policy_type"),
                "default_scope": pending.get("default_scope"),
            }
        )
        return {
            "ok": True,
            "security_approval_id": security_approval_id,
            "native_policy_type": pending.get("native_policy_type"),
            "default_scope": pending.get("default_scope"),
        }

    def close_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise KeyError(f"session not found: {session_id}")
        try:
            self._approval_bridge.reject_pending_for_session(
                session_id=session.session_id,
                requested_by=f"app_server:{session.session_id}",
                resolution_note="pending approval rejected because managed app-server session was closed",
            )
        finally:
            session.adapter.close()

    def _require_session(self, session_id: str) -> _ManagedSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"session not found: {session_id}")
        return session
