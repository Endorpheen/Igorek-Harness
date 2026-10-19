import http.client
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from factory.core import security_levels, security_settings
from factory.core.mobile_bridge.server import API_PREFIX, BridgeConfig, MobileBridge, build_server
from factory.core.mobile_bridge.service import MobileInvalid, MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database


class SecurityLevelAuditMixin:
    def _level_events(self) -> list[dict]:
        with sqlite3.connect(get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM security_events WHERE event_type = 'security_level_changed' "
                "ORDER BY created_at, rowid"
            ).fetchall()
        return [dict(row) for row in rows]

    def _only_details(self) -> dict:
        events = self._level_events()
        self.assertEqual(len(events), 1, events)
        return json.loads(events[0]["details_json"])


class SecuritySettingsServiceTest(SecurityLevelAuditMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"IGOREK_FACTORY_HOME": self._tmp.name, "FACTORY_MOBILE_PROFILE": "cc-local-8081"},
        )
        self._env.start()
        initialize_database(get_db_path())
        self.service = MobileService(profile="cc-local-8081")

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_get_returns_level_baseline_and_available_levels(self):
        payload = self.service.security_settings()

        self.assertEqual(payload["level"], "standard")
        self.assertEqual(payload["baseline"], "standard")
        self.assertFalse(payload["is_weakened"])
        self.assertIsNone(payload["expires_at"])
        self.assertEqual(payload["available_levels"], list(security_levels.LEVELS))

    def test_strengthening_applies_immediately_and_is_audited(self):
        payload = self.service.set_security_level(
            "strict", ttl_s=None, confirm=False, actor="mobile:dev-1"
        )

        self.assertEqual(payload["level"], "strict")
        self.assertFalse(payload["is_weakened"])
        details = self._only_details()
        self.assertEqual(details["level"], "strict")
        self.assertEqual(details["previous"], "standard")
        self.assertEqual(details["actor"], "mobile:dev-1")
        self.assertIsNone(details["expires_at"])

    def test_weakening_without_confirm_is_rejected_and_state_unchanged(self):
        with self.assertRaises(MobileInvalid):
            self.service.set_security_level(
                "full", ttl_s=3600, confirm=False, actor="mobile:dev-1"
            )

        self.assertEqual(security_settings.resolve_security_level()["level"], "standard")
        self.assertEqual(self._level_events(), [])

    def test_weakening_with_confirm_applies_ttl_and_audits(self):
        before = int(time.time())
        payload = self.service.set_security_level(
            "full", ttl_s=3600, confirm=True, actor="mobile:dev-1"
        )
        after = int(time.time())

        self.assertEqual(payload["level"], "full")
        self.assertTrue(payload["is_weakened"])
        self.assertGreaterEqual(payload["expires_at"], before + 3600)
        self.assertLessEqual(payload["expires_at"], after + 3600)
        details = self._only_details()
        self.assertEqual(details["level"], "full")
        self.assertEqual(details["previous"], "standard")
        self.assertEqual(details["actor"], "mobile:dev-1")
        self.assertEqual(details["expires_at"], payload["expires_at"])

    def test_weakening_ttl_above_cap_is_rejected(self):
        with self.assertRaises(MobileInvalid):
            self.service.set_security_level(
                "full", ttl_s=7200, confirm=True, actor="mobile:dev-1"
            )

        self.assertEqual(security_settings.resolve_security_level()["level"], "standard")
        self.assertEqual(self._level_events(), [])

    def test_weakening_without_ttl_uses_bounded_default(self):
        before = int(time.time())
        payload = self.service.set_security_level(
            "full", ttl_s=None, confirm=True, actor="mobile:dev-1"
        )
        after = int(time.time())

        self.assertEqual(payload["level"], "full")
        self.assertIsNotNone(payload["expires_at"])
        self.assertGreaterEqual(payload["expires_at"], before + security_settings.DEFAULT_TTL_S)
        self.assertLessEqual(payload["expires_at"], after + security_settings.DEFAULT_TTL_S)

    def test_non_positive_ttl_is_rejected(self):
        with self.assertRaises(MobileInvalid):
            self.service.set_security_level(
                "full", ttl_s=0, confirm=True, actor="mobile:dev-1"
            )
        with self.assertRaises(MobileInvalid):
            self.service.set_security_level(
                "full", ttl_s=-60, confirm=True, actor="mobile:dev-1"
            )

        self.assertEqual(self._level_events(), [])

    def test_unknown_level_is_rejected_and_state_unchanged(self):
        with self.assertRaises(MobileInvalid):
            self.service.set_security_level(
                "root", ttl_s=None, confirm=True, actor="mobile:dev-1"
            )

        self.assertEqual(security_settings.resolve_security_level()["level"], "standard")
        self.assertEqual(self._level_events(), [])

    def test_no_approval_object_is_created_on_change(self):
        self.service.set_security_level("full", ttl_s=60, confirm=True, actor="mobile:dev-1")

        with sqlite3.connect(get_db_path()) as conn:
            approvals = conn.execute("SELECT COUNT(*) FROM security_approvals").fetchone()[0]
            tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        self.assertEqual(approvals, 0)
        self.assertEqual(tasks, 0)


class SecuritySettingsRouteTest(SecurityLevelAuditMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"IGOREK_FACTORY_HOME": self._tmp.name, "FACTORY_MOBILE_PROFILE": "cc-local-8081"},
        )
        self._env.start()
        initialize_database(get_db_path())
        self.bridge = MobileBridge(config=BridgeConfig(profile="cc-local-8081", poll_interval_s=0.2))
        self.server = build_server(self.bridge, host="127.0.0.1", port=0)
        self.port = int(self.server.server_address[1])
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
        self._env.stop()
        self._tmp.cleanup()

    def _request(self, method: str, path: str, *, body: dict | None = None, token: str | None = None):
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

    def test_security_settings_requires_authentication(self):
        status, _ = self._request("GET", f"{API_PREFIX}/security-settings")
        self.assertEqual(status, 401)
        status, _ = self._request(
            "PATCH", f"{API_PREFIX}/security-settings", body={"level": "standard"}
        )
        self.assertEqual(status, 401)

    def test_get_route_returns_shape(self):
        token, _ = self._pair()

        status, payload = self._request("GET", f"{API_PREFIX}/security-settings", token=token)

        self.assertEqual(status, 200)
        self.assertEqual(payload["level"], "standard")
        self.assertEqual(payload["baseline"], "standard")
        self.assertFalse(payload["is_weakened"])
        self.assertEqual(payload["available_levels"], list(security_levels.LEVELS))

    def test_strengthening_route_applies_and_audits_device_as_actor(self):
        token, device_id = self._pair()

        status, payload = self._request(
            "PATCH",
            f"{API_PREFIX}/security-settings",
            body={"level": "strict"},
            token=token,
        )

        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["level"], "strict")
        details = self._only_details()
        self.assertEqual(details["level"], "strict")
        self.assertEqual(details["actor"], device_id)

    def test_weakening_route_requires_confirm_and_ttl_cap(self):
        token, _ = self._pair()

        status, _ = self._request(
            "PATCH",
            f"{API_PREFIX}/security-settings",
            body={"level": "full", "ttl_s": 3600},
            token=token,
        )
        self.assertEqual(status, 400)
        _, current = self._request("GET", f"{API_PREFIX}/security-settings", token=token)
        self.assertEqual(current["level"], "standard")

        status, _ = self._request(
            "PATCH",
            f"{API_PREFIX}/security-settings",
            body={"level": "full", "ttl_s": 7200, "confirm": True},
            token=token,
        )
        self.assertEqual(status, 400)

        before = int(time.time())
        status, payload = self._request(
            "PATCH",
            f"{API_PREFIX}/security-settings",
            body={"level": "full", "ttl_s": 3600, "confirm": True},
            token=token,
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["level"], "full")
        self.assertTrue(payload["is_weakened"])
        self.assertGreaterEqual(payload["expires_at"], before + 3600)

        _, root = self._request(
            "PATCH",
            f"{API_PREFIX}/security-settings",
            body={"level": "root", "confirm": True},
            token=token,
        )
        self.assertEqual(root['error'], "Unknown security level: 'root'")
        self.assertEqual(root['diagnostic']['code'], 'HTTP.REQUEST_REJECTED')
        self.assertTrue(root['http_request_id'].startswith('req_'))


if __name__ == "__main__":
    unittest.main()
