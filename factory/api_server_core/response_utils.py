import json
from typing import Any


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def count_text_tokens(text: str, *, encode: Any) -> int:
    if not text:
        return 0
    return len(encode(text, add_special_tokens=False))


def count_prompt_tokens(prompt: str, *, encode: Any) -> int:
    return count_text_tokens(prompt, encode=encode)


def estimate_tool_use_output_tokens(tool_use: dict[str, Any], *, preamble: str = "", encode: Any) -> int:
    tool_payload = json.dumps(
        {
            "type": "tool_use",
            "id": tool_use.get("id", ""),
            "name": tool_use.get("name", ""),
            "input": tool_use.get("input", {}),
        },
        ensure_ascii=False,
    )
    combined = "\n".join(part for part in (preamble.strip(), tool_payload) if part)
    return max(1, count_text_tokens(combined, encode=encode))


def anthropic_message_base(
    *,
    message_id: str,
    input_tokens: int,
    model_name: str,
    output_tokens: int = 0,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def anthropic_message_start_event(
    *,
    message_id: str,
    input_tokens: int,
    model_name: str,
    output_tokens: int = 0,
) -> str:
    return sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": anthropic_message_base(
                message_id=message_id,
                input_tokens=input_tokens,
                model_name=model_name,
                output_tokens=output_tokens,
            ),
        },
    )


def anthropic_message_delta_event(
    *,
    stop_reason: str,
    output_tokens: int,
    stop_sequence: str | None = None,
) -> str:
    return sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": stop_sequence},
            "usage": {"output_tokens": output_tokens},
        },
    )


def anthropic_tool_message_response(
    *,
    message_id: str,
    tool_use: dict[str, Any],
    input_tokens: int,
    model_name: str,
    estimate_tool_tokens: Any,
    preamble: str = "",
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if preamble:
        content.append({"type": "text", "text": preamble})
    content.append(
        {
            "type": "tool_use",
            "id": tool_use["id"],
            "name": tool_use["name"],
            "input": tool_use["input"],
        }
    )
    output_tokens = estimate_tool_tokens(tool_use, preamble=preamble)
    payload = {
        **anthropic_message_base(
            message_id=message_id,
            input_tokens=input_tokens,
            model_name=model_name,
            output_tokens=output_tokens,
        ),
        "content": content,
        "stop_reason": "tool_use",
        "stop_sequence": None,
    }
    if stats is not None:
        payload["x_paroquant_stats"] = stats
    return payload


def anthropic_text_message_response(
    *,
    message_id: str,
    input_tokens: int,
    output_text: str,
    model_name: str,
    infer_stop_reason_fn: Any,
    count_text_tokens_fn: Any,
    max_tokens: int,
    stop_reason: str | None = None,
    stop_sequence: str | None = None,
    stats: dict[str, Any] | None = None,
    policy_denial: str | None = None,
) -> dict[str, Any]:
    output_tokens = count_text_tokens_fn(output_text)
    resolved_stop_reason = stop_reason or infer_stop_reason_fn(output_tokens, max_tokens)
    payload = {
        **anthropic_message_base(
            message_id=message_id,
            input_tokens=input_tokens,
            model_name=model_name,
            output_tokens=output_tokens,
        ),
        "content": [{"type": "text", "text": output_text}],
        "stop_reason": resolved_stop_reason,
        "stop_sequence": stop_sequence,
    }
    if stats is not None:
        payload["x_paroquant_stats"] = stats
    if policy_denial is not None:
        payload["policy_denial"] = policy_denial
    return payload


def summarize_response_payload(payload: Any) -> dict[str, Any]:
    media_type = getattr(payload, "media_type", None)
    if media_type == "text/event-stream":
        return {"type": "streaming_response"}
    summary: dict[str, Any] = {
        "stop_reason": payload.get("stop_reason"),
        "content_types": [block.get("type") for block in payload.get("content", []) if isinstance(block, dict)],
    }
    if payload.get("content"):
        summary["content"] = payload.get("content")
    return summary
