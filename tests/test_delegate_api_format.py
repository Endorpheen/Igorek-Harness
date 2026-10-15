"""Regression: `delegate_task` must use the same transport as the parent.

Bug: A profile with `api_format: "openai"` (e.g., cc-opencode-go) ran the parent execution via the OpenAI path `/chat/completions`, while the child `delegate_task`—without `api_format`—defaulted to the Anthropic path `/messages` and received a 404 HTML response. This fixes the structural contract:
The executor forwards `api_format` (and authentication headers) into the `kwargs` of the tool, and `delegate_task` passes them to the child execution.

The prompt layer is untouched: the contract is purely transport-related.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core import cc_backend_executor, delegate_tool
from factory.core.subagents import subagents_journal_path


class DelegateForwardsTransportTest(unittest.TestCase):
    """The `delegate_task` to `execute_cc_backend_task` ensures transport compatibility."""

    def _run(self, **overrides):
        captured: dict = {}

        def fake_task(**kwargs):
            captured.update(kwargs)
            return {
                "status": "ok",
                "output_text": "готово",
                "tool_calls_count": 0,
                "usage": {},
            }

        with tempfile.TemporaryDirectory(prefix="delegate_transport_") as tmp:
            with patch.object(
                cc_backend_executor, "execute_cc_backend_task", fake_task
            ):
                raw = delegate_tool.delegate_task(
                    {"goal": "noop"},
                    **{
                        "workdir": tmp,
                        "artifact_workdir": tmp,
                        "filesystem_mode": "workspace",
                        "base_url": "https://opencode.ai/zen/go/v1",
                        "model": "deepseek-v4.1-flash",
                        **overrides,
                    },
                )
            payload = json.loads(raw)

        return captured, payload

    def test_delegate_forwards_api_format_and_headers_to_child(self) -> None:
        captured, payload = self._run(
            api_format="openai",
            request_headers={"Authorization": "Bearer test-key"},
        )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(captured.get("api_format"), "openai")
        self.assertEqual(
            captured.get("request_headers"), {"Authorization": "Bearer test-key"}
        )

    def test_delegate_preserves_responses_and_renewable_host_headers(self) -> None:
        from factory.core.openai_responses import RenewingHeaders
        headers = RenewingHeaders({}, lambda: {"Authorization": "Bearer refreshed-fixture"})
        captured, payload = self._run(api_format="openai-responses", request_headers=headers)
        self.assertEqual(captured["api_format"], "openai-responses")
        self.assertIs(captured["request_headers"], headers)
        self.assertEqual(captured["request_headers"].current()["Authorization"], "Bearer refreshed-fixture")

    def test_delegate_without_api_format_keeps_anthropic_default(self) -> None:
        # Backward compatibility: direct calls without `api_format` behave as before—Anthropic transport and no headers.
        captured, _ = self._run()

        self.assertEqual(captured.get("api_format"), "anthropic")
        self.assertIsNone(captured.get("request_headers"))


class ExecutorPassesTransportToDispatchTest(unittest.TestCase):
    """The `execute_cc_backend_task` function finalizes the transport configuration within `kwargs` for `execute_tool`."""

    def test_main_loop_dispatch_forwards_api_format_and_headers(self) -> None:
        captured: list[dict] = []
        requested_urls: list[str] = []

        def fake_execute_tool(name, input_data, *args, **kwargs):
            captured.append({"name": name, **kwargs})
            return json.dumps({"status": "ok", "summary": "done"})

        responses: list[dict] = [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_delegate",
                                    "type": "function",
                                    "function": {
                                        "name": "delegate_task",
                                        "arguments": json.dumps({"goal": "sub"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {},
            },
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "итог"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
            },
        ]

        def fake_streaming(url, _payload, *_args, **_kwargs):
            requested_urls.append(url)
            body = responses.pop(0)
            return body, 1

        with tempfile.TemporaryDirectory(prefix="exec_transport_") as tmp:
            with (
                patch.object(
                    cc_backend_executor, "_http_post_streaming_openai", fake_streaming
                ),
                patch.object(
                    cc_backend_executor,
                    "_resolve_backend_context_window",
                    lambda **_kwargs: (32_768, "test"),
                ),
                patch.object(cc_backend_executor, "execute_tool", fake_execute_tool),
            ):
                result = cc_backend_executor.execute_cc_backend_task(
                    base_url="https://opencode.ai/zen/go/v1",
                    model="deepseek-v4.1-flash",
                    system_prompt="system",
                    user_message="ping",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test-key"},
                )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(requested_urls)
        self.assertTrue(
            all(url.endswith("/chat/completions") for url in requested_urls),
            requested_urls,
        )
        self.assertEqual(len(captured), 1)
        call = captured[0]
        self.assertEqual(call["name"], "delegate_task")
        self.assertEqual(call.get("api_format"), "openai")
        self.assertEqual(
            call.get("request_headers"), {"Authorization": "Bearer test-key"}
        )
        # Linking a subagent to the toolcard in the web interface and the trace working directory:
        # the `subagents.jsonl` log file should be placed where the bridge reads it.
        self.assertEqual(call.get("tool_use_id"), "call_delegate")
        self.assertEqual(call.get("artifact_workdir"), tmp)


class DelegateEndToEndTransportTest(unittest.TestCase):
    """Full execution chain: `delegate_task` → `execute_cc_backend_task` → HTTP request."""

    def test_child_uses_parent_openai_transport_and_writes_journal(self) -> None:
        requested_urls: list[str] = []
        responses: list[dict] = [
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ответ субагента"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        ]

        def fake_streaming(url, *_args, **_kwargs):
            requested_urls.append(url)
            return responses.pop(0), 1

        def fail_anthropic(*_args, **_kwargs):
            raise AssertionError("child must not use the Anthropic /messages path")

        with tempfile.TemporaryDirectory(prefix="delegate_e2e_") as tmp:
            with (
                patch.object(
                    cc_backend_executor, "_http_post_streaming_openai", fake_streaming
                ),
                patch.object(cc_backend_executor, "_http_post", fail_anthropic),
                patch.object(
                    cc_backend_executor,
                    "_resolve_backend_context_window",
                    lambda **_kwargs: (32_768, "test"),
                ),
            ):
                raw = delegate_tool.delegate_task(
                    {"goal": "ping"},
                    workdir=tmp,
                    artifact_workdir=tmp,
                    filesystem_mode="workspace",
                    base_url="https://opencode.ai/zen/go/v1",
                    model="deepseek-v4.1-flash",
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test-key"},
                )
            payload = json.loads(raw)
            journal = subagents_journal_path(tmp)
            lines = [
                json.loads(line)
                for line in journal.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["summary"], "ответ субагента")
        self.assertTrue(requested_urls)
        self.assertEqual(
            requested_urls[0], "https://opencode.ai/zen/go/v1/chat/completions"
        )
        # The subagent appears in the log that the 'Session subagents' tab reads.
        self.assertEqual([line["event"] for line in lines], ["start", "finish"])
        self.assertEqual(lines[1]["status"], "ok")
        self.assertEqual(lines[1]["summary"], "ответ субагента")


if __name__ == "__main__":
    unittest.main(verbosity=2)
