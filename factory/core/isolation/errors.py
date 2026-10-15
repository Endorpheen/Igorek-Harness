"""The single structured failure type for the isolation package.

Every isolation module raises :class:`IsolationError` imported from here, so
``except IsolationError`` written against this module, against the package
(:mod:`factory.core.isolation`) or against any single module catches failures
raised anywhere in the package. Defining one class per module silently split
those handlers apart.
"""

from __future__ import annotations

__all__ = ["IsolationError"]


class IsolationError(RuntimeError):
    """Structured isolation failure with a machine-checkable ``reason``.

    ``reason`` is the machine-readable discriminator and may embed a payload
    after the first ``:`` (``sensitive_path_denied:~/.ssh``), which :attr:`code`
    strips; ``detail`` carries free-form context. ``str`` joins both so the
    reason is always present, and is stable for a given pair of values.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)

    @property
    def code(self) -> str:
        """Reason without its payload, e.g. ``sensitive_path_denied``."""
        return self.reason.split(":", 1)[0]
