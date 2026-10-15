"""Build half of the ChatGPT archive vector cache.

Owns the database-side concerns: discovering the dominant archive embedding
model, computing a cheap revision key, streaming vectors, and materialising a
new generation (float32 row-normalised matrix + ordered ids + manifest) on disk.
Kept separate from :mod:`session_recall_archive_index` so each module stays
small and the query path stays readable.

Creates files only through the caller's generation writer; never touches the
manifest publish step (that belongs to the index module's atomic ``os.replace``).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .schema import open_db

SCHEMA_VERSION = 1
ARCHIVE_EXCLUDED_KIND = "episodic_chunk"
MANIFEST_NAME = "manifest.json"
READ_BATCH = 2000
GENERATION_FILE_PREFIXES = (".vectors.", ".ids.", "vectors.", "ids.")


def archive_revision(db_path: str, model: str) -> dict[str, Any] | None:
    """Cheap fingerprint of the archive subset (count, max updated_at, max rowid)."""
    if not model:
        return None
    try:
        with open_db(db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*), COALESCE(MAX(emb.vector_dim), 0),
                       COALESCE(MAX(emb.updated_at), 0), COALESCE(MAX(emb.rowid), 0)
                FROM session_recall_embeddings emb
                JOIN session_recall_entries ent ON ent.entry_id = emb.entry_id
                WHERE emb.embedding_model = ? AND ent.entry_kind != ?
                """,
                (model, ARCHIVE_EXCLUDED_KIND),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row or int(row[0] or 0) <= 0:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "dim": int(row[1] or 0),
        "count": int(row[0] or 0),
        "max_updated_at": int(row[2] or 0),
        "max_rowid": int(row[3] or 0),
    }


def dominant_archive_model(db_path: str) -> str | None:
    try:
        with open_db(db_path) as conn:
            row = conn.execute(
                """
                SELECT emb.embedding_model, COUNT(*) AS total
                FROM session_recall_embeddings emb
                JOIN session_recall_entries ent ON ent.entry_id = emb.entry_id
                WHERE ent.entry_kind != ? AND emb.embedding_model IS NOT NULL
                      AND emb.embedding_model != ''
                GROUP BY emb.embedding_model
                ORDER BY total DESC
                LIMIT 1
                """,
                (ARCHIVE_EXCLUDED_KIND,),
            ).fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row and row[0] else None


def stream_rows(db_path: str, model: str) -> Iterator[tuple[str, list[float]]]:
    query = """
        SELECT emb.entry_id, emb.vector_json
        FROM session_recall_embeddings emb
        JOIN session_recall_entries ent ON ent.entry_id = emb.entry_id
        WHERE emb.embedding_model = ? AND ent.entry_kind != ?
        ORDER BY emb.rowid ASC
    """
    with open_db(db_path) as conn:
        cursor = conn.execute(query, (model, ARCHIVE_EXCLUDED_KIND))
        while True:
            batch = cursor.fetchmany(READ_BATCH)
            if not batch:
                break
            for entry_id, vector_json in batch:
                try:
                    vector = json.loads(vector_json) if vector_json else None
                except (TypeError, ValueError):
                    continue
                if isinstance(vector, list) and vector:
                    yield str(entry_id), vector


def write_generation(
    np: Any, cache_dir: Path, db_path: str, model: str, revision: dict[str, Any]
) -> bool:
    dim = int(revision.get("dim") or 0)
    expected = int(revision.get("count") or 0)
    if dim <= 0 or expected <= 0:
        return False
    generation = f"{time.time_ns()}-{os.getpid()}"
    vectors_name = f"vectors.{generation}.npy"
    ids_name = f"ids.{generation}.txt"
    tmp_vectors = cache_dir / f".{vectors_name}.tmp"
    matrix = np.lib.format.open_memmap(
        str(tmp_vectors), mode="w+", dtype=np.float32, shape=(expected, dim)
    )
    ids: list[str] = []
    written = 0
    try:
        for entry_id, vector in stream_rows(db_path, model):
            array = np.asarray(vector, dtype=np.float32)
            if array.ndim != 1 or int(array.shape[0]) != dim:
                continue
            norm = float(np.linalg.norm(array))
            if norm <= 0.0:
                continue
            matrix[written] = array / norm
            ids.append(entry_id)
            written += 1
        matrix.flush()
    except Exception:
        tmp_vectors.unlink(missing_ok=True)
        raise
    finally:
        del matrix
    if written <= 0:
        tmp_vectors.unlink(missing_ok=True)
        return False
    if written == expected:
        os.replace(tmp_vectors, cache_dir / vectors_name)
    else:
        partial = np.lib.format.open_memmap(str(tmp_vectors), mode="r")
        np.save(str(cache_dir / vectors_name), partial[:written])
        del partial
        tmp_vectors.unlink(missing_ok=True)
    (cache_dir / ids_name).write_text("\n".join(ids) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "dim": dim,
        "gen": generation,
        "count": written,
        "revision": revision,
        "files": {"vectors": vectors_name, "ids": ids_name},
    }
    tmp_manifest = cache_dir / f"{MANIFEST_NAME}.tmp"
    tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_manifest, cache_dir / MANIFEST_NAME)
    cleanup_old(cache_dir, generation)
    return True


def cleanup_old(cache_dir: Path, keep_generation: str) -> None:
    try:
        entries = list(cache_dir.iterdir())
    except OSError:
        return
    for path in entries:
        name = path.name
        if not name.startswith(GENERATION_FILE_PREFIXES) or keep_generation in name:
            continue
        try:
            path.unlink()
        except OSError:
            pass
