from __future__ import annotations

from dataclasses import dataclass
from typing import Any


KNOWN_APP_SERVER_REQUEST_METHODS = {
    "item/fileChange/requestApproval",
    "item/commandExecution/requestApproval",
    "item/permissions/requestApproval",
    "mcpServer/elicitation/request",
}


@dataclass(frozen=True)
class CodexNativeApproval:
    method: str
    policy_type: str
    ui_type: str
    summary: str
    target: str
    default_scope: str


def describe_app_server_request(
    method: str,
    params: dict[str, Any],
    *,
    item_snapshot: dict[str, Any] | None = None,
) -> CodexNativeApproval:
    if method == "item/fileChange/requestApproval":
        target = ""
        item = item_snapshot if isinstance(item_snapshot, dict) else {}
        changes = item.get("changes") if isinstance(item, dict) else None
        if isinstance(changes, list) and changes:
            first = changes[0]
            if isinstance(first, dict):
                target = str(first.get("path") or "")
        return CodexNativeApproval(
            method=method,
            policy_type="codex_file_change",
            ui_type="file_change",
            summary="изменение файлов",
            target=target,
            default_scope="turn",
        )

    if method == "item/commandExecution/requestApproval":
        command = params.get("command")
        target = ""
        summary = "выполнение команды"
        if isinstance(command, list) and command:
            target = " ".join(str(part) for part in command)
            summary = f"выполнение команды: {command[0]}"
        elif command is not None:
            target = str(command)
        return CodexNativeApproval(
            method=method,
            policy_type="codex_command_execution",
            ui_type="exec_command",
            summary=summary,
            target=target,
            default_scope="turn",
        )

    if method == "item/permissions/requestApproval":
        reason = str(params.get("reason") or "")
        return CodexNativeApproval(
            method=method,
            policy_type="codex_permissions",
            ui_type="permissions",
            summary="дополнительные разрешения",
            target=reason,
            default_scope="turn",
        )

    if method == "mcpServer/elicitation/request":
        mode = str(params.get("mode") or "form").strip() or "form"
        message = str(params.get("message") or "")
        url = str(params.get("url") or "")
        target = url or message
        summary = "запрос данных от MCP"
        if mode == "url":
            summary = "MCP-запрос подтверждения по ссылке"
        elif mode == "form":
            summary = "MCP-запрос дополнительных данных"
        return CodexNativeApproval(
            method=method,
            policy_type="codex_mcp_elicitation",
            ui_type="mcp_elicitation",
            summary=summary,
            target=target,
            default_scope="turn",
        )

    return CodexNativeApproval(
        method=method,
        policy_type="codex_unknown",
        ui_type="опасное действие",
        summary=method,
        target="",
        default_scope="turn",
    )


def is_known_app_server_request_method(method: str) -> bool:
    return method in KNOWN_APP_SERVER_REQUEST_METHODS
