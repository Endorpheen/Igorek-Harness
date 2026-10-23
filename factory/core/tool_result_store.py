from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import sqlite3
import re
from pathlib import Path
from .artifact_files import atomic_write_file, open_regular_file


PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
STORAGE_DIRNAME = ".tool_results"


@dataclass(frozen=True)
class ToolResultStoreConfig:
    default_threshold_chars: int = 12_000
    preview_head_chars: int = 2_000
    preview_tail_chars: int = 1_200
    turn_budget_chars: int = 40_000

    def threshold_for_tool(self, tool_name: str) -> int | float:
        normalized = str(tool_name or "").strip()
        if normalized == "Read":
            return float("inf")
        if normalized in {"Bash", "Grep"}:
            return 8_000
        return self.default_threshold_chars


DEFAULT_TOOL_RESULT_STORE_CONFIG = ToolResultStoreConfig()


def _storage_dir(workdir: str) -> Path:
    path = Path(workdir) / STORAGE_DIRNAME
    return path


def _persisted_output_path(workdir: str, tool_use_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(tool_use_id or "tool_result")).strip("._") or "tool_result"
    return _storage_dir(workdir) / f"{safe_id}.txt"


def _has_error_markers(content: str) -> bool:
    lowered = (content or "").lower()
    return any(
        marker in lowered
        for marker in (
            "traceback",
            "error",
            "exception",
            "stderr",
            "failed",
            "exit code:",
        )
    )


def _extract_exit_code(content: str) -> str | None:
    match = re.search(r"\(exit code:\s*(\d+)\)", content or "", flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _line_count(content: str) -> int:
    if not content:
        return 0
    return content.count("\n") + 1


def _preview_head(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    chunk = content[:max_chars]
    last_nl = chunk.rfind("\n")
    if last_nl > max_chars // 2:
        chunk = chunk[:last_nl]
    return chunk.rstrip()


def _preview_tail(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    chunk = content[-max_chars:]
    first_nl = chunk.find("\n")
    if 0 <= first_nl < max_chars // 2:
        chunk = chunk[first_nl + 1 :]
    return chunk.rstrip()


def _build_summary(
    *,
    content: str,
    tool_name: str,
    file_path: str,
    config: ToolResultStoreConfig,
) -> str:
    chars = len(content)
    lines = _line_count(content)
    exit_code = _extract_exit_code(content)
    has_errors = _has_error_markers(content)
    head = _preview_head(content, config.preview_head_chars)
    tail = _preview_tail(content, config.preview_tail_chars)

    bits = [
        PERSISTED_OUTPUT_TAG,
        f"Tool `{tool_name}` returned a large result and it was saved to disk.",
        f"Full output saved to: {file_path}",
        "If you need more detail, use the Read tool on this file.",
        "",
        "Summary:",
        f"- Characters: {chars}",
        f"- Lines: {lines}",
        f"- Error markers present: {'yes' if has_errors else 'no'}",
    ]
    if exit_code is not None:
        bits.append(f"- Exit code: {exit_code}")
    bits.extend(
        [
            "",
            f"Head preview (first {len(head)} chars):",
            head or "(empty)",
        ]
    )
    if tail and tail != head:
        bits.extend(
            [
                "",
                f"Tail preview (last {len(tail)} chars):",
                tail,
            ]
        )
    bits.append(PERSISTED_OUTPUT_CLOSING_TAG)
    return "\n".join(bits)


def _persist_content(workdir: str, tool_use_id: str, content: str) -> str:
    path = _persisted_output_path(workdir, tool_use_id).absolute()
    data = content.encode("utf-8", errors="replace")
    for candidate in (path, path.with_name(path.stem[:100] + "-" + hashlib.sha256(data).hexdigest()[:16] + ".txt")):
        try:
            with open_regular_file(candidate) as handle:
                if os.fstat(handle.fileno()).st_size == len(data) and handle.read(len(data) + 1) == data:
                    return str(candidate)
        except FileNotFoundError:
            atomic_write_file(candidate, data, mode=0o600, preserve_mode=False)
            return str(candidate)
    raise ValueError("A saved result identity was reused with different content")


def maybe_persist_tool_result(
    *,
    content: str,
    tool_name: str,
    tool_use_id: str,
    workdir: str,
    config: ToolResultStoreConfig = DEFAULT_TOOL_RESULT_STORE_CONFIG,
    threshold: int | None = None,
    access=None,
    already_bounded: bool = False,
) -> str:
    if already_bounded or str(tool_name).strip() == "Read":
        return content
    effective_threshold = threshold if threshold is not None else config.threshold_for_tool(tool_name)
    if len(content) <= effective_threshold:
        return content

    try:
        storage = workdir
        if access is not None:
            if os.path.normcase(os.path.realpath(workdir)) != access.artifact_workdir:
                raise ValueError("Result storage does not match the issuing runtime")
            storage = access.artifact_workdir
        file_path = _persist_content(storage, tool_use_id, content)
        if access is not None:
            access.register(file_path)
    except (OSError, ValueError, sqlite3.Error):
        return (content[:config.preview_head_chars] + "\n\n[Output truncated: the full result could not be saved for Read. "
                "Do not assume the producing action failed or repeat it without checking its outcome.]\n\n" + content[-config.preview_tail_chars:])
    return _build_summary(content=content, tool_name=tool_name, file_path=file_path, config=config)


def enforce_turn_budget(
    tool_results: list[dict[str, str]],
    *,
    workdir: str,
    config: ToolResultStoreConfig = DEFAULT_TOOL_RESULT_STORE_CONFIG,
    access=None,
) -> list[dict[str, str]]:
    total = sum(len(str(item.get("content") or "")) for item in tool_results)
    if total <= config.turn_budget_chars:
        return tool_results

    candidates = []
    for idx, item in enumerate(tool_results):
        content = str(item.get("content") or "")
        if item.get("bounded") or item.get("tool_name") == "Read" or PERSISTED_OUTPUT_TAG in content:
            continue
        candidates.append((idx, len(content)))

    candidates.sort(key=lambda item: item[1], reverse=True)
    for idx, _size in candidates:
        if total <= config.turn_budget_chars:
            break
        item = tool_results[idx]
        original = str(item.get("content") or "")
        replacement = maybe_persist_tool_result(
            content=original,
            tool_name=str(item.get("tool_name") or ""),
            tool_use_id=str(item.get("tool_use_id") or f"tool_result_{idx}"),
            workdir=workdir,
            config=config,
            threshold=0,
            access=access,
        )
        if replacement == original:
            continue
        total -= len(original)
        total += len(replacement)
        item["content"] = replacement

    return tool_results
