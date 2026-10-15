"""Read first-result progress without exposing task content to settings."""
from pathlib import Path
import sqlite3


def onboarding_progress(root: Path, *, unlocked: bool) -> dict:
    if not unlocked:
        return {"state": "locked", "successful_profiles": []}
    database = root / "database" / "factory.db"
    if not database.exists():
        return {"state": "ready", "successful_profiles": []}
    if database.is_symlink():
        return {"state": "unavailable", "successful_profiles": []}
    try:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2) as connection:
            rows = connection.execute(
                "SELECT DISTINCT profile FROM tasks WHERE status = ? "
                "AND completed_at IS NOT NULL AND length(trim(coalesce(output_text, ''))) > 0 "
                "AND length(profile) BETWEEN 1 AND 160 ORDER BY profile LIMIT 256", ("ok",)
            ).fetchall()
        return {"state": "ready", "successful_profiles": [row[0] for row in rows if isinstance(row[0], str)]}
    except sqlite3.Error:
        return {"state": "unavailable", "successful_profiles": []}
