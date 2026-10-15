"""In-guest terminal service: the PTY owner for one interactive session terminal.

The terminal is deliberately **not** part of :class:`factory.core.isolation.guest_runner.GuestRunner`:

* the runner is restarted between tasks (``igorek-guest-runner.service``), so a
  shell living inside it would die on every task boundary - and the plan
  requires the terminal to survive them;
* the runner's own hardening (bwrap around each Bash tool call, approval paths)
  is about *agent* tool execution, while an interactive shell is a different
  trust decision made deliberately by the user through the paired interface;
* keeping them apart means a bug in the terminal can never widen the task
  channel, which denies shell keys structurally.

Boundary rules (never relaxed):

* the service connects to the host over a **separate** vsock port
  (:data:`DEFAULT_TERMINAL_VSOCK_PORT`) and speaks the terminal protocol
  (:mod:`factory.core.isolation.terminal_protocol`); the task channel on
  ``IGOREK_ISOLATION_VSOCK_PORT`` is untouched and still rejects shell keys;
* the shell is spawned with ``no_new_privs`` set and every capability dropped,
  inside the guest's already-egress-filtered network namespace;
* the working directory is the exported workspace (the single virtiofs share),
  never a host path - there is no host filesystem in the guest to name;
* a malformed frame is answered with an ``error`` message and the connection is
  closed; the service never crashes the unit and never falls back to an
  unhardened spawn.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import logging
import os
import pty
import signal
import struct
import sys
import termios
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol

from .channel import (
    ChannelConfig,
    ChannelError,
    SocketLike,
    read_frame,
    write_frame,
)
from .terminal_protocol import (
    DEFAULT_REPLAY_CHUNKS,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    TerminalKind,
    TerminalProtocolError,
    TerminalStage,
    decode_message,
    encode_message,
)

logger = logging.getLogger(__name__)

#: Separate AF_VSOCK port for the terminal, so the task channel keeps its policy.
DEFAULT_TERMINAL_VSOCK_PORT: Final[int] = 5006
#: Host CID from inside a QEMU guest (VMADDR_CID_HOST).
DEFAULT_VSOCK_HOST_CID: Final[int] = 2
#: Read window for the PTY: short enough to stream, long enough to be cheap.
PTY_READ_WINDOW_S: Final[float] = 0.05
#: Idle window while nothing arrives from the host.
HOST_READ_WINDOW_S: Final[float] = 0.05
#: Default shell inside the guest image.
DEFAULT_GUEST_SHELL: Final[str] = "/bin/bash"


class PtySpawnerProtocol(Protocol):
    """The spawn seam: a PTY the service can read/write/resize/close."""

    @property
    def rows(self) -> int: ...
    @property
    def cols(self) -> int: ...
    def read(self, timeout_s: float) -> bytes | None: ...
    def write(self, data: bytes) -> None: ...
    def resize(self, rows: int, cols: int) -> None: ...
    def poll_exit(self) -> int | None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TerminalTuning:
    """Spawn settings; the defaults are the hardened production values."""

    shell: str = DEFAULT_GUEST_SHELL
    workdir: str = ""
    replay_chunks: int = DEFAULT_REPLAY_CHUNKS
    no_new_privs: bool = True
    drop_capabilities: bool = True


class GuestPty:
    """One PTY in the guest, spawned with ``no_new_privs`` and no capabilities.

    ``pty.fork`` gives the child a new session and the slave as its controlling
    terminal. Before ``exec`` the child drops every capability it may have
    inherited and sets ``PR_SET_NO_NEW_PRIVS`` so a setuid binary inside the
    image cannot regain privileges through this shell.
    """

    def __init__(self, tuning: TerminalTuning) -> None:
        self._tuning = tuning
        self._master_fd: int | None = None
        self._pid: int | None = None
        self.rows = 30
        self.cols = 100
        self._exit_code: int | None = None
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._pid

    def start(self, rows: int, cols: int) -> None:
        if self._master_fd is not None:
            raise TerminalProtocolError("terminal_exists", "pty already started")
        self.rows = max(1, min(1000, int(rows)))
        self.cols = max(1, min(1000, int(cols)))
        tuning = self._tuning
        shell = tuning.shell or DEFAULT_GUEST_SHELL
        env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL") if key in os.environ}
        env["TERM"] = env.get("TERM") or "xterm-256color"
        env["IGOREK_TERMINAL"] = "1"

        def _child() -> None:  # pragma: no cover - runs in the forked child
            try:
                # pty.fork() already creates the session and controlling terminal.
                if tuning.no_new_privs:
                    _set_no_new_privs()
                if tuning.drop_capabilities:
                    _drop_capabilities()
                if tuning.workdir and os.path.isdir(tuning.workdir):
                    os.chdir(tuning.workdir)
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
        self._apply_winsize()

    def _apply_winsize(self) -> None:
        if self._master_fd is None:
            return
        packed = struct.pack("HHHH", self.rows, self.cols, 0, 0)
        try:
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, packed)
        except OSError as exc:  # pragma: no cover - dead PTY
            logger.debug("guest terminal: TIOCSWINSZ failed: %s", exc)

    def read(self, timeout_s: float) -> bytes | None:
        if self._master_fd is None or self._closed:
            return None
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            try:
                data = os.read(self._master_fd, MAX_OUTPUT_BYTES)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return None
                time.sleep(min(PTY_READ_WINDOW_S, 0.05))
                continue
            except OSError:
                return None
            if not data:
                return None
            return data

    def write(self, data: bytes) -> None:
        if self._master_fd is None or self._closed:
            raise TerminalProtocolError("terminal_closed", "pty is gone")
        try:
            os.write(self._master_fd, data)
        except OSError as exc:
            raise TerminalProtocolError("pty_write_failed", str(exc)) from exc

    def resize(self, rows: int, cols: int) -> None:
        self.rows = max(1, min(1000, int(rows)))
        self.cols = max(1, min(1000, int(cols)))
        self._apply_winsize()

    def poll_exit(self) -> int | None:
        pid = self._pid
        if pid is None:
            return self._exit_code
        if self._exit_code is not None:
            return self._exit_code
        try:
            reaped, status = os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            self._exit_code = self._exit_code if self._exit_code is not None else 0
            return self._exit_code
        if reaped == 0:
            return None
        if os.WIFEXITED(status):
            self._exit_code = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            self._exit_code = 128 + os.WTERMSIG(status)
        else:  # pragma: no cover
            self._exit_code = 1
        return self._exit_code

    def close(self) -> None:
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
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass


def _set_no_new_privs() -> None:
    """``PR_SET_NO_NEW_PRIVS``: no setuid/setcap binary may gain privileges here."""
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        # 38 == PR_SET_NO_NEW_PRIVS, 1 == enabled.
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
    except Exception:
        os._exit(126)


def _drop_capabilities() -> None:
    """Drop every capability from the effective/permitted/inheritable sets."""
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        # Linux capability version 3, empty sets.
        header = (ctypes.c_uint32 * 2)(0x20080522, 0)
        data = (ctypes.c_uint32 * 6)()
        if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
            raise OSError(ctypes.get_errno(), "capset")
    except Exception:
        os._exit(126)


def _close_fds_on_exec() -> None:  # pragma: no cover - exercised inside a real guest
    """Placeholder hook kept for parity with the runner's spawn discipline."""
    return


class GuestTerminalService:
    """Serve one host connection: framed JSON in, PTY out, until close/EOF."""

    def __init__(
        self,
        sock: SocketLike,
        *,
        tuning: TerminalTuning | None = None,
        pty_factory: Callable[[TerminalTuning], PtySpawnerProtocol] | None = None,
        read_window_s: float = HOST_READ_WINDOW_S,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self._sock = sock
        self._tuning = tuning or TerminalTuning()
        self._pty_factory = pty_factory or GuestPty
        self._read_window_s = read_window_s
        self._log = on_log or (lambda message: logger.info("guest terminal: %s", message))
        self._pty: PtySpawnerProtocol | None = None
        self._seq = 0
        self._host_seq = 0
        self._output_credit = {}

    # ------------------------------------------------------------------ serve

    def serve(self) -> int:
        """Run until the host closes the connection or the shell exits."""
        try:
            while True:
                message = self._read_host_message()
                if message is None:
                    return 0
                if message is _CLOSE:
                    return 0
                if message is _IDLE:
                    if self._serve_pty():
                        return 0
                    continue
                keep_going = self._handle(message)
                if not keep_going:
                    return 0
        except ChannelError as exc:
            self._log(f"channel error: {exc}")
            return 0
        except Exception as exc:  # the unit must stay up
            self._log(f"unexpected failure: {exc}")
            return 1
        finally:
            self._close_pty()

    def _handle(self, message: Any) -> bool:
        kind = message.kind
        payload = message.payload
        if kind is TerminalKind.OPEN:
            return self._open(payload)
        if kind is TerminalKind.INPUT:
            return self._input(payload)
        if kind is TerminalKind.RESIZE:
            return self._resize(payload)
        if kind is TerminalKind.ACK:
            ack = payload["seq"]
            self._output_credit = {seq: size for seq, size in self._output_credit.items() if seq > ack}
            return True
        if kind is TerminalKind.CLOSE:
            self._log("host closed the terminal")
            return False
        self._log(f"ignoring unexpected kind {kind}")
        return True

    def _open(self, payload: dict[str, object]) -> bool:
        if self._pty is not None:
            self._send_error("terminal_exists")
            return False
        rows = int(payload.get("rows", 30) or 30)
        cols = int(payload.get("cols", 100) or 100)
        try:
            pty_obj = self._pty_factory(self._tuning)
            pty_obj.start(rows, cols)  # type: ignore[attr-defined]
        except (TerminalProtocolError, OSError, AttributeError) as exc:
            self._log(f"spawn failed: {exc}")
            self._send_error("spawn_failed")
            return False
        self._pty = pty_obj
        self._send(
            TerminalKind.OPENED,
            {
                "terminal_id": f"guest-{os.getpid()}",
                "rows": int(pty_obj.rows),
                "cols": int(pty_obj.cols),
            },
        )
        self._log("pty started")
        return True

    def _input(self, payload: dict[str, object]) -> bool:
        raw = payload.get("data")
        if not isinstance(raw, str) or self._pty is None:
            self._send_error("input_rejected")
            return True
        try:
            data = base64.b64decode(raw.encode("ascii"), validate=True)
        except Exception:
            self._send_error("input_rejected")
            return True
        if len(data) > MAX_INPUT_BYTES:
            self._send_error("input_too_large")
            return True
        try:
            self._pty.write(data)
        except TerminalProtocolError as exc:
            self._send_error(exc.reason)
            return self._exit_if_finished()
        return True

    def _resize(self, payload: dict[str, object]) -> bool:
        if self._pty is None:
            self._send_error("terminal_unknown")
            return True
        try:
            self._pty.resize(int(payload["rows"]), int(payload["cols"]))
        except (KeyError, TypeError, ValueError):
            self._send_error("resize_invalid")
        return True

    def _serve_pty(self) -> bool:
        """Stream PTY output; ``True`` when the shell has exited."""
        pty_obj = self._pty
        if pty_obj is None:
            return False
        if sum(self._output_credit.values()) >= 256 * 1024:
            return False
        try:
            data = pty_obj.read(PTY_READ_WINDOW_S)
        except TerminalProtocolError as exc:
            self._log(f"pty read failed: {exc.reason}")
            data = None
        if data:
            self._send(TerminalKind.OUTPUT, {"data": base64.b64encode(data).decode("ascii")})
            self._output_credit[self._seq] = len(data)
        code = pty_obj.poll_exit()
        if code is None:
            return False
        self._send(TerminalKind.EXIT, {"code": int(code)})
        self._log(f"pty exited with {code}")
        return True

    def _exit_if_finished(self) -> bool:
        pty_obj = self._pty
        if pty_obj is None:
            return True
        code = pty_obj.poll_exit()
        if code is None:
            return True
        self._send(TerminalKind.EXIT, {"code": int(code)})
        return False

    def _close_pty(self) -> None:
        pty_obj, self._pty = self._pty, None
        if pty_obj is None:
            return
        try:
            pty_obj.close()
        except Exception as exc:  # teardown must not raise
            self._log(f"pty close failed: {exc}")

    # ------------------------------------------------------------------ wire

    def _read_host_message(self) -> Any:
        try:
            frame = read_frame(self._sock, timeout_s=self._read_window_s)
        except ChannelError as exc:
            if exc.reason == "read_timeout":
                return _IDLE
            raise
        if frame is None:
            return None
        try:
            return decode_message(frame, stage=TerminalStage.INTERNAL)
        except TerminalProtocolError as exc:
            self._log(f"rejected frame: {exc.reason}")
            self._send_error(exc.reason)
            return _CLOSE

    def _send(self, kind: TerminalKind, payload: dict[str, object]) -> None:
        self._seq += 1
        try:
            write_frame(self._sock, {"kind": str(kind), "seq": self._seq, "payload": payload})
        except ChannelError as exc:
            self._log(f"send failed: {exc}")

    def _send_error(self, reason: str) -> None:
        self._send(TerminalKind.ERROR, {"reason": reason[:128]})


class _IdleMarker:
    __slots__ = ()


class _CloseMarker:
    __slots__ = ()


_IDLE = _IdleMarker()
_CLOSE = _CloseMarker()


def open_vsock_client(cid: int = DEFAULT_VSOCK_HOST_CID, port: int | None = None) -> SocketLike:
    """Connect to the host's terminal vsock port; fails closed without vsock."""
    import socket as socket_module

    effective_port = DEFAULT_TERMINAL_VSOCK_PORT if port is None else port
    family = getattr(socket_module, "AF_VSOCK", None)
    if not isinstance(family, int):
        raise ChannelError("vsock_unavailable", "AF_VSOCK is not available in this guest")
    sock = socket_module.socket(family, socket_module.SOCK_STREAM)
    try:
        sock.connect((cid, effective_port))
    except OSError as exc:
        sock.close()
        raise ChannelError("vsock_connect_failed", f"cid {cid} port {effective_port}: {exc}") from exc
    return sock


def run_guest_terminal(
    *, vsock_cid: int = DEFAULT_VSOCK_HOST_CID, vsock_port: int | None = None
) -> int:
    """Connect, handshake as the terminal peer and serve until the host leaves."""
    from .channel import open_vsock_listener
    config = ChannelConfig(port=(DEFAULT_TERMINAL_VSOCK_PORT if vsock_port is None else vsock_port))
    with open_vsock_listener(config.port) as listener:
        while True:
            sock, address = listener.accept()
            with sock:
                if address[0] != vsock_cid:
                    continue
                if not guest_terminal_handshake(sock, config):
                    continue
                tuning = TerminalTuning(workdir=os.environ.get("IGOREK_TERMINAL_WORKDIR", "/workspace"))
                GuestTerminalService(sock, tuning=tuning).serve()


def guest_terminal_handshake(sock: SocketLike, config: ChannelConfig) -> bool:
    """Guest side of the framed hello/hailed_ack dance on the terminal port."""
    from .terminal_protocol import TERMINAL_PROTOCOL_VERSION

    try:
        write_frame(sock, {"kind": "hello", "version": TERMINAL_PROTOCOL_VERSION})
        ack = read_frame(sock, timeout_s=config.hello_timeout_s)
    except ChannelError as exc:
        logger.warning("guest terminal: handshake failed: %s", exc)
        return False
    if not isinstance(ack, dict) or ack.get("kind") != "hello_ack" or ack.get("accepted") is not True:
        logger.warning("guest terminal: rejected by the host: %s", (ack or {}).get("reason"))
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``igorek-factory guest-terminal [--vsock-cid N] [--vsock-port N]``."""
    parser = argparse.ArgumentParser(
        prog="igorek-factory guest-terminal",
        description="Serve an interactive PTY inside a session VM (host<->guest vsock).",
    )
    parser.add_argument("--vsock-cid", type=int, default=DEFAULT_VSOCK_HOST_CID)
    parser.add_argument("--vsock-port", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run_guest_terminal(vsock_cid=args.vsock_cid, vsock_port=args.vsock_port)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
