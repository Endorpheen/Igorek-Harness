"""Private per-profile IPC to the existing VM owner, outside the workspace."""

import hashlib
import json
import os
import socket
import stat
import struct
import threading
from pathlib import Path

from ..runtime import get_chat_sessions_dir
from .channel import read_frame, write_frame
from .manager import SessionSpec
from .terminal_manager import TerminalError


def owner_path(profile):
    root = Path(get_chat_sessions_dir()).parent / "isolation" / "terminal-ipc"
    return root / (hashlib.sha256(profile.encode()).hexdigest()[:20] + ".sock")


def owner_request(profile, request):
    path = owner_path(profile)
    try:
        info = path.parent.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise TerminalError("terminal_owner_permissions")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(90)
            conn.connect(str(path))
            _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                raise TerminalError("terminal_owner_permissions")
            write_frame(conn, request)
            result = read_frame(conn, max_frame_bytes=4096, timeout_s=90)
    except (OSError, ValueError) as exc:
        raise TerminalError("terminal_owner_unavailable") from exc
    if not result or result.get("ok") is not True:
        raise TerminalError(str((result or {}).get("reason") or "terminal_owner_unavailable"))
    return result


class TerminalOwnerServer:
    def __init__(self, profile, manager):
        self.profile, self.manager = profile, manager
        path = owner_path(profile)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = path.parent.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise TerminalError("terminal_owner_permissions")
        if path.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.connect(str(path))
                raise TerminalError("terminal_owner_already_running")
            except ConnectionRefusedError:
                path.unlink()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        os.chmod(path, 0o600)
        self.listener.listen(8)
        self.listener.settimeout(0.25)
        self._path = path
        self._identity = (path.stat().st_dev, path.stat().st_ino)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self.serve, name="terminal-owner", daemon=True)
        self._thread.start()

    def serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                raise
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def close(self):
        if self._stop.is_set():
            return
        self._stop.set()
        self.listener.close()
        self._thread.join(timeout=1)
        try:
            info = self._path.lstat()
            if (info.st_dev, info.st_ino) == self._identity:
                self._path.unlink()
        except FileNotFoundError:
            pass

    def handle(self, conn):
        with conn:
            try:
                _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    return
                request = read_frame(conn, max_frame_bytes=4096, timeout_s=5)
                operation = (request or {}).get("op")
                if operation == "open" and set(request) == {"op", "session_id"}:
                    sid = request["session_id"]
                    if not isinstance(sid, str) or not sid or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in sid):
                        raise TerminalError("session_id_invalid")
                    path = Path(get_chat_sessions_dir()) / (sid + ".jsonl")
                    with path.open() as source:
                        meta = json.loads(source.readline())
                    if meta.get("type") != "meta" or meta.get("profile") != self.profile:
                        raise TerminalError("session_profile_mismatch")
                    result = self.manager.terminal_acquire(SessionSpec(sid, meta["cwd"]))
                elif operation == "renew" and set(request) == {"op", "lease"}:
                    if not self.manager.terminal_renew(request["lease"]):
                        raise TerminalError("terminal_vm_lost")
                    result = {}
                elif operation == "release" and set(request) == {"op", "lease"}:
                    self.manager.terminal_release(request["lease"])
                    result = {}
                else:
                    raise TerminalError("terminal_owner_request_invalid")
                write_frame(conn, {"ok": True, **result})
            except Exception as exc:
                try:
                    write_frame(conn, {"ok": False, "reason": getattr(exc, "reason", "terminal_owner_failed")})
                except Exception:
                    pass
