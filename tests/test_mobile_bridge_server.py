import http.client
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from factory.core.cli_chat import _session_path
from factory.core.mobile_bridge.server import API_PREFIX, BridgeConfig, MobileBridge, build_server
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"IGOREK_FACTORY_HOME": self._tmp.name, "FACTORY_MOBILE_PROFILE": "cc-local-8081"},
        )
        self._env.start()
        initialize_database(get_db_path())
        self.session_id = "chat-20260916-140000-srv001"
        path = _session_path(self.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"type": "meta", "session_id": self.session_id, "profile": "cc-local-8081"}) + "\n",
            encoding="utf-8",
        )

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
        parsed = json.loads(raw) if raw else {}
        return response.status, parsed

    def _pair(self, *, name: str = "Pixel") -> str:
        code = self.bridge.store.create_pairing_code()["code"]
        status, payload = self._request(
            "POST", f"{API_PREFIX}/pair/complete", body={"code": code, "name": name}
        )
        self.assertEqual(status, 201, payload)
        return payload["token"]


class AuthFlowTest(ServerTestCase):
    def test_pair_start_rechecks_issuer_after_pending_body(self):
        from factory.core.mobile_bridge.server import MobileRequestHandler
        token = self._pair()
        device = self.bridge.store.authenticate(token)
        read_body = MobileRequestHandler._read_json_body

        def revoke_before_body(handler):
            self.bridge.store.revoke_device(device['device_id'])
            return read_body(handler)

        with patch.object(MobileRequestHandler, '_read_json_body', revoke_before_body):
            status, payload = self._request('POST', f'{API_PREFIX}/pair/start', body={}, token=token)
        self.assertEqual(status, 401, payload)
        with sqlite3.connect(self.bridge.store.db_path) as connection:
            unused = connection.execute('SELECT COUNT(*) FROM mobile_pairing_codes WHERE used_at IS NULL').fetchone()[0]
        self.assertEqual(unused, 0)

    def test_pair_start_binds_new_code_to_authenticated_device(self):
        token = self._pair()
        device = self.bridge.store.authenticate(token)
        status, payload = self._request('POST', f'{API_PREFIX}/pair/start', body={}, token=token)
        self.assertEqual(status, 201, payload)
        self.bridge.store.revoke_device(device['device_id'])
        status, result = self._request('POST', f'{API_PREFIX}/pair/complete', body={'code': payload['code'], 'name': 'Denied'})
        self.assertEqual(status, 400, result)

    def test_health_needs_no_token(self):
        status, payload = self._request("GET", f"{API_PREFIX}/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_sudo_password_is_refused_without_https(self):
        token = self._pair()
        status, payload = self._request(
            "POST",
            f"{API_PREFIX}/sudo-requests/example",
            body={"decision": "allow_once", "password": "test-only-secret"},
            token=token,
        )
        self.assertEqual(status, 403)
        self.assertIn("HTTPS", payload["error"])

    def test_sessions_requires_token(self):
        status, payload = self._request("GET", f"{API_PREFIX}/sessions")

        self.assertEqual(status, 401)
        self.assertIn("invalid", payload["error"])

    def test_pin_is_boolean_authenticated_and_persisted(self):
        status,_=self._request('PATCH',f'{API_PREFIX}/sessions/{self.session_id}',body={'pinned':True})
        self.assertEqual(status,401)
        token=self._pair()
        status,_=self._request('PATCH',f'{API_PREFIX}/sessions/{self.session_id}',body={'pinned':'false'},token=token)
        self.assertEqual(status,400)
        status,result=self._request('PATCH',f'{API_PREFIX}/sessions/{self.session_id}',body={'pinned':True},token=token)
        self.assertEqual(status,200);self.assertTrue(result['pinned'])
        status,result=self._request('GET',f'{API_PREFIX}/sessions',token=token)
        self.assertTrue(result['sessions'][0]['pinned'])



    def test_pairing_then_authorized_access(self):
        token = self._pair()

        status, payload = self._request("GET", f"{API_PREFIX}/sessions", token=token)

        self.assertEqual(status, 200)
        self.assertEqual([s["session_id"] for s in payload["sessions"]], [self.session_id])

    def test_bad_pairing_code_rejected(self):
        status, payload = self._request(
            "POST", f"{API_PREFIX}/pair/complete", body={"code": "0000-0000-0000", "name": "X"}
        )

        self.assertEqual(status, 400)
        self.assertIn("invalid", payload["error"])

    def test_lockout_after_repeated_failures(self):
        for _ in range(10):
            self._request("GET", f"{API_PREFIX}/sessions", token="wrong")

        status, _ = self._request("GET", f"{API_PREFIX}/sessions", token="wrong")

        self.assertEqual(status, 429)

    def test_revoked_device_loses_access(self):
        token = self._pair()
        devices = self.bridge.store.list_devices()
        device_id = devices[0]["device_id"]
        status, _ = self._request("DELETE", f"{API_PREFIX}/devices/{device_id}", token=token)
        self.assertEqual(status, 200)

        status, _ = self._request("GET", f"{API_PREFIX}/sessions", token=token)

        self.assertEqual(status, 401)

    def test_devices_listing(self):
        token = self._pair(name="MyPhone")

        status, payload = self._request("GET", f"{API_PREFIX}/devices", token=token)

        self.assertEqual(status, 200)
        self.assertEqual(len(payload["devices"]), 1)
        self.assertEqual(payload["devices"][0]["name"], "MyPhone")

    def test_devices_listing_reports_current_device(self):
        token = self._pair(name="MyPhone")

        status, payload = self._request("GET", f"{API_PREFIX}/devices", token=token)

        self.assertEqual(status, 200)
        self.assertEqual(payload["current_device_id"], payload["devices"][0]["device_id"])

    def test_purge_revoked_devices_keeps_active_and_clears_the_rest(self):
        token = self._pair(name="MyPhone")
        self._pair(name="OldPhone")
        self._pair(name="OlderPhone")
        for device in self.bridge.store.list_devices():
            if device["name"] != "MyPhone":
                self.bridge.store.revoke_device(device["device_id"])

        status, payload = self._request("POST", f"{API_PREFIX}/devices/purge-revoked", token=token)

        self.assertEqual(status, 200)
        self.assertEqual(payload["deleted"], 2)
        status, payload = self._request("GET", f"{API_PREFIX}/devices", token=token)
        self.assertEqual(status, 200)
        self.assertEqual([d["name"] for d in payload["devices"]], ["MyPhone"])

    def test_delete_device_with_purge_flag_removes_the_row(self):
        token = self._pair(name="MyPhone")
        self._pair(name="OldPhone")
        target = next(d for d in self.bridge.store.list_devices() if d["name"] == "OldPhone")

        status, payload = self._request(
            "DELETE", f"{API_PREFIX}/devices/{target['device_id']}?purge=1", token=token
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])
        remaining = self.bridge.store.list_devices(include_revoked=True)
        self.assertEqual([d["name"] for d in remaining], ["MyPhone"])

    def test_current_device_cannot_be_purged(self):
        token = self._pair(name="MyPhone")
        own = self.bridge.store.list_devices()[0]["device_id"]

        status, payload = self._request("DELETE", f"{API_PREFIX}/devices/{own}?purge=1", token=token)

        self.assertEqual(status, 409)
        self.assertIn("cannot delete", payload["error"])


class SessionRoutesTest(ServerTestCase):
    def test_approval_settings_round_trip(self):
        token = self._pair()

        status, initial = self._request("GET", f"{API_PREFIX}/approval-settings", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(initial["enabled"])

        status, updated = self._request(
            "PATCH",
            f"{API_PREFIX}/approval-settings",
            body={"enabled": False},
            token=token,
        )
        self.assertEqual(status, 200)
        self.assertFalse(updated["enabled"])

        status, persisted = self._request("GET", f"{API_PREFIX}/approval-settings", token=token)
        self.assertEqual(status, 200)
        self.assertFalse(persisted["enabled"])

    def test_approval_settings_reject_non_boolean_value(self):
        token = self._pair()

        status, payload = self._request(
            "PATCH",
            f"{API_PREFIX}/approval-settings",
            body={"enabled": "false"},
            token=token,
        )

        self.assertEqual(status, 400)
        self.assertIn("boolean", payload["error"])

    def test_external_session_routes_are_authenticated_and_source_aware(self):
        status, _ = self._request("GET", f"{API_PREFIX}/external-sessions")
        self.assertEqual(status, 401)
        token = self._pair()
        listing = {"sessions": [{"source": "codex", "session_id": "thread-1"}], "total": 1}
        detail = {"source": "codex", "session_id": "thread-1", "messages": []}
        with (
            patch.object(self.bridge.service, "external_sessions", return_value=listing) as list_mock,
            patch.object(self.bridge.service, "get_external_session", return_value=detail) as get_mock,
        ):
            status, listed = self._request(
                "GET", f"{API_PREFIX}/external-sessions?source=codex&refresh=1", token=token
            )
            self.assertEqual(status, 200)
            status, opened = self._request(
                "GET", f"{API_PREFIX}/external-sessions/codex/thread-1", token=token
            )

        self.assertEqual(status, 200)
        self.assertEqual(listed, listing)
        self.assertEqual(opened, detail)
        list_mock.assert_called_once_with(source="codex", query="", limit=250, refresh=True)
        get_mock.assert_called_once_with("codex", "thread-1", offset=0, limit=400)

    def test_external_session_import_route_creates_internal_session(self):
        token = self._pair()
        expected = {"session": {"session_id": "import-codex-1"}, "imported_messages": 2}
        with patch.object(
            self.bridge.service,
            "import_external_session",
            return_value=expected,
        ) as mocked:
            status, payload = self._request(
                "POST",
                f"{API_PREFIX}/external-sessions/codex/thread-1/import",
                body={
                    "profile": "cc-local-8081",
                    "cwd": self._tmp.name,
                    "idempotency_key": "import-key",
                },
                token=token,
            )

        self.assertEqual(status, 201)
        self.assertEqual(payload, expected)
        mocked.assert_called_once_with(
            "codex",
            "thread-1",
            profile="cc-local-8081",
            cwd=self._tmp.name,
            idempotency_key="import-key",
        )

    def test_session_options_are_authenticated_and_use_bridge_defaults(self):
        status, _ = self._request("GET", f"{API_PREFIX}/session-options")
        self.assertEqual(status, 401)
        token = self._pair()

        status, payload = self._request("GET", f"{API_PREFIX}/session-options", token=token)

        self.assertEqual(status, 200)
        self.assertEqual(payload["default_profile"], "cc-local-8081")
        self.assertIn("cc-local-8081", payload["profiles"])

    def test_extensions_are_authenticated_and_profile_aware(self):
        token = self._pair()
        expected = {
            "profile": "cc-glm",
            "backend": "cc-backend",
            "skills": [],
            "mcp_servers": [],
            "hooks": [],
            "hooks_available": False,
        }
        with patch.object(self.bridge.service, "extensions", return_value=expected) as mocked:
            status, payload = self._request(
                "GET",
                f"{API_PREFIX}/extensions?profile=cc-glm",
                token=token,
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        mocked.assert_called_once_with(profile="cc-glm")

    def test_skill_enabled_route_requires_boolean(self):
        token = self._pair()

        status, payload = self._request(
            "PATCH",
            f"{API_PREFIX}/extensions/skills/news_rss",
            body={"enabled": "yes"},
            token=token,
        )

        self.assertEqual(status, 400)
        self.assertIn("boolean", payload["error"])

    def test_skill_enabled_route_updates_skill(self):
        token = self._pair()
        expected = {"skill": {"skill_id": "news_rss", "enabled": False}}
        with patch.object(self.bridge.service, "set_skill_enabled", return_value=expected) as mocked:
            status, payload = self._request(
                "PATCH",
                f"{API_PREFIX}/extensions/skills/news_rss",
                body={"enabled": False},
                token=token,
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        mocked.assert_called_once_with("news_rss", enabled=False)

    def test_create_session_retry_and_send_first_message(self):
        token = self._pair()
        body = {"cwd": self._tmp.name, "idempotency_key": "new-chat-1"}
        status, created = self._request("POST", f"{API_PREFIX}/sessions", body=body, token=token)
        self.assertEqual(status, 201)
        self.assertEqual(created["messages"], [])
        self.assertEqual(created["meta"]["profile"], "cc-local-8081")
        self.assertEqual(created["meta"]["cwd"], self._tmp.name)
        _, replay = self._request("POST", f"{API_PREFIX}/sessions", body=body, token=token)
        self.assertEqual(created["session_id"], replay["session_id"])
        status, sent = self._request("POST", f"{API_PREFIX}/sessions/{created['session_id']}/messages",
                                    body={"text": "Привет из нового чата"}, token=token)
        self.assertEqual(status, 202)
        self.assertTrue(sent["task_id"])
        _, listing = self._request("GET", f"{API_PREFIX}/sessions", token=token)
        self.assertIn(created["session_id"], [s["session_id"] for s in listing["sessions"]])

    def test_create_session_rejects_invalid_directory(self):
        status, _ = self._request("POST", f"{API_PREFIX}/sessions",
                                  body={"cwd": "/nonexistent-mobile-session-dir"}, token=self._pair())
        self.assertEqual(status, 400)

    def test_create_session_requires_authentication(self):
        status, _ = self._request("POST", f"{API_PREFIX}/sessions", body={})
        self.assertEqual(status, 401)

    def test_get_session_detail(self):
        token = self._pair()

        status, payload = self._request("GET", f"{API_PREFIX}/sessions/{self.session_id}", token=token)

        self.assertEqual(status, 200)
        self.assertEqual(payload["meta"]["profile"], "cc-local-8081")

    def test_session_events_endpoint_returns_journaled_events(self):
        token = self._pair()
        self.bridge.store.append_event(
            kind="tool.request_batch",
            payload={"round": 1, "tools": [{"id": "c1", "name": "Bash", "input": {"command": "ls"}}]},
            session_id=self.session_id,
            task_id="task-ev",
        )
        self.bridge.store.append_event(
            kind="task.finish",
            payload={"task_id": "task-ev", "status": "ok"},
            session_id=self.session_id,
            task_id="task-ev",
        )

        status, payload = self._request(
            "GET", f"{API_PREFIX}/sessions/{self.session_id}/events?after=0", token=token
        )

        self.assertEqual(status, 200)
        kinds = [event["kind"] for event in payload["events"]]
        self.assertEqual(kinds, ["tool.request_batch", "task.finish"])

    def test_session_events_respects_after_cursor(self):
        token = self._pair()
        first = self.bridge.store.append_event(kind="task.submitted", payload={}, session_id=self.session_id)
        self.bridge.store.append_event(kind="task.finish", payload={}, session_id=self.session_id)

        status, payload = self._request(
            "GET", f"{API_PREFIX}/sessions/{self.session_id}/events?after={first}", token=token
        )

        self.assertEqual(status, 200)
        self.assertEqual([event["kind"] for event in payload["events"]], ["task.finish"])

    def test_unknown_session_returns_404(self):
        token = self._pair()

        status, _ = self._request("GET", f"{API_PREFIX}/sessions/chat-nope", token=token)

        self.assertEqual(status, 404)

    def test_send_message_creates_task(self):
        token = self._pair()

        status, payload = self._request(
            "POST",
            f"{API_PREFIX}/sessions/{self.session_id}/messages",
            body={"text": "посчитай строки", "idempotency_key": "abc-1"},
            token=token,
        )

        self.assertEqual(status, 202, payload)
        self.assertTrue(payload["task_id"])
        self.assertFalse(payload["duplicate"])
        with sqlite3.connect(get_db_path()) as conn:
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        self.assertEqual(count, 1)

    def test_send_message_carries_plan_mode_into_task_metadata(self):
        token = self._pair()

        def finish(task_id: str) -> None:
            with sqlite3.connect(get_db_path()) as conn:
                conn.execute("UPDATE tasks SET status = 'ok' WHERE task_id = ?", (task_id,))

        status, plan = self._request(
            "POST",
            f"{API_PREFIX}/sessions/{self.session_id}/messages",
            body={"text": "только план", "plan_mode": True},
            token=token,
        )
        self.assertEqual(status, 202, plan)
        finish(plan["task_id"])
        status, execute = self._request(
            "POST",
            f"{API_PREFIX}/sessions/{self.session_id}/messages",
            body={"text": "выполнить", "plan_mode": False},
            token=token,
        )
        self.assertEqual(status, 202, execute)
        finish(execute["task_id"])
        status, invalid = self._request(
            "POST",
            f"{API_PREFIX}/sessions/{self.session_id}/messages",
            body={"text": "не булево", "plan_mode": "yes"},
            token=token,
        )
        self.assertEqual(status, 202, invalid)

        with sqlite3.connect(get_db_path()) as conn:
            rows = {
                row[0]: json.loads(row[1])
                for row in conn.execute(
                    "SELECT task_id, metadata FROM tasks WHERE task_id IN (?, ?, ?)",
                    (plan["task_id"], execute["task_id"], invalid["task_id"]),
                )
            }
        self.assertNotEqual(plan["task_id"], execute["task_id"])
        self.assertIs(rows[plan["task_id"]]["plan_mode"], True)
        self.assertNotIn("plan_mode", rows[execute["task_id"]])
        self.assertNotIn("plan_mode", rows[invalid["task_id"]])

    def test_send_message_is_idempotent(self):
        token = self._pair()
        body = {"text": "once", "idempotency_key": "same-key"}

        first = self._request("POST", f"{API_PREFIX}/sessions/{self.session_id}/messages", body=body, token=token)
        second = self._request("POST", f"{API_PREFIX}/sessions/{self.session_id}/messages", body=body, token=token)

        self.assertEqual(first[1]["task_id"], second[1]["task_id"])
        self.assertTrue(second[1]["duplicate"])
        with sqlite3.connect(get_db_path()) as conn:
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        self.assertEqual(count, 1)

    def test_send_empty_message_rejected(self):
        token = self._pair()

        status, _ = self._request(
            "POST", f"{API_PREFIX}/sessions/{self.session_id}/messages", body={"text": "  "}, token=token
        )

        self.assertEqual(status, 400)

    def test_task_and_cancel_routes(self):
        token = self._pair()
        _, created = self._request(
            "POST", f"{API_PREFIX}/sessions/{self.session_id}/messages", body={"text": "работа"}, token=token
        )
        task_id = created["task_id"]

        status, task = self._request("GET", f"{API_PREFIX}/tasks/{task_id}", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(task["status"], "queued")

        status, cancelled = self._request(
            "POST", f"{API_PREFIX}/tasks/{task_id}/cancel", body={"mode": "cancel"}, token=token
        )
        self.assertEqual(status, 200)
        self.assertTrue(cancelled["cancelled"])

    def test_session_stop_route_finishes_all_active_tasks(self):
        token = self._pair()
        task_ids = []
        for text in ("первая", "вторая"):
            _, created = self._request(
                "POST",
                f"{API_PREFIX}/sessions/{self.session_id}/messages",
                body={"text": text},
                token=token,
            )
            task_ids.append(created["task_id"])
        from factory.core.cli_tasks import create_task

        if task_ids[0] == task_ids[1]:
            # A busy session reuses its task ID, so prepare a second active task
            # directly for tests that need two distinct sessions.
            task_ids[1] = create_task(
                text="вторая",
                profile="cc-local-8081",
                metadata={"session": self.session_id, "source": "mobile_bridge"},
            )["task_id"]
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status = 'processing' WHERE task_id = ?", (task_ids[0],))
            conn.execute("UPDATE tasks SET status = 'cancelling' WHERE task_id = ?", (task_ids[1],))

        status, result = self._request(
            "POST",
            f"{API_PREFIX}/sessions/{self.session_id}/cancel",
            body={"mode": "stop"},
            token=token,
        )

        self.assertEqual(status, 200, result)
        self.assertEqual(result["affected"], 2)
        self.assertEqual(result["terminal"], 2)


class ApprovalRoutesTest(ServerTestCase):
    def _blocked_task(self) -> str:
        from factory.core.cli_tasks import create_task

        created = create_task(text="опасное", profile="cc-local-8081", metadata={"session": self.session_id})
        task_id = created["task_id"]
        blocked = [
            {
                "tool": "Bash",
                "input": {"command": "rm -rf /"},
                "target": "rm -rf /",
                "signature": 'Bash:{"command":"rm -rf /"}',
                "tool_use_id": "call_http_1",
                "source": "tool_call",
                "reason": "deletive",
            }
        ]
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'waiting_approval', approval_blocked_tools = ? WHERE task_id = ?",
                (json.dumps(blocked), task_id),
            )
        return task_id

    def test_approval_listing_and_resolution(self):
        token = self._pair()
        task_id = self._blocked_task()

        status, payload = self._request("GET", f"{API_PREFIX}/approvals", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["approvals"]), 1)
        self.assertEqual(payload["approvals"][0]["task_id"], task_id)
        self.assertTrue(payload["approvals"][0]["dangerous"])

        status, resolved = self._request(
            "POST",
            f"{API_PREFIX}/tasks/{task_id}/approvals/call_http_1",
            body={"decision": "allow_once"},
            token=token,
        )
        self.assertEqual(status, 200, resolved)
        self.assertTrue(resolved["resumed_task_id"])

        status, _ = self._request(
            "POST",
            f"{API_PREFIX}/tasks/{task_id}/approvals/call_http_1",
            body={"decision": "allow_once"},
            token=token,
        )
        self.assertEqual(status, 409)

    def test_unknown_task_returns_404(self):
        token = self._pair()

        status, _ = self._request(
            "POST", f"{API_PREFIX}/tasks/nope/approvals/call_1", body={"decision": "allow_once"}, token=token
        )

        self.assertEqual(status, 404)


class EventStreamTest(ServerTestCase):
    def test_stream_replays_more_than_one_page_and_polls_service_writes(self):
        token = self._pair()
        expected = [self.bridge.store.append_event(kind="task.submitted", payload={"i": i},
                    session_id=self.session_id) for i in range(503)]
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", f"{API_PREFIX}/events?after=0",
                           headers={"Authorization": f"Bearer {token}"})
        response = connection.getresponse()
        try:
            received = []
            while len(received) < len(expected):
                line = response.readline().decode().strip()
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    if "seq" in event:
                        received.append(event["seq"])
            self.assertEqual(received, expected)
            seq = self.bridge.store.append_event(kind="approval.resolved", payload={}, session_id=self.session_id)
            while True:
                line = response.readline().decode().strip()
                if line.startswith("data: "):
                    self.assertEqual(json.loads(line[6:])["seq"], seq)
                    break
        finally:
            response.close()
            connection.close()

    def test_invalid_stream_cursor_returns_json_error(self):
        status, payload = self._request("GET", f"{API_PREFIX}/events?after=oops", token=self._pair())
        self.assertEqual(status, 400)
        self.assertIn("integer", payload["error"])

    def test_stream_replays_journaled_events(self):
        token = self._pair()
        self.bridge.store.append_event(
            kind="tool.result",
            payload={"tool_name": "Bash", "status": "exit"},
            session_id=self.session_id,
            task_id="task-1",
        )

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request(
            "GET",
            f"{API_PREFIX}/events?after=0",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        lines = []
        try:
            for _ in range(4):
                line = response.readline().decode("utf-8").strip()
                if line:
                    lines.append(line)
                if any("tool.result" in item for item in lines):
                    break
        finally:
            connection.close()

        self.assertTrue(any("stream.ready" in item for item in lines), lines)
        self.assertTrue(any("tool.result" in item for item in lines), lines)


class UploadRoutesTest(ServerTestCase):
    def _download_upload(self, token: str, session_id: str, upload_id: str):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", f"{API_PREFIX}/sessions/{session_id}/uploads/{upload_id}",
                           headers={"Authorization": f"Bearer {token}"})
        response = connection.getresponse()
        result = response.status, dict(response.headers), response.read()
        connection.close()
        return result

    def test_uploaded_image_download_is_authenticated_and_scoped_to_session(self):
        token = self._pair()
        session = self._session(token)
        data = b"\x89PNG\r\n\x1a\nfixture"
        _, upload = self._upload(token, session, name="screen.png", mime="image/png", data=data)
        self.assertEqual(self._download_upload("wrong-token", session, upload["upload_id"])[0], 401)
        status, headers, body = self._download_upload(token, session, upload["upload_id"])
        self.assertEqual(status, 200)
        self.assertEqual(body, data)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Cache-Control"], "private, no-store")
        _, other = self._request("POST", f"{API_PREFIX}/sessions", token=token,
                                 body={"cwd": self._tmp.name, "idempotency_key": "other-upload-session"})
        self.assertEqual(self._download_upload(token, other["session_id"], upload["upload_id"])[0], 404)
        self.assertEqual(self._download_upload(token, session, "../index.json")[0], 404)

    def test_upload_download_refuses_removed_or_replaced_file(self):
        token = self._pair()
        session = self._session(token)
        _, upload = self._upload(token, session)
        path = Path(upload["path"])
        path.unlink()
        self.assertEqual(self._download_upload(token, session, upload["upload_id"])[0], 404)
        outside = Path(self._tmp.name) / "private.jpg"
        outside.write_bytes(b"private bytes")
        path.symlink_to(outside)
        self.assertEqual(self._download_upload(token, session, upload["upload_id"])[0], 404)

    def test_active_content_upload_is_never_served_as_html_or_svg(self):
        token = self._pair()
        session = self._session(token)
        _, upload = self._upload(token, session, name="image.svg", mime="image/svg+xml", data=b"<svg/>")
        status, headers, body = self._download_upload(token, session, upload["upload_id"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(body, b"<svg/>")

    def test_attachments_survive_journal_reload_and_stay_on_exact_queued_message(self):
        token = self._pair()
        session = self._session(token)
        ids = []
        for text, name in [("first screenshot", "first.png"), ("", "second.png")]:
            _, upload = self._upload(token, session, name=name, mime="image/png")
            ids.append(upload["upload_id"])
            status, sent = self._request("POST", f"{API_PREFIX}/sessions/{session}/messages", token=token,
                                         body={"text": text, "upload_ids": [ids[-1]]})
            self.assertEqual(status, 202, sent)
        self.assertTrue(sent["steered"])
        status, snapshot = self._request("GET", f"{API_PREFIX}/sessions/{session}", token=token)
        self.assertEqual(status, 200)
        messages = [item for item in snapshot["messages"] if item["role"] == "user"]
        self.assertEqual([item["attachments"][0]["upload_id"] for item in messages], ids)
        self.assertTrue(all("path" not in item["attachments"][0] for item in messages))
        events = self.bridge.store.list_events(session_id=session)
        sent_events = [item for item in events if item["kind"] in {"task.submitted", "task.message_added"}]
        self.assertEqual([item["payload"]["attachments"][0]["upload_id"] for item in sent_events], ids)
        path = _session_path(session)
        journal = [json.loads(line) for line in path.read_text().splitlines()]
        for item in journal:
            if item.get("role") == "user" and not item.get("steer"):
                item.pop("attachments", None)
        path.write_text("\n".join(json.dumps(item) for item in journal) + "\n")
        restored = self.bridge.service.get_session(session)["messages"]
        self.assertEqual([item["attachments"][0]["upload_id"] for item in restored if item["role"] == "user"], ids)

    def _session(self, token: str) -> str:
        status, created = self._request(
            "POST",
            f"{API_PREFIX}/sessions",
            body={"cwd": self._tmp.name, "idempotency_key": "attach-1"},
            token=token,
        )
        self.assertEqual(status, 201, created)
        return created["session_id"]

    def _upload(self, token: str, session_id: str, *, name: str = "фото.jpg",
                mime: str = "image/jpeg", data: bytes = b"\xff\xd8\xff\xe0data"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request(
            "POST",
            f"{API_PREFIX}/sessions/{session_id}/uploads",
            body=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": mime,
                "X-File-Name": quote(name, safe=""),
            },
        )
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, (json.loads(raw) if raw else {})

    def test_upload_saves_file_and_returns_descriptor(self):
        token = self._pair()
        session_id = self._session(token)

        status, payload = self._upload(token, session_id, name="фото.jpg", mime="image/jpeg")

        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["name"], "фото.jpg")
        self.assertEqual(payload["kind"], "image")
        self.assertEqual(payload["mime_type"], "image/jpeg")
        stored = Path(payload["path"])
        self.assertTrue(stored.is_file())
        self.assertTrue(stored.is_relative_to(Path(self._tmp.name)))

    def test_upload_requires_authentication(self):
        session_id = self._session(self._pair())

        status, _ = self._upload("wrong-token", session_id)

        self.assertEqual(status, 401)

    def test_upload_unknown_session_returns_404(self):
        status, _ = self._upload(self._pair(), "chat-nope")

        self.assertEqual(status, 404)

    def test_send_message_attaches_uploaded_files(self):
        token = self._pair()
        session_id = self._session(token)
        _, uploaded = self._upload(token, session_id, name="смета.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertEqual(uploaded["kind"], "file")

        status, sent = self._request(
            "POST",
            f"{API_PREFIX}/sessions/{session_id}/messages",
            body={"text": "посмотри таблицу", "upload_ids": [uploaded["upload_id"]]},
            token=token,
        )

        self.assertEqual(status, 202, sent)
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT input_files, input_text, metadata FROM tasks WHERE task_id = ?",
                (sent["task_id"],),
            ).fetchone()
        self.assertIn(uploaded["path"], json.loads(row[0]))
        self.assertIn("Вложения из мобильного приложения", row[1])
        self.assertIn("смета.xlsx", row[1])
        metadata = json.loads(row[2])
        self.assertEqual(metadata["attachments"][0]["upload_id"], uploaded["upload_id"])

    def test_send_attachment_without_text_is_allowed(self):
        token = self._pair()
        session_id = self._session(token)
        _, uploaded = self._upload(token, session_id, name="фото.jpg", mime="image/jpeg")

        status, sent = self._request(
            "POST",
            f"{API_PREFIX}/sessions/{session_id}/messages",
            body={"text": "", "upload_ids": [uploaded["upload_id"]]},
            token=token,
        )

        self.assertEqual(status, 202, sent)
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT input_files, input_text FROM tasks WHERE task_id = ?",
                (sent["task_id"],),
            ).fetchone()
        self.assertIn(uploaded["path"], json.loads(row[0]))
        self.assertIn("Вложения из мобильного приложения", row[1])

    def test_send_message_ignores_unknown_upload_id(self):
        token = self._pair()
        session_id = self._session(token)

        status, sent = self._request(
            "POST",
            f"{API_PREFIX}/sessions/{session_id}/messages",
            body={"text": "без вложений", "upload_ids": ["does-not-exist"]},
            token=token,
        )

        self.assertEqual(status, 202, sent)
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT input_files, input_text FROM tasks WHERE task_id = ?",
                (sent["task_id"],),
            ).fetchone()
        self.assertEqual(json.loads(row[0]), [])
        self.assertNotIn("Вложения из мобильного приложения", row[1])


class SessionMetaRoutesTest(ServerTestCase):
    def _session(self, token: str) -> str:
        status, created = self._request(
            "POST",
            f"{API_PREFIX}/sessions",
            body={"cwd": self._tmp.name, "idempotency_key": "meta-1"},
            token=token,
        )
        self.assertEqual(status, 201, created)
        return created["session_id"]

    def test_rename_updates_title_everywhere(self):
        token = self._pair()
        session_id = self._session(token)

        status, updated = self._request(
            "PATCH", f"{API_PREFIX}/sessions/{session_id}", body={"title": "Финансы"}, token=token
        )

        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["title"], "Финансы")
        _, detail = self._request("GET", f"{API_PREFIX}/sessions/{session_id}", token=token)
        self.assertEqual(detail["meta"]["title"], "Финансы")
        _, listing = self._request("GET", f"{API_PREFIX}/sessions", token=token)
        titles = {item["session_id"]: item["title"] for item in listing["sessions"]}
        self.assertEqual(titles[session_id], "Финансы")

    def test_archive_hides_from_active_and_shows_in_archive(self):
        token = self._pair()
        session_id = self._session(token)

        status, updated = self._request(
            "PATCH", f"{API_PREFIX}/sessions/{session_id}", body={"archived": True}, token=token
        )
        self.assertEqual(status, 200, updated)
        self.assertTrue(updated["archived"])

        _, active = self._request("GET", f"{API_PREFIX}/sessions", token=token)
        self.assertNotIn(session_id, [item["session_id"] for item in active["sessions"]])
        _, archived = self._request("GET", f"{API_PREFIX}/sessions?archived=1", token=token)
        self.assertIn(session_id, [item["session_id"] for item in archived["sessions"]])

        self._request("PATCH", f"{API_PREFIX}/sessions/{session_id}", body={"archived": False}, token=token)
        _, restored = self._request("GET", f"{API_PREFIX}/sessions", token=token)
        self.assertIn(session_id, [item["session_id"] for item in restored["sessions"]])

    def test_update_unknown_session_returns_404(self):
        token = self._pair()

        status, _ = self._request("PATCH", f"{API_PREFIX}/sessions/chat-nope", body={"title": "x"}, token=token)

        self.assertEqual(status, 404)

    def test_update_requires_authentication(self):
        status, _ = self._request("PATCH", f"{API_PREFIX}/sessions/{self.session_id}", body={"title": "x"})

        self.assertEqual(status, 401)

    def test_bulk_archive_updates_idle_and_skips_active(self):
        token = self._pair()
        idle_session = self._session(token)
        status, _ = self._request(
            "POST",
            f"{API_PREFIX}/sessions/{self.session_id}/messages",
            body={"text": "долгая задача", "idempotency_key": "bulk-active"},
            token=token,
        )
        self.assertEqual(status, 202)

        status, overview = self._request("GET", f"{API_PREFIX}/sessions", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(overview["total"], 2)
        self.assertEqual(overview["archive_candidate_count"], 1)
        self.assertEqual(overview["active_count"], 1)

        status, result = self._request(
            "PATCH", f"{API_PREFIX}/sessions", body={"archived": True}, token=token
        )

        self.assertEqual(status, 200, result)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped_active"], 1)
        self.assertEqual(result["session_ids"], [idle_session])

    def test_bulk_archive_requires_authentication_and_boolean(self):
        status, _ = self._request("PATCH", f"{API_PREFIX}/sessions", body={"archived": True})
        self.assertEqual(status, 401)

        token = self._pair()
        status, payload = self._request(
            "PATCH", f"{API_PREFIX}/sessions", body={"archived": "yes"}, token=token
        )
        self.assertEqual(status, 400)
        self.assertIn("boolean", payload["error"])


class FileDownloadTest(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.export_dir = Path(self._tmp.name) / "exports"
        self.export_dir.mkdir()
        self.bridge.service.workdir = str(self.export_dir)
        self.output = self.export_dir / "отчёт.txt"
        self.output.write_bytes("Файл с компьютера\n".encode())
        from factory.core.cli_chat import _append_entry
        _append_entry(_session_path(self.session_id), role="assistant", text=f"Готово: {self.output}")

    def _files(self, token):
        return self._request("GET", f"{API_PREFIX}/sessions/{self.session_id}/files", token=token)

    def test_download_exact_bytes_with_filename_and_authentication(self):
        token = self._pair()
        status, payload = self._files(token)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["files"]), 1)
        attachment = payload["files"][0]
        self.assertEqual(attachment["name"], "отчёт.txt")
        self.assertNotIn("path", attachment)
        route = f"{API_PREFIX}/sessions/{self.session_id}/files/{attachment['id']}"
        self.assertEqual(self._request("GET", route)[0], 401)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", route, headers={"Authorization": f"Bearer {token}"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn("filename*=UTF-8''", response.getheader("Content-Disposition"))
            self.assertEqual(response.read(), self.output.read_bytes())
        finally:
            connection.close()

    def test_file_not_accessible_from_another_session(self):
        token = self._pair()
        attachment = self._files(token)[1]["files"][0]
        other = self.bridge.service.create_session(cwd=str(self.export_dir))["session_id"]
        status, _ = self._request("GET", f"{API_PREFIX}/sessions/{other}/files/{attachment['id']}", token=token)
        self.assertEqual(status, 404)

    def test_changed_file_requires_new_identifier(self):
        token = self._pair()
        old = self._files(token)[1]["files"][0]["id"]
        self.output.write_bytes(b"new content")
        status, _ = self._request("GET", f"{API_PREFIX}/sessions/{self.session_id}/files/{old}", token=token)
        self.assertEqual(status, 404)
        self.assertNotEqual(old, self._files(token)[1]["files"][0]["id"])

    def test_outside_files_secrets_and_symlinks_are_not_listed(self):
        from factory.core.cli_chat import _append_entry
        outside = Path(self._tmp.name) / "private.txt"
        outside.write_text("private")
        link = self.export_dir / "linked.txt"
        link.symlink_to(outside)
        secret = self.export_dir / ".env"
        secret.write_text("private")
        _append_entry(_session_path(self.session_id), role="assistant", text=f"{outside} {link} {secret}")
        files = self._files(self._pair())[1]["files"]
        self.assertEqual([item["name"] for item in files], [self.output.name])

    def test_symlink_replacement_after_listing_is_rejected(self):
        from factory.core.mobile_bridge.files import describe_file, open_file_safely
        info = describe_file(self.output)
        outside = Path(self._tmp.name) / "private.txt"
        outside.write_text("private")
        self.output.unlink()
        self.output.symlink_to(outside)
        with self.assertRaises(OSError):
            open_file_safely(self.output, info)

    def test_finished_task_output_is_available_before_history_finalization(self):
        from factory.core.cli_tasks import create_task
        task = create_task(text="create", profile="cc-local-8081",
                           metadata={"session": self.session_id, "cwd": str(self.export_dir)})
        second = self.export_dir / "result.csv"
        second.write_text("x,y\n1,2\n")
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status = 'ok', output_text = ? WHERE task_id = ?",
                         (str(second), task["task_id"]))
        self.assertIn("result.csv", [item["name"] for item in self._files(self._pair())[1]["files"]])

    def test_workspace_artifact_with_spaces_is_discovered(self):
        from factory.core.cli_tasks import create_task
        task = create_task(text="create", profile="cc-local-8081",
                           metadata={"session": self.session_id, "cwd": str(self.export_dir)})
        artifact = Path(self._tmp.name) / "workspace" / task["task_id"] / "results" / "мой отчёт.txt"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("content")
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status = 'ok' WHERE task_id = ?", (task["task_id"],))
        self.assertIn("мой отчёт.txt", [item["name"] for item in self._files(self._pair())[1]["files"]])

    def test_assistant_audio_path_with_spaces_is_discovered(self):
        from factory.core.cli_chat import _append_entry
        audio = self.export_dir / "sPeeX - Back To Hard Klubb (2k17 Edit).mp3"
        audio.write_bytes(b"audio")
        _append_entry(
            _session_path(self.session_id),
            role="assistant",
            text=f"Готово: `{audio}`",
        )
        self.assertIn(audio.name, [item["name"] for item in self._files(self._pair())[1]["files"]])

    def test_assistant_path_in_redundant_quotes_is_discovered(self):
        from factory.core.cli_chat import _append_entry
        artifact = self.export_dir / "sample-diagram.svg"
        artifact.write_text("<svg/>")
        _append_entry(
            _session_path(self.session_id),
            role="assistant",
            text=f"Схема: `'{artifact}'`",
        )
        self.assertIn(artifact.name, [item["name"] for item in self._files(self._pair())[1]["files"]])

    def test_removed_file_disappears_without_server_error(self):
        self.output.unlink()
        status, payload = self._files(self._pair())
        self.assertEqual(status, 200)
        self.assertEqual(payload["files"], [])

    def test_oversized_file_is_not_offered(self):
        from factory.core.mobile_bridge.files import MAX_DOWNLOAD_BYTES
        with self.output.open("wb") as handle:
            handle.truncate(MAX_DOWNLOAD_BYTES + 1)
        self.assertEqual(self._files(self._pair())[1]["files"], [])


class BridgeTickTest(ServerTestCase):
    def test_tick_journals_trace_events(self):
        from factory.core.cli_tasks import create_task

        created = create_task(text="q", profile="cc-local-8081", metadata={"session": self.session_id})
        task_id = created["task_id"]
        trace = Path(self._tmp.name) / "workspace" / f"temp_execution_{task_id[:8]}" / "cc_backend_trace.jsonl"
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(
            json.dumps({"ts": 1, "kind": "task.start", "model": "m", "current_session": self.session_id}) + "\n"
            + json.dumps({"ts": 2, "kind": "tool.request_batch", "round": 1,
                          "tools": [{"id": "c1", "name": "Bash", "input": {"command": "ls"}}]}) + "\n",
            encoding="utf-8",
        )

        appended = self.bridge.tick()

        self.assertGreaterEqual(appended, 2)
        kinds = [event["kind"] for event in self.bridge.store.list_events(limit=50)]
        self.assertIn("task.start", kinds)
        self.assertIn("tool.request_batch", kinds)


class SessionChangesRouteTest(ServerTestCase):
    def test_changes_requires_auth_and_unknown_session_is_404(self):
        status, _ = self._request("GET", f"{API_PREFIX}/sessions/{self.session_id}/changes")
        self.assertEqual(status, 401)

        token = self._pair()
        status, _ = self._request("GET", f"{API_PREFIX}/sessions/chat-nope/changes", token=token)
        self.assertEqual(status, 404)

    def test_changes_returns_aggregated_contract(self):
        from factory.core.cli_tasks import create_task
        from factory.core.session_changes import write_task_changes

        token = self._pair()
        status, created = self._request(
            "POST", f"{API_PREFIX}/sessions", body={"cwd": self._tmp.name}, token=token
        )
        self.assertEqual(status, 201, created)
        session_id = created["session_id"]
        task = create_task(
            text="правь файлы",
            profile="cc-local-8081",
            metadata={"session": session_id, "source": "mobile_bridge", "cwd": self._tmp.name},
        )
        write_task_changes(
            {
                "task_id": task["task_id"],
                "workdir": self._tmp.name,
                "files": [
                    {"path": "src/main.py", "additions": 4, "deletions": 1, "status": "modified"},
                    {"path": ".checkpoints/snap", "additions": 9, "deletions": 0, "status": "added"},
                ],
            },
            data_root=self._tmp.name,
        )

        status, payload = self._request(
            "GET", f"{API_PREFIX}/sessions/{session_id}/changes", token=token
        )

        self.assertEqual(status, 200, payload)
        self.assertEqual(
            payload,
            {
                "files": [
                    {"path": "src/main.py", "additions": 4, "deletions": 1, "status": "modified"}
                ],
                "totals": {"files": 1, "additions": 4, "deletions": 1},
            },
        )


class SessionChangesDiffRouteTest(ServerTestCase):
    def _session_with_task(self, token: str, workdir: str) -> tuple[str, str]:
        from factory.core.cli_tasks import create_task

        status, created = self._request(
            "POST", f"{API_PREFIX}/sessions", body={"cwd": workdir}, token=token
        )
        self.assertEqual(status, 201, created)
        session_id = created["session_id"]
        task = create_task(
            text="правь файлы",
            profile="cc-local-8081",
            metadata={"session": session_id, "source": "mobile_bridge", "cwd": workdir},
        )
        return session_id, task["task_id"]

    def _workdir(self) -> str:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        return holder.name

    def test_review_requires_auth_and_returns_comparison_and_selected_diff(self):
        from factory.core.checkpoint_manager import CheckpointManager
        from factory.core.session_changes import write_task_changes
        route = f"{API_PREFIX}/sessions/{self.session_id}/changes/review"
        status, _ = self._request("GET", route)
        self.assertEqual(status, 401)
        token = self._pair(); workdir = self._workdir()
        target = Path(workdir) / "app.py"; target.write_text("old\n")
        self.assertTrue(CheckpointManager().ensure_checkpoint(working_dir=workdir, reason="first"))
        session, task = self._session_with_task(token, workdir)
        target.write_text("new\n")
        write_task_changes({"task_id":task,"workdir":workdir,"files":[{"path":"app.py","status":"modified","additions":8,"deletions":7}]},data_root=self._tmp.name)
        route = f"{API_PREFIX}/sessions/{session}/changes/review"
        status, data = self._request("GET", route, token=token)
        self.assertEqual(status, 200, data); self.assertEqual(data["totals"]["additions"], 1)
        status, diff = self._request("GET", route + "?path=app.py", token=token)
        self.assertEqual(status, 200, diff); self.assertEqual(diff["additions"], 1)
        status, _ = self._request("GET", route + "?mode=invalid", token=token)
        self.assertEqual(status, 400)

    def test_diff_requires_auth_and_unknown_session_is_404(self):
        status, _ = self._request(
            "GET", f"{API_PREFIX}/sessions/{self.session_id}/changes/diff?path=a.txt"
        )
        self.assertEqual(status, 401)

        token = self._pair()
        status, _ = self._request(
            "GET", f"{API_PREFIX}/sessions/chat-nope/changes/diff?path=a.txt", token=token
        )
        self.assertEqual(status, 404)

    def test_diff_returns_checkpoint_contract_for_modified_file(self):
        from factory.core.checkpoint_manager import CheckpointManager
        from factory.core.session_changes import write_task_changes

        token = self._pair()
        workdir = self._workdir()
        session_id, task_id = self._session_with_task(token, workdir)
        target = Path(workdir) / "src" / "main.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("a\nb\nc\n", encoding="utf-8")
        self.assertTrue(
            CheckpointManager().ensure_checkpoint(working_dir=workdir, reason="baseline")
        )
        target.write_text("a\nB\nc\nd\n", encoding="utf-8")
        write_task_changes(
            {
                "task_id": task_id,
                "workdir": workdir,
                "files": [
                    {"path": "src/main.py", "additions": 2, "deletions": 1, "status": "modified"}
                ],
            },
            data_root=self._tmp.name,
        )

        status, payload = self._request(
            "GET",
            f"{API_PREFIX}/sessions/{session_id}/changes/diff?path={quote('src/main.py')}",
            token=token,
        )

        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["path"], "src/main.py")
        self.assertEqual(payload["source"], "checkpoint")
        self.assertFalse(payload["truncated"])
        types = {line["type"] for hunk in payload["hunks"] for line in hunk["lines"]}
        self.assertIn("-", types)
        self.assertIn("+", types)

    def test_diff_without_baseline_is_unavailable(self):
        from factory.core.session_changes import write_task_changes

        token = self._pair()
        workdir = self._workdir()
        session_id, task_id = self._session_with_task(token, workdir)
        (Path(workdir) / "note.txt").write_text("hi\n", encoding="utf-8")
        write_task_changes(
            {
                "task_id": task_id,
                "workdir": workdir,
                "files": [
                    {"path": "note.txt", "additions": 4, "deletions": 2, "status": "modified"}
                ],
            },
            data_root=self._tmp.name,
        )

        status, payload = self._request(
            "GET",
            f"{API_PREFIX}/sessions/{session_id}/changes/diff?path=note.txt",
            token=token,
        )

        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["source"], "unavailable")
        self.assertEqual(payload["hunks"], [])
        self.assertEqual((payload["additions"], payload["deletions"]), (4, 2))

    def test_diff_path_outside_workdir_is_404(self):
        from factory.core.session_changes import write_task_changes

        token = self._pair()
        workdir = self._workdir()
        session_id, task_id = self._session_with_task(token, workdir)
        (Path(workdir) / "note.txt").write_text("hi\n", encoding="utf-8")
        write_task_changes(
            {
                "task_id": task_id,
                "workdir": workdir,
                "files": [{"path": "note.txt", "additions": 1, "deletions": 0, "status": "added"}],
            },
            data_root=self._tmp.name,
        )

        status, payload = self._request(
            "GET",
            f"{API_PREFIX}/sessions/{session_id}/changes/diff?path={quote('../escape.py')}",
            token=token,
        )

        self.assertEqual(status, 404)
        self.assertIn("outside", payload["error"])



class TerminalRoutesTest(ServerTestCase):
    def test_ticket_requires_auth_kvm_and_binds_device(self):
        from factory.core.isolation.terminal_manager import TerminalError
        token = self._pair()
        route = f"{API_PREFIX}/sessions/{self.session_id}/terminal"
        self.assertEqual(self._request('POST', route)[0], 401)
        with patch.object(self.bridge.service, 'get_session', return_value={'isolation': 'none', 'meta': {'cwd': self._tmp.name}}):
            self.assertEqual(self._request('POST', route, token=token, body={})[0], 409)
        with patch.object(self.bridge.service, 'get_session', return_value={'isolation': 'kvm', 'meta': {'cwd': self._tmp.name}}):
            status, result = self._request('POST', route, token=token, body={})
            self.assertEqual(status, 200)
            record = self.bridge.terminals.manager.consume_ticket(result['ticket'], session_id=self.session_id)
            self.assertEqual(record.device_identity, self.bridge.store.authenticate(token)['device_id'])
            with self.assertRaises(TerminalError):
                self.bridge.terminals.manager.consume_ticket(result['ticket'], session_id=self.session_id)
            self.assertEqual(self._request('POST', f"{API_PREFIX}/sessions/agent-sub_test/terminal", token=token, body={})[0], 409)

    def test_ticket_consumes_body_before_next_keepalive_request(self):
        token = self._pair()
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        with patch.object(self.bridge.service, 'get_session', return_value={'isolation': 'kvm', 'meta': {'cwd': self._tmp.name}}):
            connection.request('POST', f"{API_PREFIX}/sessions/{self.session_id}/terminal", body='{}',
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
            response = connection.getresponse(); self.assertEqual(response.status, 200); response.read()
            connection.request('GET', f"{API_PREFIX}/health")
            response = connection.getresponse(); self.assertEqual(response.status, 200); response.read()
        connection.close()

    def test_native_websocket_delivers_output_input_resize_and_detaches(self):
        import base64
        import struct
        import time
        from test_web_ui_terminal_endpoint import FakeBackend
        from factory.core.isolation.terminal_manager import TerminalManager
        backend = FakeBackend(chunks=[b'hello mobile\r\n'])
        self.bridge.terminals.manager = TerminalManager(backend_factory=lambda: backend)
        token = self._pair()
        with patch.object(self.bridge.service, 'get_session', return_value={'isolation': 'kvm', 'meta': {'cwd': self._tmp.name}}):
            status, result = self._request('POST', f"{API_PREFIX}/sessions/{self.session_id}/terminal", token=token, body={})
            self.assertEqual(status, 200)
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
            connection.request('GET', f"{API_PREFIX}/terminal", headers={
                'Authorization': f'Bearer {token}', 'Upgrade': 'websocket', 'Connection': 'Upgrade',
                'Sec-WebSocket-Version': '13', 'Sec-WebSocket-Key': base64.b64encode(b'0123456789abcdef').decode(),
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 101)
            sock = connection.sock
            def send(kind, seq, payload):
                raw = json.dumps({'kind': kind, 'seq': seq, 'payload': payload}).encode()
                mask = b'abcd'
                head = bytes([0x81, 0x80 | len(raw)]) if len(raw) < 126 else bytes([0x81, 0xfe]) + struct.pack('>H', len(raw))
                sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(raw)))
            def exact(n):
                raw = b''
                while len(raw) < n:
                    chunk = sock.recv(n - len(raw))
                    if not chunk: raise EOFError()
                    raw += chunk
                return raw
            def receive():
                head = exact(2); n = head[1] & 127
                if n == 126: n = struct.unpack('>H', exact(2))[0]
                return json.loads(exact(n))
            send('open', 1, {'ticket': result['ticket'], 'rows': 24, 'cols': 40, 'since_seq': 0})
            self.assertEqual(receive()['kind'], 'opened')
            output = receive()
            self.assertEqual(base64.b64decode(output['payload']['data']), b'hello mobile\r\n')
            send('ack', 2, {'seq': output['seq']})
            send('input', 3, {'data': base64.b64encode(b'pwd\r').decode()})
            send('resize', 4, {'rows': 18, 'cols': 35})
            deadline = time.monotonic() + 2
            while not backend.resizes and time.monotonic() < deadline: time.sleep(0.01)
            self.assertEqual(backend.written, [b'pwd\r'])
            self.assertEqual(backend.resizes, [(18, 35)])
            connection.close()
            self.assertFalse(backend.was_closed)

    def test_ws_rejects_missing_auth_browser_origin_and_bad_handshake(self):
        route = f"{API_PREFIX}/terminal"
        self.assertEqual(self._request('GET', route)[0], 401)
        token = self._pair()
        self.assertEqual(self._request('GET', route, token=token)[0], 400)
        connection = http.client.HTTPConnection('127.0.0.1', self.port)
        connection.request('GET', route, headers={'Authorization': f'Bearer {token}', 'Origin': 'https://foreign.invalid'})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read(); connection.close()



class ScheduleRoutesTest(ServerTestCase):
    def setUp(self):
        super().setUp()
        from factory.core.scheduler import TaskScheduler
        from factory.core.desktop_schedules import DesktopSchedules
        self.bridge.service.available_profiles=lambda:['cc-local-8081']
        self.bridge.service.schedules=DesktopSchedules(self.bridge.service,TaskScheduler(str(get_db_path())))

    def test_schedule_http_crud_and_manual_run_queue_a_real_task(self):
        status,_=self._request('GET',f'{API_PREFIX}/schedules')
        self.assertEqual(status,401)
        token=self._pair()
        fields={'name':'Тест расписания','payload':'Проверить проект','profile':'cc-local-8081','cwd':self._tmp.name,'schedule_type':'daily','time_value':'09:00','timezone':'Europe/Moscow'}
        status,result=self._request('POST',f'{API_PREFIX}/schedules',body=fields,token=token)
        self.assertEqual(status,200,result);identity=result['schedule_id']
        status,result=self._request('POST',f'{API_PREFIX}/schedules/{identity}/run',body={},token=token)
        self.assertEqual(status,200,result);row=result['schedules'][0]
        self.assertTrue(row['last_task_id']);self.assertTrue(row['last_session_id']);self.assertEqual(row['last_status'],'queued')
        self.assertEqual(self.bridge.service.get_task(row['last_task_id'])['metadata']['cwd'],self._tmp.name)
        status,_=self._request('PATCH',f'{API_PREFIX}/schedules/{identity}',body={'enabled':False},token=token)
        self.assertEqual(status,200)
        status,result=self._request('DELETE',f'{API_PREFIX}/schedules/{identity}',token=token)
        self.assertEqual(status,200);self.assertEqual(result['schedules'],[])

    def test_schedule_body_cannot_override_runtime_or_enable_with_string(self):
        token=self._pair()
        status,_=self._request('POST',f'{API_PREFIX}/schedules',body={'name':'x','metadata':{'auto_approve':True}},token=token)
        self.assertEqual(status,400)



if __name__ == "__main__":
    unittest.main()


class McpEndpointTest(ServerTestCase):
    def test_mcp_requires_device_authentication(self):
        with patch.object(self.bridge.mcp_tasks,'dispatch') as dispatch:
            status,_=self._request('POST',f'{API_PREFIX}/mcp',body={'action':'list','fields':{}})
            self.assertEqual(status,401)
            dispatch.assert_not_called()

    def test_mcp_passes_authenticated_identity_and_checks_exact_shape(self):
        token=self._pair()
        identity=self.bridge.store.list_devices()[0]['device_id']
        with patch.object(self.bridge.mcp_tasks,'dispatch',return_value={'entries':[]}) as dispatch:
            status,result=self._request('POST',f'{API_PREFIX}/mcp',body={'action':'list','fields':{}},token=token)
            self.assertEqual((status,result),(200,{'entries':[]}))
            dispatch.assert_called_once_with(identity,'list',{})
            status,_=self._request('POST',f'{API_PREFIX}/mcp',body={'action':'list','fields':{},'extra':True},token=token)
            self.assertEqual(status,400)
            self.assertEqual(dispatch.call_count,1)

    def test_revocation_while_processing_blocks_response(self):
        token=self._pair()
        def dispatch(identity,*args):
            self.bridge.store.revoke_device(identity)
            return {'job':{'result':'private-result'}}
        with patch.object(self.bridge.mcp_tasks,'dispatch',side_effect=dispatch):
            status,result=self._request('POST',f'{API_PREFIX}/mcp',body={'action':'status','fields':{}},token=token)
            self.assertEqual(status,401)
            self.assertNotIn('private-result',json.dumps(result))


class McpDevicePermissionsTlsTest(ServerTestCase):
    def test_device_permission_changes_require_https(self):
        token=self._pair()
        with patch.object(self.bridge.mcp_tasks,'dispatch') as dispatch:
            status,_=self._request('POST',f'{API_PREFIX}/mcp',body={'action':'configure_device','fields':{'server':'gmail','credential_id':'fixture','access':'allow','confirmation':'owner_confirmed'}},token=token)
            self.assertEqual(status,403)
            dispatch.assert_not_called()


class BrowserPreviewRouteTest(ServerTestCase):
    def test_preview_is_authenticated_read_only_and_optional(self):
        status,_=self._request('GET',API_PREFIX+'/browser/preview')
        self.assertEqual(status,401)
        token=self._pair()
        status,value=self._request('GET',API_PREFIX+'/browser/preview',token=token)
        self.assertEqual(status,200);self.assertEqual(value['reason'],'not_desktop')
        calls=[]
        self.bridge.service.browser_preview=type('Preview',(),{'read':lambda _,after:calls.append(after) or {'available':True,'ready':False,'reason':'manual_control'}})()
        status,value=self._request('GET',API_PREFIX+'/browser/preview?after='+'a'*64,token=token)
        self.assertEqual(status,200);self.assertEqual(calls,['a'*64]);self.assertEqual(value['reason'],'manual_control')
        status,_=self._request('GET',API_PREFIX+'/browser/preview?url=https://example.com',token=token)
        self.assertEqual(status,400);self.assertEqual(len(calls),1)
        status,_=self._request('POST',API_PREFIX+'/browser/preview',token=token,body={'action':'click'})
        self.assertEqual(status,404);self.assertEqual(len(calls),1)

    def test_revoking_device_while_frame_is_pending_discards_the_frame(self):
        token=self._pair();identity=self.bridge.store.authenticate(token)['device_id']
        def read(after):
            self.bridge.store.revoke_device(identity)
            return {'ready':True,'image_base64':'private-canary'}
        self.bridge.service.browser_preview=type('Preview',(),{'read':lambda _,after:read(after)})()
        status,value=self._request('GET',API_PREFIX+'/browser/preview',token=token)
        self.assertEqual(status,401);self.assertNotIn('image_base64',value)

    def test_select_tab_requires_authentication_and_an_explicit_valid_id(self):
        status,_=self._request('POST',API_PREFIX+'/browser/select',body={'tab_id':'two'})
        self.assertEqual(status,401)
        token=self._pair();calls=[]
        self.bridge.service.browser_preview=type('Preview',(),{'select':lambda _,tab_id:calls.append(tab_id) or {'selected':True,'tab_id':tab_id}})()
        status,value=self._request('POST',API_PREFIX+'/browser/select',token=token,body={'tab_id':'two'})
        self.assertEqual(status,200);self.assertTrue(value['selected']);self.assertEqual(calls,['two'])
        for payload in ({'tab_id':'../bad'}, {'tab_id':'two','url':'https://example.com'}, {}, {'tab_id':4}):
            status,_=self._request('POST',API_PREFIX+'/browser/select',token=token,body=payload)
            self.assertEqual(status,400)
        self.assertEqual(calls,['two'])

    def test_revoking_device_during_selection_discards_reply(self):
        token=self._pair();identity=self.bridge.store.authenticate(token)['device_id']
        def select(tab_id):
            self.bridge.store.revoke_device(identity)
            return {'selected':True,'tab_id':tab_id}
        self.bridge.service.browser_preview=type('Preview',(),{'select':lambda _,tab_id:select(tab_id)})()
        status,value=self._request('POST',API_PREFIX+'/browser/select',token=token,body={'tab_id':'one'})
        self.assertEqual(status,401);self.assertNotIn('tab_id',value)


class ActivityRoutesTest(ServerTestCase):
    def test_live_command_stop_is_authenticated_scoped_and_does_not_cancel_task(self):
        import sys
        import time
        from factory.core.running_commands import command_context, run_process, list_commands
        results = []
        def work():
            with command_context(self.session_id, "activity-fixture-task", "activity-fixture-call"):
                results.append(run_process([sys.executable, "-c", "import time; time.sleep(8)"],
                                           label="fixture process", cwd=self._tmp.name,
                                           text=True, capture_output=True, timeout=10))
        thread = threading.Thread(target=work)
        thread.start()
        try:
            token = self._pair()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not list_commands(self.session_id):
                time.sleep(.02)
            status, payload = self._request("GET", f"{API_PREFIX}/sessions/{self.session_id}/activity", token=token)
            self.assertEqual(status, 200)
            row = payload["processes"][0]
            self.assertEqual(row["tool_use_id"], "activity-fixture-call")
            stop = f"{API_PREFIX}/sessions/{self.session_id}/activity/{row['id']}/stop"
            self.assertEqual(self._request("POST", stop, body={})[0], 401)
            other = self.bridge.service.create_session(cwd=self._tmp.name, idempotency_key="activity-other")["session_id"]
            wrong = f"{API_PREFIX}/sessions/{other}/activity/{row['id']}/stop"
            self.assertEqual(self._request("POST", wrong, body={}, token=token)[0], 404)
            status, answer = self._request("POST", stop, body={}, token=token)
            self.assertEqual(status, 200)
            self.assertTrue(answer["requested"])
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertLess(results[0].returncode, 0)
            self.assertEqual(self._request("GET", f"{API_PREFIX}/sessions/{self.session_id}/activity", token=token)[1]["processes"], [])
            self.assertEqual(self._request("POST", stop, body={}, token=token)[0], 404)
        finally:
            for item in list_commands(self.session_id):
                from factory.core.running_commands import request_stop
                request_stop(self.session_id, item["id"])
            thread.join(3)
