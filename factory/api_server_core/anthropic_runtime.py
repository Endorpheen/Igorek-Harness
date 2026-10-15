import json
import uuid
from dataclasses import asdict
from typing import Any

import torch
from fastapi.responses import StreamingResponse

from paroquant.inference import GenerationParams
from factory.api_server_core.generator_runtime import ContextOverflowError


def build_generation_params(state, *, file_investigation_has_enough_context_fn) -> GenerationParams:
    max_tokens = state.req.max_tokens
    mode = state.normalized.mode
    if mode == "suggestion":
        max_tokens = min(max_tokens, 32000)
    elif mode == "startup_reminder" and not state.requires_tool:
        max_tokens = min(max_tokens, 32000)
    elif state.requires_tool:
        max_tokens = min(max_tokens, 32000)
    if state.current_workflow == "project_analysis" and state.workflow_step == "summarize":
        max_tokens = min(max_tokens, 768)
    if state.current_workflow == "file_investigation" and file_investigation_has_enough_context_fn(state):
        max_tokens = min(max_tokens, 2500)
    return GenerationParams(max_tokens=max_tokens, temperature=state.req.temperature, top_p=state.req.top_p)


def request_stop_sequences(req) -> list[str]:
    extra = req.model_extra or {}
    value = extra.get("stop_sequences")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def split_request_stop_sequence(text: str, req, *, split_stop_sequence_fn):
    return split_stop_sequence_fn(text, request_stop_sequences(req))


def anthropic_tool_stream_response(message_id: str, tool_use: dict[str, Any], input_tokens: int, *, preamble: str, message_start_event_fn, sse_event_fn, message_delta_event_fn, estimate_tool_tokens_fn) -> StreamingResponse:
    async def tool_stream():
        yield message_start_event_fn(message_id=message_id, input_tokens=input_tokens, output_tokens=estimate_tool_tokens_fn(tool_use, preamble))
        tool_index = 0
        if preamble:
            yield sse_event_fn("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
            yield sse_event_fn("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": preamble}})
            yield sse_event_fn("content_block_stop", {"type": "content_block_stop", "index": 0})
            tool_index = 1
        yield sse_event_fn("content_block_start", {"type": "content_block_start", "index": tool_index, "content_block": {"type": "tool_use", "id": tool_use["id"], "name": tool_use["name"], "input": {}}})
        yield sse_event_fn("content_block_delta", {"type": "content_block_delta", "index": tool_index, "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool_use["input"], ensure_ascii=False)}})
        yield sse_event_fn("content_block_stop", {"type": "content_block_stop", "index": tool_index})
        yield message_delta_event_fn(stop_reason="tool_use", output_tokens=estimate_tool_tokens_fn(tool_use, preamble))
        yield sse_event_fn("message_stop", {"type": "message_stop"})
    return StreamingResponse(tool_stream(), media_type="text/event-stream")


def anthropic_empty_text_stream_response(message_id: str, input_tokens: int, *, message_start_event_fn, message_delta_event_fn, sse_event_fn) -> StreamingResponse:
    async def empty_stream():
        yield message_start_event_fn(message_id=message_id, input_tokens=input_tokens)
        yield message_delta_event_fn(stop_reason="end_turn", output_tokens=0)
        yield sse_event_fn("message_stop", {"type": "message_stop"})
    return StreamingResponse(empty_stream(), media_type="text/event-stream")


def anthropic_text_stream_response_from_text(message_id: str, input_tokens: int, output_text: str, *, message_start_event_fn, sse_event_fn, message_delta_event_fn, count_text_tokens_fn, stop_reason: str = "end_turn", stop_sequence: str | None = None) -> StreamingResponse:
    async def text_stream():
        output_tokens = count_text_tokens_fn(output_text)
        yield message_start_event_fn(message_id=message_id, input_tokens=input_tokens)
        yield sse_event_fn("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        if output_text:
            yield sse_event_fn("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": output_text}})
        yield sse_event_fn("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield message_delta_event_fn(stop_reason=stop_reason, output_tokens=output_tokens, stop_sequence=stop_sequence)
        yield sse_event_fn("message_stop", {"type": "message_stop"})
    return StreamingResponse(text_stream(), media_type="text/event-stream")


def anthropic_text_stream_response(state, req, normalized, params, *, generator, response_state_cls, request, parse_textual_tool_use_fn, validated_named_tool_use_fn, split_request_stop_sequence_fn, extract_visible_delta_fn, sanitize_output_for_mode_fn, extract_latest_tool_exchange_fn, is_tavily_search_command_fn, looks_like_tool_error_payload_fn, trace_event_fn, build_policy_denial_response_fn, count_prompt_tokens_fn, count_text_tokens_fn, infer_stop_reason_fn, message_start_event_fn, sse_event_fn, message_delta_event_fn, estimate_tool_tokens_fn, multimodal_messages=None) -> StreamingResponse:
    response_state = response_state_cls(message_id=f"msg_{uuid.uuid4().hex}", input_tokens=count_prompt_tokens_fn(normalized.prompt))
    suppress_tool_parsing = (
        state.current_workflow == "project_analysis"
        or (state.current_workflow == "tavily_research" and state.workflow_step == "summarize")
        or (state.current_workflow == "session_recall" and state.workflow_step == "summarize")
    )
    stream_stop_reason = "end_turn"
    stream_stop_sequence: str | None = None

    async def event_stream():
        nonlocal stream_stop_reason, stream_stop_sequence
        yield message_start_event_fn(message_id=response_state.message_id, input_tokens=response_state.input_tokens)
        yield sse_event_fn("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        yield sse_event_fn("ping", {"type": "ping"})

        try:
          async for chunk in generator.stream_generate(normalized.prompt, params, messages=multimodal_messages):
            previous_emitted = response_state.emitted_output
            response_state.raw_output += chunk
            validated_tool_use = None
            if not suppress_tool_parsing:
                parsed_tool_use = parse_textual_tool_use_fn(response_state.raw_output)
                validated_tool_use = validated_named_tool_use_fn(req, state, parsed_tool_use)
                if validated_tool_use:
                    continue
            raw_output_lower = response_state.raw_output.lower()
            if (
                state.policy_denial
                or "[tool_use" in response_state.raw_output
                or "[tool_result" in response_state.raw_output
                or "<tool_call>" in raw_output_lower
                or "<|tool_call|>" in raw_output_lower
                or "<function=" in raw_output_lower
            ):
                continue
            visible_source, matched_stop_sequence, stop_hit = split_request_stop_sequence_fn(response_state.raw_output, req)
            if stop_hit:
                stream_stop_reason = "stop_sequence"
                stream_stop_sequence = matched_stop_sequence
            response_state.emitted_output, visible_delta = extract_visible_delta_fn(visible_source, previous_emitted, normalized.mode)
            if visible_delta:
                response_state.started_text_stream = True
                yield sse_event_fn("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": visible_delta}})
            if stop_hit:
                break

        except ContextOverflowError as exc:
            error_text = "Контекст превышает лимит модели. Попробуйте начать новую сессию или сократить запрос."
            yield sse_event_fn("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": error_text}})
            yield sse_event_fn("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield message_delta_event_fn(stop_reason="end_turn", output_tokens=len(error_text) // 4)
            yield sse_event_fn("message_stop", {"type": "message_stop"})
            return
        except RuntimeError as exc:
            error_text = f"Ошибка генерации: {exc}"
            yield sse_event_fn("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": error_text}})
            yield sse_event_fn("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield message_delta_event_fn(stop_reason="end_turn", output_tokens=len(error_text) // 4)
            yield sse_event_fn("message_stop", {"type": "message_stop"})
            return

        validated_tool_use = None
        if not suppress_tool_parsing:
            parsed_tool_use = parse_textual_tool_use_fn(response_state.raw_output)
            validated_tool_use = validated_named_tool_use_fn(req, state, parsed_tool_use)
        if validated_tool_use and not response_state.started_text_stream:
            yield sse_event_fn("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield sse_event_fn("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": validated_tool_use["id"], "name": validated_tool_use["name"], "input": {}}})
            yield sse_event_fn("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": json.dumps(validated_tool_use["input"], ensure_ascii=False)}})
            yield sse_event_fn("content_block_stop", {"type": "content_block_stop", "index": 1})
            yield message_delta_event_fn(stop_reason="tool_use", output_tokens=estimate_tool_tokens_fn(validated_tool_use))
            yield sse_event_fn("message_stop", {"type": "message_stop"})
            return

        if state.policy_denial:
            output_text = build_policy_denial_response_fn(state)
        elif stream_stop_reason == "stop_sequence":
            output_text = response_state.emitted_output.strip()
        else:
            output_text = response_state.emitted_output.strip() or sanitize_output_for_mode_fn(response_state.raw_output, normalized.mode)
        _user_text, latest_tool_name, latest_tool_result, latest_tool_input_obj = extract_latest_tool_exchange_fn(state.req)
        latest_command = str(latest_tool_input_obj.get("command", "")).strip() if latest_tool_name == "Bash" else ""
        if normalized.mode == "tool_result" and latest_tool_name == "Bash" and is_tavily_search_command_fn(latest_command) and looks_like_tool_error_payload_fn(latest_tool_result) and not validated_tool_use:
            output_text = ""
            trace_event_fn(request, "tavily_error_text_suppressed", mode=normalized.mode)
        output_tokens = count_text_tokens_fn(output_text)
        yield sse_event_fn("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield message_delta_event_fn(stop_reason=stream_stop_reason if stream_stop_reason == "stop_sequence" else infer_stop_reason_fn(output_tokens, req.max_tokens), output_tokens=output_tokens, stop_sequence=stream_stop_sequence)
        yield sse_event_fn("message_stop", {"type": "message_stop"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def build_text_response_from_result(state, req, normalized, result, *, parse_textual_tool_use_fn, validated_named_tool_use_fn, anthropic_tool_message_response_fn, anthropic_text_message_response_fn, count_prompt_tokens_fn):
    validated_tool_use = None
    if state.current_workflow != "project_analysis" and not (state.current_workflow == "tavily_research" and state.workflow_step == "summarize") and not (state.current_workflow == "session_recall" and state.workflow_step == "summarize"):
        parsed_tool_use = parse_textual_tool_use_fn(result.output_text)
        validated_tool_use = validated_named_tool_use_fn(req, state, parsed_tool_use)
    if validated_tool_use is not None and state.current_workflow != "project_analysis" and not (state.current_workflow == "tavily_research" and state.workflow_step == "summarize") and not (state.current_workflow == "session_recall" and state.workflow_step == "summarize"):
        return anthropic_tool_message_response_fn(message_id=f"msg_{uuid.uuid4().hex}", tool_use=validated_tool_use, input_tokens=count_prompt_tokens_fn(normalized.prompt), stats=asdict(result.stats))
    output_text = result.output_text
    return anthropic_text_message_response_fn(message_id=f"msg_{uuid.uuid4().hex}", prompt=normalized.prompt, req=req, mode=normalized.mode, output_text=output_text, stats=asdict(result.stats))
