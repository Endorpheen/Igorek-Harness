import tempfile
import unittest
from pathlib import Path

from factory.core.agent_loop_limits import load_cc_backend_loop_limits
from factory.core.session_budget import DECISION_CONTINUE, SessionBudget


class AgentLoopLimitsTest(unittest.TestCase):
    def test_default_config_disables_cloud_loop_hard_limits(self):
        limits = load_cc_backend_loop_limits()

        self.assertIsNone(limits.max_tool_rounds)
        self.assertIsNone(limits.repeated_tool_batch_limit)
        self.assertIsNone(limits.non_productive_after_useful_limit)
        self.assertIsNone(limits.same_tool_batch_block_limit)

    def test_can_load_positive_limits_from_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "limits.yaml"
            path.write_text(
                "\n".join(
                    [
                        "cc_backend:",
                        "  max_tool_rounds: 123",
                        "  repeated_tool_batch_limit: 7",
                        "  non_productive_after_useful_limit: 8",
                        "  same_tool_batch_block_limit: 9",
                    ]
                ),
                encoding="utf-8",
            )

            limits = load_cc_backend_loop_limits(path)

        self.assertEqual(limits.max_tool_rounds, 123)
        self.assertEqual(limits.repeated_tool_batch_limit, 7)
        self.assertEqual(limits.non_productive_after_useful_limit, 8)
        self.assertEqual(limits.same_tool_batch_block_limit, 9)

    def test_session_budget_none_limits_never_force_synthesis(self):
        budget = SessionBudget(
            repeated_batch_limit=None,
            non_productive_after_useful_limit=None,
        )
        signature = (("Bash", '{"command":"pwd"}'),)
        for _ in range(10):
            budget.record_tool_batch(signature, count=1)
            budget.record_round_productivity(False)

        decision = budget.evaluate_after_tool_round()

        self.assertEqual(decision.action, DECISION_CONTINUE)


if __name__ == "__main__":
    unittest.main()
