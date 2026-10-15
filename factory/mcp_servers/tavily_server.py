#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


SERVER_NAME = "Tavily"
SERVER_VERSION = "0.1.0"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


def _load_dotenv() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    for env_path in (
        base_dir / ".env.gateway",
        base_dir / ".env",
        base_dir / ".env.local",
    ):
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _make_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _make_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "tavily_search",
            "description": (
                "Веб-поиск через Tavily API. Возвращает релевантные результаты с текстом и ссылками. "
                "Используй для поиска актуальной информации в интернете."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Максимум результатов (1-10). По умолчанию 5.",
                    },
                    "search_depth": {
                        "type": "string",
                        "description": "basic или advanced. По умолчанию basic.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "tavily_extract",
            "description": (
                "Извлекает текстовое содержимое с указанных URL через Tavily. "
                "Полезно для чтения веб-страниц без полного рендеринга."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Список URL для извлечения (максимум 10).",
                    },
                },
                "required": ["urls"],
                "additionalProperties": False,
            },
        },
    ]


def _resolve_api_key() -> str:
    key = (
        os.getenv("TAVILY_API_KEY")
        or os.getenv("FACTORY_TAVILY_API_KEY")
        or ""
    ).strip()
    if not key:
        raise RuntimeError("TAVILY_API_KEY не задан в окружении или .env")
    return key


def _tavily_search(query: str, max_results: int, search_depth: str) -> dict[str, Any]:
    api_key = _resolve_api_key()
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max(1, min(10, max_results)),
        "search_depth": search_depth if search_depth in ("basic", "advanced") else "basic",
        "include_answer": True,
        "include_raw_content": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        TAVILY_SEARCH_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib_error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Tavily API HTTP {exc.code}: {details}") from exc


def _tavily_extract(urls: list[str]) -> dict[str, Any]:
    api_key = _resolve_api_key()
    payload = {
        "api_key": api_key,
        "urls": urls[:10],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        TAVILY_EXTRACT_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib_error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Tavily Extract API HTTP {exc.code}: {details}") from exc


def _format_search_response(payload: dict[str, Any], query: str) -> str:
    lines = [f"Результаты поиска Tavily по запросу: `{query}`"]

    answer = payload.get("answer")
    if answer:
        lines.append(f"\nКраткий ответ: {answer}")

    results = payload.get("results") or []
    if not results:
        return f"По запросу `{query}` ничего не найдено."

    for idx, r in enumerate(results, 1):
        title = str(r.get("title", "")).strip()
        url = str(r.get("url", "")).strip()
        content = str(r.get("content", "")).strip()[:1500]
        lines.append(f"\n--- Результат {idx} ---")
        if title:
            lines.append(f"Заголовок: {title}")
        if url:
            lines.append(f"URL: {url}")
        if content:
            lines.append(f"Текст: {content}")

    return "\n".join(lines)


def _format_extract_response(payload: dict[str, Any]) -> str:
    results = payload.get("results") or []
    if not results:
        return "Не удалось извлечь содержимое с указанных URL."

    lines = []
    for r in results:
        url = str(r.get("url", "")).strip()
        text = str(r.get("raw_content", "")).strip()[:5000]
        lines.append(f"\n=== {url} ===")
        if text:
            lines.append(text)
        else:
            lines.append("(пустой ответ)")

    failed = payload.get("failed_results") or []
    for r in failed:
        url = str(r.get("url", "")).strip()
        lines.append(f"\n=== {url} ===")
        lines.append(f"Ошибка: {r.get('error', 'unknown')}")

    return "\n".join(lines)


def _handle_tool_call(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "tavily_search":
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise RuntimeError("Поле query обязательно")
        max_results = int(arguments.get("max_results", 5))
        search_depth = str(arguments.get("search_depth", "basic")).strip()
        response = _tavily_search(query, max_results, search_depth)
        text = _format_search_response(response, query)
    elif tool_name == "tavily_extract":
        urls = arguments.get("urls")
        if not urls or not isinstance(urls, list):
            raise RuntimeError("Поле urls обязательно и должно быть списком")
        response = _tavily_extract(urls)
        text = _format_extract_response(response)
    else:
        raise RuntimeError(f"Неизвестный инструмент: {tool_name}")

    return {"content": [{"type": "text", "text": text}]}


def main() -> int:
    _load_dotenv()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            params = request.get("params") or {}

            if method == "initialize":
                _send(
                    _make_result(
                        request_id,
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {
                                "name": SERVER_NAME,
                                "version": SERVER_VERSION,
                            },
                        },
                    )
                )
                continue

            if method == "notifications/initialized":
                continue

            if method == "ping":
                _send(_make_result(request_id, {}))
                continue

            if method == "tools/list":
                _send(_make_result(request_id, {"tools": _tool_definitions()}))
                continue

            if method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments") or {}
                _send(_make_result(request_id, _handle_tool_call(tool_name, arguments)))
                continue

            _send(_make_error(request_id, -32601, f"Метод не поддерживается: {method}"))
        except Exception as exc:
            _send(
                _make_error(
                    request.get("id") if "request" in locals() else None,
                    -32000,
                    str(exc),
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
