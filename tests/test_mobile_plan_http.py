from unittest.mock import patch

from factory.core.cli_tasks import get_task
from factory.core.mobile_bridge.server import API_PREFIX
from test_mobile_bridge_server import ServerTestCase


class PlanModeHttpTest(ServerTestCase):
    def test_authenticated_plan_turn_and_active_task_rejection(self):
        path = f"{API_PREFIX}/sessions/{self.session_id}/messages"
        status, _ = self._request("POST", path, body={"text": "plan", "plan_mode": True})
        self.assertEqual(status, 401)
        token = self._pair()
        with patch.object(self.bridge.service, "_profile_backend", return_value="cc-backend"):
            status, result = self._request("POST", path, token=token, body={"text": "inspect", "plan_mode": True})
            self.assertEqual(status, 202, result)
            self.assertTrue(get_task(result["task_id"])["metadata"]["plan_mode"])
            status, _ = self._request("POST", path, token=token, body={"text": "plan again", "plan_mode": True})
            self.assertEqual(status, 409)
        self.assertEqual(len(self.bridge.service.get_session(self.session_id)["messages"]), 1)

    def test_invalid_flag_is_rejected_instead_of_running_without_plan_mode(self):
        path = f"{API_PREFIX}/sessions/{self.session_id}/messages"
        token = self._pair()
        for invalid in ("true", 1, None, [], {}):
            status, _ = self._request("POST", path, token=token, body={"text": "inspect", "plan_mode": invalid})
            self.assertEqual(status, 400)
        self.assertFalse(self.bridge.service.get_session(self.session_id)["messages"])
