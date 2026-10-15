"""Tests for reasoning_content passback normalization (DeepSeek thinking mode).

Upstream opencode zen -> DeepSeek returns HTTP 400
``The `reasoning_content` in the thinking mode must be passed back to the API``,
if an assistant message with tool_calls lacks reasoning_content. The model may
legally omit thinking in a separate round (as seen in a real trace: tool_calls with no single reasoning delta), and in such cases, our payload violates the upstream contract.
"""

import unittest

from factory.core.cc_backend_executor import (
    _anthropic_messages_to_openai,
    _ensure_reasoning_passback,
    _is_reasoning_passback_error,
)

UPSTREAM_ERROR = (
    'HTTP 400: {"error":{"param":null,"type":"invalid_request_error",'
    '"code":"invalid_request_error","message":"Upstream request failed: '
    '[invalid_request_error] The `reasoning_content` in the thinking mode must be '
    'passed back to the API."}}'
)


def _tool_call_message(call_id: str = "call_1", command: str = "echo 1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "Bash", "arguments": '{"command": "%s"}' % command},
            }
        ],
    }


class IsReasoningPassbackErrorTest(unittest.TestCase):
    def test_detects_upstream_passback_error(self):
        self.assertTrue(_is_reasoning_passback_error(UPSTREAM_ERROR))

    def test_detects_error_case_insensitively(self):
        self.assertTrue(
            _is_reasoning_passback_error(
                "upstream request failed: the REASONING_CONTENT in the thinking mode "
                "MUST BE PASSED BACK to the api"
            )
        )

    def test_ignores_unrelated_errors(self):
        for error in (
            "",
            "HTTP 400: prompt too long",
            "HTTP 400: unsupported content type '__reasoning' (2013)",
            "transport:remote_disconnected: Remote end closed connection",
            "HTTP 401: unauthorized",
        ):
            with self.subTest(error=error):
                self.assertFalse(_is_reasoning_passback_error(error))


class EnsureReasoningPassbackTest(unittest.TestCase):
    def test_fills_missing_reasoning_for_tool_call_message(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "thinking turn", "reasoning_content": "думал"},
            {"role": "tool", "tool_call_id": "call_0", "content": "out0"},
            _tool_call_message("call_1"),
            {"role": "tool", "tool_call_id": "call_1", "content": "out1"},
        ]
        patched, filled = _ensure_reasoning_passback(messages)
        self.assertEqual(1, filled)
        self.assertEqual("", patched[4]["reasoning_content"])
        # Original list is not modified.
        self.assertNotIn("reasoning_content", messages[4])

    def test_leaves_payload_untouched_without_reasoning_anywhere(self):
        """Non-thinking providers (local llama-server, standard OpenAI models)."""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
            _tool_call_message("call_1"),
            {"role": "tool", "tool_call_id": "call_1", "content": "out1"},
        ]
        patched, filled = _ensure_reasoning_passback(messages)
        self.assertEqual(0, filled)
        self.assertIs(messages, patched)
        self.assertNotIn("reasoning_content", patched[2])

    def test_does_not_overwrite_existing_reasoning(self):
        messages = [
            {"role": "assistant", "content": "x", "reasoning_content": "родное"},
            {"role": "assistant", "content": "y", "reasoning_content": "тоже"},
        ]
        patched, filled = _ensure_reasoning_passback(messages)
        self.assertEqual(0, filled)
        self.assertIs(messages, patched)
        self.assertEqual("родное", patched[0]["reasoning_content"])

    def test_does_not_touch_assistant_without_tool_calls(self):
        messages = [
            {"role": "assistant", "content": "think", "reasoning_content": "думал"},
            {"role": "assistant", "content": "просто текст"},
        ]
        patched, filled = _ensure_reasoning_passback(messages)
        self.assertEqual(0, filled)
        self.assertNotIn("reasoning_content", patched[1])

    def test_force_fills_even_without_any_reasoning(self):
        messages = [
            {"role": "user", "content": "task"},
            _tool_call_message("call_1"),
            {"role": "tool", "tool_call_id": "call_1", "content": "out1"},
        ]
        patched, filled = _ensure_reasoning_passback(messages, force=True)
        self.assertEqual(1, filled)
        self.assertEqual("", patched[1]["reasoning_content"])

    def test_empty_reasoning_does_not_count_as_existing(self):
        messages = [
            {"role": "assistant", "content": "x", "reasoning_content": ""},
            _tool_call_message("call_1"),
            {"role": "tool", "tool_call_id": "call_1", "content": "out1"},
        ]
        patched, filled = _ensure_reasoning_passback(messages)
        self.assertEqual(0, filled)
        self.assertNotIn("reasoning_content", patched[1])


class ConverterPayloadTest(unittest.TestCase):
    """Actual path: round history -> OpenAI payload -> normalization."""

    def _payload(self, rounds):
        anth = [{"role": "user", "content": "Выполни echo."}]
        for call_id, reasoning in rounds:
            blocks = []
            if reasoning:
                blocks.append({"type": "__reasoning", "text": reasoning})
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": "Bash",
                    "input": {"command": "echo 1"},
                }
            )
            anth.append({"role": "assistant", "content": blocks})
            anth.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": call_id, "content": "ok"}
                    ],
                }
            )
        return _anthropic_messages_to_openai(
            [{"role": "system", "content": "sys"}] + anth
        )

    def test_converter_keeps_reasoning_and_drops_it_for_silent_round(self):
        payload = self._payload([("c1", "первое"), ("c2", None)])
        assistants = [m for m in payload if m.get("role") == "assistant"]
        self.assertEqual("первое", assistants[0]["reasoning_content"])
        self.assertNotIn("reasoning_content", assistants[1])

    def test_normalized_payload_has_reasoning_for_every_tool_call_message(self):
        payload = self._payload([("c1", "первое"), ("c2", None), ("c3", "третье")])
        patched, filled = _ensure_reasoning_passback(payload)
        self.assertEqual(1, filled)
        assistants = [m for m in patched if m.get("role") == "assistant"]
        self.assertEqual(3, len(assistants))
        for message in assistants:
            self.assertIn("reasoning_content", message)
        self.assertEqual("первое", assistants[0]["reasoning_content"])
        self.assertEqual("", assistants[1]["reasoning_content"])
        self.assertEqual("третье", assistants[2]["reasoning_content"])


if __name__ == "__main__":
    unittest.main()
