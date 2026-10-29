import ast
import re
import unittest
from pathlib import Path

from factory.core.trace_taxonomy import (
    CcTraceEvent,
    EVENT_KIND_RE,
    all_event_kinds,
    trace_event,
)


class TraceTaxonomyTest(unittest.TestCase):
    def test_completion_signal_is_registered(self):
        self.assertEqual(CcTraceEvent.TASK_COMPLETION_SIGNAL, "task.completion_signal")
        self.assertIn(CcTraceEvent.TASK_COMPLETION_SIGNAL, all_event_kinds())

    def test_completion_review_is_registered(self):
        self.assertEqual(CcTraceEvent.MODEL_COMPLETION_REVIEW, "model.completion_review")
        self.assertIn(CcTraceEvent.MODEL_COMPLETION_REVIEW, all_event_kinds())

    def test_network_recovery_is_registered(self):
        self.assertEqual(CcTraceEvent.NETWORK_RECOVERED, 'network.recovered')
        self.assertIn(CcTraceEvent.NETWORK_RECOVERED, all_event_kinds())
    def test_compaction_progress_is_registered(self):
        self.assertEqual(CcTraceEvent.COMPACTION_PROGRESS, "compaction.progress")
        self.assertIn(CcTraceEvent.COMPACTION_PROGRESS, all_event_kinds())

    def test_all_event_kinds_follow_domain_action_format(self):
        kinds = all_event_kinds()
        self.assertTrue(kinds)
        for kind in kinds:
            self.assertRegex(kind, EVENT_KIND_RE)

    def test_trace_event_rejects_non_taxonomy_shape(self):
        with self.assertRaises(ValueError):
            trace_event("task_finish")

    def test_plan_updated_event_is_registered(self):
        self.assertIn(CcTraceEvent.PLAN_UPDATED, all_event_kinds())
        self.assertEqual(CcTraceEvent.PLAN_UPDATED, "plan.updated")

    def test_payload_retry_event_is_registered(self):
        self.assertIn(CcTraceEvent.PAYLOAD_RETRY, all_event_kinds())
        self.assertEqual(CcTraceEvent.PAYLOAD_RETRY, "payload.retry")

    def test_isolation_placement_events_are_registered(self):
        self.assertEqual(CcTraceEvent.ISOLATION_MCP_UNAVAILABLE, "isolation.mcp_unavailable")
        self.assertEqual(CcTraceEvent.ISOLATION_MCP_BROKERED_PENDING, "isolation.mcp_brokered_pending")
        self.assertEqual(CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE, "isolation.skill_asset_unavailable")
        for kind in (
            CcTraceEvent.ISOLATION_MCP_UNAVAILABLE,
            CcTraceEvent.ISOLATION_MCP_BROKERED_PENDING,
            CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE,
        ):
            self.assertIn(kind, all_event_kinds())

    def test_isolation_blocked_host_event_is_registered(self):
        self.assertEqual(
            CcTraceEvent.ISOLATION_BLOCKED_HOST, "isolation.blocked_host"
        )
        self.assertIn(CcTraceEvent.ISOLATION_BLOCKED_HOST, all_event_kinds())

    def test_isolation_secret_policy_events_are_registered(self):
        self.assertEqual(
            CcTraceEvent.ISOLATION_PROVIDER_AUTH_PROXIED,
            "isolation.provider_auth_proxied",
        )
        self.assertEqual(
            CcTraceEvent.ISOLATION_PROVIDER_AUTH_RESIDUAL,
            "isolation.provider_auth_residual",
        )
        self.assertEqual(CcTraceEvent.ISOLATION_HOST_OP, "isolation.host_op")
        for kind in (
            CcTraceEvent.ISOLATION_PROVIDER_AUTH_PROXIED,
            CcTraceEvent.ISOLATION_PROVIDER_AUTH_RESIDUAL,
            CcTraceEvent.ISOLATION_HOST_OP,
        ):
            self.assertIn(kind, all_event_kinds())

    def test_isolation_limit_event_is_registered(self):
        self.assertEqual(
            CcTraceEvent.ISOLATION_LIMIT_EXCEEDED, "isolation.limit_exceeded"
        )
        self.assertIn(CcTraceEvent.ISOLATION_LIMIT_EXCEEDED, all_event_kinds())

    def test_isolation_capacity_wait_event_is_registered(self):
        self.assertEqual(
            CcTraceEvent.ISOLATION_CAPACITY_WAIT, "isolation.capacity_wait"
        )
        self.assertIn(CcTraceEvent.ISOLATION_CAPACITY_WAIT, all_event_kinds())

    def test_isolation_durable_root_event_is_registered(self):
        self.assertEqual(
            CcTraceEvent.ISOLATION_DURABLE_ROOT_UNAVAILABLE,
            "isolation.durable_root_unavailable",
        )
        self.assertIn(
            CcTraceEvent.ISOLATION_DURABLE_ROOT_UNAVAILABLE, all_event_kinds()
        )

    def test_isolation_recovery_events_are_registered(self):
        self.assertEqual(
            CcTraceEvent.ISOLATION_RECOVERY_STARTED,
            "isolation.recovery_started",
        )
        self.assertEqual(
            CcTraceEvent.ISOLATION_RECOVERY_FINISHED,
            "isolation.recovery_finished",
        )
        self.assertIn(CcTraceEvent.ISOLATION_RECOVERY_STARTED, all_event_kinds())
        self.assertIn(CcTraceEvent.ISOLATION_RECOVERY_FINISHED, all_event_kinds())

    def test_cc_backend_executor_uses_taxonomy_constants_for_literal_kinds(self):
        source = Path("factory/core/cc_backend_executor.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        literal_kinds: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "kind"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    literal_kinds.append(value.value)

        self.assertEqual([], literal_kinds)

    def test_no_old_underscore_trace_kinds_remain_in_cc_backend_consumers(self):
        checked_files = [
            Path("factory/core/cc_backend_executor.py"),
            Path("factory/core/gateway_runtime.py"),
            Path("factory/core/cli_chat_rich.py"),
        ]
        old_shape = re.compile(
            r'"(task_finish|task_start|round_response|tool_request_batch|'
            r'tool_approval_required|salvage_match|textual_tool_retry|'
            r'policy_denial_retry|post_turn_guard_verdict|post_turn_guard_retry|'
            r'pending_write_detected|fact_check_failed|fact_check_passed|'
            r'session_budget_decision)"'
        )
        for path in checked_files:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(old_shape.search(text), str(path))


if __name__ == "__main__":
    unittest.main()
