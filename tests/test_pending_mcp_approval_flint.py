"""Pending MCP responses require a usable server and request identity."""

import json
import unittest

from factory.core.cc_backend_executor import _pending_mcp_enable_approval


class TestPendingMcpApprovalFlint(unittest.TestCase):
    """Contract tests for _pending_mcp_enable_approval (flint fixture)."""

    def test_returns_none_for_other_tool_name(self):
        payload = json.dumps(
            {
                "decision": "pending",
                "request_id": "req-1",
                "server_name": "server-a",
            }
        )
        self.assertIsNone(_pending_mcp_enable_approval(payload, "some_other_tool"))
        self.assertIsNone(
            _pending_mcp_enable_approval(payload, "suggest_enable_mcp_extra")
        )

    def test_returns_none_for_damaged_json_or_none_without_exception(self):
        with self.subTest("damaged json"):
            self.assertIsNone(
                _pending_mcp_enable_approval("{not valid json", "suggest_enable_mcp")
            )
        with self.subTest("none input"):
            self.assertIsNone(_pending_mcp_enable_approval(None, "suggest_enable_mcp"))

    def test_returns_none_for_non_dict_json_types(self):
        cases = [
            ("json list", json.dumps(["pending", "req-1"])),
            ("json string", json.dumps("pending")),
            ("json number", json.dumps(42)),
            ("json null", json.dumps(None)),
        ]
        for label, raw in cases:
            with self.subTest(label):
                self.assertIsNone(
                    _pending_mcp_enable_approval(raw, "suggest_enable_mcp")
                )

    def test_returns_none_when_decision_is_not_pending(self):
        result = _pending_mcp_enable_approval(
            json.dumps(
                {"decision": "approved", "request_id": "req-1", "server_name": "srv"}
            ),
            "suggest_enable_mcp",
        )
        self.assertIsNone(result)

    def test_returns_none_for_pending_without_non_empty_ids(self):
        cases = [
            ("missing ids", {"decision": "pending"}),
            (
                "blank request_id",
                {"decision": "pending", "request_id": "   ", "server_name": "srv"},
            ),
            (
                "blank server_name",
                {"decision": "pending", "request_id": "req-1", "server_name": "\t\n"},
            ),
            (
                "empty strings",
                {"decision": "pending", "request_id": "", "server_name": ""},
            ),
        ]
        for label, payload_obj in cases:
            with self.subTest(label):
                result = _pending_mcp_enable_approval(
                    json.dumps(payload_obj), "suggest_enable_mcp"
                )
                self.assertIsNone(result)

    def test_valid_pending_returns_trimmed_ids_and_keeps_fields(self):
        result = _pending_mcp_enable_approval(
            json.dumps(
                {
                    "decision": "pending",
                    "request_id": "  req-42  ",
                    "server_name": "\tsrv-alpha\n",
                    "profile": "default",
                    "reason": "need owner approval",
                    "description": "desc text",
                }
            ),
            "suggest_enable_mcp",
        )
        self.assertEqual(
            result,
            {
                "request_id": "req-42",
                "server_name": "srv-alpha",
                "profile": "default",
                "reason": "need owner approval",
                "description": "desc text",
            },
        )


if __name__ == "__main__":
    unittest.main()
