"""CC-backend executor: multi-turn tool use loop.

Sends requests to the Anthropic Messages API-compatible CC backend,
handles tool_use responses, executes tools locally, and sends results
back in a loop until the model returns a text-only response.
"""

from __future__ import annotations

import json
import http.client
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
import urllib.parse
from itertools import count
from collections.abc import Callable
from typing import Any

from .agent_loop_limits import load_cc_backend_loop_limits
from .cc_backend_trace import append_cc_backend_trace, scoped_agent_trace, model_failure_diagnostics, browser_tool_diagnostics
from .checkpoint_manager import CheckpointManager
from .cc_backend_tools import execute_tool, get_tool_schemas, needs_approval
from .multimodal_payload import build_anthropic_user_content
from .llama_image_input import LlamaImageInput
from .model_backend_capabilities import ModelBackendCapabilities
from .session_history import project_history_messages
from .local_history_output import HistoricalToolEchoFilter, contains_historical_tool_echo, local_history_guard_enabled
from .completion_review import (
    COMPLETION_REVIEW_SCHEMA, CompletionRecovery, completion_review_enabled,
    parse_completion_review, review_transcript,
)
from .post_turn_guards import (
    APPROVAL_REQUIRED,
    RETRY_REQUIRED,
    evaluate_text_completion_guards,
)
from .tool_result_outcome import ToolResultText, tool_result_succeeded
from .session_budget import (
    DECISION_FORCE_SYNTHESIS,
    REASON_REPEATED_TOOL_LOOP,
    SessionBudget,
)
from .session_changes import TaskChangeTracker
from .session_compaction import (
    build_compacted_messages,
    build_compaction_dry_run_plan,
    estimate_context_tokens,
    evaluate_compaction_pressure,
    write_manual_compaction_artifact,
    summarize_history,
    archive_summary_history,
    read_compaction_guards,
)
from .tool_result_store import DEFAULT_TOOL_RESULT_STORE_CONFIG, enforce_turn_budget, maybe_persist_tool_result
from .tool_result_access import ToolResultAccess
from .trace_taxonomy import CcTraceEvent
from .diagnostics import safe_text, observation
from .iteration_budget import IterationBudget
from .tool_runtime_context import (
    set_runtime_context,
    clear_runtime_context,
    is_interrupt_requested,
    notify_progress,
)
from .tool_call_salvage import (
    try_salvage_textual_tool_call,
    contains_textual_tool_artifact,
    build_textual_tool_retry_message,
    get_salvage_meta,
    strip_salvage_meta,
)

from .reasoning_tags import TaggedReasoningSplitter


def _normalize_tagged_reasoning_blocks(content_blocks: object) -> list[dict]:
    """Promote provider reasoning shapes into the internal reasoning channel."""
    if not isinstance(content_blocks, list):
        return []
    normalized: list[dict] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "thinking":
            reasoning = str(block.get("thinking") or block.get("text") or "").strip()
            if reasoning:
                normalized.append({"type": "__reasoning", "text": reasoning})
            continue
        if block.get("type") != "text":
            normalized.append(block)
            continue
        splitter = TaggedReasoningSplitter()
        parts = splitter.feed(str(block.get("text") or "")) + splitter.finish()
        converted: list[dict] = []
        for kind, value in parts:
            if kind == "__reasoning":
                value = value.strip()
            if value:
                if converted and converted[-1].get("type") == kind and kind == "text":
                    converted[-1]["text"] += value
                else:
                    converted.append({"type": kind, "text": value})
        normalized.extend(converted)
    return normalized


DEFAULT_CONTEXT_WINDOW_TOKENS = 32_000
MAX_TRANSIENT_NETWORK_RETRIES = 2
TRANSIENT_NETWORK_RETRY_DELAYS_S = (0.25, 0.75)

# Raw tool call line resembling: Write(...), Bash(...), Read(...) and similar.
_RAW_TOOL_CALL_LINE = re.compile(r'^[A-Z][A-Za-z_]{2,}\s*\(')


def _is_transient_network_error(error: str, *, error_code: str | None = None,
                                error_diagnostics: dict | None = None) -> bool:
    """Return True only for transport failures that are safe to retry."""
    if error_code is not None:
        from .diagnostics import CODES
        if isinstance(error_code, str) and error_code in CODES:
            return CODES[error_code]['retryable']
        if error_code == 'responses_failed':
            details = error_diagnostics or {}
            if details.get('provider_error_code') in {'insufficient_quota','invalid_api_key','invalid_token','access_denied','context_length_exceeded'}:
                return False
            return details.get('http_status') in {429, 500, 502, 503, 504, 529}
        if error_code == 'responses_tls':
            details = error_diagnostics or {}
            kind, reason = details.get('tls_error_type'), details.get('tls_reason')
            if (kind == 'SSLCertVerificationError' or reason == 'CERTIFICATE_VERIFY_FAILED'
                    or details.get('tls_verify_code') is not None):
                return False
            if kind in {'SSLEOFError', 'SSLZeroReturnError'}:
                return reason in {None, 'OTHER', 'UNEXPECTED_EOF_WHILE_READING'}
            return kind == 'SSLError' and reason in {'UNEXPECTED_EOF_WHILE_READING', 'TLSV1_ALERT_INTERNAL_ERROR'}
        return error_code in ("responses_network", "responses_timeout")
    normalized = str(error or "").strip().lower()
    if not normalized or normalized.startswith("http "):
        return False
    markers = (
        "temporary failure in name resolution",
        "name or service not known",
        "nodename nor servname provided",
        "connection reset",
        "connection refused",
        "connection aborted",
        "transport:remote_disconnected",
        "remote end closed connection without response",
        "remote disconnected",
        "timed out",
        "timeout",
        "temporary unavailable",
        "try again",
    )
    return any(marker in normalized for marker in markers)


# API upstream in thinking mode (e.g., DeepSeek via opencode zen and similar gateways)
# requires returning `reasoning_content` from previous assistant messages with `tool_calls`
# back to the API. An error is returned as HTTP 400 from the upstream.
_REASONING_PASSBACK_ERROR_MARKERS = ("reasoning_content", "must be passed back")


def _is_reasoning_passback_error(error: str) -> bool:
    """# Applies only to the error: 'reasoning_content must be passed back'."""
    lowered = str(error or "").lower()
    if not lowered:
        return False
    return all(marker in lowered for marker in _REASONING_PASSBACK_ERROR_MARKERS)


def _clean_preamble(text_parts: list[str]) -> str:
    """# Removes raw garbage from the preamble, such as: Write(file_path=..., content=...)."""
    all_lines: list[str] = []
    for part in text_parts:
        all_lines.extend(part.split('\n'))
    # Searches for the first line resembling a `tool call`—truncates everything below it.
    for i, line in enumerate(all_lines):
        if _RAW_TOOL_CALL_LINE.match(line.strip()):
            all_lines = all_lines[:i]
            break
    # Remove trailing empty lines
    while all_lines and not all_lines[-1].strip():
        all_lines.pop()
    return '\n'.join(all_lines).strip()


def _schedule_created_message(result_text: str) -> str | None:
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "created":
        return None
    message = str(payload.get("message") or "").strip()
    if message:
        return message
    schedule = str(payload.get("schedule") or "").strip()
    if schedule:
        return f"Ок, задача создана и выполнится в {schedule} (московское время)."
    return "Ок, отложенная задача создана."


def _is_create_schedule_call(tool_name: str) -> bool:
    return str(tool_name or "").strip() == "create_schedule"


def _build_policy_denial_retry_message(reason: str) -> str:
    return (
        "[СЛУЖЕБНОЕ. Это внутренняя инструкция от системы, не комментируй её в ответе пользователю.] "
        "Твой предыдущий ответ был отклонён системной политикой безопасности. "
        f"Причина: {reason}.\n\n"
        "Исправь свой подход:\n"
        "- Не генерируй пути вне рабочей директории проекта.\n"
        "- Не ссылайся на файлы, которые ты не создал в текущей задаче.\n"
        "- Если тебе нужно создать файл — используй инструмент Write с путём внутри рабочей директории.\n"
        "Повтори запрос корректно."
    )


def _build_reasoning_only_retry_message() -> str:
    return (
        "[СЛУЖЕБНОЕ. Это внутренняя инструкция от системы, не комментируй её в ответе пользователю.] "
        "Твой предыдущий ответ содержал только скрытое reasoning/thinking и не содержал "
        "видимого ответа пользователю или tool_use.\n\n"
        "Продолжи выполнение задачи одним из двух корректных способов:\n"
        "- если нужно продолжать работу — вызови следующий инструмент через настоящий tool_use;\n"
        "- если задача завершена — дай видимый текстовый ответ пользователю с результатом."
    )


def _tool_call_signature(tool_name: str, tool_input: Any) -> tuple[str, str]:
    """Return a stable signature for exact repeated tool-call detection."""
    try:
        normalized_input = json.dumps(
            tool_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError):
        normalized_input = repr(tool_input)
    return str(tool_name or ""), normalized_input


def _build_initial_messages(
    *,
    history_messages: list[dict[str, Any]] | None,
    trailing_context_text: str,
    user_message: str,
    input_files: list[str] | None,
    native_tool_history: bool = False,
    history_message_ids: dict[int, tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    """Assemble the initial chat messages for a task.

    Prior turns retain their text projection unless confirmed llama.cpp enables
    native tool history from paired event sidecars. Dynamic context belongs in
    the new user message so templates keep the system prompt at the beginning.
    """
    projected = project_history_messages(history_messages, native_tool_history=native_tool_history)
    messages = [message for message, _ in projected]
    if history_message_ids is not None:
        history_message_ids.update({id(message): origins for message, origins in projected if origins})
    user_content = build_anthropic_user_content(
        user_message, input_files=input_files
    )
    trailing = trailing_context_text.strip()
    if trailing:
        # Qwen/llama-server templates require system messages to be at the very
        # beginning, so dynamic context is folded into the new user message
        # instead of being sent as a mid-conversation system message.
        if isinstance(user_content, list):
            user_content = [{"type": "text", "text": f"{trailing}\n\n"}] + user_content
        else:
            user_content = f"{trailing}\n\n{user_content}"
    messages.append({"role": "user", "content": user_content})
    return messages


def _tool_denied_by_policy(tool_name: str, metadata: dict[str, Any]) -> bool:
    """Read-only/deny enforcement for stable-prefix profiles.

    The wire toolset stays constant for KV-cache reuse; per-turn restrictions
    (e.g. ``igorek-self-inspection`` is read-only) are enforced here instead of
    by reshaping the tool list.
    """
    restrict_to = metadata.get("restrict_tools_to")
    if isinstance(restrict_to, list) and restrict_to:
        allowed = {str(name) for name in restrict_to}
        if str(tool_name) not in allowed:
            return True
    blocked = metadata.get("block_tool_names")
    if isinstance(blocked, list) and blocked:
        if str(tool_name) in {str(name) for name in blocked}:
            return True
    return False


def _approval_signature(tool_name: str, tool_input: Any) -> str:
    name, normalized_input = _tool_call_signature(tool_name, tool_input)
    return f"{name}:{normalized_input}"


def resync_approval_signatures(blocked_tools: Any) -> list[dict[str, Any]]:
    # Keep the signature a function of the stored input; redaction rewrites the signature string.
    if not isinstance(blocked_tools, list):
        return []
    entries: list[dict[str, Any]] = []
    for blocked in blocked_tools:
        if not isinstance(blocked, dict):
            continue
        entry = dict(blocked)
        tool_name = str(entry.get("tool") or "")
        tool_input = entry.get("input") if isinstance(entry.get("input"), dict) else {}
        entry["signature"] = _approval_signature(tool_name, tool_input)
        rule = entry.get("session_rule")
        if isinstance(rule, dict) and rule.get("rule_type") == "exact_tool_call":
            entry["session_rule"] = {**rule, "signature": entry["signature"]}
        entries.append(entry)
    return entries


def _approval_session_rule(tool_name: str, tool_input: Any) -> dict[str, str]:
    """Return the narrow reusable approval rule offered to the Rich client."""
    name = str(tool_name or "")
    normalized_name = name.lower()
    if isinstance(tool_input, dict) and normalized_name in {
        "write",
        "edit",
        "notebookedit",
    }:
        file_path = str(tool_input.get("file_path") or "").strip()
        if file_path:
            return {"rule_type": "file_path", "tool": name, "file_path": file_path}
    return {
        "rule_type": "exact_tool_call",
        "tool": name,
        "signature": _approval_signature(name, tool_input),
    }


def _matches_approval_session_rule(
    rule: Any,
    *,
    tool_name: str,
    tool_input: Any,
) -> bool:
    if not isinstance(rule, dict):
        return False
    if str(rule.get("tool") or "") != str(tool_name or ""):
        return False

    rule_type = str(rule.get("rule_type") or "")
    if rule_type == "exact_tool_call":
        return str(rule.get("signature") or "") == _approval_signature(
            tool_name, tool_input
        )
    if rule_type == "file_path" and isinstance(tool_input, dict):
        return str(rule.get("file_path") or "") == str(
            tool_input.get("file_path") or ""
        ).strip()
    return False


def _approved_tool_call(
    *,
    metadata: dict[str, Any] | None,
    tool_name: str,
    tool_input: Any,
) -> bool:
    if not isinstance(metadata, dict):
        return False

    if metadata.get("auto_approve"):
        return True

    signatures = metadata.get("approved_tool_calls")
    if isinstance(signatures, list):
        current = _approval_signature(tool_name, tool_input)
        if current in {str(item) for item in signatures}:
            return True

    session_rules = metadata.get("approved_session_rules")
    if isinstance(session_rules, list) and any(
        _matches_approval_session_rule(
            rule,
            tool_name=tool_name,
            tool_input=tool_input,
        )
        for rule in session_rules
    ):
        return True

    # Backward compatibility for Rich sessions started before scoped rules existed.
    session_tool_names = metadata.get("approved_session_tools")
    if isinstance(session_tool_names, list) and tool_name in {
        str(item) for item in session_tool_names
    }:
        return True

    # Backward-compatible escape hatch for the interrupted partial implementation.
    tool_names = metadata.get("approved_tools")
    if isinstance(tool_names, list) and tool_name in {str(item) for item in tool_names}:
        return True

    return False


def _consume_approved_tool_call(
    *,
    metadata: dict[str, Any] | None,
    tool_name: str,
    tool_input: Any,
    tool_use_id: str = "",
) -> bool:
    """Consume the one-shot approval matching this call.

    ``tool_use_id`` is optional (todo 9): when the caller knows the call id, the
    consumption is idempotent per id - a replay/retry of the same approved call
    returns ``True`` again instead of finding the signature already gone and
    re-prompting, while a different input or a new call id never matches the
    recorded marker. Callers that omit the id keep the exact previous behavior.
    """
    if not isinstance(metadata, dict):
        return False
    signatures = metadata.get("approved_tool_calls")
    if not isinstance(signatures, list):
        return False
    current = _approval_signature(tool_name, tool_input)
    marker = str(tool_use_id or "").strip()
    markers = metadata.get("consumed_approved_tool_calls")
    markers = markers if isinstance(markers, dict) else {}
    if marker and str(markers.get(marker) or "") == current:
        return True
    normalized = [str(item) for item in signatures]
    try:
        index = normalized.index(current)
    except ValueError:
        return False
    normalized.pop(index)
    metadata["approved_tool_calls"] = normalized
    if marker:
        metadata["consumed_approved_tool_calls"] = {**markers, marker: current}
    return True


def _approval_target(tool_name: str, tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    name = str(tool_name or "").lower()
    if name in {"write", "edit"}:
        return str(tool_input.get("file_path") or "")
    if name == "bash":
        return str(tool_input.get("command") or "")
    if name in {'calendar_create_event','calendar_move_event'}:
        title=str(tool_input.get('summary') or tool_input.get('event_id') or '')
        guests=tool_input.get('attendees') or []
        return 'Google Календарь\n'+title+'\n'+str(tool_input.get('start') or '')+' — '+str(tool_input.get('end') or '')+'\n'+('Приглашения: '+', '.join(str(v) for v in guests) if guests else 'Без новых приглашений')
    return ""


def _write_target_path(tool_name: str, tool_input: Any, workdir: str) -> str:
    if str(tool_name or "") != "Write" or not isinstance(tool_input, dict):
        return ""
    raw_path = str(tool_input.get("file_path") or "").strip()
    if not raw_path:
        return ""
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(workdir) / path
    return str(path)


def _blocked_tool_payload(tool_name: str, tool_input: Any, *, source: str = "", task_id: str = "", current_session: str = "") -> dict[str, Any]:
    payload = {
        "tool": str(tool_name or ""),
        "input": tool_input if isinstance(tool_input, dict) else {},
        "target": _approval_target(str(tool_name or ""), tool_input),
        "signature": _approval_signature(str(tool_name or ""), tool_input),
        "session_rule": _approval_session_rule(str(tool_name or ""), tool_input),
    }
    reason = _approval_reason(tool_input)
    if tool_name in {'calendar_create_event','calendar_move_event'} and isinstance(tool_input,dict):
        from .mcp_bridge import calendar_approval_context
        context=calendar_approval_context(task_id,current_session,str(tool_input.get('event_id') or ''))
        calendar=context.get('calendar',{});event=context.get('event',{})
        lines=['Google Календарь: '+str(calendar.get('name') or ''),('Создать: ' if tool_name=='calendar_create_event' else 'Перенести: ')+str(tool_input.get('summary') or event.get('summary') or tool_input.get('event_id') or '')]
        if tool_name=='calendar_move_event' and event:
            lines.append('Было: '+str((event.get('start') or {}).get('dateTime') or (event.get('start') or {}).get('date') or '')+' — '+str((event.get('end') or {}).get('dateTime') or (event.get('end') or {}).get('date') or ''))
        lines.append('Время: '+str(tool_input.get('start') or '')+' — '+str(tool_input.get('end') or ''))
        if calendar.get('time_zone'):lines.append('Часовой пояс: '+calendar['time_zone'])
        for key,label in [('location','Место'),('description','Описание')]:
            if tool_input.get(key):lines.append(label+': '+str(tool_input[key]))
        guests=tool_input.get('attendees') or []
        lines.append('Приглашения: '+', '.join(str(v) for v in guests) if guests else 'Без новых приглашений')
        payload['target']='\n'.join(lines)
    if reason:
        payload["reason"] = reason
    if source:
        payload["source"] = source
    return payload


def _approval_reason(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in ("reason", "justification"):
        value = str(tool_input.get(key) or "").strip()
        if value:
            return value
    return ""


def _missing_required_tool_fields(
    tools: list[dict[str, Any]],
    tool_name: str,
    tool_input: Any,
) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    schema: dict[str, Any] | None = None
    for tool in tools:
        if str(tool.get("name") or "") == tool_name:
            raw_schema = tool.get("input_schema")
            if isinstance(raw_schema, dict):
                schema = raw_schema
            break
    if not schema:
        return []
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    return [
        str(field)
        for field in required
        if isinstance(field, str) and field not in tool_input
    ]


def _bash_web_redirect_enabled(profile: str, base_url: str, *,
                               capabilities: ModelBackendCapabilities | None = None, model: str = "") -> bool:
    try:
        from .runtime import get_profile_config

        config = get_profile_config(profile) if profile else {}
    except Exception:
        config = {}
    explicit = config.get("redirect_bash_web_to_tools")
    if isinstance(explicit, bool):
        return explicit

    return local_history_guard_enabled(base_url, capabilities=capabilities, model=model)


def _redirect_bash_web(
    tool_input: dict[str, Any],
    *,
    enabled: bool = True,
) -> tuple[str, dict[str, Any]] | None:
    """If Bash is being used for web fetching via curl/wget, redirect to web_search/web_extract.

    Returns (tool_name, tool_input) for the redirect target, or None if no redirect.
    """
    from .cc_backend_tools import web_tools_available

    if not enabled:
        return None

    if not web_tools_available():
        return None

    command = str(tool_input.get("command", ""))
    lowered = command.lower()

    # Detect curl/wget commands
    if not any(kw in lowered for kw in ("curl", "wget")):
        return None

    # Detect Tavily API calls -> redirect to web_search or web_extract
    if "tavily.com" in lowered and "search" in lowered:
        query_match = re.search(r'"query"\s*:\s*"([^"]*)"', command)
        query = query_match.group(1).strip() if query_match else ""
        # If the query contains a URL, use web_extract instead of web_search
        url_in_query = re.search(r'https?://[^\s"]+', query)
        if url_in_query:
            return "web_extract", {"urls": [url_in_query.group(0).rstrip("'\"")]}
        return "web_search", {"query": query or "general", "limit": 5}

    # Detect URL fetch -> redirect to web_extract
    url_match = re.search(
        r'(?:curl|wget)\s+.*?[\'"]?(https?://[^\s\'"<>]+)', command, re.IGNORECASE
    )
    if url_match:
        url = url_match.group(1).rstrip("'\"")
        # Don't redirect local/internal URLs
        if any(host in url for host in ("127.0.0.1", "localhost", "0.0.0.0")):
            return None
        return "web_extract", {"urls": [url]}

    return None


def _result_indicates_success(result_text: str, *, tool_name: str = "") -> bool | None:
    """Read a tool execution receipt or structured envelope, otherwise abstain."""
    return tool_result_succeeded(result_text, tool_name=tool_name)


def _tool_result_error(result_text: str) -> str | None:
    stripped = result_text.strip()
    lowered = stripped.lower()
    plain_error_prefixes = (
        "error executing ",
        "file not found:",
        "command timed out after ",
        "old_string not found",
        "old_string cannot be empty",
        "new_string must ",
    )
    if any(lowered.startswith(prefix) for prefix in plain_error_prefixes):
        return stripped
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload.get("error") or "")
    return None


def _pending_mcp_enable_approval(result_text: str, tool_name: str) -> dict[str, Any] | None:
    """# Detects a stable non-blocking result of suggest_enable_mcp."""
    if tool_name != "suggest_enable_mcp":
        return None
    try:
        payload = json.loads(result_text)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("decision") != "pending":
        return None
    request_id = str(payload.get("request_id") or "").strip()
    server_name = str(payload.get("server_name") or "").strip()
    if not request_id or not server_name:
        return None
    return {
        "request_id": request_id,
        "server_name": server_name,
        "profile": payload.get("profile"),
        "reason": payload.get("reason"),
        "description": payload.get("description"),
    }


def _extract_terminal_fal_saved_files(result_text: str) -> list[str]:
    if "STATUS: COMPLETE" not in result_text:
        return []
    paths: list[str] = []
    for line in result_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        path = stripped[2:].strip()
        if path.startswith("/"):
            paths.append(path)
    return paths


def _terminal_fal_output(tool_name: str, result_text: str) -> str | None:
    saved_files = _extract_terminal_fal_saved_files(result_text)
    if not saved_files:
        return None
    if tool_name == "fal_edit_image":
        return "Готово, отредактировал изображение. Результат прикрепляю ниже."
    return "Готово, изображение создано. Результат прикрепляю ниже."


def _looks_like_tool_artifact(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "<|tool_call",
            "call:",
            "fal_edit_image",
            "fal_generate_image",
            "<tool_call",
        )
    )


def _terminal_skill_manage_output(
    tool_name: str,
    tool_input: Any,
    result_text: str,
) -> str | None:
    if tool_name != "skill_manage" or not isinstance(tool_input, dict):
        return None
    if str(tool_input.get("action") or "").strip() != "delete":
        return None
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    name = str(tool_input.get("name") or "").strip()
    if name:
        return f"Навык `{name}` удалён."
    return "Навык удалён."


def _metadata_int(metadata: dict[str, Any], *keys: str, default: int) -> int:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return default


def _auto_scale_max_tokens(max_tokens: int, context_window_tokens: int) -> int:
    """Auto-scale output budget for models with large context windows.

    The hardcoded default 4000 tokens is inadequate for reasoning models that
    consume output budget with thinking tokens.  When the context window exceeds
    32K, scale max_tokens to at least 1/8 of the window, capped at 32K.
    """
    if max_tokens <= 4000 and context_window_tokens > 32000:
        return min(32000, context_window_tokens // 8)
    return max_tokens


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0:
        try:
            parsed = int(value)
        except (ValueError, OverflowError):
            return None
        return parsed if parsed > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _extract_context_window_from_payload(payload: Any, model: str = "") -> int | None:
    keys = {
        "context_window",
        "context_window_tokens",
        "context_length",
        "context_size",
        "model_context_window",
        "max_context_length",
        "max_model_len",
        "n_ctx",
    }
    limit_keys = {"context", "context_window"}
    model_id = str(model or "").strip()

    def walk(value: Any) -> int | None:
        if isinstance(value, dict):
            limit = value.get("limit")
            if isinstance(limit, dict):
                for lk in limit_keys:
                    parsed = _positive_int(limit.get(lk))
                    if parsed:
                        return parsed
            data = value.get("data")
            if isinstance(data, list):
                matched: list[dict[str, Any]] = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("id") or item.get("model") or "").strip()
                    if model_id and item_id == model_id:
                        matched.insert(0, item)
                    else:
                        matched.append(item)
                for item in matched:
                    found = walk(item)
                    if found:
                        return found
            for key, item in value.items():
                if str(key) in keys:
                    parsed = _positive_int(item)
                    if parsed:
                        return parsed
            for key in (
                "default_generation_settings",
                "generation_settings",
                "model_info",
                "meta",
            ):
                found = walk(value.get(key))
                if found:
                    return found
        return None

    return walk(payload)


def _context_window_from_models_cache(model: str) -> int | None:
    """Try to read context window from our models.dev cache."""
    try:
        from factory.core.opencode_models import get_context_window
        for provider_id in ("opencode-go", "opencode", "openrouter", "minimax", "glm"):
            result = get_context_window(model, provider_id=provider_id)
            if result:
                return result
    except Exception:
        pass
    try:
        from factory.core.openrouter_pricing import get_openrouter_context_window
        result = get_openrouter_context_window(model)
        if result:
            return result
    except Exception:
        pass
    cache_path = os.path.expanduser("~/.cache/opencode/models.json")
    try:
        data = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for provider_id in ("opencode-go", "opencode", "openrouter", "minimax", "glm"):
        provider = data.get(provider_id, {})
        models = provider.get("models", {}) if isinstance(provider, dict) else {}
        if not isinstance(models, dict):
            continue
        for model_id, info in models.items():
            if model_id == model:
                limit = info.get("limit", {}) if isinstance(info, dict) else {}
                if not isinstance(limit, dict):
                    continue
                ctx = _positive_int(limit.get("context"))
                if ctx:
                    return ctx
    return None


def _resolve_backend_context_window(
    *,
    base_url: str,
    model: str,
    fallback: int,
    use_curl: bool = False,
) -> tuple[int, str]:
    # Local runtime limits take precedence over catalogue training maxima.
    from .local_model_context import local_model_context_window
    local_window = local_model_context_window(base_url)
    if local_window is not None:
        return local_window, "/props"
    cached = _context_window_from_models_cache(model)
    if cached:
        return cached, "models_cache"

    normalized_base = base_url.rstrip("/")
    if normalized_base.endswith("/v1"):
        api_root = normalized_base[:-3].rstrip("/")
        metadata_endpoints = (
            ("/props", f"{api_root}/props"),
            ("/v1/models", f"{normalized_base}/models"),
        )
    else:
        metadata_endpoints = (
            ("/props", f"{normalized_base}/props"),
            ("/v1/models", f"{normalized_base}/v1/models"),
        )

    for endpoint, url in metadata_endpoints:
        try:
            if use_curl:
                response, _ = _http_get_curl(url, timeout_s=2)
                payload = response
            else:
                with urllib.request.urlopen(url, timeout=1.5) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, Exception):
            continue
        window = _extract_context_window_from_payload(payload, model=model)
        if window:
            return window, endpoint
    return fallback, "metadata_or_default"


def resolve_host_context_window(
    base_url: str, model: str, *, api_format: str = "anthropic"
) -> int:
    """Host-resolved context window for the current model.

    Resolved with the host's provider+model cache (models.dev) and the provider's
    metadata endpoints; the isolated guest receives this value per task and never
    performs the catalog fetch itself. The default is an emergency fallback only.
    """
    value, _source = _resolve_backend_context_window(
        base_url=base_url,
        model=model,
        fallback=DEFAULT_CONTEXT_WINDOW_TOKENS,
        use_curl=(api_format == "openai"),
    )
    return int(value)


def _is_prompt_too_long_error(error_text: str) -> bool:
    lowered = str(error_text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "exceed_context_size_error",
            "exceeds the available context size",
            "prompt too long",
            "context length",
            "context window",
            "maximum context",
            "too many tokens",
        )
    )


def _request_tool_result_ids(messages: object) -> list[str]:
    """Identify result receipts in the actual provider request, not free text."""
    ids = set()
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool" and isinstance(message.get("tool_call_id"), str):
            ids.add(message["tool_call_id"])
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                ids.add(block["tool_use_id"])
    return sorted(ids - {""})


def _model_output_limited(body: object) -> bool:
    return isinstance(body, dict) and body.get("stop_reason") in ("max_tokens", "length")


def _output_limit_failure(*, workdir, partial_text, usage, tool_calls_count):
    """Preserve visible partial output without admitting unfinished tool calls."""
    notice = "Ответ модели обрезан лимитом генерации. Задача не завершена; недополученные вызовы инструментов не выполнялись."
    append_cc_backend_trace(workdir, {
        "kind": CcTraceEvent.TASK_FINISH, "status": "fail",
        "reason": "model_output_limit", "tool_calls_count": tool_calls_count,
        "partial_text_len": len(partial_text),
    })
    return {
        "status": "fail", "failure_reason": "model_output_limit",
        "failure_details": "Provider stopped generation at its output token limit.",
        "output_text": f"{partial_text}\n\n{notice}" if partial_text else notice,
        "partial_output_text": partial_text, "usage": usage,
        "tool_calls_count": tool_calls_count,
    }


def _terminal_response_text_and_usage(body: Any) -> tuple[str, dict[str, int]]:
    if not isinstance(body, dict):
        return "", {}
    raw_usage = body.get("usage")
    usage = {}
    if isinstance(raw_usage, dict):
        for key in ("input_tokens", "output_tokens"):
            value = raw_usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[key] = value
    if _model_output_limited(body):
        return "", usage
    content = body.get("content")
    if not isinstance(content, list):
        return "", usage
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts), usage


def _post_terminal_text_request(url, payload, headers, timeout_s, *, is_openai_api=False):
    """Use the task's explicit API contract for short artifact acknowledgements."""
    from .openai_responses import is_responses_url
    if is_openai_api and not is_responses_url(url):
        payload = dict(payload)
        system = payload.pop("system", "")
        payload["messages"] = _anthropic_messages_to_openai(
            [{"role": "system", "content": system}] + payload["messages"]
        )
        payload["stream"] = False
    body, latency = _http_post(url, payload, headers, timeout_s)
    if is_openai_api and isinstance(body, dict) and not body.get("error"):
        body = _openai_response_to_anthropic(body, set())
    return body, latency


def _make_terminal_fal_response_call(
    *,
    url: str,
    model: str,
    system_prompt: str,
    headers: dict,
    timeout_s: int,
    tool_name: str,
    fallback_text: str,
    workdir: str,
    log_fn,
    is_openai_api: bool = False,
) -> tuple[str, int, int]:
    action = "отредактировано" if tool_name == "fal_edit_image" else "создано"
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 160,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Инструмент уже успешно завершился: изображение {action}, "
                    "результат будет прикреплён к сообщению ниже. "
                    "Ответь пользователю по-русски одной короткой естественной фразой. "
                    "Не упоминай локальные пути, tool calls, JSON или технические детали."
                ),
            }
        ],
    }
    log_fn("[cc-backend] Making terminal FAL response call (no tools)")
    append_cc_backend_trace(
        workdir,
        {"kind": CcTraceEvent.MODEL_TERMINAL_FAL_RESPONSE_CALL, "tool_name": tool_name},
    )
    body, _lat_ms = _post_terminal_text_request(
        url, payload, headers, timeout_s, is_openai_api=is_openai_api
    )
    if isinstance(body, dict) and body.get("error"):
        log_fn(f"[cc-backend] Terminal FAL response call failed: {safe_text(body['error'])}")
        return fallback_text, 0, 0

    output_text, usage = _terminal_response_text_and_usage(body)
    if not output_text or _looks_like_tool_artifact(output_text):
        return (
            fallback_text,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )
    return output_text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)


def _make_forced_write_postamble(
    *,
    url: str,
    model: str,
    system_prompt: str,
    headers: dict,
    timeout_s: int,
    file_path: str,
    raw_result: str,
    workdir: str,
    log_fn,
    is_openai_api: bool = False,
) -> tuple[str, int, int]:
    """After a forced Write succeeds, ask the model for a short Russian summary."""
    basename = os.path.basename(file_path) if file_path else "файл"
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 200,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Инструмент Write успешно записал файл: {file_path}\n"
                    f"Результат: {raw_result[:300]}\n\n"
                    f"Кратко сообщи пользователю по-русски, что файл сохранён. "
                    f"Одно-два живых предложения. "
                    f"Не упоминай tool calls, JSON или технические детали."
                ),
            }
        ],
    }
    log_fn("[cc-backend] Making forced Write postamble call (no tools)")
    append_cc_backend_trace(
        workdir,
        {"kind": CcTraceEvent.MODEL_FORCED_WRITE_POSTAMBLE, "file_path": file_path},
    )
    body, _lat_ms = _post_terminal_text_request(
        url, payload, headers, timeout_s, is_openai_api=is_openai_api
    )
    if isinstance(body, dict) and body.get("error"):
        log_fn(f"[cc-backend] Forced Write postamble failed: {safe_text(body['error'])}")
        return f"Файл сохранён: {file_path}", 0, 0

    output_text, usage = _terminal_response_text_and_usage(body)
    if not output_text or _looks_like_tool_artifact(output_text):
        return f"Файл сохранён: {file_path}", usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    return output_text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)


def _make_synthesis_call(
    url: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
    headers: dict,
    timeout_s: int,
    max_tokens: int,
    workdir: str,
    log_fn,
    total_input_tokens: int,
    total_output_tokens: int,
    start_time: float,
    tool_calls_count: int,
    is_openai_api: bool = False,
) -> dict[str, Any]:
    """Make one final API call without tools to force a text synthesis response."""
    synthesis_messages = list(messages)
    synthesis_messages.append(
        {
            "role": "user",
            "content": (
                "На основе полученных выше данных (включая Tool result) ответь на исходный вопрос пользователя текстом. "
                "Инструменты больше не доступны — отвечай сразу. "
                "КЛЮЧЕВОЕ ПРАВИЛО: используй ТОЛЬКО факты из Tool result. "
                "Не выдумывай, не нормализуй, не переименовывай значения. "
                "Если значение есть в Tool result (например branch name или path) — цитируй его точно."
            ),
        }
    )
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": synthesis_messages,
    }
    from .openai_responses import is_responses_url
    if is_openai_api and not is_responses_url(url):
        payload.pop("system", None)
        payload["messages"] = _anthropic_messages_to_openai(
            [{"role": "system", "content": system_prompt}] + synthesis_messages
        )
        # Same reasoning-mode contract: the assistant with tool_calls must include
        # reasoning_content; otherwise, the upstream API returns HTTP 400.
        payload["messages"], _ = _ensure_reasoning_passback(payload["messages"])
        payload["stream"] = False
    log_fn("[cc-backend] Making synthesis call (no tools)")
    append_cc_backend_trace(
        workdir, {"kind": CcTraceEvent.MODEL_SYNTHESIS_CALL, "messages": len(synthesis_messages)}
    )
    body, lat_ms = _http_post(url, payload, headers, timeout_s, use_curl=is_openai_api)
    if is_openai_api and isinstance(body, dict) and not body.get("error"):
        body = _openai_response_to_anthropic(body, set())

    if isinstance(body, dict) and body.get("error"):
        log_fn(f"[cc-backend] Synthesis call failed: {safe_text(body['error'])}")
        append_cc_backend_trace(workdir, {
            "kind": CcTraceEvent.TASK_FINISH, "status": "fail", "reason": "synthesis_http_error",
        })
        return {
            "status": "fail",
            "output_text": safe_text(body["error"]),
            "usage": _usage(total_input_tokens, total_output_tokens, start_time),
            "tool_calls_count": tool_calls_count,
        }

    output_text, usage = _terminal_response_text_and_usage(body)
    total_input_tokens += usage.get("input_tokens", 0)
    total_output_tokens += usage.get("output_tokens", 0)
    if _model_output_limited(body):
        return _output_limit_failure(
            workdir=workdir,
            partial_text="\n".join(str(block.get("text") or "") for block in body.get("content", [])
                                   if isinstance(block, dict) and block.get("type") == "text"),
            usage=_usage(total_input_tokens, total_output_tokens, start_time),
            tool_calls_count=tool_calls_count,
        )
    if not isinstance(body, dict) or not isinstance(body.get("content"), list):
        diagnostic = observation(
            "PROTOCOL.INVALID_RESPONSE", component="model", operation="model_round",
            endpoint_role="runtime_to_provider", phase="response_body",
        )
        append_cc_backend_trace(workdir, {
            "kind": CcTraceEvent.TASK_FINISH, "status": "fail",
            "reason": "synthesis_invalid_response", "diagnostic": diagnostic,
        })
        return {
            "status": "fail", "failure_reason": "synthesis_invalid_response",
            "failure_details": "Provider returned a malformed synthesis response.",
            "error_code": "PROTOCOL.INVALID_RESPONSE", "diagnostic": diagnostic,
            "output_text": "Провайдер вернул некорректный ответ при подготовке итога.",
            "usage": _usage(total_input_tokens, total_output_tokens, start_time),
            "tool_calls_count": tool_calls_count,
        }
    if not output_text.strip():
        append_cc_backend_trace(
            workdir,
            {
                "kind": CcTraceEvent.TASK_FINISH,
                "status": "fail",
                "reason": "synthesis_empty_after_stuck",
            },
        )
        return {
            "status": "fail",
            "failure_reason": "synthesis_empty_after_stuck",
            "failure_details": "Synthesis call after stuck detection returned empty text.",
            "output_text": "Локальная модель застряла в цикле инструментов и не смогла собрать итоговый ответ.",
            "usage": _usage(total_input_tokens, total_output_tokens, start_time),
            "tool_calls_count": tool_calls_count,
        }
    append_cc_backend_trace(
        workdir,
        {
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "reason": "synthesis_call",
            "output_preview": output_text[:500],
        },
    )
    return {
        "status": "ok",
        "output_text": output_text,
        "usage": _usage(total_input_tokens, total_output_tokens, start_time),
        "tool_calls_count": tool_calls_count,
    }


def _anthropic_messages_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/messages"
    return f"{normalized}/v1/messages"


def _telegram_runtime_suffix() -> str:
    return (
        "For this Telegram conversation, keep the response direct and concise.\n"
        "Telegram renders native rich tables: for any tabular data emit a standard "
        "GFM markdown pipe table (`| Header | Header |` / `|---|---|`) instead of an "
        "ASCII/box-drawing or monospace grid. Never claim Telegram cannot render "
        "tables.\n"
        "/no_think"
    )


def task_is_cancelling(task_id: str | None = None) -> bool:
    """True while the task row is in the cooperative ``cancelling`` state.

    Shared by the off-mode executor loop (which aborts between rounds) and the
    isolated dispatch seam (which turns it into a ``cancel`` frame across the VM
    boundary), so the mobile-stop rule has exactly one implementation. Any read
    failure means "not cancelling": a DB hiccup must never abort a run.
    """
    tid = task_id or ""
    if not tid:
        return False
    try:
        from .cli_tasks import get_task as _get_task
    except ImportError:
        from cli_tasks import get_task as _get_task
    try:
        task = _get_task(tid)
        return task is not None and task.get("status") == "cancelling"
    except Exception:
        return False


def _guest_turn() -> bool:
    """True when this executor runs inside a session VM (guest marker set)."""
    try:
        from .isolation.guest_bwrap import guest_mode_requested
    except Exception:  # a missing marker module means host mode
        return False
    return guest_mode_requested()


def task_should_stop(task_id: str | None = None) -> bool:
    """True when the executor must abandon the current task.

    Two structural reasons, both mean "continuing is pointless":

    * the row is in ``cancelling`` — the user pressed Stop and the loop aborts
      cooperatively between rounds;
    * the row is **gone** — the task was removed from ``tasks`` (for example by
      the old ``cleanup_stuck_tasks`` that did ``DELETE FROM tasks``). There is
      nowhere to write the result, the client no longer sees the task, and the
      worker keeps burning tokens while holding its profile queue: exactly how a
      live task turned into a "zombie" and new tasks sat ``queued`` forever.

    An empty ``task_id`` means "do not track": delegated subagents
    (``delegate_*``) and one-shot agents (``smarthelp_*``) run without a row in
    ``tasks`` and must never be aborted by this check. A DB read failure still
    means "do not stop".

    Inside a session VM the local task table is not authoritative: the host owns
    the row and cancellation reaches the guest as a channel ``cancel`` frame
    (the runner turns it into ``GuestCanceled``), so a missing guest row must not
    abort the turn.
    """
    tid = task_id or ""
    if not tid:
        return False
    if _guest_turn():
        return False
    try:
        from .cli_tasks import get_task as _get_task
    except ImportError:
        from cli_tasks import get_task as _get_task
    try:
        task = _get_task(tid)
    except Exception:
        return False
    if task is None:
        return True
    return task.get("status") == "cancelling"

@scoped_agent_trace
def execute_cc_backend_task(
    *,
    base_url: str,
    model: str,
    system_prompt: str,
    user_message: str,
    input_files: list[str] | None = None,
    workdir: str,
    artifact_workdir: str | None = None,
    filesystem_mode: str = "workspace",
    guard_mode: str | None = None,
    checkpoint_config: dict[str, Any] | None = None,
    current_session: str | None = None,
    source_platform: str | None = None,
    source_chat_id: str | None = None,
    max_tokens: int = 4000,
    max_output_tokens: int | None = None,
    timeout_s: int = 300,
    log_fn=None,
    delegate_depth: int = 0,
    iteration_budget: IterationBudget | None = None,
    enabled_tools: list[dict[str, Any]] | None = None,
    parent_task_id: str = "",
    task_id: str = "",
    subagent_id: str = "",
    progress_callback=None,
    metadata: dict[str, Any] | None = None,
    request_headers: dict[str, str] | None = None,
    request_headers_provider: Callable[[], dict[str, str]] | None = None,
    api_format: str = "anthropic",
    reasoning_format: str | None = None,
    profile: str = "",
    history_messages: list[dict[str, Any]] | None = None,
    trailing_context_text: str = "",
    pending_steer_provider: Callable[[], list[dict[str, Any]]] | None = None,
    priority_steer_provider: Callable[[], list[dict[str, Any]]] | None = None,
    approvals_enabled_provider: Callable[[], bool] | None = None,
    user_input_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    web_tool_provider: Callable[[str, dict[str, Any]], str] | None = None,
    session_search_provider: Callable[[dict[str, Any]], str] | None = None,
    vault_store_provider: Callable[[dict[str, Any]], str] | None = None,
    memory_forget_provider: Callable[[dict[str, Any]], str] | None = None,
    goal_control_provider: Callable[[dict[str, Any]], str] | None = None,
    data_root: str | None = None,
) -> dict[str, Any]:
    """Run the full tool-use loop against the CC backend.

    Args:
        base_url: CC backend URL (e.g. http://127.0.0.1:8001).
        model: Model name to send in requests.
        system_prompt: System prompt for the model.
        user_message: The user's task/message.
        input_files: Optional attached files. Supported images are embedded as Anthropic image blocks.
        workdir: Working directory for tool execution.
        artifact_workdir: Directory for runtime artifacts such as traces and persisted tool results.
        filesystem_mode: Filesystem policy mode for local tool execution.
        guard_mode: Resolved bash-scope guard mode forwarded to local tools
            (None keeps the env-driven default).
        max_tokens: Task output budget, subject to legacy automatic scaling.
        max_output_tokens: Exact profile override for each model request,
            including reasoning and answer; bypasses automatic scaling.
        timeout_s: Timeout per HTTP request.
        log_fn: Optional callable for log messages.
        data_root: Optional durable data root for the checkpoint shadow repos and
            the ``session_changes`` records; ``None`` keeps ``get_data_root()``.
            The isolated guest passes the shared-workspace root so those
            artifacts land on the host and survive a VM teardown (todo 26).

    Returns:
        Dict with keys: status, output_text, usage, tool_calls_count.
    """
    if log_fn is None:
        log_fn = lambda msg: None
    artifact_workdir = artifact_workdir or workdir
    from .model_output_budget import optional_output_token_limit

    max_output_tokens = optional_output_token_limit(max_output_tokens)
    if max_output_tokens is not None:
        max_tokens = max_output_tokens
    if reasoning_format is not None:
        if api_format != "openai" or not isinstance(reasoning_format, str) or reasoning_format not in {"auto", "none", "deepseek", "deepseek-legacy"}:
            raise ValueError("reasoning_format requires an explicit supported OpenAI-compatible transport")

    if (
        source_platform or ""
    ).strip().lower() == "telegram" and "/no_think" not in system_prompt:
        system_prompt = f"{system_prompt.rstrip()}\n\n{_telegram_runtime_suffix()}"

    url = _anthropic_messages_url(base_url)
    anthropic_tools = list(enabled_tools if enabled_tools is not None else get_tool_schemas())
    if vault_store_provider is None:
        anthropic_tools = [tool for tool in anthropic_tools if tool.get('name') != 'VaultStore']
    tools = anthropic_tools
    # Transport selection is based on an explicit API contract, not on authentication.
    # Local OpenAI-compatible servers (e.g., llama-server) typically operate
    # without an API key.
    is_responses_api = api_format in {"openai-responses", "opencode-responses"}
    is_google_api = api_format == "google"
    is_openai_api = api_format in {"openai", "openai-responses", "opencode-responses"}
    valid_tool_names = {
        str(t.get("name") or "") for t in anthropic_tools if t.get("name")
    }
    if is_openai_api:
        url = f"{base_url.rstrip('/')}/{'responses' if is_responses_api else 'chat/completions'}"
        tools = _anthropic_tools_to_openai(anthropic_tools)
    elif is_google_api:
        from urllib.parse import quote
        url = f"{base_url.rstrip('/')}/models/{quote(model, safe='')}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": "dummy",
        "anthropic-version": "2023-06-01",
    }
    if is_openai_api:
        headers.pop("x-api-key", None)
        headers.pop("anthropic-version", None)
        headers.setdefault("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        if not is_responses_api:
            headers.setdefault("Origin", "https://opencode.ai")
    elif is_google_api:
        headers.pop("x-api-key", None)
        headers.pop("anthropic-version", None)
        headers['__igorek_google_mode__'] = True
    if isinstance(request_headers, dict):
        headers.update(
            {
                str(key): str(value)
                for key, value in request_headers.items()
                if str(key).strip() and str(value).strip()
            }
        )
    if is_responses_api:
        if api_format == 'opencode-responses':
            headers['__igorek_generic_responses__'] = True
        from .openai_responses import RenewingHeaders
        if request_headers_provider is None and isinstance(request_headers, RenewingHeaders):
            request_headers_provider = request_headers.provider
        if request_headers_provider is not None:
            headers = RenewingHeaders(headers, request_headers_provider)

    image_input = LlamaImageInput(
        base_url, model, headers, enabled=api_format in {"openai", "anthropic"}
    )
    backend_capabilities = image_input.resolve()
    guard_local_history = local_history_guard_enabled(
        base_url, api_format, capabilities=backend_capabilities, model=model
    )
    review_completion = completion_review_enabled(
        base_url, api_format, backend_capabilities, model, anthropic_tools
    )
    if review_completion:
        from .prompt_assembly import local_completion_guidance
        system_prompt += "\n\n" + local_completion_guidance()
    completion_recovery = CompletionRecovery()
    local_history_rejections = 0
    message_history_ids: dict[int, tuple[str, ...]] = {}
    messages: list[dict[str, Any]] = _build_initial_messages(
        history_messages=history_messages,
        native_tool_history=guard_local_history,
        history_message_ids=message_history_ids,
        trailing_context_text=trailing_context_text,
        user_message=user_message,
        input_files=input_files,
    )

    covered_history_ids: set[str] = set()
    if task_id:
        message_history_ids[id(messages[-1])] = (f"task-user:{task_id}",)

    def _deliver_pending_steers(
        round_index: int,
        provider: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> int:
        """Deliver messages that arrived while the session task was running.

        This mirrors desktop chat: a user message extends the current dialogue
        without creating a new task or task ID. Returns the delivered count.
        """
        selected_provider = provider or pending_steer_provider
        if selected_provider is None:
            return 0
        try:
            items = selected_provider() or []
        except Exception as exc:  # The queue must not break the active turn.
            log_fn(f"[cc-backend] Steer drain failed: {exc}")
            return 0
        delivered = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            raw_files = item.get("input_files")
            files = [str(path) for path in raw_files] if isinstance(raw_files, list) else []
            if not text and not files:
                continue
            content = build_anthropic_user_content(text, input_files=files or None)
            messages.append({"role": "user", "content": content})
            if item.get("message_id"):
                message_history_ids[id(messages[-1])] = ("message:" + str(item["message_id"]),)
            delivered += 1
            append_cc_backend_trace(
                artifact_workdir,
                {
                    "kind": CcTraceEvent.STEER_DELIVERED,
                    "round": int(round_index),
                    "message_id": str(item.get("message_id") or ""),
                    "text_preview": text[:200],
                    "input_files": len(files),
                },
            )
        if delivered:
            log_fn(
                f"[cc-backend] Delivered {delivered} pending session message(s) "
                "into the current task"
            )
        return delivered

    def refresh_hot_reloaded_mcp_tools(result_text: str) -> None:
        nonlocal anthropic_tools, tools, valid_tool_names
        try:
            payload = json.loads(result_text)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        hot_reload = payload.get("hot_reload")
        if not isinstance(hot_reload, dict) or hot_reload.get("status") != "ok":
            return
        raw_names = hot_reload.get("tools")
        if not isinstance(raw_names, list):
            return
        names = {str(item).strip() for item in raw_names if str(item).strip()}
        if not names:
            return
        schemas_by_name = {
            str(schema.get("name") or "").strip(): schema
            for schema in get_tool_schemas()
            if str(schema.get("name") or "").strip()
        }
        added = False
        for name in sorted(names):
            if name in valid_tool_names:
                continue
            schema = schemas_by_name.get(name)
            if not isinstance(schema, dict):
                continue
            anthropic_tools.append(schema)
            valid_tool_names.add(name)
            added = True
        if added and is_openai_api:
            tools = _anthropic_tools_to_openai(anthropic_tools)

    total_input_tokens = 0
    total_output_tokens = 0
    tool_calls_count = 0
    start_time = time.time()
    checkpoint_settings = (
        checkpoint_config if isinstance(checkpoint_config, dict) else {}
    )
    checkpoint_manager = CheckpointManager(
        enabled=bool(checkpoint_settings.get("enabled", True)),
        max_snapshots=int(checkpoint_settings.get("max_snapshots", 50) or 50),
        data_root=data_root,
    )
    checkpoint_manager.new_turn()
    change_tracker = (
        TaskChangeTracker(
            task_id=task_id,
            workdir=workdir,
            checkpoint_manager=checkpoint_manager,
            data_root=data_root,
        )
        if task_id and not subagent_id
        else None
    )
    consecutive_textual_artifact_rounds = 0
    consecutive_policy_denial_rounds = 0
    consecutive_reasoning_only_rounds = 0
    # One retry per task: if the upstream rejects the payload without reasoning_content,
    # the field is then forcibly set in all rounds.
    reasoning_passback_forced = False
    loop_limits = load_cc_backend_loop_limits()
    session_budget = SessionBudget(
        repeated_batch_limit=loop_limits.repeated_tool_batch_limit,
        non_productive_after_useful_limit=loop_limits.non_productive_after_useful_limit,
    )
    called_tool_names: set[str] = set()
    called_write_paths: set[str] = set()
    fal_tools_already_executed: set[str] = set()
    last_fal_result: dict[str, str] = {}
    task_metadata = metadata if isinstance(metadata, dict) else {}
    approval_wait_status = (
        "waiting_approval"
        if str(source_platform or "").strip().lower() == "android"
        else "fail"
    )

    mcp_write_grants=set()
    def consume_mcp_write_grant(name,args,identity):
        key=(name,_approval_signature(name,args),identity)
        if key not in mcp_write_grants:return False
        mcp_write_grants.remove(key)
        return True

    def _requires_approval(tool_name: str, tool_input: Any, tool_use_id: str = "") -> bool:
        from .tool_registry import registry
        if registry.execution_error(tool_name, tool_input or {}, workdir=workdir, current_session=current_session):
            return False
        if tool_name in {'calendar_create_event','calendar_move_event'} and (tool_name not in valid_tool_names or _tool_denied_by_policy(tool_name,task_metadata)):
            return False
        if not needs_approval(tool_name, tool_input):
            return False
        if _consume_approved_tool_call(
            metadata=task_metadata,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id="" if tool_name in {'calendar_create_event','calendar_move_event'} else tool_use_id,
        ):
            if tool_name in {'calendar_create_event','calendar_move_event'}:
                mcp_write_grants.add((tool_name,_approval_signature(tool_name,tool_input),tool_use_id))
            return False
        if tool_name in {'calendar_create_event','calendar_move_event'}:
            return True
        approvals_enabled = True
        if approvals_enabled_provider is not None:
            try:
                approvals_enabled = bool(approvals_enabled_provider())
            except Exception as exc:
                log_fn(f"[cc-backend] Approval setting lookup failed: {exc}")
        if not approvals_enabled:
            return False
        return not _approved_tool_call(
            metadata=task_metadata,
            tool_name=tool_name,
            tool_input=tool_input,
        )
    raw_approval_context_messages: list[Any] = []
    top_level_approval_context = task_metadata.get("approval_context_messages")
    if isinstance(top_level_approval_context, list):
        raw_approval_context_messages.extend(top_level_approval_context)
    raw_forced_tool_call = task_metadata.get("forced_tool_call")
    if isinstance(raw_forced_tool_call, dict):
        forced_approval_context = raw_forced_tool_call.get("approval_context_messages")
        if isinstance(forced_approval_context, list):
            raw_approval_context_messages.extend(forced_approval_context)
    if raw_approval_context_messages:
        seen_context_messages: set[str] = set()
        for item in raw_approval_context_messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role in {"assistant", "user"} and isinstance(content, list):
                signature = json.dumps(
                    {"role": role, "content": content},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if signature in seen_context_messages:
                    continue
                seen_context_messages.add(signature)
                messages.append({"role": role, "content": content})
    must_write_skills: list[str] = (
        task_metadata.get("must_write_skills")
        if isinstance(task_metadata.get("must_write_skills"), list)
        else []
    )
    auto_compaction_enabled = task_metadata.get("auto_compaction_enabled") is not False
    metadata_context_window_tokens = _metadata_int(
        task_metadata,
        "context_window_tokens",
        "model_context_window",
        "modelContextWindow",
        default=DEFAULT_CONTEXT_WINDOW_TOKENS,
    )
    reserved_output_tokens = _metadata_int(
        task_metadata,
        "reserved_output_tokens",
        "compaction_reserved_output_tokens",
        default=min(max(2048, int(max_tokens or 0)), 8192),
    )
    if max_output_tokens is not None:
        reserved_output_tokens = max(reserved_output_tokens, max_output_tokens)
    context_window_tokens, context_window_source = _resolve_backend_context_window(
        base_url=base_url,
        model=model,
        fallback=metadata_context_window_tokens,
        use_curl=is_openai_api,
    )
    tool_result_access = ToolResultAccess.create(
        workdir, artifact_workdir, task_id, current_session,
        database=os.path.join(data_root, "database", "factory.db") if data_root else None,
    )
    # Auto-scale output budget for reasoning models that consume output budget
    # with thinking tokens.  Default 4000 is inadequate for large-context models:
    # the model exhausts max_tokens on reasoning and has no room for tool calls.
    scaled = (max_output_tokens if max_output_tokens is not None
              else _auto_scale_max_tokens(max_tokens, context_window_tokens))
    if scaled != max_tokens:
        log_fn(
            f"max_tokens auto-scaled {max_tokens} → {scaled} "
            f"(context_window={context_window_tokens})"
        )
        max_tokens = scaled
    compaction_attempts = 0
    max_compaction_attempts = 2
    append_cc_backend_trace(
        artifact_workdir,
        {
            "kind": CcTraceEvent.TASK_START,
            "model": model,
            "filesystem_mode": filesystem_mode,
            "guard_mode": guard_mode,
            "current_session": current_session,
            "input_files_count": len(input_files or []),
            "model_context_window": context_window_tokens,
            "context_window_source": context_window_source,
            "max_tokens": max_tokens,
            "output_budget_source": "profile" if max_output_tokens is not None else "task_or_auto",
            "parent_task_id": parent_task_id or None,
            "delegate_depth": delegate_depth,
            **({"user_message": user_message} if subagent_id else {}),
        },
    )

    # Set thread-local runtime context for this task
    import threading

    _interrupt_event = threading.Event()


    def _is_task_cancelling() -> bool:
        # Not only `cancelling`, but also when the task string disappears: a removed task
        # cannot be continued (no place to write the result, the client doesn’t see it, and
        # the profile queue is occupied by a zombie). A non-empty `task_id` (subtasks like `delegate_*`) is not tracked.
        #
        # A child task `delegate_task` runs outside of `tasks`, so the parent web cancellation address is sent to it
        # via a file flag in the task’s working directory. Registered command wait loops
        # consult this predicate as well as the checks between model rounds.
        if subagent_id:
            from .subagents import cancel_requested as _subagent_cancel_requested

            if _subagent_cancel_requested(artifact_workdir, subagent_id):
                return True
        return task_should_stop(task_id)

    set_runtime_context(
        delegate_depth=delegate_depth,
        iteration_budget=iteration_budget,
        base_url=base_url,
        model=model,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        parent_task_id=parent_task_id,
        task_id=task_id,
        progress_callback=progress_callback,
        interrupt_event=_interrupt_event,
        interrupt_check=_is_task_cancelling,
    )

    try:
        def _maybe_apply_reactive_compaction(
            *,
            round_index: int,
            prompt_too_long: bool = False,
        ) -> bool:
            nonlocal messages, compaction_attempts, total_input_tokens, total_output_tokens, message_history_ids
            if not auto_compaction_enabled:
                return False
            estimated_tokens = estimate_context_tokens(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
            )
            decision = evaluate_compaction_pressure(
                estimated_context_tokens=estimated_tokens,
                context_window_tokens=context_window_tokens,
                reserved_output_tokens=reserved_output_tokens,
                prompt_too_long=prompt_too_long,
            )
            if not decision.should_check:
                return False
            append_cc_backend_trace(
                artifact_workdir,
                {
                    "kind": CcTraceEvent.COMPACTION_CHECK,
                    "round": round_index + 1,
                    "action": decision.action,
                    "reason": decision.reason,
                    "context_ratio": decision.context_ratio,
                    "estimated_context_tokens": decision.estimated_context_tokens,
                    "usable_context_window": decision.usable_context_window,
                    "reserved_output_tokens": decision.reserved_output_tokens,
                    "prompt_too_long": prompt_too_long,
                    "attempt": compaction_attempts + 1,
                },
            )
            if decision.action not in {"manual", "reactive"}:
                return False
            if compaction_attempts >= max_compaction_attempts:
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.COMPACTION_FAILED,
                        "round": round_index + 1,
                        "reason": "max_compaction_attempts",
                        "attempts": compaction_attempts,
                    },
                )
                return False
            keep_tail = max(2, context_window_tokens // 20_000)
            plan = build_compaction_dry_run_plan(
                messages=messages,
                trace_events=read_compaction_guards(artifact_workdir),
                session_budget_snapshot=session_budget.snapshot(),
                keep_tail_messages=keep_tail,
                keep_tail_tokens=min(15000, max(1000, context_window_tokens // 5)),
                trigger="reactive",
            )
            if not plan.can_compact:
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.COMPACTION_SKIPPED,
                        "round": round_index + 1,
                        "reason": plan.reason,
                        "preserved_tool_result_ids": plan.preserved_tool_result_ids,
                        "preserved_approval_ids": plan.preserved_approval_ids,
                    },
                )
                return False
            compaction_attempts += 1
            compaction_id = f"{task_id or 'task'}_compact_{compaction_attempts:04d}"
            append_cc_backend_trace(artifact_workdir, {
                "kind": CcTraceEvent.COMPACTION_STARTED, "compaction_id": compaction_id,
                "pre_message_count": len(messages),
                "preserved_message_count": len(plan.preserve_message_indexes),
                "summarized_message_count": len(plan.summarize_message_indexes),
            })
            summary_max_tokens = min(8192, max(512, context_window_tokens // 4))

            def request_summary(prompt: str) -> str:
                nonlocal total_input_tokens, total_output_tokens
                if is_interrupt_requested() or _is_task_cancelling():
                    raise ValueError("summary_cancelled")
                for summary_attempt in range(2):
                    output_limit = min(summary_max_tokens * (2 ** summary_attempt), max(512, context_window_tokens // 4))
                    request_payload = {"model": model, "max_tokens": output_limit, "messages": [{"role": "user", "content": prompt}]}
                    if is_openai_api:
                        request_payload["stream"] = False
                        if reasoning_format is not None:
                            request_payload["reasoning_format"] = reasoning_format
                        if model.rsplit("/", 1)[-1].lower().startswith("deepseek-v4"):
                            request_payload["thinking"] = {"type": "disabled"}
                    body, _ = _http_post(url, request_payload, headers, timeout_s, use_curl=is_openai_api)
                    if not isinstance(body, dict) or body.get("error"):
                        if isinstance(body, dict) and _is_prompt_too_long_error(str(body.get("error") or "")):
                            raise ValueError("summary_input_budget_exceeded")
                        raise ValueError("summary_provider_error")
                    if is_openai_api:
                        body = _openai_response_to_anthropic(body, set())
                    usage = body.get("usage") or {}
                    total_input_tokens += int(usage.get("input_tokens") or 0)
                    total_output_tokens += int(usage.get("output_tokens") or 0)
                    append_cc_backend_trace(artifact_workdir, {
                        "kind": CcTraceEvent.COMPACTION_CHECK, "phase": "summary_response",
                        "compaction_id": compaction_id, "attempt": summary_attempt + 1,
                        "output_limit": output_limit, "stop_reason": body.get("stop_reason"),
                        "usage_input": usage.get("input_tokens"), "usage_output": usage.get("output_tokens"),
                    })
                    if body.get("stop_reason") not in {"max_tokens", "length"}:
                        return "\n".join(str(block.get("text") or "") for block in body.get("content", []) if block.get("type") == "text")
                raise ValueError("summary_output_truncated")

            def summary_progress(text: str, chunk: int, total: int) -> None:
                append_cc_backend_trace(artifact_workdir, {
                    "kind": CcTraceEvent.COMPACTION_PROGRESS, "compaction_id": compaction_id,
                    "summary_text": text, "chunk": chunk, "total_chunks": total,
                })

            removed = [messages[index] for index in plan.summarize_message_indexes]
            new_covered = set(covered_history_ids)
            for message in removed:
                origin_ids = message_history_ids.get(id(message), ())
                new_covered.update(origin_ids)
                if origin_ids and all(origin.startswith("tool:") for origin in origin_ids):
                    continue
                for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                    if block.get("type") in {"tool_use", "tool_result"}:
                        tool_id = str(block.get("id") or block.get("tool_use_id") or "")
                        if tool_id and task_id:
                            new_covered.add(f"tool:{task_id}:{tool_id}")
            try:
                archive_path = Path(artifact_workdir) / ".session_compaction" / (compaction_id + "_" + str(time.time_ns()) + "_history.jsonl")
                archive_summary_history(removed, archive_path, history_ids=[message_history_ids.get(id(message), ()) for message in removed])
                summary_text = summarize_history(
                    removed, request=request_summary,
                    input_budget_tokens=max(512, context_window_tokens - min(summary_max_tokens * 2, max(512, context_window_tokens // 4)) - min(20000, max(1024, context_window_tokens // 20))),
                    progress=summary_progress, archive_path=str(archive_path), continuation_goal=user_message,
                )
                # Persist the usable memory before changing the active context.
                artifact = write_manual_compaction_artifact(
                    plan=plan, artifact_dir=os.path.join(artifact_workdir, ".session_compaction"),
                    compaction_id=compaction_id, summary_text=summary_text,
                    covered_history_ids=sorted(new_covered),
                )
            except (ValueError, OSError) as exc:
                append_cc_backend_trace(artifact_workdir, {
                    "kind": CcTraceEvent.COMPACTION_FAILED, "compaction_id": compaction_id,
                    "reason": str(exc) if isinstance(exc, ValueError) else "summary_persistence_failed",
                })
                return False
            preserved_origins = [(message_history_ids.get(id(messages[index]), ()), messages[index]) for index in plan.preserve_message_indexes]
            previous_count = len(messages)
            candidate_messages = build_compacted_messages(plan=plan, artifact=artifact, messages=messages)
            candidate_tokens = estimate_context_tokens(system_prompt=system_prompt, messages=candidate_messages, tools=tools)
            if candidate_tokens >= estimated_tokens or candidate_tokens > context_window_tokens - reserved_output_tokens:
                append_cc_backend_trace(artifact_workdir, {
                    "kind": CcTraceEvent.COMPACTION_FAILED, "compaction_id": compaction_id,
                    "reason": "summary_context_budget_exceeded",
                })
                return False
            messages = candidate_messages
            offset = len(messages) - len(preserved_origins)
            message_history_ids = {id(messages[index + offset]): origin for index, (origin, _) in enumerate(preserved_origins) if origin}
            covered_history_ids.update(new_covered)
            boundary_event = {
                **artifact.boundary_event,
                "round": round_index + 1,
                "action": "reactive",
                "reason": decision.reason,
                "pre_message_count": previous_count,
                "post_message_count": len(messages),
                "context_ratio": decision.context_ratio,
                "estimated_context_tokens": decision.estimated_context_tokens,
                "usable_context_window": decision.usable_context_window,
            }
            append_cc_backend_trace(artifact_workdir, boundary_event)
            append_cc_backend_trace(
                artifact_workdir,
                {
                    "kind": CcTraceEvent.COMPACTION_COMPLETED,
                    "round": round_index + 1,
                    "compaction_id": compaction_id,
                    "pre_message_count": previous_count,
                    "post_message_count": len(messages),
                    "summary_path": str(artifact.summary_path),
                    "summary_text": summary_text,
                    "covered_history_ids": sorted(covered_history_ids),
                },
            )
            log_fn(
                "[cc-backend] Reactive compaction applied: "
                f"{previous_count} -> {len(messages)} messages"
            )
            return True

        def _current_usage() -> dict[str, Any]:
            return _usage(
                total_input_tokens,
                total_output_tokens,
                start_time,
                model_context_window=context_window_tokens,
                current_context_tokens=estimate_context_tokens(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                ),
            )

        def _delegated_approval(name, result):
            if name != "delegate_task":
                return None
            try:
                delegated = json.loads(result)
            except (ValueError, TypeError):
                return None
            if not isinstance(delegated, dict) or delegated.get("failure_reason") != "approval_required":
                return None
            blocked = [item for item in delegated.get("approval_blocked_tools") or [] if isinstance(item, dict)]
            if not blocked:
                return None
            for item in blocked:
                item["parent_approval_context_messages"] = messages[1:-1]
            append_cc_backend_trace(artifact_workdir, {
                "kind": CcTraceEvent.TASK_FINISH, "status": "waiting_approval",
                "reason": "approval_required", "approval_blocked_tools": blocked,
            })
            return {"status": "waiting_approval", "failure_reason": "approval_required",
                    "approval_blocked_tools": blocked, "output_text": "Дочерний агент ожидает подтверждения.",
                    "usage": _current_usage(), "tool_calls_count": tool_calls_count}

        forced_tool_call = task_metadata.get("forced_tool_call")
        if isinstance(forced_tool_call, dict):
            forced_name = str(forced_tool_call.get("name") or "").strip()
            forced_input = (
                forced_tool_call.get("input")
                if isinstance(forced_tool_call.get("input"), dict)
                else {}
            )
            forced_source = str(forced_tool_call.get("source") or "").strip()
            forced_reasoning_content = str(
                forced_tool_call.get("reasoning_content") or ""
            )
            forced_assistant_text = str(forced_tool_call.get("assistant_text") or "")
            forced_approval_reason = str(forced_tool_call.get("reason") or "").strip()
            forced_tool_calls_count = 1 if forced_name else 0
            forced_tool_id = (
                str(forced_tool_call.get("tool_use_id") or "").strip()
                or "forced_tool_call"
            )
            raw_pending_tool_calls = forced_tool_call.get("pending_tool_calls")
            pending_tool_calls = (
                raw_pending_tool_calls
                if isinstance(raw_pending_tool_calls, list)
                else []
            )
            if (
                not forced_name
                or forced_name not in valid_tool_names
                or not isinstance(forced_input, dict)
            ):
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.TASK_FINISH,
                        "status": "fail",
                        "reason": "invalid_forced_tool_call",
                        "tool_name": forced_name,
                        "tool_calls_count": forced_tool_calls_count,
                    },
                )
                return {
                    "status": "fail",
                    "failure_reason": "invalid_forced_tool_call",
                    "failure_details": f"tool={forced_name}",
                    "output_text": "Невалидный forced tool call.",
                    "usage": _current_usage(),
                    "tool_calls_count": forced_tool_calls_count,
                }

            append_cc_backend_trace(
                artifact_workdir,
                {
                    "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                    "round": 0,
                    "count": 1,
                    "forced": True,
                    "tools": [
                        {
                            "id": forced_tool_id,
                            "name": forced_name,
                            "input": forced_input,
                        }
                    ],
                },
            )
            if _requires_approval(forced_name, forced_input, forced_tool_id):
                blocked_tool = _blocked_tool_payload(
                    forced_name, forced_input, source="forced_tool_call", task_id=task_id, current_session=current_session
                )
                # Must match the TOOL_APPROVAL_REQUIRED id, or the stored approval cannot be resolved.
                blocked_tool["tool_use_id"] = forced_tool_id
                if forced_approval_reason and not blocked_tool.get("reason"):
                    blocked_tool["reason"] = forced_approval_reason
                elif forced_assistant_text and not blocked_tool.get("reason"):
                    blocked_tool["reason"] = forced_assistant_text
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.TOOL_APPROVAL_REQUIRED,
                        "round": 0,
                        "tool_use_id": forced_tool_id,
                        **blocked_tool,
                    },
                )
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.TASK_FINISH,
                        "status": approval_wait_status,
                        "reason": "approval_required",
                        "rounds": 0,
                        "tool_calls_count": forced_tool_calls_count,
                        "approval_blocked_tools": [blocked_tool],
                    },
                )
                return {
                    "status": approval_wait_status,
                    "failure_reason": "approval_required",
                    "failure_details": (
                        f"tool={blocked_tool['tool']} target={blocked_tool['target']}"
                    ),
                    "output_text": "Для выполнения этого действия требуется подтверждение.",
                    "usage": _current_usage(),
                    "tool_calls_count": forced_tool_calls_count,
                    "approval_blocked_tools": [blocked_tool],
                }

            tool_started_at = time.monotonic()
            try:
                called_tool_names.add(forced_name)
                forced_write_path = _write_target_path(forced_name, forced_input, workdir)
                if forced_write_path:
                    called_write_paths.add(forced_write_path)
                forced_result_text = execute_tool(
                    forced_name,
                    forced_input,
                    workdir,
                    filesystem_mode,
                    guard_mode=guard_mode,
                    checkpoint_manager=checkpoint_manager,
                    change_tracker=change_tracker,
                    current_session=current_session,
                    source_platform=source_platform,
                    source_chat_id=source_chat_id,
                    delegate_depth=delegate_depth,
                    iteration_budget=iteration_budget,
                    base_url=base_url,
                    model=model,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    timeout_s=timeout_s,
                    task_id=task_id,
                    artifact_workdir=artifact_workdir,
                    tool_result_access=tool_result_access,
                    read_context_window_tokens=context_window_tokens,
                    read_max_chars=max(512, DEFAULT_TOOL_RESULT_STORE_CONFIG.turn_budget_chars // (1 + len(pending_tool_calls))),
                    tool_use_id=forced_tool_id,
                    mcp_write_approval=consume_mcp_write_grant,
                    profile=profile,
                    delegate_metadata=metadata,
                    delegate_available_tools=anthropic_tools,
                    approvals_enabled_provider=approvals_enabled_provider,
                    checkpoint_config=checkpoint_settings,
                    data_root=data_root,
                    api_format=api_format,
                    request_headers=headers if is_responses_api else request_headers,
                    user_input_provider=user_input_provider,
                    web_tool_provider=web_tool_provider,
                    session_search_provider=session_search_provider,
                    vault_store_provider=vault_store_provider,
                    memory_forget_provider=memory_forget_provider,
                    **({"goal_control_provider": goal_control_provider} if goal_control_provider is not None else {}),
                    user_message=user_message,
                )
                if forced_name == "suggest_enable_mcp":
                    refresh_hot_reloaded_mcp_tools(str(forced_result_text))
            except Exception as exc:
                forced_result_text = f"Error executing {forced_name}: {exc}"
            append_cc_backend_trace(
                artifact_workdir,
                {
                    "kind": CcTraceEvent.TOOL_RESULT,
                    "round": 0,
                    "tool_use_id": forced_tool_id,
                    "tool_name": forced_name,
                    **({'browser_diagnostics':browser_tool_diagnostics(forced_input,str(forced_result_text))} if forced_name=='desktop_browser' else {}),
                    "tool_input": forced_input,
                    "forced": True,
                    "raw_result_len": len(str(forced_result_text)),
                    "raw_result_preview": str(forced_result_text)[:500],
                    "result_for_model_len": len(str(forced_result_text)),
                    "result_for_model_preview": str(forced_result_text)[:500],
                    "context_output": str(forced_result_text),
                    "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                },
            )
            completion_recovery.record(
                forced_tool_id, forced_name,
                False if _tool_result_error(str(forced_result_text)) else
                _result_indicates_success(forced_result_text, tool_name=forced_name),
            )
            delegated_approval = _delegated_approval(forced_name, forced_result_text)
            if delegated_approval is not None:
                return delegated_approval
            forced_error = _tool_result_error(str(forced_result_text))
            forced_status = "fail" if forced_error else "ok"
            forced_is_terminal_write = (
                forced_status == "ok"
                and forced_name == "Write"
                and forced_source == "structural_file_artifact"
            )
            if forced_is_terminal_write:
                postamble, add_in, add_out = _make_forced_write_postamble(
                    url=url,
                    model=model,
                    system_prompt=system_prompt,
                    headers=headers,
                    timeout_s=timeout_s,
                    file_path=str(forced_input.get("file_path") or ""),
                    raw_result=str(forced_result_text),
                    workdir=artifact_workdir,
                    log_fn=log_fn,
                    is_openai_api=is_openai_api,
                )
                total_input_tokens += add_in
                total_output_tokens += add_out
                if postamble:
                    forced_result_text = postamble
            if not forced_is_terminal_write:
                tool_calls_count += forced_tool_calls_count
                result_for_model = maybe_persist_tool_result(
                    content=str(forced_result_text),
                    tool_name=forced_name,
                    tool_use_id=forced_tool_id,
                    workdir=artifact_workdir,
                    access=tool_result_access,
                )
                session_budget.record_tool_result(
                    raw_content=str(forced_result_text),
                    model_content=result_for_model,
                )
                seeded_tool_uses = [
                    {
                        "type": "tool_use",
                        "id": forced_tool_id,
                        "name": forced_name,
                        "input": forced_input,
                    }
                ]
                seeded_tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": forced_tool_id,
                        "content": result_for_model,
                    }
                ]
                normalized_pending_tool_calls = [
                    {
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "input": item.get("input", {})
                        if isinstance(item.get("input"), dict)
                        else {},
                    }
                    for item in pending_tool_calls
                    if isinstance(item, dict)
                ]
                if normalized_pending_tool_calls:
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                            "round": 0,
                            "count": len(normalized_pending_tool_calls),
                            "forced": True,
                            "pending_after_approval": True,
                            "tools": normalized_pending_tool_calls,
                        },
                    )
                for pending_index, pending in enumerate(pending_tool_calls):
                    if not isinstance(pending, dict):
                        continue
                    pending_name = str(pending.get("name") or "").strip()
                    pending_input = (
                        pending.get("input")
                        if isinstance(pending.get("input"), dict)
                        else {}
                    )
                    pending_tool_id = (
                        str(pending.get("id") or "").strip()
                        or f"pending_tool_call_{pending_index + 1}"
                    )
                    pending_missing_fields = _missing_required_tool_fields(
                        anthropic_tools, pending_name, pending_input
                    )
                    if pending_missing_fields:
                        pending_result_text = json.dumps(
                            {
                                "error": (
                                    f"Invalid tool input for {pending_name}: "
                                    f"missing required field(s): {', '.join(pending_missing_fields)}. "
                                    "Call the tool again with all required fields."
                                ),
                                "tool_name": pending_name,
                                "missing_required_fields": pending_missing_fields,
                            },
                            ensure_ascii=False,
                        )
                    elif not pending_name or pending_name not in valid_tool_names:
                        pending_result_text = json.dumps(
                            {
                                "error": (
                                    f"Unknown tool '{pending_name}'. "
                                    f"Available: {sorted(valid_tool_names)}"
                                )
                            },
                            ensure_ascii=False,
                        )
                    elif _requires_approval(pending_name, pending_input, pending_tool_id):
                        blocked_tool = _blocked_tool_payload(
                            pending_name, pending_input, source="pending_tool_call", task_id=task_id, current_session=current_session
                        )
                        blocked_tool["tool_use_id"] = pending_tool_id
                        remaining_pending = [
                            item
                            for item in pending_tool_calls[pending_index + 1 :]
                            if isinstance(item, dict)
                        ]
                        if remaining_pending:
                            blocked_tool["pending_tool_calls"] = remaining_pending
                        if forced_reasoning_content:
                            blocked_tool["reasoning_content"] = forced_reasoning_content
                        if forced_assistant_text:
                            blocked_tool["assistant_text"] = forced_assistant_text
                        approval_context_messages = [
                            *messages[1:],
                            {
                                "role": "assistant",
                                "content": [
                                    *(
                                        [
                                            {
                                                "type": "__reasoning",
                                                "text": forced_reasoning_content,
                                            }
                                        ]
                                        if forced_reasoning_content
                                        else []
                                    ),
                                    *(
                                        [{"type": "text", "text": forced_assistant_text}]
                                        if forced_assistant_text
                                        else []
                                    ),
                                    *seeded_tool_uses,
                                ],
                            },
                            {"role": "user", "content": seeded_tool_results},
                        ]
                        if approval_context_messages:
                            blocked_tool["approval_context_messages"] = (
                                approval_context_messages
                            )
                        append_cc_backend_trace(
                            artifact_workdir,
                            {
                                "kind": CcTraceEvent.TOOL_APPROVAL_REQUIRED,
                                "round": 0,
                                "tool_use_id": pending_tool_id,
                                **blocked_tool,
                            },
                        )
                        append_cc_backend_trace(
                            artifact_workdir,
                            {
                                "kind": CcTraceEvent.TASK_FINISH,
                                "status": approval_wait_status,
                                "reason": "approval_required",
                                "rounds": 0,
                                "tool_calls_count": tool_calls_count,
                                "approval_blocked_tools": [blocked_tool],
                            },
                        )
                        return {
                            "status": approval_wait_status,
                            "failure_reason": "approval_required",
                            "failure_details": (
                                f"tool={blocked_tool['tool']} target={blocked_tool['target']}"
                            ),
                            "output_text": "Для выполнения этого действия требуется подтверждение.",
                            "usage": _current_usage(),
                            "tool_calls_count": tool_calls_count,
                            "approval_blocked_tools": [blocked_tool],
                        }
                    else:
                        pending_started_at = time.monotonic()
                        try:
                            called_tool_names.add(pending_name)
                            pending_write_path = _write_target_path(
                                pending_name, pending_input, workdir
                            )
                            if pending_write_path:
                                called_write_paths.add(pending_write_path)
                            pending_result_text = execute_tool(
                                pending_name,
                                pending_input,
                                workdir,
                                filesystem_mode,
                                guard_mode=guard_mode,
                                checkpoint_manager=checkpoint_manager,
                                change_tracker=change_tracker,
                                current_session=current_session,
                                source_platform=source_platform,
                                source_chat_id=source_chat_id,
                                delegate_depth=delegate_depth,
                                iteration_budget=iteration_budget,
                                base_url=base_url,
                                model=model,
                                system_prompt=system_prompt,
                                max_tokens=max_tokens,
                                timeout_s=timeout_s,
                                task_id=task_id,
                                # Sub-agent observability: the invocation ID and task working directory
                                # link the child launch with the `delegate_task` card in the web interface.
                                tool_use_id=pending_tool_id,
                                mcp_write_approval=consume_mcp_write_grant,
                                artifact_workdir=artifact_workdir,
                                tool_result_access=tool_result_access,
                                read_context_window_tokens=context_window_tokens,
                                read_max_chars=max(512, DEFAULT_TOOL_RESULT_STORE_CONFIG.turn_budget_chars // (1 + len(pending_tool_calls))),
                                profile=profile,
                                delegate_metadata=metadata,
                                delegate_available_tools=anthropic_tools,
                                approvals_enabled_provider=approvals_enabled_provider,
                                checkpoint_config=checkpoint_settings,
                                data_root=data_root,
                                api_format=api_format,
                                request_headers=headers if is_responses_api else request_headers,
                                user_input_provider=user_input_provider,
                                web_tool_provider=web_tool_provider,
                                session_search_provider=session_search_provider,
                                vault_store_provider=vault_store_provider,
                    memory_forget_provider=memory_forget_provider,
                    **({"goal_control_provider": goal_control_provider} if goal_control_provider is not None else {}),
                    user_message=user_message,
                            )
                            if pending_name == "suggest_enable_mcp":
                                refresh_hot_reloaded_mcp_tools(str(pending_result_text))
                        except Exception as exc:
                            pending_result_text = f"Error executing {pending_name}: {exc}"
                        append_cc_backend_trace(
                            artifact_workdir,
                            {
                                "kind": CcTraceEvent.TOOL_RESULT,
                                "round": 0,
                                "tool_use_id": pending_tool_id,
                                "tool_name": pending_name,
                                **({'browser_diagnostics':browser_tool_diagnostics(pending_input,str(pending_result_text))} if pending_name=='desktop_browser' else {}),
                                "tool_input": pending_input,
                                "pending_after_approval": True,
                                "raw_result_len": len(str(pending_result_text)),
                                "raw_result_preview": str(pending_result_text)[:500],
                                "result_for_model_len": len(str(pending_result_text)),
                                "result_for_model_preview": str(pending_result_text)[:500],
                                "context_output": str(pending_result_text),
                                "duration_ms": int(
                                    (time.monotonic() - pending_started_at) * 1000
                                ),
                            },
                        )
                    completion_recovery.record(
                        pending_tool_id, pending_name,
                        False if _tool_result_error(str(pending_result_text)) else
                        _result_indicates_success(pending_result_text, tool_name=pending_name),
                    )
                    delegated_approval = _delegated_approval(pending_name, pending_result_text)
                    if delegated_approval is not None:
                        return delegated_approval
                    pending_result_for_model = maybe_persist_tool_result(
                        content=str(pending_result_text),
                        tool_name=pending_name,
                        tool_use_id=pending_tool_id,
                        workdir=artifact_workdir,
                        access=tool_result_access,
                    )
                    session_budget.record_tool_result(
                        raw_content=str(pending_result_text),
                        model_content=pending_result_for_model,
                    )
                    tool_calls_count += 1
                    seeded_tool_uses.append(
                        {
                            "type": "tool_use",
                            "id": pending_tool_id,
                            "name": pending_name,
                            "input": pending_input,
                        }
                    )
                    seeded_tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": pending_tool_id,
                            "content": pending_result_for_model,
                        }
                    )
                messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            *(
                                [
                                    {
                                        "type": "__reasoning",
                                        "text": forced_reasoning_content,
                                    }
                                ]
                                if forced_reasoning_content
                                else []
                            ),
                            *(
                                [{"type": "text", "text": forced_assistant_text}]
                                if forced_assistant_text
                                else []
                            ),
                            *seeded_tool_uses,
                        ],
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": seeded_tool_results,
                    }
                )
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.POST_TURN_VERDICT,
                        "round": 0,
                        "action": "continue_after_approval",
                        "reason": "forced_tool_result_seeded",
                        "target": forced_name,
                    },
                )
            else:
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.TASK_FINISH,
                        "status": forced_status,
                        "reason": "forced_tool_call_completed",
                        "rounds": 0,
                        "tool_calls_count": forced_tool_calls_count,
                        "tool_name": forced_name,
                        "error": forced_error or "",
                        "output_preview": str(forced_result_text)[:500],
                    },
                )
                response = {
                    "status": forced_status,
                    "output_text": str(forced_result_text),
                    "usage": _current_usage(),
                    "tool_calls_count": forced_tool_calls_count,
                }
                if forced_error:
                    response["failure_reason"] = "forced_tool_call_failed"
                    response["failure_details"] = forced_error
                return response

        for round_num in count():
            if (
                loop_limits.max_tool_rounds is not None
                and round_num >= loop_limits.max_tool_rounds
            ):
                break
            session_budget.begin_round()
            # Cooperative interrupt check (C4)
            if is_interrupt_requested() or _is_task_cancelling():
                log_fn("[cc-backend] Interrupt requested, stopping.")
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.TASK_FINISH,
                        "status": "fail",
                        "reason": "interrupted",
                        "round": round_num + 1,
                        "parent_task_id": parent_task_id or None,
                    },
                )
                return {
                    "status": "fail",
                    "failure_reason": "interrupted",
                    "failure_details": "Task was interrupted by parent.",
                    "output_text": "Task interrupted.",
                    "usage": _current_usage(),
                    "tool_calls_count": tool_calls_count,
                }

            if iteration_budget is not None and not iteration_budget.consume():
                reason = (
                    f"iteration_budget_exhausted ({iteration_budget.used}/"
                    f"{iteration_budget.max_total})"
                )
                log_fn(f"[cc-backend] {reason}")
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.TASK_FINISH,
                        "status": "fail",
                        "reason": "iteration_budget_exhausted",
                        "round": round_num + 1,
                        "used": iteration_budget.used,
                        "max": iteration_budget.max_total,
                        "parent_task_id": parent_task_id or None,
                    },
                )
                return {
                    "status": "fail",
                    "failure_reason": "iteration_budget_exhausted",
                    "failure_details": reason,
                    "output_text": "Subagent iteration budget exhausted.",
                    "usage": _current_usage(),
                    "tool_calls_count": tool_calls_count,
                }
            compacted = _maybe_apply_reactive_compaction(round_index=round_num)
            remaining_context = estimate_context_tokens(system_prompt=system_prompt, messages=messages, tools=tools)
            if not compacted and remaining_context > context_window_tokens - reserved_output_tokens:
                append_cc_backend_trace(artifact_workdir, {
                    "kind": CcTraceEvent.TASK_FINISH, "status": "fail", "reason": "compaction_required",
                    "round": round_num + 1, "estimated_context_tokens": remaining_context,
                    "error": "compaction_required: original history preserved; context exceeds model window",
                })
                return {
                    "status": "fail", "failure_reason": "compaction_required",
                    "error": "compaction_required: original history preserved; context exceeds model window",
                    "output_text": "Контекст превышает окно модели; история сохранена, отправка отменена после неудачной компактизации.",
                    "usage": _current_usage(), "tool_calls_count": tool_calls_count,
                }
            payload: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": messages,
                "tools": tools,
            }
            if is_responses_api:
                pass  # The Responses adapter consumes the native internal history.
            elif is_openai_api:
                payload.pop("system", None)
                payload["messages"] = _anthropic_messages_to_openai(
                    [{"role": "system", "content": system_prompt}] + messages
                )
                if reasoning_format is not None:
                    payload["reasoning_format"] = reasoning_format
                # Thinking-mode uplinks (via DeepSeek through opencode zen) require that assistant messages containing tool_calls include reasoning_content.
                payload["messages"], _reasoning_filled = _ensure_reasoning_passback(
                    payload["messages"],
                    force=reasoning_passback_forced,
                )
                if _reasoning_filled:
                    log_fn(
                        "[cc-backend] reasoning_content passback: filled "
                        f"{_reasoning_filled} assistant message(s)"
                    )
                # Enable OpenRouter prompt caching by session
                if current_session:
                    payload["prompt_cache_key"] = current_session
                # stream key will be set by _http_post_streaming_openai
            else:
                # Native Anthropic /v1/messages does not understand our private
                # `__reasoning` content type. The OpenAI converter maps it to
                # `reasoning_content` (valid for chat.completions), but for
                # Anthropic we must strip the blocks entirely — otherwise
                # provider returns HTTP 400 "unsupported content type
                # '__reasoning' (2013)". Filter is applied to a copy of the
                # message list so the in-memory round state stays intact.
                payload["messages"] = _strip_internal_reasoning_blocks(
                    payload["messages"]
                )
                # MiniMax-M3 on Anthropic-compatible endpoint defaults to
                # thinking:disabled, so we must explicitly enable it to get
                # reasoning_content blocks. Other models (real Anthropic,
                # etc.) handle this differently — gate on model name to avoid
                # surprises on non-MiniMax endpoints.
                if model.lower().startswith("minimax") and max_tokens >= 1024:
                    payload["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": min(8192, max(1024, max_tokens - 1024)),
                    }

            log_fn(f"[cc-backend] Round {round_num + 1}: POST {url}")

            image_payload = image_input.prepare(payload)
            if image_payload is not payload:
                log_fn("[cc-backend] Local llama.cpp image input disabled; sending text notices instead")
                payload = image_payload
            network_retry_count = 0
            recovery_started = time.monotonic()
            while True:
                request_timeout_s = timeout_s if network_retry_count == 0 else min(timeout_s, max(1, 30-(time.monotonic()-recovery_started)))
                if is_google_api:
                    from .google_generate_content import post_stream
                    body, lat_ms = post_stream(url, payload, headers, request_timeout_s,
                        trace_dir=artifact_workdir, round_num=round_num, parent_task_id=parent_task_id,
                        cancel_check=lambda: is_interrupt_requested() or _is_task_cancelling())
                elif is_openai_api:
                    body, lat_ms = _http_post_streaming_openai(
                        url, payload, headers, request_timeout_s,
                        log_fn=log_fn,
                        trace_dir=artifact_workdir,
                        round_num=round_num,
                        parent_task_id=parent_task_id,
                        backend_capabilities=backend_capabilities,
                        cancel_check=lambda: (
                            is_interrupt_requested() or _is_task_cancelling()
                        ),
                    )
                else:
                    body, lat_ms = _http_post(url, payload, headers, request_timeout_s)
                if isinstance(body, dict) and body.get("__interrupted__"):
                    log_fn("[cc-backend] Interrupt requested mid-stream, stopping.")
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.TASK_FINISH,
                            "status": "fail",
                            "reason": "interrupted",
                            "round": round_num + 1,
                            "parent_task_id": parent_task_id or None,
                        },
                    )
                    return {
                        "status": "fail",
                        "failure_reason": "interrupted",
                        "failure_details": "Task was interrupted by user.",
                        "output_text": "Task interrupted.",
                        "usage": _current_usage(),
                        "tool_calls_count": tool_calls_count,
                    }
                network_error = (
                    str(body.get("error") or "")
                    if isinstance(body, dict)
                    else ""
                )
                image_payload = image_input.recover(payload, body)
                if image_payload is not None:
                    payload = image_payload
                    log_fn("[cc-backend] Local llama.cpp rejected image input; retrying model request with text only")
                    append_cc_backend_trace(artifact_workdir, {
                        "kind": CcTraceEvent.PAYLOAD_RETRY,
                        "round": round_num + 1,
                        "reason": "llama_image_input_unsupported",
                        "parent_task_id": parent_task_id or None,
                    })
                    continue
                if (
                    _is_reasoning_passback_error(network_error)
                    and not reasoning_passback_forced
                ):
                    # Uplink rejected payload: assistant message with tool_calls
                    # arrived without reasoning_content. Repeating the same request once,
                    # forcing the field (including an empty string).
                    reasoning_passback_forced = True
                    payload["messages"], filled = _ensure_reasoning_passback(
                        payload["messages"],
                        force=True,
                    )
                    if filled:
                        log_fn(
                            "[cc-backend] Upstream requires reasoning_content passback; "
                            f"retrying round with {filled} assistant message(s) normalized"
                        )
                        append_cc_backend_trace(
                            artifact_workdir,
                            {
                                "kind": CcTraceEvent.PAYLOAD_RETRY,
                                "round": round_num + 1,
                                "reason": "reasoning_content_passback",
                                "filled_messages": filled,
                                "parent_task_id": parent_task_id or None,
                            },
                        )
                        continue
                    log_fn(
                        "[cc-backend] Upstream requires reasoning_content passback, "
                        "but payload already carries it for every tool_calls message"
                    )
                if (
                    not _is_transient_network_error(
                        network_error,
                        error_code=body.get("error_code") if isinstance(body, dict) else None,
                        error_diagnostics=model_failure_diagnostics(body),
                    )
                    or network_retry_count >= MAX_TRANSIENT_NETWORK_RETRIES
                ):
                    break
                from .diagnostics import model_observation, retry_decision
                diagnostic = model_observation(body, profile=profile, model=model, session_id=current_session or '', task_id=task_id or '')
                # Preserve explicitly supported legacy text-only transports while migrating their producers.
                if diagnostic['code'] in {'INTERNAL.UNEXPECTED', 'ERROR.UNKNOWN'}:
                    diagnostic['code'] = 'NET.TRANSPORT_FAILURE'
                decision = retry_decision(diagnostic, attempt=network_retry_count+1,
                    elapsed_s=time.monotonic()-recovery_started, replay_safe=True)
                if not decision['retry']:
                    break
                retry_delay_s = decision['delay_ms']/1000
                network_retry_count += 1
                log_fn(
                    f"[cc-backend] transient network error, retry "
                    f"{network_retry_count}/{MAX_TRANSIENT_NETWORK_RETRIES}: "
                    f"{network_error}"
                )
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.NETWORK_RETRY,
                        "round": round_num + 1,
                        "attempt": network_retry_count,
                        "max_attempts": MAX_TRANSIENT_NETWORK_RETRIES,
                        "delay_ms": int(retry_delay_s * 1000),
                        "error": network_error,
                        "error_diagnostics": model_failure_diagnostics(body),
                        "diagnostic": {**diagnostic, 'attempt': network_retry_count+1, 'next_retry_at_ms': decision['next_retry_at_ms']},
                    },
                )
                time.sleep(retry_delay_s)
            if is_openai_api and isinstance(body, dict) and not body.get("error"):
                body = _openai_response_to_anthropic(body, valid_tool_names)
            if network_retry_count and isinstance(body, dict) and not body.get('error'):
                append_cc_backend_trace(artifact_workdir, {'kind': CcTraceEvent.NETWORK_RECOVERED,
                    'diagnostic': diagnostic, 'attempt': network_retry_count+1, 'proof': 'operation_completed'})
            if isinstance(body, dict) and body.get("error"):
                from .diagnostics import model_observation
                diagnostic = model_observation(body, profile=profile, model=model, session_id=current_session or '', task_id=task_id or '')
                http_error = str(body.get("error") or "HTTP error")
                if _is_prompt_too_long_error(str(body.get("error") or "")):
                    if _maybe_apply_reactive_compaction(
                        round_index=round_num,
                        prompt_too_long=True,
                    ):
                        continue
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.TASK_FINISH,
                        "status": "fail",
                        "reason": "http_error",
                        "error_diagnostics":model_failure_diagnostics(body),
                        "diagnostic": diagnostic,
                        "round": round_num + 1,
                        "error": http_error,
                        "output_preview": http_error,
                    },
                )
                return {
                    "status": "fail",
                    "failure_reason": "http_error",
                    "failure_details": http_error,
                    "diagnostic": diagnostic,
                    "output_text": body.get("error", "HTTP error"),
                    "usage": _current_usage(),
                    "tool_calls_count": tool_calls_count,
                }

            usage = body.get("usage", {})
            total_input_tokens += usage.get("input_tokens", 0)
            total_output_tokens += usage.get("output_tokens", 0)

            # Collect tool_use blocks and text from response
            content_blocks = _normalize_tagged_reasoning_blocks(body.get("content", []))
            tool_use_blocks: list[dict] = []
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            salvaged_from_text = False

            for block in content_blocks:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        tool_use_blocks.append(block)
                    elif block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "__reasoning":
                        reasoning_parts.append(str(block.get("text", "")))

            output_text_preview = "\n".join(text_parts)
            history_echo = guard_local_history and contains_historical_tool_echo(output_text_preview)
            if not is_openai_api and not is_google_api:
                for reasoning_text in reasoning_parts:
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.MODEL_REASONING_DELTA,
                            "round": round_num + 1,
                            "text": reasoning_text,
                        },
                    )
            append_cc_backend_trace(
                artifact_workdir,
                {
                    "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
                    "round": round_num + 1,
                    "latency_ms": lat_ms,
                    "http_error": str(body.get("error") or "")
                    if isinstance(body, dict)
                    else "",
                    "backend_kind": backend_capabilities.backend,
                    "stop_reason": body.get("stop_reason"),
                    "consumed_tool_result_ids": (
                        _request_tool_result_ids(payload.get("messages"))
                        if not _model_output_limited(body) and not history_echo else []
                    ),
                    "content_types": [
                        str(block.get("type") or "")
                        for block in content_blocks
                        if isinstance(block, dict)
                    ],
                    "usage": body.get("usage", {}) if isinstance(body, dict) else {},
                    "text_len": 0 if history_echo else len(output_text_preview),
                    "text_preview": "" if history_echo else output_text_preview[:1000],
                    "text_full": "" if history_echo else output_text_preview,
                },
            )

            if _model_output_limited(body):
                return _output_limit_failure(
                    workdir=artifact_workdir,
                    partial_text="" if history_echo else output_text_preview,
                    usage=_current_usage(), tool_calls_count=tool_calls_count,
                )

            if history_echo:
                local_history_rejections += 1
                append_cc_backend_trace(artifact_workdir, {
                    "kind": CcTraceEvent.POST_TURN_RETRY_REQUIRED,
                    "round": round_num + 1,
                    "reason": "local_tool_history_echo",
                    "attempt": local_history_rejections,
                })
                if local_history_rejections >= 2:
                    message = "Модель повторно вернула служебную историю вместо ответа. Продолжение остановлено, чтобы не повторять старые действия."
                    append_cc_backend_trace(artifact_workdir, {
                        "kind": CcTraceEvent.TASK_FINISH, "status": "fail",
                        "reason": "local_tool_history_echo_repeated",
                        "rounds": round_num + 1, "tool_calls_count": tool_calls_count,
                        "output_preview": message,
                    })
                    return {"status": "fail", "failure_reason": "local_tool_history_echo_repeated",
                            "output_text": message, "usage": _current_usage(),
                            "tool_calls_count": tool_calls_count}
                from .prompt_assembly import local_history_echo_retry_message
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": local_history_echo_retry_message()}
                ]})
                continue

            if not tool_use_blocks:
                output_text = output_text_preview
                if not output_text.strip() and reasoning_parts:
                    consecutive_reasoning_only_rounds += 1
                    log_fn(
                        "[cc-backend] Retrying after reasoning-only response "
                        f"(attempt {consecutive_reasoning_only_rounds})"
                    )
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.POST_TURN_RETRY_REQUIRED,
                            "round": round_num + 1,
                            "reason": "reasoning_only_response",
                            "target": "",
                            "attempt": consecutive_reasoning_only_rounds,
                        },
                    )
                    if consecutive_reasoning_only_rounds >= 2:
                        append_cc_backend_trace(
                            artifact_workdir,
                            {
                                "kind": CcTraceEvent.TASK_FINISH,
                                "status": "fail",
                                "reason": "reasoning_only_response_repeated",
                                "rounds": round_num + 1,
                                "tool_calls_count": tool_calls_count,
                            },
                        )
                        return {
                            "status": "fail",
                            "failure_reason": "reasoning_only_response_repeated",
                            "failure_details": (
                                "Model returned reasoning_content without visible text "
                                "or tool_use twice in a row."
                            ),
                            "output_text": (
                                "Модель дважды подряд вернула только скрытое reasoning "
                                "без видимого ответа и без tool_use."
                            ),
                            "usage": _current_usage(),
                            "tool_calls_count": tool_calls_count,
                        }
                    messages.append(
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "__reasoning",
                                    "text": "\n".join(reasoning_parts),
                                }
                            ],
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": _build_reasoning_only_retry_message(),
                                }
                            ],
                        }
                    )
                    continue
                if round_num >= 2:
                    log_fn(f"[cc-backend] DEBUG text_response r={round_num + 1} messages before round:")
                    log_fn(f"  system_prompt len={len(system_prompt)}")
                    for i, msg in enumerate(messages):
                        c = msg.get("content", "")
                        cstr = str(c)[:300] if isinstance(c, list) else (c[:300] if isinstance(c, str) else str(c)[:300])
                        log_fn(f"  msg[{i}] role={msg['role']} preview={repr(cstr)}")
                (
                    salvaged_tool,
                    salvage_ok,
                    validation_error,
                ) = try_salvage_textual_tool_call(
                    output_text, anthropic_tools, workdir
                )
                if salvaged_tool is not None and salvage_ok:
                    session_budget.record_salvage_success()
                    log_fn(
                        f"[cc-backend] Salvaged textual pseudo-tool-call: {salvaged_tool.get('name')}"
                    )
                    salvage_meta = get_salvage_meta(salvaged_tool)
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.PLANNER_SALVAGE_SUCCESS,
                            "round": round_num + 1,
                            "tool_name": salvaged_tool.get("name"),
                            "parser": salvage_meta.get("parser", ""),
                            "source_shape": salvage_meta.get("source_shape", ""),
                            "evidence": salvage_meta.get("evidence", ""),
                            "output_preview": output_text[:1000],
                        },
                    )
                    tool_use_blocks = [salvaged_tool]
                    content_blocks = [
                        {"type": "tool_use", **strip_salvage_meta(salvaged_tool)}
                    ]
                    salvaged_from_text = True
                    consecutive_textual_artifact_rounds = 0
                elif salvaged_tool is not None:
                    session_budget.record_salvage_failure()
                    log_fn(
                        "[cc-backend] Rejected textual pseudo-tool-call "
                        f"{salvaged_tool.get('name')}: {validation_error}"
                    )
                    consecutive_textual_artifact_rounds += 1
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.PLANNER_SALVAGE_REJECTED,
                            "round": round_num + 1,
                            "tool_name": salvaged_tool.get("name"),
                            "validation_error": validation_error,
                            "attempt": consecutive_textual_artifact_rounds,
                            "output_preview": output_text[:1000],
                        },
                    )
                    if consecutive_textual_artifact_rounds >= 2:
                        append_cc_backend_trace(
                            artifact_workdir,
                            {
                                "kind": CcTraceEvent.TASK_FINISH,
                                "status": "fail",
                                "reason": "invalid_salvaged_tool_call",
                                "round": round_num + 1,
                                "tool_name": salvaged_tool.get("name"),
                                "validation_error": validation_error,
                                "output_preview": output_text[:1000],
                            },
                        )
                        return {
                            "status": "fail",
                            "failure_reason": "invalid_salvaged_tool_call",
                            "failure_details": (
                                f"tool={salvaged_tool.get('name')} "
                                f"validation_error={validation_error}\n"
                                f"output_preview={output_text[:1000]}"
                            ),
                            "output_text": (
                                "Модель дважды подряд пыталась вызвать инструмент текстом, "
                                "но не смогла сформировать валидный tool_use."
                            ),
                            "usage": _current_usage(),
                            "tool_calls_count": tool_calls_count,
                        }
                    messages.append(
                        {
                            "role": "assistant",
                            "content": [{"type": "text", "text": output_text}],
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": build_textual_tool_retry_message(
                                        output_text
                                    ),
                                }
                            ],
                        }
                    )
                    continue

                if not tool_use_blocks and contains_textual_tool_artifact(
                    output_text, anthropic_tools
                ):
                    session_budget.record_textual_tool_retry()
                    consecutive_textual_artifact_rounds += 1
                    log_fn(
                        "[cc-backend] Retrying after raw textual tool artifact in assistant output"
                    )
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.PLANNER_TEXTUAL_ARTIFACT_RETRY,
                            "round": round_num + 1,
                            "attempt": consecutive_textual_artifact_rounds,
                            "output_preview": output_text[:1000],
                        },
                    )
                    if consecutive_textual_artifact_rounds >= 2:
                        append_cc_backend_trace(
                            artifact_workdir,
                            {
                                "kind": CcTraceEvent.TASK_FINISH,
                                "status": "fail",
                                "reason": "raw_textual_tool_artifact",
                                "round": round_num + 1,
                                "output_preview": output_text[:1000],
                            },
                        )
                        return {
                            "status": "fail",
                            "failure_reason": "raw_textual_tool_artifact",
                            "failure_details": f"output_preview={output_text[:1000]}",
                            "output_text": (
                                "Модель дважды подряд вывела псевдовызов инструмента текстом "
                                "вместо корректного tool_use."
                            ),
                            "usage": _current_usage(),
                            "tool_calls_count": tool_calls_count,
                        }
                    messages.append(
                        {
                            "role": "assistant",
                            "content": [{"type": "text", "text": output_text}],
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": build_textual_tool_retry_message(
                                        output_text
                                    ),
                                }
                            ],
                        }
                    )
                    continue

            if not tool_use_blocks:
                consecutive_textual_artifact_rounds = 0
                consecutive_reasoning_only_rounds = 0
                policy_denial = body.get("policy_denial")
                if policy_denial:
                    session_budget.record_policy_denial_retry()
                    consecutive_policy_denial_rounds += 1
                    log_fn(
                        f"[cc-backend] Policy denial encountered: {policy_denial}"
                    )
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.POLICY_DENIAL_RETRY,
                            "round": round_num + 1,
                            "reason": policy_denial,
                            "attempt": consecutive_policy_denial_rounds,
                            "output_preview": output_text[:1000],
                        },
                    )
                    if consecutive_policy_denial_rounds >= 2:
                        append_cc_backend_trace(
                            artifact_workdir,
                            {
                                "kind": CcTraceEvent.TASK_FINISH,
                                "status": "fail",
                                "reason": "policy_denial_repeated",
                                "round": round_num + 1,
                                "output_preview": output_text[:1000],
                            },
                        )
                        return {
                            "status": "fail",
                            "failure_reason": "policy_denial_repeated",
                            "failure_details": f"policy_denial={policy_denial}",
                            "output_text": output_text,
                            "usage": _current_usage(),
                            "tool_calls_count": tool_calls_count,
                        }
                    messages.append(
                        {
                            "role": "assistant",
                            "content": [{"type": "text", "text": output_text}],
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": _build_policy_denial_retry_message(
                                        policy_denial
                                    ),
                                }
                            ],
                        }
                    )
                    continue

                text_guard = evaluate_text_completion_guards(
                    user_message=user_message,
                    assistant_text=output_text,
                    workdir=workdir,
                    must_write_skills=must_write_skills,
                    called_tool_names=called_tool_names,
                    called_write_paths=called_write_paths,
                )
                if not (review_completion and text_guard.allowed):
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.POST_TURN_VERDICT,
                            "round": round_num + 1,
                            "action": text_guard.action,
                            "reason": text_guard.reason,
                            "target": text_guard.target or "",
                        },
                    )

                if (
                    text_guard.action == APPROVAL_REQUIRED
                    and text_guard.pending_write is not None
                ):
                    session_budget.record_approval_required()
                    pending_input = text_guard.pending_write.to_tool_input()
                    blocked_tool = _blocked_tool_payload(
                        "Write",
                        pending_input,
                        source="structural_file_artifact",
                    )
                    blocked_tool["tool_use_id"] = "pending_write"
                    if text_guard.pending_write.reason and not blocked_tool.get("reason"):
                        blocked_tool["reason"] = text_guard.pending_write.reason
                    log_fn(
                        "[cc-backend] Text-only response contains structural file artifact; "
                        f"requiring Write approval for {blocked_tool['target']}"
                    )
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.POST_TURN_PENDING_WRITE_DETECTED,
                            "round": round_num + 1,
                            "reason": text_guard.pending_write.reason,
                            "target": text_guard.pending_write.file_path,
                            "content_len": len(text_guard.pending_write.content),
                        },
                    )
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.TOOL_APPROVAL_REQUIRED,
                            "round": round_num + 1,
                            "tool_use_id": "pending_write",
                            **blocked_tool,
                        },
                    )
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.TASK_FINISH,
                            "status": approval_wait_status,
                            "reason": "approval_required",
                            "rounds": round_num + 1,
                            "tool_calls_count": tool_calls_count,
                            "approval_blocked_tools": [blocked_tool],
                        },
                    )
                    return {
                        "status": approval_wait_status,
                        "failure_reason": "approval_required",
                        "failure_details": (
                            f"tool={blocked_tool['tool']} target={blocked_tool['target']}"
                        ),
                        "output_text": "Для записи найденного файлового артефакта требуется подтверждение.",
                        "usage": _current_usage(),
                        "tool_calls_count": tool_calls_count,
                        "approval_blocked_tools": [blocked_tool],
                    }

                if text_guard.action == RETRY_REQUIRED:
                    log_fn(
                        "[cc-backend] Post-turn guard requested retry: "
                        f"{text_guard.reason} target={text_guard.target}"
                    )
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.POST_TURN_RETRY_REQUIRED,
                            "round": round_num + 1,
                            "reason": text_guard.reason,
                            "target": text_guard.target,
                            "output_preview": output_text[:500],
                        },
                    )
                    messages.append(
                        {
                            "role": "assistant",
                            "content": [{"type": "text", "text": output_text}],
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": text_guard.retry_message or "",
                                }
                            ],
                        }
                    )
                    continue

                # Check the session queue before accepting a text-only completion.
                # Messages received during execution continue under the same task ID.
                messages.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": output_text}],
                    }
                )
                if _deliver_pending_steers(round_num + 1) > 0:
                    continue

                if review_completion:
                    if iteration_budget is not None and not iteration_budget.consume():
                        append_cc_backend_trace(artifact_workdir, {
                            "kind": CcTraceEvent.TASK_FINISH, "status": "fail",
                            "reason": "iteration_budget_exhausted", "round": round_num + 1,
                            "tool_calls_count": tool_calls_count,
                        })
                        return {"status": "fail", "failure_reason": "iteration_budget_exhausted",
                                "output_text": "Бюджет итераций исчерпан до проверки завершения задачи.",
                                "usage": _current_usage(), "tool_calls_count": tool_calls_count}
                    review, review_error, add_in, add_out = _review_local_completion(
                        url=url, model=model, system_prompt=system_prompt, messages=messages,
                        receipts=completion_recovery.receipts, tools=anthropic_tools,
                        headers=headers, timeout_s=timeout_s, max_tokens=max_tokens,
                        context_window_tokens=context_window_tokens,
                        is_openai_api=is_openai_api, reasoning_format=reasoning_format,
                        cancel_check=lambda: is_interrupt_requested() or _is_task_cancelling(),
                    )
                    total_input_tokens += add_in
                    total_output_tokens += add_out
                    append_cc_backend_trace(artifact_workdir, {
                        "kind": CcTraceEvent.MODEL_COMPLETION_REVIEW,
                        "round": round_num + 1,
                        "status": review.status if review else "invalid",
                        "requires_tools": review.requires_tools if review else None,
                        "evidence_ids": list(review.evidence_ids) if review else [],
                        "error": review_error,
                        "usage": {"input_tokens": add_in, "output_tokens": add_out},
                    })
                    interrupted = is_interrupt_requested() or _is_task_cancelling()
                    if interrupted or review_error == "interrupted":
                        review_error = "interrupted"
                    elif _deliver_pending_steers(round_num + 1) > 0:
                        continue
                    if review_error:
                        message = ("Задача остановлена пользователем." if review_error == "interrupted" else
                                   "Не удалось проверить завершение задачи. Работа не отмечена выполненной.")
                        append_cc_backend_trace(artifact_workdir, {
                            "kind": CcTraceEvent.TASK_FINISH, "status": "fail",
                            "reason": review_error, "rounds": round_num + 1,
                            "tool_calls_count": tool_calls_count,
                        })
                        return {"status": "fail", "failure_reason": review_error,
                                "output_text": message, "usage": _current_usage(),
                                "tool_calls_count": tool_calls_count}
                    if review.status == "continue":
                        can_continue = completion_recovery.request_continuation()
                        reason = "completion_blocked_unfinished_action"
                        append_cc_backend_trace(artifact_workdir, {
                            "kind": CcTraceEvent.POST_TURN_VERDICT,
                            "round": round_num + 1, "action": RETRY_REQUIRED,
                            "reason": reason, "attempt": completion_recovery.attempts,
                        })
                        if not can_continue:
                            message = "Модель повторно завершила ответ без продвижения задачи после автоматического продолжения. Задача не выполнена."
                            append_cc_backend_trace(artifact_workdir, {
                                "kind": CcTraceEvent.TASK_FINISH, "status": "fail",
                                "reason": "unfinished_action_repeated", "rounds": round_num + 1,
                                "tool_calls_count": tool_calls_count,
                            })
                            return {"status": "fail", "failure_reason": "unfinished_action_repeated",
                                    "output_text": message, "usage": _current_usage(),
                                    "tool_calls_count": tool_calls_count}
                        from .prompt_assembly import incomplete_action_retry_message
                        append_cc_backend_trace(artifact_workdir, {
                            "kind": CcTraceEvent.POST_TURN_RETRY_REQUIRED,
                            "round": round_num + 1, "reason": reason,
                            "attempt": completion_recovery.attempts,
                        })
                        messages.append({"role": "user", "content": [
                            {"type": "text", "text": incomplete_action_retry_message()}
                        ]})
                        continue

                    append_cc_backend_trace(artifact_workdir, {
                        "kind": CcTraceEvent.POST_TURN_VERDICT,
                        "round": round_num + 1, "action": "completion_allowed",
                        "reason": "completion_reported_blocker" if review.status == "blocked" else "completion_allowed",
                    })

                # Model returned a completed answer or a genuine blocker.
                log_fn(
                    f"[cc-backend] Completed after {round_num + 1} round(s), {tool_calls_count} tool calls"
                )
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.TASK_FINISH,
                        "status": "ok",
                        "rounds": round_num + 1,
                        "tool_calls_count": tool_calls_count,
                        "output_preview": output_text[:500],
                    },
                )
                return {
                    "status": "ok",
                    "output_text": output_text,
                    "usage": _current_usage(),
                    "tool_calls_count": tool_calls_count,
                }

            # Process tool_use blocks
            consecutive_textual_artifact_rounds = 0  # successful tool use breaks the streak
            consecutive_reasoning_only_rounds = 0
            tool_calls_count += len(tool_use_blocks)
            log_fn(f"[cc-backend] Model requested {len(tool_use_blocks)} tool call(s)")
            # Surface the assistant's preamble ("what I'm about to do") to
            # progress consumers (e.g. Telegram) before running the tools.
            if progress_callback and tool_use_blocks and text_parts:
                preamble_text = _clean_preamble(text_parts)
                if preamble_text:
                    try:
                        progress_callback("assistant.text", text=preamble_text)
                    except Exception:
                        pass
            current_tool_batch_signature = tuple(
                _tool_call_signature(
                    str(block.get("name") or ""),
                    block.get("input", {})
                    if isinstance(block.get("input"), dict)
                    else block.get("input"),
                )
                for block in tool_use_blocks
                if isinstance(block, dict)
            )
            session_budget.record_tool_batch(
                current_tool_batch_signature,
                count=len(tool_use_blocks),
            )
            append_cc_backend_trace(
                artifact_workdir,
                {
                    "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                    "round": round_num + 1,
                    "count": len(tool_use_blocks),
                    "consecutive_same_batch": session_budget.consecutive_same_tool_batch,
                    "tools": [
                        {
                            "id": str(block.get("id") or ""),
                            "name": str(block.get("name") or ""),
                            "input": block.get("input", {})
                            if isinstance(block.get("input"), dict)
                            else {},
                        }
                        for block in tool_use_blocks
                        if isinstance(block, dict)
                    ],
                },
            )

            # Add assistant message with tool_use blocks to conversation
            assistant_content: list[dict[str, Any]] = []
            for block in content_blocks:
                if isinstance(block, dict):
                    assistant_content.append(block)
            messages.append({"role": "assistant", "content": assistant_content})
            if task_id:
                message_history_ids[id(messages[-1])] = (f"round:{task_id}:{round_num + 1}",)

            # Execute tools and build tool_result blocks
            tool_results: list[dict[str, Any]] = []
            round_outcomes: list[bool | None] = []
            round_has_web_tool = False
            schedule_confirmation: str | None = None
            for tool_index, tool_block in enumerate(tool_use_blocks):
                tool_name = tool_block.get("name", "")
                tool_input = tool_block.get("input", {})
                tool_use_id = tool_block.get("id", "")
                tool_started_at = time.monotonic()
                execution_completed = False

                log_fn(f"[cc-backend] Executing tool: {tool_name}")

                # Redirect Bash+curl/wget to proper web tools when available
                effective_name = tool_name
                effective_input = tool_input
                if tool_name == "Bash":
                    redirected = _redirect_bash_web(
                        tool_input,
                        enabled=_bash_web_redirect_enabled(profile, base_url, capabilities=backend_capabilities, model=model),
                    )
                    if redirected is not None:
                        effective_name, effective_input = redirected
                        log_fn(f"[cc-backend] Redirected Bash -> {effective_name}")
                if effective_name in ("web_search", "web_extract"):
                    round_has_web_tool = True

                missing_fields = _missing_required_tool_fields(
                    anthropic_tools,
                    str(effective_name or ""),
                    effective_input if isinstance(effective_input, dict) else {},
                )

                if missing_fields:
                    result_text = json.dumps(
                        {
                            "error": (
                                f"Invalid tool input for {effective_name}: "
                                f"missing required field(s): {', '.join(missing_fields)}. "
                                "Call the tool again with all required fields."
                            ),
                            "tool_name": effective_name,
                            "missing_required_fields": missing_fields,
                        },
                        ensure_ascii=False,
                    )
                    log_fn(
                        f"[cc-backend] Invalid tool input ({effective_name}): "
                        f"missing {missing_fields}"
                    )
                elif effective_name.startswith("fal_") and effective_name in fal_tools_already_executed:
                    previous = last_fal_result.get(effective_name, "")
                    result_text = previous
                    execution_completed = True
                    log_fn(f"[cc-backend] Tool blocked (already executed): {effective_name}")
                elif _requires_approval(
                    str(effective_name or ""),
                    effective_input if isinstance(effective_input, dict) else {},
                    str(tool_use_id or ""),
                ):
                    session_budget.record_approval_required()
                    blocked_tool = _blocked_tool_payload(
                        str(effective_name or ""), effective_input, task_id=task_id, current_session=current_session
                    )
                    blocked_tool["tool_use_id"] = str(tool_use_id or "")
                    reasoning_content = "\n".join(
                        str(block.get("text") or "")
                        for block in content_blocks
                        if isinstance(block, dict)
                        and block.get("type") == "__reasoning"
                    ).strip()
                    if reasoning_content:
                        blocked_tool["reasoning_content"] = reasoning_content
                    assistant_text = _clean_preamble(text_parts)
                    if assistant_text:
                        blocked_tool["assistant_text"] = assistant_text
                        if not blocked_tool.get("reason"):
                            blocked_tool["reason"] = assistant_text
                    pending_tool_calls = [
                        {
                            "id": str(item.get("id") or ""),
                            "name": str(item.get("name") or ""),
                            "input": item.get("input", {})
                            if isinstance(item.get("input"), dict)
                            else {},
                        }
                        for item in tool_use_blocks[tool_index + 1 :]
                        if isinstance(item, dict)
                    ]
                    if pending_tool_calls:
                        blocked_tool["pending_tool_calls"] = pending_tool_calls
                    approval_context_messages = messages[1:-1]
                    if approval_context_messages:
                        blocked_tool["approval_context_messages"] = approval_context_messages
                    log_fn(
                        f"[cc-backend] Tool requires approval: {blocked_tool['tool']} {blocked_tool['target']}"
                    )
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.TOOL_APPROVAL_REQUIRED,
                            "round": round_num + 1,
                            "tool_use_id": tool_use_id,
                            **blocked_tool,
                        },
                    )
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.TASK_FINISH,
                            "status": approval_wait_status,
                            "reason": "approval_required",
                            "rounds": round_num + 1,
                            "tool_calls_count": tool_calls_count,
                            "approval_blocked_tools": [blocked_tool],
                        },
                    )
                    preamble = _clean_preamble(text_parts)
                    return {
                        "status": approval_wait_status,
                        "failure_reason": "approval_required",
                        "failure_details": (
                            f"tool={blocked_tool['tool']} target={blocked_tool['target']}"
                        ),
                        "output_text": preamble or "Для выполнения этого действия требуется подтверждение.",
                        "usage": _current_usage(),
                        "tool_calls_count": tool_calls_count,
                        "approval_blocked_tools": [blocked_tool],
                    }
                elif (
                    loop_limits.same_tool_batch_block_limit is not None
                    and session_budget.consecutive_same_tool_batch
                    >= loop_limits.same_tool_batch_block_limit
                ):
                    result_text = json.dumps(
                        {
                            "error": (
                                f"LOOP DETECTED. BLOCKED: You have requested this exact tool call "
                                f"{session_budget.consecutive_same_tool_batch} times in a row. "
                                "The inputs have not changed. Do not call this exact tool again. "
                                "STOP repeating the same call and respond with the information you already have."
                            ),
                            "tool_name": tool_name,
                            "already_requested": session_budget.consecutive_same_tool_batch,
                        },
                        ensure_ascii=False,
                    )
                else:
                    if effective_name not in valid_tool_names:
                        result_text = json.dumps(
                            {
                                "error": (
                                    f"Unknown tool '{effective_name}'. "
                                    f"Available: {sorted(valid_tool_names)}"
                                )
                            },
                            ensure_ascii=False,
                        )
                    elif _tool_denied_by_policy(
                        effective_name,
                        task_metadata,
                    ):
                        result_text = json.dumps(
                            {
                                "error": (
                                    f"Tool '{effective_name}' is not allowed for this "
                                    "task by policy. Continue with the allowed tools."
                                ),
                                "tool_name": effective_name,
                                "policy": "restricted",
                            },
                            ensure_ascii=False,
                        )
                    else:
                        tool_args = effective_input
                        if progress_callback:
                            try:
                                progress_callback(
                                    "tool.started",
                                    name=effective_name,
                                    preview=None,
                                    args=tool_args,
                                )
                            except Exception:
                                pass

                        try:
                            called_tool_names.add(effective_name)
                            write_path = _write_target_path(
                                effective_name, effective_input, workdir
                            )
                            if write_path:
                                called_write_paths.add(write_path)
                            result_text = execute_tool(
                                effective_name,
                                effective_input,
                                workdir,
                                filesystem_mode,
                                guard_mode=guard_mode,
                                checkpoint_manager=checkpoint_manager,
                                change_tracker=change_tracker,
                                current_session=current_session,
                                source_platform=source_platform,
                                source_chat_id=source_chat_id,
                                delegate_depth=delegate_depth,
                                iteration_budget=iteration_budget,
                                base_url=base_url,
                                model=model,
                                system_prompt=system_prompt,
                                max_tokens=max_tokens,
                                timeout_s=timeout_s,
                                task_id=task_id,
                                artifact_workdir=artifact_workdir,
                                tool_result_access=tool_result_access,
                                read_context_window_tokens=context_window_tokens,
                                read_max_chars=max(512, DEFAULT_TOOL_RESULT_STORE_CONFIG.turn_budget_chars // max(1, len(tool_use_blocks))),
                                tool_use_id=tool_use_id,
                                mcp_write_approval=consume_mcp_write_grant,
                                profile=profile,
                                delegate_metadata=metadata,
                                delegate_available_tools=anthropic_tools,
                                approvals_enabled_provider=approvals_enabled_provider,
                                checkpoint_config=checkpoint_settings,
                                data_root=data_root,
                                api_format=api_format,
                                request_headers=headers if is_responses_api else request_headers,
                                user_input_provider=user_input_provider,
                                web_tool_provider=web_tool_provider,
                                session_search_provider=session_search_provider,
                                vault_store_provider=vault_store_provider,
                    memory_forget_provider=memory_forget_provider,
                    **({"goal_control_provider": goal_control_provider} if goal_control_provider is not None else {}),
                    user_message=user_message,
                            )
                            execution_completed = True
                            if effective_name == "suggest_enable_mcp":
                                refresh_hot_reloaded_mcp_tools(str(result_text))
                            if effective_name == "update_plan" and result_text == "Plan updated":
                                append_cc_backend_trace(
                                    artifact_workdir,
                                    {
                                        "kind": CcTraceEvent.PLAN_UPDATED,
                                        "explanation": effective_input.get("explanation"),
                                        "plan": effective_input.get("plan", []),
                                    },
                                )
                            if effective_name.startswith("fal_"):
                                fal_tools_already_executed.add(effective_name)
                                last_fal_result[effective_name] = str(result_text)
                        except Exception as exc:
                            result_text = ToolResultText(f"Error executing {effective_name}: {exc}", False)
                            log_fn(f"[cc-backend] Tool error ({effective_name}): {exc}")

                        if progress_callback:
                            try:
                                progress_callback(
                                    "tool.completed",
                                    name=effective_name,
                                    preview=None,
                                    args=tool_args,
                                    result=result_text,
                                )
                            except Exception:
                                pass

                    if tool_name == "Bash" and effective_name != tool_name:
                        redirected_outcome = _result_indicates_success(result_text, tool_name=effective_name)
                        result_text = (
                            f"[Redirected: curl/wget -> {effective_name}. "
                            "Use the dedicated web tools directly instead of Bash curl.]\n\n"
                            f"{result_text}"
                        )
                        if redirected_outcome is not None:
                            result_text = ToolResultText(result_text, redirected_outcome)

                if not execution_completed:
                    result_text = ToolResultText(result_text, False)
                raw_result_text = result_text
                delegated_approval = _delegated_approval(effective_name, raw_result_text)
                if delegated_approval is not None:
                    return delegated_approval
                pending_mcp_approval = _pending_mcp_enable_approval(
                    raw_result_text, str(effective_name or tool_name or "")
                )
                if pending_mcp_approval is not None:
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.TASK_FINISH,
                            "status": "waiting_approval",
                            "reason": "mcp_enable_approval_requested",
                            "rounds": round_num + 1,
                            "tool_calls_count": tool_calls_count,
                            "approval": pending_mcp_approval,
                        },
                    )
                    return {
                        "status": "waiting_approval",
                        "output_text": "Ожидается подтверждение доступа к MCP.",
                        "usage": _current_usage(),
                        "tool_calls_count": tool_calls_count,
                        "approval_blocked_tools": [pending_mcp_approval],
                    }
                if _is_create_schedule_call(str(tool_name or "")):
                    schedule_confirmation = _schedule_created_message(raw_result_text)
                result_succeeded = _result_indicates_success(raw_result_text, tool_name=effective_name)
                completion_recovery.record(tool_use_id, effective_name, result_succeeded)
                round_outcomes.append(result_succeeded)
                result_text = maybe_persist_tool_result(
                    content=result_text,
                    tool_name=tool_name,
                    tool_use_id=tool_use_id,
                    workdir=artifact_workdir,
                    access=tool_result_access,
                    already_bounded=effective_name == "Read",
                )
                session_budget.record_tool_result(
                    raw_content=raw_result_text,
                    model_content=result_text,
                )
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.TOOL_RESULT,
                        "round": round_num + 1,
                        "tool_use_id": tool_use_id,
                        "tool_name": tool_name,
                        **({'browser_diagnostics':browser_tool_diagnostics(tool_input,raw_result_text)} if effective_name=='desktop_browser' else {}),
                        "tool_input": tool_input
                        if isinstance(tool_input, dict)
                        else {},
                        "succeeded": result_succeeded,
                        "raw_result_len": len(raw_result_text),
                        "raw_result_preview": raw_result_text[:500],
                        "result_for_model_len": len(result_text),
                        "result_for_model_preview": result_text[:500],
                        "context_output": result_text,
                        "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                    },
                )

                if effective_name.startswith("fal_"):
                    terminal_output = _terminal_fal_output(
                        effective_name, raw_result_text
                    )
                    if terminal_output:
                        terminal_output, add_input_tokens, add_output_tokens = (
                            _make_terminal_fal_response_call(
                                url=url,
                                model=model,
                                system_prompt=system_prompt,
                                headers=headers,
                                timeout_s=timeout_s,
                                tool_name=effective_name,
                                fallback_text=terminal_output,
                                workdir=artifact_workdir,
                                log_fn=log_fn,
                                is_openai_api=is_openai_api,
                            )
                        )
                        total_input_tokens += add_input_tokens
                        total_output_tokens += add_output_tokens
                        log_fn(
                            f"[cc-backend] Completed after terminal {effective_name} result"
                        )
                        saved_files = _extract_terminal_fal_saved_files(raw_result_text)
                        append_cc_backend_trace(
                            artifact_workdir,
                            {
                                "kind": CcTraceEvent.TASK_FINISH,
                                "status": "ok",
                                "reason": "terminal_fal_result",
                                "rounds": round_num + 1,
                                "tool_calls_count": tool_calls_count,
                                "tool_name": effective_name,
                                "saved_files": saved_files,
                                "output_preview": terminal_output[:500],
                            },
                        )
                        return {
                            "status": "ok",
                            "output_text": terminal_output,
                            "usage": _current_usage(),
                            "tool_calls_count": tool_calls_count,
                        }

                terminal_skill_output = _terminal_skill_manage_output(
                    str(effective_name or ""),
                    effective_input,
                    raw_result_text,
                )
                if terminal_skill_output:
                    log_fn(
                        f"[cc-backend] Completed after terminal {effective_name} delete result"
                    )
                    append_cc_backend_trace(
                        artifact_workdir,
                        {
                            "kind": CcTraceEvent.TASK_FINISH,
                            "status": "ok",
                            "reason": "terminal_skill_manage_delete",
                            "rounds": round_num + 1,
                            "tool_calls_count": tool_calls_count,
                            "tool_name": effective_name,
                            "tool_input": effective_input
                            if isinstance(effective_input, dict)
                            else {},
                            "output_preview": terminal_skill_output[:500],
                        },
                    )
                    return {
                        "status": "ok",
                        "output_text": terminal_skill_output,
                        "usage": _current_usage(),
                        "tool_calls_count": tool_calls_count,
                    }

                # Intercept skill_manage write_file path errors — redirect to Bash
                if (
                    tool_name == "skill_manage"
                    and isinstance(effective_input, dict)
                    and effective_input.get("action") == "write_file"
                    and "File must be under" in result_text
                ):
                    vault_path = os.environ.get("OBSIDIAN_VAULT_PATH", "")
                    fp = str(effective_input.get("file_path") or "")
                    fc = str(effective_input.get("file_content") or "")
                    if vault_path and fp:
                        target = os.path.join(vault_path, fp)
                        result_text = (
                            f"Ошибка: skill_manage работает только с файлами skills. "
                            f"Для создания заметки в Obsidian vault вызови Bash с командой:\n"
                            f"cat > \"{target}\" << 'ENDNOTE'\n{fc}\nENDNOTE"
                        )

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "tool_name": tool_name,
                        "bounded": effective_name == "Read",
                        **({"is_error": True} if result_succeeded is False else {}),
                        "content": result_text,
                    }
                )
                if effective_name == "desktop_browser" and isinstance(effective_input, dict) and effective_input.get("action") == "screenshot":
                    from .desktop_browser import take_browser_image
                    browser_image = take_browser_image(raw_result_text)
                    if browser_image is not None:
                        tool_results.append(browser_image)

            tool_results = enforce_turn_budget(tool_results, workdir=artifact_workdir, access=tool_result_access)

            if schedule_confirmation:
                log_fn("[cc-backend] Completed after create_schedule")
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.TASK_FINISH,
                        "status": "ok",
                        "reason": "schedule_created",
                        "rounds": round_num + 1,
                        "tool_calls_count": tool_calls_count,
                        "output_preview": schedule_confirmation[:500],
                    },
                )
                return {
                    "status": "ok",
                    "output_text": schedule_confirmation,
                    "usage": _current_usage(),
                    "tool_calls_count": tool_calls_count,
                }

            for item in tool_results:
                item.pop("tool_name", None)
                item.pop("bounded", None)

            if salvaged_from_text:
                tool_results.append(
                    {
                        "type": "text",
                        "text": (
                            "Предыдущий инструментальный шаг был выведен текстом вместо настоящего tool_use. "
                            "Сервер распознал и выполнил его сам. Дальше не показывай пользователю псевдовызовы "
                            "вида web_search(...): либо отвечай текстом, либо вызывай следующий инструмент "
                            "только структурным tool_use."
                        ),
                    }
                )

            round_productive = (
                True if any(value is True for value in round_outcomes)
                else None if any(value is None for value in round_outcomes)
                else False
            )
            # Unknown outcomes must not prompt a false failure recovery.
            # After a web-tool result, the model is directed to the next correct action.
            if round_has_web_tool and round_productive is not None:
                if round_productive:
                    tool_results.append(
                        {
                            "type": "text",
                            "text": (
                                "Результаты получены. "
                                "Если в результатах есть ответ на вопрос пользователя — сразу ответь текстом. "
                                "Если данных недостаточно — вызови другой инструмент (настоящий tool_use). "
                                "Не описывай свои намерения — либо отвечай, либо действуй."
                            ),
                        }
                    )
                else:
                    tool_results.append(
                        {
                            "type": "text",
                            "text": (
                                "Предыдущий инструмент не дал результата. "
                                "Сразу вызови другой подходящий инструмент (настоящий tool_use) — "
                                "например web_search вместо web_extract, или наоборот. "
                                "Либо ответь текстом на основе известных данных. "
                                "Не описывай свои намерения — действуй."
                            ),
                        }
                    )

            session_budget.record_round_productivity(round_productive)
            budget_decision = session_budget.evaluate_after_tool_round()

            if budget_decision.action == DECISION_FORCE_SYNTHESIS:
                reason = (
                    "exact_loop"
                    if budget_decision.reason == REASON_REPEATED_TOOL_LOOP
                    else "non_productive"
                )
                log_fn(
                    f"[cc-backend] Stuck detected ({reason}): "
                    f"same_batch={session_budget.consecutive_same_tool_batch}, "
                    f"has_useful={session_budget.has_useful_result}, "
                    f"non_productive={session_budget.consecutive_non_productive}"
                )
                append_cc_backend_trace(
                    artifact_workdir,
                    {
                        "kind": CcTraceEvent.BUDGET_DECISION,
                        "round": round_num + 1,
                        "action": budget_decision.action,
                        "reason": budget_decision.reason,
                        "snapshot": budget_decision.details,
                    },
                )
                # Add tool results so model sees them in synthesis call
                messages.append({"role": "user", "content": tool_results})
                return _make_synthesis_call(
                    url=url,
                    model=model,
                    system_prompt=system_prompt,
                    messages=messages,
                    headers=headers,
                    timeout_s=timeout_s,
                    max_tokens=max_tokens,
                    workdir=artifact_workdir,
                    log_fn=log_fn,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    start_time=start_time,
                    tool_calls_count=tool_calls_count,
                    is_openai_api=is_openai_api,
                )

            # Add tool results as user message
            messages.append({"role": "user", "content": tool_results})
            if priority_steer_provider is not None:
                _deliver_pending_steers(round_num + 1, priority_steer_provider)

        # Hit max rounds
        max_tool_rounds = loop_limits.max_tool_rounds or 0
        log_fn(f"[cc-backend] Hit max rounds ({max_tool_rounds})")
        append_cc_backend_trace(
            artifact_workdir,
            {
                "kind": CcTraceEvent.TASK_FINISH,
                "status": "fail",
                "reason": "max_rounds",
                "rounds": max_tool_rounds,
                "tool_calls_count": tool_calls_count,
                "session_budget": session_budget.snapshot(),
                "output_preview": "\n".join(text_parts)[:500] if text_parts else "",
            },
        )
        last_text_preview = "\n".join(text_parts)[:500] if text_parts else ""
        return {
            "status": "fail",
            "failure_reason": "max_tool_rounds_reached",
            "failure_details": (
                f"max_tool_rounds={max_tool_rounds}; "
                f"tool_calls_count={tool_calls_count}; "
                f"last_text_preview={last_text_preview}"
            ),
            "output_text": "\n".join(text_parts)
            if text_parts
            else "Task incomplete: max tool rounds reached.",
            "usage": _current_usage(),
            "tool_calls_count": tool_calls_count,
            "session_budget": session_budget.snapshot(),
        }

    finally:
        clear_runtime_context()


def _short_uid() -> str:
    import secrets
    return secrets.token_hex(6)


def _anthropic_image_block_to_openai(block: dict) -> dict[str, Any] | None:
    """Convert an Anthropic `image` content block to an OpenAI vision part.

    Anthropic ships images as
    `{"type": "image", "source": {"type": "base64"|"url", ...}}`.
    OpenAI chat.completions expects
    `{"type": "image_url", "image_url": {"url": ...}}`, where `url` is either a
    `data:<media_type>;base64,<payload>` URI or a plain http(s) URL.
    """
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    source_type = source.get("type")
    if source_type == "base64":
        data = source.get("data")
        if not data:
            return None
        media_type = str(source.get("media_type") or "image/png")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{data}"},
        }
    if source_type == "url":
        url = source.get("url")
        if not url:
            return None
        return {"type": "image_url", "image_url": {"url": str(url)}}
    return None


def _openai_vision_content(
    text_parts: list[str], image_parts: list[dict]
) -> list[dict[str, Any]]:
    """Build an OpenAI multi-part content list (text part + image_url parts).

    OpenAI vision models reject a plain string once images are attached, so
    content has to become a list of typed parts. The text part is omitted when
    there is no text at all, keeping an image-only message valid.
    """
    content: list[dict[str, Any]] = []
    joined_text = "\n".join(part for part in text_parts if part)
    if joined_text:
        content.append({"type": "text", "text": joined_text})
    content.extend(image_parts)
    return content


def _anthropic_messages_to_openai(messages: list[dict]) -> list[dict]:
    """Convert Anthropic-format messages to OpenAI Chat Completions format."""
    result: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            result.append({"role": role, "content": str(content)})
            continue

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        image_parts: list[dict] = []
        tool_calls: list[dict] = []

        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "__reasoning":
                reasoning_parts.append(str(block.get("text", "")))
            elif block_type == "text":
                text_parts.append(str(block.get("text", "")))
            elif block_type == "image":
                image_part = _anthropic_image_block_to_openai(block)
                if image_part is not None:
                    image_parts.append(image_part)
            elif block_type == "tool_use":
                args = block.get("input", {})
                args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)
                tid = block.get("id", f"call_{_short_uid()}")
                tool_calls.append({
                    "id": tid,
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": args_str,
                    },
                })
            elif block_type == "tool_result":
                tid = block.get("tool_use_id", "")
                tc = str(block.get("content", ""))
                result.append({"role": "tool", "tool_call_id": tid, "content": tc})

        if role == "assistant" and tool_calls:
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            if image_parts:
                assistant_msg["content"] = _openai_vision_content(text_parts, image_parts)
            elif text_parts:
                assistant_msg["content"] = "\n".join(text_parts)
            else:
                assistant_msg["content"] = None
            if reasoning_parts:
                assistant_msg["reasoning_content"] = "\n".join(reasoning_parts)
            assistant_msg["tool_calls"] = tool_calls
            result.append(assistant_msg)
        elif image_parts:
            entry: dict[str, Any] = {
                "role": role,
                "content": _openai_vision_content(text_parts, image_parts),
            }
            if reasoning_parts:
                entry["reasoning_content"] = "\n".join(reasoning_parts)
            result.append(entry)
        elif text_parts or reasoning_parts:
            entry: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
            if reasoning_parts:
                entry["reasoning_content"] = "\n".join(reasoning_parts)
            result.append(entry)

    return result


def _ensure_reasoning_passback(
    messages: list[dict],
    *,
    force: bool = False,
) -> tuple[list[dict], int]:
    """Fill `reasoning_content` for assistant messages carrying tool_calls.

    Thinking-mode upstreams, including DeepSeek through opencode zen, reject
    assistant messages with `tool_calls` but no `reasoning_content`:
    "The `reasoning_content` in the thinking mode must be passed back to the API".
    A model can legitimately omit thinking in one round; a real trace includes
    tool_calls without reasoning deltas, which otherwise violates that contract.

    By default, add the field only if the conversation already contains nonempty
    `reasoning_content`, establishing that this provider uses thinking mode.
    Non-thinking providers, such as local llama-server and ordinary OpenAI
    models, do not receive an unrelated field.

    `force=True` is used by the retry path after that explicit upstream rejection.
    Return (messages, added_field_count) without mutating the original list.
    """
    has_reasoning = any(
        str(msg.get("reasoning_content") or "").strip()
        for msg in messages
        if isinstance(msg, dict)
    )
    if not has_reasoning and not force:
        return messages, 0

    result: list[dict] = []
    filled = 0
    for msg in messages:
        if (
            isinstance(msg, dict)
            and msg.get("role") == "assistant"
            and msg.get("tool_calls")
            and "reasoning_content" not in msg
        ):
            patched = dict(msg)
            patched["reasoning_content"] = ""
            result.append(patched)
            filled += 1
        else:
            result.append(msg)
    if not filled:
        return messages, 0
    return result, filled


def _strip_internal_reasoning_blocks(messages: list[dict]) -> list[dict]:
    """Remove internal `__reasoning` blocks before sending to Anthropic /v1/messages.

    Reasoning is a private structural signal of cc_backend (used to render
    ReasoningCell in TUI and to feed OpenAI-style transports via
    `reasoning_content`). Anthropic Messages API does NOT understand
    `__reasoning` content type and rejects the request with HTTP 400
    ("unsupported content type '__reasoning'").

    This filter is applied only to the outgoing payload on Anthropic transport.
    OpenAI-style transports keep `__reasoning` because the converter maps it
    to `reasoning_content` (valid for chat.completions).

    Behavior:
    * If message content is a list — drop every block with type `__reasoning`.
    * If a list becomes empty after filtering — replace it with an empty string
      so the request stays well-formed.
    * String content and non-list content pass through unchanged.
    * Input messages are NOT mutated; deep-copied dicts are returned.
    """
    result: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            result.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue
        kept = [b for b in content if not (isinstance(b, dict) and b.get("type") in {"__reasoning", "__responses_output"})]
        if len(kept) == len(content):
            result.append(msg)
            continue
        new_msg = dict(msg)
        new_msg["content"] = kept if kept else ""
        result.append(new_msg)
    return result


def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-format tool schemas to OpenAI Chat Completions format."""
    result: list[dict] = []
    for tool in tools:
        name = tool.get("name", "")
        if not name:
            continue
        openai_tool: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": name,
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object"}),
            },
        }
        result.append(openai_tool)
    return result


def _openai_response_to_anthropic(
    body: dict, valid_tool_names: set[str]
) -> dict:
    """Convert OpenAI Chat Completions response to Anthropic Messages format."""
    if "content" in body:
        return body

    choices = body.get("choices", [])
    if not choices:
        return body

    choice = choices[0]
    msg = choice.get("message", {})
    finish = choice.get("finish_reason", "stop")

    content_blocks: list[dict] = []

    reasoning = msg.get("reasoning_content", "")
    if reasoning:
        content_blocks.append({"type": "__reasoning", "text": reasoning})

    text = msg.get("content")
    if text:
        content_blocks.append({"type": "text", "text": text})

    tool_calls = msg.get("tool_calls") or []
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        if name not in valid_tool_names:
            content_blocks.append({
                "type": "text",
                "text": f'Model requested unsupported tool "{name}". Available: {", ".join(sorted(valid_tool_names))}. Please use only available tools.',
            })
            continue
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"call_{len(content_blocks)}"),
            "name": name,
            "input": args,
        })

    stop_reason = "end_turn"
    if finish == "tool_calls":
        stop_reason = "tool_use"
    elif finish == "length":
        stop_reason = "max_tokens"

    usage = body.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    anthropic_usage = {}
    for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
        value = usage.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            anthropic_usage[target] = value

    return {
        "id": body.get("id", ""),
        "model": body.get("model", ""),
        "content": content_blocks,
        "stop_reason": stop_reason,
        "usage": anthropic_usage,
    }


def _http_post(
    url: str, payload: dict, headers: dict, timeout_s: int,
    use_curl: bool = False,
) -> tuple[dict, int]:
    """Send HTTP POST and return (response_body_dict, latency_ms)."""
    from .openai_responses import is_responses_url, post as responses_post
    from .diagnostics import transport_failure, safe_text
    is_google = bool(headers.get('__igorek_google_mode__'))
    if is_google:
        headers = {key: value for key, value in headers.items() if key != '__igorek_google_mode__'}
    google_names = set()
    if is_google:
        from .google_generate_content import build_request
        google_names = {str(tool.get('name') or tool.get('function', {}).get('name') or '') for tool in payload.get('tools', [])}
        try:
            payload = build_request(payload)
        except (ValueError, TypeError, KeyError) as exc:
            return {'error': f'google_request_invalid: {safe_text(exc)}'}, 0
    if is_responses_url(url):
        return responses_post(url, payload, headers, timeout_s)
    if use_curl:
        try:
            return _http_post_curl(url, payload, headers, timeout_s)
        except subprocess.TimeoutExpired:
            error = TimeoutError('HTTP transport deadline exceeded')
            return {'error': str(error), **transport_failure(error)}, 0
    start = time.time()
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if not isinstance(body, dict):
                raise json.JSONDecodeError('Expected response object', '', 0)
    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        lat = int((time.time() - start) * 1000)
        return {"error": f"HTTP {exc.code}: {safe_text(error_body)}", **transport_failure(status=exc.code, phase='http_body', request_id=exc.headers.get('x-request-id','') if exc.headers else '', retry_after=exc.headers.get('retry-after') if exc.headers else None)}, lat
    except http.client.RemoteDisconnected as exc:
        lat = int((time.time() - start) * 1000)
        return {"error": f"transport:remote_disconnected: {safe_text(exc)}", **transport_failure(exc)}, lat
    except Exception as exc:
        lat = int((time.time() - start) * 1000)
        return {"error": safe_text(exc), **transport_failure(exc)}, lat
    if is_google:
        from .google_generate_content import to_anthropic
        try:
            body = to_anthropic(body, google_names)
        except (ValueError, TypeError, KeyError) as exc:
            return {'error': f'google_response_invalid: {safe_text(exc)}'}, int((time.time() - start) * 1000)
    lat = int((time.time() - start) * 1000)
    return body, lat


def _http_post_streaming_openai(
    url: str,
    payload: dict,
    headers: dict,
    timeout_s: int,
    *,
    log_fn=None,
    trace_dir: str | None = None,
    round_num: int = 0,
    parent_task_id: str | None = None,
    cancel_check=None,
    backend_capabilities: ModelBackendCapabilities | None = None,
) -> tuple[dict, int]:
    """SSE streaming POST for OpenAI-compatible APIs.

    Yields text deltas as trace events. Returns the assembled full response
    in the same format as _http_post (OpenAI response dict).
    Uses http.client directly for per-read socket timeout control.
    """
    from .openai_responses import is_responses_url, post as responses_post
    from .diagnostics import transport_failure, safe_text, observation
    if is_responses_url(url):
        return responses_post(url, payload, headers, timeout_s, trace_dir=trace_dir,
            round_num=round_num, parent_task_id=parent_task_id, cancel_check=cancel_check, log_fn=log_fn)
    from urllib.parse import urlparse

    start = time.time()
    stream_options = dict(payload.get("stream_options") or {})
    stream_options.setdefault("include_usage", True)
    payload = {**payload, "stream": True, "stream_options": stream_options}
    data = json.dumps(payload).encode("utf-8")
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    conn = None
    try:
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=timeout_s)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
        conn.request("POST", path, body=data, headers=headers)
        resp = conn.getresponse()
        if resp.status >= 400:
            error_body = resp.read().decode("utf-8", errors="replace")[:500]
            lat = int((time.time() - start) * 1000)
            return {"error": f"HTTP {resp.status}: {safe_text(error_body)}", **transport_failure(status=resp.status, phase='http_body', request_id=resp.getheader('x-request-id',''), retry_after=resp.getheader('retry-after'))}, lat
        # Set per-read timeout so we don't hang forever on slow/stalled streams,
        # and keep a handle on the raw socket so cancellation can ``shutdown`` it
        # and unblock a blocked ``readline()``. NB: http.client stores it as
        # ``resp.fp`` (not ``_fp``).
        fp = getattr(resp, "fp", None)
        raw_sock = getattr(fp, "raw", None) if fp is not None else None
        io_socket = getattr(raw_sock, "_sock", raw_sock) if raw_sock is not None else None
        if io_socket is not None:
            try:
                io_socket.settimeout(timeout_s)
            except Exception:
                pass
    except http.client.RemoteDisconnected as exc:
        lat = int((time.time() - start) * 1000)
        if conn:
            try: conn.close()
            except Exception: pass
        return {"error": f"transport:remote_disconnected: {safe_text(exc)}", **transport_failure(exc)}, lat
    except Exception as exc:
        lat = int((time.time() - start) * 1000)
        if conn:
            try: conn.close()
            except Exception: pass
        return {"error": safe_text(exc), **transport_failure(exc)}, lat

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    content_splitter = TaggedReasoningSplitter()
    history_filter = HistoricalToolEchoFilter() if local_history_guard_enabled(
        url, capabilities=backend_capabilities, model=payload.get("model", "")
    ) else None

    def accept_content(parts: list[tuple[str, str]]) -> None:
        for kind, value in parts:
            (reasoning_parts if kind == "__reasoning" else content_parts).append(value)
            visible = history_filter.feed(value) if kind == "text" and history_filter else value
            if trace_dir and visible:
                append_cc_backend_trace(trace_dir, {
                    "kind": CcTraceEvent.MODEL_REASONING_DELTA if kind == "__reasoning" else CcTraceEvent.MODEL_TEXT_DELTA,
                    "round": round_num + 1, "text": visible,
                    "parent_task_id": parent_task_id or None,
                })

    tool_calls_map: dict[int, dict] = {}
    usage_info: dict = {}
    model_name = ""
    last_finish_reason = ""
    sse_event_count = 0
    got_done = False
    stream_failure: dict | None = None

    def _sse_data(lines: list[str]) -> str:
        values = []
        for line in lines:
            if line.startswith("data:"):
                value = line[5:]
                values.append(value[1:] if value.startswith(" ") else value)
        return "\n".join(values)

    def _invalid_sse_shape(field: str) -> None:
        nonlocal stream_failure
        stream_failure = {
            "error": f"Malformed response stream event at {field}.",
            "error_code": "PROTOCOL.INVALID_RESPONSE",
            "diagnostic": observation(
                "PROTOCOL.INVALID_RESPONSE", component="model", operation="model_round",
                endpoint_role="runtime_to_provider", phase="sse_event",
            ),
        }

    def _process_sse_event(event_data: str):
        """Process a single SSE data payload."""
        nonlocal model_name, usage_info, last_finish_reason, got_done, stream_failure
        if not event_data:
            return
        if event_data.strip() == "[DONE]":
            got_done = True
            return
        try:
            chunk = json.loads(event_data)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(chunk, dict):
            _invalid_sse_shape("event")
            return
        provider_error = chunk.get("error")
        if provider_error or isinstance(provider_error, dict):
            detail = provider_error.get("message") if isinstance(provider_error, dict) else provider_error
            code = (provider_error.get("code") or provider_error.get("type")) if isinstance(provider_error, dict) else None
            metadata = model_failure_diagnostics({
                "provider_error_code": code,
                "request_id": resp.getheader("x-request-id", ""),
            })
            stream_failure = {
                "error": safe_text(detail if isinstance(detail, str) and detail else "Provider returned an error event."),
                "error_code": "STREAM.INTERRUPTED",
                "diagnostic": observation(
                    "STREAM.INTERRUPTED", component="model", operation="model_round",
                    endpoint_role="runtime_to_provider", phase="sse_event",
                    provider_request_id=metadata.get("request_id", ""),
                ),
                **metadata,
            }
            return
        model_name = chunk.get("model", model_name)
        choices = chunk.get("choices")
        if choices is None:
            choices = []
        if not isinstance(choices, list):
            _invalid_sse_shape("choices")
            return
        if not choices:
            if chunk.get("usage"):
                usage_info = chunk["usage"]
            return
        choice = choices[0]
        if not isinstance(choice, dict):
            _invalid_sse_shape("choices[0]")
            return
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            _invalid_sse_shape("choices[0].finish_reason")
            return
        if finish_reason:
            last_finish_reason = finish_reason
        delta = choice.get("delta")
        if delta is None:
            delta = {}
        if not isinstance(delta, dict):
            _invalid_sse_shape("choices[0].delta")
            return
        for field in ("content", "reasoning_content"):
            if delta.get(field) is not None and not isinstance(delta[field], str):
                _invalid_sse_shape(f"delta.{field}")
                return
        # Reasoning delta (DeepSeek thinking mode)
        delta_reasoning = delta.get("reasoning_content")
        if delta_reasoning:
            reasoning_parts.append(delta_reasoning)
            if trace_dir:
                append_cc_backend_trace(
                    trace_dir,
                    {
                        "kind": CcTraceEvent.MODEL_REASONING_DELTA,
                        "round": round_num + 1,
                        "text": delta_reasoning,
                        "parent_task_id": parent_task_id or None,
                    },
                )
        # Classify tagged reasoning before emitting any visible text delta.
        delta_content = delta.get("content")
        if delta_content:
            accept_content(content_splitter.feed(delta_content))
        # Tool call deltas (accumulate)
        delta_tool_calls = delta.get("tool_calls")
        if delta_tool_calls is not None and not isinstance(delta_tool_calls, list):
            _invalid_sse_shape("delta.tool_calls")
            return
        if delta_tool_calls:
            for tc_delta in delta_tool_calls:
                if not isinstance(tc_delta, dict):
                    _invalid_sse_shape("delta.tool_calls[]")
                    return
                tc_index = tc_delta.get("index", 0)
                if isinstance(tc_index, bool) or not isinstance(tc_index, int) or tc_index < 0:
                    _invalid_sse_shape("delta.tool_calls[].index")
                    return
                if tc_delta.get("id") is not None and not isinstance(tc_delta["id"], str):
                    _invalid_sse_shape("delta.tool_calls[].id")
                    return
                fn = tc_delta.get("function")
                if fn is None:
                    fn = {}
                if not isinstance(fn, dict):
                    _invalid_sse_shape("delta.tool_calls[].function")
                    return
                for field in ("name", "arguments"):
                    if fn.get(field) is not None and not isinstance(fn[field], str):
                        _invalid_sse_shape(f"delta.tool_calls[].function.{field}")
                        return
                if tc_index not in tool_calls_map:
                    tool_calls_map[tc_index] = {
                        "id": tc_delta.get("id", ""),
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                tc = tool_calls_map[tc_index]
                if tc_delta.get("id"):
                    tc["id"] = tc_delta["id"]
                if fn.get("name"):
                    tc["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    tc["function"]["arguments"] += fn["arguments"]
        if chunk.get("usage"):
            usage_info = chunk["usage"]

    # Parse SSE stream — line-by-line (correct SSE parsing)
    import threading

    current_event_lines: list[str] = []
    interrupted = False
    monitor_stop = threading.Event()

    def _cancel_monitor() -> None:
        """Wake a blocked read as soon as cancellation is requested.

        The model may still be processing the prompt (no SSE chunks yet), so
        polling inside the read loop is not enough — the read would stay
        blocked. Shutting the socket down unblocks ``readline()`` immediately.
        """
        nonlocal interrupted
        while not monitor_stop.wait(0.3):
            if cancel_check is None:
                continue
            try:
                if not cancel_check():
                    continue
            except Exception:
                continue
            interrupted = True
            if log_fn:
                log_fn("[cc-backend] Interrupt requested, aborting stream.")
            if io_socket is not None:
                try:
                    io_socket.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass
            return

    monitor_thread = None
    if cancel_check is not None:
        monitor_thread = threading.Thread(target=_cancel_monitor, daemon=True)
        monitor_thread.start()

    last_cancel_check: float | None = None
    try:
        while True:
            # Fast path: check while chunks are flowing (monitor handles the
            # case where the read is blocked on prompt processing).
            if cancel_check is not None:
                now = time.time()
                if last_cancel_check is None or now - last_cancel_check >= 0.3:
                    last_cancel_check = now
                    try:
                        should_cancel = cancel_check()
                    except Exception:
                        should_cancel = False
                    if should_cancel:
                        interrupted = True
                        if log_fn:
                            log_fn("[cc-backend] Interrupt requested mid-stream, aborting.")
                        break
            try:
                raw_line = resp.readline()
            except (socket.timeout, TimeoutError):
                if log_fn:
                    log_fn("[cc-backend] streaming read timeout, finalizing")
                break
            except Exception:
                # Socket was shut down by the cancel monitor.
                if interrupted:
                    break
                raise
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                # Empty line = event boundary, process accumulated lines
                if current_event_lines:
                    event_data = _sse_data(current_event_lines)
                    if event_data and event_data.strip() == "[DONE]":
                        got_done = True
                    else:
                        _process_sse_event(event_data)
                    sse_event_count += 1
                    current_event_lines = []
                    if stream_failure is not None:
                        break
            else:
                current_event_lines.append(line)
    except Exception as error:
        if not interrupted:
            return {'error': 'Response stream interrupted before completion.', 'error_code': 'STREAM.INTERRUPTED',
                    'diagnostic': observation('STREAM.INTERRUPTED', component='model', operation='model_round', endpoint_role='runtime_to_provider', phase='sse_read')}, int((time.time()-start)*1000)
    finally:
        monitor_stop.set()
        try:
            conn.close()
        except Exception:
            pass

    if interrupted:
        return {"__interrupted__": True, "error": "interrupted"}, int(
            (time.time() - start) * 1000
        )

    if stream_failure is not None:
        return stream_failure, int((time.time() - start) * 1000)

    # Process any remaining lines (stream ended without blank line)
    if current_event_lines:
        event_data = _sse_data(current_event_lines)
        if event_data and event_data.strip() == "[DONE]":
            got_done = True
        else:
            _process_sse_event(event_data)
            sse_event_count += 1

    if stream_failure is not None:
        return stream_failure, int((time.time() - start) * 1000)

    accept_content(content_splitter.finish())
    if history_filter:
        visible = history_filter.finish()
        if trace_dir and visible:
            append_cc_backend_trace(trace_dir, {
                "kind": CcTraceEvent.MODEL_TEXT_DELTA,
                "round": round_num + 1, "text": visible,
                "parent_task_id": parent_task_id or None,
            })
    full_text = "".join(content_parts)
    if not got_done and not last_finish_reason:
        return {'error': 'Response stream ended without a terminal event; partial tool calls were not executed.',
                'error_code': 'STREAM.INTERRUPTED', 'diagnostic': observation('STREAM.INTERRUPTED', component='model', operation='model_round', endpoint_role='runtime_to_provider', phase='response_terminal')}, int((time.time()-start)*1000)
    if log_fn:
        log_fn(
            f"[cc-backend] stream done: events={sse_event_count} "
            f"chars={len(full_text)} finish_reason={last_finish_reason!r} "
            f"got_done={got_done} last50={('[local tool history suppressed]' if history_filter and history_filter.blocked else full_text[-50:])!r}"
        )

    # Assemble into OpenAI response format
    tool_calls_list = [tool_calls_map[i] for i in sorted(tool_calls_map.keys())]
    message: dict = {"role": "assistant", "content": full_text or None}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls_list:
        message["tool_calls"] = tool_calls_list
        message["content"] = message["content"] or ""

    openai_response = {
        "id": f"chatcmpl-stream-{round_num}",
        "object": "chat.completion",
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": last_finish_reason or ("stop" if not tool_calls_list else "tool_calls"),
            }
        ],
        "usage": usage_info or {},
    }

    lat = int((time.time() - start) * 1000)
    return openai_response, lat


def _http_post_curl(
    url: str, payload: dict, headers: dict, timeout_s: int
) -> tuple[dict, int]:
    """Fallback HTTP POST using curl (bypasses Cloudflare WAF)."""
    import tempfile
    start = time.time()
    data = json.dumps(payload).encode("utf-8")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.write(data)
    tmp.close()
    try:
        curl_cmd = ["curl", "-s", "-w", "\n%{http_code}", "-X", "POST", url,
                    "--max-time", str(timeout_s)]
        for k, v in headers.items():
            curl_cmd.extend(["-H", f"{k}: {v}"])
        curl_cmd.extend(["--data-binary", f"@{tmp.name}"])
        result = subprocess.run(
            curl_cmd, capture_output=True, text=True,
            timeout=timeout_s + 5,
            env={**os.environ, "LC_ALL": "C"},
        )
        raw = result.stdout or ""
        parts = raw.rsplit("\n", 1)
        resp_body = parts[0] if len(parts) > 1 else raw
        try:
            status = int(parts[-1].strip())
        except Exception:
            status = 0
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    try:
        body = json.loads(resp_body)
    except json.JSONDecodeError:
        body = {"error": f"HTTP {status}: {resp_body[:500]}"}
    from .diagnostics import safe_text, transport_failure, observation
    if status >= 400:
        message = body.get('error', '') if isinstance(body, dict) else ''
        body = {'error': f'HTTP {status}: {safe_text(message)}', **transport_failure(status=status, phase='http_body')}
    elif getattr(result, 'returncode', 0):
        codes = {6:'NET.DNS_FAILURE',7:'NET.CONNECTION_REFUSED',28:'NET.READ_TIMEOUT',35:'TLS.PROTOCOL_FAILURE',60:'TLS.CERTIFICATE_REJECTED'}
        code = codes.get(result.returncode,'NET.TRANSPORT_FAILURE')
        body = {'error': 'HTTP transport failed.', 'error_code':code, 'diagnostic':observation(code,component='model',operation='model_round',endpoint_role='runtime_to_provider')}
    elif not isinstance(body, dict) or status < 200 or ('error' in body and status < 400):
        body = {'error': 'Unexpected provider response.', 'error_code':'PROTOCOL.INVALID_RESPONSE'}
    lat = int((time.time() - start) * 1000)
    return body, lat


def _http_get_curl(
    url: str, timeout_s: int = 5
) -> tuple[dict, int]:
    """HTTP GET using curl (bypasses Cloudflare WAF)."""
    start = time.time()
    curl_cmd = ["curl", "-s", "-w", "\n%{http_code}", "-X", "GET", url,
                "--max-time", str(timeout_s),
                "-H", "Accept: application/json",
                "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) OpenCode/1.0"]
    try:
        result = subprocess.run(
            curl_cmd, capture_output=True, text=True,
            timeout=timeout_s + 3,
            env={**os.environ, "LC_ALL": "C"},
        )
        raw = result.stdout or ""
        parts = raw.rsplit("\n", 1)
        resp_body = parts[0] if len(parts) > 1 else raw
        try:
            status = int(parts[-1].strip())
        except Exception:
            status = 0
    except Exception:
        raise

    try:
        body = json.loads(resp_body)
    except json.JSONDecodeError:
        body = {"error": f"HTTP {status}: {resp_body[:200]}"}
    lat = int((time.time() - start) * 1000)
    return body, lat


def _usage(
    input_tokens: int,
    output_tokens: int,
    start_time: float,
    *,
    model_context_window: int | None = None,
    current_context_tokens: int | None = None,
) -> dict[str, Any]:
    usage = {
        "prompt": input_tokens,
        "completion": output_tokens,
        "lat_ms_p50": int((time.time() - start_time) * 1000),
    }
    if model_context_window:
        usage["model_context_window"] = model_context_window
    if current_context_tokens:
        usage["total_tokens"] = current_context_tokens
        usage["current_usage"] = current_context_tokens
    return usage


def _review_local_completion(*, url, model, system_prompt, messages, receipts, tools,
                             headers, timeout_s, max_tokens, context_window_tokens,
                             is_openai_api, reasoning_format, cancel_check):
    """Ask the same confirmed local provider for control data, without tool authority."""
    from .prompt_assembly import completion_review_prompt

    if cancel_check():
        return None, "interrupted", 0, 0
    context = {
        "execution_policy": system_prompt,
        "conversation": review_transcript(messages),
        "current_tool_receipts": receipts,
        "available_tools": [{"name": tool["name"], "description": tool.get("description", "")}
                            for tool in tools],
    }
    review_messages = [{"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    prompt = completion_review_prompt()
    if estimate_context_tokens(system_prompt=prompt, messages=review_messages, tools=[]) > context_window_tokens - max_tokens:
        return None, "completion_review_context_exceeded", 0, 0
    payload = {"model": model, "max_tokens": max_tokens, "system": prompt, "messages": review_messages}
    if is_openai_api:
        payload.pop("system")
        payload["messages"] = [{"role": "system", "content": prompt}] + review_messages
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "completion_review", "strict": True, "schema": COMPLETION_REVIEW_SCHEMA,
        }}
        if reasoning_format is not None:
            payload["reasoning_format"] = reasoning_format
        body, _ = _http_post_streaming_openai(
            url, payload, headers, timeout_s, cancel_check=cancel_check,
        )
        if isinstance(body, dict) and not body.get("error") and not body.get("__interrupted__"):
            body = _openai_response_to_anthropic(body, set())
    else:
        body, _ = _http_post(url, payload, headers, timeout_s)
    if not isinstance(body, dict):
        return None, "completion_review_invalid", 0, 0
    usage = body.get("usage") or {}
    add_in, add_out = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    if body.get("__interrupted__") or cancel_check():
        return None, "interrupted", add_in, add_out
    if body.get("error") or _model_output_limited(body):
        return None, "completion_review_failed", add_in, add_out
    blocks = _normalize_tagged_reasoning_blocks(body.get("content", []))
    if any(block.get("type") == "tool_use" for block in blocks):
        return None, "completion_review_invalid", add_in, add_out
    text = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
    try:
        review = parse_completion_review(text, receipts)
    except ValueError:
        return None, "completion_review_invalid", add_in, add_out
    return review, "", add_in, add_out
