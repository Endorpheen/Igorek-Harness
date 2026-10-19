import argparse
import json
import os
import re
import shlex
import traceback
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import uvicorn
import torch
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from factory.api_server_core.api_payload_formatters import (
    format_compact_api_payload,
    normalize_bash_payload_text,
)
from factory.api_server_core.context_compactor import ContextCompressor, ContextCompactorConfig
from factory.api_server_core.context_compression_client import (
    ContextCompressionClient,
    build_context_compression_client_config,
)
from factory.api_server_core.file_policy import (
    build_policy_denial_response as build_policy_denial_response_text,
    extract_explicit_absolute_path,
    extract_project_root,
    file_resolution_source as file_resolution_source_name,
    resolution_base_dir,
    resolve_local_path_if_valid,
    sanitize_local_path_candidate,
    temporary_allowed_root_for_path,
    validate_existing_file_policy,
)
from factory.api_server_core.anthropic_messages import (
    build_tool_result_summary,
    convert_anthropic_to_oai_body,
    detect_prompt_mode,
    extract_anthropic_messages,
    extract_embedded_suggestion_draft,
    extract_latest_user_request_text,
    extract_last_user_text,
    extract_last_user_visible_text_block,
    extract_latest_tool_exchange,
    extract_tool_targets,
    extract_visible_message_content,
    flatten_block_content,
    has_embedded_tool_result_marker,
    is_suggestion_mode_request,
    is_tool_result_only_user_turn,
    last_user_message,
    strip_system_reminder,
)
from factory.api_server_core.output_utils import (
    extract_visible_delta,
    infer_stop_reason,
    sanitize_output,
    sanitize_output_for_mode as sanitize_output_for_mode_text,
    sanitize_tool_preamble,
    split_stop_sequence,
)
from factory.api_server_core.prompt_rules import failure_recovery_prompt_rules
from factory.api_server_core.prompt_builder import (
    build_system_prompt,
    build_tools_prompt as build_request_tools_prompt,
)
from factory.api_server_core.prompts import (
    failure_recovery_user_prompt,
    file_content_system_prompt,
    file_content_user_prompt,
    file_investigation_summarize_user_prompt,
    LANGUAGE_HINTS,
    preamble_ctx_default,
    preamble_ctx_direct_write,
    preamble_ctx_first_read,
    preamble_ctx_read_after_write,
    preamble_ctx_read_continuation,
    preamble_ctx_read_step,
    preamble_ctx_write_after_read,
    preamble_ctx_write_step,
    preamble_system_prompt,
    preamble_user_prompt,
    project_analysis_scan_user_prompt,
    project_analysis_summarize_user_prompt,
    session_recall_summarize_user_prompt,
    tavily_research_summarize_user_prompt,
    tavily_retry_success_user_prompt,
    tool_input_edit_example,
    tool_input_edit_intro,
    tool_input_general_intro,
    tool_input_retry_note,
    tool_input_schema_rules,
    tool_input_write_example,
    tool_input_write_rules,
)
from factory.api_server_core.response_utils import (
    anthropic_message_base,
    anthropic_message_delta_event,
    anthropic_message_start_event,
    anthropic_text_message_response,
    anthropic_tool_message_response,
    count_prompt_tokens,
    count_text_tokens,
    estimate_tool_use_output_tokens,
    sse_event,
    summarize_response_payload,
)
from factory.api_server_core.text_utils import (
    extract_bash_file_target,
    extract_code_block,
    extract_creation_content_from_user_text,
    extract_decorated_entrypoints,
    extract_main_entrypoints,
    extract_read_imports,
    extract_read_sections,
    extract_top_level_constants,
    find_explicit_filename,
    sample_section_names,
)
from factory.api_server_core.tool_parsing import (
    coerce_tool_plan_payload,
    extract_json_objects,
    extract_named_json_object,
    normalize_tool_input,
    parse_json_object,
    parse_textual_tool_use,
    parse_tool_plan,
    salvage_tool_plan,
    tool_use_block_count,
    validate_tool_input,
    validated_named_tool_use,
)
from factory.api_server_core.tool_history import (
    ToolExchange,
    completed_edit_pairs,
    extract_last_shell_cwd,
    extract_last_successful_read_file,
    extract_last_tool_file,
    extract_latest_successful_read_text,
    extract_tool_exchanges,
    is_duplicate_of_latest_successful_tool_turn,
    latest_success_index,
    tool_action_signature as build_tool_action_signature,
    tool_target_from_input,
)
from factory.api_server_core.trace_utils import (
    get_request_id,
    log_anthropic_request as write_anthropic_request_log,
    trace_event as write_trace_event,
    truncate_for_log,
)
from factory.api_server_core.tavily import (
    build_tavily_result_summaries,
    build_tavily_search_command,
    extract_authorization_bearer,
    extract_curl_json_payload,
    extract_successful_tavily_payload,
    extract_tavily_bearer_token,
    extract_tavily_query,
    extract_tavily_result_urls,
    extract_urls_from_text,
    is_tavily_search_command,
    is_url_fetch_bash_command,
    json_payload_has_body_validation_error,
    json_payload_looks_like_error,
    parse_json_payload,
    summarize_fetched_web_source,
    tavily_retry_bash_command_for_error,
)
from factory.api_server_core.workflow_engine import (
    PROJECT_ANALYSIS_MAX_SCAN_STEPS,
    classify_workflow_tool,
    failure_recovery_exchanges,
    failure_recovery_state,
    file_investigation_continuation_read,
    file_investigation_forced_read_tool_use,
    file_investigation_has_enough_context,
    file_investigation_related_files,
    file_investigation_state,
    project_analysis_forced_scan_tool_use,
    project_analysis_has_enough_context,
    project_analysis_state,
    session_recall_state,
    tavily_research_forced_tool_use,
    tavily_research_state,
    workflow_allows_tool,
    workflow_completion_policy,
    workflow_config,
    workflow_followup_requirements,
    workflow_state_config,
)
from factory.api_server_core.request_state import (
    apply_workflow_state as apply_request_workflow_state,
    build_turn_state as build_request_turn_state,
    maybe_attach_project_analysis_workflow as maybe_attach_project_analysis_workflow_state,
    maybe_start_initial_workflow as maybe_start_initial_workflow_state,
    route_user_intent as route_user_intent_state,
)
from factory.api_server_core.schemas import (
    AnthropicMessage,
    AnthropicMessagesRequest,
    AnthropicResponseState,
    AnthropicTextBlock,
    AnthropicTurnState,
    ChatCompletionRequest,
    ChatMessage,
    CountTokensRequest,
    NormalizedAnthropicRequest,
    PromptMode,
    ToolTurnDecision,
    UserIntent,
)
from factory.api_server_core.user_constraints import ExplicitRuntimeConstraints
from factory.api_server_core.schemas import (
    AnthropicMessage,
    AnthropicMessagesRequest,
    AnthropicResponseState,
    AnthropicTextBlock,
    ChatCompletionRequest,
    ChatMessage,
    CountTokensRequest,
    NormalizedAnthropicRequest,
    PromptMode,
    ToolTurnDecision,
    UserIntent,
)
from factory.api_server_core.tool_planner_runtime import (
    build_tool_planner_prompt as build_runtime_tool_planner_prompt,
    maybe_model_tool_use as runtime_maybe_model_tool_use,
)
from factory.api_server_core.anthropic_runtime import (
    anthropic_empty_text_stream_response as runtime_empty_text_stream_response,
    anthropic_text_stream_response as runtime_text_stream_response,
    anthropic_text_stream_response_from_text as runtime_text_stream_response_from_text,
    anthropic_tool_stream_response as runtime_tool_stream_response,
    build_generation_params as build_runtime_generation_params,
    build_text_response_from_result as runtime_build_text_response_from_result,
    request_stop_sequences as runtime_request_stop_sequences,
    split_request_stop_sequence as runtime_split_request_stop_sequence,
)
from factory.api_server_core.generator_runtime import create_runtime_generator, resolve_runtime_backend
from paroquant.inference import GenerationParams, build_prompt


MODEL_PATH: str | None = None
MODEL_NAME: str | None = None
GENERATOR = None
CONTEXT_COMPRESSOR: ContextCompressor | None = None
SERVER_READY = False
DEBUG_TRACE = False
MODEL_BACKEND = "auto"
LLAMA_SERVER_BIN = "llama-server"
LLAMA_SERVER_HOST = "127.0.0.1"
LLAMA_SERVER_PORT = 8011
LLAMA_CTX_SIZE = 32768
LLAMA_GPU_LAYERS = 999
LLAMA_SERVER_EXTRA_ARGS: list[str] = []
LLAMA_AUTODETECT_MMPROJ = True
LLAMA_SERVER_COMMAND: list[str] = []
LOG_DIR = Path(os.environ.get("IGOREK_API_LOG_DIR", str(Path.home() / ".igorek-agent-factory/logs")))
ANTHROPIC_LOG = LOG_DIR / "anthropic_messages.jsonl"
TRACE_LOG = LOG_DIR / "anthropic_trace.jsonl"
PUBLIC_ENDPOINTS = {
    "/health",
    "/v1/health",
    "/v1/models",
}

QUIET_LOG_PATHS = PUBLIC_ENDPOINTS | {
    "/metrics",
}


def _should_short_circuit_tool_result(state: AnthropicTurnState) -> tuple[bool, str]:
    if state.normalized.mode != PromptMode.TOOL_RESULT:
        return False, ""

    _user_text, tool_name, tool_result, _tool_input_obj = extract_latest_tool_exchange(
        state.req
    )
    if tool_name != "Write":
        return False, ""

    tool_use_count = 0
    for msg in state.req.messages:
        if msg.role != "assistant" or isinstance(msg.content, str):
            continue
        for block in msg.content:
            if block.get("type") == "tool_use":
                tool_use_count += 1

    if tool_use_count != 1:
        return False, ""

    if not tool_result.startswith("File created successfully at:"):
        return False, ""

    return True, ""


async def _maybe_short_circuit_execute_command_tool_result(
    request: Request | None,
    state: AnthropicTurnState,
) -> tuple[bool, str]:
    if state.normalized.mode != PromptMode.TOOL_RESULT:
        return False, ""
    if state.current_workflow not in {None, "failure_recovery"}:
        return False, ""

    _user_text, tool_name, tool_result, tool_input_obj = extract_latest_tool_exchange(
        state.req
    )
    if tool_name != "Bash":
        return False, ""

    command = str(tool_input_obj.get("command", "")).strip()

    raw_tool_result = strip_system_reminder(tool_result or "").strip()
    tool_result_is_error = _looks_like_tool_error_payload(raw_tool_result)
    rendered = normalize_bash_payload_text(
        _strip_bash_no_output_marker(tool_result or "")
    )
    if rendered.startswith("[tool_result"):
        rendered = "\n".join(rendered.splitlines()[1:]).strip()
    if tool_result_is_error or _looks_like_tool_error_payload(rendered):
        if not rendered:
            return (
                True,
                f"Команда `{command or 'Bash'}` завершилась с ошибкой без вывода.",
            )
        return (
            True,
            f"Команда `{command or 'Bash'}` завершилась с ошибкой:\n\n```text\n{rendered}\n```",
        )
    payload = parse_json_payload(rendered)
    if isinstance(payload, dict):
        compact_payload_lines = format_compact_api_payload(payload)
        if compact_payload_lines:
            return True, "\n".join(compact_payload_lines)
        symbol = payload.get("symbol")
        price = payload.get("price")
        if isinstance(symbol, str) and isinstance(price, str):
            return True, f"`{symbol}`: `{price}`"
        pretty = json.dumps(payload, ensure_ascii=False, indent=2)
        return True, f"Результат `{command or 'Bash'}`:\n\n```json\n{pretty}\n```"
    if isinstance(payload, list):
        pretty = json.dumps(payload, ensure_ascii=False, indent=2)
        return True, f"Результат `{command or 'Bash'}`:\n\n```json\n{pretty}\n```"
    if not rendered:
        return True, f"Команда `{command or 'Bash'}` не вывела ничего."
    return True, f"Результат `{command or 'Bash'}`:\n\n```text\n{rendered}\n```"


def _extract_tools(req: AnthropicMessagesRequest) -> list[dict[str, Any]]:
    extra = req.model_extra or {}
    tools = extra.get("tools")
    if isinstance(tools, list):
        return [tool for tool in tools if isinstance(tool, dict)]
    return []


def _build_tools_prompt(req: AnthropicMessagesRequest) -> str:
    return build_request_tools_prompt(_extract_tools(req))


def _tool_schema_excerpt(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        return {}
    excerpt: dict[str, Any] = {}
    properties = schema.get("properties")
    required = schema.get("required")
    if isinstance(properties, dict):
        excerpt["properties"] = {
            key: {
                "type": value.get("type"),
                "description": value.get("description", ""),
                "enum": value.get("enum"),
            }
            for key, value in list(properties.items())[:12]
            if isinstance(value, dict)
        }
    if isinstance(required, list):
        excerpt["required"] = required
    return excerpt


def _tool_lookup(req: AnthropicMessagesRequest) -> dict[str, dict[str, Any]]:
    return {str(tool.get("name", "")): tool for tool in _extract_tools(req)}


def _build_turn_state(
    req: AnthropicMessagesRequest,
    normalized: NormalizedAnthropicRequest,
) -> AnthropicTurnState:
    return build_request_turn_state(
        req,
        normalized,
        turn_state_cls=AnthropicTurnState,
        extract_tools_fn=_extract_tools,
        extract_last_user_text_fn=extract_last_user_text,
        extract_latest_user_request_text_fn=extract_latest_user_request_text,
        find_explicit_filename_fn=find_explicit_filename,
        extract_last_tool_file_fn=_extract_last_tool_file,
        extract_last_successful_read_file_fn=_extract_last_successful_read_file,
        extract_last_shell_cwd_fn=_extract_last_shell_cwd,
        extract_recent_dialogue_fn=_extract_recent_dialogue,
        strip_system_reminder_fn=strip_system_reminder,
        read_result_looks_successful_fn=_read_result_looks_successful,
        file_investigation_related_files_fn=_file_investigation_related_files,
        continued_read_input_fn=_continued_read_input,
    )


def _apply_workflow_state(
    state: AnthropicTurnState,
    workflow_state: tuple[
        str | None,
        str | None,
        int,
        list[str],
        list[str],
        list[str],
        list[str],
        list[str],
        list[str],
        list[str],
        list[str],
        list[str],
        str | None,
    ],
) -> None:
    apply_request_workflow_state(state, workflow_state)


async def _route_user_intent(request: Request | None, user_text: str) -> str:
    return await route_user_intent_state(
        request,
        user_text,
        generator=GENERATOR,
        prompt_mode_cls=PromptMode,
        user_intent_cls=UserIntent,
        parse_json_object_fn=parse_json_object,
        trace_event_fn=_trace_event,
    )


async def _maybe_attach_project_analysis_workflow(
    request: Request,
    state: AnthropicTurnState,
) -> None:
    await maybe_attach_project_analysis_workflow_state(
        request,
        state,
        route_user_intent_fn=_route_user_intent,
        extract_latest_user_request_text_fn=extract_latest_user_request_text,
        trace_event_fn=_trace_event,
    )


def _initial_project_analysis_workflow_state() -> tuple[
    str | None,
    str | None,
    int,
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    str | None,
]:
    return (
        "project_analysis",
        "scan_root",
        1,
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        None,
    )


def _initial_file_investigation_workflow_state() -> tuple[
    str | None,
    str | None,
    int,
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    str | None,
]:
    return (
        "file_investigation",
        "read_primary",
        1,
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        None,
    )


def _initial_tavily_research_workflow_state() -> tuple[
    str | None,
    str | None,
    int,
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    str | None,
]:
    return (
        "tavily_research",
        "search",
        1,
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        None,
    )


def _initial_local_implementation_workflow_state() -> tuple[
    str | None,
    str | None,
    int,
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    str | None,
]:
    return (
        "local_implementation",
        "continue",
        1,
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        None,
    )


async def _maybe_start_initial_workflow(
    request: Request,
    state: AnthropicTurnState,
) -> None:
    await maybe_start_initial_workflow_state(
        request,
        state,
        route_user_intent_fn=_route_user_intent,
        trace_event_fn=_trace_event,
    )


def _extract_recent_dialogue(
    req: AnthropicMessagesRequest,
    limit: int | None = None,
    max_chars: int = 24000,
) -> str:
    source_messages = req.messages if limit is None else req.messages[-limit:]
    dialogue: list[str] = []
    for msg in source_messages:
        content = _flatten_block_content(msg.content, role=msg.role)
        if not content:
            continue
        content = content.strip()
        if not content:
            continue
        dialogue.append(f"{msg.role.upper()}:\n{content}")

    if not dialogue:
        return ""

    joined = "\n\n".join(dialogue)
    if len(joined) <= max_chars:
        return joined
    return joined[-max_chars:]


def _initial_read_input(file_path: str) -> dict[str, Any]:
    return {
        "file_path": file_path,
        "offset": 1,
        "limit": 400,
    }


def _read_result_max_line_number(result_text: str) -> int | None:
    max_line: int | None = None
    for line in result_text.splitlines():
        match = re.match(r"^\s*(\d+)(?:→|\t)", line)
        if not match:
            continue
        number = int(match.group(1))
        if max_line is None or number > max_line:
            max_line = number
    return max_line


def _read_result_min_line_number(result_text: str) -> int | None:
    min_line: int | None = None
    for line in result_text.splitlines():
        match = re.match(r"^\s*(\d+)(?:→|\t)", line)
        if not match:
            continue
        number = int(match.group(1))
        if min_line is None or number < min_line:
            min_line = number
    return min_line


def _continued_read_input(exchange: ToolExchange) -> dict[str, Any] | None:
    exchange_is_error = exchange.is_error and not _read_result_looks_successful(
        exchange.result_text
    )
    if exchange.tool_name != "Read" or exchange_is_error or not exchange.target:
        return None
    raw_limit = exchange.tool_input_obj.get("limit")
    raw_offset = exchange.tool_input_obj.get("offset", 1)
    if not isinstance(raw_limit, int) or raw_limit <= 0:
        return None
    if not isinstance(raw_offset, int) or raw_offset <= 0:
        raw_offset = 1
    max_line = _read_result_max_line_number(exchange.result_text)
    if max_line is None:
        return None
    expected_last_line = raw_offset + raw_limit - 1
    if max_line < expected_last_line:
        return None
    return {
        "file_path": exchange.target,
        "offset": max_line + 1,
        "limit": raw_limit,
    }


def _extract_successful_tavily_payload(
    command: str, result_text: str
) -> dict[str, Any] | None:
    return extract_successful_tavily_payload(
        command, result_text, strip_system_reminder
    )


def _is_successful_tavily_summary_turn(state: AnthropicTurnState) -> bool:
    if (
        state.normalized.mode != PromptMode.TOOL_RESULT
        or state.current_workflow is not None
    ):
        return False
    _user_text, tool_name, tool_result, tool_input_obj = extract_latest_tool_exchange(
        state.req
    )
    if tool_name != "Bash":
        return False
    command = str(tool_input_obj.get("command", "")).strip()
    return _extract_successful_tavily_payload(command, tool_result) is not None


def _strip_bash_no_output_marker(text: str) -> str:
    stripped = strip_system_reminder(text or "").strip()
    if stripped == "(Bash completed with no output)":
        return ""
    return stripped


def _looks_like_tool_error_payload(text: str) -> bool:
    stripped = strip_system_reminder(text or "").strip()
    if not stripped:
        return False
    if stripped == "(Bash completed with no output)":
        return False
    if "<tool_use_error>" in stripped:
        return True
    if re.search(r"^\[tool_result[^\n]*\berror=true\b", stripped, flags=re.MULTILINE):
        return True
    if re.search(r"(^|\n)Exit code\s+[1-9]\d*", stripped):
        return True
    payload = parse_json_payload(stripped)
    if json_payload_looks_like_error(payload):
        return True
    return False


def _read_result_looks_successful(text: str) -> bool:
    return bool(re.search(r"^\s*\d+(?:→|\t)", text, flags=re.MULTILINE))


def _should_ignore_failure_recovery_exchange(exchange: ToolExchange) -> bool:
    cleaned = strip_system_reminder(exchange.result_text or "").strip()
    if not cleaned:
        return True
    if "No tools needed for suggestion" in cleaned:
        return True
    if exchange.tool_name == "skill_manage":
        recoverable_markers = (
            "SKILL.md must start with YAML frontmatter",
            "SKILL.md frontmatter is not closed",
            "Frontmatter must include",
            "YAML frontmatter parse error",
            "content is required for create",
            "content is required for edit",
            "old_string is required for patch",
            "new_string is required for patch",
        )
        if any(marker in cleaned for marker in recoverable_markers):
            return True
    return False


def _extract_tool_exchanges(req: AnthropicMessagesRequest) -> list[ToolExchange]:
    return extract_tool_exchanges(
        req,
        flatten_block_content=_flatten_block_content,
        looks_like_tool_error_payload=_looks_like_tool_error_payload,
        extract_bash_file_target=extract_bash_file_target,
    )


def _messages_since_latest_user(
    req: AnthropicMessagesRequest,
) -> list[AnthropicMessage]:
    latest_user_index: int | None = None
    for index in range(len(req.messages) - 1, -1, -1):
        if req.messages[index].role == "user":
            latest_user_index = index
            break
    if latest_user_index is None:
        return list(req.messages)
    return list(req.messages[latest_user_index:])


def _extract_recent_tool_exchanges(req: AnthropicMessagesRequest) -> list[ToolExchange]:
    recent_messages = _messages_since_latest_user(req)
    if len(recent_messages) == len(req.messages):
        return _extract_tool_exchanges(req)
    recent_req = req.model_copy(update={"messages": recent_messages})
    return _extract_tool_exchanges(recent_req)


def _workflow_has_completed_assistant_text(
    req: AnthropicMessagesRequest, workflow_name: str
) -> bool:
    last_relevant_assistant_index: int | None = None
    for index, msg in enumerate(req.messages):
        if msg.role != "assistant" or isinstance(msg.content, str):
            continue
        has_relevant_tool_use = False
        for block in msg.content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_name = str(block.get("name", "")).strip()
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                tool_input = {}
            if workflow_name == "project_analysis" and tool_name == "Bash":
                command = str(tool_input.get("command", "")).strip()
                if _is_directory_scan_bash_command(command):
                    has_relevant_tool_use = True
                    break
            if workflow_name == "file_investigation" and tool_name == "Read":
                has_relevant_tool_use = True
                break
            if workflow_name == "failure_recovery" and tool_name in {
                "Read",
                "Bash",
                "Edit",
                "Write",
                "Update",
                "MultiEdit",
            }:
                has_relevant_tool_use = True
                break
        if has_relevant_tool_use:
            last_relevant_assistant_index = index

    if last_relevant_assistant_index is None:
        return False

    for msg in req.messages[last_relevant_assistant_index + 1 :]:
        if msg.role != "assistant":
            continue
        if isinstance(msg.content, str):
            if strip_system_reminder(msg.content).strip():
                return True
            continue
        has_tool_use = any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in msg.content
        )
        if has_tool_use:
            continue
        visible_text = _flatten_block_content(msg.content, role=msg.role).strip()
        if visible_text:
            return True
    return False


def _tool_target_from_input(tool_name: str, tool_input: dict[str, Any]) -> str:
    return tool_target_from_input(
        tool_name,
        tool_input,
        extract_bash_file_target=extract_bash_file_target,
    )


def _tool_action_signature(
    tool_name: str, tool_input: dict[str, Any]
) -> tuple[str, str, str]:
    return build_tool_action_signature(
        tool_name,
        tool_input,
        extract_bash_file_target=extract_bash_file_target,
    )


def _is_duplicate_of_latest_successful_tool_turn(
    state: AnthropicTurnState,
    tool_name: str,
    tool_input: dict[str, Any],
) -> bool:
    return is_duplicate_of_latest_successful_tool_turn(
        state.req,
        tool_name,
        tool_input,
        flatten_block_content=_flatten_block_content,
        looks_like_tool_error_payload=_looks_like_tool_error_payload,
        extract_bash_file_target=extract_bash_file_target,
    )


def _extract_last_tool_file(req: AnthropicMessagesRequest) -> str | None:
    return extract_last_tool_file(
        req,
        flatten_block_content=_flatten_block_content,
        looks_like_tool_error_payload=_looks_like_tool_error_payload,
        extract_bash_file_target=extract_bash_file_target,
    )


def _extract_last_successful_read_file(req: AnthropicMessagesRequest) -> str | None:
    return extract_last_successful_read_file(
        req,
        flatten_block_content=_flatten_block_content,
        looks_like_tool_error_payload=_looks_like_tool_error_payload,
        extract_bash_file_target=extract_bash_file_target,
    )


def _extract_last_shell_cwd(
    req: AnthropicMessagesRequest, initial_cwd: str | None = None
) -> str | None:
    return extract_last_shell_cwd(
        req,
        flatten_block_content=_flatten_block_content,
        looks_like_tool_error_payload=_looks_like_tool_error_payload,
        extract_bash_file_target=extract_bash_file_target,
        initial_cwd=initial_cwd,
    )


def _resolve_edit_tool_name(tool_map: dict[str, dict[str, Any]]) -> str | None:
    for name in ("Edit", "Update", "MultiEdit"):
        if name in tool_map:
            return name
    return None


def _validate_tool_policy(
    state: AnthropicTurnState,
    tool_name: str,
    tool_input: dict[str, Any],
) -> tuple[bool, str]:
    if tool_name in {"Read", "Write", "Edit", "Update", "MultiEdit", "NotebookEdit"}:
        base_dir = resolution_base_dir(
            state.last_successful_read_file,
            state.last_tool_file,
            state.project_root,
            state.current_shell_cwd,
        )
        for key in ("file_path", "path", "notebook_path"):
            value = tool_input.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            resolved = resolve_local_path_if_valid(value, base_dir=base_dir)
            if resolved is not None:
                tool_input[key] = str(resolved)

    constraints = state.explicit_runtime_constraints
    if constraints.no_subagents and tool_name == "Agent":
        state.policy_denial = "user_forbids_subagents"
        return False, state.policy_denial
    if constraints.no_background and (
        tool_name == "Agent"
        or tool_name.startswith("Task")
        or tool_name == "VerifyPlanExecution"
    ):
        state.policy_denial = f"user_forbids_background:{tool_name}"
        return False, state.policy_denial
    if getattr(state, "is_readonly_explore_subagent", False):
        if tool_name in {"WebSearch", "WebFetch"}:
            state.policy_denial = f"explore_subagent_forbids_tool:{tool_name}"
            return False, state.policy_denial
        if tool_name == "Bash":
            command = str(tool_input.get("command", "")).strip()
            if is_tavily_search_command(command) or is_url_fetch_bash_command(command):
                state.policy_denial = "explore_subagent_forbids_web_research"
                return False, state.policy_denial
    if (
        tool_name == "Bash"
        and getattr(constraints, "explicit_local_scope", False)
        and not getattr(constraints, "explicit_web_research", False)
    ):
        command = str(tool_input.get("command", "")).strip()
        if is_tavily_search_command(command) or is_url_fetch_bash_command(command):
            state.policy_denial = "explicit_local_scope_forbids_web_research"
            return False, state.policy_denial
    if (
        state.current_workflow == "local_implementation"
        and tool_name == "Agent"
        and not constraints.allow_subagents
    ):
        state.policy_denial = f"local_implementation_forbids_tool:{tool_name}"
        return False, state.policy_denial
    if (
        state.current_workflow == "local_implementation"
        and tool_name == "VerifyPlanExecution"
    ):
        state.policy_denial = f"local_implementation_forbids_tool:{tool_name}"
        return False, state.policy_denial
    if state.current_workflow == "local_implementation" and tool_name.startswith(
        "Task"
    ):
        state.policy_denial = f"local_implementation_forbids_tool:{tool_name}"
        return False, state.policy_denial
    if tool_name in {"WebSearch", "WebFetch"}:
        state.policy_denial = f"unsupported_tool_in_local_setup:{tool_name}"
        return False, state.policy_denial
    return validate_existing_file_policy(
        state,
        tool_name,
        tool_input,
        tool_target_from_input=_tool_target_from_input,
    )


async def _generate_file_content_from_request(
    state: AnthropicTurnState,
    filename: str,
) -> str | None:
    user_text = state.latest_user_text
    structured_content = extract_creation_content_from_user_text(user_text, filename)
    if structured_content:
        return structured_content
    embedded_code = extract_code_block(user_text)
    if embedded_code:
        return embedded_code

    ext = Path(filename).suffix.lower()
    language_hint = LANGUAGE_HINTS.get(ext, "plain text")

    prompt = file_content_user_prompt(filename, language_hint, user_text)
    prompt_text = build_prompt(
        GENERATOR.tokenizer,
        [
            {"role": "system", "content": file_content_system_prompt()},
            {"role": "user", "content": prompt},
        ],
        False,
    )
    result = await GENERATOR.generate(
        prompt_text,
        GenerationParams(max_tokens=32000, temperature=0.2, top_p=0.95),
    )
    content = result.output_text.strip()
    content = re.sub(r"^```[A-Za-z0-9_+-]*\n", "", content)
    content = re.sub(r"\n```$", "", content)
    return content.strip() or None


def _has_pending_multifile_steps(state: AnthropicTurnState) -> bool:
    return False


def _is_multifile_chain_done(state: AnthropicTurnState) -> bool:
    return False


async def _maybe_multifile_structured_tool_use(
    state: AnthropicTurnState,
    tools: dict[str, dict[str, Any]],
    edit_tool_name: str | None,
) -> dict[str, Any] | None:
    return None


async def _maybe_forced_file_tool_use(
    request: Request | None, state: AnthropicTurnState
) -> dict[str, Any] | None:
    return None


def _build_tool_planner_prompt(
    state: AnthropicTurnState, *, require_tool: bool = False
) -> str:
    return build_runtime_tool_planner_prompt(
        state,
        require_tool=require_tool,
        tool_schema_excerpt_fn=_tool_schema_excerpt,
    )


def _parse_textual_tool_use(text: str) -> dict[str, Any] | None:
    return parse_textual_tool_use(text, parse_json_object_fn=parse_json_object)


def _validated_named_tool_use(
    req: AnthropicMessagesRequest,
    state: AnthropicTurnState | None,
    tool_use: dict[str, Any] | None,
) -> dict[str, Any] | None:
    return validated_named_tool_use(
        req,
        state,
        tool_use,
        tool_lookup=_tool_lookup,
        normalize_tool_input_fn=normalize_tool_input,
        validate_tool_input_fn=validate_tool_input,
        validate_tool_policy_fn=_validate_tool_policy,
    )


def _build_edit_local_context(state: AnthropicTurnState, filename: str | None) -> str:
    if not filename:
        return (
            state.latest_dialogue[-4000:]
            if len(state.latest_dialogue) > 4000
            else state.latest_dialogue
        )

    target_name = Path(filename).name
    latest_read_result = ""
    latest_tool_error = ""

    messages = state.req.messages
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if msg.role != "user" or not isinstance(msg.content, list):
            continue
        for block in reversed(msg.content):
            if block.get("type") != "tool_result":
                continue

            result_text = _flatten_block_content([block], role=msg.role)
            is_error = bool(block.get("is_error")) or _looks_like_tool_error_payload(
                result_text
            )
            tool_use_id = str(block.get("tool_use_id", ""))
            matched_target = ""
            matched_tool = ""

            for prev_idx in range(idx - 1, -1, -1):
                prev_msg = messages[prev_idx]
                if prev_msg.role != "assistant" or not isinstance(
                    prev_msg.content, list
                ):
                    continue
                found = False
                for prev_block in reversed(prev_msg.content):
                    if prev_block.get("type") != "tool_use":
                        continue
                    if str(prev_block.get("id", "")) != tool_use_id:
                        continue
                    matched_tool = str(prev_block.get("name", ""))
                    raw_input = prev_block.get("input", {})
                    if isinstance(raw_input, dict):
                        for key in ("file_path", "path", "notebook_path"):
                            value = raw_input.get(key)
                            if isinstance(value, str) and value:
                                matched_target = value
                                break
                    found = True
                    break
                if found:
                    break

            if Path(matched_target).name != target_name:
                continue

            if matched_tool == "Read" and not is_error and not latest_read_result:
                latest_read_result = result_text
            if is_error and not latest_tool_error:
                latest_tool_error = result_text

            if latest_read_result and latest_tool_error:
                break
        if latest_read_result and latest_tool_error:
            break

    parts = [f"Current file focus: {target_name}"]
    if latest_read_result:
        trimmed_read = (
            latest_read_result[-8000:]
            if len(latest_read_result) > 8000
            else latest_read_result
        )
        parts.append(f"Latest relevant Read result for this file:\n{trimmed_read}")
    if latest_tool_error:
        parts.append(f"Latest relevant tool error for this file:\n{latest_tool_error}")
    if not latest_read_result and not latest_tool_error:
        parts.append(
            "No prior relevant Read result or tool error was found for this file in the recent trace."
        )
    return "\n\n".join(parts)


def _build_tool_input_prompt(
    state: AnthropicTurnState,
    tool: dict[str, Any],
    *,
    filename: str | None = None,
    previous_error: str | None = None,
) -> str:
    schema_excerpt = json.dumps(
        _tool_schema_excerpt(tool), ensure_ascii=False, indent=2
    )
    user_text = state.latest_user_text
    tool_name = str(tool.get("name", "tool"))
    target_path = filename or "(not specified)"
    target_abs_path = None
    if filename:
        base_dir = resolution_base_dir(
            state.last_successful_read_file,
            state.last_tool_file,
            state.project_root,
            state.current_shell_cwd,
        )
        resolved = resolve_local_path_if_valid(filename, base_dir=base_dir)
        if resolved is not None:
            target_abs_path = str(resolved)
    if tool_name in {"Edit", "Update", "MultiEdit"}:
        intro = tool_input_edit_intro(tool_name)
        context_block = _build_edit_local_context(state, filename)
        example_block = tool_input_edit_example(target_abs_path)
    else:
        write_rules = ""
        if tool_name == "Write":
            write_rules = tool_input_write_rules()
        intro = tool_input_general_intro(tool_name, write_rules)
        recent_dialogue = (
            state.latest_dialogue[-12000:]
            if len(state.latest_dialogue) > 12000
            else state.latest_dialogue
        )
        context_block = f"Recent dialogue:\n{recent_dialogue}"
        example_block = (
            tool_input_write_example(target_abs_path) if tool_name == "Write" else ""
        )

    retry_note = ""
    if previous_error:
        retry_note = tool_input_retry_note(previous_error)

    return (
        intro
        + retry_note
        + tool_input_schema_rules()
        + f"Target file: {target_path}\n"
        + (f"Target absolute path: {target_abs_path}\n" if target_abs_path else "")
        + f"Tool schema excerpt:\n{schema_excerpt}\n\n"
        + (f"{example_block}\n" if example_block else "")
        + f"Latest user request:\n{user_text}\n\n"
        + f"{context_block}\n"
    )


async def _generate_validated_tool_input(
    request: Request | None,
    state: AnthropicTurnState,
    tool: dict[str, Any],
    *,
    filename: str | None = None,
    retry_reason: str | None = None,
) -> dict[str, Any] | None:
    last_error = retry_reason or ""
    for attempt in range(2):
        prompt = _build_tool_input_prompt(
            state, tool, filename=filename, previous_error=last_error or None
        )
        prompt_text = build_prompt(
            GENERATOR.tokenizer,
            [
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            False,
        )
        result = await GENERATOR.generate(
            prompt_text,
            GenerationParams(max_tokens=32000, temperature=0.0, top_p=1.0),
        )
        _trace_event(
            request,
            "tool_input_attempt",
            attempt=attempt + 1,
            tool=tool.get("name"),
            filename=filename,
            raw_output=result.output_text,
            previous_error=last_error or None,
        )
        if tool_use_block_count(result.output_text) > 0:
            last_error = "returned tool_use text instead of plain JSON"
            continue
        tool_input = parse_json_object(result.output_text)
        if not isinstance(tool_input, dict):
            last_error = "response was not a valid JSON object"
            continue
        if filename:
            tool_input.setdefault("file_path", filename)
        tool_input = normalize_tool_input(tool, tool_input)
        is_valid, reason = validate_tool_input(tool, tool_input)
        if not is_valid:
            last_error = reason
            continue
        policy_ok, policy_reason = _validate_tool_policy(
            state, str(tool.get("name", "")), tool_input
        )
        if policy_ok:
            _trace_event(
                request,
                "tool_input_valid",
                tool=tool.get("name"),
                filename=filename,
                tool_input=tool_input,
            )
            return tool_input
        last_error = policy_reason
    _trace_event(
        request,
        "tool_input_failed",
        tool=tool.get("name"),
        filename=filename,
        last_error=last_error,
    )
    return None


def _build_bash_description(command: str) -> str:
    command = command.strip()
    if command == "ls":
        return "List files in current directory"
    if command == "pwd":
        return "Show current working directory"
    if command.startswith("ls "):
        return "List files with provided options"
    if command.startswith("git status"):
        return "Show git working tree status"
    return f"Run shell command: {command[:80]}"


def _tool_target_from_latest_input(
    tool_input_obj: dict[str, Any], fallback: str | None = None
) -> str | None:
    if not isinstance(tool_input_obj, dict):
        return fallback
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input_obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _is_recoverable_local_implementation_error(
    tool_name: str, tool_result: str
) -> bool:
    text = str(tool_result or "")
    if not text or "<tool_use_error>" not in text:
        return False
    if "File has not been read yet" in text:
        return True
    if "String to replace not found" in text:
        return True
    if "InputValidationError" in text:
        return True
    if "missing required field:" in text:
        return True
    if "unexpected fields:" in text:
        return True
    if tool_name == "Write" and "required parameter `content` is missing" in text:
        return True
    return False


async def _local_implementation_recoverable_tool_use(
    request: Request,
    state: AnthropicTurnState,
) -> dict[str, Any] | None:
    if state.current_workflow != "local_implementation":
        return None
    if state.normalized.mode != PromptMode.TOOL_RESULT:
        return None

    _latest_user_text, latest_tool_name, latest_tool_result, latest_tool_input_obj = (
        extract_latest_tool_exchange(state.req)
    )
    latest_tool_name = str(latest_tool_name or "")
    latest_tool_result = str(latest_tool_result or "")
    if not latest_tool_name or not _is_recoverable_local_implementation_error(
        latest_tool_name, latest_tool_result
    ):
        return None

    target = _tool_target_from_latest_input(
        latest_tool_input_obj, state.resolved_active_path or state.active_filename
    )
    base_dir = resolution_base_dir(
        state.last_successful_read_file,
        state.last_tool_file,
        state.project_root,
        state.current_shell_cwd,
    )
    resolved_target = (
        resolve_local_path_if_valid(target, base_dir=base_dir) if target else None
    )
    try:
        target_exists = bool(
            resolved_target and resolved_target.exists() and resolved_target.is_file()
        )
    except (OSError, PermissionError):
        target_exists = False

    if (
        "File has not been read yet" in latest_tool_result
        and "Read" in state.tool_map
        and target_exists
        and target
    ):
        return {
            "id": f"toolu_{uuid.uuid4().hex[:24]}",
            "name": "Read",
            "input": _initial_read_input(target),
        }

    if "String to replace not found" in latest_tool_result and latest_tool_name in {
        "Edit",
        "Update",
        "MultiEdit",
    }:
        tool = state.tool_map.get(latest_tool_name)
        if isinstance(tool, dict) and target:
            tool_input = await _generate_validated_tool_input(
                request,
                state,
                tool,
                filename=target,
                retry_reason="Your previous Edit FAILED: 'String to replace not found in file'. "
                "This means your old_string does not exactly match any text in the file. "
                "You MUST copy old_string EXACTLY as it appears in the file content shown below — "
                "character for character, including whitespace, indentation, and punctuation. "
                "Do not guess or paraphrase. Read the actual lines carefully before constructing old_string.",
            )
            if isinstance(tool_input, dict):
                return {
                    "id": f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": latest_tool_name,
                    "input": tool_input,
                }
        # Fallback: if re-generation fails, try Read so model gets fresh context
        if "Read" in state.tool_map and target_exists and target:
            return {
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": "Read",
                "input": _initial_read_input(target),
            }

    if latest_tool_name in {"Write", "Edit", "Update", "MultiEdit"}:
        tool = state.tool_map.get(latest_tool_name)
        if not isinstance(tool, dict):
            return None
        target = target or state.active_filename
        if latest_tool_name == "Write" and target and not target_exists:
            generated_content = await _generate_file_content_from_request(state, target)
            if generated_content:
                write_target = (
                    str(resolved_target) if resolved_target is not None else target
                )
                return {
                    "id": f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": "Write",
                    "input": {
                        "file_path": write_target,
                        "content": generated_content,
                    },
                }
        tool_input = await _generate_validated_tool_input(
            request, state, tool, filename=target
        )
        if isinstance(tool_input, dict):
            return {
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": latest_tool_name,
                "input": tool_input,
            }

    return None


def _is_stdout_only_bash_command(command: str) -> bool:
    command = command.strip()
    if not command:
        return False
    if any(token in command for token in (">", "|", "&&", "||", ";", "$(", "`")):
        return False
    return bool(re.match(r"^(echo|printf)\b", command))


def _is_directory_scan_bash_command(command: str) -> bool:
    command = command.strip()
    if not command:
        return False
    return bool(re.match(r"^(ls|find|rg --files)\b", command))


def _extract_ls_directories(result_text: str) -> list[str]:
    directories: list[str] = []
    seen: set[str] = set()
    for raw_line in result_text.splitlines():
        line = raw_line.rstrip()
        if not line or line.endswith(":") or line.startswith("total "):
            continue
        if not line.startswith("d"):
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        name = parts[-1].rstrip("/")
        if name in {".", ".."}:
            continue
        if not re.match(r"^[A-Za-z0-9_.-]+$", name):
            continue
        if name in seen:
            continue
        seen.add(name)
        directories.append(name)
    return directories


def _directory_scan_targets_from_command(command: str) -> list[str]:
    command = command.strip()
    if not command.startswith("ls "):
        return []
    tokens = command.split()[1:]
    targets: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            continue
        clean = token.rstrip("/").strip()
        if not clean or clean in {".", ".."}:
            continue
        targets.append(clean)
    return targets


def _extract_ls_entry_name(line: str) -> str:
    stripped = line.rstrip()
    if not stripped or stripped.startswith("total "):
        return ""
    if stripped.endswith(":"):
        return ""
    if stripped[0] not in {"d", "-", "l"}:
        return ""
    parts = stripped.split(None, 8)
    if len(parts) < 9:
        return ""
    return parts[8].split(" -> ", 1)[0].strip()


def _summarize_directory_scan_result(command: str, result_text: str) -> str:
    targets = _directory_scan_targets_from_command(command)
    sections: list[tuple[str, list[str]]] = []
    current_name = "root"
    current_entries: list[str] = []

    def flush_section() -> None:
        nonlocal current_entries, current_name
        deduped: list[str] = []
        seen: set[str] = set()
        for entry in current_entries:
            if not entry or entry in seen:
                continue
            seen.add(entry)
            deduped.append(entry)
        if deduped:
            sections.append((current_name, deduped[:8]))

    for raw_line in result_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.endswith(":"):
            flush_section()
            current_name = line[:-1].rstrip("/").strip() or "root"
            current_entries = []
            continue
        entry_name = _extract_ls_entry_name(line)
        if entry_name:
            current_entries.append(entry_name)
    flush_section()

    if not sections and targets:
        sections = [(target, []) for target in targets[:10]]

    lines = [
        f"Directory scan summary for command: {command}",
        f"Targets scanned: {', '.join(targets[:10]) if targets else 'root'}",
    ]
    for section_name, entries in sections[:10]:
        if entries:
            lines.append(f"{section_name}: {', '.join(entries)}")
        else:
            lines.append(f"{section_name}: no visible entries parsed")
    return "\n".join(lines)


def _effective_normalized_for_text_turn(
    state: AnthropicTurnState,
) -> NormalizedAnthropicRequest:
    normalized = state.normalized
    if (
        state.current_workflow == "failure_recovery"
        and normalized.mode == PromptMode.TOOL_RESULT
        and state.workflow_step == "retry_once"
    ):
        system_prompt = (
            normalized.prompt_messages[0]["content"]
            if normalized.prompt_messages
            else ""
        )
        failed_exchanges, last_error = failure_recovery_exchanges(state.req)
        latest_failed = failed_exchanges[-1] if failed_exchanges else None
        tool_name = latest_failed.tool_name if latest_failed else "tool"
        tool_input = latest_failed.tool_input_obj if latest_failed else {}
        tool_input_json = json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
        error_text = (last_error or "Tool execution failed.")[:800]
        original_request = (
            extract_latest_user_request_text(state.req)
            or state.latest_user_text
            or "Continue the task."
        )
        prompt_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": failure_recovery_user_prompt(
                    original_request,
                    tool_name,
                    tool_input_json,
                    error_text,
                    failure_recovery_prompt_rules(),
                ),
            },
        ]
        prompt = build_prompt(GENERATOR.tokenizer, prompt_messages, False)
        return NormalizedAnthropicRequest(
            mode=normalized.mode,
            oai_body=normalized.oai_body,
            prompt_messages=prompt_messages,
            prompt=prompt,
            compaction=normalized.compaction,
        )
    if normalized.mode == PromptMode.TOOL_RESULT and not state.current_workflow:
        user_text, tool_name, tool_payload, tool_input_obj = (
            extract_latest_tool_exchange(state.req)
        )
        if tool_name == "Bash":
            command = str(tool_input_obj.get("command", "")).strip()
            tavily_payload = _extract_successful_tavily_payload(command, tool_payload)
            if tavily_payload is not None:
                system_prompt = (
                    normalized.prompt_messages[0]["content"]
                    if normalized.prompt_messages
                    else ""
                )
                condensed = json.dumps(
                    {
                        "query": tavily_payload.get("query"),
                        "results": tavily_payload.get("results", [])[:8],
                    },
                    ensure_ascii=False,
                )
                prompt_messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": tavily_retry_success_user_prompt(
                            user_text, condensed
                        ),
                    },
                ]
                prompt = build_prompt(GENERATOR.tokenizer, prompt_messages, False)
                return NormalizedAnthropicRequest(
                    mode=normalized.mode,
                    oai_body=normalized.oai_body,
                    prompt_messages=prompt_messages,
                    prompt=prompt,
                    compaction=normalized.compaction,
                )
    if (
        state.current_workflow == "project_analysis"
        and normalized.mode == PromptMode.TOOL_RESULT
        and state.workflow_step == "summarize"
        and state.collected_summaries
    ):
        system_prompt = (
            normalized.prompt_messages[0]["content"]
            if normalized.prompt_messages
            else ""
        )
        covered_directories = ", ".join(state.covered_directories[:20]) or "none"
        deep_branches = ", ".join(state.covered_deep_branches[:20]) or "none"
        evidence_lines = "\n\n".join(state.collected_summaries[-4:])
        prompt_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": project_analysis_summarize_user_prompt(
                    state.latest_user_text,
                    covered_directories,
                    deep_branches,
                    evidence_lines,
                ),
            },
        ]
        prompt = build_prompt(GENERATOR.tokenizer, prompt_messages, False)
        return NormalizedAnthropicRequest(
            mode=normalized.mode,
            oai_body=normalized.oai_body,
            prompt_messages=prompt_messages,
            prompt=prompt,
            compaction=normalized.compaction,
        )
    if (
        state.current_workflow == "file_investigation"
        and normalized.mode == PromptMode.TOOL_RESULT
        and _file_investigation_has_enough_context(state)
        and state.collected_summaries
    ):
        system_prompt = (
            normalized.prompt_messages[0]["content"]
            if normalized.prompt_messages
            else ""
        )
        investigated_files = ", ".join(state.collected_targets[:10]) or "none"
        evidence_lines = "\n\n".join(state.collected_summaries[-4:])
        prompt_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": file_investigation_summarize_user_prompt(
                    state.latest_user_text,
                    investigated_files,
                    evidence_lines,
                ),
            },
        ]
        prompt = build_prompt(GENERATOR.tokenizer, prompt_messages, False)
        return NormalizedAnthropicRequest(
            mode=normalized.mode,
            oai_body=normalized.oai_body,
            prompt_messages=prompt_messages,
            prompt=prompt,
            compaction=normalized.compaction,
        )
    if (
        state.current_workflow == "session_recall"
        and normalized.mode == PromptMode.TOOL_RESULT
        and state.workflow_step == "summarize"
        and state.collected_summaries
    ):
        system_prompt = (
            normalized.prompt_messages[0]["content"]
            if normalized.prompt_messages
            else ""
        )
        result_lines = "\n\n".join(
            _latest_session_search_result_summaries(state.req, limit=5)
        )
        if not result_lines:
            result_lines = "\n\n".join(state.collected_summaries[:5])
        prompt_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": session_recall_summarize_user_prompt(
                    state.latest_user_text,
                    result_lines,
                ),
            },
        ]
        prompt = build_prompt(GENERATOR.tokenizer, prompt_messages, False)
        return NormalizedAnthropicRequest(
            mode=normalized.mode,
            oai_body=normalized.oai_body,
            prompt_messages=prompt_messages,
            prompt=prompt,
            compaction=normalized.compaction,
        )
    if (
        state.current_workflow == "tavily_research"
        and normalized.mode == PromptMode.TOOL_RESULT
        and state.workflow_step == "summarize"
        and state.collected_summaries
    ):
        system_prompt = (
            normalized.prompt_messages[0]["content"]
            if normalized.prompt_messages
            else ""
        )
        search_evidence_lines = "\n\n".join(
            _latest_tavily_result_summaries(state.req, limit=5)
        )
        evidence_lines = "\n\n".join(state.collected_summaries[:5])
        source_lines = "\n".join(f"- {url}" for url in state.collected_targets[:5])
        prompt_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": tavily_research_summarize_user_prompt(
                    state.latest_user_text,
                    search_evidence_lines,
                    source_lines,
                    evidence_lines,
                ),
            },
        ]
        prompt = build_prompt(GENERATOR.tokenizer, prompt_messages, False)
        return NormalizedAnthropicRequest(
            mode=normalized.mode,
            oai_body=normalized.oai_body,
            prompt_messages=prompt_messages,
            prompt=prompt,
            compaction=normalized.compaction,
        )
    if state.current_workflow != "project_analysis":
        return normalized
    if normalized.mode != PromptMode.TOOL_RESULT:
        return normalized

    user_text, tool_name, tool_payload, tool_input_obj = extract_latest_tool_exchange(
        state.req
    )
    if tool_name != "Bash":
        return normalized
    command = str(tool_input_obj.get("command", "")).strip()
    if not _is_directory_scan_bash_command(command):
        return normalized

    compressed_summary = _summarize_directory_scan_result(command, tool_payload)
    system_prompt = (
        normalized.prompt_messages[0]["content"] if normalized.prompt_messages else ""
    )
    prompt_messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": project_analysis_scan_user_prompt(
                user_text, tool_name, compressed_summary
            ),
        },
    ]
    prompt = build_prompt(GENERATOR.tokenizer, prompt_messages, False)
    return NormalizedAnthropicRequest(
        mode=normalized.mode,
        oai_body=normalized.oai_body,
        prompt_messages=prompt_messages,
        prompt=prompt,
        compaction=normalized.compaction,
    )


def _is_allowed_subdir_scan_command(command: str, allowed_dirs: list[str]) -> bool:
    targets = _directory_scan_targets_from_command(command)
    if not targets:
        return False
    allowed = set(allowed_dirs)
    return all(target in allowed for target in targets)


def _project_analysis_trackable_dirs(collected_directories: list[str]) -> list[str]:
    return [
        directory
        for directory in collected_directories
        if directory not in {".git", "__pycache__"}
    ]


def _project_analysis_deep_branch_candidates(
    covered_directories: list[str],
) -> list[str]:
    preferred = {
        "backend",
        "frontend",
        "mobile",
        "mobileapp",
        "agentfactory",
        "core",
        "cloud_code_backend",
        "models",
        "services",
        "components",
        "scripts",
        "docs",
        "reference_repos",
        "html-ru-versions",
    }
    candidates: list[str] = []
    seen: set[str] = set()
    for directory in covered_directories:
        key = directory.lower()
        if key not in preferred or directory in seen:
            continue
        seen.add(directory)
        candidates.append(directory)
    return candidates


def _project_analysis_covered_dirs(
    collected_targets: list[str], collected_directories: list[str]
) -> list[str]:
    trackable = set(_project_analysis_trackable_dirs(collected_directories))
    covered: list[str] = []
    seen: set[str] = set()
    for command in collected_targets[1:]:
        for target in _directory_scan_targets_from_command(command):
            if target not in trackable or target in seen:
                continue
            seen.add(target)
            covered.append(target)
    return covered


def _project_analysis_covered_deep_branches(
    collected_targets: list[str],
    candidate_deep_branches: list[str],
) -> list[str]:
    candidates = set(candidate_deep_branches)
    covered: list[str] = []
    seen: set[str] = set()
    for command in collected_targets[2:]:
        for target in _directory_scan_targets_from_command(command):
            top_level = target.split("/", 1)[0]
            if top_level not in candidates or top_level in seen:
                continue
            seen.add(top_level)
            covered.append(top_level)
    return covered


def _project_analysis_has_enough_context(state: AnthropicTurnState) -> bool:
    if state.current_workflow != "project_analysis":
        return False
    if state.workflow_step_count < 2:
        return False
    if not state.collected_directories:
        return False
    if state.remaining_directories:
        return False
    if state.remaining_deep_branches:
        return False
    return len(state.covered_directories) >= 2 and len(state.collected_summaries) >= 2


def _summarize_read_result(target: str, result_text: str) -> str:
    cleaned_lines: list[str] = []
    for line in result_text.splitlines():
        if "<system-reminder>" in line:
            break
        normalized = re.sub(r"^\s*\d+→", "", line).strip()
        if normalized:
            cleaned_lines.append(normalized)
    snippet = " ".join(cleaned_lines[:8]).strip()
    if len(snippet) > 240:
        snippet = snippet[:237].rstrip() + "..."
    label = target or "unknown file"
    if snippet:
        return f"{label}: {snippet}"
    return f"{label}: file read successfully"


def _sample_head_tail(items: list[str], limit: int = 8) -> list[str]:
    if len(items) <= limit:
        return items
    head_count = max(1, limit // 2)
    tail_count = max(1, limit - head_count - 1)
    sampled: list[str] = []
    seen: set[str] = set()
    for item in [*items[:head_count], "...", *items[-tail_count:]]:
        if item != "..." and item in seen:
            continue
        if item != "...":
            seen.add(item)
        sampled.append(item)
    return sampled[:limit]


def _summarize_read_evidence(target: str, exchanges: list[ToolExchange]) -> str:
    if not exchanges:
        return f"{target}: file read successfully"
    ranges: list[str] = []
    seen_ranges: set[str] = set()
    sections: list[str] = []
    seen_sections: set[str] = set()
    imports: list[str] = []
    seen_imports: set[str] = set()
    constants: list[str] = []
    seen_constants: set[str] = set()
    entrypoints: list[str] = []
    seen_entrypoints: set[str] = set()
    decorated_entrypoints: list[str] = []
    seen_decorated: set[str] = set()
    for exchange in exchanges:
        min_line = _read_result_min_line_number(exchange.result_text)
        max_line = _read_result_max_line_number(exchange.result_text)
        if min_line is not None and max_line is not None:
            range_label = f"{min_line}-{max_line}"
            if range_label not in seen_ranges:
                seen_ranges.add(range_label)
                ranges.append(range_label)
        for section_name in extract_read_sections(exchange.result_text):
            if section_name in seen_sections:
                continue
            seen_sections.add(section_name)
            sections.append(section_name)
        for module_name in extract_read_imports(exchange.result_text):
            if module_name in seen_imports:
                continue
            seen_imports.add(module_name)
            imports.append(module_name)
        for constant_name in extract_top_level_constants(exchange.result_text):
            if constant_name in seen_constants:
                continue
            seen_constants.add(constant_name)
            constants.append(constant_name)
        for entry_name in extract_main_entrypoints(exchange.result_text):
            if entry_name in seen_entrypoints:
                continue
            seen_entrypoints.add(entry_name)
            entrypoints.append(entry_name)
        for entry_name in extract_decorated_entrypoints(exchange.result_text):
            if entry_name in seen_decorated:
                continue
            seen_decorated.add(entry_name)
            decorated_entrypoints.append(entry_name)

    parts: list[str] = []
    if ranges:
        displayed_ranges = ranges
        if len(ranges) > 8:
            displayed_ranges = [*ranges[:4], "...", *ranges[-4:]]
        parts.append(f"lines {', '.join(displayed_ranges)}")
    if imports:
        parts.append(f"imports {', '.join(_sample_head_tail(imports, limit=8))}")
    if constants:
        parts.append(f"constants {', '.join(constants[:8])}")
    if sections:
        sampled_sections = sample_section_names(sections, limit=10)
        parts.append(f"sections {', '.join(sampled_sections)}")
    if entrypoints:
        parts.append(f"entrypoints {', '.join(entrypoints[:8])}")
    if decorated_entrypoints:
        parts.append(f"decorated entrypoints {', '.join(decorated_entrypoints[:8])}")
    if parts:
        return f"{target}: {'; '.join(parts)}"
    return _summarize_read_result(target, exchanges[-1].result_text)


def _aggregate_file_investigation_summaries(
    successful_reads: list[ToolExchange],
) -> list[str]:
    aggregated: list[str] = []
    per_target: dict[str, list[ToolExchange]] = {}
    for exchange in successful_reads:
        target = exchange.target.strip()
        if not target or not exchange.result_text:
            continue
        per_target.setdefault(target, []).append(exchange)

    for target, exchanges in list(per_target.items())[-3:]:
        aggregated.append(_summarize_read_evidence(target, exchanges))
    return aggregated


def _file_investigation_successful_reads(
    req: AnthropicMessagesRequest,
) -> tuple[list[ToolExchange], str | None]:
    exchanges = _extract_tool_exchanges(req)
    successful_reads: list[ToolExchange] = []
    last_error: str | None = None
    latest_target: str | None = None

    for exchange in reversed(exchanges):
        if exchange.tool_name != "Read":
            if successful_reads:
                break
            continue
        exchange_is_error = exchange.is_error and not _read_result_looks_successful(
            exchange.result_text
        )
        if exchange_is_error:
            if last_error is None:
                last_error = exchange.result_text[:400]
            continue
        if not _read_result_looks_successful(exchange.result_text):
            continue
        target = exchange.target.strip()
        if not target:
            if successful_reads:
                break
            continue
        if latest_target is None:
            latest_target = target
        elif target != latest_target:
            break
        successful_reads.append(exchange)

    successful_reads.reverse()
    return successful_reads, last_error


def _file_investigation_state(
    req: AnthropicMessagesRequest,
) -> tuple[
    str | None,
    str | None,
    int,
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    str | None,
]:
    return file_investigation_state(
        req,
        _read_result_looks_successful,
        _file_investigation_related_files,
        _continued_read_input,
    )


def _failure_recovery_state(
    req: AnthropicMessagesRequest,
) -> tuple[
    str | None,
    str | None,
    int,
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    str | None,
]:
    return failure_recovery_state(req)


def _extract_tavily_result_urls(payload: dict[str, Any], limit: int = 5) -> list[str]:
    return extract_tavily_result_urls(payload, limit=limit)


def _latest_tavily_result_summaries(
    req: AnthropicMessagesRequest, limit: int = 5
) -> list[str]:
    for exchange in reversed(_extract_tool_exchanges(req)):
        if exchange.tool_name != "Bash" or exchange.is_error:
            continue
        command = str(exchange.tool_input_obj.get("command", "")).strip()
        payload = _extract_successful_tavily_payload(command, exchange.result_text)
        if payload is None:
            continue
        summaries = build_tavily_result_summaries(payload, limit=limit)
        if summaries:
            return summaries
    return []


def _latest_session_search_result_summaries(
    req: AnthropicMessagesRequest, limit: int = 5
) -> list[str]:
    for exchange in reversed(_extract_tool_exchanges(req)):
        if exchange.tool_name != "SessionSearch" or exchange.is_error:
            continue
        payload = parse_json_payload(
            strip_system_reminder(exchange.result_text or "").strip()
        )
        if not isinstance(payload, dict) or payload.get("success") is not True:
            continue
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            continue
        summaries: list[str] = []
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            session_id = str(item.get("session", "") or "").strip()
            excerpt = str(
                item.get("excerpt", "") or item.get("preview", "") or ""
            ).strip()
            last_active = str(item.get("last_active", "") or "").strip()
            source = str(
                item.get("source", "") or item.get("entry_kind", "") or ""
            ).strip()
            header_parts = []
            if session_id:
                header_parts.append(f"session={session_id}")
            if last_active:
                header_parts.append(f"last_active={last_active}")
            if source:
                header_parts.append(f"source={source}")
            header = " | ".join(header_parts)
            if excerpt:
                summaries.append(f"{header}\n{excerpt}" if header else excerpt)
            elif header:
                summaries.append(header)
        if summaries:
            return summaries
    return []


def _summarize_fetched_web_source(url: str, result_text: str) -> str:
    return summarize_fetched_web_source(url, result_text, strip_system_reminder)


def _tavily_research_state(
    req: AnthropicMessagesRequest,
) -> tuple[
    str | None,
    str | None,
    int,
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    str | None,
]:
    return tavily_research_state(req, strip_system_reminder)


def _session_recall_state(
    req: AnthropicMessagesRequest,
) -> tuple[
    str | None,
    str | None,
    int,
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    str | None,
]:
    return session_recall_state(req, strip_system_reminder)


def _tavily_retry_bash_command_for_error(command: str, error_text: str) -> str | None:
    return tavily_retry_bash_command_for_error(
        command, error_text, strip_system_reminder
    )


def _failure_recovery_tool_use(state: AnthropicTurnState) -> dict[str, Any] | None:
    failed_exchanges, _ = failure_recovery_exchanges(state.req)
    if not failed_exchanges:
        return None
    latest_failed = failed_exchanges[-1]
    tool_name = latest_failed.tool_name
    target = latest_failed.target.strip()

    if tool_name == "Read" and target and "Read" in state.tool_map:
        return {
            "id": f"toolu_{uuid.uuid4().hex[:24]}",
            "name": "Read",
            "input": _initial_read_input(target),
        }

    if tool_name == "Bash" and "Bash" in state.tool_map:
        command = str(latest_failed.tool_input_obj.get("command", "")).strip()
        corrected_tavily_command = _tavily_retry_bash_command_for_error(
            command, latest_failed.result_text
        )
        if corrected_tavily_command:
            return {
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": "Bash",
                "input": {"command": corrected_tavily_command},
            }
        if command and _is_directory_scan_bash_command(command):
            return {
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": "Bash",
                "input": {"command": command},
            }
        return None

    if (
        tool_name in {"Edit", "Write", "Update", "MultiEdit"}
        and target
        and "Read" in state.tool_map
    ):
        return {
            "id": f"toolu_{uuid.uuid4().hex[:24]}",
            "name": "Read",
            "input": _initial_read_input(target),
        }

    return None


def _build_failure_recovery_stop_response(state: AnthropicTurnState) -> str:
    summary = (
        state.collected_summaries[-1]
        if state.collected_summaries
        else "Последний инструмент завершился ошибкой."
    )
    details = (
        state.last_workflow_error[:300]
        if state.last_workflow_error
        else "Подходящий корректирующий шаг не сработал."
    )
    if summary == "(Bash completed with no output)":
        summary = "Команда завершилась с ошибкой без вывода."
    if details == "(Bash completed with no output)":
        details = "Команда завершилась с ошибкой без вывода."
    return (
        "Не удалось автоматически восстановиться после ошибки инструмента.\n\n"
        f"Последний сбой: {summary}\n"
        f"Детали: {details}\n\n"
        "Останавливаюсь без дальнейших догадок. Нужен новый явный шаг от пользователя."
    )


def _build_local_implementation_stop_response(state: AnthropicTurnState) -> str:
    details = strip_system_reminder((state.last_workflow_error or "").strip())
    details = re.sub(r"(?is)</?tool_use_error>", "", details).strip()
    details = re.sub(r"\[tool_result[^\n]*\]\s*", "", details).strip()
    if details == "(Bash completed with no output)":
        details = "Команда завершилась без вывода."
    if not details:
        details = "Локальная реализация не завершена из-за ошибки инструмента."
    return (
        "Локальная реализация не завершена.\n\n"
        "Последний шаг с инструментом завершился ошибкой, поэтому я не считаю файлы созданными или обновлёнными.\n\n"
        f"Последняя ошибка:\n```text\n{details[:500]}\n```"
    )


def _workflow_config(workflow_name: str | None) -> dict[str, Any]:
    return workflow_config(workflow_name)


def _workflow_state_config(
    workflow_name: str | None, workflow_step: str | None
) -> dict[str, Any]:
    config = _workflow_config(workflow_name)
    states = config.get("states")
    if not isinstance(states, dict) or not workflow_step:
        return {}
    state_config = states.get(workflow_step)
    return state_config if isinstance(state_config, dict) else {}


def _workflow_completion_policy(
    state: AnthropicTurnState,
) -> tuple[str | None, str | None]:
    return workflow_completion_policy(state, _file_investigation_has_enough_context)


def _workflow_allows_tool(
    workflow_name: str | None,
    workflow_step: str | None,
    tool_use: dict[str, Any] | None,
    state: AnthropicTurnState | None = None,
) -> bool:
    return workflow_allows_tool(workflow_name, workflow_step, tool_use, state)


def _project_analysis_step(
    scan_count: int,
    remaining_directories: list[str],
    remaining_deep_branches: list[str],
) -> str | None:
    if scan_count <= 0:
        return None
    if scan_count == 1:
        return "scan_root"
    if remaining_directories:
        return "scan_subdirs"
    if remaining_deep_branches:
        return "scan_deep"
    return "summarize"


def _project_analysis_forced_scan_tool_use(
    state: AnthropicTurnState,
) -> dict[str, Any] | None:
    return project_analysis_forced_scan_tool_use(state)


def _file_investigation_forced_read_tool_use(
    state: AnthropicTurnState,
) -> dict[str, Any] | None:
    return file_investigation_forced_read_tool_use(state, _initial_read_input)


def _tavily_research_forced_tool_use(
    state: AnthropicTurnState,
) -> dict[str, Any] | None:
    return tavily_research_forced_tool_use(state)


def _project_analysis_state(
    req: AnthropicMessagesRequest,
) -> tuple[
    str | None,
    str | None,
    int,
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    str | None,
]:
    return project_analysis_state(req)


def _resolve_python_module_path(module_name: str, project_root: Path) -> str | None:
    if not module_name or module_name.startswith("."):
        return None
    module_parts = [part for part in module_name.split(".") if part]
    if not module_parts:
        return None
    module_path = Path(*module_parts)
    candidates = [
        (project_root / module_path).with_suffix(".py"),
        project_root / module_path / "__init__.py",
    ]
    project_root_resolved = project_root.resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(project_root_resolved)
        except (FileNotFoundError, ValueError):
            continue
        if resolved.is_file():
            return str(resolved)
    return None


def _extract_related_python_files(
    target: str,
    result_text: str,
    project_root: str | None,
) -> list[str]:
    if not target.endswith(".py") or not result_text or not project_root:
        return []
    root = Path(project_root).resolve()
    related_files: list[str] = []
    seen: set[str] = set()
    for line in result_text.splitlines()[:200]:
        stripped = line.strip()
        modules: list[str] = []
        from_match = re.match(r"from\s+([A-Za-z_][\w\.]*)\s+import\s+", stripped)
        if from_match:
            modules = [from_match.group(1)]
        else:
            import_match = re.match(r"import\s+(.+)$", stripped)
            if import_match:
                raw_modules = [
                    part.strip() for part in import_match.group(1).split(",")
                ]
                modules = [
                    part.split(" as ", 1)[0].strip()
                    for part in raw_modules
                    if part.strip()
                ]
        for module_name in modules:
            resolved = _resolve_python_module_path(module_name, root)
            if not resolved or resolved == target or resolved in seen:
                continue
            seen.add(resolved)
            related_files.append(resolved)
    return related_files[:12]


def _file_investigation_related_files(
    successful_reads: list[ToolExchange],
    req: AnthropicMessagesRequest,
    collected_targets: list[str],
) -> list[str]:
    return file_investigation_related_files(successful_reads, req, collected_targets)


def _file_investigation_continuation_read(
    successful_reads: list[ToolExchange],
    collected_targets: list[str],
) -> dict[str, Any] | None:
    return file_investigation_continuation_read(
        successful_reads, collected_targets, _continued_read_input
    )


def _file_investigation_has_enough_context(state: AnthropicTurnState) -> bool:
    return file_investigation_has_enough_context(
        state,
        _file_investigation_successful_reads,
        _file_investigation_continuation_read,
    )


async def _maybe_model_tool_use(
    request: Request | None,
    state: AnthropicTurnState,
    *,
    require_tool: bool = False,
) -> dict[str, Any] | None:
    return await runtime_maybe_model_tool_use(
        request,
        state,
        generator=GENERATOR,
        trace_event_fn=_trace_event,
        require_tool=require_tool,
        tool_schema_excerpt_fn=_tool_schema_excerpt,
        tool_lookup_fn=lambda req: state.tool_map,
        normalize_tool_input_fn=normalize_tool_input,
        validate_tool_input_fn=validate_tool_input,
        validate_tool_policy_fn=_validate_tool_policy,
    )


async def _resolve_tool_turn(
    request: Request,
    state: AnthropicTurnState,
) -> ToolTurnDecision | None:
    _trace_event(
        request,
        "turn_state",
        mode=state.normalized.mode,
        explicit_user_file=state.explicit_user_file,
        explicit_user_absolute_path=state.explicit_user_absolute_path,
        last_tool_file=state.last_tool_file,
        last_successful_read_file=state.last_successful_read_file,
        current_shell_cwd=state.current_shell_cwd,
        project_root=state.project_root,
        temporary_allowed_root=state.temporary_allowed_root,
        active_filename=state.active_filename,
        latest_user_text=state.latest_user_text,
        explicit_runtime_constraints=asdict(state.explicit_runtime_constraints),
        explicit_read=state.explicit_read,
        explicit_write=state.explicit_write,
        explicit_edit=state.explicit_edit,
        requires_tool=state.requires_tool,
        missing_file_target=state.missing_file_target,
        current_workflow=state.current_workflow,
        workflow_step=state.workflow_step,
        workflow_step_count=state.workflow_step_count,
        workflow_started_by=state.workflow_started_by,
        collected_targets=state.collected_targets,
        collected_directories=state.collected_directories,
        covered_directories=state.covered_directories,
        remaining_directories=state.remaining_directories,
        candidate_deep_branches=state.candidate_deep_branches,
        covered_deep_branches=state.covered_deep_branches,
        remaining_deep_branches=state.remaining_deep_branches,
        candidate_related_files=state.candidate_related_files,
        collected_summaries=state.collected_summaries,
        last_workflow_error=state.last_workflow_error,
        policy_denial=state.policy_denial,
        direct_bash_command=state.direct_bash_command,
    )

    (
        execute_command_short_circuit,
        _,
    ) = await _maybe_short_circuit_execute_command_tool_result(request, state)
    if execute_command_short_circuit:
        _trace_event(
            request,
            "execute_command_tool_result_deferred_to_text_turn",
            mode=state.normalized.mode,
            latest_user_text=state.latest_user_text,
        )
        return None

    if (
        state.normalized.mode != PromptMode.TOOL_RESULT
        and not state.current_workflow
        and state.explicit_user_file
        and state.resolved_active_path
        and state.active_file_exists
        and "Read" in state.tool_map
    ):
        forced_read = {
            "id": f"toolu_{uuid.uuid4().hex[:24]}",
            "name": "Read",
            "input": _initial_read_input(state.resolved_active_path),
        }
        _trace_event(
            request,
            "forced_explicit_read",
            active_filename=state.active_filename,
            resolved_active_path=state.resolved_active_path,
            tool_input=forced_read["input"],
            mode=state.normalized.mode,
        )
        return ToolTurnDecision(tool_use=forced_read, source="forced_explicit_read")

    if (
        state.current_workflow == "file_investigation"
        and state.workflow_step == "read_primary"
    ):
        forced_primary_read = _file_investigation_forced_read_tool_use(state)
        if forced_primary_read is not None:
            _trace_event(
                request,
                "forced_file_investigation_read_primary",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                active_filename=state.active_filename,
                resolved_active_path=state.resolved_active_path,
                tool_input=forced_primary_read["input"],
                mode=state.normalized.mode,
            )
            return ToolTurnDecision(
                tool_use=forced_primary_read,
                source="forced_file_investigation_read_primary",
            )

    if state.current_workflow == "file_investigation" and state.workflow_step in {
        "read_primary",
        "read_primary_continuation",
    }:
        successful_reads, _ = _file_investigation_successful_reads(state.req)
        continuation_input = _file_investigation_continuation_read(
            successful_reads,
            [target for target in state.collected_targets if target],
        )
        if continuation_input is not None and "Read" in state.tool_map:
            _latest_user_text, last_tool_name, _last_tool_payload, last_tool_input = (
                extract_latest_tool_exchange(state.req)
            )
            if last_tool_name == "Read" and isinstance(last_tool_input, dict):
                same_file = (
                    str(last_tool_input.get("file_path", "")).strip()
                    == str(continuation_input.get("file_path", "")).strip()
                )
                same_offset = int(last_tool_input.get("offset", 1) or 1) == int(
                    continuation_input.get("offset", 1) or 1
                )
                same_limit = int(last_tool_input.get("limit", 0) or 0) == int(
                    continuation_input.get("limit", 0) or 0
                )
                if same_file and same_offset and same_limit:
                    continuation_input = None
        if continuation_input is not None and "Read" in state.tool_map:
            forced_read = {
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": "Read",
                "input": continuation_input,
            }
            _trace_event(
                request,
                "forced_read_continuation",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                tool_input=continuation_input,
                mode=state.normalized.mode,
            )
            return ToolTurnDecision(
                tool_use=forced_read, source="forced_read_continuation"
            )

    if state.current_workflow == "failure_recovery":
        if state.workflow_step == "retry_once":
            recovery_tool_use = _failure_recovery_tool_use(state)
            if recovery_tool_use is not None:
                _trace_event(
                    request,
                    "forced_failure_recovery",
                    workflow=state.current_workflow,
                    workflow_step=state.workflow_step,
                    tool_name=recovery_tool_use.get("name"),
                    tool_input=recovery_tool_use.get("input", {}),
                    last_workflow_error=state.last_workflow_error,
                )
                return ToolTurnDecision(
                    tool_use=recovery_tool_use, source="forced_failure_recovery"
                )
            _trace_event(
                request,
                "failure_recovery_unavailable",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                last_workflow_error=state.last_workflow_error,
            )
        if state.workflow_step == "stop":
            _trace_event(
                request,
                "failure_recovery_stop",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                workflow_step_count=state.workflow_step_count,
                last_workflow_error=state.last_workflow_error,
            )
            return None

    if state.current_workflow == "project_analysis" and state.workflow_step in {
        "scan_root",
        "scan_subdirs",
        "scan_deep",
    }:
        forced_scan = _project_analysis_forced_scan_tool_use(state)
        if forced_scan is not None:
            _trace_event(
                request,
                "forced_project_analysis_scan",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                tool_name=forced_scan.get("name"),
                tool_input=forced_scan.get("input", {}),
                collected_directories=state.collected_directories,
                remaining_directories=state.remaining_directories,
                remaining_deep_branches=state.remaining_deep_branches,
            )
            return ToolTurnDecision(
                tool_use=forced_scan, source="forced_project_analysis_scan"
            )

    if state.current_workflow == "tavily_research":
        if state.workflow_step in {"search", "fetch_sources"}:
            forced_tavily_tool = _tavily_research_forced_tool_use(state)
            if forced_tavily_tool is not None:
                _trace_event(
                    request,
                    "forced_tavily_research_step",
                    workflow=state.current_workflow,
                    workflow_step=state.workflow_step,
                    workflow_step_count=state.workflow_step_count,
                    tool_name=forced_tavily_tool.get("name"),
                    tool_input=forced_tavily_tool.get("input", {}),
                    candidate_related_files=state.candidate_related_files,
                    collected_targets=state.collected_targets,
                )
                return ToolTurnDecision(
                    tool_use=forced_tavily_tool, source="forced_tavily_research"
                )
        if state.workflow_step == "stop":
            _trace_event(
                request,
                "tavily_research_stop",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                workflow_step_count=state.workflow_step_count,
                last_workflow_error=state.last_workflow_error,
            )
            return None

    latest_user_text, latest_tool_name, latest_tool_result, _latest_tool_input_obj = (
        extract_latest_tool_exchange(state.req)
    )

    if (
        state.current_workflow == "local_implementation"
        and state.workflow_step == "continue"
        and "Read" in state.tool_map
        and state.resolved_active_path
        and state.active_file_exists
        and latest_tool_name in {"Write", "Edit", "Update", "MultiEdit"}
        and "File has not been read yet" in str(latest_tool_result or "")
    ):
        forced_read = {
            "id": f"toolu_{uuid.uuid4().hex[:24]}",
            "name": "Read",
            "input": _initial_read_input(state.resolved_active_path),
        }
        _trace_event(
            request,
            "forced_local_implementation_read",
            workflow=state.current_workflow,
            workflow_step=state.workflow_step,
            tool_input=forced_read["input"],
            last_workflow_error=latest_tool_result,
        )
        return ToolTurnDecision(
            tool_use=forced_read, source="forced_local_implementation_read"
        )

    if (
        state.current_workflow == "local_implementation"
        and state.workflow_step == "continue"
    ):
        recoverable_tool_use = await _local_implementation_recoverable_tool_use(
            request, state
        )
        if recoverable_tool_use is not None:
            _trace_event(
                request,
                "forced_local_implementation_recovery",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                tool_name=recoverable_tool_use.get("name"),
                tool_input=recoverable_tool_use.get("input", {}),
                last_workflow_error=state.last_workflow_error,
            )
            return ToolTurnDecision(
                tool_use=recoverable_tool_use,
                source="forced_local_implementation_recovery",
            )

    if state.normalized.mode == PromptMode.SUGGESTION and not (
        state.current_workflow == "file_investigation"
        and state.workflow_step in {"read_primary", "read_related"}
        and not _file_investigation_has_enough_context(state)
    ):
        return None

    force_initial_execute_command_followup = False
    if (
        state.normalized.mode in {PromptMode.NORMAL, PromptMode.STARTUP_REMINDER}
        and state.current_workflow is None
        and state.requires_tool
        and state.latest_user_text.strip()
    ):
        initial_intent = await _route_user_intent(request, state.latest_user_text)
        force_initial_execute_command_followup = (
            initial_intent == UserIntent.EXECUTE_COMMAND
        )
    followup = workflow_followup_requirements(
        state,
        file_investigation_has_enough_context_fn=_file_investigation_has_enough_context,
    )
    completion_action = followup["completion_action"]
    completion_reason = followup["completion_reason"]
    workflow_max_steps = int(followup["workflow_max_steps"])
    in_project_scan_workflow = bool(followup["in_project_scan_workflow"])
    in_file_investigation_workflow = bool(followup["in_file_investigation_workflow"])
    in_failure_recovery_workflow = bool(followup["in_failure_recovery_workflow"])
    in_local_implementation_workflow = bool(
        followup["in_local_implementation_workflow"]
    )
    in_tavily_research_workflow = bool(followup["in_tavily_research_workflow"])
    in_session_recall_workflow = bool(followup["in_session_recall_workflow"])
    force_project_scan_followup = bool(followup["force_project_scan_followup"])
    force_file_investigation_followup = bool(
        followup["force_file_investigation_followup"]
    )
    force_failure_recovery_followup = bool(followup["force_failure_recovery_followup"])
    force_local_implementation_followup = bool(
        followup["force_local_implementation_followup"]
    )
    force_tavily_research_followup = bool(followup["force_tavily_research_followup"])
    force_session_recall_followup = bool(followup["force_session_recall_followup"])

    if completion_action == "summarize":
        _trace_event(
            request,
            "workflow_forced_summary",
            workflow=state.current_workflow,
            workflow_step=state.workflow_step,
            workflow_step_count=state.workflow_step_count,
            completion_reason=completion_reason,
        )
        return None
    if completion_action == "stop":
        _trace_event(
            request,
            "workflow_forced_stop",
            workflow=state.current_workflow,
            workflow_step=state.workflow_step,
            workflow_step_count=state.workflow_step_count,
            completion_reason=completion_reason,
        )
        return None

    planned_tool_use = await _maybe_model_tool_use(
        request,
        state,
        require_tool=(
            force_initial_execute_command_followup
            or force_project_scan_followup
            or force_file_investigation_followup
            or force_failure_recovery_followup
            or force_local_implementation_followup
            or force_tavily_research_followup
            or force_session_recall_followup
        ),
    )
    if planned_tool_use is not None and _is_duplicate_of_latest_successful_tool_turn(
        state,
        str(planned_tool_use.get("name", "")),
        planned_tool_use.get("input", {}),
    ):
        _trace_event(
            request,
            "planner_duplicate_tool_rejected",
            mode=state.normalized.mode,
            rejected_tool=planned_tool_use.get("name"),
            rejected_input=planned_tool_use.get("input", {}),
        )
        planned_tool_use = None
    if (
        planned_tool_use is None
        and in_file_investigation_workflow
        and state.policy_denial
    ):
        _trace_event(
            request,
            "workflow_invalid_tool_rejected",
            workflow=state.current_workflow,
            workflow_step=state.workflow_step,
            workflow_step_count=state.workflow_step_count,
            rejection_reason=state.policy_denial,
            allowed_targets=state.collected_targets,
        )
        state.policy_denial = None
    if planned_tool_use is not None and completion_action == "summarize":
        _trace_event(
            request,
            "workflow_forced_summary",
            workflow=state.current_workflow,
            workflow_step=state.workflow_step,
            workflow_step_count=state.workflow_step_count,
            completion_reason=completion_reason,
            rejected_tool=planned_tool_use.get("name"),
            rejected_input=planned_tool_use.get("input", {}),
        )
        planned_tool_use = None
    if planned_tool_use is not None and completion_action == "stop":
        _trace_event(
            request,
            "workflow_forced_stop",
            workflow=state.current_workflow,
            workflow_step=state.workflow_step,
            workflow_step_count=state.workflow_step_count,
            completion_reason=completion_reason,
            rejected_tool=planned_tool_use.get("name"),
            rejected_input=planned_tool_use.get("input", {}),
        )
        planned_tool_use = None
    if planned_tool_use is not None and in_project_scan_workflow:
        if completion_action is None:
            planned_tool_name = planned_tool_use.get("name")
            planned_input = planned_tool_use.get("input", {})
            planned_command = str(planned_input.get("command", "")).strip()
            is_scan_bash = (
                planned_tool_name == "Bash"
                and _is_directory_scan_bash_command(planned_command)
            )

            if is_scan_bash and state.workflow_step_count >= workflow_max_steps:
                _trace_event(
                    request,
                    "workflow_tool_limit_reached",
                    workflow=state.current_workflow,
                    workflow_step=state.workflow_step,
                    workflow_step_count=state.workflow_step_count,
                    rejected_tool=planned_tool_name,
                    rejected_input=planned_input,
                )
                planned_tool_use = None
            elif not _workflow_allows_tool(
                state.current_workflow, state.workflow_step, planned_tool_use, state
            ):
                _trace_event(
                    request,
                    "workflow_invalid_tool_rejected",
                    workflow=state.current_workflow,
                    workflow_step=state.workflow_step,
                    workflow_step_count=state.workflow_step_count,
                    rejected_tool=planned_tool_name,
                    rejected_input=planned_input,
                    allowed_directories=(
                        state.collected_directories
                        if state.workflow_step == "scan_root"
                        else state.remaining_directories
                        if state.workflow_step == "scan_subdirs"
                        else state.remaining_deep_branches
                    ),
                )
                planned_tool_use = None
    if planned_tool_use is not None and in_file_investigation_workflow:
        if (
            completion_action is None
            and state.workflow_step_count >= workflow_max_steps
        ):
            _trace_event(
                request,
                "workflow_tool_limit_reached",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                workflow_step_count=state.workflow_step_count,
                rejected_tool=planned_tool_use.get("name"),
                rejected_input=planned_tool_use.get("input", {}),
            )
            planned_tool_use = None
        elif not _workflow_allows_tool(
            state.current_workflow, state.workflow_step, planned_tool_use, state
        ):
            _trace_event(
                request,
                "workflow_invalid_tool_rejected",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                workflow_step_count=state.workflow_step_count,
                rejected_tool=planned_tool_use.get("name"),
                rejected_input=planned_tool_use.get("input", {}),
                allowed_targets=[target for target in state.collected_targets if target]
                or state.candidate_related_files,
            )
            planned_tool_use = None
    if planned_tool_use is not None and in_tavily_research_workflow:
        if (
            completion_action is None
            and state.workflow_step_count >= workflow_max_steps
        ):
            _trace_event(
                request,
                "workflow_tool_limit_reached",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                workflow_step_count=state.workflow_step_count,
                rejected_tool=planned_tool_use.get("name"),
                rejected_input=planned_tool_use.get("input", {}),
            )
            planned_tool_use = None
        elif not _workflow_allows_tool(
            state.current_workflow, state.workflow_step, planned_tool_use, state
        ):
            _trace_event(
                request,
                "workflow_invalid_tool_rejected",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                workflow_step_count=state.workflow_step_count,
                rejected_tool=planned_tool_use.get("name"),
                rejected_input=planned_tool_use.get("input", {}),
                allowed_targets=state.candidate_related_files,
            )
            planned_tool_use = None
    if planned_tool_use is not None and in_session_recall_workflow:
        if (
            completion_action is None
            and state.workflow_step_count >= workflow_max_steps
        ):
            _trace_event(
                request,
                "workflow_tool_limit_reached",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                workflow_step_count=state.workflow_step_count,
                rejected_tool=planned_tool_use.get("name"),
                rejected_input=planned_tool_use.get("input", {}),
            )
            planned_tool_use = None
        elif not _workflow_allows_tool(
            state.current_workflow, state.workflow_step, planned_tool_use, state
        ):
            _trace_event(
                request,
                "workflow_invalid_tool_rejected",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                workflow_step_count=state.workflow_step_count,
                rejected_tool=planned_tool_use.get("name"),
                rejected_input=planned_tool_use.get("input", {}),
                allowed_targets=state.collected_targets,
            )
            planned_tool_use = None
    if planned_tool_use is not None and in_local_implementation_workflow:
        if (
            completion_action is None
            and state.workflow_step_count >= workflow_max_steps
        ):
            _trace_event(
                request,
                "workflow_tool_limit_reached",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                workflow_step_count=state.workflow_step_count,
                rejected_tool=planned_tool_use.get("name"),
                rejected_input=planned_tool_use.get("input", {}),
            )
            planned_tool_use = None
        elif not _workflow_allows_tool(
            state.current_workflow, state.workflow_step, planned_tool_use, state
        ):
            _trace_event(
                request,
                "workflow_invalid_tool_rejected",
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                workflow_step_count=state.workflow_step_count,
                rejected_tool=planned_tool_use.get("name"),
                rejected_input=planned_tool_use.get("input", {}),
                allowed_targets=[
                    target for target in state.collected_targets if target
                ],
            )
            planned_tool_use = None
    if planned_tool_use is not None:
        planned_tool_name = str(planned_tool_use.get("name", ""))
        planned_tool_input = planned_tool_use.get("input", {})
        if not isinstance(planned_tool_input, dict):
            planned_tool_input = {}
        policy_ok, policy_reason = _validate_tool_policy(
            state, planned_tool_name, planned_tool_input
        )
        if not policy_ok:
            _trace_event(
                request,
                "planner_policy_tool_rejected",
                mode=state.normalized.mode,
                rejected_tool=planned_tool_name,
                rejected_input=planned_tool_input,
                rejection_reason=policy_reason,
            )
            planned_tool_use = None
    if planned_tool_use is not None:
        return ToolTurnDecision(tool_use=planned_tool_use, source="planner")
    if state.policy_denial:
        _trace_event(
            request,
            "file_policy_denial",
            mode=state.normalized.mode,
            reason=state.policy_denial,
            active_filename=state.active_filename,
        )

    execute_command_terminal_turn = False
    if (
        state.normalized.mode == PromptMode.TOOL_RESULT
        and state.current_workflow is None
        and not state.missing_file_target
    ):
        (
            execute_command_terminal_turn,
            _,
        ) = await _maybe_short_circuit_execute_command_tool_result(request, state)

    if (
        state.normalized.mode == PromptMode.TOOL_RESULT
        and state.requires_tool
        and not force_project_scan_followup
        and not force_file_investigation_followup
        and not force_failure_recovery_followup
        and not force_local_implementation_followup
        and not force_tavily_research_followup
        and not force_session_recall_followup
        and not in_project_scan_workflow
        and not in_file_investigation_workflow
        and not in_failure_recovery_workflow
        and not in_local_implementation_workflow
        and not in_tavily_research_workflow
        and not in_session_recall_workflow
        and not state.missing_file_target
        and not execute_command_terminal_turn
    ):
        strict_planned_tool_use = await _maybe_model_tool_use(
            request, state, require_tool=True
        )
        if strict_planned_tool_use is not None:
            strict_tool_name = str(strict_planned_tool_use.get("name", ""))
            strict_tool_input = strict_planned_tool_use.get("input", {})
            if not isinstance(strict_tool_input, dict):
                strict_tool_input = {}
            policy_ok, policy_reason = _validate_tool_policy(
                state, strict_tool_name, strict_tool_input
            )
            if not policy_ok:
                _trace_event(
                    request,
                    "planner_policy_tool_rejected",
                    mode=state.normalized.mode,
                    rejected_tool=strict_tool_name,
                    rejected_input=strict_tool_input,
                    rejection_reason=policy_reason,
                )
            else:
                return ToolTurnDecision(
                    tool_use=strict_planned_tool_use, source="planner_strict"
                )
        if state.policy_denial:
            _trace_event(
                request,
                "file_policy_denial",
                mode=state.normalized.mode,
                reason=state.policy_denial,
                active_filename=state.active_filename,
            )

    return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global GENERATOR, CONTEXT_COMPRESSOR, SERVER_READY
    SERVER_READY = False
    if MODEL_PATH is None:
        raise RuntimeError("MODEL_PATH is not configured")
    GENERATOR = create_runtime_generator(
        backend=MODEL_BACKEND,
        model_path=MODEL_PATH,
        llama_server_bin=LLAMA_SERVER_BIN,
        llama_server_host=LLAMA_SERVER_HOST,
        llama_server_port=LLAMA_SERVER_PORT,
        llama_ctx_size=LLAMA_CTX_SIZE,
        llama_gpu_layers=LLAMA_GPU_LAYERS,
        llama_server_extra_args=LLAMA_SERVER_EXTRA_ARGS,
        llama_autodetect_mmproj=LLAMA_AUTODETECT_MMPROJ,
        llama_server_command=LLAMA_SERVER_COMMAND,
    )
    CONTEXT_COMPRESSOR = ContextCompressor(
        model=MODEL_NAME or "unknown",
        tokenizer=GENERATOR.tokenizer,
        context_length=int(
            os.environ.get("CONTEXT_COMPRESSION_CONTEXT_LENGTH", LLAMA_CTX_SIZE)
        ),
        summary_client=ContextCompressionClient(
            generator=GENERATOR,
            model_name=MODEL_NAME or "unknown",
            config=build_context_compression_client_config(),
        ),
        config=ContextCompactorConfig(
            enabled=os.environ.get("CONTEXT_COMPRESSION_ENABLED", "1") != "0",
            threshold=float(os.environ.get("CONTEXT_COMPRESSION_THRESHOLD", "0.50")),
            target_ratio=float(
                os.environ.get("CONTEXT_COMPRESSION_TARGET_RATIO", "0.20")
            ),
            protect_first_n=int(
                os.environ.get("CONTEXT_COMPRESSION_PROTECT_FIRST_N", "3")
            ),
            protect_last_n=int(
                os.environ.get("CONTEXT_COMPRESSION_PROTECT_LAST_N", "20")
            ),
            summary_model=os.environ.get("CONTEXT_COMPRESSION_MODEL", "").strip()
            or None,
        ),
        quiet_mode=not DEBUG_TRACE,
    )
    SERVER_READY = True
    try:
        yield
    finally:
        SERVER_READY = False
        CONTEXT_COMPRESSOR = None
        if GENERATOR is not None:
            await GENERATOR.close()


app = FastAPI(title="ParoQuant Transformers API", lifespan=lifespan)


def _error_payload(message: str, *, error_type: str) -> dict[str, Any]:
    return {
        "type": "error",
        "error": {
            "type": error_type,
            "message": message,
        },
    }


def _with_request_id(payload: dict[str, Any], request_id: str, status: int | None = None) -> dict[str, Any]:
    from factory.core.diagnostics import classify_error, observation
    normalized = dict(payload)
    normalized["type"] = "error"
    error = normalized.get("error")
    if not isinstance(error, dict):
        error = {"type": "api_error", "message": "Request failed"}
        normalized["error"] = error
    normalized["request_id"] = request_id
    normalized['diagnostic'] = observation(classify_error(status=status), component='inference-api', operation='request',
        endpoint_role='runtime_to_provider', owner='inference-api', delivery='rejected', http_status=status, http_request_id=request_id)
    return normalized


def _request_id(request: Request) -> str:
    return get_request_id(request)


@app.middleware("http")
async def request_lifecycle_middleware(request: Request, call_next):
    request.state.request_id = f"req_{uuid.uuid4().hex[:12]}"
    start = time.perf_counter()

    from factory.api_server_core.request_auth import authentication_error
    if request.url.path not in PUBLIC_ENDPOINTS:
        backend = 'openrouter' if MODEL_BACKEND == 'openrouter' else str(getattr(GENERATOR, 'backend', MODEL_BACKEND))
        denied = authentication_error(request.headers, backend=backend)
        if denied is not None:
            status, message = denied
            response = JSONResponse(status_code=status, content=_with_request_id(
                _error_payload(message, error_type='authentication_error'),
                request.state.request_id,
                status,
            ))
            response.headers['x-request-id'] = request.state.request_id
            return response

    if not SERVER_READY and request.url.path not in PUBLIC_ENDPOINTS:
        response = JSONResponse(
            status_code=503,
            content=_with_request_id(
                _error_payload("Loading model", error_type="api_error"),
                request.state.request_id,
                503,
            ),
        )
        response.headers["x-request-id"] = request.state.request_id
        return response

    response = await call_next(request)
    response.headers["x-request-id"] = request.state.request_id

    if request.url.path not in QUIET_LOG_PATHS:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(
            f"[{request.state.request_id}] {request.method} {request.url.path} "
            f"{response.status_code} {elapsed_ms:.2f}ms"
        )

    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        payload = detail
    else:
        message = detail if isinstance(detail, str) else "Request failed"
        error_type = "invalid_request_error" if exc.status_code < 500 else "api_error"
        payload = _error_payload(message, error_type=error_type)

    return JSONResponse(
        status_code=exc.status_code,
        content=_with_request_id(payload, _request_id(request), exc.status_code),
        headers={key:value for key,value in (exc.headers or {}).items() if key.lower() in {'retry-after','www-authenticate'}},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from factory.core.diagnostics import safe_text
    print(f"[{_request_id(request)}] unhandled exception: {safe_text(exc)}")
    print(safe_text(traceback.format_exc(), 16000))
    payload = _error_payload(
        "Internal Server Error",
        error_type="api_error",
    )
    return JSONResponse(
        status_code=500, content=_with_request_id(payload, _request_id(request), 500)
    )


def _flatten_content(content: str | list[AnthropicTextBlock]) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.text for block in content if block.type == "text")


def _log_anthropic_request(payload: dict[str, Any]) -> None:
    write_anthropic_request_log(ANTHROPIC_LOG, payload)


def _trace_event(request: Request | None, kind: str, **payload: Any) -> None:
    write_trace_event(TRACE_LOG, DEBUG_TRACE, request, kind, **payload)


def _flatten_block_content(
    content: str | list[dict[str, Any]] | None, *, role: str
) -> str:
    return flatten_block_content(content, role=role)


def _strip_system_reminder(text: str) -> str:
    return strip_system_reminder(text)


def _extract_embedded_suggestion_draft(text: str) -> str:
    return extract_embedded_suggestion_draft(text)


def _is_suggestion_mode_request(text: str) -> bool:
    return is_suggestion_mode_request(text)


def _has_embedded_tool_result_marker(text: str) -> bool:
    return has_embedded_tool_result_marker(text)


def _last_user_message(req: AnthropicMessagesRequest) -> AnthropicMessage | None:
    return last_user_message(req)


def _extract_last_user_visible_text_block(msg: AnthropicMessage | None) -> str:
    return extract_last_user_visible_text_block(msg)


def _is_tool_result_only_user_turn(msg: AnthropicMessage) -> bool:
    return is_tool_result_only_user_turn(msg)


def _detect_prompt_mode(req: AnthropicMessagesRequest) -> str:
    return detect_prompt_mode(req, PromptMode)


def _extract_anthropic_messages(
    req: AnthropicMessagesRequest, mode: str
) -> list[dict[str, str]]:
    return extract_anthropic_messages(
        req,
        mode,
        prompt_mode=PromptMode,
        tools_prompt=_build_tools_prompt(req),
    )


def _convert_anthropic_to_oai_body(req: AnthropicMessagesRequest) -> dict[str, Any]:
    return convert_anthropic_to_oai_body(req, extract_tools=_extract_tools)


def _extract_image_from_blocks(
    blocks: list[dict[str, Any]] | str | None,
) -> list[dict[str, object]]:
    """Extract image parts from a list of content blocks (may be nested inside tool_result)."""
    images: list[dict[str, object]] = []
    if not isinstance(blocks, list):
        return images
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", ""))
        if block_type == "image":
            source = block.get("source", {})
            if isinstance(source, dict) and source.get("type") == "base64":
                media_type = str(source.get("media_type", "image/png"))
                data = str(source.get("data", ""))
                if data:
                    images.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{data}"},
                        }
                    )
        elif block_type == "tool_result":
            inner = block.get("content")
            if isinstance(inner, list):
                images.extend(_extract_image_from_blocks(inner))
    return images


def _extract_multimodal_messages(
    req: AnthropicMessagesRequest,
    normalized: NormalizedAnthropicRequest,
) -> list[dict[str, object]] | None:
    """Build chat-format messages with image blocks if any images are present in the request.

    Uses `normalized.prompt_messages` as the authoritative text history so any
    runtime compaction is preserved. The latest user message with actual image
    blocks is then re-injected from the raw request to keep visual input.
    """
    has_images = False
    for msg in req.messages:
        if isinstance(msg.content, list):
            found = _extract_image_from_blocks(msg.content)
            if found:
                has_images = True
                break
    if not has_images:
        return None

    chat_messages: list[dict[str, object]] = []
    for msg in normalized.prompt_messages:
        role = str(msg.get("role", "user"))
        if role == "assistant":
            role = "model"
        chat_messages.append({"role": role, "content": str(msg.get("content", ""))})

    latest_user_with_images = None
    for msg in reversed(req.messages):
        if msg.role != "user" or not isinstance(msg.content, list):
            continue
        if _extract_image_from_blocks(msg.content):
            latest_user_with_images = msg
            break
    if latest_user_with_images is None:
        return chat_messages if len(chat_messages) > 1 else None

    parts: list[dict[str, object]] = []
    for block in latest_user_with_images.content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", ""))
        if block_type == "text":
            text = str(block.get("text", ""))
            if text:
                parts.append({"type": "text", "text": text})
        elif block_type == "image":
            source = block.get("source", {})
            if isinstance(source, dict) and source.get("type") == "base64":
                media_type = str(source.get("media_type", "image/png"))
                data = str(source.get("data", ""))
                if data:
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{data}"},
                        }
                    )
        elif block_type == "tool_result":
            inner = block.get("content")
            if isinstance(inner, list):
                inner_images = _extract_image_from_blocks(inner)
                parts.extend(inner_images)
                for ib in inner:
                    if isinstance(ib, dict) and ib.get("type") == "text":
                        text = str(ib.get("text", ""))
                        if text:
                            parts.append({"type": "text", "text": text})
            if not parts or all(p.get("type") != "text" for p in parts):
                content = _flatten_block_content([block], role="user")
                if content:
                    parts.append({"type": "text", "text": content})
        elif block_type == "tool_use":
            tool_name = str(block.get("name", ""))
            tool_input = block.get("input", {})
            parts.append(
                {
                    "type": "text",
                    "text": f"[tool_use: {tool_name} {json.dumps(tool_input, ensure_ascii=False)}]",
                }
            )

    if not parts:
        return chat_messages if len(chat_messages) > 1 else None

    replacement_content: object
    if len(parts) == 1 and parts[0].get("type") == "text":
        replacement_content = str(parts[0].get("text", ""))
    else:
        replacement_content = parts

    for idx in range(len(chat_messages) - 1, -1, -1):
        if chat_messages[idx].get("role") == "user":
            chat_messages[idx] = {"role": "user", "content": replacement_content}
            break
    else:
        chat_messages.append({"role": "user", "content": replacement_content})

    return chat_messages if len(chat_messages) > 1 else None


def _normalize_anthropic_request(
    req: AnthropicMessagesRequest,
) -> NormalizedAnthropicRequest:
    mode = _detect_prompt_mode(req)
    prompt_messages = _extract_anthropic_messages(req, mode)
    request_system_prompt = flatten_block_content(req.system, role="system").strip()
    prompt_messages = [
        {
            "role": "system",
            "content": build_system_prompt(
                extra_blocks=[request_system_prompt] if request_system_prompt else None,
                model_name=str(req.model or ""),
            ),
        },
        *prompt_messages,
    ]
    prompt = build_prompt(GENERATOR.tokenizer, prompt_messages, False)
    return NormalizedAnthropicRequest(
        mode=mode,
        oai_body=_convert_anthropic_to_oai_body(req),
        prompt_messages=prompt_messages,
        prompt=prompt,
        compaction=None,
    )


def _summarize_normalized_request(
    normalized: NormalizedAnthropicRequest,
) -> dict[str, Any]:
    return {
        "mode": normalized.mode,
        "prompt_messages": normalized.prompt_messages,
        "oai_body": normalized.oai_body,
        "prompt_preview": normalized.prompt[:2000],
        "compaction": normalized.compaction,
    }


async def _apply_context_compaction_lifecycle(
    request: Request | None,
    normalized: NormalizedAnthropicRequest,
    *,
    phase: str,
) -> NormalizedAnthropicRequest:
    compressor = CONTEXT_COMPRESSOR
    if compressor is None:
        return normalized

    prompt_tokens = _count_prompt_tokens(normalized.prompt)
    if not compressor.should_compress(prompt_tokens):
        return normalized

    compacted_messages, metadata = await compressor.compress(
        normalized.prompt_messages,
        current_tokens=prompt_tokens,
    )
    if not metadata:
        return normalized

    compacted_prompt = build_prompt(GENERATOR.tokenizer, compacted_messages, False)
    compacted = NormalizedAnthropicRequest(
        mode=normalized.mode,
        oai_body=normalized.oai_body,
        prompt_messages=compacted_messages,
        prompt=compacted_prompt,
        compaction=metadata,
    )
    if request is not None:
        _trace_event(
            request,
            "context_compression_applied",
            phase=phase,
            original_prompt_tokens=prompt_tokens,
            compacted_prompt_tokens=_count_prompt_tokens(compacted_prompt),
            compaction=metadata,
        )
    return compacted


def sanitize_output_for_mode(text: str, mode: str) -> str:
    return sanitize_output_for_mode_text(
        text,
        mode,
        suggestion_mode=PromptMode.SUGGESTION,
        tool_result_mode=PromptMode.TOOL_RESULT,
    )


def _extract_visible_delta(
    raw_text: str, emitted_text: str, mode: str
) -> tuple[str, str]:
    return extract_visible_delta(
        raw_text,
        emitted_text,
        mode,
        suggestion_mode=PromptMode.SUGGESTION,
        tool_result_mode=PromptMode.TOOL_RESULT,
    )


def _infer_stop_reason(output_tokens: int, max_tokens: int) -> str:
    return infer_stop_reason(output_tokens, max_tokens)


def _usage_dict(prompt: str, completion_tokens: int) -> dict[str, int]:
    prompt_tokens = len(GENERATOR.tokenizer.encode(prompt, add_special_tokens=False))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
    }


def _describe_tool_preamble_context(
    state: AnthropicTurnState, tool_use: dict[str, Any]
) -> str:
    tool_name = str(tool_use.get("name", "tool"))
    target = ""
    tool_input = tool_use.get("input", {})
    if isinstance(tool_input, dict):
        for key in ("file_path", "path", "command"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                target = value
                break

    if state.normalized.mode == PromptMode.TOOL_RESULT:
        _user_text, last_tool_name, tool_payload, tool_input_obj = (
            extract_latest_tool_exchange(state.req)
        )
        last_target = ""
        for key in ("file_path", "path", "notebook_path"):
            value = tool_input_obj.get(key)
            if isinstance(value, str) and value:
                last_target = value
                break
        last_status = (
            "ошибка" if _looks_like_tool_error_payload(tool_payload) else "успех"
        )

        if tool_name == "Write" and last_tool_name == "Read":
            return preamble_ctx_write_after_read(last_target or target)
        if tool_name == "Read" and last_tool_name == "Write":
            return preamble_ctx_read_after_write(last_status)
        if (
            tool_name == "Read"
            and last_tool_name == "Read"
            and target
            and target == last_target
            and isinstance(tool_input, dict)
            and isinstance(tool_input.get("offset"), int)
            and tool_input.get("offset", 1) > 1
        ):
            return preamble_ctx_read_continuation(target)
        if tool_name == "Read":
            return preamble_ctx_read_step()
        if tool_name == "Write":
            return preamble_ctx_write_step()

    if tool_name == "Read":
        return preamble_ctx_first_read(target)
    if tool_name == "Write":
        return preamble_ctx_direct_write(target)
    return preamble_ctx_default()


async def _generate_tool_preamble(
    state: AnthropicTurnState, tool_use: dict[str, Any]
) -> str:
    tool_name = str(tool_use.get("name", "tool"))
    tool_input = tool_use.get("input", {})
    target = ""
    for key in ("file_path", "path", "command"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            target = value
            break

    prompt_messages = [
        {
            "role": "system",
            "content": preamble_system_prompt(),
        },
        {
            "role": "user",
            "content": preamble_user_prompt(
                state.latest_user_text,
                tool_name,
                target,
                _describe_tool_preamble_context(state, tool_use),
            ),
        },
    ]
    prompt = build_prompt(GENERATOR.tokenizer, prompt_messages, False)
    params = GenerationParams(max_tokens=32000, temperature=0.2, top_p=0.9)
    try:
        result = await GENERATOR.generate(prompt, params)
    except Exception:
        return ""
    return sanitize_tool_preamble(result.output_text)


def _count_text_tokens(text: str) -> int:
    return count_text_tokens(text, encode=GENERATOR.tokenizer.encode)


def _count_prompt_tokens(prompt: str) -> int:
    return count_prompt_tokens(prompt, encode=GENERATOR.tokenizer.encode)


def _estimate_tool_use_output_tokens(
    tool_use: dict[str, Any], preamble: str = ""
) -> int:
    return estimate_tool_use_output_tokens(
        tool_use,
        preamble=preamble,
        encode=GENERATOR.tokenizer.encode,
    )


def _anthropic_message_base(
    *,
    message_id: str,
    input_tokens: int,
    output_tokens: int = 0,
) -> dict[str, Any]:
    return anthropic_message_base(
        message_id=message_id,
        input_tokens=input_tokens,
        model_name=MODEL_NAME,
        output_tokens=output_tokens,
    )


def _anthropic_message_start_event(
    *,
    message_id: str,
    input_tokens: int,
    output_tokens: int = 0,
) -> str:
    return anthropic_message_start_event(
        message_id=message_id,
        input_tokens=input_tokens,
        model_name=MODEL_NAME,
        output_tokens=output_tokens,
    )


def _anthropic_message_delta_event(
    *,
    stop_reason: str,
    output_tokens: int,
    stop_sequence: str | None = None,
) -> str:
    return anthropic_message_delta_event(
        stop_reason=stop_reason,
        output_tokens=output_tokens,
        stop_sequence=stop_sequence,
    )


def _build_generation_params(state: AnthropicTurnState) -> GenerationParams:
    return build_runtime_generation_params(
        state,
        file_investigation_has_enough_context_fn=_file_investigation_has_enough_context,
    )


def _anthropic_tool_message_response(
    *,
    message_id: str,
    tool_use: dict[str, Any],
    input_tokens: int,
    preamble: str = "",
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return anthropic_tool_message_response(
        message_id=message_id,
        tool_use=tool_use,
        input_tokens=input_tokens,
        model_name=MODEL_NAME,
        estimate_tool_tokens=_estimate_tool_use_output_tokens,
        preamble=preamble,
        stats=stats,
    )


def _anthropic_text_message_response(
    *,
    message_id: str,
    prompt: str,
    req: AnthropicMessagesRequest,
    mode: str,
    output_text: str,
    stop_reason: str | None = None,
    stop_sequence: str | None = None,
    stats: dict[str, Any] | None = None,
    policy_denial: str | None = None,
) -> dict[str, Any]:
    output_text = sanitize_output_for_mode(output_text, mode)
    usage = _usage_dict(prompt, _count_text_tokens(output_text))
    return anthropic_text_message_response(
        message_id=message_id,
        input_tokens=usage["input_tokens"],
        output_text=output_text,
        model_name=MODEL_NAME,
        infer_stop_reason_fn=_infer_stop_reason,
        count_text_tokens_fn=_count_text_tokens,
        max_tokens=req.max_tokens,
        stop_reason=stop_reason,
        stop_sequence=stop_sequence,
        stats=stats,
        policy_denial=policy_denial,
    )


def _request_stop_sequences(req: AnthropicMessagesRequest) -> list[str]:
    return runtime_request_stop_sequences(req)


def _split_request_stop_sequence(
    text: str, req: AnthropicMessagesRequest
) -> tuple[str, str | None, bool]:
    return runtime_split_request_stop_sequence(
        text, req, split_stop_sequence_fn=split_stop_sequence
    )


def _anthropic_tool_stream_response(
    message_id: str,
    tool_use: dict[str, Any],
    input_tokens: int,
    preamble: str = "",
) -> StreamingResponse:
    return runtime_tool_stream_response(
        message_id,
        tool_use,
        input_tokens,
        preamble=preamble,
        message_start_event_fn=_anthropic_message_start_event,
        sse_event_fn=sse_event,
        message_delta_event_fn=_anthropic_message_delta_event,
        estimate_tool_tokens_fn=_estimate_tool_use_output_tokens,
    )


def _anthropic_empty_text_stream_response(
    message_id: str, input_tokens: int
) -> StreamingResponse:
    return runtime_empty_text_stream_response(
        message_id,
        input_tokens,
        message_start_event_fn=_anthropic_message_start_event,
        message_delta_event_fn=_anthropic_message_delta_event,
        sse_event_fn=sse_event,
    )


def _anthropic_text_stream_response_from_text(
    message_id: str,
    input_tokens: int,
    output_text: str,
    *,
    stop_reason: str = "end_turn",
    stop_sequence: str | None = None,
) -> StreamingResponse:
    return runtime_text_stream_response_from_text(
        message_id,
        input_tokens,
        output_text,
        message_start_event_fn=_anthropic_message_start_event,
        sse_event_fn=sse_event,
        message_delta_event_fn=_anthropic_message_delta_event,
        count_text_tokens_fn=_count_text_tokens,
        stop_reason=stop_reason,
        stop_sequence=stop_sequence,
    )


def _anthropic_text_stream_response(
    state: AnthropicTurnState,
    req: AnthropicMessagesRequest,
    normalized: NormalizedAnthropicRequest,
    params: GenerationParams,
    *,
    multimodal_messages: list[dict[str, object]] | None = None,
) -> StreamingResponse:
    return runtime_text_stream_response(
        state,
        req,
        normalized,
        params,
        generator=GENERATOR,
        response_state_cls=AnthropicResponseState,
        request=None,
        multimodal_messages=multimodal_messages,
        parse_textual_tool_use_fn=_parse_textual_tool_use,
        validated_named_tool_use_fn=_validated_named_tool_use,
        split_request_stop_sequence_fn=_split_request_stop_sequence,
        extract_visible_delta_fn=_extract_visible_delta,
        sanitize_output_for_mode_fn=sanitize_output_for_mode,
        extract_latest_tool_exchange_fn=extract_latest_tool_exchange,
        is_tavily_search_command_fn=is_tavily_search_command,
        looks_like_tool_error_payload_fn=_looks_like_tool_error_payload,
        trace_event_fn=_trace_event,
        build_policy_denial_response_fn=_build_policy_denial_response,
        count_prompt_tokens_fn=_count_prompt_tokens,
        count_text_tokens_fn=_count_text_tokens,
        infer_stop_reason_fn=_infer_stop_reason,
        message_start_event_fn=_anthropic_message_start_event,
        sse_event_fn=sse_event,
        message_delta_event_fn=_anthropic_message_delta_event,
        estimate_tool_tokens_fn=_estimate_tool_use_output_tokens,
    )


def _log_decision(request: Request, kind: str, **details: Any) -> None:
    printable = {key: value for key, value in details.items() if value is not None}
    print(
        f"[{_request_id(request)}] decision={kind} {json.dumps(printable, ensure_ascii=False)}"
    )


def _build_text_response_from_result(
    state: AnthropicTurnState,
    req: AnthropicMessagesRequest,
    normalized: NormalizedAnthropicRequest,
    result: Any,
) -> dict[str, Any]:
    return runtime_build_text_response_from_result(
        state,
        req,
        normalized,
        result,
        parse_textual_tool_use_fn=_parse_textual_tool_use,
        validated_named_tool_use_fn=_validated_named_tool_use,
        anthropic_tool_message_response_fn=_anthropic_tool_message_response,
        anthropic_text_message_response_fn=_anthropic_text_message_response,
        count_prompt_tokens_fn=_count_prompt_tokens,
    )


def _summarize_response_payload(
    payload: dict[str, Any] | StreamingResponse,
) -> dict[str, Any]:
    return summarize_response_payload(payload)


def _build_policy_denial_response(state: AnthropicTurnState) -> str:
    return build_policy_denial_response_text(state.policy_denial)


def _file_resolution_source(state: AnthropicTurnState) -> str:
    return file_resolution_source_name(
        state.explicit_user_file,
        state.last_successful_read_file,
        state.last_tool_file,
    )


async def _handle_tool_turn(
    request: Request,
    state: AnthropicTurnState,
    decision: ToolTurnDecision,
) -> dict[str, Any] | StreamingResponse:
    # If planner returned Write without content, generate content now
    tool_use = decision.tool_use
    tool_name = str(tool_use.get("name", ""))
    tool_input = tool_use.get("input", {})
    if (
        tool_name in {"Write", "Edit", "Update", "MultiEdit"}
        and isinstance(tool_input, dict)
        and tool_input.get("file_path")
        and not tool_input.get("content")
        and not tool_input.get("new_string")
    ):
        filename = tool_input.get("file_path") or state.active_filename
        _trace_event(
            request,
            "planner_generating_file_content",
            tool=tool_name,
            filename=filename,
            source=decision.source,
        )
        if tool_name == "Write":
            generated_content = await _generate_file_content_from_request(
                state, filename
            )
            if generated_content:
                tool_use = {
                    "id": tool_use.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                    "name": "Write",
                    "input": {
                        "file_path": tool_input["file_path"],
                        "content": generated_content,
                    },
                }
                decision = ToolTurnDecision(
                    tool_use=tool_use,
                    source=decision.source,
                    preamble=decision.preamble,
                )
        else:
            # For Edit/Update/MultiEdit without content, generate validated input
            tool = state.tool_map.get(tool_name)
            if isinstance(tool, dict):
                generated_input = await _generate_validated_tool_input(
                    request,
                    state,
                    tool,
                    filename=filename,
                )
                if isinstance(generated_input, dict):
                    tool_use = {
                        "id": tool_use.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                        "name": tool_name,
                        "input": generated_input,
                    }
                    decision = ToolTurnDecision(
                        tool_use=tool_use,
                        source=decision.source,
                        preamble=decision.preamble,
                    )
    input_tokens = _count_prompt_tokens(state.normalized.prompt)
    if not decision.preamble:
        decision.preamble = await _generate_tool_preamble(state, decision.tool_use)
    _log_decision(
        request,
        "tool_turn",
        source=decision.source,
        tool=decision.tool_use.get("name"),
        mode=state.normalized.mode,
        file=state.active_filename,
        preamble=decision.preamble,
    )
    if state.req.stream:
        response = _anthropic_tool_stream_response(
            f"msg_{uuid.uuid4().hex}",
            decision.tool_use,
            input_tokens,
            preamble=decision.preamble,
        )
        _trace_event(
            request, "tool_turn_response", summary=_summarize_response_payload(response)
        )
        return response
    response = _anthropic_tool_message_response(
        message_id=f"msg_{uuid.uuid4().hex}",
        tool_use=decision.tool_use,
        input_tokens=input_tokens,
        preamble=decision.preamble,
    )
    _trace_event(
        request, "tool_turn_response", summary=_summarize_response_payload(response)
    )
    return response


async def _handle_text_turn(
    request: Request,
    state: AnthropicTurnState,
) -> dict[str, Any] | StreamingResponse:
    if (
        state.current_workflow == "project_analysis"
        and state.workflow_step == "summarize"
    ):
        state.policy_denial = None
        state.missing_file_target = False
    effective_normalized = _effective_normalized_for_text_turn(state)
    effective_normalized = await _apply_context_compaction_lifecycle(
        request,
        effective_normalized,
        phase="text_turn",
    )
    _trace_event(
        request,
        "effective_prompt",
        mode=state.normalized.mode,
        workflow=state.current_workflow,
        workflow_step=state.workflow_step,
        compressed_prompt=effective_normalized.prompt != state.normalized.prompt,
        original_prompt_chars=len(state.normalized.prompt),
        effective_prompt_chars=len(effective_normalized.prompt),
    )
    if state.normalized.mode == PromptMode.SUGGESTION:
        message_id = f"msg_{uuid.uuid4().hex}"
        input_tokens = _count_prompt_tokens(effective_normalized.prompt)
        _log_decision(
            request,
            "suggestion_ignored",
            mode=state.normalized.mode,
            file=state.active_filename,
        )
        if state.req.stream:
            response = _anthropic_empty_text_stream_response(message_id, input_tokens)
            _trace_event(
                request,
                "text_turn_response",
                summary=_summarize_response_payload(response),
            )
            return response
        response = _anthropic_text_message_response(
            message_id=message_id,
            prompt=effective_normalized.prompt,
            req=state.req,
            mode=state.normalized.mode,
            output_text="",
            policy_denial=state.policy_denial,
        )
        _trace_event(
            request, "text_turn_response", summary=_summarize_response_payload(response)
        )
        return response

    (
        execute_command_short_circuit,
        execute_command_output,
    ) = await _maybe_short_circuit_execute_command_tool_result(request, state)
    if execute_command_short_circuit:
        message_id = f"msg_{uuid.uuid4().hex}"
        input_tokens = _count_prompt_tokens(effective_normalized.prompt)
        _log_decision(
            request,
            "execute_command_tool_result_short_circuit",
            mode=state.normalized.mode,
            file=state.active_filename,
        )
        if state.req.stream:
            response = _anthropic_text_stream_response_from_text(
                message_id, input_tokens, execute_command_output
            )
            _trace_event(
                request,
                "text_turn_response",
                summary=_summarize_response_payload(response),
            )
            return response
        response = _anthropic_text_message_response(
            message_id=message_id,
            prompt=effective_normalized.prompt,
            req=state.req,
            mode=state.normalized.mode,
            output_text=execute_command_output,
            policy_denial=state.policy_denial,
        )
        _trace_event(
            request, "text_turn_response", summary=_summarize_response_payload(response)
        )
        return response

    short_circuit, output_text = _should_short_circuit_tool_result(state)
    if short_circuit:
        message_id = f"msg_{uuid.uuid4().hex}"
        input_tokens = _count_prompt_tokens(effective_normalized.prompt)
        _log_decision(
            request,
            "tool_result_short_circuit",
            mode=state.normalized.mode,
            file=state.active_filename,
        )
        if state.req.stream:
            response = _anthropic_empty_text_stream_response(message_id, input_tokens)
            _trace_event(
                request,
                "text_turn_response",
                summary=_summarize_response_payload(response),
            )
            return response
        response = _anthropic_text_message_response(
            message_id=message_id,
            prompt=effective_normalized.prompt,
            req=state.req,
            mode=state.normalized.mode,
            output_text=output_text,
        )
        _trace_event(
            request, "text_turn_response", summary=_summarize_response_payload(response)
        )
        return response

    if state.current_workflow == "failure_recovery" and state.workflow_step == "stop":
        message_id = f"msg_{uuid.uuid4().hex}"
        input_tokens = _count_prompt_tokens(effective_normalized.prompt)
        output_text = _build_failure_recovery_stop_response(state)
        _log_decision(
            request,
            "failure_recovery_text_turn",
            mode=state.normalized.mode,
            file=state.active_filename,
        )
        if state.req.stream:
            response = _anthropic_text_stream_response_from_text(
                message_id, input_tokens, output_text
            )
            _trace_event(
                request,
                "text_turn_response",
                summary=_summarize_response_payload(response),
            )
            return response
        response = _anthropic_text_message_response(
            message_id=message_id,
            prompt=effective_normalized.prompt,
            req=state.req,
            mode=state.normalized.mode,
            output_text=output_text,
        )
        _trace_event(
            request, "text_turn_response", summary=_summarize_response_payload(response)
        )
        return response

    if (
        state.current_workflow == "failure_recovery"
        and state.workflow_step == "retry_once"
    ):
        message_id = f"msg_{uuid.uuid4().hex}"
        input_tokens = _count_prompt_tokens(effective_normalized.prompt)
        output_text = _build_failure_recovery_stop_response(state)
        _trace_event(
            request,
            "failure_recovery_text_forced_stop",
            mode=state.normalized.mode,
            workflow=state.current_workflow,
            workflow_step=state.workflow_step,
            last_workflow_error=state.last_workflow_error,
        )
        if state.req.stream:
            response = _anthropic_text_stream_response_from_text(
                message_id, input_tokens, output_text
            )
            _trace_event(
                request,
                "text_turn_response",
                summary=_summarize_response_payload(response),
            )
            return response
        response = _anthropic_text_message_response(
            message_id=message_id,
            prompt=effective_normalized.prompt,
            req=state.req,
            mode=state.normalized.mode,
            output_text=output_text,
        )
        _trace_event(
            request, "text_turn_response", summary=_summarize_response_payload(response)
        )
        return response

    if (
        state.current_workflow == "local_implementation"
        and state.normalized.mode == PromptMode.TOOL_RESULT
        and state.workflow_step == "blocked"
    ):
        _latest_user_text, latest_tool_name, latest_tool_payload, _latest_tool_input = (
            extract_latest_tool_exchange(state.req)
        )
        latest_tool_error = _looks_like_tool_error_payload(latest_tool_payload or "")
        if latest_tool_error:
            message_id = f"msg_{uuid.uuid4().hex}"
            input_tokens = _count_prompt_tokens(effective_normalized.prompt)
            output_text = _build_local_implementation_stop_response(state)
            _trace_event(
                request,
                "local_implementation_text_forced_stop",
                mode=state.normalized.mode,
                workflow=state.current_workflow,
                workflow_step=state.workflow_step,
                last_tool_name=latest_tool_name,
                last_workflow_error=state.last_workflow_error,
            )
            if state.req.stream:
                response = _anthropic_text_stream_response_from_text(
                    message_id, input_tokens, output_text
                )
                _trace_event(
                    request,
                    "text_turn_response",
                    summary=_summarize_response_payload(response),
                )
                return response
            response = _anthropic_text_message_response(
                message_id=message_id,
                prompt=effective_normalized.prompt,
                req=state.req,
                mode=state.normalized.mode,
                output_text=output_text,
            )
            _trace_event(
                request,
                "text_turn_response",
                summary=_summarize_response_payload(response),
            )
            return response

    if state.policy_denial:
        message_id = f"msg_{uuid.uuid4().hex}"
        input_tokens = _count_prompt_tokens(effective_normalized.prompt)
        output_text = _build_policy_denial_response(state)
        _log_decision(
            request,
            "policy_denial_text_turn",
            mode=state.normalized.mode,
            file=state.active_filename,
            reason=state.policy_denial,
        )
        if state.req.stream:
            response = _anthropic_text_stream_response_from_text(
                message_id, input_tokens, output_text
            )
            _trace_event(
                request,
                "text_turn_response",
                summary=_summarize_response_payload(response),
            )
            return response
        response = _anthropic_text_message_response(
            message_id=message_id,
            prompt=effective_normalized.prompt,
            req=state.req,
            mode=state.normalized.mode,
            output_text=output_text,
            policy_denial=state.policy_denial,
        )
        _trace_event(
            request, "text_turn_response", summary=_summarize_response_payload(response)
        )
        return response

    params = _build_generation_params(state)
    multimodal_messages = _extract_multimodal_messages(state.req, effective_normalized)
    _log_decision(
        request,
        "text_turn",
        mode=state.normalized.mode,
        max_tokens=params.max_tokens,
        has_tools=bool(state.normalized.oai_body.get("tools")),
        file=state.active_filename,
        has_images=multimodal_messages is not None,
    )
    if state.req.stream:
        response = _anthropic_text_stream_response(
            state,
            state.req,
            effective_normalized,
            params,
            multimodal_messages=multimodal_messages,
        )
        _trace_event(
            request,
            "text_turn_stream",
            params=asdict(params),
            compressed_prompt=effective_normalized.prompt != state.normalized.prompt,
            prompt_chars=len(effective_normalized.prompt),
            has_images=multimodal_messages is not None,
        )
        return response

    if (
        torch.cuda.is_available()
        and state.current_workflow == "project_analysis"
        and state.workflow_step == "summarize"
    ):
        torch.cuda.empty_cache()
        _trace_event(
            request,
            "cuda_empty_cache",
            workflow=state.current_workflow,
            workflow_step=state.workflow_step,
        )

    gen_kwargs: dict[str, Any] = {}
    if (
        multimodal_messages is not None
        and hasattr(GENERATOR, "backend")
        and GENERATOR.backend == "gguf"
    ):
        gen_kwargs["messages"] = multimodal_messages
    result = await GENERATOR.generate(effective_normalized.prompt, params, **gen_kwargs)
    _trace_event(
        request,
        "text_generation_result",
        stats=asdict(result.stats),
        raw_output=result.output_text,
        compressed_prompt=effective_normalized.prompt != state.normalized.prompt,
        prompt_chars=len(effective_normalized.prompt),
        has_images=multimodal_messages is not None,
    )
    response = _build_text_response_from_result(
        state, state.req, effective_normalized, result
    )
    if response.get("stop_reason") == "tool_use":
        _log_decision(
            request,
            "text_salvaged_to_tool",
            tool=response["content"][0].get("name"),
            mode=state.normalized.mode,
            file=state.active_filename,
        )
    _trace_event(
        request, "text_turn_response", summary=_summarize_response_payload(response)
    )
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok" if SERVER_READY else "loading"}


@app.get("/v1/health")
async def v1_health() -> dict[str, str]:
    return {"status": "ok" if SERVER_READY else "loading"}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "owned_by": "local",
            }
        ],
    }


@app.post("/v1/messages/count_tokens")
async def count_tokens(
    request: Request,
    req: CountTokensRequest,
    anthropic_version: str | None = Header(default=None),
    anthropic_beta: str | None = Header(default=None),
) -> dict[str, int]:
    raw_payload = await request.json()
    _log_anthropic_request(
        {
            "ts": time.time(),
            "kind": "count_tokens",
            "path": str(request.url.path),
            "query": dict(request.query_params),
            "anthropic_version": anthropic_version,
            "anthropic_beta": anthropic_beta,
            "payload": truncate_for_log(raw_payload),
        }
    )

    normalized = _normalize_anthropic_request(
        AnthropicMessagesRequest(
            model=req.model,
            system=req.system,
            messages=req.messages,
            max_tokens=32000,
        )
    )
    normalized = await _apply_context_compaction_lifecycle(
        request, normalized, phase="count_tokens"
    )
    return {"input_tokens": _count_prompt_tokens(normalized.prompt)}


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest) -> dict[str, Any]:
    if GENERATOR is None:
        raise HTTPException(status_code=503, detail="Model is not loaded yet")
    if req.stream:
        raise HTTPException(
            status_code=400, detail="stream=true is not supported by this server yet"
        )
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    prompt = build_prompt(
        GENERATOR.tokenizer, [m.model_dump() for m in req.messages], False
    )
    params = GenerationParams(
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        top_p=req.top_p,
        top_k=req.top_k,
        repetition_penalty=req.repetition_penalty,
    )
    result = await GENERATOR.generate(prompt, params)
    created = int(time.time())
    usage = _usage_dict(prompt, result.stats.num_tokens)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created,
        "model": MODEL_NAME,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result.output_text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"],
        },
        "x_paroquant_stats": asdict(result.stats),
    }


@app.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    req: AnthropicMessagesRequest,
    anthropic_version: str | None = Header(default=None),
    anthropic_beta: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    raw_payload = await request.json()
    _log_anthropic_request(
        {
            "ts": time.time(),
            "path": str(request.url.path),
            "query": dict(request.query_params),
            "anthropic_version": anthropic_version,
            "anthropic_beta": anthropic_beta,
            "has_api_key": bool(x_api_key),
            "payload": truncate_for_log(raw_payload),
        }
    )
    if GENERATOR is None:
        raise HTTPException(status_code=503, detail="Model is not loaded yet")
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    normalized = _normalize_anthropic_request(req)
    normalized = await _apply_context_compaction_lifecycle(
        request, normalized, phase="request"
    )
    _trace_event(
        request,
        "normalized_request",
        normalized=_summarize_normalized_request(normalized),
    )
    state = _build_turn_state(req, normalized)
    await _maybe_start_initial_workflow(request, state)
    await _maybe_attach_project_analysis_workflow(request, state)
    base_dir = resolution_base_dir(
        state.last_successful_read_file,
        state.last_tool_file,
        state.project_root,
        state.current_shell_cwd,
    )
    state.active_filename = sanitize_local_path_candidate(state.active_filename)
    resolved_path = (
        resolve_local_path_if_valid(state.active_filename, base_dir=base_dir)
        if state.active_filename
        else None
    )
    state.resolved_active_path = str(resolved_path) if resolved_path else None
    try:
        state.active_file_exists = bool(resolved_path and resolved_path.exists())
    except (OSError, PermissionError):
        state.active_file_exists = False
    _trace_event(
        request,
        "file_resolution",
        source=_file_resolution_source(state),
        explicit_user_file=state.explicit_user_file,
        explicit_user_absolute_path=state.explicit_user_absolute_path,
        last_tool_file=state.last_tool_file,
        last_successful_read_file=state.last_successful_read_file,
        current_shell_cwd=state.current_shell_cwd,
        project_root=state.project_root,
        temporary_allowed_root=state.temporary_allowed_root,
        base_dir=str(base_dir),
        active_filename=state.active_filename,
        resolved_active_path=state.resolved_active_path,
        active_file_exists=state.active_file_exists,
    )
    if not state.policy_denial:
        tool_decision = await _resolve_tool_turn(request, state)
        if tool_decision is not None:
            return await _handle_tool_turn(request, state, tool_decision)

    try:
        return await _handle_text_turn(request, state)
    except Exception as exc:
        _trace_event(
            request, "text_turn_error", error=str(exc), error_type=type(exc).__name__
        )
        raise HTTPException(
            status_code=502, detail=f"Generation failed: {exc}"
        ) from exc


def main() -> None:
    global \
        MODEL_PATH, \
        MODEL_NAME, \
        DEBUG_TRACE, \
        MODEL_BACKEND, \
        LLAMA_SERVER_BIN, \
        LLAMA_SERVER_HOST, \
        LLAMA_SERVER_PORT, \
        LLAMA_CTX_SIZE, \
        LLAMA_GPU_LAYERS, \
        LLAMA_SERVER_EXTRA_ARGS, \
        LLAMA_AUTODETECT_MMPROJ, \
        LLAMA_SERVER_COMMAND

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--backend",
        choices=["auto", "transformers", "gguf", "openrouter"],
        default="auto",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--llama-server-bin", default="llama-server")
    parser.add_argument("--llama-server-host", default="127.0.0.1")
    parser.add_argument("--llama-server-port", type=int, default=8011)
    parser.add_argument("--llama-ctx-size", type=int, default=32768)
    parser.add_argument("--llama-gpu-layers", type=int, default=999)
    parser.add_argument("--llama-server-extra-args", default="")
    parser.add_argument("--no-llama-autodetect-mmproj", action="store_true")
    parser.add_argument("--llama-server-command", default="")
    parser.add_argument("--debug-trace", action="store_true")
    args = parser.parse_args()

    from factory.api_server_core.request_auth import KEY_ENV
    if args.host not in {'127.0.0.1', 'localhost', '::1'} and not os.environ.get(KEY_ENV, '').strip():
        parser.error(f'{KEY_ENV} is required for a non-loopback listener')

    MODEL_PATH = (
        args.model if args.backend == "openrouter" else os.path.abspath(args.model)
    )
    MODEL_NAME = (
        args.model
        if args.backend == "openrouter"
        else os.path.basename(MODEL_PATH.rstrip("/"))
    )
    MODEL_BACKEND = resolve_runtime_backend(MODEL_PATH, args.backend)
    LLAMA_SERVER_BIN = args.llama_server_bin
    LLAMA_SERVER_HOST = args.llama_server_host
    LLAMA_SERVER_PORT = args.llama_server_port
    LLAMA_CTX_SIZE = args.llama_ctx_size
    LLAMA_GPU_LAYERS = args.llama_gpu_layers
    LLAMA_SERVER_EXTRA_ARGS = shlex.split(args.llama_server_extra_args or "")
    LLAMA_AUTODETECT_MMPROJ = not args.no_llama_autodetect_mmproj
    LLAMA_SERVER_COMMAND = shlex.split(args.llama_server_command or "")
    DEBUG_TRACE = args.debug_trace or os.environ.get("PARO_DEBUG_TRACE") == "1"
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
