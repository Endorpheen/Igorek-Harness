"""Access level model: four tiers and their runtime projection.

No database access or side effects—only pure mapping and comparison functions.
Single source of truth for backend levels.

Levels:
  strict   — maximum protection;
  standard — default state;
  trusted  — moderate weakening (requires TTL; see security_settings);
  full     — maximum weakening (requires TTL; see security_settings).

`user-read` is NOT a level: this is a file profile mode, handled separately (see resolve_security_level) and should not be included in level_to_runtime.
"""

from __future__ import annotations

LEVELS: tuple[str, ...] = ("strict", "standard", "trusted", "full")

DEFAULT_LEVEL: str = "standard"

# The higher the rank, the weaker/less secure the level. This is used for determining degradation relative to the baseline.
_LEVEL_RANK: dict[str, int] = {
    "strict": 0,
    "standard": 1,
    "trusted": 2,
    "full": 3,
}

# The only runtime-level mapping consumed by backends:
#   security_mode   — for external backends (SecurityRuntime);
#   guard_mode      — for cc-backend tools (bash scope guard);
#   filesystem_mode — for cc-backend tools.
_RUNTIME_BY_LEVEL: dict[str, dict[str, str]] = {
    "strict": {
        "security_mode": "enforce",
        "guard_mode": "on",
        "filesystem_mode": "workspace",
    },
    "standard": {
        "security_mode": "observe",
        "guard_mode": "on",
        "filesystem_mode": "workspace",
    },
    "trusted": {
        "security_mode": "observe",
        "guard_mode": "warn",
        "filesystem_mode": "workspace",
    },
    "full": {
        "security_mode": "disabled",
        "guard_mode": "off",
        "filesystem_mode": "full-dangerous",
    },
}


def normalize_level(value: object) -> str:
    """Converts a value to one of the LEVELS (case and spaces are irrelevant).

    Raises ValueError for any value not in LEVELS.
    """
    normalized = str(value or "").strip().lower()
    if normalized not in LEVELS:
        raise ValueError(f"Unknown security level: {value!r}")
    return normalized


def level_to_runtime(level: object) -> dict[str, str]:
    """Runtime projection of the level into {security_mode, guard_mode, filesystem_mode}.

    Accepts ONLY four levels from LEVELS; `user-read` and other values raise ValueError.
    """
    normalized = normalize_level(level)
    return dict(_RUNTIME_BY_LEVEL[normalized])


def level_rank(level: object) -> int:
    """Numeric rank of the level (higher = more accessible/weaker)."""
    return _LEVEL_RANK[normalize_level(level)]


def is_weakened(level: object, baseline: object) -> bool:
    """True if `level` is more permissive than `baseline`."""
    return level_rank(level) > level_rank(baseline)
