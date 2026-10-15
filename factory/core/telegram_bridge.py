from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import html
import json
import mimetypes
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

try:
    from .artifact_files import open_regular_file
    from .agent_foundation import (
        extract_memory_write_blocks,
        strip_memory_status_lines,
    )
    from .cli_chat import (
        _append_entry,
        _clear_session,
        _history_tail,
        _load_entries,
        _sanitize_session_name,
        _session_path,
        archive_session_thread,
    )
    from .cli_tasks import cancel_task, get_task, resolve_workspace_file_paths, resume_waiting_approval_task
    from .user_input_store import list_pending_user_input_requests, resolve_user_input_request
    from .codex_provider import (
        CODEX_OPENROUTER_API_KEY_ENV,
        OPENROUTER_API_KEY_ENV,
        suggested_models_for_provider,
        user_facing_codex_provider,
    )
    from .custom_emoji_registry import list_all as _list_custom_emojis
    from .gateway_runtime import (
        GatewayAppServerState,
        GatewayMessage,
        GatewayPendingApproval,
        GatewayRuntime,
        GatewaySource,
    )
    from .memory_manager import MemoryManager
    from .runtime import get_data_root, get_workspace_root, get_profile_config, profile_backend_name, profile_model_name
except ImportError:  # pragma: no cover - direct script fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.artifact_files import open_regular_file
    from core.agent_foundation import (
        extract_memory_write_blocks,
        strip_memory_status_lines,
    )
    from core.cli_chat import (
        _append_entry,
        _clear_session,
        _history_tail,
        _load_entries,
        _sanitize_session_name,
        _session_path,
        archive_session_thread,
    )
    from core.cli_tasks import cancel_task, get_task, resolve_workspace_file_paths, resume_waiting_approval_task
    from core.user_input_store import list_pending_user_input_requests, resolve_user_input_request
    from core.codex_provider import (
        CODEX_OPENROUTER_API_KEY_ENV,
        OPENROUTER_API_KEY_ENV,
        suggested_models_for_provider,
        user_facing_codex_provider,
    )
    from core.gateway_runtime import (
        GatewayAppServerState,
        GatewayMessage,
        GatewayPendingApproval,
        GatewayRuntime,
        GatewaySource,
    )
    from core.memory_manager import MemoryManager
    from core.runtime import get_data_root, get_workspace_root, get_profile_config, profile_backend_name, profile_model_name


def _ensure_text(value: Any) -> str:
    return str(value or "").strip()


def _chunk_text(text: str, limit: int = 3500) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    chunks: list[str] = []
    current = normalized
    while len(current) > limit:
        split_at = current.rfind("\n", 0, limit)
        if split_at < limit // 3:
            split_at = current.rfind(" ", 0, limit)
        if split_at < limit // 3:
            split_at = limit
        chunks.append(current[:split_at].rstrip())
        current = current[split_at:].lstrip()
    if current:
        chunks.append(current)
    return chunks


_TELEGRAM_SAFE_CHUNK_LIMIT = 3500
_TELEGRAM_LONG_THINK_LIMIT = 2400
_TELEGRAM_RICH_CHUNK_LIMIT = 30000


def _strip_persisted_output_leak(text: str) -> str:
    cleaned = re.sub(r"(?s)<persisted-output>.*?</persisted-output>", " ", text or "")
    cleaned = re.sub(r"(?im)^.*\\.tool_results/\\S+.*$", " ", cleaned)
    cleaned = re.sub(r"(?im)^Вложения отправлены:.*$", " ", cleaned)
    return cleaned.strip()


def _prepare_long_think_fallbacks(text: str) -> tuple[str, list[dict[str, str]]]:
    attachments: list[dict[str, str]] = []

    def replace_block(match: re.Match[str]) -> str:
        thinking = (match.group(1) or "").strip()
        if len(thinking) <= _TELEGRAM_LONG_THINK_LIMIT:
            return match.group(0)
        index = len(attachments) + 1
        attachments.append(
            {
                "filename": f"model_thoughts_{index}.txt",
                "content": thinking,
            }
        )
        return (
            "\n"
            f"Размышления модели слишком длинные для цитаты Telegram "
            f"({len(thinking)} символов). Отправляю отдельным файлом: model_thoughts_{index}.txt"
            "\n"
        )

    prepared = re.sub(r"(?is)<think>\s*(.*?)\s*</think>", replace_block, text or "")
    return prepared, attachments


def _telegram_rich_messages_enabled() -> bool:
    return str(os.environ.get("FACTORY_TELEGRAM_RICH_MESSAGES", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _convert_markdown_to_telegram_html(text: str) -> str:
    normalized = text.strip()
    if not normalized:
        return ""

    think_blocks: list[str] = []
    code_blocks: list[str] = []

    def store_think_block(match: re.Match[str]) -> str:
        thinking = (match.group(1) or "").strip()
        if not thinking:
            return ""
        escaped_thinking = html.escape(thinking)
        rendered = (
            "<blockquote expandable>"
            "<b>Размышления модели</b>\n"
            f"{escaped_thinking}"
            "</blockquote>"
        )
        think_blocks.append(rendered)
        return f"\n@@THINKBLOCK_{len(think_blocks) - 1}@@\n"

    def store_code_block(match: re.Match[str]) -> str:
        language = _ensure_text(match.group(1))
        code = match.group(2) or ""
        escaped_code = html.escape(code.strip("\n"))
        if language:
            escaped_language = html.escape(language)
            rendered = f"<pre><code class=\"language-{escaped_language}\">{escaped_code}</code></pre>"
        else:
            rendered = f"<pre>{escaped_code}</pre>"
        code_blocks.append(rendered)
        return f"@@CODEBLOCK_{len(code_blocks) - 1}@@"

    rendered = re.sub(r"(?is)<think\s*/\s*>", "", normalized)
    rendered = re.sub(r"(?is)<think>\s*(.*?)\s*</think>", store_think_block, rendered)
    rendered = re.sub(r"```([a-zA-Z0-9_+\-]*)\n(.*?)```", store_code_block, rendered, flags=re.DOTALL)
    rendered = html.escape(rendered)
    rendered = re.sub(r"(?is)&lt;think\b[^&]*?(?:&gt;|/&gt;)\s*", "", rendered)
    rendered = re.sub(r"`([^`\n]+)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", rendered)
    rendered = re.sub(r"\*\*([^\n*][^*]*?)\*\*", r"<b>\1</b>", rendered)
    rendered = re.sub(r"(?<!\*)\*([^\n*][^*]*?)\*(?!\*)", r"<i>\1</i>", rendered)
    rendered = re.sub(r"__([^\n_][^_]*?)__", r"<u>\1</u>", rendered)
    rendered = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<a href=\"\2\">\1</a>", rendered)
    rendered = rendered.replace("\r\n", "\n").replace("\r", "\n")
    for index, block in enumerate(think_blocks):
        rendered = rendered.replace(f"@@THINKBLOCK_{index}@@", block)
    for index, block in enumerate(code_blocks):
        rendered = rendered.replace(f"@@CODEBLOCK_{index}@@", block)
    return rendered


def _sanitize_filename(name: str, *, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or fallback


def _build_custom_emoji_map() -> dict[str, str]:
    """Build fallback → custom_emoji_id mapping from registry."""
    result: dict[str, str] = {}
    for entry in _list_custom_emojis():
        fallback = entry.get("fallback", "")
        emoji_id = entry.get("id", "")
        if fallback and emoji_id:
            result[fallback] = emoji_id
    return result


_CUSTOM_EMOJI_MAP: dict[str, str] | None = None


def _get_custom_emoji_map() -> dict[str, str]:
    """Lazily load and cache the custom emoji map."""
    global _CUSTOM_EMOJI_MAP
    if _CUSTOM_EMOJI_MAP is None:
        _CUSTOM_EMOJI_MAP = _build_custom_emoji_map()
    return _CUSTOM_EMOJI_MAP


def _inject_custom_emojis(html_text: str) -> str:
    """Replace fallback emojis with <tg-emoji> tags for Telegram custom emojis.

    Only replaces outside <pre>, <code> blocks.
    """
    emoji_map = _get_custom_emoji_map()
    if not emoji_map:
        return html_text

    # Split HTML into segments: code blocks vs regular text
    # Pattern matches <pre>...</pre> and <code>...</code> (including nested)
    code_pattern = re.compile(r"(<(?:pre|code)[^>]*>.*?</(?:pre|code)>)", re.DOTALL)

    parts = code_pattern.split(html_text)
    result_parts: list[str] = []

    for i, part in enumerate(parts):
        if code_pattern.match(part):
            # Inside code block — don't touch
            result_parts.append(part)
        else:
            # Regular text — replace emojis
            for fallback, emoji_id in emoji_map.items():
                if fallback in part:
                    part = part.replace(
                        fallback,
                        f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>',
                    )
            result_parts.append(part)

    return "".join(result_parts)


def _is_image_document(message: dict[str, Any]) -> bool:
    document = message.get("document")
    if not isinstance(document, dict):
        return False
    mime_type = _ensure_text(document.get("mime_type")).lower()
    if mime_type.startswith("image/"):
        return True
    file_name = _ensure_text(document.get("file_name")).lower()
    return file_name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))


def _output_file_roots(base_dir: Path, workspace_task_id: str = "") -> tuple[Path, ...]:
    roots = [base_dir.expanduser().resolve()]
    if workspace_task_id and re.fullmatch(r"[A-Za-z0-9_-]+", workspace_task_id):
        workspace = Path(get_workspace_root()).expanduser().resolve()
        for candidate in (
            workspace / workspace_task_id / "results",
            workspace / f"temp_execution_{workspace_task_id[:8]}",
        ):
            try:
                resolved = candidate.resolve()
                if resolved == candidate and resolved.is_relative_to(workspace):
                    roots.append(resolved)
            except (OSError, RuntimeError, ValueError):
                continue
    return tuple(dict.fromkeys(roots))


def _check_output_file_path(
    base_dir: Path, raw_path: str, *, allowed_roots: tuple[Path, ...] | None = None,
) -> tuple[Path | None, str]:
    try:
        if not raw_path or "\x00" in raw_path:
            return None, "invalid_path"
        roots = tuple(root.expanduser().resolve() for root in (
            allowed_roots if allowed_roots is not None else (base_dir,)
        ))
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        candidate = candidate.resolve()
        if not any(candidate.is_relative_to(root) for root in roots):
            return None, "outside_allowed_roots"
        if not candidate.exists():
            return None, "missing"
        if not candidate.is_file():
            return None, "not_file"
        return candidate, "ok"
    except (OSError, RuntimeError, ValueError):
        return None, "invalid_path"


def _resolve_output_file_path(
    base_dir: Path, raw_path: str, *, allowed_roots: tuple[Path, ...] | None = None,
) -> Path | None:
    return _check_output_file_path(base_dir, raw_path, allowed_roots=allowed_roots)[0]


def _is_deliverable_output_file(path: Path) -> bool:
    artifact_dirs = {".tool_results", ".checkpoints", ".codex"}
    artifact_files = {
        ".codex",
        "input.json",
        "result.json",
        "copy_metadata.json",
        "cc_backend_trace.jsonl",
    }
    for part in path.parts:
        if part in artifact_dirs:
            return False
    if path.name in artifact_files:
        return False
    try:
        if path.stat().st_size <= 0:
            return False
    except OSError:
        return False
    return True


def _output_file_signature(path: Path) -> str:
    """Cheap content-change signature (mtime + size) for delivered-file dedup.

    A mutable output file (e.g. a spreadsheet updated several times a day) must
    be re-delivered whenever it changes, while an unchanged file must not be
    resent. Path-only dedup would silently drop every update after the first.
    """
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _strip_redundant_quotes(candidate: str) -> str:
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in ("'", '"'):
        return candidate[1:-1].strip()
    return candidate


def _text_output_file_candidates(text: str, *, absolute: bool) -> list[str]:
    remaining = str(text or "")
    delimited: list[str] = []

    markdown_spans: list[tuple[int, int, str]] = []
    for match in re.finditer(r"!?\[[^\]\n]*\]\(", remaining):
        start = match.end()
        depth = 1
        index = start
        while index < len(remaining) and depth:
            if remaining[index] == "(":
                depth += 1
            elif remaining[index] == ")":
                depth -= 1
            index += 1
        if depth == 0:
            markdown_spans.append((match.start(), index, remaining[start:index - 1]))
    for start, end, target in reversed(markdown_spans):
        delimited.append(_strip_redundant_quotes(target.strip()))
        remaining = remaining[:start] + (" " * (end - start)) + remaining[end:]

    for pattern in (r"`([^`\n]+)`", r'"([^"\n]+)"', r"'([^'\n]+)'"):
        matches = list(re.finditer(pattern, remaining))
        for match in matches:
            delimited.append(_strip_redundant_quotes(match.group(1).strip()))
        for match in reversed(matches):
            remaining = (
                remaining[:match.start()]
                + (" " * (match.end() - match.start()))
                + remaining[match.end():]
            )

    candidates = []
    for candidate in delimited:
        is_absolute = candidate.startswith(("/", "~/"))
        if is_absolute == absolute:
            candidates.append(candidate)
    for token in re.findall(r"[^\s<>()\"'`*|]+", remaining):
        token = token.lstrip("[{«").rstrip(".,:;!?]}»")
        is_absolute = token.startswith(("/", "~/"))
        if is_absolute != absolute:
            continue
        if is_absolute or re.fullmatch(
            r"(?:\.\.?/)*[\w][\w./-]*\.(?:mp3|m4a|wav|flac|ogg|opus|aac|mp4|mov|webm|gif|png|jpe?g|webp|pdf|zip|txt|md|xlsx)",
            token, flags=re.IGNORECASE,
        ):
            candidates.append(token)
    return candidates


def _collect_output_file_paths(
    base_dir: Path, text_items: list[str], explicit_files: list[str], *,
    allowed_roots: tuple[Path, ...] | None = None,
) -> list[str]:
    candidates = list(explicit_files)
    for absolute in (True, False):
        for text in text_items:
            candidates.extend(_text_output_file_candidates(text, absolute=absolute))
    resolved: list[str] = []
    seen: set[str] = set()
    for raw_path in candidates:
        path = _resolve_output_file_path(base_dir, _ensure_text(raw_path), allowed_roots=allowed_roots)
        if path is None or not _is_deliverable_output_file(path):
            continue
        normalized = str(path)
        if normalized not in seen:
            seen.add(normalized)
            resolved.append(normalized)
    return resolved


def _extract_existing_file_paths(
    base_dir: Path, text: str, *, allowed_roots: tuple[Path, ...] | None = None,
) -> list[str]:
    return _collect_output_file_paths(
        base_dir, [], _text_output_file_candidates(text, absolute=True), allowed_roots=allowed_roots,
    )


def _extract_named_workdir_files(base_dir: Path, text: str) -> list[str]:
    return _collect_output_file_paths(base_dir, [], _text_output_file_candidates(text, absolute=False))


def _build_workspace_context_entry(
    *,
    entries: list[dict],
    workspace_task_id: str | None,
    base_dir: Path,
    limit: int = 8,
) -> dict[str, str] | None:
    workspace_id = _ensure_text(workspace_task_id)
    if not workspace_id:
        return None

    recent_paths: list[str] = []
    seen_paths: set[str] = set()

    def remember(path: str) -> None:
        normalized = _ensure_text(path)
        if not normalized:
            return
        if normalized in seen_paths:
            recent_paths.remove(normalized)
        else:
            seen_paths.add(normalized)
        recent_paths.append(normalized)

    for entry in entries:
        if entry.get("role") != "assistant":
            continue
        task_id = _ensure_text(entry.get("workspace_task_id") or entry.get("task_id") or workspace_id)
        roots = _output_file_roots(base_dir, task_id)
        workspace_files = entry.get("workspace_files")
        explicit_files = []
        if isinstance(workspace_files, list) and re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
            explicit_files = resolve_workspace_file_paths(task_id, [str(item) for item in workspace_files])
        for path in _collect_output_file_paths(base_dir, [], explicit_files, allowed_roots=roots):
            remember(path)
        for path in _extract_existing_file_paths(base_dir, _ensure_text(entry.get("text")), allowed_roots=roots):
            remember(path)

    if not recent_paths:
        return None

    selected = recent_paths[-limit:]
    lines = [
        "Workspace follow-up context:",
        f"workspace_task_id: {workspace_id}",
        "recent_workspace_files:",
    ]
    lines.extend(f"- {path}" for path in selected)
    non_image = [p for p in selected if Path(p).suffix.lower() not in {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"
    }]
    if len(non_image) == 1:
        lines.append(f"active_file_candidate: {non_image[0]}")
    elif len(non_image) > 1:
        lines.append("active_file_candidate: ambiguous")
    else:
        lines.append("active_file_candidate: none")
    return {"role": "assistant", "text": "\n".join(lines)}


PendingApproval = GatewayPendingApproval

# Polling resilience / media download limits.
_POLL_BASE_BACKOFF_S = 3.0
_POLL_MAX_BACKOFF_S = 30.0
_POLL_WARN_EVERY = 3
_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
_MEDIA_READ_TIMEOUT_S = 60.0


def _max_download_bytes() -> int:
    """Download cap in bytes (default 100 MB; override FACTORY_TELEGRAM_MAX_DOWNLOAD_MB).

    Note: the cloud Telegram Bot API only serves files up to 20 MB to bots;
    higher caps require a self-hosted local Bot API server.
    """
    raw = os.environ.get("FACTORY_TELEGRAM_MAX_DOWNLOAD_MB", "").strip()
    if raw:
        try:
            mb = int(float(raw))
            if mb > 0:
                return mb * 1024 * 1024
        except ValueError:
            pass
    return _MAX_DOWNLOAD_BYTES


def _next_poll_backoff(failures: int) -> float:
    """Exponential backoff (capped) for consecutive Telegram polling failures."""
    if failures <= 0:
        return 0.0
    return min(_POLL_BASE_BACKOFF_S * (2 ** (failures - 1)), _POLL_MAX_BACKOFF_S)


@dataclass
class ChatRuntimeState:
    chat_id: str
    user_id: str
    username: str = ""
    chat_session: str = ""
    profile_override: str | None = None
    provider_override: str | None = None
    model_override: str | None = None
    thread_id: str | None = None
    runtime_session_id: str | None = None
    event_after_seq: int = 0
    pending_approvals: list[PendingApproval] = field(default_factory=list)
    item_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    recent_file_paths: list[str] = field(default_factory=list)
    workspace_task_id: str | None = None
    pending_task_id: str | None = None
    last_user_message_id: int | None = None
    delivered_files: dict[str, str] = field(default_factory=dict)
    smarthelp_active: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_json(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "username": self.username,
            "chat_session": self.chat_session,
            "profile_override": self.profile_override,
            "provider_override": self.provider_override,
            "model_override": self.model_override,
            "thread_id": self.thread_id,
            "runtime_session_id": self.runtime_session_id,
            "event_after_seq": self.event_after_seq,
            "workspace_task_id": self.workspace_task_id,
            "pending_task_id": self.pending_task_id,
            "delivered_files": dict(self.delivered_files),
            "smarthelp_active": self.smarthelp_active,
        }


class TelegramApiUnavailableError(RuntimeError):
    """Telegram API could not be reached because of a transport-level error."""


class TelegramBotApi:
    def __init__(self, token: str, *, timeout_s: int = 60) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.file_base_url = f"https://api.telegram.org/file/bot{token}"
        self.timeout_s = timeout_s

    def get_updates(self, *, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": max(1, timeout)}
        if offset is not None:
            payload["offset"] = offset
        data = self._request_json("getUpdates", payload)
        result = data.get("result")
        return result if isinstance(result, list) else []

    def get_me(self) -> dict[str, Any]:
        data = self._request_json("getMe", {})
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        self._request_json("setMyCommands", {"commands": commands})

    def get_file(self, file_id: str) -> dict[str, Any]:
        data = self._request_json("getFile", {"file_id": file_id})
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    def download_file_bytes(
        self,
        file_path: str,
        *,
        max_bytes: int | None = None,
        read_timeout_s: float | None = None,
    ) -> bytes:
        limit = _max_download_bytes() if max_bytes is None else int(max_bytes)
        read_timeout = _MEDIA_READ_TIMEOUT_S if read_timeout_s is None else float(read_timeout_s)
        req = urllib_request.Request(f"{self.file_base_url}/{file_path}", method="GET")
        try:
            with urllib_request.urlopen(req, timeout=read_timeout) as resp:
                declared = resp.headers.get("Content-Length") if resp.headers else None
                if declared is not None:
                    try:
                        if int(declared) > limit:
                            raise RuntimeError(
                                f"Telegram file too large: {declared} > {limit} bytes"
                            )
                    except ValueError:
                        pass
                data = resp.read(limit + 1)
        except urllib_error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram file download failed: HTTP {exc.code}: {details}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"Telegram file download unavailable: {exc.reason}") from exc
        if len(data) > limit:
            raise RuntimeError(f"Telegram file exceeds {limit} bytes limit")
        return data

    def _send_rich_message_chunk(
        self,
        chat_id: str | int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "rich_message": {
                "markdown": text,
            },
        }
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        return self._request_json("sendRichMessage", payload)

    def _send_html_message_chunk(
        self,
        chat_id: str | int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "parse_mode": "HTML",
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            return self._request_json("sendMessage", payload)
        except Exception:
            fallback_payload = {
                "chat_id": chat_id,
                "text": html.unescape(re.sub(r"<[^>]+>", "", text)),
                "disable_web_page_preview": True,
            }
            if reply_to_message_id is not None:
                fallback_payload["reply_to_message_id"] = reply_to_message_id
            return self._request_json("sendMessage", fallback_payload)

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        text, fallback_documents = _prepare_long_think_fallbacks(text)
        last_response: dict[str, Any] | None = None
        html_fallback_text = text

        # Rich messages do not support reply_markup, so approval is sent via the standard API.
        if _telegram_rich_messages_enabled() and reply_markup is None:
            rich_chunks = _chunk_text(text, limit=_TELEGRAM_RICH_CHUNK_LIMIT) or [""]
            for index, chunk in enumerate(rich_chunks):
                try:
                    last_response = self._send_rich_message_chunk(
                        chat_id,
                        chunk,
                        reply_to_message_id=reply_to_message_id,
                    )
                except Exception as exc:
                    print(
                        f"[telegram] rich message failed ({type(exc).__name__}: {exc}), falling back to HTML",
                        flush=True,
                    )
                    html_fallback_text = "\n\n".join(rich_chunks[index:])
                    last_response = None
                    break

        if last_response is None:
            html_text = _convert_markdown_to_telegram_html(html_fallback_text)
            html_text = _inject_custom_emojis(html_text)
            chunks = _chunk_text(html_text, limit=_TELEGRAM_SAFE_CHUNK_LIMIT) or [""]
            for chunk in chunks:
                last_response = self._send_html_message_chunk(
                    chat_id,
                    chunk,
                    reply_to_message_id=reply_to_message_id,
                    reply_markup=reply_markup if len(chunks) == 1 else None,
                )
        for document in fallback_documents:
            with tempfile.TemporaryDirectory(prefix="igorek_model_thoughts_") as tmp_dir:
                tmp_path = Path(tmp_dir) / document["filename"]
                tmp_path.write_text(document["content"], encoding="utf-8")
                self.send_document(chat_id, tmp_path, caption=document["filename"])
        return last_response

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        self._request_json(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text[:200]},
        )

    def edit_message_text(self, chat_id: str | int, message_id: str | int, text: str) -> dict[str, Any] | None:
        html_text = _convert_markdown_to_telegram_html(text)
        html_text = _inject_custom_emojis(html_text)
        chunks = _chunk_text(html_text, limit=_TELEGRAM_SAFE_CHUNK_LIMIT) or [""]
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": chunks[-1],
            "disable_web_page_preview": True,
            "parse_mode": "HTML",
        }
        try:
            return self._request_json("editMessageText", payload)
        except RuntimeError as exc:
            msg = str(exc)
            if "message is not modified" in msg:
                return None
            fallback_payload = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": html.unescape(re.sub(r"<[^>]+>", "", chunks[-1])),
                "disable_web_page_preview": True,
            }
            try:
                return self._request_json("editMessageText", fallback_payload)
            except RuntimeError as exc2:
                if "message is not modified" in str(exc2):
                    return None
                raise

    def send_chat_action(self, chat_id: str | int, action: str = "typing") -> None:
        self._request_json(
            "sendChatAction",
            {
                "chat_id": chat_id,
                "action": action,
            },
        )

    def send_document(self, chat_id: str | int, file_path: str | Path, *, caption: str | None = None) -> None:
        path = Path(file_path).expanduser().absolute()
        with open_regular_file(path) as stream:
            content = stream.read(50 * 1024 * 1024 + 1)
        if len(content) > 50 * 1024 * 1024:
            raise RuntimeError("Telegram attachment exceeds the 50 MiB read limit")
        boundary = f"----IgorekBoundary{uuid.uuid4().hex}"
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(f"--{boundary}\r\n".encode("utf-8"))
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            parts.append(value.encode("utf-8"))
            parts.append(b"\r\n")

        add_field("chat_id", str(chat_id))
        if caption:
            add_field("caption", caption)
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            (
                f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(content)
        parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        req = urllib_request.Request(
            f"{self.base_url}/sendDocument",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram API sendDocument failed: HTTP {exc.code}: {details}") from exc
        except urllib_error.URLError as exc:
            raise TelegramApiUnavailableError(
                f"Telegram API sendDocument unavailable: {exc.reason}"
            ) from exc
        parsed = json.loads(raw) if raw.strip() else {}
        if not isinstance(parsed, dict) or not parsed.get("ok"):
            raise RuntimeError(f"Telegram API sendDocument failed: {raw}")

    def _request_json(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = urllib_parse.urlencode(
            {
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                for key, value in payload.items()
                if value is not None
            }
        ).encode("utf-8")
        req = urllib_request.Request(
            f"{self.base_url}/{method}",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram API {method} failed: HTTP {exc.code}: {details}") from exc
        except urllib_error.URLError as exc:
            raise TelegramApiUnavailableError(
                f"Telegram API {method} unavailable: {exc.reason}"
            ) from exc
        parsed = json.loads(raw) if raw.strip() else {}
        if not isinstance(parsed, dict) or not parsed.get("ok"):
            raise RuntimeError(f"Telegram API {method} failed: {raw}")
        return parsed


class TelegramToolProgress:
    """Hermes-style Telegram tool progress: one accumulated message, edited in place."""

    EDIT_INTERVAL_S = 1.5
    PREAMBLE_MAX_CHARS = 500
    EDIT_BACKOFF_BASE_S = 1.5
    EDIT_MAX_BACKOFF_S = 10.0
    EDIT_MAX_FAILURES = 3

    def __init__(self, bridge: "TelegramBridge", chat_id: str):
        self.bridge = bridge
        self.chat_id = chat_id
        self.lines: list[str] = []
        self.message_id: str | int | None = None
        self.can_edit = True
        self.last_message: str | None = None
        self.repeat_count = 0
        self.last_edit_ts = 0.0
        self.edit_failures = 0
        self.next_edit_allowed_ts = 0.0
        self.lock = threading.Lock()

    def callback(self, event_type: str, **kwargs: Any) -> None:
        if event_type == "assistant.text":
            text = _ensure_text(kwargs.get("text"))
            if not text:
                return
            text = text.strip()
            if len(text) > self.PREAMBLE_MAX_CHARS:
                text = text[: self.PREAMBLE_MAX_CHARS].rstrip() + "…"
            line = f"💬 {text}"
            with self.lock:
                if line == self.last_message:
                    return
                self.last_message = line
                self.repeat_count = 0
                self.lines.append(line)
                self._flush_locked(force=True)
            return
        if event_type != "tool.started":
            return
        tool_name = _ensure_text(kwargs.get("tool_name") or kwargs.get("name"))
        if not tool_name:
            return
        # Approval is displayed as a message with buttons that does not include an internal tool call.
        if tool_name == "suggest_enable_mcp":
            return
        args = kwargs.get("args") if isinstance(kwargs.get("args"), dict) else {}
        preview = _ensure_text(kwargs.get("preview"))
        try:
            from .smarthelp import _TOOL_EMOJI_MAP, build_tool_preview
        except ImportError:  # pragma: no cover
            from core.smarthelp import _TOOL_EMOJI_MAP, build_tool_preview
        if not preview:
            preview = build_tool_preview(tool_name, args)
        emoji = _TOOL_EMOJI_MAP.get(tool_name, "⚙️")
        message = f"{emoji} {tool_name}: \"{preview}\"" if preview else f"{emoji} {tool_name}..."

        with self.lock:
            if message == self.last_message and self.lines:
                self.repeat_count += 1
                self.lines[-1] = f"{message} (×{self.repeat_count + 1})"
            else:
                self.last_message = message
                self.repeat_count = 0
                self.lines.append(message)
            self._flush_locked(force=False)

    def finish(self) -> None:
        with self.lock:
            self._flush_locked(force=True)

    def _flush_locked(self, *, force: bool) -> None:
        if not self.lines:
            return
        now = time.monotonic()
        if not force:
            # Edit flood-control: once edits start failing, stop sending new
            # intermediate messages too (avoid spamming the chat).
            if not self.can_edit and self.message_id is not None:
                return
            if (
                self.message_id is not None
                and now - self.last_edit_ts < self.EDIT_INTERVAL_S
            ):
                return
            if now < self.next_edit_allowed_ts:
                return
        text = "\n".join(self.lines)
        if self.message_id is not None and self.can_edit:
            try:
                self.bridge.telegram.edit_message_text(self.chat_id, self.message_id, text)
                self.last_edit_ts = now
                self.edit_failures = 0
                self.next_edit_allowed_ts = 0.0
                return
            except Exception as exc:
                print(f"[telegram] progress edit failed: {exc}")
                self.edit_failures += 1
                self.next_edit_allowed_ts = now + min(
                    self.EDIT_BACKOFF_BASE_S * (2 ** (self.edit_failures - 1)),
                    self.EDIT_MAX_BACKOFF_S,
                )
                if self.edit_failures >= self.EDIT_MAX_FAILURES:
                    self.can_edit = False
                if not force:
                    return
        sent = self.bridge._safe_send(self.chat_id, text)
        if isinstance(sent, dict):
            result = sent.get("result") if isinstance(sent.get("result"), dict) else sent
            msg_id = result.get("message_id") if isinstance(result, dict) else None
            if msg_id:
                self.message_id = msg_id
        self.last_edit_ts = now


class TelegramBridge:
    _TASK_MEMORY_MESSAGES = 30
    _TASK_MEMORY_CHARS = 50_000

    def __init__(
        self,
        *,
        token: str,
        profile: str,
        runtime_api_base_url: str,
        runtime_api_key: str,
        allowed_user_ids: set[str],
        allowed_chat_ids: set[str] | None = None,
        workdir: str | None = None,
        poll_timeout_s: int = 30,
        auto_approve: bool = False,
        auto_approve_scope: str = "session",
    ) -> None:
        self.profile = profile
        self.allowed_user_ids = {str(item).strip() for item in allowed_user_ids if str(item).strip()}
        self.allowed_chat_ids = {str(item).strip() for item in (allowed_chat_ids or set()) if str(item).strip()}
        if not self.allowed_user_ids:
            raise ValueError("Telegram bridge requires at least one allowed user_id")
        self.workdir = str(Path(workdir).expanduser().resolve()) if workdir else str(Path.cwd())
        self.poll_timeout_s = max(1, poll_timeout_s)
        self.auto_approve = bool(auto_approve)
        normalized_scope = auto_approve_scope.strip().lower()
        self.auto_approve_scope = normalized_scope if normalized_scope in {"turn", "session"} else "session"
        self.telegram = TelegramBotApi(token)
        self.gateway_runtime = GatewayRuntime(
            profile=profile,
            workdir=self.workdir,
            runtime_api_base_url=runtime_api_base_url,
            runtime_api_key=runtime_api_key,
        )
        self.offset: int | None = None
        self._sessions_path = Path(get_data_root()) / "telegram" / "sessions.json"
        self._uploads_dir = Path(get_data_root()) / "telegram" / "uploads"
        self._sessions_lock = threading.RLock()
        self._known_sessions: dict[str, ChatRuntimeState] = self._load_sessions()
        # A limited executor prevents unlimited growth of processes and file descriptors (FD).
        self._update_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="telegram-update")
        self.memory_manager = MemoryManager()

    @staticmethod
    def _bot_commands() -> list[dict[str, str]]:
        return [
            {"command": "help", "description": "Показать справку"},
            {"command": "session", "description": "Текущая runtime session"},
            {"command": "provider", "description": "Показать или сменить provider"},
            {"command": "models", "description": "Список моделей для provider"},
            {"command": "model", "description": "Показать или сменить модель"},
            {"command": "profile", "description": "Показать или сменить профиль"},
            {"command": "plugins", "description": "Нативная команда Codex plugins"},
            {"command": "approvals", "description": "Показать pending approvals"},
            {"command": "approve", "description": "Одобрить approval"},
            {"command": "approve_session", "description": "Одобрить approval до конца session"},
            {"command": "reject", "description": "Отклонить approval"},
            {"command": "reset", "description": "Сбросить chat session"},
            {"command": "remember", "description": "Записать факт в долговременную память"},
            {"command": "smarthelp", "description": "Вопрос через OpenRouter"},
            {"command": "schedule", "description": "Отложенные задачи: создать / список / удалить"},
        ]

    def run_forever(self) -> None:
        while True:
            try:
                me = self.telegram.get_me()
                break
            except TelegramApiUnavailableError as exc:
                print(f"[telegram] bootstrap error: {exc}; retrying in 3s")
                time.sleep(3)
        bot_username = _ensure_text(me.get("username")) or "<unknown>"
        bot_id = _ensure_text(me.get("id")) or "-"
        try:
            self.telegram.set_my_commands(self._bot_commands())
        except Exception as exc:
            print(f"[telegram] setMyCommands skipped: {exc}")
        print(
            "[telegram] bridge ready:"
            f" bot=@{bot_username} id={bot_id}"
            f" profile={self.profile}"
            f" auto_approve={'on' if self.auto_approve else 'off'}:{self.auto_approve_scope}"
            f" allowed_users={len(self.allowed_user_ids)}"
            f" workdir={self.workdir}"
        )
        consecutive_poll_errors = 0
        while True:
            try:
                updates = self.telegram.get_updates(offset=self.offset, timeout=self.poll_timeout_s)
                if consecutive_poll_errors:
                    print(
                        f"[telegram] polling recovered after {consecutive_poll_errors} error(s)"
                    )
                    consecutive_poll_errors = 0
                for update in updates:
                    if not isinstance(update, dict):
                        continue
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self.offset = update_id + 1
                    self._handle_update(update)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                consecutive_poll_errors += 1
                delay = _next_poll_backoff(consecutive_poll_errors)
                if consecutive_poll_errors % _POLL_WARN_EVERY == 0:
                    print(
                        f"[telegram] poll error #{consecutive_poll_errors}: {exc}; "
                        f"retrying in {delay:.0f}s"
                    )
                else:
                    print(f"[telegram] poll error: {exc}")
                time.sleep(delay)

    def _load_sessions(self) -> dict[str, ChatRuntimeState]:
        try:
            if not self._sessions_path.exists():
                return {}
            payload = json.loads(self._sessions_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        result: dict[str, ChatRuntimeState] = {}
        for chat_id, raw in payload.items():
            if not isinstance(raw, dict):
                continue
            chat_key = _ensure_text(chat_id)
            if not chat_key:
                continue
            raw_delivered = raw.get("delivered_files")
            delivered_files: dict[str, str] = {}
            if isinstance(raw_delivered, dict):
                for delivered_path, signature in raw_delivered.items():
                    normalized = _ensure_text(delivered_path)
                    if normalized:
                        delivered_files[normalized] = _ensure_text(signature)
            elif isinstance(raw_delivered, list):
                # Legacy format: plain path list. Mark signature as unknown ("")
                # so a changed file is re-delivered once, then tracked normally.
                for item in raw_delivered:
                    normalized = _ensure_text(item)
                    if normalized:
                        delivered_files[normalized] = ""
            result[chat_key] = ChatRuntimeState(
                chat_id=chat_key,
                user_id=_ensure_text(raw.get("user_id")),
                username=_ensure_text(raw.get("username")),
                chat_session=_ensure_text(raw.get("chat_session")) or f"telegram-chat-{chat_key}",
                profile_override=_ensure_text(raw.get("profile_override")) or None,
                provider_override=_ensure_text(raw.get("provider_override")) or None,
                model_override=_ensure_text(raw.get("model_override")) or None,
                thread_id=_ensure_text(raw.get("thread_id")) or None,
                runtime_session_id=_ensure_text(raw.get("runtime_session_id")) or None,
                event_after_seq=int(raw.get("event_after_seq") or 0),
                workspace_task_id=_ensure_text(raw.get("workspace_task_id")) or None,
                pending_task_id=_ensure_text(raw.get("pending_task_id")) or None,
                delivered_files=delivered_files,
                smarthelp_active=bool(raw.get("smarthelp_active")),
            )
        return result

    def _save_sessions(self) -> None:
        self._sessions_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {chat_id: session.to_json() for chat_id, session in self._known_sessions.items()}
        self._sessions_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _handle_update(self, update: dict[str, Any]) -> None:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            self._handle_callback_query(callback)
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        chat_id = _ensure_text(chat.get("id"))
        user_id = _ensure_text(sender.get("id"))
        username = _ensure_text(sender.get("username") or sender.get("first_name") or sender.get("last_name"))
        text = _ensure_text(message.get("text"))
        caption = _ensure_text(message.get("caption"))
        if not chat_id or not user_id:
            return
        if not self._is_allowed(user_id=user_id, chat_id=chat_id):
            self._safe_send(chat_id, "Доступ запрещён для этого Telegram user_id.")
            return
        attachments = self._extract_telegram_attachments(chat_id=chat_id, message=message)
        effective_text = text or caption
        if not effective_text and not attachments:
            return
        self._update_executor.submit(
            self._process_message,
            **{
                "chat_id": chat_id,
                "user_id": user_id,
                "username": username,
                "text": effective_text,
                "attachments": attachments,
                "reply_to_message_id": message.get("message_id"),
            },
        )

    def _is_allowed(self, *, user_id: str, chat_id: str) -> bool:
        if user_id not in self.allowed_user_ids:
            return False
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return False
        return True

    def _process_message(
        self,
        *,
        chat_id: str,
        user_id: str,
        username: str,
        text: str,
        attachments: list[dict[str, str]] | None = None,
        reply_to_message_id: int | None = None,
    ) -> None:
        session = self._get_or_create_state(chat_id=chat_id, user_id=user_id, username=username)
        # Lock is not held during the model/MCP execution; otherwise, /approve and /reset would wait for the required task.
        with session.lock:
            session.last_user_message_id = reply_to_message_id
        try:
            self._dispatch_message(session, text, attachments=attachments or [])
        except Exception as exc:
            self._safe_send(chat_id, f"Ошибка bridge: {exc}")

    def _handle_callback_query(self, callback: dict[str, Any]) -> None:
        sender = callback.get("from")
        message = callback.get("message")
        if not isinstance(sender, dict) or not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return
        chat_id = _ensure_text(chat.get("id"))
        user_id = _ensure_text(sender.get("id"))
        data = _ensure_text(callback.get("data"))
        callback_id = _ensure_text(callback.get("id"))
        if not chat_id or not user_id or not self._is_allowed(user_id=user_id, chat_id=chat_id):
            return
        if callback_id:
            try:
                self.telegram.answer_callback_query(callback_id)
            except Exception:
                pass
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            return
        task_id, action = parts[1], parts[2]
        session = self._get_or_create_state(
            chat_id=chat_id,
            user_id=user_id,
            username=_ensure_text(sender.get("username") or sender.get("first_name")),
        )
        try:
            self._resolve_queued_mcp_approval(session, task_id=task_id, action=action)
        except Exception as exc:
            self._safe_send(chat_id, f"Не удалось обработать approval: {exc}")

    def _get_or_create_state(self, *, chat_id: str, user_id: str, username: str) -> ChatRuntimeState:
        with self._sessions_lock:
            session = self._known_sessions.get(chat_id)
            if session is None:
                session = ChatRuntimeState(
                    chat_id=chat_id,
                    user_id=user_id,
                    username=username,
                    chat_session=f"telegram-chat-{chat_id}",
                )
                self._known_sessions[chat_id] = session
            else:
                session.user_id = user_id
                session.username = username
                if not session.chat_session:
                    session.chat_session = f"telegram-chat-{chat_id}"
            self._save_sessions()
            return session

    def _effective_profile_name(self, session: ChatRuntimeState) -> str:
        return _ensure_text(session.profile_override) or self.profile

    def _session_for_chat(self, chat_id: str) -> ChatRuntimeState | None:
        with self._sessions_lock:
            return self._known_sessions.get(chat_id)

    def _effective_profile_config(self, session: ChatRuntimeState) -> dict[str, Any]:
        return self.gateway_runtime.get_profile_config(self._effective_profile_name(session))

    def _effective_provider_name(self, session: ChatRuntimeState) -> str:
        config = self._effective_profile_config(session)
        raw_provider = _ensure_text(session.provider_override) or _ensure_text(config.get("provider")) or "openai-codex"
        return user_facing_codex_provider(raw_provider)

    def _effective_model_name(self, session: ChatRuntimeState) -> str:
        if _ensure_text(session.model_override):
            return _ensure_text(session.model_override)
        return profile_model_name(self._effective_profile_config(session))

    def _runtime_selection_summary(self, session: ChatRuntimeState) -> str:
        config = self._effective_profile_config(session)
        lines = [
            f"profile: {self._effective_profile_name(session)}",
            f"backend: {profile_backend_name(config)}",
            f"provider: {self._effective_provider_name(session)}",
            f"model: {self._effective_model_name(session)}",
            f"transport: {'queued-task' if self._use_task_queue_transport(session) else 'app-server'}",
            f"chat_session: {session.chat_session}",
            f"thread_id: {session.thread_id or '-'}",
            f"runtime_session_id: {session.runtime_session_id or '-'}",
            f"pending approvals: {len(session.pending_approvals)}",
        ]
        if self._effective_provider_name(session) == "openrouter":
            has_key = bool(
                os.environ.get(CODEX_OPENROUTER_API_KEY_ENV, "").strip()
                or os.environ.get(OPENROUTER_API_KEY_ENV, "").strip()
            )
            lines.append(f"openrouter_key: {'configured' if has_key else 'missing'} ({OPENROUTER_API_KEY_ENV})")
        return "\n".join(lines)

    def _gateway_app_state(self, session: ChatRuntimeState) -> GatewayAppServerState:
        return GatewayAppServerState(
            runtime_session_id=session.runtime_session_id,
            thread_id=session.thread_id,
            event_after_seq=session.event_after_seq,
            pending_approvals=list(session.pending_approvals),
            item_cache=dict(session.item_cache),
            recent_file_paths=list(session.recent_file_paths),
        )

    def _apply_gateway_app_state(self, session: ChatRuntimeState, state: GatewayAppServerState) -> None:
        session.runtime_session_id = state.runtime_session_id
        session.thread_id = state.thread_id
        session.event_after_seq = state.event_after_seq
        session.pending_approvals = list(state.pending_approvals)
        session.item_cache = dict(state.item_cache)
        session.recent_file_paths = list(state.recent_file_paths)
        with self._sessions_lock:
            self._save_sessions()

    def _gateway_source(self, session: ChatRuntimeState) -> GatewaySource:
        return GatewaySource(
            platform="telegram",
            chat_id=session.chat_id,
            user_id=session.user_id,
            username=session.username,
            chat_session=session.chat_session,
        )

    def _reset_runtime_session(self, session: ChatRuntimeState) -> None:
        state = self.gateway_runtime.close_app_server_session(self._gateway_app_state(session))
        self._apply_gateway_app_state(session, state)

    def _telegram_session_path(self, session: ChatRuntimeState) -> Path:
        session_name = _sanitize_session_name(session.chat_session or f"telegram-chat-{session.chat_id}")
        return _session_path(session_name)

    def _use_task_queue_transport(self, session: ChatRuntimeState) -> bool:
        config = self._effective_profile_config(session)
        return self.gateway_runtime.use_task_queue_transport(
            profile_config=config,
            provider_name=self._effective_provider_name(session),
        )

    def _set_profile_override(self, session: ChatRuntimeState, profile_name: str) -> str:
        normalized = _ensure_text(profile_name)
        if not normalized:
            raise ValueError("Используй /profile <name>.")
        config = get_profile_config(normalized)
        backend = profile_backend_name(config)
        if backend not in ("codex", "cc-backend"):
            raise ValueError(f"Для Telegram runtime поддерживаются только codex и cc-backend профили, got: {backend}")
        session.profile_override = normalized
        session.provider_override = None
        session.model_override = None
        self._reset_runtime_session(session)
        with self._sessions_lock:
            self._save_sessions()
        return normalized

    def _set_provider_override(self, session: ChatRuntimeState, provider_name: str) -> str:
        config = self._effective_profile_config(session)
        if profile_backend_name(config) != "codex":
            raise ValueError("Команда /provider доступна только для codex backend.")
        normalized = _ensure_text(provider_name).lower()
        if normalized not in {"openai", "openrouter"}:
            raise ValueError("Используй /provider openai или /provider openrouter.")
        session.provider_override = normalized
        provider_models = suggested_models_for_provider(normalized)
        session.model_override = provider_models[0] if normalized == "openrouter" and provider_models else None
        self._reset_runtime_session(session)
        with self._sessions_lock:
            self._save_sessions()
        return self._effective_provider_name(session)

    def _set_model_override(self, session: ChatRuntimeState, model_name: str) -> str:
        config = self._effective_profile_config(session)
        if profile_backend_name(config) != "codex":
            raise ValueError("Команда /model доступна только для codex backend.")
        normalized = _ensure_text(model_name)
        if not normalized:
            raise ValueError("Используй /model <name>.")
        session.model_override = normalized
        self._reset_runtime_session(session)
        with self._sessions_lock:
            self._save_sessions()
        return self._effective_model_name(session)

    def _models_list_text(self, session: ChatRuntimeState) -> str:
        provider = self._effective_provider_name(session)
        models = suggested_models_for_provider(provider)
        if not models:
            return f"Provider {provider}: список моделей не задан. Можно использовать /model <name> вручную."
        current = self._effective_model_name(session)
        lines = [f"Модели для provider {provider}:"]
        for item in models:
            marker = " (current)" if item == current else ""
            lines.append(f"- {item}{marker}")
        return "\n".join(lines)

    def _dispatch_message(self, session: ChatRuntimeState, text: str, *, attachments: list[dict[str, str]]) -> None:
        command, _, argument = text.partition(" ")
        command = command.strip().lower()
        if command.startswith("/") and "@" in command:
            command = command.split("@", 1)[0]
        argument = argument.strip()
        if command in {"/start", "/help"}:
            self._send_help(session.chat_id)
            return
        if command == "/reset":
            self._reset_session(session)
            self._safe_send(session.chat_id, "Сессия Telegram-чата сброшена. Следующее сообщение начнёт новый thread.")
            return
        if command == "/session":
            self._safe_send(session.chat_id, self._runtime_selection_summary(session))
            return
        if command == "/schedule":
            self._handle_schedule_command(session, argument)
            return
        if command == "/provider":
            if not argument:
                self._safe_send(session.chat_id, self._runtime_selection_summary(session))
                return
            try:
                provider_name = self._set_provider_override(session, argument)
            except Exception as exc:
                self._safe_send(session.chat_id, str(exc))
                return
            self._safe_send(session.chat_id, f"Provider переключён: {provider_name}\n\n{self._runtime_selection_summary(session)}")
            return
        if command == "/models":
            self._safe_send(session.chat_id, self._models_list_text(session))
            return
        if command == "/model":
            if not argument:
                self._safe_send(session.chat_id, self._runtime_selection_summary(session))
                return
            try:
                model_name = self._set_model_override(session, argument)
            except Exception as exc:
                self._safe_send(session.chat_id, str(exc))
                return
            self._safe_send(session.chat_id, f"Model переключена: {model_name}\n\n{self._runtime_selection_summary(session)}")
            return
        if command == "/profile":
            if not argument:
                self._safe_send(session.chat_id, self._runtime_selection_summary(session))
                return
            try:
                profile_name = self._set_profile_override(session, argument)
            except Exception as exc:
                self._safe_send(session.chat_id, str(exc))
                return
            self._safe_send(session.chat_id, f"Профиль переключён: {profile_name}\n\n{self._runtime_selection_summary(session)}")
            return
        if command == "/approvals":
            self._send_pending_approvals(session)
            return
        if command in {"/approve", "/approve_session", "/reject"}:
            self._handle_approval_command(session, command=command, argument=argument)
            return
        if command == "/plugins":
            if profile_backend_name(self._effective_profile_config(session)) != "codex":
                self._safe_send(session.chat_id, "Команда /plugins доступна только для codex-профиля.")
                return
            if self._use_task_queue_transport(session):
                self._safe_send(session.chat_id, "Команда /plugins в Telegram сейчас доступна только через app-server/OpenAI.")
                return
            self._forward_user_message(session, text, attachments=[])
            return
        if command == "/smarthelp":
            normalized = argument.strip().lower() if argument else ""
            if normalized in ("on", "start", "enable"):
                session.smarthelp_active = True
                self._save_sessions()
                self._safe_send(
                    session.chat_id,
                    "✅ SmartHelp Agent активирован. Все сообщения теперь будут обрабатываться через OpenRouter с инструментами.\n\n"
                    "Используйте /smarthelp off для возврата к локальной модели."
                )
                return
            if normalized in ("off", "exit", "stop", "disable"):
                session.smarthelp_active = False
                self._save_sessions()
                self._safe_send(
                    session.chat_id,
                    "✅ SmartHelp Agent деактивирован. Возвращаюсь к локальной модели."
                )
                return
            if not argument:
                status = "включён" if session.smarthelp_active else "выключен"
                self._safe_send(
                    session.chat_id,
                    f"SmartHelp Agent: {status}\n\n"
                    "Использование:\n"
                    "• /smarthelp <вопрос> — быстрый ответ от Qwen\n"
                    "• /smarthelp on — активировать агентный режим с инструментами\n"
                    "• /smarthelp off — деактивировать агентный режим"
                )
                return
            self.telegram.send_chat_action(session.chat_id, "typing")
            from .smarthelp import SmarthelpError
            try:
                if session.smarthelp_active:
                    self._send_smarthelp_agent_answer(session, argument)
                else:
                    from .smarthelp import smarthelp_query
                    answer, log_line = smarthelp_query(argument)
                    print(f"[telegram] {log_line}", flush=True)
                    self._safe_send(session.chat_id, answer)
            except SmarthelpError as exc:
                self._safe_send(session.chat_id, str(exc))
            except Exception as exc:
                self._safe_send(session.chat_id, f"Ошибка: {exc}")
            return
        if session.smarthelp_active:
            self.telegram.send_chat_action(session.chat_id, "typing")
            try:
                self._send_smarthelp_agent_answer(session, self._compose_runtime_message(text, attachments))
            except Exception as exc:
                self._safe_send(session.chat_id, f"Ошибка SmartHelp Agent: {exc}")
            return
        handled_memory, assistant_text, _memory_writes = self.memory_manager.handle_explicit_user_memory(
            text,
            data_dir=str(get_data_root()),
        )
        if handled_memory:
            self._safe_send(session.chat_id, assistant_text, reply_to=session.last_user_message_id)
            return
        self._forward_user_message(session, text, attachments=attachments)

    def _handle_schedule_command(self, session: ChatRuntimeState, argument: str) -> None:
        """Handle /schedule command: create, list, or remove deferred tasks."""
        from .scheduler import get_scheduler

        normalized = argument.strip().lower()

        # /schedule list — show all schedules
        if not normalized or normalized in ("list", "ls"):
            try:
                scheduler = get_scheduler()
                schedules = scheduler.list_schedules(enabled_only=False)
                if not schedules:
                    self._safe_send(session.chat_id, "Нет отложенных задач.")
                    return
                lines = ["Отложенные задачи:"]
                for i, s in enumerate(schedules, 1):
                    d = s.to_dict()
                    status = "включена" if d.get("enabled") else "выключена"
                    stype = d.get("schedule_type", "?")
                    tval = d.get("time_value", "?")
                    run_count = d.get("run_count", 0)
                    output_preview = ""
                    raw_output = d.get("last_output")
                    if raw_output:
                        output_preview = f"\n   последний результат: {raw_output[:100]}"
                    sid_short = str(d.get("schedule_id", ""))[:8]
                    lines.append(
                        f"{i}. [{sid_short}] {d.get('name', '?')} — {tval} ({stype}, {status}, ran={run_count}){output_preview}"
                    )
                lines.append("")
                lines.append("Удалить: /schedule remove <номер или ID>")
                self._safe_send(session.chat_id, "\n".join(lines))
            except Exception as exc:
                self._safe_send(session.chat_id, f"Ошибка: {exc}")
            return

        # /schedule remove N — remove schedule
        if normalized.startswith("remove ") or normalized.startswith("rm "):
            ref = argument.strip().split(None, 1)[1].strip() if " " in argument.strip() else ""
            if not ref:
                self._safe_send(session.chat_id, "Используй /schedule remove <номер или ID>")
                return
            try:
                scheduler = get_scheduler()
                # Try as index first
                try:
                    idx = int(ref)
                    schedules = scheduler.list_schedules(enabled_only=False)
                    if 1 <= idx <= len(schedules):
                        ref = schedules[idx - 1].schedule_id
                    else:
                        self._safe_send(session.chat_id, f"Номер {idx} вне диапазона (1-{len(schedules)}).")
                        return
                except ValueError:
                    pass  # Use as-is (schedule_id)
                success = scheduler.delete_schedule(ref)
                if success:
                    self._safe_send(session.chat_id, f"Задача удалена.")
                else:
                    self._safe_send(session.chat_id, "Задача не найдена.")
            except Exception as exc:
                self._safe_send(session.chat_id, f"Ошибка удаления: {exc}")
            return

        # /schedule HH:MM payload — create one-shot schedule
        parts = argument.strip().split(None, 1)
        if len(parts) < 2:
            self._safe_send(session.chat_id, "Используй /schedule HH:MM <текст задачи>\nПример: /schedule 14:30 Сделай обзор погоды")
            return

        time_str = parts[0].strip()
        payload = parts[1].strip()

        # Validate HH:MM
        try:
            t_parts = time_str.split(":")
            if len(t_parts) != 2:
                raise ValueError
            hour, minute = int(t_parts[0]), int(t_parts[1])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (ValueError, IndexError):
            self._safe_send(session.chat_id, f"Неверный формат времени '{time_str}'. Используй HH:MM, например 14:30")
            return

        profile = self._effective_profile_name(session)
        try:
            scheduler = get_scheduler()
            schedule_id = scheduler.create_schedule(
                name=payload[:50],
                agent_id=profile,
                payload=payload,
                schedule_type="once",
                time_value=time_str,
                tz="Europe/Moscow",
                enabled=True,
                metadata={
                    "created_via": "telegram_schedule_command",
                    "deliver": {
                        "platform": "telegram",
                        "chat_id": session.chat_id,
                    },
                },
            )
            self._safe_send(
                session.chat_id,
                f"Задача создана. Выполнится в {time_str} (московское время).\n"
                f"ID: {schedule_id[:8]}... Профиль: {profile}",
            )
        except Exception as exc:
            self._safe_send(session.chat_id, f"Ошибка создания задачи: {exc}")

    def _send_help(self, chat_id: str) -> None:
        self._safe_send(
            chat_id,
            "\n".join(
                [
                    "Команды:",
                    "/help - показать справку",
                    "/session - показать текущую runtime session",
                    "/provider [openai|openrouter] - показать или сменить provider",
                    "/models - показать список моделей для текущего provider",
                    "/model [name] - показать или сменить модель",
                    "/profile [name] - показать или сменить профиль",
                    "/plugins [args] - нативная команда Codex plugins",
                    "/approvals - показать pending approvals",
                    "/approve <n|all> - одобрить approval",
                    "/approve_session <n|all> - одобрить до конца app-server session",
                    "/reject <n|all> - отклонить approval",
                    "/reset - сбросить chat session и начать новый thread",
                    "/remember <факт> - записать факт в долговременную память",
                    "/smarthelp <вопрос> - быстрый ответ через OpenRouter",
                    "/schedule HH:MM <текст> - создать отложенную задачу",
                    "/schedule list - показать отложенные задачи",
                    "/schedule remove <N|ID> - удалить задачу",
                    "",
                    "Любой другой текст отправляется в Codex runtime.",
                ]
            ),
        )

    def _reset_session(self, session: ChatRuntimeState) -> None:
        self._reset_runtime_session(session)
        session.workspace_task_id = None
        archive_session_thread(self._telegram_session_path(session))
        with self._sessions_lock:
            self._save_sessions()

    def _send_pending_approvals(self, session: ChatRuntimeState) -> None:
        queued = self._queued_mcp_approvals(session)
        if queued:
            lines = ["Ожидающие MCP-разрешения:"]
            for index, item in enumerate(queued, start=1):
                lines.append(f"{index}. {item['server_name']} — {item['reason'] or 'без описания'}")
            lines.append("Используй кнопки в сообщении или /approve <номер>.")
            self._safe_send(session.chat_id, "\n".join(lines))
            return
        if not session.pending_approvals:
            self._safe_send(session.chat_id, "Pending approvals нет.")
            return
        lines = ["Pending approvals:"]
        for index, approval in enumerate(session.pending_approvals, start=1):
            target = f" -> {approval.target}" if approval.target else ""
            lines.append(f"{index}. {approval.summary}{target}")
        self._safe_send(session.chat_id, "\n".join(lines))

    def _handle_approval_command(self, session: ChatRuntimeState, *, command: str, argument: str) -> None:
        queued = self._queued_mcp_approvals(session)
        if queued:
            selected = queued if not argument or argument.strip().lower() == "all" else []
            if not selected:
                try:
                    selected = [queued[int(argument) - 1]]
                except (ValueError, IndexError):
                    selected = [item for item in queued if item["request_id"] == argument]
            if not selected:
                self._safe_send(session.chat_id, "Не удалось определить MCP approval. Используй /approvals.")
                return
            action = "reject" if command == "/reject" else ("session" if command == "/approve_session" else "once")
            for item in selected:
                self._resolve_queued_mcp_approval(session, task_id=item["task_id"], action=action)
            return
        if not session.pending_approvals:
            self._safe_send(session.chat_id, "Нет pending approvals.")
            return
        if not session.runtime_session_id:
            self._safe_send(session.chat_id, "Runtime session не активна.")
            return
        remember_for_session = command == "/approve_session"
        approved = command != "/reject"
        selected = self._select_approvals(session.pending_approvals, argument)
        if not selected:
            self._safe_send(session.chat_id, "Не удалось определить approval. Используй /approvals.")
            return
        state = self._gateway_app_state(session)
        for approval in selected:
            state = self.gateway_runtime.resolve_app_server_approval(
                state=state,
                request_id=approval.request_id,
                approved=approved,
                remember_for_session=remember_for_session,
            )
        self._apply_gateway_app_state(session, state)
        action_label = "одобрены" if approved else "отклонены"
        scope_label = " до конца session" if remember_for_session and approved else ""
        self._safe_send(session.chat_id, f"Approval {action_label}{scope_label}. Жду продолжения ответа от runtime.")
        outcome = self.gateway_runtime.continue_app_server_turn(
            state=state,
            turn_timeout_s=600,
            auto_approve=self.auto_approve,
            auto_approve_scope=self.auto_approve_scope,
        )
        self._apply_gateway_app_state(session, state)
        self._send_turn_outcome(session.chat_id, outcome)

    def _queued_mcp_approvals(self, session: ChatRuntimeState) -> list[dict[str, str]]:
        """Reads stable queued approval tasks without relying on app-server state."""
        result: list[dict[str, str]] = []
        # This is only a database read without holding a Telegram session lock.
        candidates = [item for item in (session.pending_task_id, session.workspace_task_id) if item]
        for task_id in candidates:
            if not task_id:
                continue
            for request in list_pending_user_input_requests(task_id=task_id):
                payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
                context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
                server_name = _ensure_text(context.get("server_name"))
                if server_name:
                    result.append({
                        "task_id": task_id,
                        "request_id": _ensure_text(request.get("request_id")),
                        "server_name": server_name,
                        "reason": _ensure_text(context.get("reason")),
                    })
        return result

    def _resolve_queued_mcp_approval(self, session: ChatRuntimeState, *, task_id: str, action: str) -> None:
        task = get_task(task_id)
        if not task or _ensure_text(task.get("status")) != "waiting_approval":
            self._safe_send(session.chat_id, "Этот запрос уже обработан или задача больше не ожидает approval.")
            return
        requests = list_pending_user_input_requests(task_id=task_id)
        if not requests:
            self._safe_send(session.chat_id, "Pending approval для задачи не найден.")
            return
        request = requests[-1]
        payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        server_name = _ensure_text(context.get("server_name"))
        if action == "cancel":
            resolve_user_input_request(_ensure_text(request.get("request_id")), response=None, cancelled=True)
            cancel_task(task_id)
            session.pending_task_id = None
            self._save_sessions()
            self._safe_send(session.chat_id, "Задача отменена, Telegram-сессия свободна.")
            return
        approved = action in {"once", "session"}
        resolve_user_input_request(
            _ensure_text(request.get("request_id")),
            response={"answers": {"mcp_enable_decision": {"selected_options": ["Включить"]}}} if approved else None,
            cancelled=not approved,
        )
        if not approved:
            cancel_task(task_id)
            session.pending_task_id = None
            self._save_sessions()
            self._safe_send(session.chat_id, "Доступ отклонён; задача отменена, сессия свободна.")
            return
        # Existing MCP approval includes capability and returns the task to the queue.
        from .mcp_config import accept_mcp_enable_suggestion

        accept_mcp_enable_suggestion(self._effective_profile_name(session), server_name)
        if not resume_waiting_approval_task(task_id):
            self._safe_send(session.chat_id, "Approval сохранён, но задачу не удалось вернуть в очередь.")
            return
        session.pending_task_id = None
        self._save_sessions()
        scope = "до конца сессии" if action == "session" else "для продолжения этой задачи"
        self._safe_send(session.chat_id, f"Доступ к {server_name} разрешён {scope}. Исходная задача продолжена.")
        self._update_executor.submit(self._watch_resumed_task, session, task_id)

    def _watch_resumed_task(self, session: ChatRuntimeState, task_id: str) -> None:
        """Sends task progress and final result after returning approval to the queue."""
        trace_path = Path(get_data_root()) / "workspace" / f"temp_execution_{task_id[:8]}" / "cc_backend_trace.jsonl"
        seen_lines = 0
        progress = TelegramToolProgress(self, session.chat_id)
        deadline = time.monotonic() + 1800
        final_states = {"ok", "fail", "dead_letter", "cancelled", "waiting_approval"}
        task: dict[str, Any] | None = None
        try:
            while time.monotonic() < deadline:
                try:
                    lines = trace_path.read_text(encoding="utf-8").splitlines() if trace_path.exists() else []
                    for line in lines[seen_lines:]:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event.get("kind") != "tool.request_batch":
                            continue
                        for tool in event.get("tools") if isinstance(event.get("tools"), list) else []:
                            if isinstance(tool, dict):
                                name = _ensure_text(tool.get("name"))
                                # Approval buttons are already displayed; raw tool calls are not rendered.
                                if name and name != "suggest_enable_mcp":
                                    progress.callback("tool.started", tool_name=name, args=tool.get("input") or {})
                    seen_lines = len(lines)
                except OSError:
                    pass
                task = get_task(task_id)
                if task and _ensure_text(task.get("status")) in final_states:
                    break
                time.sleep(1)
        finally:
            progress.finish()
        if task is None:
            self._safe_send(session.chat_id, "Не удалось получить состояние продолженной задачи.")
            return
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        user_text = _ensure_text(metadata.get("original_message_text")) or "Продолжение ранее отправленной задачи"
        self._send_task_outcome(session, user_text, task)

    def _select_approvals(self, approvals: list[PendingApproval], argument: str) -> list[PendingApproval]:
        normalized = argument.strip().lower()
        if not normalized or normalized == "all":
            return list(approvals)
        try:
            index = int(normalized)
        except ValueError:
            return [item for item in approvals if item.request_id == normalized]
        if 1 <= index <= len(approvals):
            return [approvals[index - 1]]
        return []

    def _forward_user_message(self, session: ChatRuntimeState, text: str, *, attachments: list[dict[str, str]]) -> None:
        self.telegram.send_chat_action(session.chat_id, "typing")
        if self._use_task_queue_transport(session):
            self._forward_user_message_via_task_queue(session, text, attachments=attachments)
            return
        state, outcome = self.gateway_runtime.handle_app_server_message(
            state=self._gateway_app_state(session),
            source=self._gateway_source(session),
            message=GatewayMessage(text=self._compose_runtime_message(text, attachments), attachments=attachments),
            profile=self._effective_profile_name(session),
            provider_override=session.provider_override,
            model_override=session.model_override,
            turn_timeout_s=1800,
            auto_approve=self.auto_approve,
            auto_approve_scope=self.auto_approve_scope,
        )
        self._apply_gateway_app_state(session, state)
        self._send_turn_outcome(session.chat_id, outcome)

    def _forward_user_message_via_task_queue(
        self,
        session: ChatRuntimeState,
        text: str,
        *,
        attachments: list[dict[str, str]],
    ) -> None:
        session_path = self._telegram_session_path(session)
        composed_text = self._compose_runtime_message(text, attachments)
        entries = _load_entries(session_path)
        if not session.workspace_task_id:
            for entry in reversed(entries):
                if entry.get("role") != "assistant":
                    continue
                candidate = _ensure_text(entry.get("workspace_task_id") or entry.get("task_id"))
                if candidate:
                    session.workspace_task_id = candidate
                    self._save_sessions()
                    break
        context = _history_tail(entries, max_messages=self._TASK_MEMORY_MESSAGES, max_chars=self._TASK_MEMORY_CHARS)
        workspace_context = _build_workspace_context_entry(
            entries=entries,
            workspace_task_id=session.workspace_task_id,
            base_dir=Path(self.workdir).resolve(),
        )
        if workspace_context:
            composed_text = composed_text.rstrip() + "\n\n" + workspace_context["text"]
        progress = TelegramToolProgress(self, session.chat_id)
        typing_stop = self._start_typing_keepalive(session.chat_id)
        try:
            result = self.gateway_runtime.handle_message(
                source=GatewaySource(
                    platform="telegram",
                    chat_id=session.chat_id,
                    user_id=session.user_id,
                    username=session.username,
                    chat_session=session.chat_session,
                ),
                message=GatewayMessage(text=composed_text, attachments=attachments),
                profile=self._effective_profile_name(session),
                history_context=context,
                provider_override=session.provider_override,
                model_override=session.model_override,
                workspace_task_id=session.workspace_task_id,
                timeout_s=1800,
                progress_callback=progress.callback,
                auto_approve=self.auto_approve,
            )
        finally:
            typing_stop()
            progress.finish()
        if result.task_id and not session.workspace_task_id:
            session.workspace_task_id = result.task_id
            self._save_sessions()
        if _ensure_text((result.task or {}).get("status")) == "waiting_approval":
            session.pending_task_id = result.task_id
            self._save_sessions()
        outcome = result.task or {
            "task_id": result.task_id,
            "status": result.status or "fail",
            "error_message": "GatewayRuntime не вернул результат task.",
        }
        self._send_task_outcome(session, text, outcome)

    def _start_typing_keepalive(self, chat_id: str, interval_s: float = 4.0):
        """Keep the Telegram 'typing' indicator alive for a long-running turn.

        Returns a stop() callable to be invoked in a finally block.
        """
        stop = threading.Event()

        def _loop() -> None:
            while not stop.wait(interval_s):
                try:
                    self.telegram.send_chat_action(chat_id, "typing")
                except Exception:
                    pass

        threading.Thread(target=_loop, daemon=True, name="telegram-typing").start()
        return stop.set

    def _compose_runtime_message(self, text: str, attachments: list[dict[str, str]]) -> str:
        normalized_text = text.strip()
        if not attachments:
            return normalized_text
        lines: list[str] = []
        if normalized_text:
            lines.append(normalized_text)
            lines.append("")
        lines.append("Telegram attachments:")
        for index, item in enumerate(attachments, start=1):
            file_path = _ensure_text(item.get("path"))
            kind = _ensure_text(item.get("kind")) or "file"
            file_name = _ensure_text(item.get("name")) or Path(file_path).name
            lines.append(f"{index}. {kind}: {file_name}")
            if kind == "image":
                lines.append("   изображение уже прикреплено напрямую как визуальный input для модели")
            lines.append(f"   path: {file_path}")
        lines.append("")
        lines.append(
            "Для изображений отвечай по прикреплённому визуальному input и не используй Read по пути картинки. "
            "Для не-image файлов при необходимости используй локальный путь."
        )
        return "\n".join(lines).strip()

    def _extract_telegram_attachments(self, *, chat_id: str, message: dict[str, Any]) -> list[dict[str, str]]:
        attachments: list[dict[str, str]] = []
        photo_items = message.get("photo")
        if isinstance(photo_items, list) and photo_items:
            best = photo_items[-1]
            if isinstance(best, dict):
                saved = self._download_telegram_file(
                    chat_id=chat_id,
                    file_id=_ensure_text(best.get("file_id")),
                    suggested_name=_ensure_text(message.get("caption")) or "telegram_photo.jpg",
                    default_extension=".jpg",
                    kind="image",
                )
                if saved is not None:
                    attachments.append(saved)
        if _is_image_document(message):
            document = message.get("document")
            if isinstance(document, dict):
                saved = self._download_telegram_file(
                    chat_id=chat_id,
                    file_id=_ensure_text(document.get("file_id")),
                    suggested_name=_ensure_text(document.get("file_name")) or "telegram_image",
                    default_extension=".bin",
                    kind="image",
                )
                if saved is not None:
                    attachments.append(saved)
        return attachments

    def _download_telegram_file(
        self,
        *,
        chat_id: str,
        file_id: str,
        suggested_name: str,
        default_extension: str,
        kind: str,
    ) -> dict[str, str] | None:
        if not file_id:
            return None
        try:
            info = self.telegram.get_file(file_id)
            file_path = _ensure_text(info.get("file_path"))
            if not file_path:
                return None
            payload = self.telegram.download_file_bytes(file_path)
        except Exception as exc:
            print(f"[telegram] media download failed ({kind}): {exc}")
            try:
                self._safe_send(chat_id, f"⚠️ Не удалось получить вложение: {exc}")
            except Exception:
                pass
            return None
        source_name = Path(file_path).name or suggested_name
        suffix = Path(source_name).suffix or default_extension
        stem = Path(source_name).stem or Path(suggested_name).stem or f"telegram_{kind}"
        safe_name = _sanitize_filename(f"{stem}{suffix}", fallback=f"telegram_{kind}{suffix}")
        target_dir = self._uploads_dir / chat_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{int(time.time())}_{safe_name}"
        target_path.write_bytes(payload)
        return {
            "kind": kind,
            "name": safe_name,
            "path": str(target_path.resolve()),
        }

    def _send_turn_outcome(self, chat_id: str, outcome: dict[str, Any]) -> None:
        session = self._session_for_chat(chat_id)
        messages = outcome.get("messages") if isinstance(outcome.get("messages"), list) else []
        files = outcome.get("files") if isinstance(outcome.get("files"), list) else []
        status = _ensure_text(outcome.get("status"))
        cleaned_messages: list[str] = []
        persisted_writes: list[dict[str, Any]] = []
        for message in messages:
            cleaned, writes = extract_memory_write_blocks(_ensure_text(message))
            cleaned = strip_memory_status_lines(cleaned)
            persisted_writes.extend(
                self.memory_manager.review_turn(
                    user_text="",
                    assistant_text=cleaned,
                    profile=self._effective_profile_name(session) if session else self.profile,
                    data_dir=str(get_data_root()),
                    legacy_raw_text=_ensure_text(message),
                )
            )
            if cleaned:
                cleaned_messages.append(cleaned)
        messages = cleaned_messages
        base_dir = Path(self.workdir).resolve()
        roots = _output_file_roots(base_dir, (session.workspace_task_id or "") if session else "")
        file_paths = _collect_output_file_paths(base_dir, messages, files, allowed_roots=roots)
        if messages:
            self._safe_send(chat_id, "\n\n".join(_ensure_text(item) for item in messages if _ensure_text(item)))
        memory_message = self.memory_manager.format_memory_update_message(persisted_writes)
        if memory_message:
            self._safe_send(chat_id, memory_message)
        if file_paths:
            self._send_output_files(
                chat_id, file_paths, allowed_roots=roots,
                delivered_files=session.delivered_files if session else None,
            )
        if status == "waiting_on_approval":
            approvals = outcome.get("approvals") if isinstance(outcome.get("approvals"), list) else []
            lines = ["Нужно approval для продолжения:"]
            for index, approval in enumerate(approvals, start=1):
                if isinstance(approval, PendingApproval):
                    target = f" -> {approval.target}" if approval.target else ""
                    lines.append(f"{index}. {approval.summary}{target}")
            lines.extend(
                [
                    "",
                    "Команды:",
                    "/approve <n|all>",
                    "/approve_session <n|all>",
                    "/reject <n|all>",
                ]
            )
            self._safe_send(chat_id, "\n".join(lines))
            return
        if status == "timeout":
            self._safe_send(chat_id, "Runtime не завершил ответ вовремя. Можно подождать, проверить /approvals или отправить следующее сообщение позже.")
            return
        if not messages and not file_paths:
            self._safe_send(chat_id, "Ответ получен, но без текстового вывода.")

    def _send_task_outcome(self, session: ChatRuntimeState, user_text: str, task: dict[str, Any]) -> None:
        session_path = self._telegram_session_path(session)
        status = _ensure_text(task.get("status"))
        raw_assistant_text = _ensure_text(task.get("result_text") or task.get("output_text") or task.get("error_message"))
        raw_assistant_text = _strip_persisted_output_leak(raw_assistant_text)
        _append_entry(session_path, role="user", text=user_text)

        if status == "waiting_approval":
            blocked = task.get("approval_blocked_tools")
            approval = blocked[0] if isinstance(blocked, list) and blocked and isinstance(blocked[0], dict) else {}
            server_name = _ensure_text(approval.get("server_name")) or "MCP"
            reason = _ensure_text(approval.get("reason"))
            task_id = _ensure_text(task.get("task_id"))
            text = f"Игорёк запрашивает доступ к {server_name}"
            if reason:
                text += f" для: {reason}"
            text += "."
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "Разрешить один раз", "callback_data": f"mcp:{task_id}:once"},
                        {"text": "Разрешить до конца сессии", "callback_data": f"mcp:{task_id}:session"},
                    ],
                    [
                        {"text": "Отклонить", "callback_data": f"mcp:{task_id}:reject"},
                        {"text": "Отменить задачу", "callback_data": f"mcp:{task_id}:cancel"},
                    ],
                ]
            }
            self._safe_send(session.chat_id, text, reply_to=session.last_user_message_id, reply_markup=keyboard)
            return
        if status != "ok":
            error_text = raw_assistant_text or "Задача завершилась с ошибкой."
            self._safe_send(session.chat_id, error_text, reply_to=session.last_user_message_id)
            return

        assistant_text, _requested_writes = extract_memory_write_blocks(raw_assistant_text)
        assistant_text = strip_memory_status_lines(assistant_text)
        persisted_writes = self.memory_manager.review_turn(
            user_text=user_text,
            assistant_text=assistant_text,
            profile=self._effective_profile_name(session),
            data_dir=str(get_data_root()),
            legacy_raw_text=raw_assistant_text,
            log_fn=lambda msg: print(f"[telegram] {msg}"),
        )
        visible_writes = [item for item in persisted_writes if not item.get("rejected")]
        _append_entry(
            session_path,
            role="assistant",
            text=assistant_text,
            task_id=_ensure_text(task.get("task_id")) or None,
            status=status or None,
            extra={
                "workspace_task_id": session.workspace_task_id,
                "workspace_files": list(task.get("workspace_files") or []),
                "memory_writes": persisted_writes,
            },
        )

        if assistant_text:
            self._safe_send(session.chat_id, assistant_text, reply_to=session.last_user_message_id)

        memory_message = self.memory_manager.format_memory_update_message(visible_writes)
        if memory_message:
            self._safe_send(session.chat_id, memory_message)

        base_dir = Path(self.workdir).resolve()
        task_id = _ensure_text(task.get("task_id"))
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        workspace_task_id = _ensure_text(metadata.get("workspace_task_id")) or task_id
        roots = _output_file_roots(base_dir, workspace_task_id)
        workspace_files = task.get("workspace_files")
        explicit_files = []
        if isinstance(workspace_files, list) and re.fullmatch(r"[A-Za-z0-9_-]+", workspace_task_id):
            explicit_files = resolve_workspace_file_paths(workspace_task_id, [str(item) for item in workspace_files])
        file_paths = _collect_output_file_paths(base_dir, [assistant_text], explicit_files, allowed_roots=roots)
        if file_paths:
            self._send_output_files(
                session.chat_id, file_paths, delivered_files=session.delivered_files, allowed_roots=roots,
            )

    def _safe_send(
        self,
        chat_id: str,
        text: str,
        reply_to: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            return self.telegram.send_message(
                chat_id, text, reply_to_message_id=reply_to, reply_markup=reply_markup
            )
        except Exception as exc:
            print(f"[telegram] send error for chat {chat_id}: {exc}")
            return None

    def _send_smarthelp_agent_answer(self, session: ChatRuntimeState, question: str) -> None:
        from .smarthelp import smarthelp_agent_query

        progress = TelegramToolProgress(self, session.chat_id)
        answer, log_line, _tool_events = smarthelp_agent_query(
            question,
            workdir=self._resolve_workdir_for_session(session),
            progress_callback=progress.callback,
        )
        progress.finish()
        print(f"[telegram] {log_line}", flush=True)
        self._safe_send(session.chat_id, answer, reply_to=session.last_user_message_id)

    def _resolve_workdir_for_session(self, session: ChatRuntimeState) -> str:
        """Resolve workdir for a session, falling back to bridge default."""
        if session.workspace_task_id:
            from .runtime import get_workspace_root
            workspace_root = get_workspace_root()
            task_workdir = str(Path(workspace_root) / f"temp_execution_{session.workspace_task_id}")
            if Path(task_workdir).exists():
                return task_workdir
        return self.workdir

    def _send_output_files(
        self, chat_id: str, files: list[str], *, delivered_files: dict[str, str] | None = None,
        allowed_roots: tuple[Path, ...] | None = None,
    ) -> None:
        base_dir = Path(self.workdir).resolve()
        delivered: set[str] = set()
        missing: list[str] = []
        for raw_path in files:
            resolved, reason = _check_output_file_path(base_dir, _ensure_text(raw_path), allowed_roots=allowed_roots)
            if resolved is None:
                print(f"[telegram] file rejected reason={reason} path={raw_path!r}")
                description = {
                    "outside_allowed_roots": "путь вне разрешённых папок",
                    "missing": "файл не найден",
                    "not_file": "это не файл",
                    "invalid_path": "некорректный или недоступный путь",
                }[reason]
                missing.append(f"{raw_path} ({description})")
                continue
            if not _is_deliverable_output_file(resolved):
                continue
            resolved_str = str(resolved)
            signature = _output_file_signature(resolved)
            if resolved_str in delivered:
                continue
            if delivered_files is not None and signature and delivered_files.get(resolved_str) == signature:
                # Freshness check: same content already delivered this session.
                # A changed file (new mtime/size) is NOT a duplicate and goes out.
                continue
            try:
                self.telegram.send_document(chat_id, resolved)
                delivered.add(resolved_str)
                if delivered_files is not None:
                    delivered_files[resolved_str] = signature
            except Exception as exc:
                print(f"[telegram] sendDocument error reason=telegram_error for chat {chat_id}: {exc}")
                missing.append(resolved_str)
        if missing:
            self._safe_send(chat_id, "Не удалось приложить файлы:\n" + "\n".join(f"- {item}" for item in missing))


def _parse_csv_ids(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def run_telegram_bridge(
    *,
    token: str,
    profile: str,
    runtime_api_base_url: str,
    runtime_api_key: str,
    allowed_user_ids: set[str],
    allowed_chat_ids: set[str] | None = None,
    workdir: str | None = None,
    poll_timeout_s: int = 30,
    auto_approve: bool = False,
    auto_approve_scope: str = "session",
) -> int:
    if not token:
        raise ValueError("Telegram bot token is required")
    bridge = TelegramBridge(
        token=token,
        profile=profile,
        runtime_api_base_url=runtime_api_base_url,
        runtime_api_key=runtime_api_key,
        allowed_user_ids=allowed_user_ids,
        allowed_chat_ids=allowed_chat_ids,
        workdir=workdir,
        poll_timeout_s=poll_timeout_s,
        auto_approve=auto_approve,
        auto_approve_scope=auto_approve_scope,
    )
    bridge.run_forever()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram bridge for Igorek Agent Factory")
    parser.add_argument("--token", default=os.environ.get("FACTORY_TELEGRAM_BOT_TOKEN", ""), help="Telegram bot token")
    parser.add_argument("--profile", default=os.environ.get("FACTORY_TELEGRAM_PROFILE", "codex"), help="Factory profile for Telegram chats")
    parser.add_argument(
        "--runtime-api-base-url",
        default=os.environ.get("FACTORY_API_BASE_URL", "http://127.0.0.1:8181"),
        help="Runtime API base URL",
    )
    parser.add_argument(
        "--runtime-api-key",
        default=os.environ.get("FACTORY_API_KEY", ""),
        help="Runtime API key for mutating app-server endpoints",
    )
    parser.add_argument(
        "--allowed-user-ids",
        default=os.environ.get("FACTORY_TELEGRAM_ALLOWED_USER_IDS", ""),
        help="Comma-separated Telegram user_id whitelist",
    )
    parser.add_argument(
        "--allowed-chat-ids",
        default=os.environ.get("FACTORY_TELEGRAM_ALLOWED_CHAT_IDS", ""),
        help="Optional comma-separated chat_id whitelist",
    )
    parser.add_argument(
        "--workdir",
        default=os.environ.get("FACTORY_TELEGRAM_WORKDIR", ""),
        help="Working directory for Codex app-server session",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=int(os.environ.get("FACTORY_TELEGRAM_POLL_TIMEOUT_S", "30")),
        help="Telegram getUpdates long-poll timeout in seconds",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        default=os.environ.get("FACTORY_TELEGRAM_AUTO_APPROVE", "").strip().lower() in {"1", "true", "yes", "on"},
        help="Automatically approve app-server requests for Telegram chats",
    )
    parser.add_argument(
        "--auto-approve-scope",
        default=os.environ.get("FACTORY_TELEGRAM_AUTO_APPROVE_SCOPE", "session"),
        help="Auto-approve scope: turn or session",
    )
    args = parser.parse_args()
    run_telegram_bridge(
        token=args.token,
        profile=args.profile,
        runtime_api_base_url=args.runtime_api_base_url,
        runtime_api_key=args.runtime_api_key,
        allowed_user_ids=_parse_csv_ids(args.allowed_user_ids),
        allowed_chat_ids=_parse_csv_ids(args.allowed_chat_ids),
        workdir=args.workdir or None,
        poll_timeout_s=args.poll_timeout,
        auto_approve=args.auto_approve,
        auto_approve_scope=args.auto_approve_scope,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
