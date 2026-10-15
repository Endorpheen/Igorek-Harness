#!/usr/bin/env python3
"""Mobile GET/PATCH requests to `/sandbox-settings`."""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.bash_scope_guard import SANDBOX_ENV  # noqa: E402
from factory.core.mobile_bridge.server import (  # noqa: E402
    API_PREFIX,
    BridgeConfig,
    MobileBridge,
    build_server,
)
from factory.core.mobile_bridge.service import MobileService  # noqa: E402
from factory.core.runtime import get_db_path  # noqa: E402
from factory.core.schema import initialize_database  # noqa: E402

_PROBE = "factory.core.bash_sandbox_settings.probe_bwrap_capability"


class _MobileCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_mobile_sandbox_")
        self._prev_sandbox = os.environ.get(SANDBOX_ENV)
        os.environ.pop(SANDBOX_ENV, None)
        self._env = patch.dict(
            os.environ,
            {
                "IGOREK_FACTORY_HOME": self._tmp.name,
                "FACTORY_MOBILE_PROFILE": "cc-local-8081",
            },
            clear=False,
        )
        self._env.start()
        initialize_database(get_db_path())

    def tearDown(self) -> None:
        self._env.stop()
        if self._prev_sandbox is None:
            os.environ.pop(SANDBOX_ENV, None)
        else:
            os.environ[SANDBOX_ENV] = self._prev_sandbox
        self._tmp.cleanup()

    @staticmethod
    def _status_patch(probe: str = "ok"):
        return patch(_PROBE, return_value=probe)


class SandboxSettingsServiceTest(_MobileCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = MobileService(profile="cc-local-8081")

    def test_default_status_shape(self) -> None:
        with self._status_patch("ok"):
            payload = self.service.sandbox_settings()
        self.assertEqual(set(payload), {"enabled", "active", "available", "reason"})
        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["active"])
        self.assertTrue(payload["available"])
        self.assertEqual(payload["reason"], "")

    def test_set_disabled_persists(self) -> None:
        with self._status_patch("ok"):
            payload = self.service.set_sandbox_settings(False)
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["active"])
        self.assertEqual(payload["reason"], "user_disabled")
        with self._status_patch("ok"):
            self.assertFalse(self.service.sandbox_settings()["enabled"])


class SandboxSettingsRouteTest(_MobileCase):
    def setUp(self) -> None:
        super().setUp()
        self.bridge = MobileBridge(
            config=BridgeConfig(profile="cc-local-8081", poll_interval_s=0.2)
        )
        self.server = build_server(self.bridge, host="127.0.0.1", port=0)
        self.port = int(self.server.server_address[1])
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
        super().tearDown()

    def _request(
        self, method: str, path: str, *, body: dict | None = None, token: str | None = None
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = json.dumps(body or {}) if body is not None else None
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, (json.loads(raw) if raw else {})

    def _pair(self) -> tuple[str, str]:
        code = self.bridge.store.create_pairing_code()["code"]
        status, payload = self._request(
            "POST", f"{API_PREFIX}/pair/complete", body={"code": code, "name": "Pixel"}
        )
        self.assertEqual(status, 201, payload)
        device_id = self.bridge.store.list_devices()[0]["device_id"]
        return payload["token"], device_id

    def test_auth_required(self) -> None:
        status, _ = self._request("GET", f"{API_PREFIX}/sandbox-settings")
        self.assertEqual(status, 401)
        status, _ = self._request(
            "PATCH", f"{API_PREFIX}/sandbox-settings", body={"enabled": True}
        )
        self.assertEqual(status, 401)

    def test_get_returns_status_shape(self) -> None:
        token, _ = self._pair()
        with self._status_patch("ok"):
            status, payload = self._request(
                "GET", f"{API_PREFIX}/sandbox-settings", token=token
            )
        self.assertEqual(status, 200)
        self.assertEqual(set(payload), {"enabled", "active", "available", "reason"})
        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["active"])

    def test_patch_invalid_body_is_400(self) -> None:
        token, _ = self._pair()
        status, _ = self._request(
            "PATCH",
            f"{API_PREFIX}/sandbox-settings",
            body={"enabled": "yes"},
            token=token,
        )
        self.assertEqual(status, 400)

    def test_patch_toggles_and_returns_payload(self) -> None:
        token, _ = self._pair()
        with self._status_patch("ok"):
            status, payload = self._request(
                "PATCH",
                f"{API_PREFIX}/sandbox-settings",
                body={"enabled": False},
                token=token,
            )
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["active"])
        self.assertEqual(payload["reason"], "user_disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
