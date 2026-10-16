"""Owner-local shared CLI runtime with explicit Codex-style lifecycle commands."""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .platform_support import LockBusyError, exclusive_lock
from .runtime import get_data_root, get_db_path, get_profile_config, get_profiles_dir
from .schema import open_db
from .worker_presence import consumers, private_directory, write_private

LIMIT = 8 * 1024 * 1024


def directory() -> Path:
    return private_directory(get_data_root() / "run" / "daemon")


def receive(stream) -> dict:
    raw = bytearray()
    while not raw.endswith(b"\n"):
        data = stream.recv(min(4096, LIMIT + 1 - len(raw)))
        if not data or len(raw) + len(data) > LIMIT:
            raise ValueError("Invalid daemon frame")
        raw.extend(data)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid daemon message")
    return value


def request(action: str, *, optional=False, **fields):
    path = directory() / "endpoint.json"
    try:
        endpoint = json.loads(path.read_text())
        address = Path(endpoint["socket"])
        private_directory(address.parent)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(45)
            stream.connect(str(address))
            if hasattr(socket, "SO_PEERCRED"):
                _, uid, _ = struct.unpack("3i", stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    raise ValueError("Daemon owner mismatch")
            stream.sendall(json.dumps({"id": endpoint["id"], "action": action, **fields}).encode() + b"\n")
            value = receive(stream)
        if value.get("error"):
            raise RuntimeError(value["error"])
        return value["result"]
    except (FileNotFoundError, ConnectionRefusedError):
        if optional:
            return None
        raise RuntimeError("Shared runtime is not running") from None


class Daemon:
    def __init__(self):
        self.identity = uuid.uuid4().hex
        self.workers = {}
        self.execution_locks = {}
        self.execution_lock = threading.Lock()
        self.served_profiles = set()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.scheduled = 0
        self.scheduler = None
        self.watchdog = None

    def ensure(self, profile):
        if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", profile):
            raise ValueError("Invalid configured profile")
        if not (Path(get_profiles_dir()) / (profile + ".json")).is_file():
            raise ValueError("Unknown configured profile")
        with self.lock:
            if self.stop.is_set():
                raise RuntimeError("Shared runtime is stopping")
            config = get_profile_config(profile)
            self.served_profiles.add(profile)
            if consumers(profile, config):
                return
            from .worker import Worker
            worker = Worker(profile, poll_interval=0.25)
            # Match desktop AgentRuntime: task-local environment changes are
            # process-global, so workers in one daemon must not execute together.
            execution_lock = self.execution_lock
            self.execution_locks[profile] = execution_lock
            thread = threading.Thread(target=worker.run,
                                      kwargs={"stop_event": self.stop, "execution_lock": execution_lock}, daemon=True)
            self.workers[profile] = thread
            thread.start()
            deadline = time.monotonic() + 15
            while not consumers(profile, config):
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Shared worker failed to start")
                time.sleep(0.05)

    def schedule(self, info):
        with self.lock:
            self.scheduled += 1
        try:
            self.ensure(info["agent_id"])
            from factory.factory_api import _execute_schedule_via_gateway
            _execute_schedule_via_gateway(info)
        finally:
            with self.lock:
                self.scheduled -= 1

    def schedule_ready(self, info):
        try:
            self.ensure(info["agent_id"])
            return True
        except (KeyError, OSError, ValueError, RuntimeError):
            return False

    def dispatch(self, value):
        if value.get("id") != self.identity:
            raise ValueError("Daemon instance changed")
        action = value.get("action")
        with self.lock:
            if action == "version":
                return {"running": True, "version": version("igorek-agent-factory"),
                        "profiles": sorted(self.served_profiles), "scheduler": bool(self.scheduler and self.scheduler.running)}
            if action in {"ensure", "submit"}:
                self.ensure(value.get("profile"))
                if action == "ensure":
                    return {"ready": True}
                if not isinstance(value.get("text"), str) or not isinstance(value.get("metadata", {}), dict):
                    raise ValueError("Invalid task payload")
                from .cli_tasks import create_task
                return create_task(value["text"], profile=value["profile"],
                                   metadata=value.get("metadata"), input_files=value.get("input_files"))
            if action == "stop":
                acquired = []
                try:
                    for lock in dict.fromkeys(self.execution_locks.values()):
                        if not lock.acquire(blocking=False):
                            raise RuntimeError("Shared runtime is busy; finish or cancel active tasks before stopping it")
                        acquired.append(lock)
                    with open_db(get_db_path()) as connection:
                        active = connection.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('processing','waiting_approval','cancelling')").fetchone()[0]
                    if active or self.scheduled:
                        raise RuntimeError("Shared runtime is busy; finish or cancel active tasks before stopping it")
                    self.stop.set()
                    return {"stopping": True}
                finally:
                    for lock in reversed(acquired):
                        lock.release()
            raise ValueError("Unknown daemon action")

    def handle(self, stream):
        with stream:
            stream.settimeout(45)
            try:
                if hasattr(socket, "SO_PEERCRED"):
                    _, uid, _ = struct.unpack("3i", stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != os.getuid():
                        raise ValueError("Daemon owner mismatch")
                result = {"result": self.dispatch(receive(stream))}
            except (OSError, ValueError, RuntimeError, KeyError) as error:
                result = {"error": str(error)}
            stream.sendall(json.dumps(result).encode() + b"\n")

    def serve(self):
        if os.name != "posix":
            raise RuntimeError("This CLI runtime requires a POSIX environment")
        root = directory()
        try:
            owner = exclusive_lock(root / "instance.lock")
        except LockBusyError:
            return
        identity = hashlib.sha256(str(get_data_root().resolve()).encode()).hexdigest()[:20]
        socket_root = private_directory(Path(tempfile.gettempdir()) / f"igorek-{os.getuid()}-{identity}")
        address = socket_root / "control.sock"
        if len(str(address).encode()) >= 104:
            os.close(owner)
            raise RuntimeError("Daemon socket path is too long; use a shorter TMPDIR")
        try:
            from .cli_run import _bootstrap_stack_env
            _bootstrap_stack_env()
            address.unlink(missing_ok=True)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(address))
                os.chmod(address, 0o600)
                listener.listen(16)
                listener.settimeout(0.25)
                from .scheduler import TaskScheduler
                self.scheduler = TaskScheduler(get_db_path())
                self.scheduler.set_task_callback(self.schedule)
                self.scheduler.set_readiness_check(self.schedule_ready)
                self.scheduler.start()
                from factory.factory_api import TaskWatchdog, _ensure_db_schema
                self.watchdog = TaskWatchdog(get_db_path(), _ensure_db_schema)
                self.watchdog.start()
                write_private(root / "endpoint.json", {"id": self.identity, "socket": str(address)})
                signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
                signal.signal(signal.SIGINT, lambda *_: self.stop.set())
                while not self.stop.is_set():
                    for profile in list(self.served_profiles):
                        try:
                            self.ensure(profile)
                        except (OSError, ValueError, RuntimeError):
                            pass
                    try:
                        stream, _ = listener.accept()
                    except socket.timeout:
                        continue
                    threading.Thread(target=self.handle, args=(stream,), daemon=True).start()
        finally:
            self.stop.set()
            if self.scheduler:
                self.scheduler.stop()
            if self.watchdog:
                self.watchdog.stop()
            for thread in self.workers.values():
                thread.join(timeout=3)
            from .mcp_bridge import shutdown_mcp_servers
            shutdown_mcp_servers()
            (root / "endpoint.json").unlink(missing_ok=True)
            address.unlink(missing_ok=True)
            os.close(owner)


def lifecycle(action):
    root = directory()
    if action == "version":
        return request("version", optional=True) or {"running": False, "version": version("igorek-agent-factory")}
    operation = exclusive_lock(root / "operation.lock", blocking=True)
    try:
        running = request("version", optional=True)
        if action in {"stop", "restart"} and running:
            request("stop")
            deadline = time.monotonic() + 15
            while (root / "endpoint.json").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Shared runtime is still stopping")
                time.sleep(0.05)
            running = None
        if action == "stop":
            return {"running": False}
        if running:
            return running
        if os.name != "posix":
            raise RuntimeError("This CLI runtime requires a POSIX environment")
        descriptor = os.open(root / "daemon.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as output:
            process = subprocess.Popen([sys.executable, "-m", "factory.core.cli_daemon"],
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = request("version", optional=True)
            if result:
                return result
            if process.poll() is not None:
                raise RuntimeError(f"Daemon failed to start. Diagnostic log: {root / 'daemon.log'}")
            time.sleep(0.05)
        raise RuntimeError("Daemon startup timed out")
    finally:
        os.close(operation)


if __name__ == "__main__":
    Daemon().serve()
