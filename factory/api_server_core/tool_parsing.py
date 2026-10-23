import hashlib
import json
import math
import re
from typing import Any, Callable


def extract_named_json_object(text: str, field_name: str) -> dict[str, Any] | None:
    field_match = re.search(rf'"{re.escape(field_name)}"\s*:', text)
    if not field_match:
        return None
    brace = text.find("{", field_match.end())
    if brace == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(text[brace:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def extract_json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    idx = 0
    length = len(text)
    while idx < length:
        brace = text.find("{", idx)
        if brace == -1:
            break
        try:
            payload, end = decoder.raw_decode(text[brace:])
        except json.JSONDecodeError:
            idx = brace + 1
            continue
        if isinstance(payload, dict):
            objects.append(payload)
        idx = brace + max(end, 1)
    return objects


def parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        text = match.group(0)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def coerce_tool_plan_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    tool_name = payload.get("tool")
    tool_input = payload.get("input", {})
    if isinstance(tool_name, dict):
        nested_name = tool_name.get("name")
        nested_input = tool_name.get("input")
        if isinstance(nested_name, str):
            tool_name = nested_name
            if isinstance(nested_input, dict):
                tool_input = nested_input
    if tool_name is None:
        return None
    if not isinstance(tool_name, str):
        return None
    if tool_name == "Bash" and isinstance(tool_input, str):
        tool_input = {"command": tool_input}
    if not isinstance(tool_input, dict):
        return None
    return {"tool": tool_name, "input": tool_input}


def salvage_tool_plan(text: str) -> dict[str, Any] | None:
    tool_match = re.search(r'"tool"\s*:\s*"(?P<tool>[^"]+)"', text)
    if not tool_match:
        return None
    tool_name = tool_match.group("tool").strip()
    if not tool_name:
        return None
    tool_input = extract_named_json_object(text, "input")
    if isinstance(tool_input, dict):
        return {"tool": tool_name, "input": tool_input}
    # Fallback: if input is too large to parse (e.g. Write with huge content),
    # extract just file_path so the tool routing can proceed with a separate
    # content-generation step.
    for key in ("file_path", "path"):
        path_match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', text)
        if path_match:
            return {"tool": tool_name, "input": {key: path_match.group(1)}}
    return None


def parse_tool_plan(text: str) -> dict[str, Any] | None:
    payload = parse_json_object(text)
    if payload is not None:
        parsed = coerce_tool_plan_payload(payload)
        if parsed is not None:
            return parsed
    salvaged = salvage_tool_plan(text)
    if salvaged is not None:
        return salvaged
    for payload in extract_json_objects(text):
        parsed = coerce_tool_plan_payload(payload)
        if parsed is not None:
            return parsed
    return None


def tool_use_block_count(text: str) -> int:
    return len(re.findall(r"\[tool_use\b", text)) + len(re.findall(r"<tool_call\b", text, flags=re.IGNORECASE))


def _parse_bracket_tool_use(text: str, *, parse_json_object_fn: Callable[[str], dict[str, Any] | None]) -> dict[str, Any] | None:
    if len(re.findall(r"\[tool_use\b", text)) != 1:
        return None
    match = re.search(
        r"\[tool_use\s+id=(?P<id>[^\s\]]+)\s+name=(?P<name>[^\s\]]+)\s+input=(?P<input>\{.*?\})\]",
        text,
        flags=re.DOTALL,
    )
    if not match:
        return None
    prefix = text[: match.start()].strip()
    suffix = text[match.end() :].strip()
    if prefix:
        prefix_lines = [line.strip() for line in prefix.splitlines() if line.strip()]
        if len(prefix_lines) > 3:
            return None
        if any(line.startswith("[") or line.startswith("<tool_call") for line in prefix_lines):
            return None
    if suffix:
        suffix_lines = [line.strip() for line in suffix.splitlines() if line.strip()]
        if len(suffix_lines) > 2:
            return None
        if any(line.startswith("[") or line.startswith("<tool_call") for line in suffix_lines):
            return None

    tool_id = match.group("id").strip()
    tool_name = match.group("name").strip()
    tool_input = parse_json_object_fn(match.group("input"))
    if not tool_id or not tool_name or not isinstance(tool_input, dict):
        return None
    return {"id": tool_id, "name": tool_name, "input": tool_input}


def _parse_xml_tool_use(text: str) -> dict[str, Any] | None:
    if len(re.findall(r"<tool_call\b", text, flags=re.IGNORECASE)) != 1:
        return None
    match = re.search(
        r"(?is)<tool_call>\s*<function=(?P<name>[^>\s]+)>\s*(?P<body>.*?)\s*</function>\s*</tool_call>",
        text,
    )
    if not match:
        return None
    prefix = text[: match.start()].strip()
    suffix = text[match.end() :].strip()
    if prefix:
        prefix_lines = [line.strip() for line in prefix.splitlines() if line.strip()]
        if len(prefix_lines) > 3:
            return None
        if any(line.startswith("[") or line.startswith("<tool_call") for line in prefix_lines):
            return None
    if suffix:
        suffix_lines = [line.strip() for line in suffix.splitlines() if line.strip()]
        if len(suffix_lines) > 2:
            return None
        if any(line.startswith("[") or line.startswith("<tool_call") for line in suffix_lines):
            return None

    tool_name = match.group("name").strip()
    body = match.group("body")
    if not tool_name:
        return None

    tool_input: dict[str, Any] = {}
    for param_match in re.finditer(
        r"(?is)<parameter=(?P<key>[^>\s]+)>\s*(?P<value>.*?)\s*</parameter>",
        body,
    ):
        key = param_match.group("key").strip()
        value = param_match.group("value").strip()
        if not key:
            return None
        tool_input[key] = value

    if not tool_input:
        return None

    digest = hashlib.sha1(match.group(0).encode("utf-8")).hexdigest()[:12]
    return {"id": f"toolu_xml_{digest}", "name": tool_name, "input": tool_input}


def parse_textual_tool_use(text: str, *, parse_json_object_fn: Callable[[str], dict[str, Any] | None]) -> dict[str, Any] | None:
    if tool_use_block_count(text) != 1:
        return None
    return _parse_bracket_tool_use(text, parse_json_object_fn=parse_json_object_fn) or _parse_xml_tool_use(text)


def validate_tool_input(tool: dict[str, Any], tool_input: dict[str, Any]) -> tuple[bool, str]:
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

    if schema.get("additionalProperties") is False:
        unexpected = [key for key in tool_input if key not in properties]
        if unexpected:
            return False, f"unexpected fields: {', '.join(unexpected)}"

    for key, value in tool_input.items():
        prop = properties.get(key)
        if not isinstance(prop, dict):
            continue
        expected_type = prop.get("type")
        if expected_type == "string" and not isinstance(value, str):
            return False, f"field {key} must be string"
        if expected_type == "boolean" and not isinstance(value, bool):
            return False, f"field {key} must be boolean"
        if expected_type == "number" and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            return False, f"field {key} must be number"
        if expected_type == "integer" and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (isinstance(value, float) and not value.is_integer())
        ):
            return False, f"field {key} must be integer"
        if expected_type == "array" and not isinstance(value, list):
            return False, f"field {key} must be array"
        if expected_type == "object" and not isinstance(value, dict):
            return False, f"field {key} must be object"

    if tool.get("name") == "Edit":
        old_string = tool_input.get("old_string")
        new_string = tool_input.get("new_string")
        if not isinstance(old_string, str) or not old_string.strip():
            return False, "old_string must be a non-empty string"
        if not isinstance(new_string, str):
            return False, "new_string must be a string"
        if old_string == new_string:
            return False, "new_string must differ from old_string"

    return True, ""


def normalize_tool_input(tool: dict[str, Any], tool_input: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(tool_input)
    tool_name = str(tool.get("name", ""))

    if tool_name in {"Read", "Write", "Edit", "Update", "MultiEdit", "NotebookEdit"}:
        legacy_target = normalized.get("file_name")
        if not isinstance(legacy_target, str) or not legacy_target.strip():
            legacy_target = normalized.get("filename")
        if isinstance(legacy_target, str) and legacy_target.strip() and "file_path" not in normalized:
            normalized["file_path"] = legacy_target.strip()
        normalized.pop("file_name", None)
        normalized.pop("filename", None)

    if tool_name == "AskUserQuestion":
        questions = normalized.get("questions")
        if isinstance(questions, list):
            normalized_questions: list[dict[str, Any]] = []
            for item in questions:
                if isinstance(item, str):
                    question_text = item.strip()
                    if not question_text:
                        continue
                    normalized_questions.append(
                        {
                            "question": question_text,
                            "header": "Question",
                            "options": [
                                {"label": "Да", "description": "Подтвердить и продолжить"},
                                {"label": "Нет", "description": "Остановиться и уточнить"},
                            ],
                            "multiSelect": False,
                        }
                    )
                    continue
                if isinstance(item, dict):
                    question_obj = dict(item)
                    question_text = question_obj.get("question")
                    if not isinstance(question_text, str) or not question_text.strip():
                        continue
                    header = question_obj.get("header")
                    if not isinstance(header, str) or not header.strip():
                        question_obj["header"] = "Question"
                    else:
                        question_obj["header"] = header.strip()[:12]
                    options = question_obj.get("options")
                    if not isinstance(options, list) or len(options) < 2:
                        question_obj["options"] = [
                            {"label": "Да", "description": "Подтвердить и продолжить"},
                            {"label": "Нет", "description": "Остановиться и уточнить"},
                        ]
                    else:
                        normalized_options: list[dict[str, Any]] = []
                        for option in options[:4]:
                            if not isinstance(option, dict):
                                continue
                            label = option.get("label")
                            description = option.get("description")
                            if not isinstance(label, str) or not label.strip():
                                continue
                            if not isinstance(description, str) or not description.strip():
                                continue
                            normalized_options.append(
                                {
                                    "label": label.strip(),
                                    "description": description.strip(),
                                }
                            )
                        if len(normalized_options) < 2:
                            normalized_options = [
                                {"label": "Да", "description": "Подтвердить и продолжить"},
                                {"label": "Нет", "description": "Остановиться и уточнить"},
                            ]
                        question_obj["options"] = normalized_options
                    if not isinstance(question_obj.get("multiSelect"), bool):
                        question_obj["multiSelect"] = False
                    normalized_questions.append(question_obj)
            if normalized_questions:
                normalized["questions"] = normalized_questions
    schema = tool.get("input_schema")
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, prop in properties.items():
                if not isinstance(prop, dict):
                    continue
                if key not in normalized:
                    if "default" not in prop:
                        continue
                    normalized[key] = prop["default"]
                expected_type = prop.get("type")
                if expected_type not in ("number", "integer"):
                    continue
                value = normalized[key]
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                if isinstance(value, float) and not math.isfinite(value):
                    continue
                if expected_type == "integer":
                    if isinstance(value, float) and not value.is_integer():
                        continue
                    value = int(value)
                normalized[key] = value
    return normalized


def validated_named_tool_use(
    req: Any,
    state: Any,
    tool_use: dict[str, Any] | None,
    *,
    tool_lookup: Callable[[Any], dict[str, dict[str, Any]]],
    normalize_tool_input_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    validate_tool_input_fn: Callable[[dict[str, Any], dict[str, Any]], tuple[bool, str]],
    validate_tool_policy_fn: Callable[[Any, str, dict[str, Any]], tuple[bool, str]],
) -> dict[str, Any] | None:
    if tool_use is None:
        return None
    tool = tool_lookup(req).get(str(tool_use.get("name", "")))
    if not isinstance(tool, dict):
        return None
    tool_input = normalize_tool_input_fn(tool, tool_use.get("input", {}))
    is_valid, _ = validate_tool_input_fn(tool, tool_input)
    if not is_valid:
        return None
    if state is not None:
        policy_ok, _ = validate_tool_policy_fn(state, str(tool_use.get("name", "")), tool_input)
        if not policy_ok:
            return None
    validated = dict(tool_use)
    validated["input"] = tool_input
    return validated
