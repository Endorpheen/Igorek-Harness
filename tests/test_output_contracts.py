import json
import os
import tempfile
import unittest
from pathlib import Path

from factory.core import cc_backend_executor
from factory.core.cc_backend_executor import (
    _approval_signature,
    _approval_session_rule,
    _approved_tool_call,
    _auto_scale_max_tokens,
    _bash_web_redirect_enabled,
    _is_transient_network_error,
    _normalize_tagged_reasoning_blocks,
    _redirect_bash_web,
    execute_cc_backend_task,
)
from factory.core.output_contracts import detect_pending_write_action, detect_ask_to_write_without_content, detect_file_content_in_text, extract_writable_paths


class OutputContractsTest(unittest.TestCase):
    def test_executor_uses_openai_transport_without_auth_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_streaming = cc_backend_executor._http_post_streaming_openai
            old_post = cc_backend_executor._http_post
            old_context_window = cc_backend_executor._resolve_backend_context_window
            calls = []

            def fake_streaming(url, payload, headers, *_args, **_kwargs):
                calls.append((url, payload, headers))
                return (
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "Локальная модель отвечает.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    },
                    1,
                )

            def fail_anthropic(*_args, **_kwargs):
                raise AssertionError("OpenAI profile must not use Anthropic transport")

            cc_backend_executor._http_post_streaming_openai = fake_streaming
            cc_backend_executor._http_post = fail_anthropic
            cc_backend_executor._resolve_backend_context_window = (
                lambda **_kwargs: (32_768, "test")
            )
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:8081/v1",
                    model="ornith-1.5-35b",
                    system_prompt="test",
                    user_message="ping",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    api_format="openai",
                    request_headers=None,
                    enabled_tools=[],
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming
                cc_backend_executor._http_post = old_post
                cc_backend_executor._resolve_backend_context_window = old_context_window

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "Локальная модель отвечает.")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "http://127.0.0.1:8081/v1/chat/completions")
        self.assertNotIn("Authorization", calls[0][2])
        self.assertNotIn("x-api-key", calls[0][2])

    def test_openai_tool_dispatch_executes_read_without_unbound_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "notes.txt"
            target.write_text("dispatcher reached tool handler", encoding="utf-8")
            old_streaming = cc_backend_executor._http_post_streaming_openai
            old_context_window = cc_backend_executor._resolve_backend_context_window
            observed_payloads = []

            def fake_streaming(_url, payload, *_args, **_kwargs):
                observed_payloads.append(payload)
                if len(observed_payloads) == 1:
                    return (
                        {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": None,
                                        "tool_calls": [
                                            {
                                                "id": "call_read",
                                                "type": "function",
                                                "function": {
                                                    "name": "Read",
                                                    "arguments": json.dumps(
                                                        {"file_path": str(target)}
                                                    ),
                                                },
                                            }
                                        ],
                                    },
                                    "finish_reason": "tool_calls",
                                }
                            ],
                            "usage": {},
                        },
                        1,
                    )
                return (
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "Файл прочитан.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post_streaming_openai = fake_streaming
            cc_backend_executor._resolve_backend_context_window = (
                lambda **_kwargs: (32_768, "test")
            )
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:8081/v1",
                    model="test",
                    system_prompt="test",
                    user_message="Прочитай notes.txt.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Read",
                            "input_schema": {
                                "type": "object",
                                "properties": {"file_path": {"type": "string"}},
                                "required": ["file_path"],
                            },
                        }
                    ],
                    api_format="openai",
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming
                cc_backend_executor._resolve_backend_context_window = old_context_window

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "Файл прочитан.")
        self.assertEqual(len(observed_payloads), 2)
        tool_results = [
            message["content"]
            for message in observed_payloads[1]["messages"]
            if message.get("role") == "tool"
        ]
        self.assertEqual(len(tool_results), 1)
        self.assertIn("dispatcher reached tool handler", tool_results[0])
        self.assertNotIn("sudo_provider", tool_results[0])

    def test_write_session_rule_allows_same_file_with_new_content_only(self):
        first = {"file_path": "/tmp/report.md", "content": "first"}
        metadata = {
            "approved_session_rules": [_approval_session_rule("Write", first)],
        }

        self.assertTrue(_approved_tool_call(
            metadata=metadata,
            tool_name="Write",
            tool_input={"file_path": "/tmp/report.md", "content": "second"},
        ))
        self.assertFalse(_approved_tool_call(
            metadata=metadata,
            tool_name="Write",
            tool_input={"file_path": "/tmp/other.md", "content": "second"},
        ))

    def test_bash_session_rule_allows_exact_command_only(self):
        metadata = {
            "approved_session_rules": [
                _approval_session_rule("Bash", {"command": "rm -f /tmp/demo"})
            ],
        }

        self.assertTrue(_approved_tool_call(
            metadata=metadata,
            tool_name="Bash",
            tool_input={"command": "rm -f /tmp/demo"},
        ))
        self.assertFalse(_approved_tool_call(
            metadata=metadata,
            tool_name="Bash",
            tool_input={"command": "rm -f /tmp/other"},
        ))

    def test_legacy_session_tool_names_remain_accepted(self):
        self.assertTrue(_approved_tool_call(
            metadata={"approved_session_tools": ["Write"]},
            tool_name="Write",
            tool_input={"file_path": "/tmp/legacy.md", "content": "ok"},
        ))

    def test_tagged_reasoning_is_promoted_out_of_visible_text(self):
        blocks = _normalize_tagged_reasoning_blocks([
            {
                "type": "text",
                "text": "<think>Проверяю ответ.</think>\nДа, тут.",
            }
        ])

        self.assertEqual(blocks, [
            {"type": "__reasoning", "text": "Проверяю ответ."},
            {"type": "text", "text": "\nДа, тут."},
        ])

    def test_tagged_reasoning_inside_code_fence_stays_visible(self):
        text = "```xml\n<think>это пример</think>\n```"

        self.assertEqual(
            _normalize_tagged_reasoning_blocks([{"type": "text", "text": text}]),
            [{"type": "text", "text": text}],
        )

    def test_structured_anthropic_thinking_is_promoted_out_of_visible_text(self):
        blocks = _normalize_tagged_reasoning_blocks([
            {"type": "thinking", "thinking": "Проверяю ответ.", "signature": "sig"},
            {"type": "text", "text": "Да, тут."},
        ])

        self.assertEqual(blocks, [
            {"type": "__reasoning", "text": "Проверяю ответ."},
            {"type": "text", "text": "Да, тут."},
        ])

    def test_executor_hides_tagged_reasoning_and_traces_structured_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_post = cc_backend_executor._http_post

            def fake_post(*_args, **_kwargs):
                return (
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": "<think>Сначала проверю.</think>\nГотово.",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post = fake_post
            try:
                result = execute_cc_backend_task(
                    base_url="https://opencode.ai/zen/go/v1",
                    model="minimax-m3",
                    system_prompt="test",
                    user_message="ping",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    api_format="anthropic",
                    request_headers={"x-api-key": "test"},
                    enabled_tools=[],
                )
            finally:
                cc_backend_executor._http_post = old_post

            trace = Path(tmp, "cc_backend_trace.jsonl").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "\nГотово.")
        self.assertNotIn("<think>", result["output_text"])
        self.assertIn('"kind": "model.reasoning_delta"', trace)
        self.assertIn("Сначала проверю.", trace)

    def test_executor_hides_structured_anthropic_thinking_and_traces_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_post = cc_backend_executor._http_post

            def fake_post(*_args, **_kwargs):
                return (
                    {
                        "content": [
                            {"type": "thinking", "thinking": "Сначала проверю."},
                            {"type": "text", "text": "Готово."},
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post = fake_post
            try:
                result = execute_cc_backend_task(
                    base_url="https://api.minimax.io/anthropic",
                    model="MiniMax-M3",
                    system_prompt="test",
                    user_message="ping",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    api_format="anthropic",
                    request_headers={"x-api-key": "test"},
                    enabled_tools=[],
                )
            finally:
                cc_backend_executor._http_post = old_post

            trace = Path(tmp, "cc_backend_trace.jsonl").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "Готово.")
        self.assertNotIn("Сначала проверю.", result["output_text"])
        self.assertIn('"kind": "model.reasoning_delta"', trace)
        self.assertIn("Сначала проверю.", trace)

    def test_context_window_cache_is_used_for_non_curl_transport(self):
        old_cache = cc_backend_executor._context_window_from_models_cache
        old_urlopen = cc_backend_executor.urllib.request.urlopen

        def fail_urlopen(*_args, **_kwargs):
            raise AssertionError("network probe must not run when cache is populated")

        cc_backend_executor._context_window_from_models_cache = lambda _model: 100_000
        cc_backend_executor.urllib.request.urlopen = fail_urlopen
        try:
            window, source = cc_backend_executor._resolve_backend_context_window(
                base_url="https://example.invalid/v1",
                model="qwen3.7-max",
                fallback=32_000,
                use_curl=False,
            )
        finally:
            cc_backend_executor._context_window_from_models_cache = old_cache
            cc_backend_executor.urllib.request.urlopen = old_urlopen

        self.assertEqual(window, 100_000)
        self.assertEqual(source, "models_cache")

    def test_bash_web_redirect_disabled_for_remote_profiles_by_default(self):
        for base_url in (
            "https://opencode.ai/zen/go/v1",
            "https://openrouter.ai/api/v1",
            "https://api.minimax.io/anthropic",
        ):
            self.assertFalse(_bash_web_redirect_enabled("", base_url))

    def test_bash_web_redirect_enabled_for_local_backend_by_default(self):
        self.assertTrue(
            _bash_web_redirect_enabled("", "http://127.0.0.1:8001/v1")
        )
        self.assertTrue(
            _bash_web_redirect_enabled("", "http://localhost:8001/v1")
        )

    def test_bash_web_redirect_respects_explicit_profile_flag(self):
        def fake_get_profile_config(profile):
            self.assertEqual(profile, "cc-local")
            return {"redirect_bash_web_to_tools": False}

        from factory.core import runtime

        old_runtime_get_profile_config = runtime.get_profile_config
        runtime.get_profile_config = fake_get_profile_config
        try:
            self.assertFalse(
                _bash_web_redirect_enabled("cc-local", "http://127.0.0.1:8001/v1")
            )
        finally:
            runtime.get_profile_config = old_runtime_get_profile_config

    def test_bash_web_redirect_explicit_true_overrides_remote_default(self):
        def fake_get_profile_config(profile):
            self.assertEqual(profile, "cc-local")
            return {"redirect_bash_web_to_tools": True}

        from factory.core import runtime

        old_runtime_get_profile_config = runtime.get_profile_config
        runtime.get_profile_config = fake_get_profile_config
        try:
            self.assertTrue(
                _bash_web_redirect_enabled("cc-local", "https://openrouter.ai/api/v1")
            )
        finally:
            runtime.get_profile_config = old_runtime_get_profile_config

    def test_redirect_bash_web_noops_when_disabled(self):
        result = _redirect_bash_web(
            {"command": 'curl -sL "https://example.com/data.txt"'},
            enabled=False,
        )
        self.assertIsNone(result)

    def test_executor_returns_failure_reason_for_openai_http_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_streaming = cc_backend_executor._http_post_streaming_openai
            calls = []

            def fake_streaming(*_args, **_kwargs):
                calls.append(1)
                return (
                    {
                        "error": (
                            "HTTP 404: "
                            '{"error":{"message":"Model is no longer available"}}'
                        )
                    },
                    1,
                )

            cc_backend_executor._http_post_streaming_openai = fake_streaming
            try:
                result = execute_cc_backend_task(
                    base_url="https://openrouter.ai/api/v1",
                    model="test/free:free",
                    system_prompt="test",
                    user_message="ping",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test"},
                    enabled_tools=[],
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "http_error")
        self.assertIn("Model is no longer available", result["failure_details"])
        self.assertEqual(len(calls), 1)

    def test_executor_retries_transient_openai_dns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_streaming = cc_backend_executor._http_post_streaming_openai
            old_sleep = cc_backend_executor.time.sleep
            calls = []

            def fake_streaming(*_args, **_kwargs):
                calls.append(1)
                if len(calls) == 1:
                    return {"error": "[Errno -3] Temporary failure in name resolution"}, 1
                return (
                    {
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "Готово."},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    },
                    1,
                )

            cc_backend_executor._http_post_streaming_openai = fake_streaming
            cc_backend_executor.time.sleep = lambda _seconds: None
            try:
                result = execute_cc_backend_task(
                    base_url="https://opencode.ai/zen/go/v1",
                    model="deepseek-v4-flash",
                    system_prompt="test",
                    user_message="ping",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test"},
                    enabled_tools=[],
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming
                cc_backend_executor.time.sleep = old_sleep

            trace = Path(tmp, "cc_backend_trace.jsonl").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "Готово.")
        self.assertEqual(len(calls), 2)
        self.assertIn('"kind": "network.retry"', trace)

    def test_executor_retries_remote_disconnected_anthropic_transport(self):
        self.assertTrue(
            _is_transient_network_error(
                "transport:remote_disconnected: Remote end closed connection without response"
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            old_post = cc_backend_executor._http_post
            old_sleep = cc_backend_executor.time.sleep
            calls = []

            def fake_post(*_args, **_kwargs):
                calls.append(1)
                if len(calls) == 1:
                    return (
                        {
                            "error": (
                                "transport:remote_disconnected: "
                                "Remote end closed connection without response"
                            )
                        },
                        1,
                    )
                return (
                    {
                        "content": [{"type": "text", "text": "Готово."}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                    1,
                )

            cc_backend_executor._http_post = fake_post
            cc_backend_executor.time.sleep = lambda _seconds: None
            try:
                result = execute_cc_backend_task(
                    base_url="https://api.z.ai/api/anthropic",
                    model="glm-5.2",
                    system_prompt="test",
                    user_message="ping",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    api_format="anthropic",
                    request_headers={"x-api-key": "test"},
                    enabled_tools=[],
                )
            finally:
                cc_backend_executor._http_post = old_post
                cc_backend_executor.time.sleep = old_sleep

            trace = Path(tmp, "cc_backend_trace.jsonl").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "Готово.")
        self.assertEqual(len(calls), 2)
        self.assertIn('"kind": "network.retry"', trace)

    def test_detects_excalidraw_json_artifact_for_single_requested_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "diagram.excalidraw")
            payload = {
                "type": "excalidraw",
                "version": 2,
                "elements": [],
                "appState": {"viewBackgroundColor": "#ffffff"},
            }

            action = detect_pending_write_action(
                user_message=f"Создай файл {target}",
                assistant_text="```json\n" + json.dumps(payload) + "\n```",
                workdir=tmp,
            )

        self.assertIsNotNone(action)
        self.assertEqual(action.file_path, target)
        self.assertEqual(json.loads(action.content)["type"], "excalidraw")

    def test_detects_excalidraw_jsonc_artifact_with_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "diagram.excalidraw")
            assistant_text = """
{
  "type": "excalidraw",
  // title element
  "version": 2,
  "elements": [
    {
      "type": "text",
      "text": "https://example.test/not-a-comment"
    }
  ],
  "appState": {
    /* block comment */
    "viewBackgroundColor": "#ffffff"
  }
}
"""

            action = detect_pending_write_action(
                user_message=f"Создай файл {target}",
                assistant_text=assistant_text,
                workdir=tmp,
            )

        self.assertIsNotNone(action)
        parsed = json.loads(action.content)
        self.assertEqual(parsed["type"], "excalidraw")
        self.assertEqual(
            parsed["elements"][0]["text"], "https://example.test/not-a-comment"
        )

    def test_executor_turns_structural_artifact_into_approval_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "diagram.excalidraw")
            payload = {
                "type": "excalidraw",
                "version": 2,
                "elements": [],
            }
            old_http_post = cc_backend_executor._http_post

            def fake_http_post(*_args, **_kwargs):
                return (
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": "```json\n" + json.dumps(payload) + "\n```",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post = fake_http_post
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message=f"Создай файл {target}",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Write",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "file_path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["file_path", "content"],
                            },
                        }
                    ],
                )
            finally:
                cc_backend_executor._http_post = old_http_post

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "approval_required")
        [blocked] = result["approval_blocked_tools"]
        self.assertEqual(blocked["tool"], "Write")
        self.assertEqual(blocked["target"], target)
        self.assertEqual(blocked["source"], "structural_file_artifact")
        self.assertFalse(Path(target).exists())

    def test_approval_payload_uses_structured_tool_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "reasoned.txt")
            old_http_post = cc_backend_executor._http_post

            def fake_http_post(*_args, **_kwargs):
                return (
                    {
                        "content": [
                            {"type": "text", "text": "Сделаю файл."},
                            {
                                "type": "tool_use",
                                "id": "call_reasoned",
                                "name": "Write",
                                "input": {
                                    "file_path": target,
                                    "content": "ok\n",
                                    "reason": "Нужно сохранить результат в файл.",
                                },
                            },
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post = fake_http_post
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message=f"Создай файл {target}",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Write",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "file_path": {"type": "string"},
                                    "content": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["file_path", "content"],
                            },
                        }
                    ],
                )
            finally:
                cc_backend_executor._http_post = old_http_post

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "approval_required")
        [blocked] = result["approval_blocked_tools"]
        self.assertEqual(blocked["tool"], "Write")
        self.assertEqual(blocked["reason"], "Нужно сохранить результат в файл.")
        self.assertEqual(blocked["assistant_text"], "Сделаю файл.")
        self.assertEqual(blocked["session_rule"], {
            "rule_type": "file_path",
            "tool": "Write",
            "file_path": target,
        })
        self.assertNotEqual(blocked["reason"], blocked["assistant_text"])
        self.assertFalse(Path(target).exists())

    def test_forced_write_executes_after_exact_approval_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "diagram.excalidraw")
            content = json.dumps(
                {"type": "excalidraw", "version": 2, "elements": []},
                ensure_ascii=False,
            )
            tool_input = {"file_path": target, "content": content}

            result = execute_cc_backend_task(
                base_url="http://127.0.0.1:1",
                model="test",
                system_prompt="test",
                user_message="ignored",
                workdir=tmp,
                artifact_workdir=tmp,
                enabled_tools=[
                    {
                        "name": "Write",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["file_path", "content"],
                        },
                    }
                ],
                metadata={
                    "forced_tool_call": {
                        "name": "Write",
                        "input": tool_input,
                        "source": "structural_file_artifact",
                    },
                    "approved_tool_calls": [_approval_signature("Write", tool_input)],
                },
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(Path(target).read_text(encoding="utf-8"), content)

    def test_mobile_disabled_approvals_execute_forced_write_without_saved_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "approval-disabled.txt")
            tool_input = {"file_path": target, "content": "ok\n"}

            result = execute_cc_backend_task(
                base_url="http://127.0.0.1:1",
                model="test",
                system_prompt="test",
                user_message="ignored",
                workdir=tmp,
                artifact_workdir=tmp,
                enabled_tools=[
                    {
                        "name": "Write",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["file_path", "content"],
                        },
                    }
                ],
                metadata={
                    "forced_tool_call": {
                        "name": "Write",
                        "input": tool_input,
                        "source": "structural_file_artifact",
                    }
                },
                approvals_enabled_provider=lambda: False,
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(Path(target).read_text(encoding="utf-8"), "ok\n")

    def test_android_approval_pauses_task_in_waiting_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "waiting.txt")
            tool_input = {"file_path": target, "content": "pending\n"}

            result = execute_cc_backend_task(
                base_url="http://127.0.0.1:1",
                model="test",
                system_prompt="test",
                user_message="ignored",
                workdir=tmp,
                artifact_workdir=tmp,
                enabled_tools=[
                    {
                        "name": "Write",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["file_path", "content"],
                        },
                    }
                ],
                metadata={
                    "forced_tool_call": {
                        "name": "Write",
                        "input": tool_input,
                        "tool_use_id": "call_waiting",
                    }
                },
                source_platform="android",
            )

        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(result["failure_reason"], "approval_required")
        self.assertFalse(Path(target).exists())

    def test_forced_write_executes_after_file_scoped_session_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "report.md")
            prior_input = {"file_path": target, "content": "old\n"}
            next_input = {"file_path": target, "content": "new\n"}

            result = execute_cc_backend_task(
                base_url="http://127.0.0.1:1",
                model="test",
                system_prompt="test",
                user_message="ignored",
                workdir=tmp,
                artifact_workdir=tmp,
                enabled_tools=[
                    {
                        "name": "Write",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["file_path", "content"],
                        },
                    }
                ],
                metadata={
                    "forced_tool_call": {
                        "name": "Write",
                        "input": next_input,
                        "source": "structural_file_artifact",
                    },
                    "approved_session_rules": [
                        _approval_session_rule("Write", prior_input)
                    ],
                },
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(Path(target).read_text(encoding="utf-8"), "new\n")

    def test_blocked_forced_tool_call_keeps_the_id_it_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "report.md")
            tool_input = {"file_path": target, "content": "new\n"}

            result = execute_cc_backend_task(
                base_url="http://127.0.0.1:1",
                model="test",
                system_prompt="test",
                user_message="ignored",
                workdir=tmp,
                artifact_workdir=tmp,
                enabled_tools=[
                    {
                        "name": "Write",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["file_path", "content"],
                        },
                    }
                ],
                metadata={
                    "forced_tool_call": {
                        "name": "Write",
                        "input": tool_input,
                        "source": "forced_tool_call",
                        "tool_use_id": "call_resume_1",
                    },
                },
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["failure_reason"], "approval_required")
            blocked = result["approval_blocked_tools"]
            self.assertEqual(len(blocked), 1)
            self.assertEqual(blocked[0]["tool_use_id"], "call_resume_1")
            published = [
                entry["tool_use_id"]
                for entry in (
                    json.loads(line)
                    for line in Path(tmp, "cc_backend_trace.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
                if entry.get("kind") == "tool.approval_required"
            ]
            self.assertEqual(published, ["call_resume_1"])

    def test_forced_approved_runtime_tool_continues_agent_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_http_post = cc_backend_executor._http_post
            old_streaming = cc_backend_executor._http_post_streaming_openai
            observed_payloads = []

            def fake_http_post(_url, payload, *_args, **_kwargs):
                observed_payloads.append(payload)
                return (
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": "Продолжаю после подтверждённого инструмента.",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            def fake_streaming(_url, payload, *_args, **_kwargs):
                observed_payloads.append(payload)
                return (
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion",
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Продолжаю после подтверждённого инструмента.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            tool_input = {"command": "pwd"}
            cc_backend_executor._http_post = fake_http_post
            cc_backend_executor._http_post_streaming_openai = fake_streaming
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Найди файлы и продолжи анализ.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Bash",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "command": {"type": "string"},
                                },
                                "required": ["command"],
                            },
                        }
                    ],
                    metadata={
                        "forced_tool_call": {
                            "name": "Bash",
                            "input": tool_input,
                            "tool_use_id": "call_original",
                            "reasoning_content": "нужно проверить cwd",
                            "assistant_text": "Проверю директорию.",
                        },
                        "approved_tool_calls": [_approval_signature("Bash", tool_input)],
                    },
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test"},
                )
            finally:
                cc_backend_executor._http_post = old_http_post
                cc_backend_executor._http_post_streaming_openai = old_streaming

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["output_text"], "Продолжаю после подтверждённого инструмента."
        )
        self.assertEqual(result["tool_calls_count"], 1)
        self.assertEqual(len(observed_payloads), 1)
        messages = observed_payloads[0]["messages"]
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["reasoning_content"], "нужно проверить cwd")
        self.assertEqual(messages[2]["content"], "Проверю директорию.")
        self.assertEqual(messages[2]["tool_calls"][0]["id"], "call_original")
        self.assertEqual(
            messages[2]["tool_calls"][0]["function"]["name"], "Bash"
        )
        self.assertEqual(messages[3]["role"], "tool")
        self.assertEqual(messages[3]["tool_call_id"], "call_original")
        self.assertIn(tmp, messages[3]["content"])

    def test_allow_once_signature_is_consumed_before_same_call_can_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_streaming = cc_backend_executor._http_post_streaming_openai
            command = f"rm -f {Path(tmp) / 'never-created.txt'}"
            tool_input = {"command": command}

            def fake_streaming(*_args, **_kwargs):
                return (
                    {
                        "id": "chatcmpl-repeat",
                        "object": "chat.completion",
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Повторю действие.",
                                    "tool_calls": [
                                        {
                                            "id": "call_repeat",
                                            "type": "function",
                                            "function": {
                                                "name": "Bash",
                                                "arguments": json.dumps(tool_input),
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post_streaming_openai = fake_streaming
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Выполни один раз.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Bash",
                            "input_schema": {
                                "type": "object",
                                "properties": {"command": {"type": "string"}},
                                "required": ["command"],
                            },
                        }
                    ],
                    metadata={
                        "forced_tool_call": {
                            "name": "Bash",
                            "input": tool_input,
                            "tool_use_id": "call_original",
                        },
                        "approved_tool_calls": [_approval_signature("Bash", tool_input)],
                    },
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test"},
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "approval_required")
        self.assertEqual(result["approval_blocked_tools"][0]["tool_use_id"], "call_repeat")

    def test_forced_approved_tool_runs_pending_batch_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_streaming = cc_backend_executor._http_post_streaming_openai
            observed_payloads = []

            def fake_streaming(_url, payload, *_args, **_kwargs):
                observed_payloads.append(payload)
                return (
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion",
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Продолжаю после всего batch.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            target = str(Path(tmp) / "report.txt")
            write_input = {"file_path": target, "content": "ok\n"}
            bash_input = {"command": "python3 -c 'print(123)'"}
            cc_backend_executor._http_post_streaming_openai = fake_streaming
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Создай файл и запусти проверку.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Write",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "file_path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["file_path", "content"],
                            },
                        },
                        {
                            "name": "Bash",
                            "input_schema": {
                                "type": "object",
                                "properties": {"command": {"type": "string"}},
                                "required": ["command"],
                            },
                        },
                    ],
                    metadata={
                        "forced_tool_call": {
                            "name": "Write",
                            "input": write_input,
                            "tool_use_id": "call_write",
                            "assistant_text": "Сначала запишу файл.",
                            "pending_tool_calls": [
                                {
                                    "id": "call_bash",
                                    "name": "Bash",
                                    "input": bash_input,
                                }
                            ],
                        },
                        "approved_tool_calls": [_approval_signature("Write", write_input)],
                    },
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test"},
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["tool_calls_count"], 2)
            self.assertEqual(Path(target).read_text(encoding="utf-8"), "ok\n")
            self.assertEqual(len(observed_payloads), 1)
            messages = observed_payloads[0]["messages"]
            self.assertEqual(messages[2]["role"], "assistant")
            self.assertEqual(len(messages[2]["tool_calls"]), 2)
            self.assertEqual(messages[2]["tool_calls"][0]["id"], "call_write")
            self.assertEqual(messages[2]["tool_calls"][1]["id"], "call_bash")
            self.assertEqual(messages[3]["tool_call_id"], "call_write")
            self.assertEqual(messages[4]["tool_call_id"], "call_bash")
            self.assertIn("123", messages[4]["content"])

    def test_forced_approved_tool_preserves_prior_approval_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_streaming = cc_backend_executor._http_post_streaming_openai
            observed_payloads = []

            def fake_streaming(_url, payload, *_args, **_kwargs):
                observed_payloads.append(payload)
                return (
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion",
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Контекст сохранён.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            previous_input = {
                "file_path": str(Path(tmp) / "report_builder.py"),
                "content": "print('old')\n",
            }
            current_input = {
                "file_path": str(Path(tmp) / "sample_scores.json"),
                "content": "[]\n",
            }
            approval_context_messages = [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_previous",
                            "name": "Write",
                            "input": previous_input,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_previous",
                            "content": (
                                "File written successfully: "
                                f"{previous_input['file_path']}"
                            ),
                        }
                    ],
                },
            ]
            cc_backend_executor._http_post_streaming_openai = fake_streaming
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Создай файлы.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Write",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "file_path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["file_path", "content"],
                            },
                        }
                    ],
                    metadata={
                        "approval_context_messages": approval_context_messages,
                        "forced_tool_call": {
                            "name": "Write",
                            "input": current_input,
                            "tool_use_id": "call_current",
                        },
                        "approved_tool_calls": [
                            _approval_signature("Write", current_input)
                        ],
                    },
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test"},
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming

            self.assertEqual(result["status"], "ok")
            messages = observed_payloads[0]["messages"]
            previous_results = [
                item
                for item in messages
                if item.get("role") == "tool"
                and item.get("tool_call_id") == "call_previous"
            ]
            current_results = [
                item
                for item in messages
                if item.get("role") == "tool"
                and item.get("tool_call_id") == "call_current"
            ]
            self.assertEqual(len(previous_results), 1)
            self.assertEqual(len(current_results), 1)

    def test_forced_edit_plain_text_failure_continues_and_seeds_error_to_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_streaming = cc_backend_executor._http_post_streaming_openai
            observed_payloads = []

            def fake_streaming(_url, payload, *_args, **_kwargs):
                observed_payloads.append(payload)
                return (
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion",
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Edit не удался из-за old_string, но я понял ошибку.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
                    },
                    1,
                )

            target = Path(tmp) / "report.py"
            target.write_text("value = 1\n", encoding="utf-8")
            edit_input = {
                "file_path": str(target),
                "old_string": "missing = 1",
                "new_string": "missing = 2",
            }
            cc_backend_executor._http_post_streaming_openai = fake_streaming
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Измени файл.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Edit",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "file_path": {"type": "string"},
                                    "old_string": {"type": "string"},
                                    "new_string": {"type": "string"},
                                },
                                "required": [
                                    "file_path",
                                    "old_string",
                                    "new_string",
                                ],
                            },
                        }
                    ],
                    metadata={
                        "forced_tool_call": {
                            "name": "Edit",
                            "input": edit_input,
                            "tool_use_id": "call_edit",
                        },
                        "approved_tool_calls": [_approval_signature("Edit", edit_input)],
                    },
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test"},
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming

            self.assertEqual(result["status"], "ok")
            self.assertNotIn("forced_tool_call_failed", result.get("failure_reason", ""))
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")  # file unchanged
            # verify error was visible to model in the follow-up request
            self.assertTrue(len(observed_payloads) >= 1)
            # the error text should appear somewhere in messages/content
            all_text = str(observed_payloads[-1])
            self.assertIn("old_string not found", all_text)

    def test_approval_payload_preserves_reasoning_for_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_http_post = cc_backend_executor._http_post

            def fake_http_post(*_args, **_kwargs):
                return (
                    {
                        "content": [
                            {"type": "__reasoning", "text": "надо посмотреть файлы"},
                            {"type": "text", "text": "Осмотрюсь."},
                            {
                                "type": "tool_use",
                                "id": "call_original",
                                "name": "Bash",
                                "input": {
                                    "command": "rm -rf /tmp/igorek-approval-probe"
                                },
                            },
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post = fake_http_post
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Посмотри файлы.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Bash",
                            "input_schema": {
                                "type": "object",
                                "properties": {"command": {"type": "string"}},
                                "required": ["command"],
                            },
                        }
                    ],
                )
            finally:
                cc_backend_executor._http_post = old_http_post

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "approval_required")
        [blocked] = result["approval_blocked_tools"]
        self.assertEqual(blocked["tool_use_id"], "call_original")
        self.assertEqual(blocked["reasoning_content"], "надо посмотреть файлы")
        self.assertEqual(blocked["assistant_text"], "Осмотрюсь.")
        self.assertEqual(blocked["reason"], "Осмотрюсь.")
        self.assertNotEqual(blocked["reason"], blocked["reasoning_content"])

    def test_approval_payload_preserves_pending_batch_tail_for_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_http_post = cc_backend_executor._http_post
            target = str(Path(tmp) / "report.txt")

            def fake_http_post(*_args, **_kwargs):
                return (
                    {
                        "content": [
                            {"type": "text", "text": "Сначала файл, потом проверка."},
                            {
                                "type": "tool_use",
                                "id": "call_write",
                                "name": "Write",
                                "input": {"file_path": target, "content": "ok\n"},
                            },
                            {
                                "type": "tool_use",
                                "id": "call_bash",
                                "name": "Bash",
                                "input": {"command": "python3 -c 'print(123)'"},
                            },
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post = fake_http_post
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Создай файл и запусти проверку.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Write",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "file_path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["file_path", "content"],
                            },
                        },
                        {
                            "name": "Bash",
                            "input_schema": {
                                "type": "object",
                                "properties": {"command": {"type": "string"}},
                                "required": ["command"],
                            },
                        },
                    ],
                )
            finally:
                cc_backend_executor._http_post = old_http_post

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "approval_required")
        [blocked] = result["approval_blocked_tools"]
        self.assertEqual(blocked["tool"], "Write")
        self.assertEqual(blocked["tool_use_id"], "call_write")
        self.assertEqual(blocked["assistant_text"], "Сначала файл, потом проверка.")
        self.assertEqual(blocked["pending_tool_calls"][0]["id"], "call_bash")
        self.assertEqual(blocked["pending_tool_calls"][0]["name"], "Bash")

    def test_approval_payload_preserves_context_after_forced_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_streaming = cc_backend_executor._http_post_streaming_openai
            first_path = str(Path(tmp) / "report_builder.py")
            second_path = str(Path(tmp) / "sample_scores.json")

            def fake_streaming(_url, _payload, *_args, **_kwargs):
                return (
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion",
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call_second",
                                            "type": "function",
                                            "function": {
                                                "name": "Write",
                                                "arguments": json.dumps(
                                                    {
                                                        "file_path": second_path,
                                                        "content": "[]\n",
                                                    }
                                                ),
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            first_input = {"file_path": first_path, "content": "print('ok')\n"}
            cc_backend_executor._http_post_streaming_openai = fake_streaming
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Создай файлы.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Write",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "file_path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["file_path", "content"],
                            },
                        }
                    ],
                    metadata={
                        "forced_tool_call": {
                            "name": "Write",
                            "input": first_input,
                            "tool_use_id": "call_first",
                        },
                        "approved_tool_calls": [
                            _approval_signature("Write", first_input)
                        ],
                    },
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test"},
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "approval_required")
        [blocked] = result["approval_blocked_tools"]
        context = blocked["approval_context_messages"]
        self.assertEqual(context[0]["role"], "assistant")
        self.assertEqual(context[0]["content"][0]["id"], "call_first")
        self.assertEqual(context[1]["role"], "user")
        self.assertEqual(context[1]["content"][0]["tool_use_id"], "call_first")

    def test_reasoning_only_response_retries_instead_of_empty_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_http_post = cc_backend_executor._http_post
            calls = 0

            def fake_http_post(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return (
                        {
                            "content": [
                                {"type": "__reasoning", "text": "думаю, что делать дальше"}
                            ],
                            "usage": {},
                        },
                        1,
                    )
                return (
                    {
                        "content": [
                            {"type": "text", "text": "Продолжаю видимым ответом."}
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post = fake_http_post
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Продолжи задачу.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[],
                )
            finally:
                cc_backend_executor._http_post = old_http_post

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "Продолжаю видимым ответом.")
        self.assertEqual(calls, 2)

    def test_repeated_reasoning_only_response_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_http_post = cc_backend_executor._http_post

            def fake_http_post(*_args, **_kwargs):
                return (
                    {
                        "content": [
                            {"type": "__reasoning", "text": "только скрытое reasoning"}
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post = fake_http_post
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Продолжи задачу.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[],
                )
            finally:
                cc_backend_executor._http_post = old_http_post

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "reasoning_only_response_repeated")
        self.assertNotEqual(result["output_text"], "")

    def test_invalid_tool_input_returns_tool_result_instead_of_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_http_post = cc_backend_executor._http_post
            calls = 0

            def fake_http_post(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return (
                        {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "call_bad_edit",
                                    "name": "Edit",
                                    "input": {},
                                }
                            ],
                            "usage": {},
                        },
                        1,
                    )
                return (
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": "Понял ошибку и продолжил без approval.",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post = fake_http_post
            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Внеси правку.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Edit",
                            "input_schema": {
                                "type": "object",
                                "properties": {
                                    "file_path": {"type": "string"},
                                    "old_string": {"type": "string"},
                                    "new_string": {"type": "string"},
                                },
                                "required": [
                                    "file_path",
                                    "old_string",
                                    "new_string",
                                ],
                            },
                        }
                    ],
                )
            finally:
                cc_backend_executor._http_post = old_http_post

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "Понял ошибку и продолжил без approval.")
        self.assertEqual(calls, 2)

    def test_detects_ask_to_write_without_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "diagram.excalidraw")
            result = detect_ask_to_write_without_content(
                user_message=f"Создай файл {target}",
                assistant_text=f"Создать файл {target}? Подтверди.",
                workdir=tmp,
            )

        self.assertIsNotNone(result)
        self.assertIn("diagram.excalidraw", result)

    def test_does_not_flag_read_only_overview_that_mentions_relative_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = detect_ask_to_write_without_content(
                user_message=(
                    "Проведи обзор проекта без изменения файлов. "
                    "Найди pyproject.toml и определи текущую версию проекта."
                ),
                assistant_text=(
                    "## Обзор проекта\n\n"
                    "Версия `0.1.488` прописана в `pyproject.toml`."
                ),
                workdir=tmp,
            )

        self.assertIsNone(result)

    def test_does_not_link_path_to_question_mark_in_another_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = detect_ask_to_write_without_content(
                user_message=(
                    "Прочитай factory/core/post_turn_guards.py и объясни его. "
                    "Ничего не изменяй."
                ),
                assistant_text=(
                    "Прочитал `factory/core/post_turn_guards.py`. Файл не трогал.\n\n"
                    "Guard обрабатывает несколько случаев:\n"
                    "1. Модель спросила «записать?», но не приложила содержимое.\n"
                    "2. Модель вывела артефакт без Write."
                ),
                workdir=tmp,
            )

        self.assertIsNone(result)

    def test_detects_question_when_target_and_question_mark_share_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "report.py")
            result = detect_ask_to_write_without_content(
                user_message=f"Создай файл {target}",
                assistant_text=f"Файл подготовлен. Записать `{target}` — разрешаешь?",
                workdir=tmp,
            )

        self.assertEqual(result, target)

    def test_does_not_flag_when_json_artifact_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "diagram.excalidraw")
            payload = {"type": "excalidraw", "version": 2, "elements": []}
            result = detect_ask_to_write_without_content(
                user_message=f"Создай файл {target}",
                assistant_text=f"Вот файл:\n```json\n{json.dumps(payload)}\n```",
                workdir=tmp,
            )

        self.assertIsNone(result)

    def test_does_not_flag_when_path_not_in_assistant_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "diagram.excalidraw")
            result = detect_ask_to_write_without_content(
                user_message=f"Создай файл {target}",
                assistant_text="Привет, я не упоминаю этот путь.",
                workdir=tmp,
            )

        self.assertIsNone(result)

    def test_detects_truncated_excalidraw_json_in_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "diagram.excalidraw")
            # Simulates truncated output: JSON starts but never closes
            truncated = (
                f'Вот файл {target}:\n'
                '{{\n'
                '  "type": "excalidraw",\n'
                '  "version": 2,\n'
                '  "elements": [\n'
                '    {{"type": "rectangle"'
            )
            result = detect_file_content_in_text(
                user_message=f"Создай файл {target}",
                assistant_text=truncated,
                workdir=tmp,
            )

        self.assertIsNotNone(result)
        self.assertIn("diagram.excalidraw", result)

    def test_detects_complete_excalidraw_json_in_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "diagram.excalidraw")
            payload = {"type": "excalidraw", "version": 2, "elements": []}
            assistant = f"Вот содержимое {target}:\n```json\n{json.dumps(payload)}\n```"
            result = detect_file_content_in_text(
                user_message=f"Создай файл {target}",
                assistant_text=assistant,
                workdir=tmp,
            )

        self.assertIsNotNone(result)

    def test_does_not_flag_normal_text_without_file_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "diagram.excalidraw")
            result = detect_file_content_in_text(
                user_message=f"Создай файл {target}",
                assistant_text="Привет, я не вывожу JSON.",
                workdir=tmp,
            )

        self.assertIsNone(result)

    def test_detects_json_file_by_brace_density(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "data.json")
            # Lots of braces but no known type marker — should trigger brace fallback
            json_like = (
                "Содержимое " + target + ":\n"
                '{"a": {"b": {"c": 1}}, "d": {"e": {"f": {"g": 3}}}}'
            )
            result = detect_file_content_in_text(
                user_message="Создай файл " + target,
                assistant_text=json_like,
                workdir=tmp,
            )

        self.assertIsNotNone(result)

    # Relative paths section.

    def test_resolves_relative_filename_to_workdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = extract_writable_paths(
                "создай файл agent_lifecycle.excalidraw в корне проекта",
                workdir=tmp,
            )
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].endswith("agent_lifecycle.excalidraw"))
        self.assertTrue(os.path.isabs(paths[0]))

    def test_resolves_relative_path_with_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = extract_writable_paths(
                "запиши в src/output.json",
                workdir=tmp,
            )
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].endswith("src/output.json"))

    def test_does_not_confuse_version_numbers_with_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = extract_writable_paths(
                "обнови версию до 0.1.345 в pyproject.toml",
                workdir=tmp,
            )
        # `0.1.345` should not be a path (starts with a number)
        # However, `pyproject.toml` is an exception.
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].endswith("pyproject.toml"))

    def test_detect_file_content_with_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {"type": "excalidraw", "version": 2, "elements": []}
            assistant = (
                "Вот содержимое agent_lifecycle.excalidraw:\n"
                "```json\n" + json.dumps(payload) + "\n```"
            )
            result = detect_file_content_in_text(
                user_message="создай файл agent_lifecycle.excalidraw в корне проекта",
                assistant_text=assistant,
                workdir=tmp,
            )
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("agent_lifecycle.excalidraw"))

    def test_detect_ask_to_write_with_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = detect_ask_to_write_without_content(
                user_message="создай файл agent_lifecycle.excalidraw в корне проекта",
                assistant_text="Записать agent_lifecycle.excalidraw? Подтверди.",
                workdir=tmp,
            )
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("agent_lifecycle.excalidraw"))

    def test_detect_pending_write_with_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {"type": "excalidraw", "version": 2, "elements": []}
            action = detect_pending_write_action(
                user_message="создай файл agent_lifecycle.excalidraw в корне проекта",
                assistant_text="```json\n" + json.dumps(payload) + "\n```",
                workdir=tmp,
            )
        self.assertIsNotNone(action)
        self.assertTrue(action.file_path.endswith("agent_lifecycle.excalidraw"))


    # ── max_tokens auto-scale ────────────────────────────────────────────

    def test_auto_scale_default_4000_large_context_window_25000(self):
        self.assertEqual(
            _auto_scale_max_tokens(4000, 200_000),
            25000,
        )

    def test_auto_scale_default_4000_huge_context_window_capped_at_32k(self):
        self.assertEqual(
            _auto_scale_max_tokens(4000, 1_000_000),
            32000,
        )

    def test_auto_scale_default_4000_small_context_stays_4000(self):
        self.assertEqual(
            _auto_scale_max_tokens(4000, 32_000),
            4000,
        )

    def test_auto_scale_default_4000_very_small_context_stays_4000(self):
        self.assertEqual(
            _auto_scale_max_tokens(4000, 16_000),
            4000,
        )

    def test_auto_scale_explicit_max_tokens_8k_not_overridden(self):
        self.assertEqual(
            _auto_scale_max_tokens(8000, 200_000),
            8000,
        )

    def test_auto_scale_explicit_max_tokens_16k_not_overridden(self):
        self.assertEqual(
            _auto_scale_max_tokens(16000, 1_000_000),
            16000,
        )

    def test_auto_scale_128k_context_gives_16k(self):
        self.assertEqual(
            _auto_scale_max_tokens(4000, 128_000),
            16000,
        )

    def test_auto_scale_64k_context_gives_8k(self):
        self.assertEqual(
            _auto_scale_max_tokens(4000, 64_000),
            8000,
        )

    def test_auto_scale_33k_context_stays_4000_below_threshold(self):
        self.assertEqual(
            _auto_scale_max_tokens(4000, 33_000),
            4125,
        )

    def test_auto_scale_32k_boundary_stays_4000(self):
        self.assertEqual(
            _auto_scale_max_tokens(4000, 32_000),
            4000,
        )

    def test_auto_scale_subdefault_2000_large_context_scales_up(self):
        self.assertEqual(
            _auto_scale_max_tokens(2000, 200_000),
            25000,
        )


if __name__ == "__main__":
    unittest.main()
