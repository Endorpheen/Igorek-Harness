"""Regression: messages received during task execution are delivered in the same move.

Equivalence with standard PC chat: a user message does not create a new task but
appends to the current dialog with the same task_id.
"""

import json
import tempfile
import unittest

from factory.core import cc_backend_executor
from factory.core.cc_backend_executor import execute_cc_backend_task


class _FakeTransport:
    """Transport delivering pre-defined text model responses."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    def __call__(self, url, payload, headers, *_args, **_kwargs):
        self.payloads.append(payload)
        text = self.responses.pop(0) if self.responses else "пусто"
        return (
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
            1,
        )


class SteerDeliveryTest(unittest.TestCase):
    def _run(self, responses, provider):
        with tempfile.TemporaryDirectory() as tmp:
            transport = _FakeTransport(responses)
            old_streaming = cc_backend_executor._http_post_streaming_openai
            old_post = cc_backend_executor._http_post
            old_context_window = cc_backend_executor._resolve_backend_context_window
            cc_backend_executor._http_post_streaming_openai = transport
            cc_backend_executor._http_post = lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("Anthropic transport must not be used")
            )
            cc_backend_executor._resolve_backend_context_window = (
                lambda **_kwargs: (32_768, "test")
            )
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:8081/v1",
                    model="ornith-1.5-35b",
                    system_prompt="test",
                    user_message="первое сообщение",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    api_format="openai",
                    request_headers=None,
                    enabled_tools=[],
                    pending_steer_provider=provider,
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming
                cc_backend_executor._http_post = old_post
                cc_backend_executor._resolve_backend_context_window = old_context_window
        return result, transport

    def test_pending_message_continues_the_same_turn(self):
        calls: list[int] = []

        def provider():
            calls.append(1)
            if len(calls) == 1:
                return [
                    {
                        "text": "и ещё уточнение",
                        "message_id": "m2",
                        "input_files": [],
                        "selected_skills": [],
                    }
                ]
            return []

        result, transport = self._run(["первый ответ", "второй ответ"], provider)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "второй ответ")
        # The model is called twice: the initial move and continuation with a message.
        self.assertEqual(len(transport.payloads), 2)
        second_messages = json.dumps(transport.payloads[1]["messages"], ensure_ascii=False)
        self.assertIn("и ещё уточнение", second_messages)
        self.assertIn("первый ответ", second_messages)

    def test_without_pending_messages_turn_finishes_as_before(self):
        def provider():
            return []

        result, transport = self._run(["единственный ответ"], provider)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "единственный ответ")
        self.assertEqual(len(transport.payloads), 1)

    def test_force_ahead_message_is_delivered_without_waiting_for_turn_end(self):
        """A forced message arrives in the same task after the current round.

        Igorek scenario: the agent is active, the user clicks 'Send now' on the second message. The message should not wait for the entire task to complete—it is delivered by the queue provider immediately after the current round, within the same task ID.
        """
        calls: list[int] = []

        def provider():
            # The queue line appears immediately after the first round, as prioritize_steer does,
            # and is delivered first.
            calls.append(1)
            if len(calls) == 1:
                return [
                    {
                        "text": "форсированное уточнение",
                        "message_id": "forced-1",
                        "input_files": [],
                        "selected_skills": [],
                    }
                ]
            return []

        result, transport = self._run(["первый ответ", "второй ответ"], provider)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "второй ответ")
        # The model is called twice: the initial move and continuation with a forced
        # message within the same task.
        self.assertEqual(len(transport.payloads), 2)
        second_messages = json.dumps(transport.payloads[1]["messages"], ensure_ascii=False)
        self.assertIn("форсированное уточнение", second_messages)
        self.assertIn("первый ответ", second_messages)
        # The forced message follows the already spoken response, rather than replacing it:
        # continuation is a new user move after the assistant’s reply.
        self.assertLess(
            second_messages.index("первый ответ"),
            second_messages.index("форсированное уточнение"),
        )

    def test_broken_provider_does_not_break_the_turn(self):
        def provider():
            raise RuntimeError("queue is down")

        result, transport = self._run(["ответ"], provider)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "ответ")
        self.assertEqual(len(transport.payloads), 1)


if __name__ == "__main__":
    unittest.main()
