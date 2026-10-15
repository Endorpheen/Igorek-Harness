from __future__ import annotations

import json
import time
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .runtime import get_data_root


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60


def _pricing_cache_path() -> Path:
    return get_data_root() / "cache" / "openrouter_models.json"


def _decimal(value: Any) -> Decimal:
    try:
        if value is None:
            return Decimal("0")
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return max(0, int(value))
    except Exception:
        return 0


def _read_cached_models(cache_path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_cached_models(cache_path: Path, payload: dict[str, Any]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        return


def _fetch_models(timeout_s: float) -> dict[str, Any] | None:
    from .isolation.guest_bwrap import guest_mode_requested

    if guest_mode_requested():
        return None
    try:
        request = urllib.request.Request(
            OPENROUTER_MODELS_URL,
            headers={"User-Agent": "igorek-agent-factory"},
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def load_openrouter_model_pricing(
    model_id: str,
    *,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    timeout_s: float = 1.0,
    cache_path: Path | None = None,
) -> dict[str, Any] | None:
    model_id = str(model_id or "").strip()
    if not model_id:
        return None

    path = cache_path or _pricing_cache_path()
    cached = _read_cached_models(path)
    now = int(time.time())
    if cached and now - int(cached.get("fetched_at") or 0) <= cache_ttl_seconds:
        pricing = _pricing_from_payload(cached.get("payload"), model_id)
        if pricing is not None:
            return pricing

    fetched = _fetch_models(timeout_s)
    if fetched is not None:
        _write_cached_models(path, {"fetched_at": now, "payload": fetched})
        pricing = _pricing_from_payload(fetched, model_id)
        if pricing is not None:
            return pricing

    if cached:
        return _pricing_from_payload(cached.get("payload"), model_id)
    return None


def _pricing_from_payload(payload: Any, model_id: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    aliases = {model_id}
    if model_id.endswith(":free"):
        aliases.add(model_id.removesuffix(":free"))
    for item in data:
        if not isinstance(item, dict):
            continue
        item_ids = {
            str(item.get("id") or ""),
            str(item.get("canonical_slug") or ""),
        }
        if not aliases.intersection(item_ids):
            continue
        pricing = item.get("pricing")
        return pricing if isinstance(pricing, dict) else None
    return None


def estimate_openrouter_cost_usd(
    usage: dict[str, Any],
    pricing: dict[str, Any] | None,
) -> float:
    if not isinstance(usage, dict) or not isinstance(pricing, dict):
        return 0.0

    input_tokens = _safe_int(usage.get("input_tokens"))
    if input_tokens <= 0:
        input_tokens = _safe_int(usage.get("prompt")) or _safe_int(
            usage.get("prompt_tokens")
        )

    output_tokens = _safe_int(usage.get("output_tokens"))
    if output_tokens <= 0:
        output_tokens = _safe_int(usage.get("completion")) or _safe_int(
            usage.get("completion_tokens")
        )

    reasoning_tokens = _safe_int(usage.get("reasoning_output_tokens")) or _safe_int(
        usage.get("reasoning_tokens")
    )
    cache_read_tokens = (
        _safe_int(usage.get("cache_read_input_tokens"))
        or _safe_int(usage.get("cached_input_tokens"))
        or _safe_int(usage.get("cache_read_tokens"))
    )
    cache_write_tokens = (
        _safe_int(usage.get("cache_creation_input_tokens"))
        or _safe_int(usage.get("cache_write_input_tokens"))
        or _safe_int(usage.get("cache_write_tokens"))
    )

    output_billable = max(0, output_tokens - reasoning_tokens)
    reasoning_rate = _decimal(pricing.get("internal_reasoning")) or _decimal(
        pricing.get("completion")
    )
    total = (
        Decimal(input_tokens) * _decimal(pricing.get("prompt"))
        + Decimal(output_billable) * _decimal(pricing.get("completion"))
        + Decimal(reasoning_tokens) * reasoning_rate
        + Decimal(cache_read_tokens) * _decimal(pricing.get("input_cache_read"))
        + Decimal(cache_write_tokens) * _decimal(pricing.get("input_cache_write"))
    )
    return float(total)


def _context_length_from_payload(payload: Any, model_id: str) -> int | None:
    """Extract context_length for a model from the OpenRouter models payload."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    aliases = {model_id}
    if model_id.endswith(":free"):
        aliases.add(model_id.removesuffix(":free"))
    for item in data:
        if not isinstance(item, dict):
            continue
        item_ids = {
            str(item.get("id") or ""),
            str(item.get("canonical_slug") or ""),
        }
        if not aliases.intersection(item_ids):
            continue
        ctx = _safe_int(item.get("context_length"))
        if ctx > 0:
            return ctx
        top = item.get("top_provider")
        if isinstance(top, dict):
            ctx = _safe_int(top.get("context_length"))
            if ctx > 0:
                return ctx
    return None


def get_openrouter_context_window(
    model_id: str,
    *,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    timeout_s: float = 1.0,
    cache_path: Path | None = None,
) -> int | None:
    """Return the context_length for an OpenRouter model, using cache with TTL."""
    model_id = str(model_id or "").strip()
    if not model_id:
        return None

    path = cache_path or _pricing_cache_path()
    cached = _read_cached_models(path)
    now = int(time.time())
    if cached and now - int(cached.get("fetched_at") or 0) <= cache_ttl_seconds:
        result = _context_length_from_payload(cached.get("payload"), model_id)
        if result is not None:
            return result

    fetched = _fetch_models(timeout_s)
    if fetched is not None:
        _write_cached_models(path, {"fetched_at": now, "payload": fetched})
        result = _context_length_from_payload(fetched, model_id)
        if result is not None:
            return result

    if cached:
        return _context_length_from_payload(cached.get("payload"), model_id)
    return None


def estimate_cost_for_openrouter_model(model_id: str, usage: dict[str, Any]) -> float:
    pricing = load_openrouter_model_pricing(model_id)
    return estimate_openrouter_cost_usd(usage, pricing)
