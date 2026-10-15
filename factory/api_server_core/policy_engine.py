from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


POLICY_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "inference-policy.yaml"

_POLICY_CACHE: dict[str, Any] | None = None
_POLICY_MTIME: float | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _load_policy_file() -> dict[str, Any]:
    if not POLICY_CONFIG_PATH.exists():
        return {}
    raw = yaml.safe_load(POLICY_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    return raw


def load_policy_config() -> dict[str, Any]:
    global _POLICY_CACHE, _POLICY_MTIME

    if POLICY_CONFIG_PATH.exists():
        mtime = POLICY_CONFIG_PATH.stat().st_mtime
    else:
        mtime = None

    if _POLICY_CACHE is not None and _POLICY_MTIME == mtime:
        return _POLICY_CACHE

    raw = _load_policy_file()
    defaults = raw.get("defaults", {}) if isinstance(raw.get("defaults"), dict) else {}
    profiles = raw.get("profiles", {}) if isinstance(raw.get("profiles"), dict) else {}
    active_profile = str(raw.get("active_profile", "default"))
    profile = profiles.get(active_profile, {})
    if not isinstance(profile, dict):
        profile = {}

    merged = _deep_merge(defaults, profile)
    _POLICY_CACHE = merged
    _POLICY_MTIME = mtime
    return merged


def policy_section(name: str) -> dict[str, Any]:
    value = load_policy_config().get(name, {})
    return value if isinstance(value, dict) else {}


def policy_bool(section: str, key: str, default: bool = False) -> bool:
    value = policy_section(section).get(key, default)
    return value if isinstance(value, bool) else default


def policy_list(section: str, key: str, default: list[str] | None = None) -> list[str]:
    value = policy_section(section).get(key, default or [])
    if not isinstance(value, list):
        return list(default or [])
    return [str(item) for item in value]
