import unittest

from factory.core.session_budget import (
    DECISION_CONTINUE,
    DECISION_FORCE_SYNTHESIS,
    REASON_NON_PRODUCTIVE_AFTER_USEFUL,
    REASON_REPEATED_TOOL_LOOP,
    SessionBudget,
)
from factory.core.tool_result_store import PERSISTED_OUTPUT_TAG


class SessionBudgetTest(unittest.TestCase):
    def test_repeated_tool_batch_forces_synthesis_at_existing_limit(self):
        budget = SessionBudget()
        signature = (("Read", '{"file_path":"a.py"}'),)

        for _ in range(3):
            budget.record_tool_batch(signature, count=1)
            budget.record_round_productivity(productive=False)
            decision = budget.evaluate_after_tool_round()
            self.assertEqual(decision.action, DECISION_CONTINUE)

        budget.record_tool_batch(signature, count=1)
        budget.record_round_productivity(productive=False)
        decision = budget.evaluate_after_tool_round()

        self.assertEqual(decision.action, DECISION_FORCE_SYNTHESIS)
        self.assertEqual(decision.reason, REASON_REPEATED_TOOL_LOOP)
        self.assertEqual(decision.details["consecutive_same_tool_batch"], 4)

    def test_non_productive_after_useful_forces_synthesis(self):
        budget = SessionBudget()
        budget.record_tool_batch((("Grep", "{}"),), count=1)
        budget.record_round_productivity(productive=True)
        self.assertEqual(budget.evaluate_after_tool_round().action, DECISION_CONTINUE)

        budget.record_tool_batch((("Read", '{"file_path":"missing"}'),), count=1)
        budget.record_round_productivity(productive=False)
        self.assertEqual(budget.evaluate_after_tool_round().action, DECISION_CONTINUE)

        budget.record_tool_batch((("Read", '{"file_path":"missing2"}'),), count=1)
        budget.record_round_productivity(productive=False)
        decision = budget.evaluate_after_tool_round()

        self.assertEqual(decision.action, DECISION_FORCE_SYNTHESIS)
        self.assertEqual(decision.reason, REASON_NON_PRODUCTIVE_AFTER_USEFUL)
        self.assertTrue(decision.details["has_useful_result"])
        self.assertEqual(decision.details["consecutive_non_productive"], 2)

    def test_counters_capture_runtime_events_and_output_volume(self):
        budget = SessionBudget()
        budget.begin_round()
        budget.record_salvage_success()
        budget.record_salvage_failure()
        budget.record_textual_tool_retry()
        budget.record_policy_denial_retry()
        budget.record_fact_check_correction()
        budget.record_approval_required()
        budget.record_tool_result(
            raw_content="abcdef",
            model_content=f"{PERSISTED_OUTPUT_TAG}\npreview",
        )

        snapshot = budget.snapshot()

        self.assertEqual(snapshot["rounds_started"], 1)
        self.assertEqual(snapshot["salvage_successes"], 1)
        self.assertEqual(snapshot["salvage_failures"], 1)
        self.assertEqual(snapshot["textual_tool_retries"], 2)
        self.assertEqual(snapshot["policy_denial_retries"], 1)
        self.assertEqual(snapshot["fact_check_corrections"], 1)
        self.assertEqual(snapshot["approval_required_count"], 1)
        self.assertEqual(snapshot["tool_result_raw_chars"], 6)
        self.assertEqual(snapshot["persisted_tool_results"], 1)


if __name__ == "__main__":
    unittest.main()
