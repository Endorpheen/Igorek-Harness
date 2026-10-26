from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from math import isfinite, sqrt
from pathlib import Path
from typing import Any

from .runtime import get_chat_sessions_dir, get_db_path
from .schema import open_db
from .session_embeddings import LocalSessionEmbedder
from .session_recall_archive_index import get_archive_index

_MAX_USER_CHARS = 320
_MAX_ASSISTANT_CHARS = 700
_MIN_COMBINED_CHARS = 48
_FTS_TABLE = "session_recall_entries_fts"
_EMBEDDING_BATCH_SIZE = 16


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clip_text(value: str, limit: int) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "…"


def _load_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}:
            entries.append(item)
    return entries


def _decode_tags(raw: Any) -> list[Any]:
    """Retain valid tag lists without failing recall on optional corrupt metadata."""
    try:
        parsed = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _build_recall_chunks(session_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    pending_user = ""
    assistant_parts: list[str] = []
    started_ts = 0

    def flush_chunk() -> None:
        nonlocal pending_user, assistant_parts, started_ts
        user_text = _clip_text(pending_user, _MAX_USER_CHARS)
        assistant_text = _clip_text(" ".join(part for part in assistant_parts if part.strip()), _MAX_ASSISTANT_CHARS)
        if len(user_text) + len(assistant_text) < _MIN_COMBINED_CHARS:
            pending_user = ""
            assistant_parts = []
            started_ts = 0
            return
        lines: list[str] = []
        if user_text:
            lines.append(f"User goal: {user_text}")
        if assistant_text:
            lines.append(f"Outcome: {assistant_text}")
        text = "\n".join(lines).strip()
        if not text:
            pending_user = ""
            assistant_parts = []
            started_ts = 0
            return
        content_hash = hashlib.sha256(f"{session_id}\n{text}".encode("utf-8")).hexdigest()
        tags = ["episodic_chunk"]
        if user_text and assistant_text:
            tags.append("paired_turn")
        elif assistant_text:
            tags.append("assistant_only")
        chunks.append(
            {
                "entry_id": str(uuid.uuid4()),
                "session_id": session_id,
                "entry_kind": "episodic_chunk",
                "text": text,
                "content_hash": content_hash,
                "ts": int(started_ts or time.time()),
                "tags": tags,
            }
        )
        pending_user = ""
        assistant_parts = []
        started_ts = 0

    for entry in entries:
        role = str(entry.get("role") or "")
        text = _normalize_text(entry.get("text") or "")
        if not text:
            continue
        try:
            ts_value = int(entry.get("ts") or 0)
        except (TypeError, ValueError, OverflowError):
            ts_value = 0
        if role == "user":
            if pending_user or assistant_parts:
                flush_chunk()
            pending_user = text
            started_ts = ts_value
            continue
        if role == "assistant":
            if not pending_user and not assistant_parts:
                started_ts = ts_value
            assistant_parts.append(text)

    if pending_user or assistant_parts:
        flush_chunk()
    return chunks


class SessionRecallStore:
    def __init__(self, db_path: str | None = None, *, archive_cache_dir: str | None = None) -> None:
        self.db_path = db_path or get_db_path()
        self.embedder = LocalSessionEmbedder()
        self.archive_cache_dir = archive_cache_dir

    def ensure_schema(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with open_db(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_recall_entries (
                    entry_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    entry_kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    source_path TEXT,
                    source_mtime INTEGER DEFAULT 0,
                    ts INTEGER NOT NULL,
                    tags_json TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_recall_entries_session_ts
                ON session_recall_entries(session_id, ts DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_recall_entries_updated
                ON session_recall_entries(updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_recall_entries_source_path
                ON session_recall_entries(source_path)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_recall_embeddings (
                    entry_id TEXT PRIMARY KEY,
                    embedding_model TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    vector_dim INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_recall_embeddings_model_updated
                ON session_recall_embeddings(embedding_model, updated_at DESC)
                """
            )
            try:
                conn.execute(
                    f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE}
                    USING fts5(entry_id UNINDEXED, session_id UNINDEXED, text)
                    """
                )
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def sync_session_file(self, path: Path) -> int:
        self.ensure_schema()
        resolved = path.expanduser().resolve()
        if not resolved.exists():
            return 0
        session_id = resolved.stem
        source_mtime = int(resolved.stat().st_mtime)
        with open_db(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                """
                SELECT DISTINCT source_mtime
                FROM session_recall_entries
                WHERE source_path = ?
                LIMIT 1
                """,
                (str(resolved),),
            ).fetchone()
            if existing and int(existing["source_mtime"] or 0) == source_mtime:
                return 0

            entries = _load_entries(resolved)
            chunks = _build_recall_chunks(session_id, entries)
            now = int(time.time())

            if not chunks:
                # No recall chunks: skip the O(table) FTS/embedding cleanups below. They would
                # otherwise run on every call because an empty session stores no row and thus
                # can never record source_mtime. Only clean up if this file used to have rows.
                if existing is not None:
                    conn.execute("DELETE FROM session_recall_entries WHERE source_path = ?", (str(resolved),))
                    conn.execute(
                        """
                        DELETE FROM session_recall_embeddings
                        WHERE entry_id NOT IN (SELECT entry_id FROM session_recall_entries)
                        """
                    )
                    try:
                        conn.execute(f"DELETE FROM {_FTS_TABLE} WHERE session_id = ?", (session_id,))
                    except sqlite3.OperationalError:
                        pass
                    conn.commit()
                return 0

            conn.execute("DELETE FROM session_recall_entries WHERE source_path = ?", (str(resolved),))
            conn.execute(
                """
                DELETE FROM session_recall_embeddings
                WHERE entry_id NOT IN (SELECT entry_id FROM session_recall_entries)
                """
            )
            try:
                conn.execute(f"DELETE FROM {_FTS_TABLE} WHERE session_id = ?", (session_id,))
            except sqlite3.OperationalError:
                pass

            inserted = 0
            for chunk in chunks:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO session_recall_entries (
                        entry_id, session_id, entry_kind, text, content_hash, source_path,
                        source_mtime, ts, tags_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["entry_id"],
                        session_id,
                        chunk["entry_kind"],
                        chunk["text"],
                        chunk["content_hash"],
                        str(resolved),
                        source_mtime,
                        int(chunk["ts"]),
                        json.dumps(chunk["tags"], ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                if cursor.rowcount <= 0:
                    continue
                try:
                    conn.execute(
                        f"INSERT INTO {_FTS_TABLE} (entry_id, session_id, text) VALUES (?, ?, ?)",
                        (chunk["entry_id"], session_id, chunk["text"]),
                    )
                except sqlite3.OperationalError:
                    pass
                inserted += 1
            conn.commit()
        if inserted and self.embedder.available:
            try:
                self.sync_missing_embeddings(session_id=session_id)
            except sqlite3.OperationalError:
                pass
        return inserted

    def sync_recent_sessions(self, *, current_session: str | None = None, limit: int = 200) -> int:
        sessions_dir = Path(get_chat_sessions_dir())
        sessions_dir.mkdir(parents=True, exist_ok=True)
        current_name = f"{current_session}.jsonl" if current_session else ""
        files = sorted(
            (path for path in sessions_dir.glob("*.jsonl") if path.name != current_name),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[: max(1, limit)]
        touched = 0
        for path in files:
            try:
                touched += self.sync_session_file(path)
            except sqlite3.OperationalError:
                return touched
        return touched

    def search_lexical(self, query: str, *, current_session: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        normalized_query = _normalize_text(query)
        if not normalized_query:
            return []
        try:
            self.sync_recent_sessions(current_session=current_session)
        except sqlite3.OperationalError:
            return []
        requested_limit = max(1, min(int(limit or 5), 10))
        current_name = str(current_session or "").strip()
        try:
            with open_db(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        f"""
                        SELECT e.entry_id, e.session_id, e.entry_kind, e.text, e.ts, e.tags_json,
                               bm25({_FTS_TABLE}) AS lexical_score
                        FROM {_FTS_TABLE} f
                        JOIN session_recall_entries e ON e.entry_id = f.entry_id
                        WHERE {_FTS_TABLE} MATCH ?
                        ORDER BY lexical_score ASC, e.ts DESC
                        LIMIT ?
                        """,
                        (normalized_query, requested_limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    like_query = f"%{normalized_query.lower()}%"
                    rows = conn.execute(
                        """
                        SELECT entry_id, session_id, entry_kind, text, ts, tags_json, 0.0 AS lexical_score
                        FROM session_recall_entries
                        WHERE lower(text) LIKE ?
                        ORDER BY ts DESC
                        LIMIT ?
                        """,
                        (like_query, requested_limit),
                    ).fetchall()
        except sqlite3.OperationalError:
            return []

        results: list[dict[str, Any]] = []
        lowered = normalized_query.lower()
        terms = [term for term in re.split(r"\s+", lowered) if term]
        for row in rows:
            session_id = str(row["session_id"] or "")
            if current_name and session_id == current_name:
                continue
            text = str(row["text"] or "").strip()
            matched_terms = [term for term in terms if term in text.lower()]
            excerpt = text
            if len(excerpt) > 900:
                excerpt = excerpt[:900].rstrip() + "…"
            results.append(
                {
                    "source": "recall_entry",
                    "session": session_id,
                    "entry_id": str(row["entry_id"] or ""),
                    "entry_kind": str(row["entry_kind"] or "episodic_chunk"),
                    "last_active": "",
                    "matched_terms": matched_terms,
                    "lexical_score": float(row["lexical_score"] or 0.0),
                    "excerpt": excerpt,
                    "tags": _decode_tags(row["tags_json"]),
                }
            )
        return results[:requested_limit]

    def _embeddings_complete(self, conn: sqlite3.Connection) -> bool:
        try:
            entries = conn.execute("SELECT COUNT(*) FROM session_recall_entries").fetchone()[0]
            embeddings = conn.execute("SELECT COUNT(*) FROM session_recall_embeddings").fetchone()[0]
            if int(entries or 0) != int(embeddings or 0):
                return False
            foreign = conn.execute(
                "SELECT COUNT(*) FROM session_recall_embeddings WHERE embedding_model != ?",
                (self.embedder.model,),
            ).fetchone()[0]
            return int(foreign or 0) == 0
        except sqlite3.Error:
            return False

    def sync_missing_embeddings(self, *, session_id: str | None = None, limit: int = 200) -> int:
        if not self.embedder.available:
            return 0
        self.ensure_schema()
        where_clauses = [
            "(emb.entry_id IS NULL OR emb.content_hash != ent.content_hash OR emb.embedding_model != ?)"
        ]
        params: list[Any] = [self.embedder.model]
        if session_id:
            where_clauses.append("ent.session_id = ?")
            params.append(session_id)
        query = f"""
            SELECT ent.entry_id, ent.text, ent.content_hash
            FROM session_recall_entries ent
            LEFT JOIN session_recall_embeddings emb ON emb.entry_id = ent.entry_id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY ent.updated_at DESC, ent.ts DESC
            LIMIT ?
        """
        params.append(max(1, int(limit or 200)))
        with open_db(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if session_id is None and self._embeddings_complete(conn):
                return 0
            rows = conn.execute(query, params).fetchall()
            if not rows:
                return 0
            inserted = 0
            for batch_start in range(0, len(rows), _EMBEDDING_BATCH_SIZE):
                batch_rows = rows[batch_start : batch_start + _EMBEDDING_BATCH_SIZE]
                batch = self.embedder.embed_texts([str(row["text"] or "") for row in batch_rows])
                if batch is None:
                    continue
                now = int(time.time())
                for row, vector in zip(batch_rows, batch.vectors):
                    conn.execute(
                        """
                        INSERT INTO session_recall_embeddings (
                            entry_id, embedding_model, vector_json, vector_dim, content_hash, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(entry_id) DO UPDATE SET
                            embedding_model=excluded.embedding_model,
                            vector_json=excluded.vector_json,
                            vector_dim=excluded.vector_dim,
                            content_hash=excluded.content_hash,
                            updated_at=excluded.updated_at
                        """,
                        (
                            str(row["entry_id"] or ""),
                            batch.model,
                            json.dumps(vector, ensure_ascii=False),
                            int(batch.dimensions),
                            str(row["content_hash"] or ""),
                            now,
                            now,
                        ),
                    )
                    inserted += 1
            conn.commit()
        return inserted

    def sync_recent_embeddings(self, *, current_session: str | None = None, limit: int = 200) -> int:
        if not self.embedder.available:
            return 0
        self.sync_recent_sessions(current_session=current_session, limit=limit)
        return self.sync_missing_embeddings(limit=limit)

    def search_semantic(self, query: str, *, current_session: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        normalized_query = _normalize_text(query)
        if not normalized_query or not self.embedder.available:
            return []
        embedded = self.embedder.embed_text(normalized_query)
        if embedded is None:
            return []
        embedding_model, query_vector = embedded
        try:
            self.sync_recent_embeddings(current_session=current_session, limit=200)
        except sqlite3.OperationalError:
            return []
        current_name = str(current_session or "").strip()
        requested_limit = max(1, min(int(limit or 5), 10))
        try:
            with open_db(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT ent.entry_id, ent.session_id, ent.entry_kind, ent.text, ent.ts, ent.tags_json,
                           emb.embedding_model, emb.vector_json
                    FROM session_recall_entries ent
                    JOIN session_recall_embeddings emb ON emb.entry_id = ent.entry_id
                    WHERE emb.embedding_model = ?
                    ORDER BY ent.updated_at DESC, ent.ts DESC
                    LIMIT 600
                    """,
                    (embedding_model,),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        scored: list[dict[str, Any]] = []
        for row in rows:
            session_id = str(row["session_id"] or "")
            if current_name and session_id == current_name:
                continue
            try:
                vector = json.loads(row["vector_json"]) if row["vector_json"] else []
            except json.JSONDecodeError:
                continue
            if not isinstance(vector, list) or not vector:
                continue
            try:
                score = _cosine_similarity(query_vector, vector)
            except (TypeError, ValueError, OverflowError):
                continue
            if not isfinite(score) or score <= 0.0:
                continue
            text = str(row["text"] or "").strip()
            excerpt = text if len(text) <= 900 else text[:900].rstrip() + "…"
            scored.append(
                {
                    "source": "semantic_entry",
                    "session": session_id,
                    "entry_id": str(row["entry_id"] or ""),
                    "entry_kind": str(row["entry_kind"] or "episodic_chunk"),
                    "last_active": "",
                    "semantic_score": score,
                    "excerpt": excerpt,
                    "tags": _decode_tags(row["tags_json"]),
                    "_provenance": "local",
                }
            )
        scored.sort(key=lambda item: float(item.get("semantic_score") or 0.0), reverse=True)
        local_top = scored[:requested_limit]
        archive_top = self._archive_semantic_results(
            query_vector, embedding_model, requested_limit, current_name
        )
        return local_top + archive_top

    def _archive_semantic_results(
        self,
        query_vector: list[float],
        embedding_model: str,
        requested_limit: int,
        current_name: str,
    ) -> list[dict[str, Any]]:
        """Semantic hits from the full archive, fail-open at every step.

        The archive cache is optional: missing/corrupt/stale cache, absent numpy,
        dim mismatch or any DB error all silently yield no archive hits so the
        local path keeps working exactly as before.
        """
        try:
            index = get_archive_index(self.db_path, self.archive_cache_dir)
            hits = index.search(query_vector, requested_limit, model=embedding_model)
        except Exception:
            return []
        if not hits:
            return []
        ids = [entry_id for entry_id, _ in hits]
        scores = {entry_id: score for entry_id, score in hits}
        placeholders = ",".join("?" for _ in ids)
        try:
            with open_db(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"""
                    SELECT entry_id, session_id, entry_kind, text, tags_json
                    FROM session_recall_entries
                    WHERE entry_id IN ({placeholders})
                    """,
                    ids,
                ).fetchall()
        except sqlite3.Error:
            return []
        by_id = {str(row["entry_id"]): row for row in rows}
        results: list[dict[str, Any]] = []
        for entry_id in ids:
            row = by_id.get(entry_id)
            if row is None:
                continue
            session_id = str(row["session_id"] or "")
            if current_name and session_id == current_name:
                continue
            text = str(row["text"] or "").strip()
            excerpt = text if len(text) <= 900 else text[:900].rstrip() + "…"
            results.append(
                {
                    "source": "semantic_entry",
                    "session": session_id,
                    "entry_id": entry_id,
                    "entry_kind": str(row["entry_kind"] or ""),
                    "last_active": "",
                    "semantic_score": scores[entry_id],
                    "excerpt": excerpt,
                    "tags": _decode_tags(row["tags_json"]),
                    "_provenance": "archive",
                }
            )
        return results


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    if length <= 0:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for index in range(length):
        lval = float(left[index])
        rval = float(right[index])
        dot += lval * rval
        left_norm += lval * lval
        right_norm += rval * rval
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (sqrt(left_norm) * sqrt(right_norm))
