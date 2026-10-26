"""Session-based orchestration for ``@`` file searches (port of file_search.rs).

The Rust port uses the ``codex-file-search`` crate to maintain a search
session. The Python port mirrors the orchestration exactly, but provides a
``FileSearchSession`` ABC so a backend (e.g. :class:`OsWalkFileSearchSession`)
can be substituted.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Protocol


class AppEventLike(Protocol):
    """Minimal protocol that the TUI's ``AppEvent::FileSearchResult`` shape satisfies."""


@dataclass
class FileSearchSnapshot:
    query: str
    matches: List[str] = field(default_factory=list)


class FileSearchSession(Protocol):
    def update_query(self, query: str) -> None: ...
    def snapshot(self) -> FileSearchSnapshot: ...


class FileSearchOptions:
    compute_indices: bool = True


class OsWalkFileSearchSession:
    """A very small :class:`FileSearchSession` implementation based on ``os.walk``.

    Used as the default backend in the Python port; the orchestration
    logic in :class:`FileSearchManager` is identical regardless of
    backend.
    """

    def __init__(self, roots: List[Path], options: FileSearchOptions) -> None:
        self._roots = roots
        self._options = options
        self._query: str = ""
        self._matches: List[str] = []

    def update_query(self, query: str) -> None:
        self._query = query
        if not query:
            self._matches = []
            return
        q = query.lower()
        self._matches = []
        seen = set()
        for root in self._roots:
            if not root.exists():
                continue
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, root)
                    if q in name.lower() or q in rel.lower():
                        if rel in seen:
                            continue
                        seen.add(rel)
                        self._matches.append(rel)
        self._matches.sort()

    def snapshot(self) -> FileSearchSnapshot:
        return FileSearchSnapshot(query=self._query, matches=list(self._matches))


@dataclass
class _SearchState:
    latest_query: str = ""
    session: Optional[FileSearchSession] = None
    session_token: int = 0


def create_session(
    roots: List[Path],
    options: FileSearchOptions,
    reporter: "TuiSessionReporter",
    cancel_flag: Optional[object] = None,
) -> "FileSearchSession":
    """Build a new search session (port of ``codex_file_search::create_session``)."""
    return OsWalkFileSearchSession(roots, options)


class FileSearchManager:
    """Session-based manager for ``@`` file searches (port of ``FileSearchManager``)."""

    def __init__(self, search_dir: Path, app_tx: "Callable[[object], None]") -> None:
        self._state = threading.RLock()
        self._st = _SearchState()
        self._search_dir = Path(search_dir)
        self._app_tx = app_tx

    def update_search_dir(self, new_dir: Path) -> None:
        """Replace the search root and drop the current session (port one-to-one)."""
        with self._state:
            self._search_dir = Path(new_dir)
            self._st.session = None
            self._st.latest_query = ""

    def on_user_query(self, query: str) -> None:
        """Push a new ``@`` query (port of ``FileSearchManager::on_user_query``)."""
        with self._state:
            if query == self._st.latest_query:
                return
            self._st.latest_query = query
            if not query:
                self._st.session = None
                return
            if self._st.session is None:
                self._start_session_locked()
            if self._st.session is not None:
                self._st.session.update_query(query)

    def _start_session_locked(self) -> None:
        self._st.session_token = (self._st.session_token + 1) & 0xFFFFFFFFFFFFFFFF
        reporter = TuiSessionReporter(
            self._state, self._st, self._app_tx, self._st.session_token
        )
        try:
            self._st.session = create_session(
                [self._search_dir], FileSearchOptions(), reporter
            )
        except Exception as err:
            import warnings
            warnings.warn(f"file search session failed to start: {err}")
            self._st.session = None


class TuiSessionReporter:
    """Reporter bridge to the TUI's app event bus (port of ``TuiSessionReporter``)."""

    def __init__(
        self,
        state: "threading.RLock",
        st: _SearchState,
        app_tx: "Callable[[object], None]",
        session_token: int,
    ) -> None:
        self._state = state
        self._st = st
        self._app_tx = app_tx
        self._session_token = session_token

    def on_update(self, snapshot: FileSearchSnapshot) -> None:
        self._send_snapshot(snapshot)

    def on_complete(self) -> None:
        return None

    def _send_snapshot(self, snapshot: FileSearchSnapshot) -> None:
        with self._state:
            if (
                self._st.session_token != self._session_token
                or not self._st.latest_query
                or not snapshot.query
                or snapshot.query != self._st.latest_query
            ):
                return
            query = snapshot.query
        self._app_tx(_FileSearchResultMessage(query=query, matches=list(snapshot.matches)))


@dataclass
class _FileSearchResultMessage:
    """Sentinel message for the TUI's app event bus (port of ``AppEvent::FileSearchResult``)."""
    query: str
    matches: List[str]


__all__ = [
    "FileSearchManager",
    "FileSearchOptions",
    "FileSearchSession",
    "FileSearchSnapshot",
    "OsWalkFileSearchSession",
    "TuiSessionReporter",
    "create_session",
]
