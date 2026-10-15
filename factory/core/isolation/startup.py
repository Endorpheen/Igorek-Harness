"""Gateway-start fail-safe recovery for the isolation layer (plan todo 25).

A host process that dies mid-task leaves three things behind: a libvirt domain
definition for a VMM that is gone, a qcow2 overlay no live lease covers, and a
task row stuck in ``processing``. This module owns the one gateway-start pass
that reconciles all three:

* every orphan domain/overlay/endpoint is reaped through the existing
  :meth:`IsolationManager.reap_orphans` - there is no second reaper;
* a task whose session has no live overlay lease is marked terminal ``fail``
  with a clear ``error_message``, so the UI shows a real error instead of an
  eternal "processing";
* a task parked in ``waiting_approval`` retains its warm session VM; only
  ``processing`` rows without a live session lease are candidates for recovery;
* a task whose overlay is still leased by a live process (a sibling worker, or
  an earlier gateway) is left alone, and so is a task younger than the grace
  window: it may belong to a worker that is still starting its VM, before the
  lease exists. The watchdog remains the backstop for those.

The pass is fail-safe by contract: :func:`recover_isolation_startup` never
raises and never degrades isolation mode ``off`` into any side effect (no
manager, no reaper, no DB write, no probe).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..runtime import get_db_path
from .config import MODE_KVM, IsolationConfig
from .manager import IsolationManager

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_STARTUP_GRACE_S",
    "FAILED_BY_RECOVERY",
    "StartupRecovery",
    "recover_isolation_startup",
]

#: A ``processing`` row younger than this may belong to a live worker that has
#: not started its VM yet; the watchdog owns anything older than its own timeout.
DEFAULT_STARTUP_GRACE_S: Final[float] = 60.0
#: Machine token inside ``error_message`` for a task failed by this recovery.
FAILED_BY_RECOVERY: Final[str] = "isolation_vm_lost"


@dataclass(frozen=True, slots=True)
class StartupRecovery:
    """What one gateway-start pass observed and changed."""

    mode: str
    reaped: int
    failed_tasks: tuple[str, ...]
    error: str

    @property
    def ok(self) -> bool:
        return not self.error


def recover_isolation_startup(
    env: Mapping[str, str] | None = None,
    *,
    manager: IsolationManager | None = None,
    db_path: str | Path | None = None,
    now: int | None = None,
    grace_s: float = DEFAULT_STARTUP_GRACE_S,
) -> StartupRecovery:
    """Reap isolation leftovers and fail orphaned running tasks; never raises.

    ``env`` is the environment to resolve the isolation mode from (the real
    process environment by default); ``manager`` is an injection seam for tests
    and for callers that already own a manager. Off mode returns before any
    side effect. Any failure while reaping or scanning is collected into
    ``StartupRecovery.error`` and logged - the gateway must start regardless.
    """
    source = os.environ if env is None else env
    try:
        config = IsolationConfig.from_env(source)
    except Exception as exc:  # config boundary: a bad config must not stop the host
        logger.warning("isolation startup: cannot resolve the config: %s", exc)
        return StartupRecovery(mode="", reaped=0, failed_tasks=(), error=f"config_error: {exc}")
    if config.mode != MODE_KVM:
        return StartupRecovery(mode=config.mode, reaped=0, failed_tasks=(), error="")

    errors: list[str] = []
    active = manager
    if active is None:
        try:
            active = IsolationManager(config)
        except Exception as exc:  # host boundary: libvirt/lease state may be unavailable
            logger.warning("isolation startup: cannot build the manager: %s", exc)
            errors.append(f"manager_init_failed: {exc}")
            active = None
    reaped = 0
    if active is not None:
        try:
            reaped = max(0, int(active.reap_orphans()))
        except Exception as exc:  # the reaper is fail-safe: report, never raise
            logger.warning("isolation startup: orphan reaping failed: %s", exc)
            errors.append(f"reap_failed: {exc}")
    failed: tuple[str, ...] = ()
    try:
        failed = _fail_orphaned_tasks(
            db_path=db_path,
            manager=active,
            now=now,
            grace_s=grace_s,
        )
    except Exception as exc:  # DB boundary: the host stays healthy either way
        logger.warning("isolation startup: cannot reconcile orphaned tasks: %s", exc)
        errors.append(f"task_scan_failed: {exc}")
    return StartupRecovery(
        mode=config.mode,
        reaped=reaped,
        failed_tasks=failed,
        error="; ".join(errors),
    )


def _fail_orphaned_tasks(
    *,
    db_path: str | Path | None,
    manager: IsolationManager | None,
    now: int | None,
    grace_s: float,
) -> tuple[str, ...]:
    """Mark ``processing`` tasks whose VM is provably gone as failed.

    Only the ``processing`` status is considered, so a task parked in
    ``waiting_approval`` task is not a recovery candidate and can never be failed here.
    Within ``processing``, a task is orphaned only when it is older than the
    grace window *and* no live overlay lease covers its session.
    """
    if manager is None:
        return ()
    moment = int(time.time()) if now is None else int(now)
    grace = max(0.0, float(grace_s))
    path = str(db_path) if db_path is not None else get_db_path()
    with closing(sqlite3.connect(path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            rows = conn.execute(
                """
                SELECT task_id, metadata, COALESCE(started_at, updated_at, created_at, 0)
                FROM tasks
                WHERE status = 'processing'
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return ()
        orphans = [
            str(task_id)
            for task_id, metadata, activity_ts in rows
            if moment - int(activity_ts or 0) > grace
            and not manager.owns_overlay(_session_of(metadata, str(task_id)))
        ]
        failed: list[str] = []
        for task_id in orphans:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'fail', error_message = ?, completed_at = ?, updated_at = ?
                WHERE task_id = ? AND status = 'processing'
                """,
                (
                    _failure_message(task_id),
                    moment,
                    moment,
                    task_id,
                ),
            )
            if cursor.rowcount:
                failed.append(task_id)
        conn.commit()
    return tuple(failed)


def _session_of(metadata_raw: object, task_id: str) -> str:
    """Session id a task runs in; mirrors the manager's dispatch precedence."""
    if isinstance(metadata_raw, str) and metadata_raw.strip():
        try:
            payload = json.loads(metadata_raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            session = payload.get("session")
            if isinstance(session, str) and session.strip():
                return session.strip()
    return task_id


def _failure_message(task_id: str) -> str:
    return (
        f"{FAILED_BY_RECOVERY}: the VM for this task is gone (the host process "
        f"restarted mid-task); task {task_id} was marked failed by the isolation "
        "fail-safe recovery"
    )
