"""Integration tests: retry on `reasoning_content` passback error in the executor loop.

The upstream thinking mode (via DeepSeek through opencode zen) may return HTTP 400:
``The `reasoning_content` in the thinking mode must be passed back to the API``,
if an assistant message with `tool_calls` arrives without `reasoning_content`. The executor should retry the same round with forced payload normalization.
"""

import json
import tempfile
import unittest

import factory.core.cc_backend_executor as cc_backend_executor
from factory.core.cc_backend_executor import execute_cc_backend_task

PASSBACK_ERROR = (
    'HTTP 400: {"error":{"param":null,"type":"invalid_request_error",'
    '"code":"invalid_request_error","message":"Upstream request failed: '
    '[invalid_request_error] The `reasoning_content` in the thinking mode must be '
    'passed back to the API."}}'
)

ENABLED_TOOLS = [
    {
        "name": "Bash",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }
]


def _tool_call_body(*, reasoning: str | None) -> dict:
    message: dict = {"role": "assistant", "content": ""}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    message["tool_calls"] = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "Bash", "arguments": json.dumps({"command": "echo hi"})},
        }
    ]
    return {
        "id": "chatcmpl-tool",
        "object": "chat.completion",
        "model": "test",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
        "usage": {},
    }


def _text_body(text: str) -> dict:
    return {
        "id": "chatcmpl-text",
        "object": "chat.completion",
        "model": "test",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }


class ReasoningPassbackRetryTest(unittest.TestCase):
    def _run(self, bodies: list[dict]) -> tuple[dict, list[dict]]:
        observed_payloads: list[dict] = []
        old_streaming = cc_backend_executor._http_post_streaming_openai
        queue = list(bodies)

        def fake_streaming(_url, payload, *_args, **_kwargs):
            observed_payloads.append(payload)
            body = queue.pop(0) if queue else {"error": "HTTP 500: unexpected extra call"}
            return body, 1

        cc_backend_executor._http_post_streaming_openai = fake_streaming
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Выполни echo.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=ENABLED_TOOLS,
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test"},
                    log_fn=lambda _m: None,
                )
        finally:
            cc_backend_executor._http_post_streaming_openai = old_streaming
        return result, observed_payloads

    def test_retry_normalizes_payload_and_continues(self):
        result, observed = self._run(
            [_tool_call_body(reasoning=None), {"error": PASSBACK_ERROR}, _text_body("Готово.")]
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "Готово.")
        self.assertEqual(3, len(observed), "ожидался ровно один retry раунда")

        retried_messages = observed[2]["messages"]
        assistants = [m for m in retried_messages if m.get("role") == "assistant"]
        self.assertTrue(assistants)
        for message in assistants:
            if message.get("tool_calls"):
                self.assertIn("reasoning_content", message)
                self.assertEqual("", message["reasoning_content"])

        # The first (discarded) payload field was missing—this is what we are fixing.
        first_assistants = [
            m for m in observed[0]["messages"] if m.get("role") == "assistant"
        ]
        self.assertFalse(
            any("reasoning_content" in m for m in first_assistants if m.get("tool_calls"))
        )

    def test_no_pointless_retry_when_nothing_to_normalize(self):
        result, observed = self._run(
            [_tool_call_body(reasoning="думал над задачей"), {"error": PASSBACK_ERROR}]
        )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "http_error")
        self.assertEqual(2, len(observed), "повтор без изменений payload не нужен")


if __name__ == "__main__":
    unittest.main()
