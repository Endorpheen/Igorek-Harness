"""Tamper-resistance for in-guest probe reports (plan todo 28, T13 residual).

The live probe reports (``probe_*.txt``) are written inside the shared
workspace, so the guest - including a prompt-injected agent that controls the
in-guest Bash tool - can overwrite them. A host that read such a report as
evidence of a clean boundary could be fed a forged "everything is absent" file.

This module is the host-side gate every live report passes before it is read as
evidence:

* the host issues one **single-use challenge** per probe before the guest script
  runs (:meth:`ProbeLedger.issue`); the challenge carries a fresh
  ``secrets.token_hex`` nonce and the host-side issue time;
* the guest script prints ``PROBE_NONCE=<nonce>`` into its report, and
  :meth:`ProbeLedger.verify` accepts a report only when the nonce is present,
  current (no second or foreign nonce line), the artifact mtime is not older
  than the challenge (a pre-planted report is stale), the challenge was never
  consumed before (a replayed report is refused) and every host-observed fact
  supplied by the caller holds;
* a successful verification consumes the challenge, so one issued nonce can
  never back two verdicts.

Honest limit, documented rather than hidden: the script text (and therefore the
nonce) is readable inside the guest by the same uid that runs the probe, so this
cannot stop a hostile in-guest agent from *authoring* a report. What it closes is
accepting a report that no run issued, a report from an earlier run, a stale or
replayed report, and a report contradicting a host-observed fact. The
authoritative negative probes are therefore the structural host-side checks; the
live half additionally pairs every report with host observations the guest
cannot fake (host-side canaries, host transient units, host control sockets, the
domain XML).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "NONCE_LINE_PREFIX",
    "REASON_ARTIFACT_MISSING",
    "REASON_HOST_OBSERVATION",
    "REASON_NONCE_MISMATCH",
    "REASON_NONCE_MISSING",
    "REASON_REPLAYED",
    "REASON_STALE",
    "REASON_UNISSUED",
    "ProbeChallenge",
    "ProbeLedger",
    "ProbeReportRejected",
    "ProbeVerdict",
]

#: Report line that carries the host-issued challenge nonce.
NONCE_LINE_PREFIX: Final[str] = "PROBE_NONCE="

REASON_UNISSUED: Final[str] = "probe_report_unissued"
REASON_NONCE_MISSING: Final[str] = "probe_report_nonce_missing"
REASON_NONCE_MISMATCH: Final[str] = "probe_report_nonce_mismatch"
REASON_STALE: Final[str] = "probe_report_stale"
REASON_REPLAYED: Final[str] = "probe_report_replayed"
REASON_ARTIFACT_MISSING: Final[str] = "probe_report_artifact_missing"
REASON_HOST_OBSERVATION: Final[str] = "host_observation_failed"


class ProbeReportRejected(Exception):
    """A guest report failed the tamper check; callers must treat this as a FAIL."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = str(reason)
        self.detail = str(detail)

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


@dataclass(frozen=True, slots=True)
class ProbeChallenge:
    """One host-issued, single-use probe challenge."""

    probe: str
    nonce: str
    issued_at: float

    @property
    def line(self) -> str:
        return f"{NONCE_LINE_PREFIX}{self.nonce}"


@dataclass(frozen=True, slots=True)
class ProbeVerdict:
    """The tamper-check outcome; ``reason`` is a stable machine code."""

    probe: str
    ok: bool
    reason: str
    detail: str = ""


def _default_nonce() -> str:
    return secrets.token_hex(16)


@dataclass
class ProbeLedger:
    """Per-run challenge ledger: issue before the guest runs, verify after."""

    clock: Callable[[], float] = time.time
    nonce_factory: Callable[[], str] = _default_nonce
    _issued: dict[str, ProbeChallenge] = field(default_factory=dict, init=False)
    _consumed: set[str] = field(default_factory=set, init=False)

    def issue(self, probe: str) -> ProbeChallenge:
        """Issue the one challenge for ``probe``; a duplicate issue is refused."""
        name = str(probe).strip()
        if not name:
            raise ValueError("probe name must be a non-empty string")
        if name in self._issued:
            raise ValueError(f"challenge for {name!r} was already issued in this run")
        challenge = ProbeChallenge(name, self.nonce_factory(), float(self.clock()))
        self._issued[name] = challenge
        return challenge

    def challenges(self) -> dict[str, ProbeChallenge]:
        """Snapshot of the run's issued challenges (for rendering/evidence)."""
        return dict(self._issued)

    def verify(
        self,
        probe: str,
        report: str,
        *,
        artifact_mtime: float | None,
        host_observations: Mapping[str, bool] | None = None,
    ) -> ProbeVerdict:
        """Check one report against its challenge; consume the challenge on success."""
        name = str(probe)
        challenge = self._issued.get(name)
        if challenge is None:
            return ProbeVerdict(name, False, REASON_UNISSUED, "no challenge was issued for this probe")
        if name in self._consumed:
            return ProbeVerdict(
                name, False, REASON_REPLAYED, f"challenge {challenge.nonce[:8]}... was already consumed"
            )
        nonces = [
            line[len(NONCE_LINE_PREFIX) :].strip()
            for line in str(report).splitlines()
            if line.startswith(NONCE_LINE_PREFIX)
        ]
        if not nonces:
            return ProbeVerdict(name, False, REASON_NONCE_MISSING, "the report carries no probe nonce")
        if len(nonces) != 1 or nonces[0] != challenge.nonce:
            return ProbeVerdict(
                name,
                False,
                REASON_NONCE_MISMATCH,
                f"{len(nonces)} nonce line(s); none matches the issued challenge",
            )
        if artifact_mtime is None:
            return ProbeVerdict(name, False, REASON_ARTIFACT_MISSING, "the report artifact does not exist")
        if float(artifact_mtime) < challenge.issued_at:
            return ProbeVerdict(
                name,
                False,
                REASON_STALE,
                f"report mtime {artifact_mtime} predates the challenge {challenge.issued_at}",
            )
        failed = sorted(name for name, ok in (host_observations or {}).items() if not ok)
        if failed:
            return ProbeVerdict(
                name, False, REASON_HOST_OBSERVATION, f"host observation(s) failed: {', '.join(failed)}"
            )
        self._consumed.add(name)
        return ProbeVerdict(name, True, "ok", f"nonce verified, artifact fresh ({artifact_mtime})")
