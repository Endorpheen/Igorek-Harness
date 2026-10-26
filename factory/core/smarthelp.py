"""Smarthelp: Quick question via OpenRouter API or agentic tool loop."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


_SYSTEM_PROMPT = "Ты — полезный ассистент. Отвечай кратко и по делу, на русском языке."
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_OPENROUTER_MESSAGES_BASE_URL = "https://openrouter.ai/api"
_DEFAULT_MODEL = "qwen/qwen3.6-plus"

_TOOL_EMOJI_MAP = {
    "Read": "📖",
    "Write": "✏️",
    "Edit": "🖊️",
    "Bash": "🔧",
    "Grep": "🔍",
    "Glob": "📁",
    "web_search": "🌐",
    "web_extract": "📥",
    "delegate_task": "🤖",
    "skill_view": "📚",
}


class SmarthelpError(Exception):
    """Smarthelp-specific error with user-facing message."""


def _resolve_openrouter_key() -> str:
    from .codex_provider import OPENROUTER_API_KEY_ENV

    return (
        os.environ.get("OPENROUTER_API_KEY", "").strip()
        or os.environ.get(OPENROUTER_API_KEY_ENV, "").strip()
    )


def _resolve_model() -> str:
    return (
        os.environ.get("FACTORY_SMART_ROUTING_STRONG_MODEL", "").strip()
        or _DEFAULT_MODEL
    )


def _one_line(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def build_tool_preview(tool_name: str, args: dict[str, Any] | None) -> str:
    args = args if isinstance(args, dict) else {}
    if tool_name == "Bash":
        return _one_line(args.get("command"), 80)
    if tool_name in {"Read", "Write", "Edit"}:
        return _one_line(args.get("file_path") or args.get("path"), 80)
    if tool_name == "Grep":
        return _one_line(args.get("pattern") or args.get("query"), 60)
    if tool_name == "Glob":
        return _one_line(args.get("pattern") or args.get("path"), 80)
    if tool_name == "web_search":
        return _one_line(args.get("query"), 80)
    if tool_name == "web_extract":
        urls = args.get("urls")
        if isinstance(urls, list) and urls:
            return _one_line(urls[0], 80)
        return _one_line(args.get("url"), 80)
    if tool_name == "delegate_task":
        return _one_line(args.get("task") or args.get("goal") or args.get("prompt"), 80)
    if tool_name == "skill_view":
        return _one_line(args.get("skill") or args.get("name"), 80)
    try:
        return _one_line(json.dumps(args, ensure_ascii=False, sort_keys=True), 80)
    except Exception:
        return _one_line(args, 80)


def smarthelp_query(question: str) -> tuple[str, str]:
    """Send a question to OpenRouter, return (answer, log_line).

    Raises:
        SmarthelpError: Missing API key, empty response, HTTP error.
    """
    api_key = _resolve_openrouter_key()
    if not api_key:
        raise SmarthelpError(
            "OpenRouter API key не настроен (OPENROUTER_API_KEY / FACTORY_OPENROUTER_API_KEY)"
        )

    model = _resolve_model()

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "max_tokens": 2048,
        "temperature": 0.3,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        _OPENROUTER_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise SmarthelpError(f"OpenRouter HTTP {exc.code}: {err_body}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise SmarthelpError(f"Ошибка подключения к OpenRouter: {exc}") from exc

    latency_ms = int((time.time() - t0) * 1000)
    usage = body.get("usage") or {}
    log_line = (
        f"[smarthelp] OpenRouter → {model} | {latency_ms}ms | "
        f"in={usage.get('prompt_tokens', '?')} out={usage.get('completion_tokens', '?')}"
    )

    answer = ""
    choices = body.get("choices") or []
    if choices:
        answer = (choices[0].get("message") or {}).get("content", "")

    if not answer.strip():
        raise SmarthelpError("Пустой ответ от OpenRouter")

    return answer.strip(), log_line


def smarthelp_agent_query(
    question: str,
    *,
    workdir: str,
    progress_callback=None,
    max_tokens: int = 4000,
    timeout_s: int = 300,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Run /smarthelp through the cc-backend tool loop on OpenRouter /messages."""
    api_key = _resolve_openrouter_key()
    if not api_key:
        raise SmarthelpError(
            "OpenRouter API key не настроен (OPENROUTER_API_KEY / FACTORY_OPENROUTER_API_KEY)"
        )
    model = _resolve_model()

    from .cc_backend_executor import execute_cc_backend_task
    from .cc_backend_tools import get_tool_schemas, web_tools_available
    from .prompt_assembly import build_cc_backend_system_prompt

    resolved_workdir = str(Path(workdir or os.getcwd()).expanduser().resolve())
    tool_events: list[dict[str, Any]] = []

    def _progress(event_type: str, **kwargs: Any) -> None:
        tool_name = str(kwargs.get("name") or kwargs.get("tool_name") or "").strip()
        args = kwargs.get("args") if isinstance(kwargs.get("args"), dict) else {}
        preview = str(kwargs.get("preview") or build_tool_preview(tool_name, args)).strip()
        if tool_name:
            tool_events.append(
                {
                    "type": event_type,
                    "tool": tool_name,
                    "preview": preview,
                    "args": args,
                }
            )
        if progress_callback:
            progress_callback(
                event_type,
                tool_name=tool_name,
                preview=preview,
                args=args,
                **{k: v for k, v in kwargs.items() if k not in {"name", "tool_name", "preview", "args"}},
            )

    system_prompt = build_cc_backend_system_prompt(
        workdir=resolved_workdir,
        filesystem_mode="workspace",
        model_name=model,
        foundation_context="",
        skills_text="",
        web_tools_enabled=web_tools_available(),
        mcp_tool_names=None,
    )
    task_id = f"smarthelp_{uuid.uuid4().hex[:8]}"
    artifact_workdir = tempfile.mkdtemp(prefix=f"{task_id}_")

    started = time.time()
    result = execute_cc_backend_task(
        base_url=_OPENROUTER_MESSAGES_BASE_URL,
        model=model,
        system_prompt=system_prompt,
        user_message=question,
        input_files=[],
        workdir=resolved_workdir,
        artifact_workdir=artifact_workdir,
        filesystem_mode="workspace",
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        log_fn=lambda msg: print(f"[smarthelp-agent] {msg}", flush=True),
        enabled_tools=get_tool_schemas(),
        progress_callback=_progress,
        metadata={
            "source": "smarthelp",
            "created_via": "/smarthelp",
            "session": task_id,
        },
        request_headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/igorek-agent-factory",
            "X-Title": "Igorek SmartHelp",
        },
    )
    latency_ms = int((time.time() - started) * 1000)
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    log_line = (
        f"[smarthelp] OpenRouter agent → {model} | {latency_ms}ms | "
        f"in={usage.get('prompt', '?')} out={usage.get('completion', '?')} "
        f"tools={result.get('tool_calls_count', 0)}"
    )
    output_text = str(result.get("output_text") or "").strip()
    if result.get("status") != "ok":
        detail = str(result.get("failure_details") or output_text or "unknown error")
        raise SmarthelpError(f"SmartHelp Agent завершился с ошибкой: {detail}")
    if not output_text:
        raise SmarthelpError("Пустой ответ от SmartHelp Agent")
    return output_text, log_line, tool_events
