"""Shared access-level store backed by the `settings` table.

Store JSON ``{"level": <str>, "expires_at": <int|null>}`` using epoch seconds:
  * ``security_level``: global level;
  * ``security_level:session:<id>``: session override.

Weaker levels (``trusted``/``full``) require a future ``expires_at``. Writes
validate a bounded TTL; reads validate expiration. Stronger levels
(``strict``/``standard``) may have no expiration.

Precedence: session override > global > baseline (when_no_level).

Residual limit: reads require a future ``expires_at`` but do not reapply the
3600/86400-second cap to raw database rows. A direct database write can therefore
supply an expiration farther in the future than the API accepts.

An access level is a user control, not a security boundary against an agent
with equivalent Bash access. Direct changes to `settings` persist across tasks:
resolve-once fixes the current task's level, but the next task reads the changed
row. Process isolation (bwrap) or a separate uid provides that boundary; this
module does not.

Do not create tables. Use existing `settings`, not `security_policies`, and do
not obtain the level from `task_metadata`.
"""

from __future__ import annotations

# TODO(bwrap-boundary): Level is a user-controlled setting, not a security boundary
# against a Bash agent; direct tampering with the `settings` table
# persists across tasks and is removed only by bwrap/isolation via UID.

import json
import time

from .runtime import get_db_path
from .schema import initialize_database, open_db
from .security_levels import (
    DEFAULT_LEVEL,
    LEVELS,
    level_rank,
    normalize_level,
)

GLOBAL_KEY = "security_level"
SESSION_KEY_PREFIX = "security_level:session:"

# Security-reducing levels: must have a future `expires_at`.
WEAKENING: frozenset[str] = frozenset({"trusted", "full"})

DEFAULT_TTL_S: int = 3600

# Maximum allowed TTL for security-reducing levels.
MAX_TTL_S: dict[str, int] = {"trusted": 86400, "full": 3600}

# Profiles setting the baseline when no explicit level is set.
# `full-dangerous` intentionally does not provide `full`: profile appears as `standard`.
_PROFILE_BASELINE: dict[str, str] = {
    "user-read": "user-read",
    "full-dangerous": DEFAULT_LEVEL,
}


def _session_key(session_id: object) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}"


def _baseline_level(when_no_level: object) -> str:
    """Baseline when no explicit level is provided.

    None/unknown -> DEFAULT_LEVEL; the `user-read` profile is preserved;
    the `full-dangerous` profile -> DEFAULT_LEVEL (profile does not provide `full`).
    """
    if when_no_level is None:
        return DEFAULT_LEVEL
    raw = str(when_no_level).strip().lower()
    if raw in LEVELS:
        return raw
    return _PROFILE_BASELINE.get(raw, DEFAULT_LEVEL)


def _rank_or_default(level: str) -> int:
    """Level rank; for non-levels (`user-read`) — same as DEFAULT_LEVEL."""
    try:
        return level_rank(level)
    except ValueError:
        return level_rank(DEFAULT_LEVEL)


def _parse_value(raw: object) -> tuple[str, int | None] | None:
    """Structural parsing of the value from settings.

    Returns (level, expires_at) or None if JSON is corrupted, not an object
    or the level is unknown. Non-numeric expires_at is treated as None.
    """
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        level = normalize_level(payload.get("level"))
    except ValueError:
        return None
    expires_at = payload.get("expires_at")
    if isinstance(expires_at, bool) or not isinstance(expires_at, int):
        expires_at = None
    return level, expires_at


def _is_valid_record(level: str, expires_at: int | None, now: int) -> bool:
    """Is the record valid at the moment `now`?

    A weakening level without a future `expires_at` is invalid. Any level with an expired `expires_at` is invalid. The cap is not applied here (see module).
    """
    if level in WEAKENING:
        return expires_at is not None and expires_at > now
    if expires_at is not None and expires_at <= now:
        return False
    return True


def _load_valid_record(key: str) -> dict[str, object] | None:
    initialize_database(get_db_path())
    with open_db(get_db_path()) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
    if row is None:
        return None
    parsed = _parse_value(row[0])
    if parsed is None:
        return None
    level, expires_at = parsed
    if not _is_valid_record(level, expires_at, int(time.time())):
        return None
    return {"level": level, "expires_at": expires_at}


def _compute_expires_at(level: str, ttl_s: object) -> int | None:
    """Computes `expires_at`, validating TTL.

    For weakening TTL, it is mandatory (None → DEFAULT_TTL_S) and must be within (0, MAX_TTL_S[level]]. For strengthening TTL, it is optional.
    """
    if level in WEAKENING:
        effective_ttl = DEFAULT_TTL_S if ttl_s is None else ttl_s
        cap = MAX_TTL_S[level]
    else:
        if ttl_s is None:
            return None
        effective_ttl = ttl_s
        cap = None

    if isinstance(effective_ttl, bool) or not isinstance(effective_ttl, (int, float)):
        raise ValueError(f"ttl_s must be a positive number, got {effective_ttl!r}")
    if effective_ttl <= 0:
        raise ValueError(f"ttl_s must be > 0, got {effective_ttl!r}")
    if cap is not None and effective_ttl > cap:
        raise ValueError(
            f"ttl_s={effective_ttl!r} exceeds maximum {cap}s for level {level!r}"
        )
    return int(time.time()) + int(effective_ttl)


def set_security_level(
    level: object,
    session_id: object = None,
    ttl_s: object = None,
) -> dict[str, object]:
    """Records the level (globally or per session).

    Returns the saved record ``{"level", "expires_at"}``.
    """
    normalized = normalize_level(level)
    expires_at = _compute_expires_at(normalized, ttl_s)
    record: dict[str, object] = {"level": normalized, "expires_at": expires_at}
    key = _session_key(session_id) if session_id else GLOBAL_KEY

    initialize_database(get_db_path())
    with open_db(get_db_path()) as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(record), int(time.time())),
        )
    return record


def get_global_level() -> dict[str, object] | None:
    """Valid global level record or None."""
    return _load_valid_record(GLOBAL_KEY)


def get_session_level(session_id: object) -> dict[str, object] | None:
    """Valid session-level override level or None."""
    if not session_id:
        return None
    return _load_valid_record(_session_key(session_id))


def clear_session_level(session_id: object) -> None:
    """Removes session-level override level."""
    if not session_id:
        return
    initialize_database(get_db_path())
    with open_db(get_db_path()) as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (_session_key(session_id),))


def resolve_security_level(
    *,
    session_id: object = None,
    when_no_level: object = None,
) -> dict[str, object]:
    """Final level prioritized as session > global > baseline.

    A level without a future `expires_at` is considered invalid and is skipped:
    expired session -> global; expired global -> baseline; invalid/unknown -> baseline.
    `when_no_level` sets the baseline:
    None -> `standard`; `user-read` -> `user-read`; `full-dangerous` -> `standard` (profile does not allow `full`).
    """
    baseline = _baseline_level(when_no_level)

    if session_id:
        session = _load_valid_record(_session_key(session_id))
        if session is not None:
            return _resolve_result(session, "session", baseline)

    global_record = _load_valid_record(GLOBAL_KEY)
    if global_record is not None:
        return _resolve_result(global_record, "global", baseline)

    return {
        "level": baseline,
        "expires_at": None,
        "source": "baseline",
        "is_weakened": False,
        "baseline": baseline,
    }


def _resolve_result(
    record: dict[str, object],
    source: str,
    baseline: str,
) -> dict[str, object]:
    level = str(record["level"])
    return {
        "level": level,
        "expires_at": record["expires_at"],
        "source": source,
        "is_weakened": _rank_or_default(level) > _rank_or_default(baseline),
        "baseline": baseline,
    }
