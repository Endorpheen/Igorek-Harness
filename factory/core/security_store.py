from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from .runtime import get_data_root, get_db_path
except ImportError:  # pragma: no cover - direct script fallback
    from runtime import get_data_root, get_db_path

try:
    from .schema import open_db
except ImportError:  # pragma: no cover - direct script fallback
    from schema import open_db

try:
    from .security_levels import normalize_level
except ImportError:  # pragma: no cover - direct script fallback
    from security_levels import normalize_level


FACTORY_DIR = Path(get_data_root())
DB_PATH = get_db_path()
DEFAULT_POLICY_PATH = FACTORY_DIR / "config" / "security_policy.default.json"


def load_default_policy_document() -> dict[str, Any]:
    if DEFAULT_POLICY_PATH.exists():
        return json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))

    return {
        "name": "default",
        "version": 1,
        "mode": "disabled",
        "static": {
            "workspace_only": True,
            "allowed_tools": ["claude", "codex"],
        },
        "dynamic": {
            "network_allowlist": [],
            "cloud_model_routes": [],
            "auto_approve_contexts": {
                "autonomous_boltbook": {
                    "exec_binary": {
                        "targets": ["codex"],
                        "command_prefix": "codex",
                        "required_flags": ["--dangerously-bypass-approvals-and-sandbox"],
                    },
                    "cloud_model_route": {
                        "targets": ["openai-codex:gpt-5.4"],
                    },
                }
            },
            "approval_required_for": [
                "network_host",
                "write_outside_workspace",
                "exec_binary",
                "secret_use",
                "cloud_model_route",
            ],
        },
    }


def _merge_policy_defaults(current: Any, default: Any) -> Any:
    if isinstance(current, dict) and isinstance(default, dict):
        merged = dict(current)
        for key, default_value in default.items():
            if key in merged:
                merged[key] = _merge_policy_defaults(merged[key], default_value)
            else:
                merged[key] = default_value
        return merged

    if current in (None, "", [], {}):
        return default

    return current


def ensure_security_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS security_policies (
            policy_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            policy_json TEXT NOT NULL,
            is_active INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS security_approvals (
            approval_id TEXT PRIMARY KEY,
            task_id TEXT,
            session_id TEXT,
            approval_type TEXT NOT NULL,
            target TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            requested_by TEXT,
            resolved_by TEXT,
            resolution_note TEXT,
            created_at INTEGER NOT NULL,
            resolved_at INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS security_events (
            event_id TEXT PRIMARY KEY,
            task_id TEXT,
            session_id TEXT,
            event_type TEXT NOT NULL,
            decision TEXT,
            target TEXT,
            policy_name TEXT,
            details_json TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_security_events_task_created
        ON security_events(task_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_security_approvals_status_created
        ON security_approvals(status, created_at)
        """
    )
    conn.commit()


class SecurityStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def ensure_bootstrap(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with open_db(self.db_path) as conn:
            ensure_security_schema(conn)
            self._ensure_default_policy(conn)

    def _ensure_default_policy(self, conn: sqlite3.Connection) -> None:
        cursor = conn.cursor()
        now = int(time.time())
        payload = load_default_policy_document()
        cursor.execute(
            """
            SELECT policy_id, policy_json, is_active
            FROM security_policies
            WHERE name = ?
            LIMIT 1
            """,
            ("default",),
        )
        row = cursor.fetchone()
        if row:
            try:
                current_policy = json.loads(row[1]) if row[1] else {}
            except json.JSONDecodeError:
                current_policy = {}

            merged_policy = _merge_policy_defaults(current_policy, payload)
            if merged_policy != current_policy:
                conn.execute(
                    """
                    UPDATE security_policies
                    SET policy_json = ?, updated_at = ?
                    WHERE policy_id = ?
                    """,
                    (
                        json.dumps(merged_policy, ensure_ascii=False),
                        now,
                        row[0],
                    ),
                )
                conn.commit()
            return

        conn.execute(
            """
            INSERT INTO security_policies (
                policy_id, name, policy_json, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                "default",
                json.dumps(payload, ensure_ascii=False),
                1,
                now,
                now,
            ),
        )
        conn.commit()

    def list_policies(self) -> list[dict[str, Any]]:
        with open_db(self.db_path) as conn:
            ensure_security_schema(conn)
            self._ensure_default_policy(conn)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT policy_id, name, policy_json, is_active, created_at, updated_at
                FROM security_policies
                ORDER BY updated_at DESC, created_at DESC
                """
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["policy"] = json.loads(item.pop("policy_json"))
            except json.JSONDecodeError:
                item["policy"] = {}
            result.append(item)
        return result

    def get_active_policy(self) -> dict[str, Any]:
        with open_db(self.db_path) as conn:
            ensure_security_schema(conn)
            self._ensure_default_policy(conn)
            row = conn.execute(
                """
                SELECT name, policy_json
                FROM security_policies
                WHERE is_active = 1
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()

        if not row:
            return {"name": "default", "policy": load_default_policy_document()}

        try:
            policy = json.loads(row[1])
        except json.JSONDecodeError:
            policy = {}
        return {"name": row[0], "policy": policy}

    def log_event(
        self,
        event_type: str,
        task_id: str | None = None,
        session_id: str | None = None,
        decision: str | None = None,
        target: str | None = None,
        policy_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        now = int(time.time())
        with open_db(self.db_path) as conn:
            ensure_security_schema(conn)
            conn.execute(
                """
                INSERT INTO security_events (
                    event_id, task_id, session_id, event_type, decision,
                    target, policy_name, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    task_id,
                    session_id,
                    event_type,
                    decision,
                    target,
                    policy_name,
                    json.dumps(details or {}, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
        return event_id

    def log_level_change(
        self,
        level: str,
        *,
        previous: str | None,
        actor: str,
        session_id: str | None = None,
        expires_at: int | None = None,
    ) -> str:
        """Records audit of access level change.

        The level is only audited and **not stored**; the sole truth source is `security_settings` (settings table). `policy_json` is not modified.
        """
        normalized = normalize_level(level)
        return self.log_event(
            event_type="security_level_changed",
            task_id=None,
            session_id=session_id,
            decision=None,
            target=normalized,
            policy_name=None,
            details={
                "previous": previous,
                "level": normalized,
                "actor": actor,
                "expires_at": expires_at,
            },
        )

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with open_db(self.db_path) as conn:
            ensure_security_schema(conn)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT event_id, task_id, session_id, event_type, decision,
                       target, policy_name, details_json, created_at
                FROM security_events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
            result.append(item)
        return result

    def create_approval(
        self,
        approval_type: str,
        target: str,
        task_id: str | None = None,
        session_id: str | None = None,
        reason: str = "",
        requested_by: str = "system",
    ) -> str:
        approval_id = str(uuid.uuid4())
        now = int(time.time())
        with open_db(self.db_path) as conn:
            ensure_security_schema(conn)
            conn.execute(
                """
                INSERT INTO security_approvals (
                    approval_id, task_id, session_id, approval_type, target, status,
                    reason, requested_by, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    approval_id,
                    task_id,
                    session_id,
                    approval_type,
                    target,
                    reason,
                    requested_by,
                    now,
                ),
            )
            conn.commit()
        self.log_event(
            event_type="approval_requested",
            task_id=task_id,
            session_id=session_id,
            decision="pending",
            target=target,
            details={
                "approval_id": approval_id,
                "approval_type": approval_type,
                "reason": reason,
                "requested_by": requested_by,
            },
        )
        return approval_id

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        with open_db(self.db_path) as conn:
            ensure_security_schema(conn)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT *
                FROM security_approvals
                WHERE approval_id = ?
                LIMIT 1
                """,
                (approval_id,),
            ).fetchone()
        return dict(row) if row else None

    def resolve_approval(
        self,
        approval_id: str,
        status: str,
        resolved_by: str = "api",
        resolution_note: str = "",
    ) -> dict[str, Any]:
        normalized_status = status.strip().lower()
        if normalized_status not in {"approved", "rejected"}:
            raise ValueError("Approval status must be 'approved' or 'rejected'")

        now = int(time.time())
        with open_db(self.db_path) as conn:
            ensure_security_schema(conn)
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                """
                SELECT *
                FROM security_approvals
                WHERE approval_id = ?
                LIMIT 1
                """,
                (approval_id,),
            ).fetchone()
            if not existing:
                raise KeyError(f"Approval not found: {approval_id}")

            existing_item = dict(existing)
            if existing_item.get("status") != "pending":
                raise ValueError(f"Approval already resolved with status={existing_item.get('status')}")

            conn.execute(
                """
                UPDATE security_approvals
                SET status = ?,
                    resolved_by = ?,
                    resolution_note = ?,
                    resolved_at = ?
                WHERE approval_id = ?
                """,
                (
                    normalized_status,
                    resolved_by,
                    resolution_note,
                    now,
                    approval_id,
                ),
            )
            conn.commit()

        updated = self.get_approval(approval_id)
        if updated is None:
            raise KeyError(f"Approval not found after update: {approval_id}")

        self.log_event(
            event_type="approval_resolved",
            task_id=updated.get("task_id"),
            session_id=updated.get("session_id"),
            decision=normalized_status,
            target=updated.get("target"),
            details={
                "approval_id": approval_id,
                "approval_type": updated.get("approval_type"),
                "resolved_by": resolved_by,
                "resolution_note": resolution_note,
            },
        )
        return updated

    def find_latest_approval(
        self,
        approval_type: str,
        target: str,
        requested_by: str | None = None,
        statuses: tuple[str, ...] = ("approved", "rejected"),
    ) -> dict[str, Any] | None:
        if not statuses:
            return None

        placeholders = ", ".join("?" for _ in statuses)
        query = f"""
            SELECT *
            FROM security_approvals
            WHERE approval_type = ?
              AND target = ?
              AND status IN ({placeholders})
        """
        params: list[Any] = [approval_type, target, *statuses]

        if requested_by:
            query += " AND requested_by = ?"
            params.append(requested_by)

        query += """
            ORDER BY COALESCE(resolved_at, created_at) DESC, created_at DESC
            LIMIT 1
        """

        with open_db(self.db_path) as conn:
            ensure_security_schema(conn)
            conn.row_factory = sqlite3.Row
            row = conn.execute(query, tuple(params)).fetchone()

        return dict(row) if row else None

    def list_approvals(
        self,
        status: str | None = None,
        limit: int = 100,
        task_id: str | None = None,
        session_id: str | None = None,
        requested_by: str | None = None,
    ) -> list[dict[str, Any]]:
        with open_db(self.db_path) as conn:
            ensure_security_schema(conn)
            conn.row_factory = sqlite3.Row
            clauses: list[str] = []
            params: list[Any] = []
            if status:
                clauses.append("status = ?")
                params.append(status)
            if task_id:
                clauses.append("task_id = ?")
                params.append(task_id)
            if session_id:
                clauses.append("session_id = ?")
                params.append(session_id)
            if requested_by:
                clauses.append("requested_by = ?")
                params.append(requested_by)

            query = "SELECT * FROM security_approvals"
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]
