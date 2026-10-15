import unittest
from pathlib import Path

from factory.core.codex_event_parser import (
    extract_codex_final_text,
    extract_codex_final_text_after_tool_results,
    extract_codex_tool_events,
    extract_codex_tool_result_context,
    extract_codex_usage,
)


FIXTURE = Path("tests/fixtures/codex_events/openrouter_handoff_probe.jsonl")


class CodexEventParserTest(unittest.TestCase):
    def setUp(self):
        self.stdout_text = FIXTURE.read_text(encoding="utf-8")

    def test_extracts_final_text_from_raw_agent_message(self):
        self.assertEqual(extract_codex_final_text(self.stdout_text), "Привет, Игорь!")

    def test_rejects_pre_tool_message_as_final_when_tool_result_follows(self):
        stdout_text = "\n".join(
            [
                '{"type":"agent_message","message":"Сейчас посмотрю."}',
                '{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\\"cmd\\": \\"cat handoff.md\\"}","call_id":"call_1"}}',
                '{"type":"response_item","payload":{"type":"function_call_output","call_id":"call_1","output":"Process exited with code 0\\nOutput:\\n# Handoff"}}',
                '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
            ]
        )

        self.assertEqual(extract_codex_final_text(stdout_text), "Сейчас посмотрю.")
        self.assertEqual(extract_codex_final_text_after_tool_results(stdout_text), "")

    def test_accepts_message_after_tool_result_as_final(self):
        stdout_text = "\n".join(
            [
                '{"type":"agent_message","message":"Сейчас посмотрю."}',
                '{"type":"response_item","payload":{"type":"function_call_output","call_id":"call_1","output":"Process exited with code 0"}}',
                '{"type":"event_msg","payload":{"type":"agent_message","message":"Итог после tool result."}}',
            ]
        )

        self.assertEqual(
            extract_codex_final_text_after_tool_results(stdout_text),
            "Итог после tool result.",
        )

    def test_extracts_tool_result_context_for_synthesis(self):
        stdout_text = "\n".join(
            [
                '{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\\"cmd\\": \\"cat Implementation.md\\"}","call_id":"call_1"}}',
                '{"type":"response_item","payload":{"type":"function_call_output","call_id":"call_1","output":"Process exited with code 0\\nOutput:\\n# Plan\\nACP/gateway smoke review"}}',
            ]
        )

        context = extract_codex_tool_result_context(stdout_text)

        self.assertIn("cat Implementation.md", context)
        self.assertIn("ACP/gateway smoke review", context)

    def test_extracts_usage_from_turn_completed(self):
        usage = extract_codex_usage(self.stdout_text)

        self.assertEqual(usage["prompt"], 52464)
        self.assertEqual(usage["completion"], 1031)
        self.assertEqual(usage["input_tokens"], 52464)
        self.assertEqual(usage["cached_input_tokens"], 4480)
        self.assertEqual(usage["output_tokens"], 1031)
        self.assertEqual(usage["reasoning_output_tokens"], 234)
        self.assertEqual(usage["total_tokens"], 53495)
        self.assertEqual(usage["model_context_window"], 258400)

    def test_extracts_tool_call_and_result_summaries(self):
        events = extract_codex_tool_events(self.stdout_text)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_type, "function_call")
        self.assertEqual(events[0].name, "exec_command")
        self.assertIn("handoff.md", events[0].summary)
        self.assertEqual(events[1].event_type, "function_call_output")
        self.assertEqual(events[1].exit_code, 0)
        self.assertIn("OpenRouter session cost", events[1].output_preview)


if __name__ == "__main__":
    unittest.main()
