from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory.core.schema import open_db
from factory.core.session_recall_archive_build import archive_revision
from factory.core.session_recall_archive_index import (
    get_archive_index,
    reset_archive_index_cache,
)
from factory.core.session_recall_store import SessionRecallStore
from factory.core.session_search import _merge_search_results

MODEL = "fake-model"
DIM = 4
QUERY = "remember the archive"


class _FakeBatch:
    def __init__(self, model: str, vectors: list[list[float]]) -> None:
        self.model = model
        self.vectors = vectors
        self.dimensions = len(vectors[0]) if vectors else 0


class _FakeEmbedder:
    """Deterministic embedder: text -> fixed vector, dim 4, no network."""

    def __init__(self, vectors: dict[str, list[float]], *, available: bool = True) -> None:
        self._vectors = vectors
        self.model = MODEL
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def embed_text(self, text: str) -> tuple[str, list[float]] | None:
        vector = self._vectors.get(text)
        if vector is None:
            return None
        return self.model, list(vector)

    def embed_texts(self, texts: list[str]) -> _FakeBatch | None:
        vectors = [self._vectors.get(text) for text in texts]
        if any(vector is None for vector in vectors):
            return None
        return _FakeBatch(self.model, [list(vector) for vector in vectors])


def _insert_entry(
    conn,
    *,
    entry_id: str,
    session_id: str,
    kind: str,
    text: str,
    vector: list[float],
    updated_at: int,
    model: str = MODEL,
) -> None:
    content_hash = f"h-{entry_id}"
    conn.execute(
        """
        INSERT INTO session_recall_entries (
            entry_id, session_id, entry_kind, text, content_hash, source_path,
            source_mtime, ts, tags_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, NULL, 0, 0, '[]', 0, ?)
        """,
        (entry_id, session_id, kind, text, content_hash, updated_at),
    )
    conn.execute(
        """
        INSERT INTO session_recall_embeddings (
            entry_id, embedding_model, vector_json, vector_dim, content_hash,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 0, 0)
        """,
        (entry_id, model, json.dumps(vector), len(vector), content_hash),
    )
    conn.commit()


class ArchiveIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = mock.patch.dict(
            os.environ,
            {
                "IGOREK_FACTORY_HOME": "",
                "FACTORY_DATA_DIR": "",
                "IGOREK_SESSION_EMBEDDINGS_BASE_URL": "",
                "IGOREK_SESSION_EMBEDDINGS_MODEL": "",
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.db = str(self.dir / "recall.db")
        self.cache = self.dir / "cache"
        self._home = mock.patch.dict(os.environ, {"IGOREK_FACTORY_HOME": str(self.dir)})
        self._home.start()
        self.addCleanup(self._home.stop)
        reset_archive_index_cache()
        self.addCleanup(reset_archive_index_cache)

    def _store(self, vectors: dict[str, list[float]], *, available: bool = True) -> SessionRecallStore:
        store = SessionRecallStore(db_path=self.db, archive_cache_dir=str(self.cache))
        store.ensure_schema()
        store.embedder = _FakeEmbedder(vectors, available=available)
        return store

    def _index(self):
        return get_archive_index(self.db, str(self.cache))

    # (a) archive row with updated_at=0 appears despite the 600-row local window
    def test_archive_row_surfaces_even_outside_local_window(self) -> None:
        store = self._store({QUERY: [1.0, 0.0, 0.0, 0.0]})
        with open_db(self.db) as conn:
            for i in range(600):
                _insert_entry(
                    conn,
                    entry_id=f"f{i}",
                    session_id=f"fs{i}",
                    kind="episodic_chunk",
                    text=f"filler {i}",
                    vector=[0.0, 0.0, 0.0, 1.0],
                    updated_at=1000 + i,
                )
            _insert_entry(
                conn,
                entry_id="arch1",
                session_id="arch-session",
                kind="chatgpt_export_message",
                text="archive only text",
                vector=[1.0, 0.0, 0.0, 0.0],
                updated_at=0,
            )
        assert self._index().rebuild(model=MODEL) is True

        results = store.search_semantic(QUERY, limit=3)

        archive_hits = [item for item in results if item["entry_id"] == "arch1"]
        assert len(archive_hits) == 1
        assert archive_hits[0]["_provenance"] == "archive"
        assert abs(archive_hits[0]["semantic_score"] - 1.0) < 1e-6

    # (b) local results survive the floor when archive candidates outrank them
    def test_local_entry_not_drowned_by_archive(self) -> None:
        archive_items = [
            {
                "source": "semantic_entry",
                "session": "arch",
                "entry_id": f"a{i}",
                "entry_kind": "chatgpt_export_message",
                "last_active": "",
                "semantic_score": 0.99 - i * 0.01,
                "excerpt": f"archive {i}",
                "tags": [],
                "_provenance": "archive",
            }
            for i in range(3)
        ]
        local_item = {
            "source": "semantic_entry",
            "session": "local",
            "entry_id": "l1",
            "entry_kind": "episodic_chunk",
            "last_active": "",
            "semantic_score": 0.5,
            "excerpt": "local detail",
            "tags": [],
            "_provenance": "local",
        }
        transcript = [
            {
                "source": "transcript",
                "session": "t1",
                "entry_kind": "session_transcript",
                "excerpt": "transcript text",
                "matched_terms": [],
                "last_active": "",
            }
        ]

        merged = _merge_search_results(transcript, [], archive_items + [local_item], limit=3)
        ids = [item.get("entry_id") for item in merged]

        assert "l1" in ids
        assert any(entry_id in {"a0", "a1", "a2"} for entry_id in ids)
        assert all("_provenance" not in item for item in merged)

    # (c) cache builds and invalidates when the revision changes
    def test_cache_build_and_invalidation_on_revision_change(self) -> None:
        self._store({QUERY: [1.0, 0.0, 0.0, 0.0]})
        with open_db(self.db) as conn:
            _insert_entry(
                conn,
                entry_id="arch1",
                session_id="s1",
                kind="chatgpt_export_message",
                text="first",
                vector=[1.0, 0.0, 0.0, 0.0],
                updated_at=0,
            )
        index = self._index()
        assert index.rebuild(model=MODEL) is True
        manifest1 = json.loads((self.cache / "manifest.json").read_text(encoding="utf-8"))
        assert manifest1["count"] == 1
        assert manifest1["revision"] == archive_revision(self.db, MODEL)

        with open_db(self.db) as conn:
            _insert_entry(
                conn,
                entry_id="arch2",
                session_id="s2",
                kind="chatgpt_export_message",
                text="second",
                vector=[0.0, 1.0, 0.0, 0.0],
                updated_at=0,
            )
        assert archive_revision(self.db, MODEL) != manifest1["revision"]

        assert index.rebuild(model=MODEL) is True
        manifest2 = json.loads((self.cache / "manifest.json").read_text(encoding="utf-8"))
        assert manifest2["count"] == 2
        assert manifest2["revision"] == archive_revision(self.db, MODEL)
        assert manifest2["gen"] != manifest1["gen"]
        assert not (self.cache / f"vectors.{manifest1['gen']}.npy").exists()

    # (d) unavailable embedder short-circuits before any cache work
    def test_unavailable_embedder_returns_empty_and_leaves_cache_untouched(self) -> None:
        store = self._store({}, available=False)
        assert store.search_semantic(QUERY, limit=3) == []
        assert not (self.cache / "manifest.json").exists()

    # (e) corrupt cache falls back to local-only results
    def test_corrupt_cache_falls_back_to_local(self) -> None:
        store = self._store({QUERY: [1.0, 0.0, 0.0, 0.0]})
        with open_db(self.db) as conn:
            _insert_entry(
                conn,
                entry_id="local1",
                session_id="ls",
                kind="episodic_chunk",
                text="local only",
                vector=[1.0, 0.0, 0.0, 0.0],
                updated_at=50,
            )
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / "manifest.json").write_text("not json {", encoding="utf-8")

        results = store.search_semantic(QUERY, limit=3)

        assert any(item["entry_id"] == "local1" for item in results)

    # (f) numpy absent -> graceful skip, local path still works
    def test_numpy_absent_skips_archive_gracefully(self) -> None:
        store = self._store({QUERY: [1.0, 0.0, 0.0, 0.0]})
        with open_db(self.db) as conn:
            _insert_entry(
                conn,
                entry_id="local1",
                session_id="ls",
                kind="episodic_chunk",
                text="local only",
                vector=[1.0, 0.0, 0.0, 0.0],
                updated_at=50,
            )
        index = self._index()
        with mock.patch.dict(sys.modules, {"numpy": None}):
            assert index.search([1.0, 0.0, 0.0, 0.0], 3, model=MODEL) == []
            results = store.search_semantic(QUERY, limit=3)
        assert any(item["entry_id"] == "local1" for item in results)

    # (g) fairness is a no-op without archive candidates, and active with them
    def test_merge_fairness_noop_without_archive_and_active_with_archive(self) -> None:
        transcript = [
            {
                "source": "transcript",
                "session": "t1",
                "entry_kind": "session_transcript",
                "excerpt": "transcript text",
                "matched_terms": [],
                "last_active": "",
            }
        ]
        local_no_marker = {
            "source": "semantic_entry",
            "session": "local",
            "entry_id": "l1",
            "entry_kind": "episodic_chunk",
            "last_active": "",
            "semantic_score": 0.62,
            "excerpt": "local detail",
            "tags": [],
        }
        local_with_marker = dict(local_no_marker, _provenance="local")

        legacy = _merge_search_results(transcript, [], [local_no_marker], limit=3)
        modern = _merge_search_results(transcript, [], [local_with_marker], limit=3)
        assert modern == legacy

        archive = dict(local_no_marker, entry_id="a1", session="arch")
        archive["entry_kind"] = "chatgpt_export_message"
        archive["_provenance"] = "archive"
        archive["semantic_score"] = 0.99
        modern_archive = _merge_search_results(transcript, [], [archive, local_with_marker], limit=2)
        ids = [item.get("entry_id") for item in modern_archive]
        assert "a1" in ids and "l1" in ids


if __name__ == "__main__":
    unittest.main()
