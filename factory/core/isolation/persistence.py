"""Persistence classes of one isolated session (plan todo 26).

The host owns durable state; the guest holds none. Every artifact of a session
belongs to exactly one of four documented classes:

* ``base_image`` - permanent, host: the frozen guest image; the only source of
  the guest rootfs, rebuilt only by the image recipe;
* ``overlay`` - disposable, host: the per-session copy-on-write layer; in-guest
  installs, temp files and caches live here and die with the VM;
* ``workspace`` - permanent, host, shared: the user tree exported through the
  single virtiofs device *plus* the durable runtime artifacts written inside it
  (``.igorek-host-data``: checkpoints and ``session_changes``), so rollback and
  review data survive every teardown;
* ``cache`` - disposable, in-guest: uv/npm caches, ``~/.cache`` and ``/tmp``
  live on the overlay only; they are never written to a host path and never
  restored.

The trace file is host-authoritative as well: the guest streams trace frames
and :class:`factory.core.isolation.relay.TraceRelay` appends them to the host
artifact dir, so it survives teardown independently of the guest.

:func:`durable_data_root` is the one path convention both halves share: the
guest runner hands the executor this root (``data_root``), so the checkpoint
shadow repos and the ``session_changes`` records are produced inside the
exported workspace and land on the host. The host computes the same relative
root from its own workspace realpath. Nothing here touches the filesystem.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from collections.abc import Mapping
from typing import Final

__all__ = [
    "DURABLE_DATA_DIRNAME",
    "PERSISTENCE_POLICY",
    "PersistenceClass",
    "PersistenceRule",
    "durable_data_root",
    "persistence_rule",
    "persistence_summary",
]

#: Hidden directory inside the exported workspace that carries the guest's
#: durable runtime artifacts (checkpoints, ``session_changes``) onto the host.
DURABLE_DATA_DIRNAME: Final[str] = ".igorek-host-data"


class PersistenceClass(StrEnum):
    """The four persistence classes of one isolated session (todo 26)."""

    BASE_IMAGE = "base_image"
    OVERLAY = "overlay"
    WORKSPACE = "workspace"
    CACHE = "cache"


@dataclass(frozen=True, slots=True)
class PersistenceRule:
    """What one persistence class is and where it lives.

    ``locations`` are the roots that carry the class, guest-visible for the
    in-guest classes (cache) and host-visible for the host classes; a location
    is a label for documentation/status, never a path this module touches.
    """

    durable: bool
    locations: tuple[str, ...]
    description: str


PERSISTENCE_POLICY: Final[Mapping[PersistenceClass, PersistenceRule]] = MappingProxyType(
    {
        PersistenceClass.BASE_IMAGE: PersistenceRule(
            durable=True,
            locations=("<data_root>/isolation/images/base.qcow2",),
            description=(
                "frozen guest image; the only source of the guest rootfs and "
                "rebuilt only by the image recipe"
            ),
        ),
        PersistenceClass.OVERLAY: PersistenceRule(
            durable=False,
            locations=("<overlay_dir>/<session>.qcow2",),
            description=(
                "disposable copy-on-write layer over the base image: in-guest "
                "installs, temp files and caches live here and are discarded "
                "with the VM"
            ),
        ),
        PersistenceClass.WORKSPACE: PersistenceRule(
            durable=True,
            locations=(
                "<host workspace tree> (exported at /workspace)",
                f"/workspace/{DURABLE_DATA_DIRNAME} (checkpoints, session_changes)",
            ),
            description=(
                "user data plus the durable runtime artifacts written inside the "
                "shared workspace path; survives every teardown"
            ),
        ),
        PersistenceClass.CACHE: PersistenceRule(
            durable=False,
            locations=("~/.cache", "~/.local/share/uv", "/tmp"),
            description=(
                "per-session caches on the disposable overlay; never written to "
                "a host path and never restored"
            ),
        ),
    }
)


def persistence_rule(rule_class: PersistenceClass) -> PersistenceRule:
    """The documented rule for one persistence class."""
    return PERSISTENCE_POLICY[rule_class]


def persistence_summary() -> tuple[str, ...]:
    """One line per persistence class, for docs, status and evidence."""
    return tuple(
        f"{rule_class}: durable={str(rule.durable).lower()} "
        f"locations={', '.join(rule.locations)} - {rule.description}"
        for rule_class, rule in PERSISTENCE_POLICY.items()
    )


def durable_data_root(workdir: str) -> str:
    """The durable guest/host data root inside one shared workspace path.

    The guest runner hands this root to the executor (``data_root``), so the
    checkpoint shadow repos and the ``session_changes`` records are produced
    inside the exported workspace and land on the host through the single
    virtiofs device. The host computes the same relative root from its own
    workspace realpath; an empty workdir is refused instead of guessing.
    """
    text = str(workdir).strip()
    if not text:
        raise ValueError("workdir must be a non-empty path")
    return posixpath.join(posixpath.normpath(text), DURABLE_DATA_DIRNAME)
