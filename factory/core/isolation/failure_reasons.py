"""Machine-readable failure codes for KVM isolation infrastructure faults.

Kept dependency-free so both the host worker and the isolation manager can
import it without an import cycle. A failure that carries one of these codes
means the guest/VM lost the ability to run the turn (the process/VM stopped
making progress), as opposed to a model or tool decision. Host-side consumers
use this structural marker to keep such a failure visible and to avoid silently
re-running it as if it were an ordinary model retry.
"""

from __future__ import annotations

from typing import Final

#: Terminal isolation failure tokens that describe infrastructure loss, not a
#: model/tool outcome. ``guest_stalled`` is the fast liveness detector;
#: ``result_timeout`` stays as the last backstop; ``cancel_timeout`` is a stop
#: whose ack never arrived; ``channel_lost``/``vm_lost`` are transport/VM loss.
INFRA_FAILURE_REASONS: Final[frozenset[str]] = frozenset(
    {
        "guest_stalled",
        "result_timeout",
        "cancel_timeout",
        "channel_lost",
        "vm_lost",
    }
)

__all__ = ["INFRA_FAILURE_REASONS"]
