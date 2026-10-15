"""Salvage parsers for textual pseudo-tool-calls emitted by local models.

When models like gemma-4-E4B fail to produce proper ``tool_use`` blocks,
they often emit tool calls as plain text in various formats.  This module
detects and parses those formats into structured tool-call dicts that the
executor can dispatch normally.

Parsers are tried in order inside ``try_salvage_textual_tool_call``.
Add new parsers there when new model output formats appear.
"""

from __future__ import annotations

import ast
import os
import re
from typing import Any

SALVAGE_META_KEY = "_salvage"

SALVAGE_PARSER_EVIDENCE: dict[str, str] = {
    "gemma_pseudo": (
        "Confirmed by temp_execution_6c40c953 and temp_execution_ed9d52ca: "
        "<|tool_call>call: skill_manage{...}<tool_call|> variants."
    ),
    "gemma_empty": (
        "Confirmed during Obsidian vault work: empty-body Gemma MCP calls like "
        "<|tool_call>call:NAME{}<tool_call|>."
    ),
    "function_style": (
        "Confirmed by temp_execution_7181e7e3, temp_execution_2ea516a2, "
        "and temp_execution_ed9d52ca: tool_code/function-style skill calls."
    ),
    "write_code_block": (
        "Confirmed by earlier Write code-block salvage work; keep fixture-backed "
        "before changing behavior."
    ),
    "bash_pseudo": (
        "Confirmed by temp_execution_696f7d6b and temp_execution_7417b4db: "
        "Bash: command emitted as assistant text."
    ),
    "bash_code_block": (
        "Unconfirmed by current cc_backend_trace corpus as a Gemma tool artifact; "
        "kept for log-evidence rather than removed."
    ),
    "bracket_tool_use": (
        "Confirmed by temp_execution_dac5b46f: local model emitted "
        "[tool_use id=... name=Read input={...}] as visible text."
    ),
    "foreign_file_write": (
        "Confirmed by task 1aa8517b: model emitted "
        "tool_code/print(excalidraw.write_file(file_path=..., content=...)) "
        "instead of native Write tool_use."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _with_salvage_meta(
    call: dict[str, Any],
    *,
    parser: str,
    source_shape: str,
) -> dict[str, Any]:
    call[SALVAGE_META_KEY] = {
        "parser": parser,
        "source_shape": source_shape,
        "evidence": SALVAGE_PARSER_EVIDENCE.get(parser, ""),
    }
    return call


def strip_salvage_meta(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Return a tool_use-safe copy without local salvage metadata."""
    return {k: v for k, v in tool_call.items() if k != SALVAGE_META_KEY}


def get_salvage_meta(tool_call: dict[str, Any]) -> dict[str, Any]:
    meta = tool_call.get(SALVAGE_META_KEY)
    return dict(meta) if isinstance(meta, dict) else {}


def _tool_by_name(tools: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for tool in tools:
        if str(tool.get("name") or "") == name:
            return tool
    return None


def _normalize_salvaged_tool_input(
    tool_name: str, tool_input: dict[str, Any], workdir: str
) -> dict[str, Any]:
    normalized = dict(tool_input)

    if tool_name.startswith("skill_") or tool_name.startswith("skills_"):
        legacy_name = normalized.pop("skill_name", None)
        legacy_id = normalized.pop("skill_id", None)
        if legacy_name is not None and "name" not in normalized:
            normalized["name"] = legacy_name
        if legacy_id is not None and "name" not in normalized:
            normalized["name"] = legacy_id

    legacy_old = normalized.pop("text_to_replace", None)
    legacy_new = normalized.pop("replacement_text", None)
    if legacy_old is not None and "old_string" not in normalized:
        normalized["old_string"] = legacy_old
    if legacy_new is not None and "new_string" not in normalized:
        normalized["new_string"] = legacy_new

    if tool_name in {"Read", "Write", "Edit"}:
        file_path = normalized.get("file_path")
        if (
            isinstance(file_path, str)
            and file_path.strip()
            and not os.path.isabs(file_path)
        ):
            normalized["file_path"] = os.path.join(workdir, file_path.strip())

    if tool_name == "Grep" and "file_path" in normalized and "path" not in normalized:
        normalized["path"] = normalized.pop("file_path")

    if tool_name == "Read":
        if "path" in normalized and "file_path" not in normalized:
            normalized["file_path"] = normalized.pop("path")
        elif "file" in normalized and "file_path" not in normalized:
            normalized["file_path"] = normalized.pop("file")

    return normalized


def _validate_salvaged_tool_input(
    tool: dict[str, Any], tool_input: dict[str, Any]
) -> tuple[bool, str]:
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        return False, "missing input_schema"
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return False, "missing schema properties"

    required = schema.get("required")
    required_keys = required if isinstance(required, list) else []
    for key in required_keys:
        if key not in tool_input:
            return False, f"missing required field: {key}"

    for key, value in tool_input.items():
        prop = properties.get(key)
        if not isinstance(prop, dict):
            return False, f"unexpected field: {key}"
        expected_type = prop.get("type")
        if expected_type == "string" and not isinstance(value, str):
            return False, f"field {key} must be string"
        if expected_type == "boolean" and not isinstance(value, bool):
            return False, f"field {key} must be boolean"
        if expected_type == "integer" and not isinstance(value, int):
            return False, f"field {key} must be integer"
        if expected_type == "array":
            if not isinstance(value, list):
                return False, f"field {key} must be array"
            items = prop.get("items")
            if isinstance(items, dict) and items.get("type") == "string":
                if any(not isinstance(item, str) for item in value):
                    return False, f"field {key} must contain only strings"

    if str(tool.get("name") or "") == "Edit":
        old_string = tool_input.get("old_string")
        new_string = tool_input.get("new_string")
        if not isinstance(old_string, str) or not old_string.strip():
            return False, "old_string must be a non-empty string"
        if not isinstance(new_string, str):
            return False, "new_string must be a string"
        if old_string == new_string:
            return False, "new_string must differ from old_string"

    return True, ""


# ---------------------------------------------------------------------------
# Parsers — each takes text (+ tools for some) and returns {id, name, input}
# or None.
# ---------------------------------------------------------------------------

def _parse_gemma_pseudo_tool_call(text: str) -> dict[str, Any] | None:
    match = re.search(
        r"(?is)<\|tool_call>\s*call:\s*(?P<name>[A-Za-z0-9_]+)\s*\{(?P<body>.*?)\}<tool_call\|>",
        text,
    )
    if not match:
        return None

    tool_name = match.group("name").strip()
    body = match.group("body")
    if not tool_name:
        return None

    tool_input: dict[str, Any] = {}

    # Parse array parameters: key:[<|"|>val1<|"|>, <|"|>val2<|"|>]
    for key, array_content in re.findall(
        r"([A-Za-z0-9_]+):\[(.*?)\]", body, flags=re.DOTALL
    ):
        items = re.findall(r'<\|"\|>(.*?)<\|"\|>', array_content, flags=re.DOTALL)
        if items:
            tool_input[key.strip()] = items

    # Parse string parameters: key:<|"|>value<|"|> (skip keys already parsed as arrays)
    for key, quoted_value in re.findall(
        r'([A-Za-z0-9_]+):<\|"\|>(.*?)<\|"\|>', body, flags=re.DOTALL
    ):
        if key.strip() not in tool_input:
            tool_input[key.strip()] = quoted_value

    # Parse loose Gemma shape: <|tool_call>call: tool{key: "value"}<tool_call|>
    # Some local models emit this instead of the stricter <|"|> string markers.
    for key, quoted_value in re.findall(
        r'([A-Za-z0-9_]+)\s*:\s*"((?:\\.|[^"\\])*)"',
        body,
        flags=re.DOTALL,
    ):
        clean_key = key.strip()
        if clean_key not in tool_input:
            try:
                tool_input[clean_key] = ast.literal_eval(f'"{quoted_value}"')
            except Exception:
                tool_input[clean_key] = quoted_value

    if not tool_input:
        return None

    return _with_salvage_meta(
        {
            "id": f"toolu_salvaged_{tool_name.lower()}",
            "name": tool_name,
            "input": tool_input,
        },
        parser="gemma_pseudo",
        source_shape="gemma_tool_call_marker",
    )


def _trim_to_balanced_call(expr: str) -> str | None:
    """Extract the balanced function call from a potentially messy expression.

    Handles stray closing parens and trailing garbage (e.g. ``<eos>tool_code``)
    that the model appends after the actual function call.
    Skips over string literals (triple-quoted, single/double-quoted).
    """
    paren_start = expr.find("(")
    if paren_start < 0:
        return None
    depth = 0
    in_triple = None  # '"""' or "'''"
    i = paren_start
    while i < len(expr):
        ch = expr[i]
        # Inside triple-quoted string: look for closing triple-quote
        if in_triple:
            if expr[i : i + 3] == in_triple:
                i += 3
                in_triple = None
            else:
                i += 1
            continue
        # Opening triple-quote
        if expr[i : i + 3] in ('"""', "'''"):
            in_triple = expr[i : i + 3]
            i += 3
            continue
        # Single/double-quoted string
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < len(expr) and expr[i] != quote:
                if expr[i] == "\\":
                    i += 1  # skip escaped char
                i += 1
            if i < len(expr):
                i += 1  # skip closing quote
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return expr[: i + 1]
        i += 1
    return None


def _parse_function_style_pseudo_tool_call(
    text: str, tools: list[dict[str, Any]]
) -> dict[str, Any] | None:
    known_tools = {
        str(tool.get("name") or "").strip()
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("name") or "").strip()
    }
    if not known_tools:
        return None

    tool_name_pattern = "|".join(re.escape(name) for name in sorted(known_tools, key=len, reverse=True))
    for match in re.finditer(
        rf"(?is)\btool_code\s*:\s*(?P<expr>(?:{tool_name_pattern})\s*\()",
        text or "",
    ):
        expr_start = match.start("expr")
        next_marker = re.search(
            r"(?is)\n\s*(?:<eos>\s*)?(?:tool_output|tool_code(?:\s*:|\b))",
            (text or "")[expr_start:],
        )
        expr_end = expr_start + next_marker.start() if next_marker else len(text or "")
        expr = (text or "")[expr_start:expr_end].strip()
        parsed = None
        try:
            parsed = ast.parse(expr, mode="eval")
        except SyntaxError:
            # Model often appends stray ')' or '<eos>tool_code' after the
            # balanced call.  Try trimming to the balanced closing paren.
            trimmed = _trim_to_balanced_call(expr)
            if trimmed and trimmed != expr:
                try:
                    parsed = ast.parse(trimmed, mode="eval")
                except SyntaxError:
                    parsed = None
        if parsed is None:
            continue
        call = parsed.body
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        tool_name = call.func.id.strip()
        if tool_name not in known_tools or call.args:
            continue
        tool_input: dict[str, Any] = {}
        valid = True
        for kw in call.keywords:
            if kw.arg is None:
                valid = False
                break
            try:
                tool_input[kw.arg] = ast.literal_eval(kw.value)
            except Exception:
                valid = False
                break
        if valid:
            return _with_salvage_meta(
                {
                    "id": f"toolu_salvaged_{tool_name.lower()}",
                    "name": tool_name,
                    "input": tool_input,
                },
                parser="function_style",
                source_shape="tool_code_colon_function_call",
            )

    for raw_line in reversed((text or "").splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)", line, flags=re.DOTALL):
            continue
        try:
            parsed = ast.parse(line, mode="eval")
        except SyntaxError:
            continue
        call = parsed.body
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        tool_name = call.func.id.strip()
        if tool_name not in known_tools or call.args:
            continue

        tool_input: dict[str, Any] = {}
        valid = True
        for kw in call.keywords:
            if kw.arg is None:
                valid = False
                break
            try:
                tool_input[kw.arg] = ast.literal_eval(kw.value)
            except Exception:
                valid = False
                break
        if not valid:
            continue
        return _with_salvage_meta(
            {
                "id": f"toolu_salvaged_{tool_name.lower()}",
                "name": tool_name,
                "input": tool_input,
            },
            parser="function_style",
            source_shape="plain_function_call",
        )

    # Detect "Write: /path/to/file\n```lang\n...content...\n```" pattern
    for m in re.finditer(
        r"(?is)Write\s*:\s*(?P<path>/\S+)\s*\n```[a-z]*\n(?P<content>.*?)\n```",
        text or "",
    ):
        path = m.group("path").strip()
        content = m.group("content")
        if path and content and len(content) > 20:
            return _with_salvage_meta(
                {
                    "id": "toolu_salvaged_write",
                    "name": "Write",
                    "input": {"file_path": path, "content": content},
                },
                parser="write_code_block",
                source_shape="write_colon_code_block",
            )

    return None


# ---------------------------------------------------------------------------
# Empty-body gemma parser
# ---------------------------------------------------------------------------


def _parse_gemma_empty_tool_call(text: str) -> dict[str, Any] | None:
    """Handle gemma-style ``<|tool_call>call:NAME{}<tool_call|>`` with no args.

    The main gemma parser skips these because ``tool_input`` is empty.
    MCP tools like ``skills_list`` legitimately take zero arguments, so we
    salvage them here.
    """
    match = re.search(
        r"(?is)<\|tool_call>\s*call:\s*(?P<name>[A-Za-z0-9_]+)\s*\{\s*\}<tool_call\|>",
        text,
    )
    if not match:
        return None

    tool_name = match.group("name").strip()
    if not tool_name:
        return None

    return _with_salvage_meta(
        {
            "id": f"toolu_salvaged_{tool_name.lower()}",
            "name": tool_name,
            "input": {},
        },
        parser="gemma_empty",
        source_shape="gemma_empty_tool_call_marker",
    )


# ---------------------------------------------------------------------------
# Bash pseudo-call parser
# ---------------------------------------------------------------------------


def _parse_bash_pseudo_call(text: str) -> dict[str, Any] | None:
    """Handle ``Bash: <command>`` or ``Bash(command=...)`` textual patterns.

    Models like gemma-4 sometimes emit Bash commands as visible text instead
    of structured tool_use blocks.  We salvage the first plausible command
    line and turn it into a real ``Bash`` tool call.
    """
    # Pattern 1: "Bash:\n```bash\n<command>\n```"
    # Check this before single-line "Bash:" so the language tag is not
    # mistaken for the command.
    match = re.search(
        r"(?im)^Bash:\s*\n```(?:bash|sh|shell)?\s*\n(?P<cmd>.+?)\n```",
        text or "",
        re.DOTALL,
    )
    if match:
        cmd = match.group("cmd").strip()
        if cmd and len(cmd) > 2:
            return _with_salvage_meta(
                {
                    "id": "toolu_salvaged_bash",
                    "name": "Bash",
                    "input": {"command": cmd},
                },
                parser="bash_pseudo",
                source_shape="bash_colon_fenced_code_block",
            )

    # Pattern 2: "Bash: <rest of line>" (optionally with <eos> or quotes)
    match = re.search(
        r'(?i)^Bash:\s*(?P<cmd>[^\n<]+)',
        text or "",
        re.MULTILINE,
    )
    if match:
        cmd = match.group("cmd").strip().strip('"').strip("'")
        # Remove trailing <eos>, backticks, stray parens
        cmd = re.sub(r"\s*<eos>.*$", "", cmd, flags=re.IGNORECASE)
        cmd = cmd.strip("`").strip()
        if cmd and len(cmd) > 2:
            return _with_salvage_meta(
                {
                    "id": "toolu_salvaged_bash",
                    "name": "Bash",
                    "input": {"command": cmd},
                },
                parser="bash_pseudo",
                source_shape="bash_colon_line",
            )

    # Pattern 3: ```bash\n<command>\n```
    match = re.search(
        r"```bash\s*\n(?P<cmd>.+?)\n```",
        text or "",
        re.DOTALL,
    )
    if match:
        cmd = match.group("cmd").strip()
        if cmd and len(cmd) > 2:
            return _with_salvage_meta(
                {
                    "id": "toolu_salvaged_bash",
                    "name": "Bash",
                    "input": {"command": cmd},
                },
                parser="bash_code_block",
                source_shape="fenced_bash_code_block",
            )

    # Pattern 4: "shell_command: <command>"
    match = re.search(
        r'(?im)^shell_command:\s*(?P<cmd>[^\n<]+)',
        text or "",
    )
    if match:
        cmd = match.group("cmd").strip().strip('"').strip("'")
        cmd = re.sub(r"\s*<eos>.*$", "", cmd, flags=re.IGNORECASE)
        cmd = cmd.strip("`").strip()
        if cmd and len(cmd) > 2:
            return _with_salvage_meta(
                {
                    "id": "toolu_salvaged_bash",
                    "name": "Bash",
                    "input": {"command": cmd},
                },
                parser="bash_pseudo",
                source_shape="shell_command_line",
            )

    # Pattern 5: "Bash\n<command>"
    match = re.search(
        r"(?im)^Bash\s*\n(?P<cmd>[^\n<]+)",
        text or "",
    )
    if match:
        cmd = match.group("cmd").strip().strip('"').strip("'")
        cmd = re.sub(r"\s*<eos>.*$", "", cmd, flags=re.IGNORECASE)
        cmd = cmd.strip("`").strip()
        if cmd and len(cmd) > 2:
            return _with_salvage_meta(
                {
                    "id": "toolu_salvaged_bash",
                    "name": "Bash",
                    "input": {"command": cmd},
                },
                parser="bash_pseudo",
                source_shape="bash_label_newline_command",
            )

    return None


def _parse_bracket_tool_use(text: str) -> dict[str, Any] | None:
    """Handle ``[tool_use id=... name=Tool input={...}]`` textual artifacts."""
    match = re.search(
        r"(?is)\[tool_use\s+id=(?P<id>[A-Za-z0-9_-]+)\s+name=(?P<name>[A-Za-z0-9_]+)\s+input=(?P<input>\{.*?\})\s*\]",
        text or "",
    )
    if not match:
        return None

    tool_name = match.group("name").strip()
    raw_input = match.group("input").strip()
    try:
        parsed_input = ast.literal_eval(raw_input)
    except Exception:
        return None
    if not isinstance(parsed_input, dict):
        return None

    return _with_salvage_meta(
        {
            "id": f"toolu_salvaged_{tool_name.lower()}",
            "name": tool_name,
            "input": parsed_input,
        },
        parser="bracket_tool_use",
        source_shape="anthropic_bracket_tool_use_text",
    )


def _parse_json_object_tool_call(text: str) -> dict[str, Any] | None:
    """Handle `Bash{"command": "..."}` JSON object style textual artifacts."""
    match = re.search(
        r'(?is)(Bash|Read|Grep|Glob|Write|Edit)\s*\{([^}]+)\}',
        text or "",
    )
    if not match:
        return None

    tool_name = match.group(1).strip()
    raw_input = match.group(2).strip()

    try:
        parsed_input = ast.literal_eval(f"{{{raw_input}}}")
    except Exception:
        return None

    if not isinstance(parsed_input, dict):
        return None

    return _with_salvage_meta(
        {
            "id": f"toolu_salvaged_{tool_name.lower()}",
            "name": tool_name,
            "input": parsed_input,
        },
        parser="json_object_tool_call",
        source_shape="tool_brace_json_style",
    )


def _parse_foreign_file_write_artifact(text: str) -> dict[str, Any] | None:
    """Handle foreign skill-style write_file calls like:

    tool_code
    print(excalidraw.write_file(file_path="...", content=\"\"\"...\"\"\"))

    Converts to synthetic Write tool call after validating content through
    output_contracts.
    """
    match = re.search(
        r'(?s)[A-Za-z_]\w*\.write_file\s*\(\s*file_path\s*=\s*"([^"]+)"\s*,\s*content\s*=\s*"""(.*?)"""\s*\)',
        text or "",
    )
    if not match:
        return None

    file_path = match.group(1).strip()
    raw_content = match.group(2).strip()

    if not file_path or not raw_content:
        return None

    from .output_contracts import _parse_json_candidate, _json_artifact_content

    parsed = _parse_json_candidate(raw_content)
    if parsed is None:
        return None

    validated = _json_artifact_content(parsed, target_path=file_path)
    if validated is None:
        return None

    return _with_salvage_meta(
        {
            "id": "toolu_salvaged_write",
            "name": "Write",
            "input": {"file_path": file_path, "content": validated},
        },
        parser="foreign_file_write",
        source_shape="skill_write_file_triple_quoted",
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def contains_textual_tool_artifact(text: str, tools: list[dict[str, Any]]) -> bool:
    """Return True if *text* contains a textual pseudo-tool-call artifact."""
    lowered = text.lower()
    # Inline protocol examples embedded in prose are not execution attempts.
    # Standalone snippets and fenced pseudo-calls retain the existing protection.
    dsml_text = re.sub(
        r"(?m)^(.*\S.*)$",
        lambda line: re.sub(r"(?<!`)`[^`\n]+`(?!`)", "", line.group(0))
        if re.sub(r"(?<!`)`[^`\n]+`(?!`)", "", line.group(0)).strip()
        else line.group(0),
        lowered,
    )
    if (
        "<|tool_call>" in lowered
        or re.search(r'<｜｜dsml｜｜\s+(?:calls|invoke)\b', dsml_text)
        or "[tool_use" in lowered
        or "tool_code" in lowered
        or re.search(r"<unused\d+>", lowered)
        or "call:edit{" in lowered
        or "call:write{" in lowered
        or "call:read{" in lowered
        or re.search(r"(?i)^Write\s*:\s*/\S+", text or "", re.MULTILINE)
        or re.search(r"(?i)^Bash:\s*\S+", text or "", re.MULTILINE)
        or re.search(r"(?i)^Bash\s*\n\S+", text or "", re.MULTILINE)
        or re.search(r"(?i)^shell_command:\s*\S+", text or "", re.MULTILINE)
        or re.search(r"Bash\s*\{", text or "")
        or re.search(r"(?is)\b[A-Za-z_]\w*\.write_file\s*\(", text or "")
    ):
        return True

    known_tools = [
        re.escape(str(tool.get("name") or "").strip())
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("name") or "").strip()
    ]
    if not known_tools:
        return False
    tool_name_pattern = "|".join(known_tools)
    return bool(
        re.search(
            rf"(?im)^\s*(?:{tool_name_pattern})\s*\(",
            text or "",
        )
        or re.search(
            rf"(?im)^\s*/[^\s\n]*?(?:{tool_name_pattern})\s*\(",
            text or "",
        )
        or re.search(
            rf"(?is)\bprint\s*\(\s*(?:{tool_name_pattern})(?:\.[A-Za-z_][A-Za-z0-9_]*)?\s*\(",
            text or "",
        )
        or re.search(
            rf"(?is)\b(?:{tool_name_pattern})\.[A-Za-z_][A-Za-z0-9_]*\s*\(",
            text or "",
        )
    )


# ---------------------------------------------------------------------------
# Retry message
# ---------------------------------------------------------------------------

def build_textual_tool_retry_message(raw_text: str) -> str:
    preview = " ".join((raw_text or "").strip().split())
    if len(preview) > 240:
        preview = preview[:240] + "..."
    return (
        "[СЛУЖЕБНОЕ. Это внутренняя инструкция от системы, не комментируй её в ответе пользователю.] "
        "Ты попытался вызвать инструмент текстом, а не структурным tool_use. "
        "Пользователю нельзя показывать строки вроде псевдовызова инструмента.\n\n"
        f"Проблемный фрагмент: {preview}\n\n"
        "Повтори тот же шаг корректно:\n"
        "- либо вызови нужный инструмент как настоящий tool_use;\n"
        "- либо, если инструмент уже не нужен, ответь пользователю обычным текстом.\n"
        "Не печатай название инструмента с аргументами в видимом ответе."
    )


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------

def try_salvage_textual_tool_call(
    text: str,
    tools: list[dict[str, Any]],
    workdir: str,
) -> tuple[dict[str, Any] | None, bool, str]:
    """Try to salvage a textual pseudo-tool-call from *text*.

    Returns ``(tool_call, valid, validation_error)``:
    - ``tool_call`` is ``{id, name, input}`` if a pseudo-call was found, else None.
    - ``valid`` is True if the salvaged input passes schema validation.
    - ``validation_error`` is a human-readable error string (empty when valid).

    If *tool_call* is None, no pseudo-call was detected at all.
    """
    # Try parsers in order
    salvaged = _parse_gemma_pseudo_tool_call(text)
    if salvaged is None:
        salvaged = _parse_gemma_empty_tool_call(text)
    if salvaged is None:
        salvaged = _parse_function_style_pseudo_tool_call(text, tools)
    if salvaged is None:
        salvaged = _parse_bash_pseudo_call(text)
    if salvaged is None:
        salvaged = _parse_json_object_tool_call(text)
    if salvaged is None:
        salvaged = _parse_bracket_tool_use(text)
    if salvaged is None:
        salvaged = _parse_foreign_file_write_artifact(text)

    if salvaged is None:
        return None, False, ""

    tool_schema = _tool_by_name(tools, str(salvaged.get("name") or ""))
    if tool_schema is None:
        return None, False, ""

    normalized_input = _normalize_salvaged_tool_input(
        str(salvaged.get("name") or ""),
        salvaged.get("input") if isinstance(salvaged.get("input"), dict) else {},
        workdir,
    )
    ok, validation_error = _validate_salvaged_tool_input(tool_schema, normalized_input)
    salvaged["input"] = normalized_input
    return salvaged, ok, validation_error
