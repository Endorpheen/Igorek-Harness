import unittest

from factory.core.cc_backend_executor import (
    _build_initial_messages,
    _telegram_runtime_suffix,
    _tool_denied_by_policy,
)
from factory.core.prompt_assembly import build_cc_backend_system_prompt
from factory.core.worker import _stable_prompt_prefix


class StablePromptPrefixFlagTest(unittest.TestCase):
    def test_explicit_flag_wins(self):
        self.assertTrue(_stable_prompt_prefix({"stable_prompt_prefix": True}))
        self.assertFalse(_stable_prompt_prefix({"stable_prompt_prefix": False}))
        # Explicit false must win even for a local endpoint.
        self.assertFalse(
            _stable_prompt_prefix(
                {
                    "stable_prompt_prefix": False,
                    "provider": "local",
                    "base_url": "http://127.0.0.1:8081/v1",
                }
            )
        )

    def test_auto_enabled_for_local_provider(self):
        self.assertTrue(_stable_prompt_prefix({"provider": "local"}))

    def test_auto_enabled_for_localhost_base_url(self):
        for url in (
            "http://127.0.0.1:8081/v1",
            "http://localhost:8001",
            "http://[::1]:8001/v1",
        ):
            self.assertTrue(_stable_prompt_prefix({"base_url": url}), url)

    def test_disabled_for_remote_endpoints(self):
        self.assertFalse(_stable_prompt_prefix({}))
        self.assertFalse(
            _stable_prompt_prefix(
                {
                    "provider": "glm",
                    "base_url": "https://api.z.ai/api/anthropic",
                }
            )
        )


class ToolPolicyTest(unittest.TestCase):
    def test_restrict_tools_to_allows_only_listed(self):
        metadata = {"restrict_tools_to": ["Read", "Grep", "Glob"]}
        self.assertFalse(_tool_denied_by_policy("Read", metadata))
        self.assertTrue(_tool_denied_by_policy("Bash", metadata))
        self.assertTrue(_tool_denied_by_policy("Write", metadata))

    def test_plan_mode_restriction_denies_mutating_tools(self):
        metadata = {"restrict_tools_to": ["Read", "Grep", "Glob", "update_plan"]}
        for allowed in ("Read", "Grep", "Glob", "update_plan"):
            self.assertFalse(_tool_denied_by_policy(allowed, metadata), allowed)
        for denied in ("Write", "Edit", "MultiEdit", "Bash"):
            self.assertTrue(_tool_denied_by_policy(denied, metadata), denied)

    def test_block_tool_names_denies_listed(self):
        metadata = {"block_tool_names": ["delegate_task"]}
        self.assertTrue(_tool_denied_by_policy("delegate_task", metadata))
        self.assertFalse(_tool_denied_by_policy("Read", metadata))

    def test_empty_metadata_allows_everything(self):
        self.assertFalse(_tool_denied_by_policy("Bash", {}))
        self.assertFalse(_tool_denied_by_policy("delegate_task", {}))


class InitialMessagesTest(unittest.TestCase):
    def test_history_then_user_with_trailing_context(self):
        messages = _build_initial_messages(
            history_messages=[
                {"role": "user", "content": "вопрос 1"},
                {"role": "assistant", "content": "ответ 1"},
            ],
            trailing_context_text="foundation + skills",
            user_message="вопрос 2",
            input_files=None,
        )
        roles = [m["role"] for m in messages]
        # No mid-conversation system message: Qwen/llama-server rejects it.
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "ответ 1")
        self.assertIn("foundation + skills", str(messages[2]["content"]))
        self.assertIn("вопрос 2", str(messages[2]["content"]))

    def test_history_entries_are_filtered(self):
        messages = _build_initial_messages(
            history_messages=[
                {"role": "user", "content": "q0"},
                {"role": "user", "content": "  "},
                {"role": "tool", "content": "ignored"},
                "not-a-dict",
                {"role": "assistant", "content": "ok"},
            ],
            trailing_context_text="",
            user_message="hi",
            input_files=None,
        )
        roles = [m["role"] for m in messages]
        self.assertEqual(roles, ["user", "assistant", "user"])

    def test_leading_assistant_history_is_dropped(self):
        messages = _build_initial_messages(
            history_messages=[
                {"role": "assistant", "content": "bootstrap intro"},
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
            ],
            trailing_context_text="",
            user_message="q2",
            input_files=None,
        )
        self.assertEqual(
            [m["role"] for m in messages], ["user", "assistant", "user"]
        )

    def test_no_history_keeps_single_user_message(self):
        messages = _build_initial_messages(
            history_messages=None,
            trailing_context_text="",
            user_message="hi",
            input_files=None,
        )
        self.assertEqual([m["role"] for m in messages], ["user"])


class TelegramRuntimeSuffixTest(unittest.TestCase):
    def test_suffix_requires_pipe_tables_and_keeps_no_think(self):
        suffix = _telegram_runtime_suffix()
        # Telegram Bot API 10.1+ renders native rich tables, so the model must
        # emit GFM pipe tables instead of ASCII/monospace grids.
        self.assertIn("GFM markdown pipe table", suffix)
        self.assertIn("|---|---|", suffix)
        self.assertIn("/no_think", suffix)

    def test_suffix_explicitly_rejects_no_table_claim(self):
        self.assertIn("Never claim Telegram cannot render tables", _telegram_runtime_suffix())


class StaticSystemPromptTest(unittest.TestCase):
    def _static_prompt(self):
        return build_cc_backend_system_prompt(
            workdir="/tmp/proj",
            filesystem_mode="workspace",
            model_name="qwen3.8-flash-next",
            foundation_context="",
            skills_text="",
            wrapped_task_context="",
            web_tools_enabled=False,
            mcp_tool_names=None,
            disabled_mcp_capabilities=None,
            interactive_input_enabled=True,
            prompt_style="full",
        )

    def test_dynamic_sections_absent_when_emptied(self):
        prompt = self._static_prompt()
        self.assertNotIn("## Foundation Memory", prompt)
        self.assertNotIn("ВЫБРАННЫЕ SKILLS ДЛЯ ЭТОЙ ЗАДАЧИ", prompt)
        self.assertNotIn("История текущей сессии", prompt)

    def test_prompt_is_identical_regardless_of_message(self):
        # The static builder takes no message input; assert repeated builds are
        # byte-identical so the KV-cache prefix cannot drift on normal turns.
        self.assertEqual(self._static_prompt(), self._static_prompt())


if __name__ == "__main__":
    unittest.main()
