from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CodexToolEvent:
    event_type: str
    call_id: str
    name: str = ""
    arguments: dict[str, Any] | None = None
    output_preview: str = ""
    output_chars: int = 0
    exit_code: int | None = None
    summary: str = ""


def parse_codex_json_events(stdout_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type"):
            events.append(payload)
    return events


def extract_codex_final_text(stdout_text: str) -> str:
    return _extract_codex_final_text(stdout_text, require_after_tool_result=False)


def extract_codex_final_text_after_tool_results(stdout_text: str) -> str:
    return _extract_codex_final_text(stdout_text, require_after_tool_result=True)


def _extract_codex_final_text(
    stdout_text: str,
    *,
    require_after_tool_result: bool,
) -> str:
    final_text = ""
    final_text_index = -1
    last_tool_result_index = -1
    for index, event in enumerate(parse_codex_json_events(stdout_text)):
        payload = _event_payload(event)
        event_type = str(payload.get("type") or "")
        if event_type == "function_call_output":
            last_tool_result_index = index
            continue
        if event_type == "item.completed":
            item = payload.get("item")
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") != "agent_message":
                continue
            item_text = item.get("text")
            if isinstance(item_text, str) and item_text.strip():
                final_text = item_text.strip()
                final_text_index = index
            continue
        if event_type not in {"agent_message", "message"}:
            continue
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            final_text = message.strip()
            final_text_index = index
            continue
        content = payload.get("content")
        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
            if texts:
                final_text = "\n".join(texts)
                final_text_index = index
    if (
        require_after_tool_result
        and last_tool_result_index >= 0
        and final_text_index < last_tool_result_index
    ):
        return ""
    return final_text


def extract_codex_usage(stdout_text: str) -> dict[str, int]:
    usage_summary: dict[str, int] = {}
    for event in parse_codex_json_events(stdout_text):
        payload = _event_payload(event)
        event_type = str(payload.get("type") or "")
        usage = None
        if event_type == "turn.completed":
            usage = payload.get("usage")
        elif event_type == "token_count":
            info = payload.get("info")
            if isinstance(info, dict):
                usage = info.get("last_token_usage") or info.get("total_token_usage")
        if not isinstance(usage, dict):
            continue
        usage_summary = _usage_summary(usage)
        model_context_window = None
        if event_type == "token_count":
            info = payload.get("info")
            if isinstance(info, dict):
                model_context_window = _int_value(info, "model_context_window")
        else:
            model_context_window = _int_value(
                usage,
                "model_context_window",
                "modelContextWindow",
            )
        if model_context_window is not None:
            usage_summary["model_context_window"] = model_context_window
    return usage_summary


def extract_codex_tool_events(stdout_text: str) -> list[CodexToolEvent]:
    tool_events: list[CodexToolEvent] = []
    call_names: dict[str, str] = {}
    for event in parse_codex_json_events(stdout_text):
        payload = _event_payload(event)
        event_type = str(payload.get("type") or "")
        if event_type == "function_call":
            call_id = str(payload.get("call_id") or "").strip()
            name = str(payload.get("name") or "").strip()
            arguments = _parse_arguments(payload.get("arguments"))
            if call_id and name:
                call_names[call_id] = name
            tool_events.append(
                CodexToolEvent(
                    event_type="function_call",
                    call_id=call_id,
                    name=name,
                    arguments=arguments,
                    summary=_function_call_summary(name, arguments),
                )
            )
        elif event_type == "function_call_output":
            call_id = str(payload.get("call_id") or "").strip()
            output = str(payload.get("output") or "")
            exit_code = _extract_exit_code(output)
            name = call_names.get(call_id, "")
            tool_events.append(
                CodexToolEvent(
                    event_type="function_call_output",
                    call_id=call_id,
                    name=name,
                    output_preview=_preview(output),
                    output_chars=len(output),
                    exit_code=exit_code,
                    summary=_function_output_summary(name, output, exit_code),
                )
            )
    return tool_events


def extract_codex_tool_result_context(
    stdout_text: str,
    *,
    per_result_limit: int = 4000,
    total_limit: int = 12000,
) -> str:
    call_names: dict[str, str] = {}
    chunks: list[str] = []
    total_chars = 0
    for event in parse_codex_json_events(stdout_text):
        payload = _event_payload(event)
        event_type = str(payload.get("type") or "")
        if event_type == "function_call":
            call_id = str(payload.get("call_id") or "").strip()
            name = str(payload.get("name") or "").strip()
            arguments = _parse_arguments(payload.get("arguments"))
            if call_id and name:
                call_names[call_id] = _function_call_summary(name, arguments)
            continue
        if event_type != "function_call_output":
            continue
        call_id = str(payload.get("call_id") or "").strip()
        label = call_names.get(call_id, call_id or "tool_result")
        output = str(payload.get("output") or "")
        if not output.strip():
            continue
        excerpt = _preview(output, limit=per_result_limit)
        chunk = f"### {label}\n{excerpt}"
        if total_chars + len(chunk) > total_limit:
            remaining = max(total_limit - total_chars, 0)
            if remaining <= 0:
                break
            chunk = chunk[:remaining]
        chunks.append(chunk)
        total_chars += len(chunk)
        if total_chars >= total_limit:
            break
    return "\n\n".join(chunks).strip()


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("type"), str):
        return payload
    return event


def _int_value(source: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, int):
            return value
    return None


def _usage_summary(usage: dict[str, Any]) -> dict[str, int]:
    input_tokens = _int_value(usage, "input_tokens", "inputTokens")
    cached_input_tokens = _int_value(usage, "cached_input_tokens", "cachedInputTokens")
    output_tokens = _int_value(usage, "output_tokens", "outputTokens")
    reasoning_output_tokens = _int_value(
        usage,
        "reasoning_output_tokens",
        "reasoningOutputTokens",
    )
    total_tokens = _int_value(usage, "total_tokens", "totalTokens")
    result = {
        "prompt": input_tokens or 0,
        "completion": output_tokens or 0,
        "input_tokens": input_tokens or 0,
        "cached_input_tokens": cached_input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "reasoning_output_tokens": reasoning_output_tokens or 0,
    }
    if total_tokens is not None:
        result["total_tokens"] = total_tokens
    return result


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return loaded if isinstance(loaded, dict) else {"raw": value}


def _function_call_summary(name: str, arguments: dict[str, Any]) -> str:
    if name == "exec_command":
        command = str(arguments.get("cmd") or arguments.get("command") or "").strip()
        return f"exec_command: {_preview(command, limit=120)}" if command else name
    return name or "function_call"


def _function_output_summary(name: str, output: str, exit_code: int | None) -> str:
    parts = [name or "function_call_output", f"{len(output)} chars"]
    if exit_code is not None:
        parts.append(f"exit {exit_code}")
    return ", ".join(parts)


def _extract_exit_code(output: str) -> int | None:
    match = re.search(r"Process exited with code\s+(-?\d+)", output)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _preview(value: str, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
