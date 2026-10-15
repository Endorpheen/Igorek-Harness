"""Host-side route of the guest artifact tree into the exported workspace.

The in-guest cc-backend executor writes its per-task artifacts (the trace, stored
``.tool_results``, materialized skill assets and the ``subagents.jsonl`` journal)
under ``task.artifact_workdir``. In an isolated session the manager sends that
directory *inside the exported workspace* instead of the disposable guest
``/tmp``, so the host can read the subagent journal through the same single
virtiofs share the user files use, while the guest still creates the tree itself
(no guest code changes, no image rebuild).

Layout: the guest artifact dir of a task is ``<guest workdir>/.igorek-artifacts/
<task8>`` (the guest workdir is the exported workspace), and its host mirror is
``<host workdir>/.igorek-artifacts/<task8>``. The per-task subdirectory keeps the
journal of one task isolated from its siblings, so the host cursor that already
exists per task never re-reads another task's lines.

The manager writes one small JSON *route* file next to the host trace
(``<host artifact_workdir>/guest_artifact_workdir.json``) before the task frame
is sent. That file is the single source of truth for the mobile bridge: it never
recomputes the layout, and a dispatch that wrote no route (off mode, legacy
tasks) keeps the historical ``temp_execution_<task8>`` journal location.
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import re
from pathlib import Path

__all__ = [
    "ARTIFACT_DIRNAME",
    "ROUTE_FILENAME",
    "ROUTE_VERSION",
    "guest_artifact_dir",
    "host_artifact_dir",
    "read_artifact_route",
    "task_segment",
    "write_artifact_route",
]

logger = logging.getLogger(__name__)

#: Hidden directory the guest artifact tree is routed through inside the
#: exported workspace; the same name is excluded from user-facing listings.
ARTIFACT_DIRNAME = ".igorek-artifacts"
#: Route file written into the host artifact dir of one dispatch.
ROUTE_FILENAME = "guest_artifact_workdir.json"
ROUTE_VERSION = 1
_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]+")


def task_segment(task_id: str) -> str:
    """Stable per-task directory segment (mirrors ``temp_execution_<task8>``)."""
    safe = _UNSAFE_SEGMENT.sub("_", str(task_id or "")[:8]).strip("._")
    return safe or "task"


def guest_artifact_dir(guest_workdir: str, task_id: str) -> str:
    """Guest-side artifact dir of one task, under its exported workspace root."""
    root = posixpath.normpath(str(guest_workdir or "/"))
    return posixpath.join(root, ARTIFACT_DIRNAME, task_segment(task_id))


def host_artifact_dir(host_workdir: str | os.PathLike[str], task_id: str) -> Path:
    """Host mirror of :func:`guest_artifact_dir` for the same exported workspace."""
    return Path(host_workdir) / ARTIFACT_DIRNAME / task_segment(task_id)


def write_artifact_route(
    host_artifact_workdir: str | os.PathLike[str],
    mapped_dir: str | os.PathLike[str],
) -> None:
    """Write the host artifact route for one dispatch; never fails the task.

    The route is a trace-level concern: an unwritable artifact dir must not
    abort a turn, so a write failure is logged and dropped.
    """
    target = Path(host_artifact_workdir) / ROUTE_FILENAME
    payload = {
        "version": ROUTE_VERSION,
        "host_artifact_workdir": str(Path(mapped_dir)),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, target)
    except OSError as exc:
        logger.warning(
            "isolation artifact route not written (%s): %s", target, type(exc).__name__
        )


def read_artifact_route(host_artifact_workdir: str | os.PathLike[str]) -> Path | None:
    """Read the mapped host artifact dir, or ``None`` for a legacy dispatch.

    A missing, malformed or foreign-version route is not an error: the caller
    falls back to the historical host trace directory.
    """
    path = Path(host_artifact_workdir) / ROUTE_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or parsed.get("version") != ROUTE_VERSION:
        return None
    mapped = parsed.get("host_artifact_workdir")
    if not isinstance(mapped, str) or not mapped.strip():
        return None
    candidate = Path(mapped)
    if not candidate.is_absolute():
        return None
    return candidate
