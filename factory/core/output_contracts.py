"""Structural contracts for model text that should have been tool use.

This module intentionally avoids natural-language intent checks.  It only
looks for machine-checkable artifacts: writable path tokens and parseable file
payloads.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ABS_PATH_RE = re.compile(r"(?<![\w.-])/(?:[^\s`'\"<>|{}[\]]+)")
_REL_FILE_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_./-]*\.[a-zA-Z]{2,})\b")
_JSON_FENCE_RE = re.compile(r"```(?:json|excalidraw)?\s*\n(?P<body>.*?)\n```", re.DOTALL)


@dataclass(frozen=True)
class PendingWriteAction:
    file_path: str
    content: str
    reason: str

    def to_tool_input(self) -> dict[str, str]:
        return {"file_path": self.file_path, "content": self.content}


def _clean_path_token(token: str) -> str:
    return token.rstrip(".,);:")


def _is_within_root(path: str, root: str) -> bool:
    root_real = os.path.realpath(root)
    path_real = os.path.realpath(path)
    return path_real == root_real or path_real.startswith(root_real + os.sep)


def _writable_path(path: str, *, workdir: str) -> bool:
    try:
        resolved = os.path.realpath(path)
    except (TypeError, ValueError):
        return False
    if _is_within_root(resolved, workdir):
        return True
    vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    return bool(vault and _is_within_root(resolved, vault))


def extract_writable_paths(text: str, *, workdir: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    # Absolute paths
    for match in _ABS_PATH_RE.finditer(text or ""):
        raw = _clean_path_token(match.group(0))
        if not raw or not _writable_path(raw, workdir=workdir):
            continue
        normalized = os.path.realpath(raw)
        if normalized in seen:
            continue
        seen.add(normalized)
        paths.append(normalized)
    # Relative paths (file names like `name.ext` or `dir/name.ext`)
    for match in _REL_FILE_RE.finditer(text or ""):
        raw = match.group(1)
        if not raw or raw.startswith("/"):
            continue
        # Skip if it is a basename/substring of an already found absolute path
        is_suffix_of_abs = any(
            abs_path.endswith("/" + raw) or abs_path.endswith("\\" + raw)
            for abs_path in paths
        )
        if is_suffix_of_abs:
            continue
        resolved = os.path.realpath(os.path.join(workdir, raw))
        if resolved in seen:
            continue
        if not _writable_path(resolved, workdir=workdir):
            continue
        seen.add(resolved)
        paths.append(resolved)
    return paths


def _parse_json_candidate(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        stripped = _strip_jsonc_comments(text)
        if stripped == text:
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None


def _strip_jsonc_comments(text: str) -> str:
    """Remove // and /* */ comments outside strings.

    This is syntax normalization, not intent detection.  The result still has
    to pass strict json.loads before it can become a file artifact.
    """
    out: list[str] = []
    index = 0
    in_string = False
    escape = False
    while index < len(text):
        ch = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            index += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            index += 1
            continue
        if ch == "/" and nxt == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if ch == "/" and nxt == "*":
            index += 2
            while index + 1 < len(text) and text[index : index + 2] != "*/":
                index += 1
            index = min(index + 2, len(text))
            continue
        out.append(ch)
        index += 1
    return "".join(out)


def _iter_json_values(text: str) -> list[Any]:
    values: list[Any] = []
    for match in _JSON_FENCE_RE.finditer(text or ""):
        parsed = _parse_json_candidate(match.group("body").strip())
        if parsed is not None:
            values.append(parsed)

    index = 0
    while index < len(text or ""):
        start = text.find("{", index)
        if start < 0:
            break
        end = _balanced_object_end(text, start)
        if end is None:
            index = start + 1
            continue
        parsed = _parse_json_candidate(text[start:end])
        if parsed is not None:
            values.append(parsed)
        index = start + 1
    return values


def _balanced_object_end(text: str, start: int) -> int | None:
    depth = 0
    index = start
    in_string = False
    escape = False
    while index < len(text):
        ch = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            index += 1
            continue
        if ch == '"':
            in_string = True
            index += 1
            continue
        if ch == "/" and nxt == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if ch == "/" and nxt == "*":
            index += 2
            while index + 1 < len(text) and text[index : index + 2] != "*/":
                index += 1
            index = min(index + 2, len(text))
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _json_artifact_content(value: Any, *, target_path: str) -> str | None:
    suffix = Path(target_path).suffix.lower()
    if suffix == ".excalidraw":
        if not isinstance(value, dict):
            return None
        if value.get("type") != "excalidraw":
            return None
        if not isinstance(value.get("elements"), list):
            return None
        return json.dumps(value, ensure_ascii=False, indent=2)

    if suffix == ".json":
        if not isinstance(value, (dict, list)):
            return None
        return json.dumps(value, ensure_ascii=False, indent=2)

    return None


def detect_pending_write_action(
    *,
    user_message: str,
    assistant_text: str,
    workdir: str,
) -> PendingWriteAction | None:
    """Return a pending Write when text contains a structural file artifact.

    Preconditions are intentionally narrow:
    - exactly one writable absolute path is present in the user message;
    - exactly one parseable JSON artifact in assistant text matches that path.
    """
    targets = extract_writable_paths(user_message, workdir=workdir)
    if len(targets) != 1:
        return None

    target = targets[0]
    matching_contents: list[str] = []
    for value in _iter_json_values(assistant_text):
        content = _json_artifact_content(value, target_path=target)
        if content is not None:
            matching_contents.append(content)

    unique_contents = list(dict.fromkeys(matching_contents))
    if len(unique_contents) != 1:
        return None

    return PendingWriteAction(
        file_path=target,
        content=unique_contents[0],
        reason="json_artifact_for_requested_path",
    )


def detect_ask_to_write_without_content(
    *,
    user_message: str,
    assistant_text: str,
    workdir: str,
) -> str | None:
    """Return target path when assistant mentions a writable path but has no artifact.

    This detects the pattern where the model asks for confirmation to write a file
    without actually generating the file content.  The check is structural:
    - exactly one writable absolute path in user message;
    - assistant text mentions that path;
    - assistant text contains NO parseable JSON values.
    """
    targets = extract_writable_paths(user_message, workdir=workdir)
    if len(targets) != 1:
        return None

    target = targets[0]

    # Check if assistant text mentions the target path (or its basename)
    basename = os.path.basename(target)
    if target not in assistant_text and basename not in assistant_text:
        return None

    # Text-based approval salvage is only permitted when the target itself pertains
    # to an interrogative fragment. A path from a single paragraph cannot be linked
    # to a standalone or quoted question below the response.
    if not _target_has_local_question(
        assistant_text,
        target=target,
        basename=basename,
    ):
        return None

    # If there are ANY JSON values in assistant text, content exists
    if _iter_json_values(assistant_text):
        return None

    return target


def _target_has_local_question(
    text: str,
    *,
    target: str,
    basename: str,
) -> bool:
    """Verify that after mentioning the `target` locally, the following character is ``?``.

    Structural locality: The question mark must immediately follow the exact path token on the same logical line. This preserves concise approval requests consisting of two sentences but does not link a path from one paragraph to a quoted question in another.
    """
    for token in dict.fromkeys((target, basename)):
        if not token:
            continue
        search_from = 0
        while True:
            token_index = text.find(token, search_from)
            if token_index < 0:
                break
            fragment_start = token_index + len(token)
            question_index = text.find("?", fragment_start)
            if question_index >= 0:
                between = text[fragment_start:question_index]
                if "\n" not in between:
                    return True
            search_from = fragment_start
    return False


# Known file-like JSON type markers — structural, not verbal.
_FILE_LIKE_TYPE_MARKERS = (
    '"type": "excalidraw"',
    '"type":"excalidraw"',
)


def detect_file_content_in_text(
    *,
    user_message: str,
    assistant_text: str,
    workdir: str,
) -> str | None:
    """Return target path when assistant text contains file-like JSON content.

    Detects when the model outputs file content as plain text instead of using
    the Write tool.  Works even if the JSON is truncated (max_tokens hit).

    Structural checks:
    - exactly one writable absolute path in user message;
    - assistant text mentions that path;
    - assistant text contains a file-like JSON type marker (e.g. ``"type":
      "excalidraw"``).
    - no Write tool_use was actually performed (checked by caller).
    """
    targets = extract_writable_paths(user_message, workdir=workdir)
    if len(targets) != 1:
        return None

    target = targets[0]
    basename = os.path.basename(target)
    if target not in assistant_text and basename not in assistant_text:
        return None

    # Check for file-like JSON markers — even in truncated output
    for marker in _FILE_LIKE_TYPE_MARKERS:
        if marker in assistant_text:
            return target

    # Fallback: check file extension against common JSON artifacts
    suffix = Path(target).suffix.lower()
    if suffix in (".excalidraw", ".json"):
        # Look for any substantial JSON-like content (many opening braces)
        if assistant_text.count("{") >= 5:
            return target

    return None
