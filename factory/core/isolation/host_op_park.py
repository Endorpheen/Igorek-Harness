"""Host-owned index of parked host-op requests (plan todo 22 runtime wiring).

The gate's in-memory pending table cannot authenticate a decision that arrives
in a *different* host process than the one that parked the request: the worker
parks a host op, the mobile bridge resolves it, and the TUI hands its decision
back through task metadata. Without a persistent authority the resolver either
refuses a legitimate decision or would have to trust a record it never parked.

This module is that authority: a small host-owned JSON index whose ``claim`` is
atomic across processes, so one parked request can be executed exactly once no
matter which host runtime resolves it. The file lives under the data root,
contains only typed request records (no secrets, no paths) and every mutation is
a locked read-modify-write committed with :func:`os.replace`.
"""

from __future__ import annotations

import json
import os
from ..platform_support import exclusive_lock
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final, Protocol

from .channel import JsonValue
from .host_op_approval import REASON_INVALID_REQUEST, HostOpApprovalError

__all__ = [
    "REASON_PARK_UNAVAILABLE",
    "HostOpParkStore",
    "JsonlHostOpParkStore",
]

#: The park index could not be read/written; a decision must then refuse.
REASON_PARK_UNAVAILABLE: Final[str] = "host_op_park_unavailable"


class HostOpParkStore(Protocol):
    """Persistent parked-request authority; ``claim`` is the once-only gate."""

    def put(self, record: Mapping[str, JsonValue]) -> None: ...

    def claim(self, request_id: str) -> Mapping[str, JsonValue] | None: ...


class JsonlHostOpParkStore:
    """One JSON object of parked requests, mutated under an exclusive file lock."""

    __slots__ = ("_lock_path", "_path")

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_name(self._path.name + ".lock")

    @property
    def path(self) -> Path:
        return self._path

    def put(self, record: Mapping[str, JsonValue]) -> None:
        """Index one parked request; indexing the same request twice is idempotent."""
        request_id = _request_id(record)
        parked = dict(record)

        def mutate(index: dict[str, JsonValue]) -> None:
            index[request_id] = parked

        self._update(mutate)

    def claim(self, request_id: str) -> Mapping[str, JsonValue] | None:
        """Atomically remove and return the request, or ``None`` when absent."""
        key = str(request_id or "").strip()
        if not key:
            return None
        claimed: list[Mapping[str, JsonValue] | None] = [None]

        def mutate(index: dict[str, JsonValue]) -> None:
            value = index.pop(key, None)
            claimed[0] = dict(value) if isinstance(value, Mapping) else None

        self._update(mutate)
        return claimed[0]

    def _update(self, mutate: Callable[[dict[str, JsonValue]], None]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(exclusive_lock(self._lock_path, blocking=True), "a+", encoding="utf-8"):
            index = _read_index(self._path)
            mutate(index)
            temporary = self._path.with_name(self._path.name + ".tmp")
            temporary.write_text(
                json.dumps(index, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)


def _request_id(record: Mapping[str, JsonValue]) -> str:
    request_id = str(record.get("request_id") or "").strip()
    if not request_id:
        raise HostOpApprovalError(REASON_INVALID_REQUEST, "empty_request_id")
    return request_id


def _read_index(path: Path) -> dict[str, JsonValue]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}
