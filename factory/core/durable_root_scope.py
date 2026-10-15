"""Path scoping for the configured durable data root (plan todo 43).

The changed-file tracker and the checkpoint manager must ignore the runtime's
own artifacts when they are produced inside a tracked work tree (checkpoint
shadow repos, ``session_changes`` records). In isolation/kvm mode the guest
runner hands the executor ``data_root=<workspace>/.igorek-host-data``
(:func:`factory.core.isolation.persistence.durable_data_root`), so those
artifacts live inside the exported workspace; in off mode the data root is the
process data root outside every workdir and nothing may be excluded - that is
what keeps off mode byte-identical to the pre-isolation behaviour.

The scoping signal is the data-root/workdir relation itself, so there is no
global reserved dirname and no mode flag that can drift from the real layout.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["durable_root_within_workdir", "is_within_relative"]


def durable_root_within_workdir(*, workdir: str, data_root: str | None) -> str | None:
    """The workdir-relative durable root when ``data_root`` is nested inside it.

    Returns a posix relative path such as ``.igorek-host-data``, or ``None``
    when no data root is configured, it lies outside the workdir (off mode), or
    it *is* the workdir itself (excluding the whole tree is never intended).
    """
    if not data_root:
        return None
    try:
        root = Path(data_root).resolve()
        tree = Path(workdir).resolve()
        relative = root.relative_to(tree)
    except (ValueError, OSError):
        return None
    if not relative.parts:
        return None
    return relative.as_posix()


def is_within_relative(relative_path: str, root_relative: str) -> bool:
    """True when ``relative_path`` is ``root_relative`` or lives under it."""
    root_parts = Path(str(root_relative or "")).parts
    if not root_parts:
        return False
    parts = Path(str(relative_path or "")).parts
    return len(parts) >= len(root_parts) and parts[: len(root_parts)] == root_parts
