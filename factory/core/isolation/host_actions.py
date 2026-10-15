"""Typed host commands behind the broker catalog (plan todo 20).

The broker (todo 19) owns the envelope, the typed argument schemas and the host
policy decision; this module owns what happens after approval:

* ``runtime.restart`` - ``systemctl --user restart <host-configured units>``,
  where the unit names come from :data:`DEFAULT_RESTART_UNITS` and the guest
  only picks the closed CHOICE token ``gateway`` or ``runtime``; this op is the
  documented replacement for the old ``systemd-run --user`` reach;
* ``runtime.update`` - the documented host-side safe update path
  (``factory/scripts/update_safe.sh`` pattern): ``bash <script>`` with a fixed
  argv and no guest arguments; a deployment points
  ``IGOREK_ISOLATION_UPDATE_SCRIPT`` at its machine-appropriate wrapper, and a
  missing script **fails closed** (``update_script_missing``) running nothing;
* ``vault.fetch`` - delegated to
  :func:`factory.core.isolation.host_vault.fetch_vault_secret`.

Injection: :class:`HostActionLayer` takes a :class:`HostCommandRunner` and the
Vault boundaries, so tests substitute recording fakes and never restart a real
unit or run the real update script. :class:`SubprocessHostRunner` is the only
place a process is spawned, always an argv list with ``shell=False``. Typed
failures carry a stable reason (:class:`HostActionError`) plus an ``executed``
bit the broker maps into the response and the audit trail.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol

from .broker_ops import BROKER_VERSION, ArgValue, HostActionError, HostOpAction
from .channel import JsonValue
from .host_vault import (
    HostProcessEnvTarget,
    HostVaultEnvProvider,
    VaultEnvProvider,
    VaultEnvTarget,
    VaultFetchContext,
    fetch_vault_secret,
)

__all__ = [
    "DEFAULT_RESTART_TIMEOUT_S",
    "DEFAULT_RESTART_UNITS",
    "DEFAULT_UPDATE_TIMEOUT_S",
    "ENV_UPDATE_SCRIPT",
    "REASON_ACTION_TIMEOUT",
    "REASON_COMMAND_UNAVAILABLE",
    "REASON_INVALID_ACTION_ARGUMENTS",
    "REASON_RESTART_FAILED",
    "REASON_RESTART_TARGET_UNMAPPED",
    "REASON_UPDATE_FAILED",
    "REASON_UPDATE_SCRIPT_MISSING",
    "CommandOutcome",
    "HostActionLayer",
    "HostCommandRunner",
    "SubprocessHostRunner",
    "default_host_action_layer",
    "default_update_script",
]

logger = logging.getLogger(__name__)

#: Host-configured unit set per restart target; the guest only picks the token.
DEFAULT_RESTART_UNITS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "gateway": ("igorek-gateway.service",),
        "runtime": ("igorek-gateway.service", "igorek-mobile.service"),
    }
)
DEFAULT_RESTART_TIMEOUT_S: Final[float] = 120.0
DEFAULT_UPDATE_TIMEOUT_S: Final[float] = 600.0

#: Host-configured path of the documented safe update wrapper.
ENV_UPDATE_SCRIPT: Final[str] = "IGOREK_ISOLATION_UPDATE_SCRIPT"

REASON_RESTART_FAILED: Final[str] = "restart_failed"
REASON_RESTART_TARGET_UNMAPPED: Final[str] = "restart_target_unmapped"
REASON_UPDATE_SCRIPT_MISSING: Final[str] = "update_script_missing"
REASON_UPDATE_FAILED: Final[str] = "update_failed"
REASON_ACTION_TIMEOUT: Final[str] = "host_action_timeout"
REASON_COMMAND_UNAVAILABLE: Final[str] = "host_command_unavailable"
REASON_INVALID_ACTION_ARGUMENTS: Final[str] = "invalid_action_arguments"


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """The captured result of one host command."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class HostCommandRunner(Protocol):
    """Injected command boundary: an argv list in, one coded outcome out."""

    def run(
        self, argv: Sequence[str], *, timeout_s: float, cwd: Path | None = None
    ) -> CommandOutcome: ...


@dataclass(frozen=True, slots=True)
class SubprocessHostRunner:
    """Real runner: an argv list through ``subprocess`` with ``shell=False``."""

    def run(
        self, argv: Sequence[str], *, timeout_s: float, cwd: Path | None = None
    ) -> CommandOutcome:
        try:
            completed = subprocess.run(
                list(argv),
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=None if cwd is None else str(cwd),
            )
        except subprocess.TimeoutExpired as exc:
            raise HostActionError(
                REASON_ACTION_TIMEOUT, f"timeout_s={timeout_s}", executed=False
            ) from exc
        except OSError as exc:
            # The binary could not even be started; nothing ran and nothing changed.
            raise HostActionError(
                REASON_COMMAND_UNAVAILABLE, type(exc).__name__, executed=False
            ) from exc
        return CommandOutcome(completed.returncode, completed.stdout or "", completed.stderr or "")


@dataclass(frozen=True, slots=True)
class HostActionLayer:
    """The typed actions behind the catalog, wired from injectable boundaries."""

    runner: HostCommandRunner
    vault: VaultEnvProvider
    vault_target: VaultEnvTarget
    session_id: str = ""
    units: Mapping[str, tuple[str, ...]] = DEFAULT_RESTART_UNITS
    update_script: Path | None = None
    restart_timeout_s: float = DEFAULT_RESTART_TIMEOUT_S
    update_timeout_s: float = DEFAULT_UPDATE_TIMEOUT_S

    def actions(self) -> Mapping[str, HostOpAction]:
        """Exactly one typed function per catalog operation."""
        return {
            "runtime.status": self.runtime_status,
            "runtime.restart": self.restart_runtime,
            "runtime.update": self.update_runtime,
            "vault.fetch": self.fetch_vault_secret,
        }

    def runtime_status(self, args: Mapping[str, ArgValue]) -> Mapping[str, JsonValue]:
        """Read-only capability snapshot; touches nothing on the host."""
        return {"broker_version": BROKER_VERSION, "ops": sorted(self.actions())}

    def restart_runtime(self, args: Mapping[str, ArgValue]) -> Mapping[str, JsonValue]:
        """Restart the host units configured for the requested target."""
        target = str(args.get("target") or "")
        units = tuple(self.units.get(target, ()))
        if not units:
            raise HostActionError(
                REASON_RESTART_TARGET_UNMAPPED, target or "<missing>", executed=False
            )
        outcome = self.runner.run(
            ["systemctl", "--user", "restart", *units], timeout_s=self.restart_timeout_s
        )
        if outcome.returncode != 0:
            raise HostActionError(REASON_RESTART_FAILED, f"{target}:rc={outcome.returncode}")
        return {"target": target, "units": list(units), "restarted": True}

    def update_runtime(self, args: Mapping[str, ArgValue]) -> Mapping[str, JsonValue]:
        """Run the host-configured safe update script; no argument ever crosses."""
        script = self.update_script
        if script is None or not script.is_file():
            raise HostActionError(REASON_UPDATE_SCRIPT_MISSING, "", executed=False)
        outcome = self.runner.run(["bash", str(script)], timeout_s=self.update_timeout_s)
        if outcome.returncode != 0:
            raise HostActionError(REASON_UPDATE_FAILED, f"rc={outcome.returncode}")
        return {"updated": True, "update_script": script.name}

    def fetch_vault_secret(self, args: Mapping[str, ArgValue]) -> Mapping[str, JsonValue]:
        """Materialize one service's env host-side; return metadata only."""
        return fetch_vault_secret(
            args,
            VaultFetchContext(
                vault=self.vault, vault_target=self.vault_target, session_id=self.session_id
            ),
        )


def default_update_script() -> Path:
    """The repo-relative documented safe update script (a deployment overrides it)."""
    return Path(__file__).resolve().parents[2] / "scripts" / "update_safe.sh"


def default_host_action_layer(*, session_id: str = "") -> HostActionLayer:
    """Production layer: real runner, real Vault provider, host-configured script."""
    raw_script = str(os.environ.get(ENV_UPDATE_SCRIPT) or "").strip()
    script = Path(raw_script).expanduser() if raw_script else default_update_script()
    return HostActionLayer(
        runner=SubprocessHostRunner(),
        vault=HostVaultEnvProvider(),
        vault_target=HostProcessEnvTarget(),
        session_id=session_id,
        update_script=script,
    )
