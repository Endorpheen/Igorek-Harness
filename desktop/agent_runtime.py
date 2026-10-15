"""Own one standard queue worker bound to the unlocked desktop vault."""
import json
import os
import tempfile
import threading
from pathlib import Path

from provider_setup import profile_config
from execution_mode import DesktopExecution


class AgentBusyError(Exception):
    pass


class ProfileInUseError(Exception):
    pass


class ProfileNotVerifiedError(Exception):
    pass


class DesktopAgentRuntime:
    def __init__(self, root, lifecycle, service, *, worker_factory=None, execution=None):
        self.root = Path(root)
        self.lifecycle = lifecycle
        self.service = service
        self.worker_factory = worker_factory
        self.execution = execution if execution is not None else DesktopExecution(self.root)
        self._host_workdir = getattr(service, 'workdir', str(self.root / 'workspace'))
        self.sync_workdir()
        self.isolation_manager = None
        self.thread = None
        self.stop_event = None
        self.execution_lock = threading.Lock()
        self.control_lock = threading.RLock()
        self.profile = ""
        self.service.available_profiles = self.available_profiles
        if hasattr(self.service, 'configure_live_isolation_status'):
            self.service.configure_live_isolation_status(self.isolation_status)

    def sync_workdir(self):
        """Keep VM-exportable project data separate from private runtime state."""
        if self.execution.mode == 'kvm':
            from factory.core.artifact_files import parent_handle
            directory = self.root.with_name(self.root.name + '-workspaces')
            with parent_handle(directory / '.entry', create=True):
                pass
            self.service.workdir = str(directory)
        else:
            self.service.workdir = self._host_workdir

    def isolation_status(self):
        if self.execution.mode == 'off':
            return 'none'
        return 'kvm' if self.isolation_manager is not None and self.available_profiles() else 'kvm-unavailable'

    def available_profiles(self):
        return [self.profile] if self.thread and self.thread.is_alive() and not self.stop_event.is_set() else []

    def status(self):
        return {"profile": self.profile, "running": bool(self.available_profiles())}

    def start(self, credential_id):
        with self.control_lock:
            return self._start(credential_id)

    def _start(self, credential_id):
        profiles = self.lifecycle.list_provider_profiles()
        profile = next((item for item in profiles if item["credential_id"] == credential_id), None)
        if profile is None:
            raise ValueError("Unknown provider profile")
        if profile["status"] != "verified":
            raise ProfileNotVerifiedError()
        config = profile["config"]
        validated = profile_config(config["name"], config["model"], config["base_url"], config["api_format"])
        if config != validated:
            raise ValueError("Invalid runtime config")
        if self.available_profiles() == [config["name"]]:
            return self.status()
        self.stop()
        folder = self.root / "profiles"
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder / (config["name"] + ".json")
        if target.exists() and json.loads(target.read_text()) != config:
            raise ValueError("Existing profile differs")
        if not target.exists():
            fd, temporary = tempfile.mkstemp(dir=folder, prefix=".profile-")
            try:
                with os.fdopen(fd, "w") as handle:
                    json.dump(config, handle)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

        def headers(worker_config, api_format):
            if worker_config != config:
                raise ValueError("Worker profile binding changed")
            return self.lifecycle.provider_runtime_headers(credential_id, config, api_format)

        from factory.core.mcp_bridge import set_desktop_access_check
        from mcp_access import McpAccess
        access = McpAccess(self.root, self.lifecycle)
        from factory.core.mcp_tasks.calendar import CalendarConnection
        set_desktop_access_check(lambda server: access.authorize(server, config), access.registry, CalendarConnection(self.lifecycle))
        factory = self.worker_factory
        if factory is None:
            from factory.core.worker import Worker
            factory = Worker
        manager = self.execution.create_manager(self.lifecycle, credential_id, config)
        try:
            options = {"isolation_manager": manager} if manager is not None else {}
            worker = factory(config["name"], poll_interval=1, auth_headers_provider=headers, **options)
            from factory.core.vault.agent_values import AgentValueWriter
            worker.vault_value_writer = AgentValueWriter(self.lifecycle, credential_id, config)
        except Exception:
            if manager is not None:
                manager.close()
            raise
        self.isolation_manager = manager
        self.stop_event = threading.Event()
        self.profile = config["name"]
        def run_worker():
            try:
                worker.run(stop_event=self.stop_event, execution_lock=self.execution_lock)
            finally:
                terminal = getattr(worker, "_terminal_owner", None)
                if manager is not None and terminal is not None:
                    terminal.close()
        self.thread = threading.Thread(target=run_worker,
            name="desktop-worker-" + self.profile, daemon=True)
        self.thread.start()
        self.service.profile = self.profile
        return self.status()

    def prepare_profile_update(self, previous, config):
        if previous["name"] in self.available_profiles():
            raise ProfileInUseError()
        target = self.root / "profiles" / (previous["name"] + ".json")
        if target.is_symlink():
            raise ValueError("Profile publication is a symlink")
        if target.exists():
            if json.loads(target.read_text()) != previous:
                raise ValueError("Published profile changed outside setup")
            if previous != config:
                # Regenerate the public config at the next verified start.
                target.unlink()

    def stop(self):
        with self.control_lock:
            return self._stop()

    def _stop(self):
        if not self.thread:
            if self.isolation_manager is not None:
                self.isolation_manager.close()
                self.isolation_manager = None
            return self.status()
        if not self.execution_lock.acquire(blocking=False):
            raise AgentBusyError()
        try:
            self.stop_event.set()
        finally:
            self.execution_lock.release()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise AgentBusyError()
        self.thread = None
        if self.isolation_manager is not None:
            self.isolation_manager.close()
            self.isolation_manager = None
        self.service.profile = ""
        return self.status()

    def close(self):
        with self.control_lock:
            return self._close()

    def _close(self):
        if self.stop_event:
            self.stop_event.set()
        # The existing task cancellation path stops model/tool work on shutdown.
        if self.thread and self.thread.is_alive():
            from factory.core.runtime import get_db_path
            from factory.core.cli_tasks import cancel_task
            import sqlite3
            with sqlite3.connect(get_db_path()) as conn:
                tasks = conn.execute("SELECT task_id FROM tasks WHERE profile=? AND status IN ('processing','waiting_approval')",
                                     (self.profile,)).fetchall()
            for (task_id,) in tasks:
                cancel_task(task_id)
            self.thread.join(timeout=3)

        if self.isolation_manager is not None:
            self.isolation_manager.close()
            self.isolation_manager = None
