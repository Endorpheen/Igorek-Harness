from __future__ import annotations

import time

from .bash_scope_guard import probe_bwrap_capability, resolve_sandbox_mode
from .runtime import get_db_path
from .schema import initialize_database, open_db


BASH_SANDBOX_ENABLED_KEY = "bash_sandbox_enabled"


def sandbox_enabled() -> bool:
    initialize_database(get_db_path())
    with open_db(get_db_path()) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (BASH_SANDBOX_ENABLED_KEY,),
        ).fetchone()
    if row is None:
        return True
    return str(row[0] or "").strip().lower() not in {"0", "false", "no", "off"}


def set_sandbox_enabled(enabled: bool) -> bool:
    initialize_database(get_db_path())
    value = "true" if enabled else "false"
    with open_db(get_db_path()) as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (BASH_SANDBOX_ENABLED_KEY, value, int(time.time())),
        )
    return bool(enabled)


def sandbox_status() -> dict[str, object]:
    """Consistent sandbox status: `env` + configuration + actual `bwrap` readiness.

    Priority order: ``env off`` > disabled configuration > ``observe`` > namespace readiness.
    ``enabled`` — user intention, ``active`` — actual isolation execution.
    """
    mode = resolve_sandbox_mode()
    probe_result = probe_bwrap_capability()
    available = probe_result == "ok"
    if mode == "off":
        return {
            "enabled": False,
            "active": False,
            "available": available,
            "reason": "env_disabled",
        }
    if not sandbox_enabled():
        return {
            "enabled": False,
            "active": False,
            "available": available,
            "reason": "user_disabled",
        }
    if mode == "observe":
        return {
            "enabled": True,
            "active": False,
            "available": available,
            "reason": "observe",
        }
    if not available:
        return {
            "enabled": True,
            "active": False,
            "available": False,
            "reason": probe_result,
        }
    return {"enabled": True, "active": True, "available": True, "reason": ""}
