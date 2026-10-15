from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .openrouter_pricing import (
    DEFAULT_CACHE_TTL_SECONDS,
    _fetch_models,
    _pricing_cache_path,
    _read_cached_models,
    _safe_int,
    _write_cached_models,
)
from .runtime import get_profile_path


CODING_HINT_TERMS = (
    "coder",
    "coding",
    "code",
    "cobuddy",
    "laguna",
    "devstral",
    "gpt-oss",
)


@dataclass(frozen=True)
class OpenRouterModelCandidate:
    model_id: str
    name: str
    created: int
    context_length: int
    max_completion_tokens: int
    coding_hint_score: int
    supported_parameters: tuple[str, ...]
    score: tuple[int, int, int, int, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "name": self.name,
            "created": self.created,
            "context_length": self.context_length,
            "max_completion_tokens": self.max_completion_tokens,
            "coding_hint_score": self.coding_hint_score,
            "supported_parameters": list(self.supported_parameters),
            "score": list(self.score),
        }


def _decimal(value: Any) -> Decimal:
    try:
        if value is None:
            return Decimal("0")
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _is_free_text_model(item: dict[str, Any]) -> bool:
    model_id = str(item.get("id") or "")
    pricing = item.get("pricing")
    if not model_id.endswith(":free") or not isinstance(pricing, dict):
        return False
    return _decimal(pricing.get("prompt")) == 0 and _decimal(pricing.get("completion")) == 0


def _supports_tools(item: dict[str, Any]) -> bool:
    params = item.get("supported_parameters")
    return isinstance(params, list) and "tools" in {str(p) for p in params}


def _supports_text_output(item: dict[str, Any]) -> bool:
    architecture = item.get("architecture")
    if not isinstance(architecture, dict):
        return True
    outputs = architecture.get("output_modalities")
    if not isinstance(outputs, list):
        return True
    return "text" in {str(output) for output in outputs}


def _top_provider_int(item: dict[str, Any], key: str) -> int:
    top = item.get("top_provider")
    if not isinstance(top, dict):
        return 0
    return _safe_int(top.get(key))


def _coding_hint_score(item: dict[str, Any]) -> int:
    haystack = f"{item.get('id') or ''} {item.get('name') or ''}".lower()
    return sum(1 for term in CODING_HINT_TERMS if term in haystack)


def select_free_openrouter_models(
    payload: dict[str, Any],
    *,
    require_tools: bool = True,
    min_context: int = 65536,
    max_age_days: int | None = 31,
    reference_ts: int | None = None,
    exclude_models: set[str] | None = None,
    limit: int = 10,
) -> list[OpenRouterModelCandidate]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []

    candidates: list[OpenRouterModelCandidate] = []
    excluded = {str(model).strip() for model in (exclude_models or set()) if str(model).strip()}
    min_created = 0
    if max_age_days is not None and max_age_days > 0:
        min_created = int(reference_ts or time.time()) - (max_age_days * 24 * 60 * 60)

    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "")
        canonical_slug = str(item.get("canonical_slug") or "")
        if model_id in excluded or canonical_slug in excluded:
            continue
        created = _safe_int(item.get("created"))
        if min_created and created < min_created:
            continue
        if not _is_free_text_model(item):
            continue
        if require_tools and not _supports_tools(item):
            continue
        if not _supports_text_output(item):
            continue

        context_length = _safe_int(item.get("context_length")) or _top_provider_int(
            item, "context_length"
        )
        if context_length < min_context:
            continue

        supported_parameters = tuple(
            sorted(str(p) for p in item.get("supported_parameters") or [])
        )
        max_completion = _top_provider_int(item, "max_completion_tokens")
        coding_score = _coding_hint_score(item)
        score = (
            created,
            min(context_length, 262144),
            coding_score,
            max_completion,
            len(supported_parameters),
        )
        candidates.append(
            OpenRouterModelCandidate(
                model_id=model_id,
                name=str(item.get("name") or ""),
                created=created,
                context_length=context_length,
                max_completion_tokens=max_completion,
                coding_hint_score=coding_score,
                supported_parameters=supported_parameters,
                score=score,
            )
        )

    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[: max(1, limit)]


def load_openrouter_models_payload(
    *,
    refresh: bool = False,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    timeout_s: float = 5.0,
    cache_path: Path | None = None,
) -> dict[str, Any] | None:
    path = cache_path or _pricing_cache_path()
    cached = _read_cached_models(path)
    now = int(time.time())
    if (
        not refresh
        and cached
        and now - int(cached.get("fetched_at") or 0) <= cache_ttl_seconds
    ):
        payload = cached.get("payload")
        return payload if isinstance(payload, dict) else None

    fetched = _fetch_models(timeout_s)
    if fetched is not None:
        _write_cached_models(path, {"fetched_at": now, "payload": fetched})
        return fetched

    if cached:
        payload = cached.get("payload")
        return payload if isinstance(payload, dict) else None
    return None


def update_openrouter_profile_model(
    *,
    profile: str = "cc-openrouter",
    profiles_dir: str | Path | None = None,
    refresh: bool = True,
    dry_run: bool = False,
    require_tools: bool = True,
    min_context: int = 65536,
    max_age_days: int | None = 31,
    exclude_models: set[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    payload = load_openrouter_models_payload(refresh=refresh)
    if payload is None:
        return {
            "status": "fail",
            "reason": "openrouter_models_unavailable",
            "profile": profile,
        }

    candidates = select_free_openrouter_models(
        payload,
        require_tools=require_tools,
        min_context=min_context,
        max_age_days=max_age_days,
        reference_ts=int(time.time()),
        exclude_models=exclude_models,
        limit=limit,
    )
    if not candidates:
        return {
            "status": "fail",
            "reason": "no_matching_free_models",
            "profile": profile,
            "require_tools": require_tools,
            "min_context": min_context,
            "max_age_days": max_age_days,
            "exclude_models": sorted(exclude_models or []),
        }

    profile_path = get_profile_path(profile, profiles_dir=profiles_dir)
    if not profile_path.exists():
        return {
            "status": "fail",
            "reason": "profile_not_found",
            "profile": profile,
            "profile_path": str(profile_path),
            "selected": candidates[0].as_dict(),
            "candidates": [candidate.as_dict() for candidate in candidates],
        }

    profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(profile_data, dict):
        return {
            "status": "fail",
            "reason": "invalid_profile_json",
            "profile": profile,
            "profile_path": str(profile_path),
        }

    selected = candidates[0]
    previous_model = str(profile_data.get("model") or "")
    if not dry_run and previous_model != selected.model_id:
        backup_path = profile_path.with_suffix(
            f"{profile_path.suffix}.{int(time.time())}.bak"
        )
        shutil.copy2(profile_path, backup_path)
        profile_data["model"] = selected.model_id
        profile_path.write_text(
            json.dumps(profile_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        backup_path = None

    return {
        "status": "ok",
        "profile": profile,
        "profile_path": str(profile_path),
        "dry_run": dry_run,
        "changed": previous_model != selected.model_id and not dry_run,
        "previous_model": previous_model,
        "selected": selected.as_dict(),
        "backup_path": str(backup_path) if backup_path else "",
        "candidates": [candidate.as_dict() for candidate in candidates],
    }
