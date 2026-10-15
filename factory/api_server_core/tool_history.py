import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from factory.api_server_core.file_policy import resolve_local_path, sanitize_local_path_candidate


@dataclass
class ToolExchange:
    tool_name: str
    target: str
    tool_input_obj: dict[str, Any]
    result_text: str
    is_error: bool
    index: int


def tool_target_from_input(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    extract_bash_file_target: Callable[[str], str],
) -> str:
    for key in ("file_path", "file_name", "filename", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            sanitized = sanitize_local_path_candidate(value)
            return sanitized or ""
    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            return extract_bash_file_target(command)
    return ""


def tool_action_signature(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    extract_bash_file_target: Callable[[str], str],
) -> tuple[str, str, str]:
    target = tool_target_from_input(
        tool_name,
        tool_input,
        extract_bash_file_target=extract_bash_file_target,
    )
    if tool_name == "Bash":
        command = tool_input.get("command")
        payload = command.strip() if isinstance(command, str) else ""
    else:
        payload = json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
    return tool_name, target, payload


def extract_tool_exchanges(
    req: Any,
    *,
    flatten_block_content: Callable[..., str],
    looks_like_tool_error_payload: Callable[[str], bool],
    extract_bash_file_target: Callable[[str], str],
) -> list[ToolExchange]:
    pending: dict[str, tuple[str, str, dict[str, Any]]] = {}
    exchanges: list[ToolExchange] = []
    index = 0

    for msg in req.messages:
        content = msg.content
        if isinstance(content, str):
            continue
        if msg.role == "assistant":
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                raw_input = block.get("input", {})
                tool_input_obj = raw_input if isinstance(raw_input, dict) else {}
                target = tool_target_from_input(
                    str(block.get("name", "")),
                    tool_input_obj,
                    extract_bash_file_target=extract_bash_file_target,
                )
                pending[str(block.get("id", ""))] = (
                    str(block.get("name", "")),
                    target,
                    tool_input_obj,
                )
        elif msg.role == "user":
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                tool_use_id = str(block.get("tool_use_id", ""))
                tool_name, target, tool_input_obj = pending.get(tool_use_id, ("", "", {}))
                result_text = flatten_block_content([block], role=msg.role)
                is_error = bool(block.get("is_error")) or looks_like_tool_error_payload(result_text)
                exchanges.append(
                    ToolExchange(
                        tool_name=tool_name,
                        target=target,
                        tool_input_obj=tool_input_obj,
                        result_text=result_text,
                        is_error=is_error,
                        index=index,
                    )
                )
                index += 1

    return exchanges


def is_duplicate_of_latest_successful_tool_turn(
    req: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    flatten_block_content: Callable[..., str],
    looks_like_tool_error_payload: Callable[[str], bool],
    extract_bash_file_target: Callable[[str], str],
) -> bool:
    exchanges = extract_tool_exchanges(
        req,
        flatten_block_content=flatten_block_content,
        looks_like_tool_error_payload=looks_like_tool_error_payload,
        extract_bash_file_target=extract_bash_file_target,
    )
    if not exchanges:
        return False

    latest = exchanges[-1]
    if latest.is_error:
        return False

    latest_signature = tool_action_signature(
        latest.tool_name,
        latest.tool_input_obj,
        extract_bash_file_target=extract_bash_file_target,
    )
    candidate_signature = tool_action_signature(
        tool_name,
        tool_input,
        extract_bash_file_target=extract_bash_file_target,
    )
    return latest_signature == candidate_signature


def extract_last_tool_file(
    req: Any,
    *,
    flatten_block_content: Callable[..., str],
    looks_like_tool_error_payload: Callable[[str], bool],
    extract_bash_file_target: Callable[[str], str],
) -> str | None:
    exchanges = extract_tool_exchanges(
        req,
        flatten_block_content=flatten_block_content,
        looks_like_tool_error_payload=looks_like_tool_error_payload,
        extract_bash_file_target=extract_bash_file_target,
    )
    for exchange in reversed(exchanges):
        if not exchange.is_error:
            persisted = _extract_persisted_output_path(exchange.result_text)
            if persisted:
                return persisted
        if exchange.target:
            return exchange.target
    for msg in reversed(req.messages):
        if msg.role != "assistant" or isinstance(msg.content, str):
            continue
        for block in reversed(msg.content):
            if block.get("type") != "tool_use":
                continue
            raw_input = block.get("input", {})
            if not isinstance(raw_input, dict):
                continue
            target = tool_target_from_input(
                str(block.get("name", "")),
                raw_input,
                extract_bash_file_target=extract_bash_file_target,
            )
            if target:
                return target
    return None


def _extract_persisted_output_path(text: str) -> str | None:
    match = re.search(r"(?im)Full output saved to:\s*(/[^\s]+)", text or "")
    if not match:
        return None
    return sanitize_local_path_candidate(match.group(1))


def extract_last_successful_read_file(
    req: Any,
    *,
    flatten_block_content: Callable[..., str],
    looks_like_tool_error_payload: Callable[[str], bool],
    extract_bash_file_target: Callable[[str], str],
) -> str | None:
    exchanges = extract_tool_exchanges(
        req,
        flatten_block_content=flatten_block_content,
        looks_like_tool_error_payload=looks_like_tool_error_payload,
        extract_bash_file_target=extract_bash_file_target,
    )
    for exchange in reversed(exchanges):
        if exchange.tool_name == "Read" and not exchange.is_error and exchange.target:
            return exchange.target
    return None


def _bash_cd_target(command_segment: str) -> str | None:
    try:
        tokens = shlex.split(command_segment)
    except ValueError:
        tokens = command_segment.strip().split()
    if len(tokens) < 2 or tokens[0] != "cd":
        return None
    return sanitize_local_path_candidate(tokens[1])


def _cwd_after_bash_command(command: str, cwd: Path) -> Path:
    current = cwd
    for segment in re.split(r"\s*(?:&&|;)\s*", command):
        stripped = segment.strip()
        if not stripped:
            continue
        cd_target = _bash_cd_target(stripped)
        if not cd_target:
            continue
        current = resolve_local_path(cd_target, base_dir=current)
    return current


def extract_last_shell_cwd(
    req: Any,
    *,
    flatten_block_content: Callable[..., str],
    looks_like_tool_error_payload: Callable[[str], bool],
    extract_bash_file_target: Callable[[str], str],
    initial_cwd: str | None = None,
) -> str | None:
    exchanges = extract_tool_exchanges(
        req,
        flatten_block_content=flatten_block_content,
        looks_like_tool_error_payload=looks_like_tool_error_payload,
        extract_bash_file_target=extract_bash_file_target,
    )
    cwd = Path(initial_cwd).expanduser().resolve() if initial_cwd else None
    saw_successful_bash = False
    for exchange in exchanges:
        if exchange.tool_name != "Bash" or exchange.is_error:
            continue
        command = str(exchange.tool_input_obj.get("command", "")).strip()
        if not command:
            continue
        if cwd is not None:
            cwd = _cwd_after_bash_command(command, cwd)
        saw_successful_bash = True
    return str(cwd) if saw_successful_bash and cwd is not None else None


def extract_latest_successful_read_text(exchanges: list[ToolExchange], filename: str) -> str:
    target_name = Path(filename).name
    for exchange in reversed(exchanges):
        if exchange.tool_name != "Read" or exchange.is_error:
            continue
        if Path(exchange.target).name != target_name:
            continue
        lines: list[str] = []
        for line in exchange.result_text.splitlines():
            if "<system-reminder>" in line:
                break
            lines.append(re.sub(r"^\s*\d+→", "", line))
        return "\n".join(lines).strip()
    return ""


def latest_success_index(exchanges: list[ToolExchange], filename: str, tool_names: set[str]) -> int:
    target_name = Path(filename).name
    latest = -1
    for exchange in exchanges:
        if exchange.is_error or exchange.tool_name not in tool_names:
            continue
        if Path(exchange.target).name != target_name:
            continue
        latest = max(latest, exchange.index)
    return latest


def completed_edit_pairs(exchanges: list[ToolExchange], filename: str) -> set[tuple[str, str]]:
    target_name = Path(filename).name
    completed: set[tuple[str, str]] = set()
    for exchange in exchanges:
        if exchange.is_error or exchange.tool_name not in {"Edit", "Update", "MultiEdit"}:
            continue
        if Path(exchange.target).name != target_name:
            continue
        old_string = str(exchange.tool_input_obj.get("old_string", "")).strip()
        new_string = str(exchange.tool_input_obj.get("new_string", "")).strip()
        if old_string and new_string:
            completed.add((old_string, new_string))
    return completed
