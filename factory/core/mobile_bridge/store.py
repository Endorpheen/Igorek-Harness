"""Mobile bridge state storage.

Uses the shared factory database (`factory.db`) in its own tables without
changing the existing runtime schema.

Responsibilities:

* ``mobile_devices``: one row per paired phone. Store only the device-token hash,
  so a database leak alone does not grant access. A device can be softly revoked
  and remain marked in the list, or permanently deleted to remove old entries.
* ``mobile_pairing_codes``: short-lived, single-use codes. The owner displays a
  QR/text code to enroll exactly one new device.
* ``mobile_events``: a durable journal with monotonic ``seq``. A reconnecting
  client uses ``after_seq`` to retrieve missed events; state does not depend on
  the lifetime of an open connection.

Timestamps use Unix seconds, consistently with the rest of the factory schema.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from typing import Any

from ..runtime import get_db_path
from ..schema import initialize_database, open_db

# Special 'salt' for hashing device tokens: prevents reuse of the same digest in another table and explicitly marks the hash's purpose.
_DEVICE_TOKEN_PEPPER = b"igorek-mobile-device-v1"
_PAIRING_CODE_PEPPER = b"igorek-mobile-pairing-v1"

_DEFAULT_PAIRING_TTL_S = 300
_MAX_EVENT_LIMIT = 500

MOBILE_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS mobile_devices (
        device_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        platform TEXT NOT NULL DEFAULT '',
        token_hash TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL,
        last_seen_at INTEGER,
        revoked_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mobile_pairing_codes (
        code_hash TEXT PRIMARY KEY,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        used_at INTEGER,
        created_by TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mobile_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        task_id TEXT,
        ts INTEGER NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        src_line INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mobile_events_session_seq ON mobile_events(session_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_mobile_events_task_seq ON mobile_events(task_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_mobile_events_kind_seq ON mobile_events(kind, seq)",
    # Idempotent restart: one trace line results in at most one event.
    # Events not from the trace have NULL values, and in SQLite, NULL does not conflict in UNIQUE constraints.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_events_task_line ON mobile_events(task_id, src_line)",
    """
    CREATE TABLE IF NOT EXISTS mobile_requests (
        request_key TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        session_id TEXT,
        task_id TEXT,
        created_at INTEGER NOT NULL
    )
    """,
)


def ensure_mobile_schema(conn: sqlite3.Connection) -> None:
    """Creates bridge tables if they do not exist. Idempotent."""
    for statement in MOBILE_SCHEMA_STATEMENTS:
        conn.execute(statement)
    from ..schema import _ensure_columns
    _ensure_columns(conn, 'mobile_requests', [('receipt_json', "TEXT NOT NULL DEFAULT '{}'" )])


def _digest(pepper: bytes, value: str) -> str:
    return hashlib.sha256(pepper + value.encode("utf-8")).hexdigest()


def _hash_device_token(token: str) -> str:
    return _digest(_DEVICE_TOKEN_PEPPER, token)


def _hash_pairing_code(code: str) -> str:
    return _digest(_PAIRING_CODE_PEPPER, code)


def _normalize_pairing_code(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def _format_pairing_code(code: str) -> str:
    parts = [code[index : index + 4] for index in range(0, len(code), 4)]
    return "-".join(parts)


def _device_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "device_id": row[0],
        "name": row[1],
        "platform": row[2],
        "created_at": row[3],
        "last_seen_at": row[4],
        "revoked_at": row[5],
    }


class PairingAuthorityError(ValueError):
    """The authenticated issuer no longer has permission to pair devices."""


def _check_pairing_issuer(conn, created_by: str) -> None:
    if created_by == "http":
        raise PairingAuthorityError("Create a fresh pairing code from an authorized device")
    if created_by.startswith("device:"):
        row = conn.execute(
            "SELECT 1 FROM mobile_devices WHERE device_id=? AND revoked_at IS NULL",
            (created_by[len("device:"):],),
        ).fetchone()
        if row is None:
            raise PairingAuthorityError("Pairing issuer is missing or revoked")


class MobileStore:
    """Device pairing, their tokens, and event logging."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or get_db_path()
        initialize_database(self.db_path)
        with open_db(self.db_path) as conn:
            ensure_mobile_schema(conn)


    def create_pairing_code(
        self,
        *,
        ttl_s: int = _DEFAULT_PAIRING_TTL_S,
        replace_previous: bool = False,
        created_by: str = "cli",
        now: int | None = None,
    ) -> dict[str, Any]:
        """Issue a single-use pairing code.

        Returns `{"code": <display to owner>, "expires_at": <unix>}`.
        Only the code hash is stored in the database.
        """
        current = int(time.time() if now is None else now)
        ttl = max(30, int(ttl_s))
        raw_code = secrets.token_hex(6).upper()
        expires_at = current + ttl
        with open_db(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            _check_pairing_issuer(conn, str(created_by or ""))
            if replace_previous:
                conn.execute("UPDATE mobile_pairing_codes SET used_at=? WHERE created_by=? AND used_at IS NULL",(current,str(created_by or "")))
            conn.execute(
                "INSERT INTO mobile_pairing_codes (code_hash, created_at, expires_at, used_at, created_by) "
                "VALUES (?, ?, ?, NULL, ?)",
                (_hash_pairing_code(raw_code), current, expires_at, str(created_by or "")),
            )
        return {"code": _format_pairing_code(raw_code), "expires_at": expires_at}

    def complete_pairing(
        self,
        *,
        code: str,
        name: str,
        platform: str = "android",
        now: int | None = None,
    ) -> dict[str, Any]:
        """Exchanges the pairing code for a device token.

        The code is one-time-use and expires. Repeating the same code is rejected, so intercepted old codes are invalid.
        """
        current = int(time.time() if now is None else now)
        normalized = _normalize_pairing_code(code)
        if not normalized:
            raise ValueError("pairing code is required")
        code_hash = _hash_pairing_code(normalized)

        with open_db(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT expires_at, used_at, created_by FROM mobile_pairing_codes WHERE code_hash = ?",
                (code_hash,),
            ).fetchone()
            if row is None:
                raise ValueError("invalid pairing code")
            expires_at, used_at = int(row[0]), row[1]
            if used_at is not None:
                raise ValueError("pairing code already used")
            if current > expires_at:
                raise ValueError("pairing code expired")
            _check_pairing_issuer(conn, str(row[2] or ""))

            device_id = str(uuid.uuid4())
            token = secrets.token_urlsafe(32)
            device_name = str(name or "").strip() or "device"
            conn.execute(
                "INSERT INTO mobile_devices (device_id, name, platform, token_hash, created_at, last_seen_at, revoked_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (
                    device_id,
                    device_name,
                    str(platform or "").strip(),
                    _hash_device_token(token),
                    current,
                ),
            )
            conn.execute(
                "UPDATE mobile_pairing_codes SET used_at = ? WHERE code_hash = ?",
                (current, code_hash),
            )

        return {"device_id": device_id, "token": token, "name": device_name}


    def authenticate(self, token: str, *, now: int | None = None) -> dict[str, Any] | None:
        """Checks the device token and updates the activity timestamp.

        Returns the device description or `None` if the token is unknown or the device has been revoked.
        """
        candidate = str(token or "")
        if not candidate:
            return None
        current = int(time.time() if now is None else now)
        digest = _hash_device_token(candidate)
        with open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT device_id, name, platform, created_at, last_seen_at, revoked_at "
                "FROM mobile_devices WHERE token_hash = ?",
                (digest,),
            ).fetchone()
            if row is None:
                return None
            stored_hash = digest
            device = _device_row_to_dict(row)
            if device["revoked_at"] is not None:
                return None
            # Additional time-based check: hash comparisons already occurred via index; here we fix a constant time for clarity.
            if not hmac.compare_digest(stored_hash, _hash_device_token(candidate)):
                return None
            conn.execute(
                "UPDATE mobile_devices SET last_seen_at = ? WHERE device_id = ?",
                (current, device["device_id"]),
            )
            device["last_seen_at"] = current
        return device

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        with open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT device_id, name, platform, created_at, last_seen_at, revoked_at "
                "FROM mobile_devices WHERE device_id = ?",
                (str(device_id or ""),),
            ).fetchone()
        return _device_row_to_dict(row) if row is not None else None

    def list_devices(self, *, include_revoked: bool = False) -> list[dict[str, Any]]:
        query = (
            "SELECT device_id, name, platform, created_at, last_seen_at, revoked_at "
            "FROM mobile_devices"
        )
        if not include_revoked:
            query += " WHERE revoked_at IS NULL"
        query += " ORDER BY created_at DESC"
        with open_db(self.db_path) as conn:
            rows = conn.execute(query).fetchall()
        return [_device_row_to_dict(row) for row in rows]

    def revoke_device(self, device_id: str, *, now: int | None = None) -> bool:
        """Revocs device access. Idempotent."""
        current = int(time.time() if now is None else now)
        with open_db(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE mobile_devices SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
                (current, str(device_id or "")),
            )
            conn.execute(
                "UPDATE mobile_pairing_codes SET used_at=? WHERE created_by=? AND used_at IS NULL",
                (current, "device:" + str(device_id or "")),
            )
            return cursor.rowcount > 0

    def delete_device(self, device_id: str) -> bool:
        """Deletes the entire device entry (hard deletion from the list).

        Unlike ``revoke_device``, revocation keeps the record in the list with a "access revoked" marker, while deletion removes it entirely to prevent the list of connected devices from cluttering with old connections.
        Returns ``False`` if the device with this ID does not exist.
        """
        with open_db(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM mobile_devices WHERE device_id = ?",
                (str(device_id or ""),),
            )
            return cursor.rowcount > 0

    def purge_revoked_devices(self) -> int:
        """Deletes all devices with revoked access at once.

        Returns the number of deleted entries (0 if none were revoked).
        """
        with open_db(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM mobile_devices WHERE revoked_at IS NOT NULL")
            return int(cursor.rowcount or 0)


    def append_event(
        self,
        *,
        kind: str,
        payload: dict[str, Any] | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        ts: int | None = None,
        src_line: int | None = None,
    ) -> int | None:
        """Adds an event to the log.

        Returns the ``seq`` of the new event or ``None`` if the event was already recorded. Pass ``src_line`` from the trace (then insertion is idempotent and a bridge restart won’t create duplicates).
        """
        current = int(time.time() if ts is None else ts)
        verb = "INSERT OR IGNORE" if src_line is not None else "INSERT"
        with open_db(self.db_path) as conn:
            cursor = conn.execute(
                f"{verb} INTO mobile_events (session_id, task_id, ts, kind, payload_json, src_line) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id or None,
                    task_id or None,
                    current,
                    str(kind),
                    json.dumps(payload or {}, ensure_ascii=False),
                    src_line,
                ),
            )
            new_seq = cursor.lastrowid
            inserted = int(cursor.rowcount or 0)
        if src_line is not None and not inserted:
            return None
        return int(new_seq or 0)

    def last_trace_line(self, task_id: str, *, below: int | None = None) -> int:
        """Maximum number of already processed trace line entries for the task.

        ``below`` limits trace line selection: the subagent log writes its own lines into the same table with offset numbering (see ``events._SUBAGENT_SRC_LINE_OFFSET``), otherwise the trace cursor would extend far beyond the file’s end.
        """
        query = "SELECT COALESCE(MAX(src_line), -1) FROM mobile_events WHERE task_id = ?"
        params: list[Any] = [str(task_id or "")]
        if below is not None:
            query += " AND src_line < ?"
            params.append(int(below))
        with open_db(self.db_path) as conn:
            row = conn.execute(query, params).fetchone()
        return int(row[0]) if row else -1

    def list_events(
        self,
        *,
        after_seq: int = 0,
        session_id: str | None = None,
        limit: int = 200,
        kinds: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Returns events strictly after ``after_seq``, sorted by increasing ``seq``."""
        bounded = max(1, min(int(limit), _MAX_EVENT_LIMIT))
        query = "SELECT seq, session_id, task_id, ts, kind, payload_json FROM mobile_events WHERE seq > ?"
        params: list[Any] = [max(0, int(after_seq))]
        if session_id:
            query += " AND session_id = ?"
            params.append(str(session_id))
        if kinds:
            query += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
            params.extend(kinds)
        query += " ORDER BY seq ASC LIMIT ?"
        params.append(bounded)
        with open_db(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "seq": int(row[0]),
                "session_id": row[1],
                "task_id": row[2],
                "ts": int(row[3]),
                "kind": row[4],
                "payload": json.loads(row[5]) if row[5] else {},
            }
            for row in rows
        ]

    def agent_record_event(self, session_id: str) -> dict[str, Any] | None:
        with open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT session_id, payload_json FROM mobile_events WHERE kind = 'subagent.start' "
                "AND (json_extract(payload_json, '$.agent_session_id') = ? "
                "OR ('agent-' || json_extract(payload_json, '$.subagent_id')) = ?) ORDER BY seq DESC LIMIT 1",
                (session_id, session_id),
            ).fetchone()
        if row is None:
            return None
        return {"root_session_id": row[0], **json.loads(row[1])}

    def list_subagent_events(
        self, session_id: str, *, limit: int = 2_000
    ) -> list[dict[str, Any]]:
        """All events in the lifecycle of subagents for a single session.

        This is a separate method, not a filter over ``list_events``: the list of subagents
        is collapsed across the entire session history, not just the last 500 events.
        """
        bounded = max(1, min(int(limit), 10_000))
        with open_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT seq, session_id, task_id, ts, kind, payload_json FROM mobile_events "
                "WHERE session_id = ? AND kind LIKE 'subagent.%' ORDER BY seq ASC LIMIT ?",
                (str(session_id or ""), bounded),
            ).fetchall()
        return [
            {
                "seq": int(row[0]),
                "session_id": row[1],
                "task_id": row[2],
                "ts": int(row[3]),
                "kind": row[4],
                "payload": json.loads(row[5]) if row[5] else {},
            }
            for row in rows
        ]

    def max_seq(self, *, session_id: str | None = None) -> int:
        """The maximum ``seq`` in the log (or session) is the catch-up point for events.

        Clients (including the local web UI) take this cursor before subscribing to SSE
        to avoid replaying history and losing events between requests.
        """
        query = "SELECT COALESCE(MAX(seq), 0) FROM mobile_events"
        params: list[Any] = []
        if session_id:
            query += " WHERE session_id = ?"
            params.append(str(session_id))
        with open_db(self.db_path) as conn:
            row = conn.execute(query, params).fetchone()
        return int(row[0]) if row else 0

    def event_exists(
        self,
        *,
        kind: str,
        session_id: str | None,
        task_id: str | None,
        payload_match: tuple[str, str] | None = None,
    ) -> bool:
        """Checks for the presence of an event (required for idempotent writes).

        ``payload_match`` is a tuple ``(json_path, expected_value)``; if present,
        it adds ``json_extract(payload_json, ?) = ?`` to ensure that identical
        ``kind`` values differ by payload content rather than being completely suppressed.
        """
        query = "SELECT 1 FROM mobile_events WHERE kind = ?"
        params: list[Any] = [str(kind)]
        if session_id is not None:
            query += " AND session_id = ?"
            params.append(session_id)
        if task_id is not None:
            query += " AND task_id = ?"
            params.append(task_id)
        if payload_match is not None:
            json_path, expected_value = payload_match
            query += " AND json_extract(payload_json, ?) = ?"
            params.extend([str(json_path), str(expected_value)])
        query += " LIMIT 1"
        with open_db(self.db_path) as conn:
            return conn.execute(query, params).fetchone() is not None

    def prune_pairing_codes(self, *, older_than_s: int = 3600, now: int | None = None) -> int:
        current = int(time.time() if now is None else now)
        threshold = current - max(60, int(older_than_s))
        with open_db(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM mobile_pairing_codes WHERE expires_at < ?",
                (threshold,),
            )
            return int(cursor.rowcount or 0)


    def remember_request(
        self,
        request_key: str,
        *,
        kind: str,
        session_id: str | None = None,
        task_id: str | None = None,
        now: int | None = None,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        key = str(request_key or "").strip()
        if not key:
            raise ValueError("request_key is required")
        current = int(time.time() if now is None else now)
        with open_db(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mobile_requests (request_key, kind, session_id, task_id, created_at, receipt_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, str(kind), session_id or None, task_id or None, current, json.dumps({k:v for k,v in (receipt or {}).items() if k in {'task_id','message_id','session_id','status','steered','duplicate'}})),
            )

    def lookup_request(self, request_key: str) -> dict[str, Any] | None:
        key = str(request_key or "").strip()
        if not key:
            return None
        with open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT request_key, kind, session_id, task_id, created_at, receipt_json FROM mobile_requests WHERE request_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "request_key": row[0],
            "kind": row[1],
            "session_id": row[2],
            "task_id": row[3],
            "created_at": int(row[4] or 0),
            "receipt": json.loads(row[5] or '{}'),
        }

