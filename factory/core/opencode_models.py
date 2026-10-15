"""Fetch and cache OpenCode model metadata from models.dev."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


_MODELS_URL = os.environ.get(
    "FACTORY_OPENCODE_MODELS_URL",
    "https://models.dev/api.json",
)
_CACHE_TTL = int(os.environ.get("FACTORY_OPENCODE_MODELS_TTL", str(24 * 3600)))


def _cache_path() -> Path:
    data_root = (
        os.environ.get("FACTORY_DATA_DIR")
        or os.environ.get("IGOREK_FACTORY_HOME")
        or os.path.expanduser("~/.igorek-agent-factory")
    )
    return Path(data_root) / "cache" / "opencode_models.json"


def _fetch_models() -> dict | None:
    import urllib.request

    from .isolation.guest_bwrap import guest_mode_requested

    if guest_mode_requested():
        return None
    try:
        req = urllib.request.Request(
            _MODELS_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "IgorekFactory/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_model_info(model: str, provider_id: str = "opencode-go") -> dict[str, Any] | None:
    """Get metadata for a model from cached models.dev data.

    Returns None if the model is not found or the cache is empty.
    """
    cache_file = _cache_path()

    if not cache_file.exists() or (time.time() - cache_file.stat().st_mtime) > _CACHE_TTL:
        data = _fetch_models()
        if data:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    provider = data.get(provider_id, {})
    if not isinstance(provider, dict):
        return None
    models = provider.get("models", {})
    if not isinstance(models, dict):
        return None
    return models.get(model) if isinstance(models.get(model), dict) else None


def get_context_window(model: str, provider_id: str = "opencode-go") -> int | None:
    """Get context window for a model from models.dev cache."""
    info = get_model_info(model, provider_id=provider_id)
    if not info:
        return None
    limit = info.get("limit", {})
    if not isinstance(limit, dict):
        return None
    ctx = limit.get("context")
    if isinstance(ctx, (int, float)) and ctx > 0:
        return int(ctx)
    return None


def list_models(provider_id: str = "opencode-go") -> list[str]:
    """List all available model IDs for a provider."""
    info = get_model_info("__any__", provider_id=provider_id)
    if info:
        return []
    cache_file = _cache_path()
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    provider = data.get(provider_id, {})
    if not isinstance(provider, dict):
        return []
    models = provider.get("models", {})
    if not isinstance(models, dict):
        return []
    return sorted(models.keys())
