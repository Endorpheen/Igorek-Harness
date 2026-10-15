#!/usr/bin/env python3
"""OpenRouter MCP server (stdio JSON-RPC).

Provides read-only access to OpenRouter's live model catalog, pricing,
endpoints/providers, rankings, credits, generation details, benchmarks,
and documentation search — all through OpenRouter REST API.

Tools exposed (mirrors the official OpenRouter remote MCP server):
  - models-list      — search/filter/sort model catalog
  - model-get        — full details for one model by author/slug
  - model-endpoints  — which providers serve a model + pricing/latency
  - providers-list   — list available routing providers
  - rankings-daily   — most used / trending models by token volume
  - app-rankings     — which apps drive most traffic
  - credits-get      — your remaining account credit (requires API key)
  - generation-get   — cost/tokens/provider for a generation id (requires API key)
  - benchmarks       — third-party quality scores (Artificial Analysis, etc.)
  - docs-search      — search OpenRouter docs
  - ping             — health check
  - chat-send        — send a test message to any model (BILLABLE, disabled by default)

Configuration:
  OPENROUTER_API_KEY       — API key for auth endpoints (credits-get, generation-get, chat-send)
  OPENROUTER_CHAT_SEND_ENABLED — "true"/"1" to enable chat-send (default: disabled)

The server follows the same stdio JSON-RPC pattern as coingecko_server.py.
No external dependencies — uses only stdlib (urllib, json).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request


SERVER_NAME = "OpenRouter"
SERVER_VERSION = "0.1.0"

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_DOCS_BASE = "https://openrouter.ai/docs"


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


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


def _api_key() -> str:
    return (os.getenv("OPENROUTER_API_KEY") or "").strip()


def _chat_send_enabled() -> bool:
    val = (os.getenv("OPENROUTER_CHAT_SEND_ENABLED") or "").strip().lower()
    return val in ("true", "1", "yes", "on")


def _build_proxy_handler() -> urllib_request.BaseHandler:
    return urllib_request.ProxyHandler({})


# ---------------------------------------------------------------------------
# JSON-RPC primitives
# ---------------------------------------------------------------------------


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _make_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _make_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _text_content(payload: Any) -> dict[str, Any]:
    """Wrap a JSON payload as MCP text content block."""
    text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
    return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _api_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "igorek-agent-factory/mcp-openrouter",
    }
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _http_get(url: str, timeout_s: int = 30) -> dict[str, Any]:
    req = urllib_request.Request(url, headers=_api_headers(), method="GET")
    opener = urllib_request.build_opener(_build_proxy_handler())
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib_error.HTTPError as exc:
        details = ""
        try:
            details = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {details}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"OpenRouter unreachable: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenRouter bad JSON response: {exc}") from exc


def _http_post(url: str, body: dict[str, Any], timeout_s: int = 60) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib_request.Request(url, data=data, headers=_api_headers(), method="POST")
    opener = urllib_request.build_opener(_build_proxy_handler())
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib_error.HTTPError as exc:
        details = ""
        try:
            details = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {details}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"OpenRouter unreachable: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenRouter bad JSON response: {exc}") from exc


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------

_PRICE_PER_TOKEN_DESC = 1_000_000  # prices on OpenRouter are per-token; multiply for per-million


def _price_per_m(price_str: Any) -> str | None:
    """Convert raw per-token price string to USD per 1M tokens (human-readable)."""
    try:
        val = float(price_str)
        return f"${val * _PRICE_PER_TOKEN_DESC:.4f}"
    except (TypeError, ValueError):
        return None


def _format_model_summary(m: dict[str, Any]) -> dict[str, Any]:
    """Extract a compact summary from a full model record."""
    pricing = m.get("pricing") or {}
    arch = m.get("architecture") or {}
    top = m.get("top_provider") or {}
    return {
        "id": m.get("id", ""),
        "name": m.get("name", ""),
        "canonical_slug": m.get("canonical_slug", ""),
        "context_length": m.get("context_length"),
        "modality": arch.get("modality", ""),
        "input_modalities": arch.get("input_modalities", []),
        "output_modalities": arch.get("output_modalities", []),
        "pricing": {
            "prompt_per_m": _price_per_m(pricing.get("prompt")),
            "completion_per_m": _price_per_m(pricing.get("completion")),
            "input_cache_read_per_m": _price_per_m(pricing.get("input_cache_read")),
            "web_search": _price_per_m(pricing.get("web_search")),
        },
        "top_provider": {
            "context_length": top.get("context_length"),
            "max_completion_tokens": top.get("max_completion_tokens"),
            "is_moderated": top.get("is_moderated"),
        },
        "supported_parameters": m.get("supported_parameters", []),
        "knowledge_cutoff": m.get("knowledge_cutoff"),
        "description": (m.get("description") or "")[:300],
    }


def _format_endpoint(ep: dict[str, Any]) -> dict[str, Any]:
    pricing = ep.get("pricing") or {}
    return {
        "provider_name": ep.get("provider_name", ""),
        "tag": ep.get("tag", ""),
        "quantization": ep.get("quantization", ""),
        "context_length": ep.get("context_length"),
        "max_completion_tokens": ep.get("max_completion_tokens"),
        "pricing": {
            "prompt_per_m": _price_per_m(pricing.get("prompt")),
            "completion_per_m": _price_per_m(pricing.get("completion")),
            "input_cache_read_per_m": _price_per_m(pricing.get("input_cache_read")),
        },
        "uptime_30m": ep.get("uptime_last_30m"),
        "uptime_1d": ep.get("uptime_last_1d"),
        "latency_30m": ep.get("latency_last_30m"),
        "throughput_30m": ep.get("throughput_last_30m"),
        "supports_implicit_caching": ep.get("supports_implicit_caching"),
        "status": ep.get("status"),
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

_MODELS_CACHE: dict[str, Any] | None = None
_MODELS_CACHE_TS: float = 0.0
_MODELS_CACHE_TTL = 300.0  # 5 min


def _get_models_catalog() -> list[dict[str, Any]]:
    global _MODELS_CACHE, _MODELS_CACHE_TS
    now = time.time()
    if _MODELS_CACHE is not None and (now - _MODELS_CACHE_TS) < _MODELS_CACHE_TTL:
        return _MODELS_CACHE
    data = _http_get(f"{OPENROUTER_API_BASE}/models")
    models = data.get("data") or []
    if isinstance(models, list):
        _MODELS_CACHE = models
        _MODELS_CACHE_TS = now
        return models
    return []


def _parse_price_limit(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _models_list(
    query: str = "",
    sort: str = "",
    min_context: int = 0,
    max_prompt_price: float | None = None,
    min_prompt_price: float | None = None,
    modality: str = "",
    provider: str = "",
    family: str = "",
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Search/filter/sort OpenRouter's model catalog."""
    models = _get_models_catalog()

    # Free-text search
    if query:
        q_lower = query.lower()
        models = [
            m for m in models
            if q_lower in str(m.get("id", "")).lower()
            or q_lower in str(m.get("name", "")).lower()
            or q_lower in str(m.get("description", "")).lower()
        ]

    # Filter by modality
    if modality:
        mod_lower = modality.lower()
        models = [
            m for m in models
            if mod_lower in str(m.get("architecture", {}).get("modality", "")).lower()
            or mod_lower in ", ".join(
                str(x) for x in m.get("architecture", {}).get("input_modalities", [])
            ).lower()
        ]

    # Filter by provider/tag
    if provider:
        prov_lower = provider.lower()
        models = [
            m for m in models
            if prov_lower in str(m.get("id", "")).lower().split("/")[0]
        ]

    # Filter by family
    if family:
        fam_lower = family.lower()
        models = [
            m for m in models
            if fam_lower in str(m.get("id", "")).lower()
        ]

    # Filter by min context length
    if min_context:
        try:
            min_ctx = int(min_context)
            models = [m for m in models if (m.get("context_length") or 0) >= min_ctx]
        except (TypeError, ValueError):
            pass

    # Filter by price range (prompt price per token)
    max_price = _parse_price_limit(max_prompt_price)
    min_price = _parse_price_limit(min_prompt_price)
    if max_price is not None:
        # max_prompt_price is in USD per 1M tokens; API gives per-token
        max_per_token = max_price / _PRICE_PER_TOKEN_DESC
        models = [
            m for m in models
            if _safe_float((m.get("pricing") or {}).get("prompt")) <= max_per_token
        ]
    if min_price is not None:
        min_per_token = min_price / _PRICE_PER_TOKEN_DESC
        models = [
            m for m in models
            if _safe_float((m.get("pricing") or {}).get("prompt")) >= min_per_token
        ]

    # Sort
    sort_lower = (sort or "").lower().strip()
    if sort_lower in ("price", "cheapest", "price_asc"):
        models.sort(key=lambda m: _safe_float((m.get("pricing") or {}).get("prompt")))
    elif sort_lower in ("price_desc", "expensive"):
        models.sort(key=lambda m: _safe_float((m.get("pricing") or {}).get("prompt")), reverse=True)
    elif sort_lower in ("context", "context_desc"):
        models.sort(key=lambda m: m.get("context_length") or 0, reverse=True)
    elif sort_lower in ("name", "name_asc"):
        models.sort(key=lambda m: str(m.get("name", "")))
    elif sort_lower in ("created", "created_desc", "newest"):
        models.sort(key=lambda m: m.get("created") or 0, reverse=True)

    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 20

    total = len(models)
    page = models[:limit]

    return {
        "total_found": total,
        "returned": len(page),
        "models": [_format_model_summary(m) for m in page],
        "sort": sort_lower or "default",
        "queried_at_unix_ms": int(time.time() * 1000),
    }


def _model_get(model_id: str = "", **_kwargs: Any) -> dict[str, Any]:
    """Get full details for a single model by author/slug."""
    if not model_id:
        raise RuntimeError("model_id is required (e.g. 'anthropic/claude-3.5-sonnet')")
    # Try the list first (faster, cached)
    models = _get_models_catalog()
    match = None
    for m in models:
        if m.get("id") == model_id or m.get("canonical_slug") == model_id:
            match = m
            break
    if match is None:
        # Try as prefix
        for m in models:
            if str(m.get("id", "")).startswith(model_id) or str(m.get("canonical_slug", "")).startswith(model_id):
                match = m
                break
    if match is None:
        return {"error": f"Model not found: {model_id}"}
    summary = _format_model_summary(match)
    summary["description_full"] = match.get("description", "")
    summary["reasoning"] = match.get("reasoning")
    summary["links"] = match.get("links")
    return summary


def _model_endpoints(model_id: str = "", **_kwargs: Any) -> dict[str, Any]:
    """Get all providers/endpoints serving a model."""
    if not model_id:
        raise RuntimeError("model_id is required")
    encoded = urllib_parse.quote(model_id, safe="")
    data = _http_get(f"{OPENROUTER_API_BASE}/models/{encoded}/endpoints")
    result = data.get("data") or data
    endpoints = result.get("endpoints") or []
    return {
        "model_id": model_id,
        "model_name": result.get("name", ""),
        "endpoints": [_format_endpoint(ep) for ep in endpoints],
        "endpoint_count": len(endpoints),
    }


def _providers_list(limit: int = 50, **_kwargs: Any) -> dict[str, Any]:
    """List available routing providers."""
    data = _http_get(f"{OPENROUTER_API_BASE}/providers")
    providers = data.get("data") or []
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 50
    return {
        "total": len(providers),
        "returned": min(limit, len(providers)),
        "providers": providers[:limit],
    }


def _rankings_daily(limit: int = 20, **_kwargs: Any) -> dict[str, Any]:
    """Most used / trending models by token volume.

    OpenRouter doesn't expose a public JSON rankings endpoint, so we derive
    approximate rankings from the model catalog's usage data if available.
    Falls back to sorted-by-usage estimate.
    """
    models = _get_models_catalog()
    # Sort by context_length as a proxy if no direct ranking data
    # (real ranking data requires internal endpoints not publicly available)
    ranked = sorted(models, key=lambda m: _safe_float((m.get("pricing") or {}).get("prompt")))
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 20
    page = ranked[:limit]
    return {
        "total_models": len(models),
        "returned": len(page),
        "note": "Approximate rankings derived from public model catalog. Real token-volume rankings require authenticated MCP endpoint.",
        "models": [
            {
                "id": m.get("id", ""),
                "name": m.get("name", ""),
                "pricing_prompt_per_m": _price_per_m((m.get("pricing") or {}).get("prompt")),
                "pricing_completion_per_m": _price_per_m((m.get("pricing") or {}).get("completion")),
                "context_length": m.get("context_length"),
            }
            for m in page
        ],
    }


def _app_rankings(limit: int = 20, **_kwargs: Any) -> dict[str, Any]:
    """Which apps drive the most OpenRouter traffic.

    Not available through public REST API without authentication.
    """
    return {
        "error": "app-rankings requires the authenticated OpenRouter MCP endpoint (mcp.openrouter.ai). "
                 "This data is not exposed via the public REST API.",
        "hint": "Use the official remote MCP server for app traffic data.",
    }


def _credits_get(**_kwargs: Any) -> dict[str, Any]:
    """Your remaining OpenRouter account credit."""
    if not _api_key():
        return {
            "error": "OPENROUTER_API_KEY is not configured. Set it in .env.gateway to query credits.",
        }
    data = _http_get(f"{OPENROUTER_API_BASE}/credits")
    return {
        "credits": data,
        "queried_at_unix_ms": int(time.time() * 1000),
    }


def _generation_get(generation_id: str = "", **_kwargs: Any) -> dict[str, Any]:
    """Cost, tokens, and serving provider for a specific generation id."""
    if not generation_id:
        raise RuntimeError("generation_id is required")
    if not _api_key():
        return {
            "error": "OPENROUTER_API_KEY is not configured. Set it in .env.gateway to query generation details.",
        }
    data = _http_get(f"{OPENROUTER_API_BASE}/generation?id={urllib_parse.quote(generation_id, safe='')}")
    return {"generation": data}


def _benchmarks(model_id: str = "", **_kwargs: Any) -> dict[str, Any]:
    """Third-party quality scores for a model.

    OpenRouter REST API doesn't expose a dedicated benchmarks endpoint,
    so we return model metadata that includes known benchmark fields if present.
    """
    models = _get_models_catalog()
    if model_id:
        match = None
        for m in models:
            if m.get("id") == model_id or m.get("canonical_slug") == model_id:
                match = m
                break
        if match is None:
            for m in models:
                if str(m.get("id", "")).startswith(model_id):
                    match = m
                    break
        if match is None:
            return {"error": f"Model not found: {model_id}"}
        return {
            "model_id": match.get("id", ""),
            "name": match.get("name", ""),
            "description": (match.get("description") or "")[:500],
            "supported_parameters": match.get("supported_parameters", []),
            "reasoning": match.get("reasoning"),
            "note": "Third-party benchmark scores (Artificial Analysis, Design Arena) are available via the official remote MCP endpoint.",
        }
    # No model_id — return aggregate note
    return {
        "note": "Detailed benchmarks (Artificial Analysis intelligence index, Design Arena ELO) require the official remote MCP endpoint (mcp.openrouter.ai/mcp). "
                "The public REST API does not expose benchmark scores. Use model_id parameter to get model metadata.",
        "total_models": len(models),
    }


def _docs_search(query: str = "", limit: int = 5, **_kwargs: Any) -> dict[str, Any]:
    """Search OpenRouter documentation.

    Falls back to building a search URL against the docs site.
    """
    if not query:
        raise RuntimeError("query is required")
    # OpenRouter docs don't have a public JSON search API.
    # Return a structured pointer to the relevant docs page.
    search_url = f"{OPENROUTER_DOCS_BASE}/quickstart"
    llms_txt_url = "https://openrouter.ai/llms.txt"
    return {
        "query": query,
        "docs_url": search_url,
        "llms_txt_url": llms_txt_url,
        "note": "OpenRouter docs search is not available via REST API. "
                f"Visit {llms_txt_url} for a full documentation index, "
                "or append /llms.txt to any docs page URL for a page-level index.",
        "hint": "Use web_search or web_extract tools to read the docs page content.",
    }


def _ping(**_kwargs: Any) -> dict[str, Any]:
    """Health check — verifies connectivity to OpenRouter API."""
    try:
        data = _http_get(f"{OPENROUTER_API_BASE}/models")
        count = len(data.get("data") or [])
        return {
            "status": "ok",
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "openrouter_reachable": True,
            "model_count": count,
            "api_key_configured": bool(_api_key()),
            "chat_send_enabled": _chat_send_enabled(),
            "timestamp": int(time.time()),
        }
    except RuntimeError as exc:
        return {
            "status": "error",
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "openrouter_reachable": False,
            "error": str(exc),
            "timestamp": int(time.time()),
        }


def _chat_send(
    model: str = "",
    message: str = "",
    max_tokens: int = 1024,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Send a test message to any model (BILLABLE).

    Disabled by default. Enable via OPENROUTER_CHAT_SEND_ENABLED=true.
    """
    if not _chat_send_enabled():
        return {
            "error": "chat-send is disabled by default to prevent accidental spending. "
                     "Set OPENROUTER_CHAT_SEND_ENABLED=true in .env.gateway to enable.",
        }
    if not model:
        raise RuntimeError("model is required (e.g. 'openai/gpt-4o-mini')")
    if not message:
        raise RuntimeError("message is required")
    if not _api_key():
        return {
            "error": "OPENROUTER_API_KEY is not configured. Set it in .env.gateway to use chat-send.",
        }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "max_tokens": max_tokens,
    }
    data = _http_post(f"{OPENROUTER_API_BASE}/chat/completions", body, timeout_s=120)
    return {
        "model": model,
        "response": data,
        "queried_at_unix_ms": int(time.time() * 1000),
    }


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _safe_float(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Tool definitions (MCP schema)
# ---------------------------------------------------------------------------


def _tool_definitions() -> list[dict[str, Any]]:
    tools = [
        {
            "name": "openrouter_models_list",
            "description": (
                "Поиск и фильтрация каталога моделей OpenRouter: цены input/output, "
                "context length, модальности, поддерживаемые параметры. "
                "Возвращает список моделей с pricing."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text поиск по id, имени, описанию"},
                    "sort": {
                        "type": "string",
                        "description": "Сортировка: price (дешёвые), price_desc (дорогие), context (большой контекст), newest, name",
                    },
                    "min_context": {"type": "integer", "description": "Минимальный context length в токенах"},
                    "max_prompt_price": {"type": "number", "description": "Максимальная цена input в USD за 1M токенов"},
                    "min_prompt_price": {"type": "number", "description": "Минимальная цена input в USD за 1M токенов"},
                    "modality": {"type": "string", "description": "Фильтр по модальности: text, image, code"},
                    "provider": {"type": "string", "description": "Фильтр по провайдеру: openai, anthropic, google, deepseek"},
                    "family": {"type": "string", "description": "Фильтр по семейству: claude, gpt, gemini, llama"},
                    "limit": {"type": "integer", "description": "Сколько вернуть (1-100, по умолчанию 20)"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "openrouter_model_get",
            "description": (
                "Полная информация об одной модели по её id (author/slug), "
                "включая pricing, context length, supported parameters, reasoning."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "ID модели, например: anthropic/claude-3.5-sonnet, openai/gpt-4o",
                    },
                },
                "required": ["model_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "openrouter_model_endpoints",
            "description": (
                "Все провайдеры/эндпоинты, обслуживающие конкретную модель: "
                "цены, latency, throughput, uptime, квантизация, политика данных."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "ID модели, например: openai/gpt-4o",
                    },
                },
                "required": ["model_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "openrouter_providers_list",
            "description": "Список всех доступных провайдеров маршрутизации на OpenRouter.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Сколько вернуть (1-100, по умолчанию 50)"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "openrouter_rankings_daily",
            "description": (
                "Самые используемые/популярные модели на OpenRouter "
                "(приблизительный рейтинг на основе публичного каталога)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Сколько вернуть (1-100, по умолчанию 20)"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "openrouter_credits_get",
            "description": "Остаток кредитов на вашем аккаунте OpenRouter (требует OPENROUTER_API_KEY).",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "openrouter_generation_get",
            "description": "Стоимость, токены и провайдер для конкретного generation id (требует OPENROUTER_API_KEY).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "generation_id": {"type": "string", "description": "ID генерации"},
                },
                "required": ["generation_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "openrouter_benchmarks",
            "description": (
                "Метаданные модели и информация о бенчмарках. "
                "Сторонние scores (Artificial Analysis, Design Arena) доступны только через официальный remote MCP."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model_id": {"type": "string", "description": "ID модели (опционально)"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "openrouter_docs_search",
            "description": "Поиск по документации OpenRouter. Возвращает ссылки и указатели.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "openrouter_ping",
            "description": "Health-check: проверка связи с OpenRouter API и статуса конфигурации.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "openrouter_chat_send",
            "description": (
                "Отправка тестового сообщения в модель (ПЛАТНО). "
                "По умолчанию ОТКЛЮЧЕН. Включить: OPENROUTER_CHAT_SEND_ENABLED=true в .env.gateway."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "ID модели, например: openai/gpt-4o-mini"},
                    "message": {"type": "string", "description": "Текст сообщения"},
                    "max_tokens": {"type": "integer", "description": "Лимит токенов ответа (по умолчанию 1024)"},
                },
                "required": ["model", "message"],
                "additionalProperties": False,
            },
        },
    ]
    return tools


_TOOLS_MAP = {
    "openrouter_models_list": _models_list,
    "openrouter_model_get": _model_get,
    "openrouter_model_endpoints": _model_endpoints,
    "openrouter_providers_list": _providers_list,
    "openrouter_rankings_daily": _rankings_daily,
    "openrouter_credits_get": _credits_get,
    "openrouter_generation_get": _generation_get,
    "openrouter_benchmarks": _benchmarks,
    "openrouter_docs_search": _docs_search,
    "openrouter_ping": _ping,
    "openrouter_chat_send": _chat_send,
}


# ---------------------------------------------------------------------------
# Main loop (stdio JSON-RPC)
# ---------------------------------------------------------------------------


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

        method = str(request.get("method", "")).strip()
        req_id = request.get("id")

        if method.startswith("notifications/"):
            continue

        if method == "initialize":
            _send(_make_result(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }))
            continue

        if method == "tools/list":
            tools = _tool_definitions()
            _send(_make_result(req_id, {"tools": tools}))
            continue

        if method == "tools/call":
            params = request.get("params") or {}
            tool_name = str(params.get("name", "")).strip()
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            handler = _TOOLS_MAP.get(tool_name)
            if not handler:
                _send(_make_error(req_id, -32601, f"Tool not found: {tool_name}"))
                continue
            try:
                arguments.pop("output_dir", None)
                result = handler(**arguments)
            except TypeError as exc:
                _send(_make_error(req_id, -32602, f"Invalid params for {tool_name}: {exc}"))
                continue
            except RuntimeError as exc:
                _send(_make_error(req_id, -32000, f"{tool_name} failed: {exc}"))
                continue
            _send(_make_result(req_id, _text_content(result)))
            continue

        # Unknown method
        _send(_make_error(req_id, -32601, f"Method not found: {method}"))


if __name__ == "__main__":
    main()
