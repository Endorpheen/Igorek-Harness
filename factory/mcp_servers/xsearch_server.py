#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


SERVER_NAME = "Xsearch"
SERVER_VERSION = "0.1.0"
X_SEARCH_API_URL = "https://api.twitter.com/2/tweets/search/recent"


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


def _build_proxy_handler() -> urllib_request.BaseHandler:
    return urllib_request.ProxyHandler({})


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
            "name": "xsearch_query",
            "description": (
                "Ищет свежие публичные посты в X (Twitter) через Recent Search API. "
                "Используй для получения последних твитов по ключевому слову или теме."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос X API, например: bitcoin lang:en -is:retweet",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Сколько постов вернуть, от 10 до 100. По умолчанию 10.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }
    ]


def _search_x(query: str, max_results: int) -> dict[str, Any]:
    bearer_token = (
        os.getenv("XSEARCH_BEARER_TOKEN") or os.getenv("TWITTER_BEARER_TOKEN") or ""
    ).strip()
    if not bearer_token:
        raise RuntimeError("XSEARCH_BEARER_TOKEN не задан в окружении или .env")

    params = {
        "query": query,
        "max_results": max_results,
        "tweet.fields": "created_at,author_id,lang,public_metrics",
        "expansions": "author_id",
        "user.fields": "username,name",
    }
    query_string = "&".join(
        f"{urllib_request.quote(k)}={urllib_request.quote(str(v))}"
        for k, v in params.items()
    )
    url = f"{X_SEARCH_API_URL}?{query_string}"

    req = urllib_request.Request(
        url,
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        },
        method="GET",
    )
    opener = urllib_request.build_opener(_build_proxy_handler())
    try:
        with opener.open(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib_error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"X API HTTP {exc.code}: {details}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"X API unavailable: {exc.reason}") from exc

    if not raw or not raw.strip():
        raise RuntimeError("X API вернул пустой ответ")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"X API вернул не-JSON ответ: {raw[:500]}") from exc


def _format_search_response(
    payload: dict[str, Any], query: str, max_results: int
) -> str:
    posts = payload.get("data") or []
    includes = payload.get("includes") or {}
    users = includes.get("users") or []
    users_by_id = {str(item.get("id")): item for item in users if item.get("id")}

    if not posts:
        meta = payload.get("meta") or {}
        count = meta.get("result_count", 0)
        return f"По запросу `{query}` свежих постов не найдено. result_count={count}."

    lines = [
        (
            "Ниже реальные свежие посты из X API. "
            "Не делай общую сводку. "
            "В ответе пользователю перечисли посты по отдельности. "
            "Для каждого поста покажи: автора, полный URL, оригинальный текст и перевод на русский. "
            "URL_COPY_CONTRACT: для каждого POST обязательно скопируй строку `URL:` полностью и дословно. "
            "Не заменяй URL на `[ссылка]`, `ссылка`, `link`, `тут`, markdown без href или любой сокращённый текст. "
            "Если URL unavailable, так и напиши `URL: unavailable`."
        ),
        f"Запрос X API: `{query}`",
        f"Найдено постов в ответе: {len(posts[:max_results])}",
    ]
    for idx, post in enumerate(posts[:max_results], 1):
        post_id = str(post.get("id") or "").strip()
        author_id = str(post.get("author_id") or "").strip()
        user = users_by_id.get(author_id, {})
        username = str(user.get("username") or "").strip()
        display_name = str(user.get("name") or "").strip()
        created_at = str(post.get("created_at") or "").strip()
        text = " ".join(str(post.get("text") or "").split())
        text = text[:1200]
        url = (
            f"https://x.com/{username}/status/{post_id}" if username and post_id else ""
        )
        author_label = f"@{username}" if username else author_id or "unknown"
        name_line = f"Имя: {display_name}" if display_name else "Имя: unknown"
        url_line = f"URL: {url}" if url else "URL: unavailable"
        lines.append(
            "\n".join(
                [
                    f"POST {idx}",
                    f"Автор: {author_label}",
                    name_line,
                    f"Время: {created_at}",
                    url_line,
                    "ORIGINAL:",
                    text,
                ]
            )
        )
    return "\n\n".join(lines)


def _handle_tool_call(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    if not query:
        raise RuntimeError("Поле query обязательно")

    try:
        max_results = int(arguments.get("max_results", 10))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("max_results должен быть целым числом") from exc
    max_results = max(10, min(100, max_results))

    response = _search_x(query=query, max_results=max_results)
    text = _format_search_response(response, query=query, max_results=max_results)

    return {
        "content": [
            {
                "type": "text",
                "text": text,
            }
        ]
    }


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
                if tool_name != "xsearch_query":
                    _send(
                        _make_error(
                            request_id, -32602, f"Неизвестный инструмент: {tool_name}"
                        )
                    )
                    continue
                _send(_make_result(request_id, _handle_tool_call(arguments)))
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
