"""Archive vector cache for semantic search over the full ChatGPT export.

Archive entries (``chatgpt_export_message``) are many orders of magnitude larger
than the local recall window, so scoring them row-by-row from ``vector_json`` on
the query path is not viable. This module materialises the archive subset into a
single float32 matrix on disk (``numpy.memmap``) and answers top-k cosine
queries with one matrix-vector product. The database-side build work lives in
:mod:`session_recall_archive_build`.

Everything here is fail-open and lazy:

* ``numpy`` is imported inside functions, so importing this module never
  requires it.
* The hot query path never blocks on a rebuild: a missing or stale cache
  schedules a daemon thread and the caller falls back to local-only results
  (``search`` returns ``[]``). A stale-but-readable cache is served while the
  rebuild happens in the background.
* A cross-process ``fcntl`` lock keeps concurrent processes from building the
  same cache twice.

Only vectors and ``entry_id`` values are stored; text is re-fetched from the
database by id.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .runtime import get_data_root, get_db_path
from .platform_support import exclusive_lock
from .session_recall_archive_build import (
    MANIFEST_NAME,
    SCHEMA_VERSION,
    archive_revision,
    dominant_archive_model,
    write_generation,
)

_CACHE_DIRNAME = "session_recall_cache"
_LOCK_NAME = "manifest.lock"
_REVISION_TTL_S = 30.0

_indexes: dict[tuple[str, str], "ArchiveVectorIndex"] = {}
_indexes_lock = threading.Lock()


def _import_numpy() -> Any:
    import numpy as np  # noqa: PLC0415 - optional dependency, imported lazily on purpose

    return np


def default_cache_dir() -> Path:
    return get_data_root() / _CACHE_DIRNAME


def get_archive_index(
    db_path: str | None = None, cache_dir: str | Path | None = None
) -> "ArchiveVectorIndex":
    resolved_db = str(db_path or get_db_path())
    resolved_cache = Path(cache_dir) if cache_dir else default_cache_dir()
    key = (resolved_db, str(resolved_cache))
    with _indexes_lock:
        index = _indexes.get(key)
        if index is None:
            index = ArchiveVectorIndex(resolved_db, resolved_cache)
            _indexes[key] = index
        return index


def reset_archive_index_cache() -> None:
    with _indexes_lock:
        _indexes.clear()


class ArchiveVectorIndex:
    def __init__(self, db_path: str, cache_dir: str | Path) -> None:
        self.db_path = str(db_path)
        self.cache_dir = Path(cache_dir)
        self._thread_lock = threading.Lock()
        self._build_thread: threading.Thread | None = None
        self._revision_checked_at = 0.0
        self._revision_checked_model = ""
        self._revision_value: dict[str, Any] | None = None

    def _revision_cached(self, model: str) -> dict[str, Any] | None:
        import time as _time

        now = _time.monotonic()
        with self._thread_lock:
            fresh = (
                self._revision_checked_model == model
                and (now - self._revision_checked_at) < _REVISION_TTL_S
            )
            if fresh:
                return self._revision_value
        value = archive_revision(self.db_path, model)
        with self._thread_lock:
            self._revision_checked_model = model
            self._revision_checked_at = _time.monotonic()
            self._revision_value = value
        return value

    # -- query path ---------------------------------------------------------

    def search(
        self, query_vector: list[float], k: int, *, model: str | None = None
    ) -> list[tuple[str, float]]:
        if int(k or 0) <= 0:
            return []
        try:
            np = _import_numpy()
        except ImportError:
            return []
        try:
            matrix, ids = self._serve(model)
        except Exception:
            return []
        if matrix is None or not ids:
            return []
        try:
            query = np.asarray(query_vector, dtype=np.float32)
            if query.ndim != 1 or int(query.shape[0]) != int(matrix.shape[1]):
                return []
            norm = float(np.linalg.norm(query))
            if norm <= 0.0:
                return []
            scores = matrix @ (query / norm)
            total = int(scores.shape[0])
            count = min(int(k), total)
            if count <= 0:
                return []
            if count < total:
                candidates = np.argpartition(-scores, count - 1)[:count]
            else:
                candidates = np.arange(total)
            ordered = candidates[np.argsort(-scores[candidates])]
            results: list[tuple[str, float]] = []
            for position in ordered:
                score = float(scores[int(position)])
                if score <= 0.0:
                    continue
                results.append((ids[int(position)], score))
            return results
        except Exception:
            return []

    def _serve(self, model: str | None) -> tuple[Any, list[str]]:
        manifest = self._read_manifest()
        if manifest is None or not self._manifest_valid(manifest):
            self._schedule_build()
            return None, []
        matrix, ids = self._load_files(manifest)
        if matrix is None:
            self._schedule_build()
            return None, []
        if model and str(manifest.get("model") or "") != model:
            return None, []
        revision = self._revision_cached(str(manifest.get("model") or ""))
        if revision != manifest.get("revision"):
            # Stale-while-revalidate: serve current data, refresh in background.
            self._schedule_build()
        return matrix, ids

    # -- cache build --------------------------------------------------------

    def rebuild(self, *, model: str | None = None) -> bool:
        try:
            np = _import_numpy()
        except ImportError:
            return False
        lock_fd = self._acquire_lock()
        if lock_fd is None:
            return False
        try:
            target_model = str(model or dominant_archive_model(self.db_path) or "")
            revision = archive_revision(self.db_path, target_model)
            if revision is None:
                return False
            existing = self._read_manifest()
            if (
                existing
                and self._manifest_valid(existing)
                and existing.get("revision") == revision
                and self._files_present(existing)
            ):
                return True
            return write_generation(np, self.cache_dir, self.db_path, target_model, revision)
        except Exception:
            return False
        finally:
            self._release_lock(lock_fd)

    # -- cache metadata -----------------------------------------------------

    def _read_manifest(self) -> dict[str, Any] | None:
        try:
            data = json.loads((self.cache_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _manifest_valid(manifest: dict[str, Any]) -> bool:
        if int(manifest.get("schema_version") or 0) != SCHEMA_VERSION:
            return False
        if not str(manifest.get("model") or "") or not str(manifest.get("gen") or ""):
            return False
        if int(manifest.get("dim") or 0) <= 0 or int(manifest.get("count") or 0) <= 0:
            return False
        files = manifest.get("files")
        if not isinstance(files, dict) or not files.get("vectors") or not files.get("ids"):
            return False
        return isinstance(manifest.get("revision"), dict)

    def _load_files(self, manifest: dict[str, Any]) -> tuple[Any, list[str]]:
        try:
            np = _import_numpy()
            files = manifest["files"]
            matrix = np.load(self.cache_dir / str(files["vectors"]), mmap_mode="r")
            ids = (self.cache_dir / str(files["ids"])).read_text(encoding="utf-8").splitlines()
        except (ImportError, OSError, KeyError, TypeError, ValueError):
            return None, []
        if getattr(matrix, "ndim", 0) != 2 or int(matrix.shape[0]) != len(ids) or int(matrix.shape[0]) == 0:
            return None, []
        return matrix, ids

    def _files_present(self, manifest: dict[str, Any]) -> bool:
        files = manifest.get("files")
        if not isinstance(files, dict):
            return False
        try:
            return (self.cache_dir / str(files["vectors"])).exists() and (
                self.cache_dir / str(files["ids"])
            ).exists()
        except KeyError:
            return False

    # -- build scheduling / locking -----------------------------------------

    def _schedule_build(self) -> None:
        with self._thread_lock:
            if self._build_thread is not None and self._build_thread.is_alive():
                return
            thread = threading.Thread(
                target=self._background_build,
                name="session-recall-archive-build",
                daemon=True,
            )
            self._build_thread = thread
            thread.start()

    def _background_build(self) -> None:
        try:
            self.rebuild()
        except Exception:
            pass
        finally:
            with self._thread_lock:
                self._build_thread = None

    def _acquire_lock(self) -> int | None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd = exclusive_lock(self.cache_dir / _LOCK_NAME)
        except OSError:
            return None
        return fd

    @staticmethod
    def _release_lock(fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass
