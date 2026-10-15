"""Host-side terminal manager: the owner of one interactive PTY per web client.

The manager is the **single host-side authority** for terminal sessions. It is
deliberately *not* part of :class:`factory.core.isolation.manager.IsolationManager`:
the task channel and the terminal have different trust models (a task frame must
never carry a shell string, a terminal is nothing but raw shell I/O) and different
lifetimes (a task VM is torn down by the idle sweeper while a terminal must
survive a browser reload). Keeping them apart means a bug in one cannot widen
the other.

Responsibilities
----------------

* **Tickets.** ``issue_ticket(session_id)`` mints a 128-bit single-use ticket
  bound to one session. The web server hands it to the browser over the paired,
  same-origin HTTPS interface; the browser then presents it on the WebSocket
  ``open`` message. A ticket is consumed exactly once (:meth:`consume_ticket`),
  expires (:data:`TICKET_TTL_S`) and is bound to the session it was minted for,
  so a stolen ticket id is useless without the paired cookie *and* unguessable
  in the first place. Tickets are held in memory only: a restart invalidates
  them, which is the safe direction.
* **Terminals.** :meth:`open_terminal` starts one PTY session through the
  injected :class:`TerminalBackend`, :meth:`input`/:meth:`resize`/:meth:`close`
  drive it, and :meth:`ack` records the browser's rendered-output cursor for the
  replay bookkeeping.
* **Limits.** One terminal per session and a bounded number of sessions
  (:data:`DEFAULT_MAX_TERMINALS`); the limits are enforced structurally, so a
  reload storm cannot spawn an unbounded number of PTYs.

Backend seam
------------

Everything process-shaped - spawning the shell in a PTY, resizing it, reading
its bytes - lives behind :class:`TerminalBackend`. Production uses
:class:`PtyTerminalBackend` (host PTY via :mod:`pty`/:mod:`fcntl`); the KVM
variant is the same protocol over the guest's vsock terminal service, and tests
inject a fake. No libvirt and no guest import happens here.
"""

from __future__ import annotations

import binascii
import errno
import logging
import os
import secrets
import signal
import struct
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final, Protocol

if os.name != 'nt':
    import fcntl
    import pty
    import termios

from .terminal_protocol import (
    DEFAULT_REPLAY_CHUNKS,
    MAX_DIMENSION,
    MAX_REPLAY_BYTES,
    MIN_DIMENSION,
    TerminalProtocolError,
    TicketId,
)

logger = logging.getLogger(__name__)

#: A ticket lives long enough for one browser handshake and no longer.
TICKET_TTL_S: Final[float] = 120.0
#: Distinct sessions with a live terminal; a host is not a terminal server farm.
DEFAULT_MAX_TERMINALS: Final[int] = 8
#: One terminal per session by design: the shell is the session's shell.
DEFAULT_DEFAULT_ROWS: Final[int] = 30
DEFAULT_DEFAULT_COLS: Final[int] = 100
#: Bound on the per-session replay ring (chunks; bytes are bounded separately).
DEFAULT_SESSION_REPLAY_CHUNKS: Final[int] = DEFAULT_REPLAY_CHUNKS


class TerminalError(Exception):
    """Typed terminal manager failure with a stable machine-readable ``reason``."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


@dataclass(frozen=True, slots=True)
class TerminalSpec:
    """Everything one terminal needs to be started (never a command string)."""

    session_id: str
    rows: int = DEFAULT_DEFAULT_ROWS
    cols: int = DEFAULT_DEFAULT_COLS
    #: Guest/host working directory the shell starts in (validated by the caller).
    workdir: str = ""


@dataclass(frozen=True, slots=True)
class TerminalOutput:
    """One chunk of PTY output with its monotonic sequence number."""

    seq: int
    data: bytes


@dataclass(frozen=True, slots=True)
class TicketRecord:
    """One single-use ticket and the session it authorises."""

    ticket: TicketId
    session_id: str
    issued_at: float
    expires_at: float
    device_identity: str = ""


class TerminalBackend(Protocol):
    """The process boundary: spawn a PTY, read/write it, resize it, end it."""

    def open(self, spec: TerminalSpec) -> None: ...
    def read(self, timeout_s: float) -> bytes | None: ...
    def write(self, data: bytes) -> None: ...
    def resize(self, rows: int, cols: int) -> None: ...
    def close(self) -> None: ...

    @property
    def closed(self) -> bool: ...

    def poll_exit(self) -> int | None:
        """Child exit code when the shell is gone, else ``None`` (never blocks)."""
        ...


def _clamp_dimension(value: int, fallback: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return fallback
    if value < MIN_DIMENSION or value > MAX_DIMENSION:
        return fallback
    return value


class PtyTerminalBackend:
    """Host PTY implementation of :class:`TerminalBackend`.

    The child is started with :func:`pty.fork`, which already puts it in a new
    session and makes the slave the controlling terminal. The parent keeps the
    master fd, asks for a non-blocking read and applies ``TIOCSWINSZ`` on
    resize. The shell comes from ``$SHELL`` (falling back to ``/bin/bash``) and
    is started as an **interactive login-less** shell; no command, no argv and no
    environment value ever arrives from the client, which is why the protocol
    has no field for them.

    In-guest use: the guest terminal service replaces this class with a
    vsock-backed backend implementing the same three methods.
    """

    def __init__(
        self,
        *,
        shell: str | None = None,
        env: dict[str, str] | None = None,
        read_timeout_s: float = 0.25,
    ) -> None:
        self._shell = shell
        self._env = env
        self._read_timeout_s = read_timeout_s
        self._master_fd: int | None = None
        self._pid: int | None = None
        self._closed = False
        self._exit_code: int | None = None
        self._lock = threading.Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pid(self) -> int | None:
        return self._pid

    def poll_exit(self) -> int | None:
        """Reap the child once (non-blocking); ``None`` while it still runs."""
        pid = self._pid
        if pid is None:
            return self._exit_code
        if self._exit_code is not None:
            return self._exit_code
        try:
            reaped, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            self._exit_code = self._exit_code if self._exit_code is not None else 0
            return self._exit_code
        except OSError:
            return None
        if reaped == 0:
            return None
        if os.WIFEXITED(status):
            self._exit_code = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            self._exit_code = 128 + os.WTERMSIG(status)
        else:  # pragma: no cover - neither exited nor signalled
            self._exit_code = 1
        return self._exit_code

    def open(self, spec: TerminalSpec) -> None:
        if self._master_fd is not None:
            raise TerminalError("backend_already_open")
        shell = self._shell or os.environ.get("SHELL") or "/bin/bash"
        if not os.path.isabs(shell):
            raise TerminalError("shell_not_absolute", shell)
        rows = _clamp_dimension(spec.rows, DEFAULT_DEFAULT_ROWS)
        cols = _clamp_dimension(spec.cols, DEFAULT_DEFAULT_COLS)
        workdir = spec.workdir or ""
        if workdir and not os.path.isdir(workdir):
            raise TerminalError("workdir_missing", workdir)
        env = dict(self._env) if self._env is not None else {
            key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "PATH")
            if key in os.environ
        }
        env["TERM"] = env.get("TERM") or "xterm-256color"
        env["IGOREK_TERMINAL"] = "1"

        def _child() -> None:  # pragma: no cover - runs in the forked child
            try:
                # pty.fork() already creates the session and controlling terminal.
                if workdir:
                    os.chdir(workdir)
            except OSError:
                os._exit(1)
            os.execve(shell, [shell, "-i"], env)
            os._exit(1)

        pid, fd = pty.fork()
        if pid == 0:
            _child()
        self._pid = pid
        self._master_fd = fd
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._apply_winsize(rows, cols)

    def _apply_winsize(self, rows: int, cols: int) -> None:
        fd = self._master_fd
        if fd is None:
            return
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)
        except OSError as exc:  # pragma: no cover - a dead PTY is already gone
            logger.debug("terminal: TIOCSWINSZ failed: %s", exc)

    def read(self, timeout_s: float) -> bytes | None:
        fd = self._master_fd
        if fd is None or self._closed:
            return None
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return None
                time.sleep(min(self._read_timeout_s, 0.05))
                continue
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return None  # the slave side is gone: normal exit
                raise TerminalError("pty_read_failed", str(exc)) from exc
            if not data:
                return None
            return data

    def write(self, data: bytes) -> None:
        fd = self._master_fd
        if fd is None or self._closed:
            raise TerminalError("terminal_closed")
        try:
            os.write(fd, data)
        except OSError as exc:
            raise TerminalError("pty_write_failed", str(exc)) from exc

    def resize(self, rows: int, cols: int) -> None:
        self._apply_winsize(
            _clamp_dimension(rows, DEFAULT_DEFAULT_ROWS),
            _clamp_dimension(cols, DEFAULT_DEFAULT_COLS),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            fd, pid = self._master_fd, self._pid
            self._master_fd = None
            self._pid = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if pid is not None:
            try:
                os.kill(pid, signal.SIGHUP)
            except OSError:
                pass
            # Reap the child so a long-lived host process does not accumulate
            # zombies for every terminal the user closed.
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass


@dataclass(slots=True)
class TerminalState:
    """One live terminal: backend, replay ring and the browser's ack cursor."""

    terminal_id: str
    session_id: str
    backend: TerminalBackend
    rows: int
    cols: int
    opened_at: float
    next_seq: int = 1
    acked_seq: int = 0
    viewer: str | None = None
    replay: deque[TerminalOutput] = field(default_factory=deque)
    replay_bytes: int = 0
    exit_code: int | None = None
    reconnect_timer: threading.Timer | None = None

    def remember(self, chunk: bytes, *, max_chunks: int) -> TerminalOutput:
        """Append one output chunk to the bounded replay ring."""
        item = TerminalOutput(seq=self.next_seq, data=chunk)
        self.next_seq += 1
        self.replay.append(item)
        self.replay_bytes += len(chunk)
        while len(self.replay) > max_chunks or self.replay_bytes > MAX_REPLAY_BYTES:
            dropped = self.replay.popleft()
            self.replay_bytes -= len(dropped.data)
        return item

    def replay_since(self, since_seq: int, *, max_bytes: int = MAX_REPLAY_BYTES) -> list[TerminalOutput]:
        """Chunks after ``since_seq``, newest-last, bounded by ``max_bytes``."""
        if since_seq >= self.next_seq - 1:
            return []
        pending = [item for item in self.replay if item.seq > since_seq]
        total = 0
        kept: list[TerminalOutput] = []
        for item in reversed(pending):
            if total + len(item.data) > max_bytes:
                break
            kept.append(item)
            total += len(item.data)
        kept.reverse()
        return kept


class TerminalManager:
    """Ticket authority + live terminal registry (thread-safe)."""

    def __init__(
        self,
        *,
        backend_factory: Callable[[], TerminalBackend] | None = None,
        max_terminals: int = DEFAULT_MAX_TERMINALS,
        ticket_ttl_s: float = TICKET_TTL_S,
        replay_chunks: int = DEFAULT_SESSION_REPLAY_CHUNKS,
        clock: Callable[[], float] | None = None,
        ticket_bytes: Callable[[int], bytes] | None = None,
    ) -> None:
        self._backend_factory = backend_factory or PtyTerminalBackend
        self._max_terminals = max(1, int(max_terminals))
        self._ticket_ttl_s = max(1.0, float(ticket_ttl_s))
        self._replay_chunks = max(1, int(replay_chunks))
        self._clock = clock or time.monotonic
        self._ticket_bytes = ticket_bytes or os.urandom
        self._lock = threading.Lock()
        self._tickets: dict[TicketId, TicketRecord] = {}
        self._terminals: dict[str, TerminalState] = {}
        self._by_session: dict[str, str] = {}
        #: Slots claimed by an in-flight :meth:`open_terminal` (see the comment
        #: there): the limit counts live terminals plus reservations.
        self._reserved = 0

    # ---------------------------------------------------------------- tickets

    def issue_ticket(self, session_id: str, *, device_identity: str = "") -> TicketId:
        """Mint one single-use ticket bound to ``session_id``."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise TerminalError("session_id_invalid", repr(session_id))
        raw = self._ticket_bytes(16)
        if not isinstance(raw, (bytes, bytearray)) or len(raw) != 16:
            raise TerminalError("ticket_source_invalid", "expected 16 random bytes")
        ticket = TicketId(binascii.hexlify(bytes(raw)).decode("ascii"))
        now = self._clock()
        record = TicketRecord(
            ticket=ticket,
            session_id=session_id,
            issued_at=now,
            expires_at=now + self._ticket_ttl_s,
            device_identity=device_identity,
        )
        with self._lock:
            self._sweep_tickets_locked(now)
            self._tickets[ticket] = record
        return ticket

    def consume_ticket(self, ticket: str, session_id: str | None = None) -> TicketRecord:
        """Consume a ticket exactly once; raise on unknown/expired/mismatched.

        ``session_id`` is optional because the WebSocket handshake already proves
        the paired session; when supplied it must match the binding, so a ticket
        minted for one session cannot open another session's shell.
        """
        if not isinstance(ticket, str):
            raise TerminalError("ticket_invalid", "not a string")
        now = self._clock()
        with self._lock:
            self._sweep_tickets_locked(now)
            record = self._tickets.pop(ticket, None)
        if record is None:
            raise TerminalError("ticket_invalid", "unknown or already used")
        if record.expires_at <= now:
            raise TerminalError("ticket_expired", f"ttl {self._ticket_ttl_s:g}s")
        if session_id is not None and record.session_id != session_id:
            raise TerminalError("ticket_session_mismatch", record.session_id)
        return record

    def _sweep_tickets_locked(self, now: float) -> None:
        expired = [key for key, record in self._tickets.items() if record.expires_at <= now]
        for key in expired:
            del self._tickets[key]

    def pending_tickets(self) -> int:
        """Number of unused, unexpired tickets (diagnostics only)."""
        with self._lock:
            self._sweep_tickets_locked(self._clock())
            return len(self._tickets)

    # -------------------------------------------------------------- terminals

    def open_terminal(self, spec: TerminalSpec) -> TerminalState:
        """Start one terminal for ``spec.session_id`` (one per session, bounded).

        The limit is checked and a slot **reserved** under the lock, but the
        backend is spawned outside it: ``pty.fork`` must not run while the
        manager lock is held, and the reservation keeps a concurrent open from
        slipping past the limit in the meantime.
        """
        previous: TerminalState | None = None
        with self._lock:
            existing_id = self._by_session.get(spec.session_id)
            if existing_id is not None:
                existing = self._terminals.get(existing_id)
                if existing is not None and existing.exit_code is None:
                    raise TerminalError("terminal_exists", existing_id)
                # A finished terminal is replaced, never silently reused: its
                # PTY is closed first, so no orphaned child is left behind.
                previous = self._terminals.pop(existing_id, None)
                self._by_session.pop(spec.session_id, None)
            if len(self._terminals) + self._reserved >= self._max_terminals:
                raise TerminalError("terminal_limit", f"max {self._max_terminals}")
            self._reserved += 1
        backend: TerminalBackend | None = None
        try:
            backend = self._backend_factory()
            backend.open(spec)
            state = TerminalState(
                terminal_id=secrets.token_hex(8),
                session_id=spec.session_id,
                backend=backend,
                rows=_clamp_dimension(spec.rows, DEFAULT_DEFAULT_ROWS),
                cols=_clamp_dimension(spec.cols, DEFAULT_DEFAULT_COLS),
                opened_at=self._clock(),
            )
        except BaseException:
            with self._lock:
                self._reserved -= 1
            if backend is not None:
                try:
                    backend.close()
                except Exception:  # best-effort cleanup
                    logger.debug("terminal: failed open left a backend to close")
            raise
        with self._lock:
            self._reserved -= 1
            self._terminals[state.terminal_id] = state
            self._by_session[spec.session_id] = state.terminal_id
        if previous is not None:
            try:
                previous.backend.close()
            except Exception as exc:  # teardown must not raise
                logger.warning("terminal: replacing %s failed to close: %s", previous.terminal_id, exc)
        logger.info(
            "terminal: opened %s for session %s (%dx%d)",
            state.terminal_id,
            spec.session_id,
            state.rows,
            state.cols,
        )
        return state

    def terminal(self, terminal_id: str) -> TerminalState | None:
        with self._lock:
            return self._terminals.get(terminal_id)

    def terminal_for_session(self, session_id: str) -> TerminalState | None:
        with self._lock:
            terminal_id = self._by_session.get(session_id)
            return self._terminals.get(terminal_id) if terminal_id else None

    def count(self) -> int:
        with self._lock:
            return len(self._terminals)

    def pump(self, terminal_id: str, *, budget: int = MAX_REPLAY_BYTES) -> list[TerminalOutput]:
        """Read whatever the backend has, remember it and return the new chunks.

        ``None`` from the backend means "nothing right now" *or* EOF; EOF is
        distinguished by the caller through :meth:`mark_exit`, which the pump
        loop calls when the child is gone.
        """
        state = self.terminal(terminal_id)
        if state is None:
            raise TerminalError("terminal_unknown", terminal_id)
        chunks: list[TerminalOutput] = []
        total = 0
        while total < budget:
            try:
                data = state.backend.read(0.0)
            except (TerminalError, TerminalProtocolError):
                # The backend is a trusted component: a typed failure it raises
                # is already the right answer and must not be re-labelled.
                raise
            except OSError as exc:
                raise TerminalError("terminal_read_failed", str(exc)) from exc
            if data is None:
                break
            chunks.append(state.remember(data, max_chunks=self._replay_chunks))
            total += len(data)
        return chunks

    def input(self, terminal_id: str, data: bytes) -> None:
        """Forward keystroke bytes to the PTY."""
        state = self.terminal(terminal_id)
        if state is None:
            raise TerminalError("terminal_unknown", terminal_id)
        if state.exit_code is not None:
            raise TerminalError("terminal_exited", str(state.exit_code))
        state.backend.write(data)

    def resize(self, terminal_id: str, rows: int, cols: int) -> None:
        state = self.terminal(terminal_id)
        if state is None:
            raise TerminalError("terminal_unknown", terminal_id)
        rows = _clamp_dimension(rows, state.rows)
        cols = _clamp_dimension(cols, state.cols)
        state.rows, state.cols = rows, cols
        state.backend.resize(rows, cols)

    def ack(self, terminal_id: str, seq: int) -> int:
        """Record the last fully rendered output seq; never moves backwards."""
        state = self.terminal(terminal_id)
        if state is None:
            raise TerminalError("terminal_unknown", terminal_id)
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise TerminalError("ack_invalid", repr(seq))
        if seq > state.acked_seq:
            state.acked_seq = seq
        return state.acked_seq

    def replay(self, terminal_id: str, since_seq: int) -> list[TerminalOutput]:
        """Chunks the browser still needs after a reload (item 10)."""
        state = self.terminal(terminal_id)
        if state is None:
            raise TerminalError("terminal_unknown", terminal_id)
        if not isinstance(since_seq, int) or isinstance(since_seq, bool) or since_seq < 0:
            raise TerminalError("replay_seq_invalid", repr(since_seq))
        return state.replay_since(since_seq)

    def mark_exit(self, terminal_id: str, code: int) -> None:
        state = self.terminal(terminal_id)
        if state is None:
            return
        if state.exit_code is None:
            state.exit_code = max(-1, min(255, int(code)))

    def poll_exit(self, terminal_id: str) -> int | None:
        """Ask the backend whether the shell ended; remember the code once.

        ``None`` while the shell runs. A terminal whose backend reports an exit
        is marked so a later :meth:`input` is refused instead of silently
        vanishing into a dead PTY.
        """
        state = self.terminal(terminal_id)
        if state is None:
            raise TerminalError("terminal_unknown", terminal_id)
        if state.exit_code is not None:
            return state.exit_code
        code = state.backend.poll_exit()
        if code is None:
            return None
        self.mark_exit(terminal_id, code)
        return state.exit_code

    # --------------------------------------------------------------- viewers

    def claim_viewer(self, terminal_id: str, token: str) -> TerminalState:
        """Attach one viewer to a terminal.

        A live terminal of the same session is **reused**: that is the browser
        reload path (item 10) - reloading the page must restore the visible
        screen, not kill the running shell. Only one viewer may be attached at a
        time; a second concurrent attach is refused with ``terminal_busy`` so two
        tabs cannot fight over one PTY's geometry and ack cursor.
        """
        if not isinstance(token, str) or not token:
            raise TerminalError("viewer_token_invalid", repr(token))
        with self._lock:
            state = self._terminals.get(terminal_id)
            if state is None:
                raise TerminalError("terminal_unknown", terminal_id)
            if state.viewer is not None and state.viewer != token:
                raise TerminalError("terminal_busy", state.terminal_id)
            state.viewer = token
            if state.reconnect_timer is not None:
                state.reconnect_timer.cancel()
                state.reconnect_timer = None
            return state

    def release_viewer(self, terminal_id: str, token: str) -> None:
        with self._lock:
            state = self._terminals.get(terminal_id)
            if state is not None and state.viewer == token:
                state.viewer = None
                state.reconnect_timer = threading.Timer(300, self._expire_viewer, args=(terminal_id,))
                state.reconnect_timer.daemon = True
                state.reconnect_timer.start()

    def _expire_viewer(self, terminal_id: str) -> None:
        with self._lock:
            state = self._terminals.get(terminal_id)
            if state is None or state.viewer is not None:
                return
        self.close_terminal(terminal_id)

    def close_terminal(self, terminal_id: str) -> bool:
        """Close one terminal; idempotent and safe from any thread."""
        with self._lock:
            state = self._terminals.pop(terminal_id, None)
            if state is not None and state.reconnect_timer is not None:
                state.reconnect_timer.cancel()
            if state is not None and self._by_session.get(state.session_id) == terminal_id:
                self._by_session.pop(state.session_id, None)
        if state is None:
            return False
        try:
            state.backend.close()
        except Exception as exc:  # teardown must not raise
            logger.warning("terminal: close %s failed: %s", terminal_id, exc)
        return True

    def close_session(self, session_id: str) -> int:
        """Close the terminal of one session (used when a session is stopped)."""
        with self._lock:
            terminal_id = self._by_session.get(session_id)
        if terminal_id is None:
            return 0
        return 1 if self.close_terminal(terminal_id) else 0

    def close_all(self) -> None:
        with self._lock:
            ids = list(self._terminals)
        for terminal_id in ids:
            self.close_terminal(terminal_id)

    def live_ids(self) -> Iterable[str]:
        with self._lock:
            return tuple(self._terminals)
