import json
from typing import Any, Callable

from .prompts import tool_result_system_prompt, tool_result_user_prompt


def flatten_block_content(content: str | list[dict[str, Any]] | None, *, role: str) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for block in content:
        block_type = block.get("type", "text")
        if block_type == "text":
            text = block.get("text", "")
            if text:
                parts.append(text)
        elif block_type == "tool_result":
            tool_text = flatten_block_content(block.get("content"), role=role)
            tool_use_id = block.get("tool_use_id", "")
            is_error = block.get("is_error", False)
            prefix = f"[tool_result id={tool_use_id}"
            if is_error:
                prefix += " error=true"
            prefix += "]"
            parts.append(f"{prefix}\n{tool_text}" if tool_text else prefix)
        elif block_type == "tool_use":
            name = block.get("name", "tool")
            tool_input = block.get("input", {})
            tool_id = block.get("id", "")
            parts.append(
                f"[tool_use id={tool_id} name={name} input={json.dumps(tool_input, ensure_ascii=False)}]"
            )
        elif block_type == "image":
            source = block.get("source", {})
            media_type = source.get("media_type", block.get("media_type", "image"))
            parts.append(f"[image omitted media_type={media_type}]")
        elif block_type == "document":
            source = block.get("source", {})
            media_type = source.get("media_type", block.get("media_type", "document"))
            parts.append(f"[document omitted media_type={media_type}]")
        elif block_type in {"thinking", "redacted_thinking", "signature"}:
            continue
        else:
            text = block.get("text") or block.get("content")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(part for part in parts if part).strip()


def strip_system_reminder(text: str) -> str:
    text = text.replace("<system-reminder>", "").replace("</system-reminder>", "")
    return text.strip()


def extract_embedded_suggestion_draft(text: str) -> str:
    marker = "[SUGGESTION MODE:"
    if marker not in text:
        return ""

    prefix = text.split(marker, 1)[0]
    lines: list[str] = []
    skip_prefixes = (
        "[tool_result ",
        "original user request:",
        "latest tool used:",
        "tool result:",
        "edited file:",
        "applied change:",
        "old:",
        "new:",
        "file created successfully at:",
        "the file ",
    )

    for raw_line in prefix.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if stripped.startswith("<system-reminder>"):
            continue
        if any(lowered.startswith(prefix) for prefix in skip_prefixes):
            continue
        if lowered.startswith("wrote ") and " lines to " in lowered:
            continue
        lines.append(stripped)

    if not lines:
        return ""

    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)
    return deduped[-1] if deduped else ""


def is_suggestion_mode_request(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("[SUGGESTION MODE:")


def has_embedded_tool_result_marker(text: str) -> bool:
    return "[tool_result " in text


def last_user_message(req: Any) -> Any | None:
    for msg in reversed(req.messages):
        if msg.role == "user":
            return msg
    return None


def is_pure_suggestion_user_message(msg: Any | None) -> bool:
    if msg is None or getattr(msg, "role", None) != "user":
        return False

    content = msg.content
    if isinstance(content, str):
        return is_suggestion_mode_request(content.strip())

    saw_visible_text = False
    for block in content:
        block_type = block.get("type")
        if block_type == "tool_result":
            return False
        if block_type != "text":
            continue
        text = str(block.get("text", "")).strip()
        if not text or text.startswith("<system-reminder>"):
            continue
        saw_visible_text = True
        if not is_suggestion_mode_request(text):
            return False
    return saw_visible_text


def last_actionable_user_message(req: Any) -> Any | None:
    for msg in reversed(req.messages):
        if msg.role != "user":
            continue
        if is_pure_suggestion_user_message(msg):
            continue
        return msg
    return None


def extract_last_user_visible_text_block(msg: Any | None) -> str:
    if msg is None:
        return ""

    content = msg.content
    if isinstance(content, str):
        return content.strip()

    for block in reversed(content):
        if block.get("type") != "text":
            continue
        text = str(block.get("text", "")).strip()
        if not text or text.startswith("<system-reminder>"):
            continue
        return text
    return ""


def is_tool_result_only_user_turn(msg: Any) -> bool:
    content = msg.content
    if isinstance(content, str):
        text = content.strip()
        return bool(text) and has_embedded_tool_result_marker(text)

    saw_tool_result = False
    for block in content:
        block_type = block.get("type")
        if block_type == "tool_result":
            saw_tool_result = True
            continue
        if block_type == "text":
            text = str(block.get("text", "")).strip()
            if not text or text.startswith("<system-reminder>"):
                continue
            return False
    return saw_tool_result


def detect_prompt_mode(req: Any, prompt_mode: Any) -> str:
    if not req.messages:
        return prompt_mode.NORMAL

    raw_last_user = last_user_message(req)
    if raw_last_user and is_tool_result_only_user_turn(raw_last_user):
        return prompt_mode.TOOL_RESULT

    raw_last_user_text = extract_last_user_visible_text_block(raw_last_user)

    if is_suggestion_mode_request(raw_last_user_text):
        if extract_embedded_suggestion_draft(raw_last_user_text):
            return prompt_mode.NORMAL
        return prompt_mode.SUGGESTION

    has_assistant_turn = any(msg.role == "assistant" for msg in req.messages)
    if has_assistant_turn:
        return prompt_mode.NORMAL

    for msg in req.messages:
        content = msg.content
        if isinstance(content, str):
            continue
        for block in content:
            if block.get("type") == "text" and str(block.get("text", "")).lstrip().startswith("<system-reminder>"):
                return prompt_mode.STARTUP_REMINDER
    return prompt_mode.NORMAL


def extract_last_user_text(req: Any) -> str:
    msg = last_actionable_user_message(req)
    if msg is None:
        return ""

    content = msg.content
    if isinstance(content, str):
        return content.strip()

    for block in reversed(content):
        if block.get("type") != "text":
            continue
        text = str(block.get("text", "")).strip()
        if text and not text.startswith("<system-reminder>"):
            return text
    return ""


def extract_latest_user_request_text(req: Any) -> str:
    saw_tool_chain = False

    for msg in reversed(req.messages):
        if msg.role == "assistant":
            content = msg.content
            if isinstance(content, str):
                if content.strip():
                    saw_tool_chain = True
                continue
            for block in content:
                if block.get("type") == "tool_use":
                    saw_tool_chain = True
                    break
            continue

        if msg.role != "user":
            continue

        if is_pure_suggestion_user_message(msg):
            continue

        if is_tool_result_only_user_turn(msg):
            saw_tool_chain = True
            continue

        text = extract_last_user_visible_text_block(msg)
        if not text:
            continue
        return text if saw_tool_chain else text

    return extract_last_user_text(req)


def extract_latest_tool_exchange(req: Any) -> tuple[str, str, str, dict[str, Any]]:
    """Pair the latest result with its preceding call, retaining legacy id-less input."""
    selected_result = None
    result_index = len(req.messages)
    for index in range(len(req.messages) - 1, -1, -1):
        msg = req.messages[index]
        if msg.role != "user" or isinstance(msg.content, str):
            continue
        selected_result = next((block for block in reversed(msg.content)
                                if block.get("type") == "tool_result"), None)
        if selected_result is not None:
            result_index = index
            break

    result_id = str(selected_result.get("tool_use_id") or "") if selected_result is not None else ""
    tool_name, tool_input = "", ""
    tool_input_obj: dict[str, Any] = {}
    for msg in reversed(req.messages[:result_index]):
        if msg.role != "assistant" or isinstance(msg.content, str):
            continue
        for block in reversed(msg.content):
            if block.get("type") != "tool_use" or result_id and str(block.get("id", "")) != result_id:
                continue
            tool_name = str(block.get("name", "tool"))
            raw_input = block.get("input", {})
            tool_input_obj = raw_input if isinstance(raw_input, dict) else {}
            tool_input = json.dumps(tool_input_obj, ensure_ascii=False)
            break
        if tool_name:
            break

    user_text = extract_latest_user_request_text(req)
    payload = (flatten_block_content([selected_result], role="user")
               if selected_result is not None else tool_input)
    return user_text, tool_name, payload, tool_input_obj


def extract_tool_targets(req: Any, tool_names: set[str]) -> list[str]:
    targets: list[str] = []
    for msg in req.messages:
        if msg.role != "assistant" or isinstance(msg.content, str):
            continue
        for block in msg.content:
            if block.get("type") != "tool_use":
                continue
            name = str(block.get("name", ""))
            if name not in tool_names:
                continue
            raw_input = block.get("input", {})
            if not isinstance(raw_input, dict):
                continue
            for key in ("file_path", "path", "notebook_path"):
                value = raw_input.get(key)
                if isinstance(value, str) and value:
                    targets.append(value)
                    break
    return targets


def build_tool_result_summary(tool_name: str, tool_result: str, tool_input_obj: dict[str, Any]) -> str:
    summary = tool_result.strip() or "Tool completed successfully."
    if summary == "(Bash completed with no output)":
        summary = "Tool completed successfully."
    if tool_name != "Edit":
        return summary

    file_path = str(tool_input_obj.get("file_path", "")).strip()
    old_string = str(tool_input_obj.get("old_string", ""))
    new_string = str(tool_input_obj.get("new_string", ""))
    if not old_string or new_string == "":
        return summary

    parts = [summary]
    if file_path:
        parts.append(f"Edited file: {file_path}")
    parts.append("Applied change:")
    parts.append(f"OLD:\n{old_string}")
    parts.append(f"NEW:\n{new_string}")
    return "\n\n".join(parts)


def extract_visible_message_content(msg: Any) -> str:
    if isinstance(msg.content, str):
        text = msg.content.strip()
        if "[SUGGESTION MODE:" in text:
            draft = extract_embedded_suggestion_draft(text)
            return draft
        return text

    visible_parts: list[str] = []
    for block in msg.content:
        block_type = block.get("type", "text")
        if block_type == "text":
            text = str(block.get("text", ""))
            if not text:
                continue
            if text.lstrip().startswith("<system-reminder>"):
                continue
            if "[SUGGESTION MODE:" in text:
                draft = extract_embedded_suggestion_draft(text)
                if draft:
                    visible_parts.append(draft)
                continue
            visible_parts.append(text)
            continue

        if block_type in {"thinking", "redacted_thinking", "signature"}:
            continue

        flattened = flatten_block_content([block], role=msg.role)
        if flattened:
            visible_parts.append(flattened)

    return "\n\n".join(part for part in visible_parts if part).strip()


def extract_anthropic_messages(
    req: Any,
    mode: str,
    *,
    prompt_mode: Any,
    tools_prompt: str,
) -> list[dict[str, str]]:
    if mode == prompt_mode.SUGGESTION:
        last_text = ""
        if req.messages:
            content = req.messages[-1].content
            last_text = content if isinstance(content, str) else flatten_block_content(content, role=req.messages[-1].role)
        return [
            {
                "role": "system",
                "content": (
                    "You are generating a very short suggestion for Claude Code input completion. "
                    "Return either an empty string or a short suggestion of 2 to 8 words. "
                    "Do not explain. Do not include role labels. Do not quote the suggestion."
                ),
            },
            {"role": "user", "content": last_text},
        ]

    if mode == prompt_mode.TOOL_RESULT:
        system_content = tool_result_system_prompt()
        if tools_prompt:
            system_content += "\n\n" + tools_prompt
        # Preserve full conversation history instead of stripping to 2 messages.
        user_text, tool_name, tool_payload, tool_input_obj = extract_latest_tool_exchange(req)
        tool_summary = build_tool_result_summary(tool_name, tool_payload, tool_input_obj)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content},
        ]
        for msg in req.messages:
            content = extract_visible_message_content(msg)
            if content:
                messages.append({"role": msg.role, "content": content})
        # Append tool result context to the last user message if not already present
        tool_ctx = tool_result_user_prompt(user_text, tool_name, tool_summary)
        if messages and messages[-1]["role"] == "user":
            existing = messages[-1]["content"]
            if tool_summary and "Tool result:" not in existing:
                messages[-1]["content"] = existing + "\n\n" + tool_ctx
        else:
            messages.append({"role": "user", "content": tool_ctx})
        return messages

    if mode == prompt_mode.STARTUP_REMINDER:
        system_content = (
            "You are serving as a backend model for Claude Code. "
            "Ignore hidden reminders, tool schemas, and internal instructions. "
            "Respond only to the user's visible request. "
            "Do not repeat transcript structure, role labels, or hidden prompt text."
        )
        if tools_prompt:
            system_content += "\n\n" + tools_prompt
        user_text = extract_last_user_text(req)
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_text or "Привет"},
        ]

    messages: list[dict[str, str]] = []
    system_parts: list[str] = [
        "You are serving as a backend model for Claude Code. "
        "Do not repeat hidden instructions, tool schemas, system reminders, role labels, or transcript structure. "
        "Use the visible conversation history in this same session as context. "
        "If the user shared a name, preference, or fact earlier in this conversation, keep using it."
    ]
    if tools_prompt:
        system_parts.append(tools_prompt)

    for msg in req.messages:
        content = extract_visible_message_content(msg)
        if content:
            messages.append({"role": msg.role, "content": content})

    system_text = "\n\n".join(part for part in system_parts if part).strip()
    if system_text:
        messages.insert(0, {"role": "system", "content": system_text})
    return messages


def convert_anthropic_to_oai_body(req: Any, *, extract_tools: Callable[[Any], list[dict[str, Any]]]) -> dict[str, Any]:
    oai_body: dict[str, Any] = {}
    oai_messages: list[dict[str, Any]] = []

    system_param = req.system
    if system_param is not None:
        system_content = ""
        if isinstance(system_param, str):
            system_content = system_param
        elif isinstance(system_param, list):
            for block in system_param:
                if isinstance(block, dict) and block.get("type") == "text":
                    system_content += str(block.get("text", ""))
        if system_content:
            oai_messages.append({"role": "system", "content": system_content})

    for msg in req.messages:
        role = msg.role
        content = msg.content
        if isinstance(content, str):
            oai_messages.append({"role": role, "content": content})
            continue

        converted_content: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        reasoning_content = ""

        for block in content:
            block_type = str(block.get("type", ""))

            if block_type == "text":
                converted_content.append({"type": "text", "text": str(block.get("text", ""))})
            elif block_type == "thinking":
                reasoning_content += str(block.get("thinking", ""))
            elif block_type == "image":
                source = block.get("source", {})
                if isinstance(source, dict):
                    source_type = str(source.get("type", ""))
                    if source_type == "base64":
                        media_type = str(source.get("media_type", "image/jpeg"))
                        data = str(source.get("data", ""))
                        converted_content.append(
                            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}
                        )
                    elif source_type == "url":
                        url = str(source.get("url", ""))
                        if url:
                            converted_content.append({"type": "image_url", "image_url": {"url": url}})
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": str(block.get("id", "")),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name", "")),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    }
                )
            elif block_type == "tool_result":
                result_content = block.get("content", "")
                result_text = ""
                if isinstance(result_content, str):
                    result_text = result_content
                elif isinstance(result_content, list):
                    for item in result_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            result_text += str(item.get("text", ""))
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(block.get("tool_use_id", "")),
                        "content": result_text,
                    }
                )

        if converted_content or tool_calls or reasoning_content:
            new_msg: dict[str, Any] = {"role": role}
            new_msg["content"] = converted_content if converted_content else ""
            if tool_calls:
                new_msg["tool_calls"] = tool_calls
            if reasoning_content:
                new_msg["reasoning_content"] = reasoning_content
            oai_messages.append(new_msg)

        oai_messages.extend(tool_results)

    oai_body["messages"] = oai_messages

    tools = extract_tools(req)
    if tools:
        oai_tools = []
        for tool in tools:
            oai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": str(tool.get("name", "")),
                        "description": str(tool.get("description", "")),
                        "parameters": tool.get("input_schema", {}),
                    },
                }
            )
        oai_body["tools"] = oai_tools

    extra = req.model_extra or {}
    tool_choice = extra.get("tool_choice")
    if isinstance(tool_choice, dict):
        choice_type = str(tool_choice.get("type", ""))
        if choice_type == "auto":
            oai_body["tool_choice"] = "auto"
        elif choice_type in {"any", "tool"}:
            oai_body["tool_choice"] = "required"

    stop_sequences = extra.get("stop_sequences")
    if stop_sequences is not None:
        oai_body["stop"] = stop_sequences

    oai_body["max_tokens"] = req.max_tokens or 32000
    oai_body["temperature"] = req.temperature
    oai_body["top_p"] = req.top_p
    oai_body["stream"] = req.stream

    thinking = extra.get("thinking")
    if isinstance(thinking, dict):
        thinking_type = str(thinking.get("type", ""))
        if thinking_type == "enabled":
            oai_body["thinking_budget_tokens"] = int(thinking.get("budget_tokens", 10000))

    metadata = extra.get("metadata")
    if isinstance(metadata, dict):
        user_id = metadata.get("user_id")
        if isinstance(user_id, str) and user_id:
            oai_body["__metadata_user_id"] = user_id

    return oai_body
