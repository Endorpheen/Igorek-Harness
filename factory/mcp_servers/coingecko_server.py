#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


SERVER_NAME = "CoinGecko"
SERVER_VERSION = "0.1.0"

COINGECKO_FREE_API = "https://api.coingecko.com/api/v3"


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


def _api_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    }


def _api_url(path: str) -> str:
    key = (os.getenv("COINGECKO_API_KEY") or "").strip()
    url = f"{COINGECKO_FREE_API}{path}"
    if key:
        sep = "&" if "?" in path else "?"
        url += f"{sep}x_cg_demo_api_key={key}"
    return url


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "coingecko_price_history",
            "description": (
                "Получает исторические данные цены криптовалюты (BTC, ETH, ...) "
                "с CoinGecko за указанное количество дней. "
                "Возвращает массив [timestamp_ms, price] для построения графиков."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "coin_id": {
                        "type": "string",
                        "description": (
                            "ID монеты на CoinGecko, например: bitcoin, ethereum, "
                            "solana, dogecoin. По умолчанию bitcoin."
                        ),
                    },
                    "vs_currency": {
                        "type": "string",
                        "description": "Валюта котировки. По умолчанию usd.",
                    },
                    "days": {
                        "type": "integer",
                        "description": (
                            "Количество дней истории: 1, 3, 7, 14, 30, 90, 180, 365, max. "
                            "По умолчанию 3."
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "coingecko_price_current",
            "description": (
                "Получает текущую цену одной или нескольких криптовалют "
                "с CoinGecko (simple/price). "
                "Также возвращает изменение за 24ч, market cap и volume."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "coin_ids": {
                        "type": "string",
                        "description": (
                            "ID монет через запятую, например: bitcoin,ethereum,solana. "
                            "По умолчанию bitcoin."
                        ),
                    },
                    "vs_currencies": {
                        "type": "string",
                        "description": "Валюты через запятую. По умолчанию usd.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "coingecko_search",
            "description": (
                "Ищет монету/токен по названию на CoinGecko. "
                "Возвращает список найденных монет с их id, символом и market cap."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос, например: bitcoin, shiba, pepe.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    ]


def _http_get(url: str, timeout_s: int = 120) -> dict[str, Any]:
    req = urllib_request.Request(url, headers=_api_headers(), method="GET")
    opener = urllib_request.build_opener(_build_proxy_handler())
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib_error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"CoinGecko HTTP {exc.code}: {details[:500]}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"CoinGecko unavailable: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"CoinGecko bad JSON response: {exc}") from exc


def _history(coin_id: str = "bitcoin", vs_currency: str = "usd", days: int = 3) -> dict[str, Any]:
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 3
    path = (
        f"/coins/{urllib_request.quote(coin_id, safe='')}"
        f"/market_chart?vs_currency={urllib_request.quote(vs_currency)}"
        f"&days={days}"
    )
    data = _http_get(_api_url(path))
    prices = data.get("prices") or []
    return {
        "coin_id": coin_id,
        "vs_currency": vs_currency,
        "days": days,
        "data_points": len(prices),
        "prices": prices,
        "market_caps": data.get("market_caps") or [],
        "total_volumes": data.get("total_volumes") or [],
    }


def _current_price(coin_ids: str = "bitcoin", vs_currencies: str = "usd") -> dict[str, Any]:
    ids = [c.strip() for c in coin_ids.split(",") if c.strip()]
    if not ids:
        ids = ["bitcoin"]
    vs = [v.strip() for v in vs_currencies.split(",") if v.strip()]
    if not vs:
        vs = ["usd"]
    params = (
        f"?ids={urllib_request.quote(','.join(ids))}"
        f"&vs_currencies={urllib_request.quote(','.join(vs))}"
        f"&include_24hr_change=true"
        f"&include_market_cap=true"
        f"&include_24hr_vol=true"
    )
    path = f"/simple/price{params}"
    data = _http_get(_api_url(path))
    return {
        "prices": data,
        "queried_at_unix_ms": int(time.time() * 1000),
    }


def _search(query: str) -> dict[str, Any]:
    path = f"/search?query={urllib_request.quote(query)}"
    data = _http_get(_api_url(path))
    coins = data.get("coins") or []
    return {
        "query": query,
        "results": [
            {
                "id": c.get("id", ""),
                "name": c.get("name", ""),
                "symbol": c.get("symbol", ""),
                "market_cap_rank": c.get("market_cap_rank"),
            }
            for c in coins[:20]
        ],
    }


_TOOLS_MAP = {
    "coingecko_price_history": _history,
    "coingecko_price_current": _current_price,
    "coingecko_search": _search,
}


def main() -> None:
    _load_dotenv()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            _send(_make_error(None, -32600, "Request must be an object"))
            continue
        method = str(request.get("method", "")).strip()
        req_id = request.get("id")

        if method.startswith("notifications/"):
            continue

        if method == "tools/list":
            tools = _tool_definitions()
            _send(_make_result(req_id, {"tools": tools}))
            continue

        if method == "tools/call":
            params = request.get("params")
            if params is None:
                params = {}
            if not isinstance(params, dict):
                _send(_make_error(req_id, -32602, "Tool params must be an object"))
                continue
            tool_name = str(params.get("name", "")).strip()
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            handler = _TOOLS_MAP.get(tool_name)
            if not handler:
                _send(
                    _make_error(req_id, -32601, f"Tool not found: {tool_name}")
                )
                continue
            try:
                arguments.pop("output_dir", None)
                result = handler(**arguments)
            except TypeError as exc:
                _send(
                    _make_error(
                        req_id, -32602, f"Invalid params for {tool_name}: {exc}"
                    )
                )
                continue
            except RuntimeError as exc:
                _send(
                    _make_error(
                        req_id, -32000, f"{tool_name} failed: {exc}"
                    )
                )
                continue
            _send(_make_result(req_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}))
            continue

        if method == "initialize":
            _send(
                _make_result(
                    req_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {
                            "name": SERVER_NAME,
                            "version": SERVER_VERSION,
                        },
                        "capabilities": {"tools": {}},
                    },
                )
            )
            continue

        _send(_make_error(req_id, -32601, f"Unknown method: {method}"))


if __name__ == "__main__":
    main()
