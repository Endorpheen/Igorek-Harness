from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    from .cli_chat import _build_prompt
    from .cli_tasks import create_task, get_task, wait_task
    from .codex_native_policy import describe_app_server_request
    from .memory_manager import MemoryManager
    from .runtime import (
        get_data_root,
        get_profile_config,
        get_workspace_root,
        profile_backend_name,
    )
    from .trace_taxonomy import CcTraceEvent
except ImportError:  # pragma: no cover - direct script fallback
    from cli_chat import _build_prompt
    from cli_tasks import create_task, get_task, wait_task
    from codex_native_policy import describe_app_server_request
    from memory_manager import MemoryManager
    from runtime import (
        get_data_root,
        get_profile_config,
        get_workspace_root,
        profile_backend_name,
    )
    from trace_taxonomy import CcTraceEvent


def _ensure_text(value: Any) -> str:
    return str(value or "").strip()


def _resolve_output_file_path(base_dir: Path, raw_path: str) -> Path | None:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError:
        pass
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _is_deliverable_output_file(path: Path) -> bool:
    artifact_dirs = {".tool_results", ".checkpoints"}
    artifact_files = {"input.json", "result.json", "copy_metadata.json", "cc_backend_trace.jsonl"}
    for part in path.parts:
        if part in artifact_dirs:
            return False
    return path.name not in artifact_files


def _task_trace_path(task_id: str) -> Path:
    workspace_root = Path(get_workspace_root())
    return workspace_root / f"temp_execution_{task_id[:8]}" / "cc_backend_trace.jsonl"


def _read_trace_events(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], offset
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], offset
    events: list[dict[str, Any]] = []
    for line in lines[offset:]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events, len(lines)


class _RuntimeAppServerApiClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        raw_base = (base_url or os.environ.get("FACTORY_API_BASE_URL") or "http://127.0.0.1:8181").strip()
        self.base_url = raw_base.rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("FACTORY_API_KEY", "")

    def start_session(
        self,
        *,
        profile: str,
        cwd: str,
        thread_id: str | None,
        chat_session: str,
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "profile": profile,
            "cwd": cwd,
            "chat_session": chat_session,
        }
        if thread_id:
            payload["thread_id"] = thread_id
        if provider_override:
            payload["provider_override"] = provider_override
        if model_override:
            payload["model_override"] = model_override
        return self._request_json("POST", "/app-server/sessions", payload)

    def get_events(self, session_id: str, *, after_seq: int, limit: int = 100) -> dict[str, Any]:
        return self._request_json("GET", f"/app-server/sessions/{session_id}/events?after={after_seq}&limit={limit}")

    def send_message(self, session_id: str, text: str) -> dict[str, Any]:
        return self._request_json("POST", f"/app-server/sessions/{session_id}/messages", {"text": text})

    def resolve_approval(
        self,
        session_id: str,
        request_id: str,
        *,
        approved: bool,
        remember_for_session: bool,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/app-server/sessions/{session_id}/approvals/{request_id}",
            {
                "action": "approve" if approved else "reject",
                "remember_for_session": remember_for_session,
            },
        )

    def close_session(self, session_id: str) -> dict[str, Any]:
        return self._request_json("DELETE", f"/app-server/sessions/{session_id}")

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body: bytes | None = None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib_request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Factory API {method} {path} failed: HTTP {exc.code}: {details}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"Factory API {method} {path} unavailable: {exc.reason}") from exc
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"result": parsed}


@dataclass(frozen=True)
class GatewaySource:
    platform: str
    chat_id: str
    user_id: str = ""
    username: str = ""
    chat_session: str = ""


@dataclass(frozen=True)
class GatewayMessage:
    text: str
    attachments: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class GatewayTurnResult:
    transport: str
    status: str
    task_id: str | None = None
    task: dict[str, Any] | None = None


@dataclass
class GatewayPendingApproval:
    request_id: str
    method: str
    params: dict[str, Any]
    summary: str
    target: str
    default_scope: str
    security_approval_id: str = ""


@dataclass
class GatewayAppServerState:
    runtime_session_id: str | None = None
    thread_id: str | None = None
    event_after_seq: int = 0
    pending_approvals: list[GatewayPendingApproval] = field(default_factory=list)
    item_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    recent_file_paths: list[str] = field(default_factory=list)


class GatewayRuntime:
    def __init__(
        self,
        *,
        profile: str,
        workdir: str | None = None,
        runtime_api_base_url: str | None = None,
        runtime_api_key: str | None = None,
    ) -> None:
        self.profile = profile
        self.workdir = str(Path(workdir).expanduser().resolve()) if workdir else str(Path.cwd())
        self.data_dir = str(get_data_root())
        self.memory_manager = MemoryManager()
        self.app_server = _RuntimeAppServerApiClient(
            base_url=runtime_api_base_url,
            api_key=runtime_api_key,
        )

    def get_profile_config(self, profile: str | None = None) -> dict[str, Any]:
        return get_profile_config(profile or self.profile)

    def use_task_queue_transport(
        self,
        *,
        profile_config: dict[str, Any],
        provider_name: str,
    ) -> bool:
        backend = profile_backend_name(profile_config)
        if backend == "cc-backend":
            return True
        return backend == "codex" and provider_name == "openrouter"

    def foundation_context(self) -> str:
        try:
            return self.memory_manager.foundation_context(data_dir=self.data_dir).strip()
        except Exception:
            return ""

    def handle_message(
        self,
        *,
        source: GatewaySource,
        message: GatewayMessage,
        profile: str | None = None,
        history_context: str = "",
        provider_override: str | None = None,
        model_override: str | None = None,
        workspace_task_id: str | None = None,
        timeout_s: int = 1800,
        progress_callback=None,
        auto_approve: bool = False,
    ) -> GatewayTurnResult:
        effective_profile = profile or self.profile
        input_files = [
            str(item.get("path") or "").strip()
            for item in message.attachments
            if str(item.get("path") or "").strip()
        ]
        metadata: dict[str, Any] = {
            "source": "gateway_runtime",
            "platform": source.platform,
            "created_via": "GatewayRuntime.handle_message",
            "session": source.chat_session,
            "chat_id": source.chat_id,
            "user_id": source.user_id,
            "cwd": self.workdir,
            # Stored separately from the assembled prompt for returning the result to the original chat.
            "original_message_text": message.text,
        }
        if source.username:
            metadata["username"] = source.username
        if input_files:
            metadata["attachments"] = list(message.attachments)
        if provider_override:
            metadata["provider_override"] = provider_override
        if model_override:
            metadata["model_override"] = model_override
        if workspace_task_id:
            metadata["workspace_task_id"] = workspace_task_id
        if auto_approve:
            metadata["auto_approve"] = True

        prompt = _build_prompt(
            history_context,
            message.text,
            foundation_context=self.foundation_context(),
        )
        created = create_task(
            text=prompt,
            profile=effective_profile,
            metadata=metadata,
            parent_id=workspace_task_id,
            input_files=input_files,
        )
        task_id = str(created.get("task_id") or "").strip()
        if not task_id:
            raise RuntimeError("Task queue did not return task_id")
        trace_path = _task_trace_path(task_id)
        seen_trace_lines = 0
        deadline = time.time() + timeout_s
        # waiting_approval — stable pause: control immediately returns to Telegram.
        final_states = {"ok", "fail", "dead_letter", "waiting_approval", "cancelled"}
        task = get_task(task_id) or {"task_id": task_id, "status": "queued"}
        while time.time() < deadline:
            if progress_callback:
                events, seen_trace_lines = _read_trace_events(trace_path, seen_trace_lines)
                for event in events:
                    if event.get("kind") != CcTraceEvent.TOOL_REQUEST_BATCH:
                        continue
                    tools = event.get("tools") if isinstance(event.get("tools"), list) else []
                    for tool in tools:
                        if not isinstance(tool, dict):
                            continue
                        progress_callback(
                            "tool.started",
                            tool_name=_ensure_text(tool.get("name")),
                            args=tool.get("input") if isinstance(tool.get("input"), dict) else {},
                        )
            task = get_task(task_id) or task
            if str(task.get("status") or "") in final_states:
                break
            time.sleep(1.0)
        else:
            task = get_task(task_id) or {"task_id": task_id, "status": "timeout"}
        return GatewayTurnResult(
            transport="task_queue",
            status=str(task.get("status") or ""),
            task_id=task_id,
            task=task,
        )

    def close_app_server_session(self, state: GatewayAppServerState) -> GatewayAppServerState:
        if state.runtime_session_id:
            try:
                self.app_server.close_session(state.runtime_session_id)
            except Exception:
                pass
        return GatewayAppServerState()

    def ensure_app_server_session(
        self,
        *,
        state: GatewayAppServerState,
        source: GatewaySource,
        profile: str | None = None,
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> GatewayAppServerState:
        if state.runtime_session_id:
            try:
                self.app_server.get_events(
                    state.runtime_session_id,
                    after_seq=state.event_after_seq,
                    limit=1,
                )
                return state
            except Exception:
                state.runtime_session_id = None
                state.event_after_seq = 0

        effective_profile = profile or self.profile
        chat_session = source.chat_session or f"{source.platform}-chat-{source.chat_id}"
        try:
            payload = self.app_server.start_session(
                profile=effective_profile,
                cwd=self.workdir,
                thread_id=state.thread_id,
                chat_session=chat_session,
                provider_override=provider_override,
                model_override=model_override,
            )
        except Exception as exc:
            error_text = _ensure_text(exc).lower()
            stale_thread = (
                state.thread_id
                and (
                    "no rollout found for thread id" in error_text
                    or "thread/resume failed" in error_text
                    or "thread not found" in error_text
                )
            )
            if not stale_thread:
                raise
            state = GatewayAppServerState()
            payload = self.app_server.start_session(
                profile=effective_profile,
                cwd=self.workdir,
                thread_id=None,
                chat_session=chat_session,
                provider_override=provider_override,
                model_override=model_override,
            )

        state.runtime_session_id = _ensure_text(payload.get("session_id")) or None
        state.thread_id = _ensure_text(payload.get("thread_id")) or state.thread_id
        state.pending_approvals = []
        state.item_cache = {}
        state.recent_file_paths = []
        return state

    def handle_app_server_message(
        self,
        *,
        state: GatewayAppServerState,
        source: GatewaySource,
        message: GatewayMessage,
        profile: str | None = None,
        provider_override: str | None = None,
        model_override: str | None = None,
        turn_timeout_s: int = 1800,
        auto_approve: bool = False,
        auto_approve_scope: str = "session",
    ) -> tuple[GatewayAppServerState, dict[str, Any]]:
        state = self.ensure_app_server_session(
            state=state,
            source=source,
            profile=profile,
            provider_override=provider_override,
            model_override=model_override,
        )
        if not state.runtime_session_id:
            raise RuntimeError("runtime session is not initialized")
        self.app_server.send_message(state.runtime_session_id, message.text)
        outcome = self.continue_app_server_turn(
            state=state,
            turn_timeout_s=turn_timeout_s,
            auto_approve=auto_approve,
            auto_approve_scope=auto_approve_scope,
        )
        return state, outcome

    def resolve_app_server_approval(
        self,
        *,
        state: GatewayAppServerState,
        request_id: str,
        approved: bool,
        remember_for_session: bool,
    ) -> GatewayAppServerState:
        if not state.runtime_session_id:
            raise RuntimeError("Runtime session не активна.")
        self.app_server.resolve_approval(
            state.runtime_session_id,
            request_id,
            approved=approved,
            remember_for_session=remember_for_session,
        )
        state.pending_approvals = [
            item for item in state.pending_approvals if item.request_id != request_id
        ]
        return state

    def continue_app_server_turn(
        self,
        *,
        state: GatewayAppServerState,
        turn_timeout_s: int,
        auto_approve: bool = False,
        auto_approve_scope: str = "session",
    ) -> dict[str, Any]:
        import time

        started_at = time.time()
        aggregated_messages: list[str] = []
        turn_completed = False
        waiting_on_approval = False
        while time.time() - started_at < turn_timeout_s:
            if not state.runtime_session_id:
                raise RuntimeError("runtime session is not initialized")
            payload = self.app_server.get_events(
                state.runtime_session_id,
                after_seq=state.event_after_seq,
                limit=100,
            )
            events = payload.get("events") if isinstance(payload, dict) else []
            if not isinstance(events, list):
                events = []
            if not events:
                time.sleep(0.4)
                continue
            for event in events:
                if not isinstance(event, dict):
                    continue
                try:
                    state.event_after_seq = max(state.event_after_seq, int(event.get("seq") or 0))
                except Exception:
                    pass
                parsed = self._handle_app_server_event(
                    state=state,
                    event=event,
                    auto_approve=auto_approve,
                    auto_approve_scope=auto_approve_scope,
                )
                for message in parsed.get("assistant_messages", []):
                    if message not in aggregated_messages:
                        aggregated_messages.append(message)
                if parsed.get("waiting_on_approval"):
                    waiting_on_approval = True
                if parsed.get("turn_completed"):
                    turn_completed = True
            if turn_completed:
                return {
                    "status": "completed",
                    "messages": aggregated_messages,
                    "files": list(state.recent_file_paths),
                }
            if waiting_on_approval and state.pending_approvals:
                return {
                    "status": "waiting_on_approval",
                    "messages": aggregated_messages,
                    "files": list(state.recent_file_paths),
                    "approvals": list(state.pending_approvals),
                }
        return {
            "status": "timeout",
            "messages": aggregated_messages,
            "files": list(state.recent_file_paths),
        }

    def _handle_app_server_event(
        self,
        *,
        state: GatewayAppServerState,
        event: dict[str, Any],
        auto_approve: bool,
        auto_approve_scope: str,
    ) -> dict[str, Any]:
        kind = _ensure_text(event.get("kind"))
        assistant_messages: list[str] = []
        waiting_on_approval = False
        turn_completed = False
        if kind == "server_request":
            method = _ensure_text(event.get("method"))
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            item_id = _ensure_text(params.get("itemId"))
            item_snapshot = state.item_cache.get(item_id) if item_id else None
            descriptor = describe_app_server_request(method, params, item_snapshot=item_snapshot)
            approval = GatewayPendingApproval(
                request_id=_ensure_text(event.get("request_id")),
                security_approval_id=_ensure_text(event.get("security_approval_id")),
                method=method,
                params=params,
                summary=descriptor.summary,
                target=descriptor.target,
                default_scope=descriptor.default_scope,
            )
            if auto_approve and state.runtime_session_id and approval.request_id:
                try:
                    self.app_server.resolve_approval(
                        state.runtime_session_id,
                        approval.request_id,
                        approved=True,
                        remember_for_session=(auto_approve_scope == "session"),
                    )
                    return {
                        "assistant_messages": assistant_messages,
                        "waiting_on_approval": False,
                        "turn_completed": turn_completed,
                    }
                except Exception:
                    pass
            known = {item.request_id for item in state.pending_approvals}
            if approval.request_id and approval.request_id not in known:
                state.pending_approvals.append(approval)
            waiting_on_approval = True
            return {
                "assistant_messages": assistant_messages,
                "waiting_on_approval": waiting_on_approval,
                "turn_completed": turn_completed,
            }
        if kind == "error":
            assistant_messages.append(f"Runtime error: {_ensure_text(event.get('message'))}")
            return {
                "assistant_messages": assistant_messages,
                "waiting_on_approval": waiting_on_approval,
                "turn_completed": turn_completed,
            }
        if kind == "diagnostic" or kind != "notification":
            return {
                "assistant_messages": assistant_messages,
                "waiting_on_approval": waiting_on_approval,
                "turn_completed": turn_completed,
            }
        method = _ensure_text(event.get("method"))
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        if method == "thread/status/changed":
            status = params.get("status") if isinstance(params, dict) else {}
            flags = status.get("activeFlags") if isinstance(status, dict) else []
            if isinstance(flags, list) and "waitingOnApproval" in flags:
                waiting_on_approval = True
        elif method == "item/started":
            item = params.get("item")
            if isinstance(item, dict):
                item_id = _ensure_text(item.get("id"))
                if item_id:
                    state.item_cache[item_id] = item
        elif method == "item/agentMessage/delta":
            item_id = _ensure_text(params.get("itemId"))
            if item_id:
                item = state.item_cache.setdefault(item_id, {"type": "agentMessage", "text": ""})
                item["text"] = f"{_ensure_text(item.get('text'))}{_ensure_text(params.get('delta'))}"
        elif method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict):
                item_id = _ensure_text(item.get("id"))
                if item_id:
                    state.item_cache[item_id] = item
                item_type = _ensure_text(item.get("type"))
                if item_type == "agentMessage":
                    text = _ensure_text(item.get("text"))
                    if text:
                        assistant_messages.append(text)
                elif item_type == "fileChange":
                    for change in item.get("changes") or []:
                        if not isinstance(change, dict):
                            continue
                        path = _ensure_text(change.get("path"))
                        resolved = _resolve_output_file_path(Path(self.workdir).resolve(), path) if path else None
                        if resolved is None or not _is_deliverable_output_file(resolved):
                            continue
                        normalized = str(resolved)
                        if normalized not in state.recent_file_paths:
                            state.recent_file_paths.append(normalized)
        elif method == "serverRequest/resolved":
            request_id = _ensure_text(params.get("requestId") or params.get("id"))
            if request_id:
                state.pending_approvals = [
                    item for item in state.pending_approvals if item.request_id != request_id
                ]
        elif method == "turn/completed":
            turn_completed = True
        return {
            "assistant_messages": assistant_messages,
            "waiting_on_approval": waiting_on_approval,
            "turn_completed": turn_completed,
        }
