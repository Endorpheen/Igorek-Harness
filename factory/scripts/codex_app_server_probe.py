#!/usr/bin/env python3
"""Minimal spike client for investigating codex app-server approvals."""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any


SERVER_REQUEST_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
}


@dataclass
class PendingCall:
    event: threading.Event
    result: Any | None = None
    error: Any | None = None


class CodexAppServerProbe:
    def __init__(
        self,
        *,
        message: str,
        cwd: str,
        timeout_s: float,
        approval_policy: str,
        sandbox: str,
        auto_approve_mode: str,
        permissions_scope: str,
        model: str | None,
        model_provider: str | None,
        print_raw: bool,
    ) -> None:
        self.message = message
        self.cwd = cwd
        self.timeout_s = timeout_s
        self.approval_policy = approval_policy
        self.sandbox = sandbox
        self.auto_approve_mode = auto_approve_mode
        self.permissions_scope = permissions_scope
        self.model = model
        self.model_provider = model_provider
        self.print_raw = print_raw

        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._pending: dict[int, PendingCall] = {}
        self._pending_lock = threading.Lock()
        self._done = threading.Event()
        self._fatal_error: str | None = None
        self._last_turn_id: str | None = None
        self._last_thread_id: str | None = None
        self._stderr_lines: queue.Queue[str] = queue.Queue()

    def start(self) -> None:
        self._proc = subprocess.Popen(
            ["codex", "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def stop(self) -> None:
        if not self._proc:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def run(self) -> int:
        self.start()
        try:
            init_result = self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "igorek-app-server-probe",
                        "version": "0.1",
                    },
                    "capabilities": {
                        "experimentalApi": True,
                    },
                },
            )
            self._log("initialize ok", init_result)

            thread_params: dict[str, Any] = {
                "cwd": self.cwd,
                "approvalPolicy": self.approval_policy,
                "approvalsReviewer": "user",
                "sandbox": self.sandbox,
                "ephemeral": True,
                "personality": "pragmatic",
            }
            if self.model:
                thread_params["model"] = self.model
            if self.model_provider:
                thread_params["modelProvider"] = self.model_provider

            thread_result = self.request("thread/start", thread_params)
            self._last_thread_id = thread_result["thread"]["id"]
            self._log(
                "thread/start ok",
                {
                    "thread_id": self._last_thread_id,
                    "approvalPolicy": thread_result.get("approvalPolicy"),
                    "approvalsReviewer": thread_result.get("approvalsReviewer"),
                    "sandbox": thread_result.get("sandbox"),
                    "model": thread_result.get("model"),
                    "modelProvider": thread_result.get("modelProvider"),
                },
            )

            turn_result = self.request(
                "turn/start",
                {
                    "threadId": self._last_thread_id,
                    "cwd": self.cwd,
                    "approvalPolicy": self.approval_policy,
                    "approvalsReviewer": "user",
                    "input": [
                        {
                            "type": "text",
                            "text": self.message,
                            "text_elements": [],
                        }
                    ],
                },
            )
            self._last_turn_id = turn_result["turn"]["id"]
            self._log("turn/start ok", {"turn_id": self._last_turn_id})

            if not self._done.wait(timeout=self.timeout_s):
                raise TimeoutError(
                    f"turn did not complete within {self.timeout_s:.1f}s"
                )
            if self._fatal_error:
                raise RuntimeError(self._fatal_error)
            return 0
        finally:
            self.stop()

    def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self._next_request_id()
        pending = PendingCall(event=threading.Event())
        with self._pending_lock:
            self._pending[request_id] = pending
        self._send({"id": request_id, "method": method, "params": params})
        if not pending.event.wait(timeout=self.timeout_s):
            raise TimeoutError(f"timeout waiting for response to {method}")
        if pending.error is not None:
            raise RuntimeError(
                f"{method} failed: {json.dumps(pending.error, ensure_ascii=False)}"
            )
        return pending.result

    def _next_request_id(self) -> int:
        current = self._next_id
        self._next_id += 1
        return current

    def _send(self, payload: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("app-server process is not running")
        line = json.dumps(payload, ensure_ascii=False)
        if self.print_raw:
            print(f">>> {line}", file=sys.stderr)
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _read_stdout(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        for raw_line in self._proc.stdout:
            line = raw_line.strip()
            if not line:
                continue
            if self.print_raw:
                print(f"<<< {line}", file=sys.stderr)
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._log("non-json stdout", line)
                continue
            self._handle_message(message)

    def _read_stderr(self) -> None:
        assert self._proc is not None
        assert self._proc.stderr is not None
        for raw_line in self._proc.stderr:
            line = raw_line.rstrip()
            if line:
                self._stderr_lines.put(line)
                print(f"[codex stderr] {line}", file=sys.stderr)

    def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and "method" not in message:
            self._resolve_pending(message)
            return
        method = message.get("method")
        if not method:
            return
        if "id" in message:
            self._handle_server_request(message)
        else:
            self._handle_notification(method, message.get("params"))

    def _resolve_pending(self, message: dict[str, Any]) -> None:
        request_id = message["id"]
        with self._pending_lock:
            pending = self._pending.pop(request_id, None)
        if not pending:
            self._log("orphan response", message)
            return
        if "error" in message:
            pending.error = message["error"]
        else:
            pending.result = message.get("result")
        pending.event.set()

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message["method"]
        params = message.get("params", {})
        if method not in SERVER_REQUEST_METHODS:
            self._log("unexpected server request", {"method": method, "params": params})
            self._send(
                {
                    "id": message["id"],
                    "error": {
                        "code": -32601,
                        "message": f"unsupported server request: {method}",
                    },
                }
            )
            return

        summary = {"method": method}
        for key in ("threadId", "turnId", "itemId", "approvalId", "reason", "command", "cwd", "permissions"):
            if key in params:
                summary[key] = params[key]
        self._log("approval request", summary)

        if method == "item/permissions/requestApproval":
            result = {
                "permissions": params.get("permissions", {}),
                "scope": self.permissions_scope,
            }
        elif method == "item/fileChange/requestApproval":
            result = {"decision": self.auto_approve_mode}
        else:
            result = {"decision": self.auto_approve_mode}

        self._send({"id": message["id"], "result": result})

    def _handle_notification(self, method: str, params: Any) -> None:
        if method == "error":
            self._fatal_error = json.dumps(params, ensure_ascii=False)
            self._log("server error", params)
            return
        if method == "turn/started":
            turn = (params or {}).get("turn", {})
            self._last_turn_id = turn.get("id", self._last_turn_id)
            self._log("turn/started", {"threadId": params.get("threadId"), "turnId": self._last_turn_id})
            return
        if method == "turn/completed":
            turn = (params or {}).get("turn", {})
            self._last_turn_id = turn.get("id", self._last_turn_id)
            self._log("turn/completed", {"threadId": params.get("threadId"), "turnId": self._last_turn_id})
            self._done.set()
            return
        if method == "server/request/resolved":
            self._log("server/request/resolved", params)
            return
        if method in {
            "thread/started",
            "thread/status/changed",
            "item/started",
            "item/completed",
            "agent/message/delta",
            "reasoning/textDelta",
            "reasoning/summary/textDelta",
            "command/exec/outputDelta",
            "commandExecution/outputDelta",
            "fileChange/outputDelta",
            "turn/planUpdated",
            "turn/diffUpdated",
        }:
            self._log(method, params)
            return
        self._log(method, params)

    def _log(self, label: str, payload: Any) -> None:
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        print(f"[probe] {label}: {rendered}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Минимальный spike-клиент для codex app-server approvals."
    )
    parser.add_argument(
        "message",
        help="Текст user turn для turn/start.",
    )
    parser.add_argument(
        "--cwd",
        default=str(Path(__file__).resolve().parents[1]),
        help="Рабочая директория для thread/start и turn/start.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Таймаут в секундах на response и завершение turn.",
    )
    parser.add_argument(
        "--approval-policy",
        default="untrusted",
        choices=["untrusted", "on-failure", "on-request", "never"],
        help="approvalPolicy для thread/start и turn/start.",
    )
    parser.add_argument(
        "--sandbox",
        default="workspace-write",
        choices=["read-only", "workspace-write", "danger-full-access"],
        help="sandbox режима thread/start.",
    )
    parser.add_argument(
        "--auto-approve-mode",
        default="accept",
        choices=["accept", "acceptForSession", "decline", "cancel"],
        help="Как отвечать на command/file change approval requests.",
    )
    parser.add_argument(
        "--permissions-scope",
        default="turn",
        choices=["turn", "session"],
        help="scope для permissions approval response.",
    )
    parser.add_argument("--model", default=None, help="Override model для thread/start.")
    parser.add_argument(
        "--model-provider",
        default=None,
        help="Override modelProvider для thread/start.",
    )
    parser.add_argument(
        "--print-raw",
        action="store_true",
        help="Печатать сырой JSON-RPC трафик в stderr.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    probe = CodexAppServerProbe(
        message=args.message,
        cwd=args.cwd,
        timeout_s=args.timeout,
        approval_policy=args.approval_policy,
        sandbox=args.sandbox,
        auto_approve_mode=args.auto_approve_mode,
        permissions_scope=args.permissions_scope,
        model=args.model,
        model_provider=args.model_provider,
        print_raw=args.print_raw,
    )
    try:
        return probe.run()
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
