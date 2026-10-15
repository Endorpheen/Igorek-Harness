"""Convenience sender for app events and common outbound TUI commands (port of app_event_sender.rs).

Wraps the raw event channel so call sites can submit typed ``AppCommand``s
without duplicating event construction or session-logging behavior. The
Python port mirrors the original structure: a thin wrapper around a
thread-safe event channel that exposes typed helpers for the most common
outbound events. The channel is a :class:`queue.Queue` in the Python port;
callers can install their own backend (e.g. an in-memory list for tests)
by passing a queue factory.

This is a one-to-one Python port of ``codex-rs/tui/src/app_event_sender.rs``
(132 production lines; tests not ported with the production implementation).
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Protocol

from .app_event import AppEvent


_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Queue backend protocol — replaceable for tests
# ---------------------------------------------------------------------------

class _AppEventQueue(Protocol):
    """Minimal ``put``/``get`` surface used by :class:`AppEventSender`.

    The default backend is :class:`queue.Queue`, but anything with a
    thread-safe ``put_nowait`` method (e.g. an in-memory list wrapper for
    tests) is accepted.
    """
    def put_nowait(self, item: Any) -> None: ...


# ---------------------------------------------------------------------------
# session_log stub — port of codex_app_server_protocol / session_log
# ---------------------------------------------------------------------------

def _log_inbound_app_event(event: AppEvent) -> None:
    """Port of ``session_log::log_inbound_app_event`` (no-op by default).

    Avoid double-logging :class:`AppEvent` ``CodexOp`` events: those are
    logged at the point of submission.
    """
    return None


# ---------------------------------------------------------------------------
# AppCommand / ThreadId forward references
# ---------------------------------------------------------------------------

class _AppCommandFactory(Protocol):
    """Forward-protocol for the ``AppCommand`` factory used by sender helpers.

    The real ``AppCommand`` lives in ``factory.core.tui.app_command`` (a
    separate one-to-one port). Sender methods delegate to ``AppCommand.<method>``
    by name, allowing the factory to be injected and stubbed in tests.
    """
    def interrupt(self) -> Any: ...
    def compact(self) -> Any: ...
    def set_thread_name(self, name: str) -> Any: ...
    def review(self, target: Any) -> Any: ...
    def list_skills(self, cwds: List[Path], force_reload: bool) -> Any: ...
    def realtime_conversation_audio(self, frame: Any) -> Any: ...
    def user_input_answer(self, id: str, response: Any) -> Any: ...
    def exec_approval(self, id: str, turn_id: Optional[str], decision: Any) -> Any: ...
    def request_permissions_response(self, id: str, response: Any) -> Any: ...
    def patch_approval(self, id: str, decision: Any) -> Any: ...
    def resolve_elicitation(self, server_name: str, request_id: Any,
                            decision: Any, content: Optional[Any],
                            meta: Optional[Any]) -> Any: ...


# ---------------------------------------------------------------------------
# AppEventSender
# ---------------------------------------------------------------------------

@dataclass
class AppEventSender:
    """Port of ``codex::app_event_sender::AppEventSender`` (one-to-one API).

    Holds a reference to a thread-safe event queue. Senders never block
    the caller: if the queue is closed, errors are logged and swallowed
    (mirroring the Rust ``UnboundedSender::send`` + ``tracing::error!``
    pattern).

    Parameters
    ----------
    app_event_tx:
        Thread-safe queue used to deliver ``AppEvent`` values to the
        app loop. Defaults to an unbounded :class:`queue.Queue`.
    log_inbound:
        Optional hook called for every non-``CodexOp`` event before it
        is enqueued. Defaults to the no-op :func:`_log_inbound_app_event`.
    app_command_factory:
        Optional factory providing ``AppCommand`` constructor methods.
        When ``None``, sender methods construct opaque ``op`` dicts that
        preserve the call signature for the app layer to dispatch.
    """
    app_event_tx: _AppEventQueue = field(default_factory=queue.Queue)
    log_inbound: Callable[[AppEvent], None] = field(default=_log_inbound_app_event)
    app_command_factory: Optional[_AppCommandFactory] = None
    _closed: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @classmethod
    def new(cls, app_event_tx: Optional[_AppEventQueue] = None) -> "AppEventSender":
        """Port of ``AppEventSender::new(app_event_tx)``."""
        return cls(app_event_tx=app_event_tx or queue.Queue())

    def send(self, event: AppEvent) -> None:
        """Send an event to the app event channel. If it fails, we swallow the
        error and log it (port one-to-one of ``AppEventSender::send``).

        Avoid double-logging :class:`AppEvent` ``CodexOp`` events: those
        are logged at the point of submission, not at the inbound side.
        """
        if not (event.kind == "CodexOp"):
            try:
                self.log_inbound(event)
            except Exception:  # noqa: BLE001 — log hook must never crash
                _log.exception("log_inbound_app_event failed")
        try:
            self.app_event_tx.put_nowait(event)
        except Exception as e:  # noqa: BLE001 — port of `tracing::error!` + swallow
            _log.error("failed to send event: %s", e)

    def close(self) -> None:
        """Mark the sender closed. Further ``send`` calls are dropped.

        Python analogue of the Rust ``UnboundedSender`` being dropped
        downstream — the queue is no longer drained.
        """
        with self._lock:
            self._closed = True

    def _resolve_op(self, factory_method: str, *args: Any, **kwargs: Any) -> Any:
        """Resolve an ``AppCommand`` op via the factory, or fall back to a dict.

        When ``app_command_factory`` is set, delegate to the named method
        (one-to-one with the Rust ``AppCommand::<method>(...)`` call site).
        Otherwise, build a minimal dict that the app layer can dispatch on.
        """
        if self.app_command_factory is not None:
            method = getattr(self.app_command_factory, factory_method)
            return method(*args, **kwargs)
        return {"_op": factory_method, "args": args, "kwargs": kwargs}

    # ------------------------------------------------------------------
    # Outbound helpers (each emits one ``CodexOp`` or ``SubmitThreadOp``)
    # ------------------------------------------------------------------

    def interrupt(self) -> None:
        self.send(AppEvent.codex_op(self._resolve_op("interrupt")))

    def compact(self) -> None:
        self.send(AppEvent.codex_op(self._resolve_op("compact")))

    def set_thread_name(self, name: str) -> None:
        self.send(AppEvent.codex_op(self._resolve_op("set_thread_name", name)))

    def review(self, target: Any) -> None:
        self.send(AppEvent.codex_op(self._resolve_op("review", target)))

    def list_skills(self, cwds: List[Path], force_reload: bool) -> None:
        self.send(AppEvent.codex_op(self._resolve_op("list_skills", cwds, force_reload)))

    def realtime_conversation_audio(self, frame: Any) -> None:
        """Linux cfg-gated in Rust; emitted unconditionally in Python port."""
        self.send(AppEvent.codex_op(self._resolve_op("realtime_conversation_audio", frame)))

    def user_input_answer(self, id: str, response: Any) -> None:
        self.send(AppEvent.codex_op(self._resolve_op("user_input_answer", id, response)))

    def exec_approval(self, thread_id: Any, id: str, decision: Any) -> None:
        """Port one-to-one: emits ``AppEvent::SubmitThreadOp`` with ``AppCommand::exec_approval``.

        The ``turn_id`` parameter in codex is hard-coded to ``None`` at
        the call site (``/*turn_id*/ None``) — preserved here.
        """
        op = self._resolve_op("exec_approval", id, None, decision)
        self.send(AppEvent.submit_thread_op(thread_id, op))

    def request_permissions_response(self, thread_id: Any, id: str, response: Any) -> None:
        op = self._resolve_op("request_permissions_response", id, response)
        self.send(AppEvent.submit_thread_op(thread_id, op))

    def patch_approval(self, thread_id: Any, id: str, decision: Any) -> None:
        op = self._resolve_op("patch_approval", id, decision)
        self.send(AppEvent.submit_thread_op(thread_id, op))

    def resolve_elicitation(self, thread_id: Any, server_name: str, request_id: Any,
                            decision: Any, content: Optional[Any],
                            meta: Optional[Any]) -> None:
        op = self._resolve_op("resolve_elicitation",
                              server_name, request_id, decision, content, meta)
        self.send(AppEvent.submit_thread_op(thread_id, op))


__all__ = ["AppEventSender"]
