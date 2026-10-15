"""VT100 mouse-capture output adapter.

The stock prompt_toolkit ``Vt100_Output`` enables ``1000h``, ``1003h``,
``1015h`` and ``1006h``. The TZ requires a narrower contract:

* enable ``1000h``, ``1002h`` (drag), ``1006h`` (SGR);
* never enable ``1003h`` (any-motion) or ``1015h`` (urxvt);
* disable ``1000l``, ``1002l``, ``1006l`` and defensively ``1003l``.

The adapter wraps the real output instance and overrides only the two
capture methods, delegating everything else (stream, size, colour depth,
``TERM``, CPR support) to the wrapped object. Non-VT100 outputs
(``DummyOutput``, ``PlainTextOutput``, ...) are left untouched and report
``supports_capture == False`` so the controller never enables the mouse
there.
"""
from __future__ import annotations

from typing import Optional

try:
    from prompt_toolkit.output.vt100 import Vt100_Output
except Exception:  # pragma: no cover - prompt_toolkit always present in TUI
    Vt100_Output = None  # type: ignore


ENABLE_SEQUENCES: tuple[str, ...] = ("\x1b[?1000h", "\x1b[?1002h", "\x1b[?1006h")
DISABLE_SEQUENCES: tuple[str, ...] = (
    "\x1b[?1000l",
    "\x1b[?1002l",
    "\x1b[?1006l",
    "\x1b[?1003l",
)


def _is_vt100(output: object) -> bool:
    if Vt100_Output is None or output is None:
        return False
    return isinstance(output, Vt100_Output)


class MouseCaptureOutput:
    """Proxy around a prompt_toolkit output that narrows mouse capture."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self._capture_enabled = False
        self._ever_enabled = False

    # -- delegation --------------------------------------------------------
    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    @property
    def inner(self) -> object:
        return self._inner

    @property
    def supports_capture(self) -> bool:
        return _is_vt100(self._inner)

    @property
    def capture_enabled(self) -> bool:
        return self._capture_enabled

    @property
    def ever_enabled(self) -> bool:
        return self._ever_enabled

    # -- capture -----------------------------------------------------------
    def _write(self, text: str) -> None:
        writer = getattr(self._inner, "write_raw", None)
        if callable(writer):
            writer(text)
        else:
            self._inner.write(text)  # type: ignore[attr-defined]

    def enable_mouse_support(self) -> None:
        if not self.supports_capture:
            return
        for seq in ENABLE_SEQUENCES:
            self._write(seq)
        self._capture_enabled = True
        self._ever_enabled = True

    def disable_mouse_support(self) -> None:
        if not self.supports_capture:
            return
        for seq in DISABLE_SEQUENCES:
            self._write(seq)
        flush = getattr(self._inner, "flush", None)
        if callable(flush):
            flush()
        self._capture_enabled = False


def wrap_output_for_mouse(output: object) -> Optional[MouseCaptureOutput]:
    """Return a capturing adapter for ``output``, or ``None`` if unsupported."""
    if not _is_vt100(output):
        return None
    if isinstance(output, MouseCaptureOutput):
        return output
    return MouseCaptureOutput(output)


# Register the proxy as a virtual ``Output`` subclass so prompt_toolkit's
# ``isinstance(output, Output)`` assertions (e.g. in run_in_terminal) pass.
try:  # pragma: no cover - registration is environment dependent
    from prompt_toolkit.output.base import Output as _OutputBase

    _OutputBase.register(MouseCaptureOutput)
except Exception:
    pass


__all__ = [
    "DISABLE_SEQUENCES",
    "ENABLE_SEQUENCES",
    "MouseCaptureOutput",
    "wrap_output_for_mouse",
]
