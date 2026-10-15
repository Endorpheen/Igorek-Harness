import unittest
from tempfile import TemporaryDirectory

from factory.core.session_compaction import (
    REASON_PENDING_APPROVAL,
    REASON_PROMPT_TOO_LONG,
    REASON_UNRESOLVED_FAILED_TOOL_RESULT,
    build_compacted_messages,
    build_compaction_dry_run_plan,
    estimate_context_tokens,
    evaluate_compaction_pressure,
    write_manual_compaction_artifact,
)
from factory.core.trace_taxonomy import CcTraceEvent


def _tool_use(tool_id: str) -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_id, "name": "Read", "input": {}}],
    }


def _tool_result(tool_id: str, text: str = "ok") -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}],
    }


class SessionCompactionDryRunTest(unittest.TestCase):
    def test_old_successful_messages_can_be_summarized_without_mutating_history(self):
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "current"},
            {"role": "assistant", "content": "working"},
        ]

        plan = build_compaction_dry_run_plan(
            messages=messages,
            trace_events=[],
            session_budget_snapshot={"rounds_started": 3},
        )

        self.assertTrue(plan.can_compact)
        self.assertFalse(plan.blocked)
        self.assertEqual([0, 1], plan.summarize_message_indexes)
        self.assertEqual([2, 3], plan.preserve_message_indexes)
        self.assertEqual(CcTraceEvent.COMPACTION_CHECK, plan.boundary_preview["kind"])
        self.assertTrue(plan.boundary_preview["dry_run"])
        self.assertEqual({"rounds_started": 3}, plan.summary_shape["budget"])

    def test_failed_tool_result_blocks_compaction_until_fact_check_resolution(self):
        messages = [
            {"role": "user", "content": "start"},
            _tool_use("toolu_failed"),
            _tool_result("toolu_failed", "traceback"),
        ]
        trace_events = [
            {
                "kind": CcTraceEvent.TOOL_RESULT,
                "tool_use_id": "toolu_failed",
                "is_error": True,
            }
        ]

        plan = build_compaction_dry_run_plan(
            messages=messages,
            trace_events=trace_events,
        )

        self.assertFalse(plan.can_compact)
        self.assertTrue(plan.blocked)
        self.assertEqual(REASON_UNRESOLVED_FAILED_TOOL_RESULT, plan.reason)
        self.assertEqual(["toolu_failed"], plan.preserved_tool_result_ids)
        self.assertEqual([1, 2], plan.preserve_message_indexes)

    def test_pending_approval_blocks_compaction(self):
        plan = build_compaction_dry_run_plan(
            messages=[
                {"role": "user", "content": "make file"},
                {"role": "assistant", "content": "needs approval"},
            ],
            trace_events=[
                {
                    "kind": CcTraceEvent.TOOL_APPROVAL_REQUIRED,
                    "approval_id": "approval_1",
                    "status": "pending",
                }
            ],
        )

        self.assertTrue(plan.blocked)
        self.assertEqual(REASON_PENDING_APPROVAL, plan.reason)
        self.assertEqual(["approval_1"], plan.preserved_approval_ids)
        self.assertEqual([0], plan.preserve_trace_indexes)

    def test_tail_tool_result_does_not_split_tool_use_pair(self):
        messages = [
            {"role": "user", "content": "older"},
            _tool_use("toolu_pair"),
            _tool_result("toolu_pair"),
        ]

        plan = build_compaction_dry_run_plan(
            messages=messages,
            trace_events=[
                {
                    "kind": CcTraceEvent.FACT_CHECK_PASSED,
                    "tool_use_id": "toolu_pair",
                }
            ],
            keep_tail_messages=1,
        )

        self.assertFalse(plan.blocked)
        self.assertEqual([0], plan.summarize_message_indexes)
        self.assertEqual([1, 2], plan.preserve_message_indexes)

    def test_manual_artifact_writes_summary_and_boundary_without_context_mutation(self):
        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current"},
            {"role": "assistant", "content": "current answer"},
        ]
        plan = build_compaction_dry_run_plan(
            messages=messages,
            trace_events=[],
            session_budget_snapshot={"tool_rounds": 2},
        )

        with TemporaryDirectory() as tmpdir:
            artifact = write_manual_compaction_artifact(
                plan=plan,
                artifact_dir=tmpdir,
                compaction_id="compact_0001",
                created_at=1770000000,
            )

            self.assertEqual("compact_0001", artifact.compaction_id)
            self.assertTrue(artifact.summary_path.exists())
            self.assertEqual(CcTraceEvent.COMPACTION_BOUNDARY, artifact.boundary_event["kind"])
            self.assertFalse(artifact.boundary_event["dry_run"])
            self.assertEqual(str(artifact.summary_path), artifact.boundary_event["summary_path"])
            self.assertEqual({"tool_rounds": 2}, artifact.summary["budget"])
            self.assertEqual(messages[0]["content"], "old")

            compacted = build_compacted_messages(
                plan=plan,
                artifact=artifact,
                messages=messages,
            )
            self.assertEqual("user", compacted[0]["role"])
            self.assertIn("session-compaction-boundary", compacted[0]["content"][0]["text"])
            self.assertEqual(messages[2]["content"], compacted[0]["content"][1]["text"])
            self.assertEqual(messages[3:], compacted[1:])

    def test_manual_artifact_refuses_blocked_plan(self):
        plan = build_compaction_dry_run_plan(
            messages=[{"role": "user", "content": "x"}],
            trace_events=[
                {
                    "kind": CcTraceEvent.TOOL_APPROVAL_REQUIRED,
                    "approval_id": "approval_1",
                }
            ],
        )

        with TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                write_manual_compaction_artifact(
                    plan=plan,
                    artifact_dir=tmpdir,
                    compaction_id="compact_0001",
                )

    def test_pressure_gate_accounts_for_reserved_output_tokens(self):
        decision = evaluate_compaction_pressure(
            estimated_context_tokens=25_000,
            context_window_tokens=32_000,
            reserved_output_tokens=2_000,
        )

        self.assertEqual("manual", decision.action)
        self.assertTrue(decision.should_check)
        self.assertAlmostEqual(0.8333333333333334, decision.context_ratio)
        self.assertEqual(30_000, decision.usable_context_window)

    def test_pressure_gate_reacts_to_prompt_too_long_signal(self):
        decision = evaluate_compaction_pressure(
            estimated_context_tokens=10_000,
            context_window_tokens=32_000,
            prompt_too_long=True,
        )

        self.assertEqual("reactive", decision.action)
        self.assertEqual(REASON_PROMPT_TOO_LONG, decision.reason)

    def test_context_estimate_includes_system_messages_and_tools(self):
        estimate = estimate_context_tokens(
            system_prompt="system",
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"name": "Read"}],
        )

        self.assertGreater(estimate, 1)

    def test_context_estimate_does_not_count_image_base64_as_text(self):
        image_data = "A" * (2_172 * 1024 * 4 // 3)
        estimate = estimate_context_tokens(
            system_prompt="system",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "что тут изображено"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_data,
                            },
                        },
                    ],
                }
            ],
            tools=[],
        )

        self.assertLess(estimate, 10_000)
        self.assertGreater(estimate, 1_000)


if __name__ == "__main__":
    unittest.main()
