"""Shared approval continuation independent of any terminal frontend."""
from typing import Any

def _approval_context_for_resume(messages: list) -> list[dict]:
    """Keep tool protocol context, but do not replay assistant preambles."""
    cleaned: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in {"assistant", "user"} or not isinstance(content, list):
            continue
        if role != "assistant":
            cleaned.append({"role": role, "content": list(content)})
            continue

        has_tool_use = any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in content
        )
        if not has_tool_use:
            cleaned.append({"role": role, "content": list(content)})
            continue

        stripped_content = [
            block
            for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") == "text"
                and str(block.get("text") or "").strip()
            )
        ]
        if stripped_content:
            cleaned.append({"role": role, "content": stripped_content})
    return cleaned


def _forced_tool_call_for_resume(
    blocked: dict,
    approval_context_messages: list,
) -> dict | None:
    tn = str(blocked.get("tool") or "?")
    src = str(blocked.get("source") or "").strip()
    bi = blocked.get("input")
    if not isinstance(bi, dict):
        return None

    ftc: dict[str, Any] = {"name": tn, "input": bi, "source": src}
    for key in (
        "tool_use_id",
        "reasoning_content",
        "reason",
        "pending_tool_calls",
    ):
        if key in blocked:
            ftc[key] = blocked[key]
    source_context = approval_context_messages
    if not source_context:
        raw_context = blocked.get("approval_context_messages")
        source_context = raw_context if isinstance(raw_context, list) else []
    sanitized_context = _approval_context_for_resume(source_context)
    if sanitized_context:
        ftc["approval_context_messages"] = sanitized_context
    return ftc
