from factory.core.mobile_bridge.server import API_PREFIX
from test_mobile_bridge_server import ServerTestCase


class GoalHttpContractTest(ServerTestCase):
    def test_goal_controls_require_auth_and_keep_sessions_isolated(self):
        path = f"{API_PREFIX}/sessions/{self.session_id}/goal"
        status, _ = self._request("POST", path, body={"action": "create", "objective": "Work"})
        self.assertEqual(status, 401)
        token = self._pair()
        status, payload = self._request("POST", path, token=token, body={"action": "create", "objective": "Work"})
        self.assertEqual(status, 200)
        goal_id = payload["goal"]["goal_id"]
        status, detail = self._request("GET", f"{API_PREFIX}/sessions/{self.session_id}", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(detail["goal"]["objective"], "Work")
        status, _ = self._request("POST", path, token=token, body={"action": "pause", "goal_id": "stale"})
        self.assertEqual(status, 409)
        status, payload = self._request("POST", path, token=token, body={"action": "pause", "goal_id": goal_id})
        self.assertEqual(status, 200)
        self.assertEqual(payload["goal"]["status"], "paused")

    def test_invalid_goal_inputs_are_rejected(self):
        token = self._pair()
        path = f"{API_PREFIX}/sessions/{self.session_id}/goal"
        for body in ({"action": "create", "objective": ""}, {"action": "unknown"},
                     {"action": "create", "objective": "Work", "token_budget": True},
                     {"action": "create", "objective": "Work", "extra": "unexpected"}):
            status, _ = self._request("POST", path, token=token, body=body)
            self.assertEqual(status, 400, body)
