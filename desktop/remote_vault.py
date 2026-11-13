"""Allow an already paired native client to unlock an existing desktop Vault."""
from __future__ import annotations

import threading
import time


class RemoteVault:
    def __init__(self, owner, *, on_state=None, clock=time.monotonic):
        self.owner = owner
        self.on_state = on_state
        self.clock = clock
        self._lock = threading.RLock()
        self._failures = []

    def status(self):
        state = self.owner.lifecycle.state
        return {"supported": True, "state": state,
                "unlock_available": state == "locked"}

    def allows_requests(self):
        return self.status()["state"] == "unlocked"

    def unlock(self, password, *, authorized=None):
        if not isinstance(password, str) or not password.strip() or len(password) > 1024:
            return 400, {"error": "invalid_unlock_request"}
        with self._lock, self.owner._dispatch_lock:
            if authorized is not None and not authorized():
                return 401, {"error": "device_revoked"}
            current = self.clock()
            self._failures = [stamp for stamp in self._failures if current - stamp < 60]
            if len(self._failures) >= 5:
                retry = max(1, int(60 - (current - self._failures[0])) + 1)
                return 429, {"error": "unlock_rate_limited", "retry_after": retry}
            state = self.status()
            if state["state"] == "not_initialized":
                return 409, {"error": "vault_setup_required"}
            if state["state"] == "unlocked":
                return 200, state
            reply = self.owner.dispatch({"id": "remote-vault", "action": "unlock", "password": password})
            if reply.get("error"):
                self._failures.append(current)
                code = reply["error"]
                if code not in {"authentication_failed", "vault_busy", "migration_required"}:
                    code = "vault_unlock_failed"
                return (403 if code == "authentication_failed" else 409), {"error": code}
            self._failures.clear()
            state = self.status()
            if state["state"] != "unlocked":
                return 409, {"error": "vault_unlock_failed"}
            if self.on_state:
                self.on_state({"state": "unlocked", "interface_locked": bool(self.owner.interface_locked)})
            return 200, state
