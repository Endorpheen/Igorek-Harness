from __future__ import annotations

import time

from .runtime import get_db_path
from .schema import initialize_database, open_db


MOBILE_APPROVALS_ENABLED_KEY = "mobile_approvals_enabled"


def mobile_approvals_enabled() -> bool:
    initialize_database(get_db_path())
    with open_db(get_db_path()) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (MOBILE_APPROVALS_ENABLED_KEY,),
        ).fetchone()
    if row is None:
        return True
    return str(row[0] or "").strip().lower() not in {"0", "false", "no", "off"}


def set_mobile_approvals_enabled(enabled: bool) -> bool:
    initialize_database(get_db_path())
    value = "true" if enabled else "false"
    with open_db(get_db_path()) as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (MOBILE_APPROVALS_ENABLED_KEY, value, int(time.time())),
        )
    return bool(enabled)
