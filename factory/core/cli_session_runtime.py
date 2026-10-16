"""Reference-counted workers for foreground CLI sessions."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from .platform_support import LockBusyError, exclusive_lock
from .runtime import get_profile_config
from .worker_presence import consumers, locked, private_directory, profile_directory, write_private


class SessionRuntime:
    def __init__(self):
        self.clients: dict[str, tuple[int, Path]] = {}
        self.started: list[tuple[str, subprocess.Popen]] = []
        self.shared = None

    def select(self, profile: str) -> None:
        config = get_profile_config(profile)
        from .cli_daemon import request
        if self.shared is None:
            self.shared = request("version", optional=True) is not None
        if self.shared:
            request("ensure", profile=profile)
            return
        directory = profile_directory(profile)
        if profile not in self.clients:
            clients = private_directory(directory / "clients")
            path = clients / (uuid.uuid4().hex + ".lock")
            self.clients[profile] = (exclusive_lock(path), path)
        try:
            # Serialize only launcher startup, never model execution.
            startup = exclusive_lock(directory / "startup.lock", blocking=True)
            try:
                deadline = time.monotonic() + 45
                while (directory / "stopping.json").exists() and locked(directory / "service.lock"):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("The previous worker is still finishing its task")
                    time.sleep(0.05)
                if not consumers(profile, config):
                    self._start(profile, directory, config)
            finally:
                os.close(startup)
        except BaseException:
            self._release(profile)
            raise
        for previous in list(self.clients):
            if previous != profile:
                self._release(previous)

    def submit(self, *, text, profile, metadata=None, input_files=None):
        if self.shared:
            from .cli_daemon import request
            return request("submit", text=text, profile=profile, metadata=metadata or {}, input_files=input_files or [])
        from .cli_tasks import create_task
        return create_task(text, profile=profile, metadata=metadata, input_files=input_files)

    def _start(self, profile: str, directory: Path, config: dict) -> None:
        options = {"start_new_session": True} if os.name == "posix" else {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        }
        log = directory / "worker.log"
        descriptor = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "a") as output:
            process = subprocess.Popen(
                [sys.executable, "-m", "factory.core.cli_session_runtime", "--profile", profile],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"}, **options,
            )
        self.started.append((profile, process))
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if consumers(profile, config):
                return
            if process.poll() is not None:
                raise RuntimeError(f"Worker failed to start. Diagnostic log: {log}")
            time.sleep(0.05)
        raise RuntimeError(f"Worker startup timed out. Diagnostic log: {log}")

    def _release(self, profile: str) -> None:
        descriptor, path = self.clients.pop(profile)
        os.close(descriptor)
        path.unlink(missing_ok=True)

    def close(self) -> None:
        for profile in list(self.clients):
            self._release(profile)
        for profile, process in self.started:
            clients = profile_directory(profile) / "clients"
            if not any(locked(path) for path in clients.glob("*.lock")):
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # Finish only owned task cancellation; never kill a shared process.
                    pass
            else:
                process.poll()
        self.started.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def serve(profile: str) -> None:
    directory = profile_directory(profile)
    try:
        descriptor = exclusive_lock(directory / "service.lock")
    except LockBusyError:
        return
    stop = threading.Event()
    try:
        (directory / "stopping.json").unlink(missing_ok=True)
        config = get_profile_config(profile)
        if consumers(profile, config):
            return
        from .worker import Worker
        worker = Worker(profile, poll_interval=0.25)
        def monitor():
            while not stop.wait(0.25):
                startup = exclusive_lock(directory / "startup.lock", blocking=True)
                try:
                    if not any(locked(path) for path in (directory / "clients").glob("*.lock")):
                        write_private(directory / "stopping.json", {})
                        stop.set()
                finally:
                    os.close(startup)
        observer = threading.Thread(target=monitor, daemon=True)
        observer.start()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        try:
            worker.run(stop_event=stop)
        finally:
            stop.set()
            observer.join(timeout=1)
            from .mcp_bridge import shutdown_mcp_servers
            shutdown_mcp_servers()
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    serve(parser.parse_args().profile)
