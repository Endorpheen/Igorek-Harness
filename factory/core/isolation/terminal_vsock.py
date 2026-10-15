"""Host PTY backend routed to the VM held by the profile's worker."""

import base64
import json
import os
import select
import socket
import threading
import time
from pathlib import Path

from ..runtime import get_chat_sessions_dir
from .channel import read_frame, write_frame
from .terminal_manager import TerminalError
from .terminal_manager import PtyTerminalBackend
def owner_request(profile, request):
    from .terminal_owner import owner_request as request_owner
    return request_owner(profile, request)
from .terminal_protocol import TerminalKind, TerminalStage, decode_message


class VsockTerminalBackend:
    def __init__(self):
        self.sock = None
        self.profile = None
        self.lease = None
        self.exit_code = None
        self.seq = 0
        self._write_lock = threading.Lock()
        self._stopped = threading.Event()

    def open(self, spec):
        path = Path(get_chat_sessions_dir()) / (spec.session_id + ".jsonl")
        with path.open() as source:
            meta = json.loads(source.readline())
        self.profile = meta["profile"]
        route = owner_request(self.profile, {"op": "open", "session_id": spec.session_id})
        self.lease = route["lease"]
        self.sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        deadline = time.monotonic() + 60
        while True:
            try:
                self.sock.settimeout(5)
                self.sock.connect((route["cid"], 5006))
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TerminalError("guest_terminal_unavailable") from exc
                self.sock.close()
                self.sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
                time.sleep(0.5)
        hello = read_frame(self.sock, max_frame_bytes=4096, timeout_s=5)
        if hello != {"kind": "hello", "version": 1}:
            raise TerminalError("guest_terminal_handshake")
        owner_request(self.profile, {"op": "renew", "lease": self.lease})
        write_frame(self.sock, {"kind": "hello_ack", "accepted": True})
        self._send("open", {"rows": spec.rows, "cols": spec.cols})
        opened = read_frame(self.sock, max_frame_bytes=128 * 1024, timeout_s=5)
        if not opened or opened.get("kind") != "opened":
            raise TerminalError("guest_terminal_spawn_failed")
        threading.Thread(target=self._renew, name="terminal-vm-lease", daemon=True).start()

    def _renew(self):
        while not self._stopped.wait(10):
            try:
                owner_request(self.profile, {"op": "renew", "lease": self.lease})
            except Exception:
                self.exit_code = 125
                self.close()
                return

    def _send(self, kind, payload):
        with self._write_lock:
            self.seq += 1
            write_frame(self.sock, {"kind": kind, "seq": self.seq, "payload": payload})

    def read(self, timeout_s):
        if self.sock is None or self._stopped.is_set():
            return None
        if not select.select([self.sock], [], [], timeout_s)[0]:
            return None
        frame = read_frame(self.sock, max_frame_bytes=128 * 1024, timeout_s=5)
        if frame is None:
            self.exit_code = 125
            return None
        message = decode_message(frame, stage=TerminalStage.INTERNAL)
        if message.kind == TerminalKind.OUTPUT:
            data = base64.b64decode(message.payload["data"], validate=True)
            self._send("ack", {"seq": message.seq})
            return data
        if message.kind == TerminalKind.EXIT:
            self.exit_code = message.payload["code"]
        elif message.kind == TerminalKind.ERROR:
            raise TerminalError(message.payload["reason"])
        return None

    def write(self, data):
        self._send("input", {"data": base64.b64encode(data).decode()})

    def resize(self, rows, cols):
        self._send("resize", {"rows": rows, "cols": cols})

    def poll_exit(self):
        return self.exit_code

    def close(self):
        if self._stopped.is_set():
            return
        self._stopped.set()
        if self.sock is not None:
            try:
                self._send("close", {})
            except Exception:
                pass
            self.sock.close()
            self.sock = None
        if self.lease:
            try:
                owner_request(self.profile, {"op": "release", "lease": self.lease})
            except Exception:
                pass


class RoutedTerminalBackend:
    def __init__(self, modes):
        self.modes = modes
        self.backend = None

    def open(self, spec):
        mode = self.modes.get(spec.session_id)
        if mode == "kvm":
            self.backend = VsockTerminalBackend()
        elif mode == "none":
            if os.name == 'nt':
                from ..platform_support.windows_terminal import WindowsTerminalBackend
                self.backend = WindowsTerminalBackend()
            else:
                self.backend = PtyTerminalBackend()
        else:
            raise TerminalError("session_mode_unknown")
        self.backend.open(spec)

    def read(self, timeout_s):
        return self.backend.read(timeout_s)

    def write(self, data):
        self.backend.write(data)

    def resize(self, rows, cols):
        self.backend.resize(rows, cols)

    def poll_exit(self):
        return self.backend.poll_exit()

    def close(self):
        if self.backend is not None:
            self.backend.close()
