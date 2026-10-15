from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


NotificationHandler = Callable[[str, dict[str, Any]], None]
ServerRequestHandler = Callable[[str, int | str, dict[str, Any]], None]
ErrorHandler = Callable[[str], None]


def _default_elicitation_content(params: dict[str, Any]) -> dict[str, Any]:
    schema = params.get("requestedSchema")
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    content: dict[str, Any] = {}
    for key, raw_spec in properties.items():
        if not isinstance(raw_spec, dict):
            continue
        if "default" in raw_spec:
            content[str(key)] = raw_spec.get("default")
    return content


@dataclass
class _PendingResponse:
    event: threading.Event
    result: Any | None = None
    error: Any | None = None


class CodexAppServerSession:
    def __init__(
        self,
        *,
        cwd: str,
        model: str | None,
        model_provider: str | None,
        env: dict[str, str] | None,
        developer_instructions: str | None,
        notification_handler: NotificationHandler,
        server_request_handler: ServerRequestHandler,
        error_handler: ErrorHandler,
    ) -> None:
        self.cwd = cwd
        self.model = model
        self.model_provider = model_provider
        self.env = dict(env or {})
        self.developer_instructions = developer_instructions
        self.notification_handler = notification_handler
        self.server_request_handler = server_request_handler
        self.error_handler = error_handler

        self._proc: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _PendingResponse] = {}
        self._next_id = 1
        self._thread_id: str | None = None
        self._closed = False

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    def start(self, thread_id: str | None = None) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            ["codex", "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=self.cwd,
            env={**os.environ, **self.env},
        )
        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "igorek-tui-app-server",
                    "version": "0.1",
                },
                "capabilities": {
                    "experimentalApi": True,
                },
            },
        )

        params: dict[str, Any] = {
            "cwd": self.cwd,
            "approvalPolicy": "untrusted",
            "approvalsReviewer": "user",
            "sandbox": "workspace-write",
            "ephemeral": True,
            "personality": "pragmatic",
        }
        if self.model:
            params["model"] = self.model
        if self.model_provider:
            params["modelProvider"] = self.model_provider
        if self.developer_instructions:
            params["developerInstructions"] = self.developer_instructions

        if thread_id:
            params["threadId"] = thread_id
            result = self.request("thread/resume", params)
        else:
            result = self.request("thread/start", params)
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, dict) or not thread.get("id"):
            raise RuntimeError("app-server did not return thread id")
        self._thread_id = str(thread["id"])

    def close(self) -> None:
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None

    def request(self, method: str, params: dict[str, Any], timeout_s: float = 30.0) -> Any:
        request_id = self._allocate_request_id()
        pending = _PendingResponse(event=threading.Event())
        with self._pending_lock:
            self._pending[request_id] = pending
        self._send({"id": request_id, "method": method, "params": params})
        if not pending.event.wait(timeout=timeout_s):
            raise TimeoutError(f"timeout while waiting for app-server response to {method}")
        if pending.error is not None:
            raise RuntimeError(f"app-server {method} failed: {json.dumps(pending.error, ensure_ascii=False)}")
        return pending.result

    def send_user_message(self, text: str, timeout_s: float = 30.0) -> dict[str, Any]:
        if not self._thread_id:
            raise RuntimeError("app-server thread is not initialized")
        return self.request(
            "turn/start",
            {
                "threadId": self._thread_id,
                "cwd": self.cwd,
                "approvalPolicy": "untrusted",
                "approvalsReviewer": "user",
                "input": [
                    {
                        "type": "text",
                        "text": text,
                        "text_elements": [],
                    }
                ],
            },
            timeout_s=timeout_s,
        )

    def resolve_server_request(
        self,
        *,
        request_id: int | str,
        method: str,
        params: dict[str, Any],
        remember_for_session: bool,
        approved: bool,
    ) -> None:
        if method == "item/permissions/requestApproval":
            result = {
                "permissions": params.get("permissions", {}) if approved else {},
                "scope": "session" if approved and remember_for_session else "turn",
            }
        elif method in {"item/fileChange/requestApproval", "item/commandExecution/requestApproval"}:
            if approved:
                decision = "acceptForSession" if remember_for_session else "accept"
            else:
                decision = "decline"
            result = {"decision": decision}
        elif method == "mcpServer/elicitation/request":
            if approved:
                result = {"action": "accept"}
                content = _default_elicitation_content(params)
                if content:
                    result["content"] = content
            else:
                result = {"action": "decline"}
        else:
            raise RuntimeError(f"unsupported app-server approval method: {method}")
        self._send({"id": request_id, "result": result})

    def _allocate_request_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    def _send(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("app-server process is not running")
        encoded = json.dumps(payload, ensure_ascii=False)
        with self._write_lock:
            self._proc.stdin.write(encoded + "\n")
            self._proc.stdin.flush()

    def _read_stdout(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        for raw_line in self._proc.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self.error_handler(f"app-server emitted non-JSON stdout: {line}")
                continue
            self._handle_message(message)
        if not self._closed:
            return_code = self._proc.poll()
            self.error_handler(f"codex app-server stdout closed (exit code {return_code})")

    def _read_stderr(self) -> None:
        assert self._proc is not None
        assert self._proc.stderr is not None
        for raw_line in self._proc.stderr:
            line = raw_line.rstrip()
            if not line or self._closed:
                continue
            if "bwrap: loopback:" in line:
                continue
            self.error_handler(f"codex app-server stderr: {line}")
        if not self._closed:
            return_code = self._proc.poll()
            self.error_handler(f"codex app-server stderr closed (exit code {return_code})")

    def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and "method" not in message:
            self._handle_response(message)
            return
        method = str(message.get("method") or "")
        if not method:
            return
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}
        if "id" in message:
            self.server_request_handler(method, message["id"], params)
            return
        self.notification_handler(method, params)

    def _handle_response(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        with self._pending_lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        if "error" in message:
            pending.error = message["error"]
        else:
            pending.result = message.get("result")
        pending.event.set()
