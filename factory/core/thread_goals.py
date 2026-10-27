"""Persistent per-session objectives and bounded usage accounting."""
from __future__ import annotations

import time
import uuid
import sqlite3

from .runtime import get_db_path
from .schema import initialize_database, open_db


class GoalConflict(ValueError):
    """A concurrent or unfinished goal prevents the requested change."""


_COLUMNS = (
    "session_id", "surface", "goal_id", "objective", "revision", "status", "token_budget", "tokens_used",
    "time_used_seconds", "empty_turns", "created_at", "updated_at",
)
_STATUSES = {"active", "paused", "blocked", "usage_limited", "budget_limited", "complete"}


def _database() -> str:
    for _ in range(19):
        try:
            return initialize_database(get_db_path())
        except sqlite3.OperationalError as exc:
            if not any(word in str(exc).lower() for word in ("locked", "busy")):
                raise
            time.sleep(0.025)
    return initialize_database(get_db_path())


def _read(conn, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT " + ", ".join(_COLUMNS) + " FROM thread_goals WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


def get_goal(session_id: str) -> dict | None:
    session = str(session_id or "").strip()
    if not session:
        return None
    with open_db(_database()) as conn:
        return _read(conn, session)


def create_goal(session_id: str, objective: str, *, token_budget: int | None = None,
                surface: str = "mobile_bridge") -> dict:
    session = str(session_id or "").strip()
    text = str(objective or "").strip()
    if not session or not text or len(text) > 4000 or surface not in {"mobile_bridge", "cli_chat_rich"}:
        raise ValueError("session and objective of at most 4000 characters are required")
    if token_budget is not None and (type(token_budget) is not int or token_budget <= 0):
        raise ValueError("token budget must be a positive integer")
    now = int(time.time())
    with open_db(_database()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = _read(conn, session)
        if previous and previous["status"] != "complete":
            raise GoalConflict("an unfinished goal already exists")
        if previous:
            conn.execute("DELETE FROM thread_goal_usage WHERE goal_id = ?", (previous["goal_id"],))
            conn.execute("DELETE FROM thread_goals WHERE session_id = ?", (session,))
        conn.execute(
            "INSERT INTO thread_goals (session_id, surface, goal_id, objective, status, token_budget, "
            "tokens_used, time_used_seconds, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, 0, 0, ?, ?)",
            (session, surface, uuid.uuid4().hex, text, token_budget, now, now),
        )
        return _read(conn, session)


def list_active_goals(surface: str) -> list[dict]:
    with open_db(_database()) as conn:
        rows = conn.execute(
            "SELECT " + ", ".join(_COLUMNS) + " FROM thread_goals WHERE status = 'active' AND surface = ?",
            (surface,),
        ).fetchall()
        return [dict(zip(_COLUMNS, row)) for row in rows]


def edit_goal(session_id: str, objective: str, *, expected_goal_id: str | None = None) -> dict:
    session = str(session_id or "").strip()
    text = str(objective or "").strip()
    if not text or len(text) > 4000:
        raise ValueError("objective must contain at most 4000 characters")
    with open_db(_database()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _read(conn, session)
        if not current:
            raise ValueError("no goal exists for this session")
        if expected_goal_id is not None and current["goal_id"] != expected_goal_id:
            raise GoalConflict("the goal has changed")
        status = "active" if current["status"] == "complete" else current["status"]
        conn.execute(
            "UPDATE thread_goals SET objective = ?, status = ?, empty_turns = 0, revision = revision + 1, updated_at = ? WHERE session_id = ?",
            (text, status, int(time.time()), session),
        )
        return _read(conn, session)


def update_goal(session_id: str, status: str, *, expected_goal_id: str | None = None,
                expected_revision: int | None = None,
                by_user: bool = False) -> dict:
    session = str(session_id or "").strip()
    if status not in _STATUSES:
        raise ValueError("unknown goal status")
    if status in {"active", "usage_limited", "budget_limited"} and not by_user:
        raise ValueError("this status is controlled by the user or runtime")
    with open_db(_database()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _read(conn, session)
        if not current:
            raise ValueError("no goal exists for this session")
        if expected_goal_id is not None and current["goal_id"] != expected_goal_id:
            raise GoalConflict("the goal has changed")
        if expected_revision is not None and current["revision"] != expected_revision:
            raise GoalConflict("the goal has changed")
        if status == "active" and current["status"] not in {"paused", "blocked", "usage_limited"}:
            raise GoalConflict("this goal cannot be resumed")
        if status != "active" and current["status"] != "active":
            raise GoalConflict("the goal is not active")
        conn.execute("UPDATE thread_goals SET status = ?, empty_turns = CASE WHEN ? THEN 0 ELSE empty_turns END, revision = revision + ?, updated_at = ? WHERE session_id = ?",
                     (status, by_user, 1 if by_user else 0, int(time.time()), session))
        return _read(conn, session)


def account_usage(session_id: str, goal_id: str, turn_id: str, *, tokens: int, seconds: int,
                  empty: bool = False, failed: bool = False, revision: int | None = None) -> dict:
    session = str(session_id or "").strip()
    if not turn_id or any(type(value) is not int or value < 0 for value in (tokens, seconds)):
        raise ValueError("turn and non-negative integer usage are required")
    with open_db(_database()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _read(conn, session)
        if not current or current["goal_id"] != goal_id:
            raise GoalConflict("the goal has changed")
        inserted = conn.execute(
            "INSERT OR IGNORE INTO thread_goal_usage (goal_id, turn_id) VALUES (?, ?)",
            (goal_id, turn_id),
        ).rowcount
        if inserted:
            total = current["tokens_used"] + tokens
            status = current["status"]
            empty_turns = current["empty_turns"]
            if status == "active" and (revision is None or revision == current["revision"]):
                empty_turns = empty_turns + 1 if empty else 0
                if failed or empty_turns >= 3:
                    status = "blocked"
                elif current["token_budget"] is not None and total >= current["token_budget"]:
                    status = "budget_limited"
            conn.execute(
                "UPDATE thread_goals SET tokens_used = ?, time_used_seconds = ?, empty_turns = ?, status = ?, updated_at = ? "
                "WHERE session_id = ?",
                (total, current["time_used_seconds"] + seconds, empty_turns, status, int(time.time()), session),
            )
        return _read(conn, session)


def clear_goal(session_id: str, *, expected_goal_id: str | None = None) -> bool:
    session = str(session_id or "").strip()
    with open_db(_database()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _read(conn, session)
        if not current:
            return False
        if expected_goal_id is not None and current["goal_id"] != expected_goal_id:
            raise GoalConflict("the goal has changed")
        conn.execute("DELETE FROM thread_goal_usage WHERE goal_id = ?", (current["goal_id"],))
        conn.execute("DELETE FROM thread_goals WHERE session_id = ?", (session,))
        return True
