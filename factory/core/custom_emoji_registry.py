"""Custom Telegram emoji registry.

Stores mappings of custom_emoji_id → (alias, fallback) extracted
from Telegram message JSON dumps.

Usage:
    from factory.core.custom_emoji_registry import register_from_json, get_emoji, emoji_registry

    # Register from a Telegram message JSON:
    register_from_json({"text": "\u270c\ufe0f", "entities": [{"type": "custom_emoji", "custom_emoji_id": "5978797211373276715"}]})

    # Register with explicit alias:
    register_from_json(data, alias="pepe_1")

    # Look up:
    entry = get_emoji("5978797211373276715")
    # → {"id": "5978797211373276715", "alias": "custom_1", "fallback": "✌️"}
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_registry_path() -> Path:
    """Resolve registry path: prefer data root, fallback to project root."""
    try:
        from .runtime import get_data_root
        data_root = Path(get_data_root())
        if data_root.exists() or data_root.parent.exists():
            return data_root / "custom_emoji_registry.json"
    except Exception:
        pass
    return Path(__file__).resolve().parent.parent.parent / "custom_emoji_registry.json"


REGISTRY_PATH = _default_registry_path()

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_registry: dict[str, dict[str, str]] = {}  # custom_emoji_id → {id, alias, fallback}
_alias_counter: int = 0
_next_alias_num: int = 1


def _generate_alias() -> str:
    global _next_alias_num
    alias = f"custom_{_next_alias_num}"
    _next_alias_num += 1
    return alias


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load() -> None:
    """Load registry from disk if it exists."""
    global _next_alias_num
    if not REGISTRY_PATH.exists():
        return
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for emoji_id, entry in data.items():
                if isinstance(entry, dict) and "id" in entry:
                    _registry[emoji_id] = entry
            # Set next alias counter above any existing custom_N aliases
            for entry in _registry.values():
                alias = entry.get("alias", "")
                m = re.match(r"custom_(\d+)$", alias)
                if m:
                    _next_alias_num = max(_next_alias_num, int(m.group(1)) + 1)
    except Exception as exc:
        logger.warning("Failed to load custom emoji registry: %s", exc)


def _save() -> None:
    """Save registry to disk."""
    try:
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY_PATH.write_text(
            json.dumps(_registry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to save custom emoji registry: %s", exc)


# Load on import
_load()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_from_json(message: dict[str, Any], alias: str | None = None) -> list[dict[str, str]]:
    """Extract custom emojis from a Telegram message JSON and register them.

    Args:
        message: Telegram message object with ``text`` and ``entities``.
        alias: Optional alias. If not given, auto-generated (custom_1, custom_2, ...).

    Returns:
        List of registered entries (one per custom_emoji entity found).
    """
    text = message.get("text", "")
    entities = message.get("entities", [])
    if not isinstance(entities, list):
        return []

    registered: list[dict[str, str]] = []

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if entity.get("type") != "custom_emoji":
            continue

        emoji_id = str(entity.get("custom_emoji_id", "")).strip()
        if not emoji_id:
            continue

        # Extract fallback text using offset and length
        offset = entity.get("offset", 0)
        length = entity.get("length", 1)
        if isinstance(text, str) and offset < len(text):
            fallback = text[offset : offset + length]
        else:
            fallback = ""

        if emoji_id in _registry:
            # Already registered — update fallback if changed, keep existing alias
            existing = _registry[emoji_id]
            if fallback and existing.get("fallback") != fallback:
                existing["fallback"] = fallback
                _save()
            registered.append(existing)
            continue

        entry_alias = alias if alias else _generate_alias()
        entry = {
            "id": emoji_id,
            "alias": entry_alias,
            "fallback": fallback,
        }
        _registry[emoji_id] = entry
        registered.append(entry)
        logger.info("Registered custom emoji: %s → %s (fallback: %s)", emoji_id, entry_alias, fallback)

    if registered:
        _save()

    return registered


def get_emoji(emoji_id: str) -> dict[str, str] | None:
    """Look up a custom emoji by its ID.

    Returns ``{"id", "alias", "fallback"}`` or ``None``.
    """
    return _registry.get(str(emoji_id))


def get_emoji_by_alias(alias: str) -> dict[str, str] | None:
    """Look up a custom emoji by its alias."""
    for entry in _registry.values():
        if entry.get("alias") == alias:
            return entry
    return None


def list_all() -> list[dict[str, str]]:
    """Return all registered custom emojis."""
    return list(_registry.values())


def format_for_prompt() -> str:
    """Return a text summary of all custom emojis for use in prompts."""
    if not _registry:
        return ""
    lines = ["Custom emoji available:"]
    for entry in _registry.values():
        lines.append(f"  {entry['alias']} → id:{entry['id']} (fallback: {entry.get('fallback', '')})")
    return "\n".join(lines)
