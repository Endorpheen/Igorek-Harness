"""OpenCode Go subscription quota in the mobile bridge.

The route `/mobile/v1/opencode-usage` is validated for the following structural properties:
- Requires a device token;
- Returns normalized windows (rolling/weekly/monthly) and `fetched_at`;
- Does not return the OpenCode key or upstream extraneous fields;
- Typed errors are returned with a JSON code instead of a 500 status;
- Shared cache with the web panel (one upstream request for both transports);
- `refresh=1` forces an immediate quota refresh.
"""

import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from factory.core.mobile_bridge.server import API_PREFIX, BridgeConfig, MobileBridge, build_server
from factory.core.opencode_quota import (
    CACHE_LOCK,
    KEY_CACHE,
    USAGE_CACHE,
    OpenCodeUsageError,
    cached_usage_payload,
    fetch_opencode_usage,
)
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database

_PAYLOAD = {
    "usage": {
        "rolling": {"percent": 5, "status": "ok", "resetsAt": "2026-10-01T18:27:14.544Z"},
        "weekly": {"percent": 26, "status": "ok", "resetsAt": "2026-10-05T00:00:00.000Z"},
        "monthly": {"percent": 59, "status": "rate-limited", "resetsAt": "2026-10-17T19:35:03.000Z"},
    }
}


class MobileOpenCodeUsageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"IGOREK_FACTORY_HOME": self._tmp.name, "FACTORY_MOBILE_PROFILE": "cc-local-8081"},
        )
        self._env.start()
        initialize_database(get_db_path())
        with CACHE_LOCK:
            USAGE_CACHE.clear()
            USAGE_CACHE.update({"at": 0.0, "payload": None})
            KEY_CACHE.clear()
            KEY_CACHE.update({"at": 0.0, "key": ""})
        self.bridge = MobileBridge(config=BridgeConfig(profile="cc-local-8081", poll_interval_s=0.2))
        self.server = build_server(self.bridge, host="127.0.0.1", port=0)
        self.port = int(self.server.server_address[1])
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
        self._env.stop()
        self._tmp.cleanup()

    def _request(self, method: str, path: str, *, body: dict | None = None, auth: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if auth is not None:
            headers["Authorization"] = "Bearer " + auth
        payload = json.dumps(body or {}) if body is not None else None
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        parsed = json.loads(raw) if raw else {}
        return response.status, parsed

    def _pair(self) -> str:
        code = self.bridge.store.create_pairing_code()["code"]
        status, payload = self._request(
            "POST", f"{API_PREFIX}/pair/complete", body={"code": code, "name": "Pixel"}
        )
        self.assertEqual(status, 201, payload)
        return payload["token"]

    def test_requires_device_token(self) -> None:
        status, _ = self._request("GET", f"{API_PREFIX}/opencode-usage", auth="nope")

        self.assertEqual(status, 401)

    def test_returns_normalized_windows_without_key(self) -> None:
        auth = self._pair()
        with patch("factory.core.opencode_quota.opencode_api_key", return_value="host-key"), patch(
            "factory.core.opencode_quota.fetch_opencode_usage", return_value=_PAYLOAD
        ) as fetch:
            status, data = self._request("GET", f"{API_PREFIX}/opencode-usage", auth=auth)

        self.assertEqual(status, 200, data)
        self.assertEqual(data["usage"], _PAYLOAD["usage"])
        self.assertIn("fetched_at", data)
        self.assertNotIn("host-key", json.dumps(data))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0], "host-key")

    def test_second_call_is_served_from_shared_cache(self) -> None:
        auth = self._pair()
        with patch("factory.core.opencode_quota.opencode_api_key", return_value="host-key"), patch(
            "factory.core.opencode_quota.fetch_opencode_usage", return_value=_PAYLOAD
        ) as fetch:
            self._request("GET", f"{API_PREFIX}/opencode-usage", auth=auth)
            self._request("GET", f"{API_PREFIX}/opencode-usage", auth=auth)
            # The web panel retrieves its quota from the same cache as the bridge.
            self.assertIsNotNone(cached_usage_payload())
            self._request("GET", f"{API_PREFIX}/opencode-usage?refresh=1", auth=auth)
            self.assertEqual(fetch.call_count, 2)

    def test_missing_key_is_typed_error(self) -> None:
        auth = self._pair()
        with patch("factory.core.opencode_quota.opencode_api_key", return_value=""):
            status, data = self._request("GET", f"{API_PREFIX}/opencode-usage", auth=auth)

        self.assertEqual(status, 503)
        self.assertEqual(data["code"], "no_key")

    def test_upstream_rejection_is_typed_error(self) -> None:
        auth = self._pair()
        error = OpenCodeUsageError("subscription_required", 502, "Нужна подписка OpenCode Go")
        with patch("factory.core.opencode_quota.opencode_api_key", return_value="host-key"), patch(
            "factory.core.opencode_quota.fetch_opencode_usage", side_effect=error
        ):
            status, data = self._request("GET", f"{API_PREFIX}/opencode-usage", auth=auth)

        self.assertEqual(status, 502)
        self.assertEqual(data["code"], "subscription_required")
        self.assertNotIn("host-key", json.dumps(data))

    def test_fetch_uses_mobile_user_agent(self) -> None:
        class _Response:
            def read(self) -> bytes:
                return json.dumps(_PAYLOAD).encode("utf-8")

            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, *args: object) -> bool:
                return False

        with patch("factory.core.opencode_quota.urllib.request.urlopen", return_value=_Response()) as opened:
            fetch_opencode_usage("host-key", user_agent="IgorekFactory mobile-bridge")

        request = opened.call_args.args[0]
        self.assertIsInstance(request, urllib.request.Request)
        self.assertEqual(request.headers.get("User-agent"), "IgorekFactory mobile-bridge")


if __name__ == "__main__":
    unittest.main()
