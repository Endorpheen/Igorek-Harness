from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from .runtime import get_db_path
from .schema import initialize_database, open_db


DB_PATH = initialize_database(get_db_path())


def log_autonomous_action(
    *,
    source: str,
    action_type: str,
    status: str,
    profile: str | None = None,
    entity_kind: str | None = None,
    entity_id: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    created_at: int,
) -> str:
    action_id = str(uuid.uuid4())
    with open_db(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO autonomous_actions (
                action_id, source, action_type, status, profile, entity_kind, entity_id,
                summary, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                str(source or "").strip(),
                str(action_type or "").strip(),
                str(status or "").strip(),
                str(profile or "").strip() or None,
                str(entity_kind or "").strip() or None,
                str(entity_id or "").strip() or None,
                str(summary or "").strip() or None,
                json.dumps(details or {}, ensure_ascii=False),
                int(created_at),
            ),
        )
        conn.commit()
    return action_id


def list_autonomous_actions(
    *,
    limit: int = 20,
    source: str | None = None,
    action_type: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if source:
        clauses.append("source = ?")
        params.append(source)
    if action_type:
        clauses.append("action_type = ?")
        params.append(action_type)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT action_id, source, action_type, status, profile, entity_kind, entity_id,
               summary, details, created_at
        FROM autonomous_actions
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ?
    """
    params.append(max(1, int(limit)))

    with open_db(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(item.get("details") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
        items.append(item)
    return {
        "status": "ok",
        "count": len(items),
        "items": items,
    }


def summarize_autonomous_actions(*, hours: int = 24, sent_limit: int = 20) -> dict[str, Any]:
    window_hours = max(1, int(hours))
    since_ts = int(time.time()) - window_hours * 3600
    with open_db(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        aggregate_rows = conn.execute(
            """
            SELECT source, action_type, status, COUNT(*) AS count
            FROM autonomous_actions
            WHERE created_at >= ?
            GROUP BY source, action_type, status
            ORDER BY source, action_type, status
            """,
            (since_ts,),
        ).fetchall()
        sent_rows = conn.execute(
            """
            SELECT created_at, entity_id, details
            FROM autonomous_actions
            WHERE created_at >= ?
              AND source = 'autonomous_comment'
              AND action_type = 'comment_sent'
              AND status = 'ok'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (since_ts, max(1, int(sent_limit))),
        ).fetchall()
        reason_rows = conn.execute(
            """
            SELECT status, json_extract(details, '$.reason') AS reason, COUNT(*) AS count
            FROM autonomous_actions
            WHERE created_at >= ?
              AND source = 'autonomous_comment'
              AND action_type = 'comment_tick'
            GROUP BY status, reason
            ORDER BY count DESC
            """,
            (since_ts,),
        ).fetchall()

    aggregates = []
    for row in aggregate_rows:
        aggregates.append(
            {
                "source": row["source"],
                "action_type": row["action_type"],
                "status": row["status"],
                "count": int(row["count"] or 0),
            }
        )

    sent_items = []
    for row in sent_rows:
        details_raw = row["details"] or "{}"
        try:
            details = json.loads(details_raw)
        except json.JSONDecodeError:
            details = {}
        sent_items.append(
            {
                "created_at": int(row["created_at"] or 0),
                "entity_id": row["entity_id"],
                "candidate_id": details.get("candidate_id"),
                "remote_comment_id": details.get("remote_comment_id"),
                "message": details.get("message"),
            }
        )

    tick_breakdown = []
    for row in reason_rows:
        tick_breakdown.append(
            {
                "status": row["status"],
                "reason": row["reason"],
                "count": int(row["count"] or 0),
            }
        )

    return {
        "status": "ok",
        "window_hours": window_hours,
        "since_ts": since_ts,
        "aggregates": aggregates,
        "sent_comments": sent_items,
        "autonomous_tick_breakdown": tick_breakdown,
    }
