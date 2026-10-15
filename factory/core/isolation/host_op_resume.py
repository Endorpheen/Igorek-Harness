"""Host-op resume consumption: approved decisions execute exactly once (todo 22).

The TUI approves a host op on the terminal and stores the decision in the
resumed task's metadata (``host_op_resolutions`` with ``pending_broker``
records); the mobile bridge stores an executed outcome there, and every
``allow_session`` carries ``host_op_session_grants``. This module is the host
runtime half of that contract:

* :func:`consume_pending_host_op_resolutions` claims the pending records in the
  task row (``consuming``) *before* it asks the gate to execute anything, so a
  crash or a second resume can never run an approved op twice;
* an unusable gate, or a malformed/foreign record, refuses with zero host
  actions - the failure is recorded, never silently dropped;
* stored session grants are replayed into the runtime, so a later same-op
  request does not park again.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from ..runtime import get_db_path
from .channel import JsonValue
from .errors import IsolationError
from .host_op_gate import PENDING_BROKER_STATUS
from .host_op_runtime import HostOpApprovalRuntime, build_host_op_runtime

logger = logging.getLogger(__name__)

__all__ = [
    "CONSUMING_STATUS",
    "REASON_GATE_UNAVAILABLE",
    "REFUSED_STATUS",
    "HostOpConsumption",
    "consume_pending_host_op_resolutions",
]

#: A runtime where no gate/broker can be built still refuses, with this reason.
REASON_GATE_UNAVAILABLE: Final[str] = "host_op_gate_unavailable"
#: A record claimed but not yet executed: never re-executed after a crash.
CONSUMING_STATUS: Final[str] = "consuming"
#: A record that could not be executed: kept for diagnosis, never pending again.
REFUSED_STATUS: Final[str] = "refused"

_RESOLVED_BY: Final[str] = "worker"


@dataclass(frozen=True, slots=True)
class HostOpConsumption:
    """What one resume consumed: the executed decisions and the updated metadata."""

    executed: tuple[Mapping[str, JsonValue], ...] = ()
    refused_reason: str = ""
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return bool(self.refused_reason)


def consume_pending_host_op_resolutions(
    *,
    task_id: str,
    runtime: HostOpApprovalRuntime | None = None,
    db_path: Path | None = None,
) -> HostOpConsumption:
    """Execute the task's pending host-op decisions once; zero actions when unable."""
    resolved_db = Path(db_path) if db_path is not None else Path(get_db_path())
    metadata = _load_metadata(resolved_db, str(task_id))
    records = _records(metadata.get("host_op_resolutions"))
    pending = _unique_pending(records)
    grants = _strings(metadata.get("host_op_session_grants"))
    if not pending and not grants:
        return HostOpConsumption(metadata=metadata)
    active = runtime
    if active is None:
        active = build_host_op_runtime(session_id=str(metadata.get("session") or ""))
    if active is None:
        return HostOpConsumption(refused_reason=REASON_GATE_UNAVAILABLE, metadata=metadata)
    if grants:
        active.register_session_grants(grants, session_id=str(metadata.get("session") or ""))
    if not pending:
        return HostOpConsumption(metadata=metadata)

    claimed = dict(metadata)
    claimed["host_op_resolutions"] = [
        _claim_record(record) if _is_pending(record) else record for record in records
    ]
    _write_metadata(resolved_db, str(task_id), claimed)

    executed: list[Mapping[str, JsonValue]] = []
    outcomes: dict[str, Mapping[str, JsonValue]] = {}
    for record in pending:
        outcome = _execute_stored(active, record, metadata=metadata, task_id=str(task_id))
        outcomes[str(record.get("request_id") or "")] = outcome
        if outcome.get("executed") is True:
            executed.append(outcome)
    final = dict(claimed)
    final["host_op_resolutions"] = [
        outcomes.get(str(record.get("request_id") or ""), record) for record in records
    ]
    _write_metadata(resolved_db, str(task_id), final)
    return HostOpConsumption(executed=tuple(executed), metadata=final)


def _execute_stored(
    runtime: HostOpApprovalRuntime,
    record: Mapping[str, JsonValue],
    *,
    metadata: Mapping[str, JsonValue],
    task_id: str,
) -> Mapping[str, JsonValue]:
    try:
        outcome = runtime.gate.resolve_stored(
            record,
            resolved_by=_RESOLVED_BY,
            session_id=str(metadata.get("session") or ""),
            task_id=task_id,
        )
    except IsolationError as exc:
        logger.warning("host-op resume: refusing a pending record (%s)", exc.reason)
        return {
            **dict(record),
            "status": REFUSED_STATUS,
            "executed": False,
            "reason": exc.reason,
            "resolved_by": _RESOLVED_BY,
        }
    return dict(outcome.to_record())


def _claim_record(record: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return {**dict(record), "status": CONSUMING_STATUS, "resolved_by": _RESOLVED_BY}


def _is_pending(record: object) -> bool:
    return isinstance(record, Mapping) and record.get("status") == PENDING_BROKER_STATUS


def _unique_pending(records: Sequence[Mapping[str, JsonValue]]) -> list[Mapping[str, JsonValue]]:
    unique: list[Mapping[str, JsonValue]] = []
    seen: set[str] = set()
    for record in records:
        if not _is_pending(record):
            continue
        request_id = str(record.get("request_id") or "")
        if request_id in seen:
            continue
        seen.add(request_id)
        unique.append(record)
    return unique


def _records(raw: object) -> list[Mapping[str, JsonValue]]:
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _strings(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _load_metadata(db_path: Path, task_id: str) -> dict[str, JsonValue]:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT metadata FROM tasks WHERE task_id = ?", (str(task_id),)
        ).fetchone()
    raw = row[0] if row else None
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def _write_metadata(db_path: Path, task_id: str, metadata: Mapping[str, JsonValue]) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE tasks SET metadata = ?, updated_at = ? WHERE task_id = ?",
            (json.dumps(dict(metadata), ensure_ascii=False), int(time.time()), str(task_id)),
        )
        conn.commit()
