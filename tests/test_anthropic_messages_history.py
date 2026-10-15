"""Unit tests for anthropic_messages history preservation.

Tests that TOOL_RESULT mode preserves full conversation history
instead of stripping to 2 messages.
"""

import unittest
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _FakeMessage:
    role: str
    content: str | list[dict[str, Any]]


@dataclass
class _FakePromptMode:
    NORMAL = "normal"
    TOOL_RESULT = "tool_result"
    SUGGESTION = "suggestion"
    STARTUP_REMINDER = "startup_reminder"


@dataclass
class _FakeReq:
    messages: list[_FakeMessage]
    system: str | list[dict[str, Any]] = ""
    tools: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers to build realistic multi-turn conversations using plain dicts
# ---------------------------------------------------------------------------


def _make_tool_result_only_user(tool_use_id: str, result_text: str) -> _FakeMessage:
    """Build a user message containing only a tool_result block."""
    return _FakeMessage(
        role="user",
        content=[
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result_text,
            },
        ],
    )


def _make_assistant_tool_use(tool_use_id: str, tool_name: str, tool_input: dict) -> _FakeMessage:
    """Build an assistant message containing a tool_use block."""
    return _FakeMessage(
        role="assistant",
        content=[
            {
                "type": "tool_use",
                "id": tool_use_id,
                "name": tool_name,
                "input": tool_input,
            },
        ],
    )


def _make_user_text(text: str) -> _FakeMessage:
    """Build a user message with plain text."""
    return _FakeMessage(role="user", content=text)


def _make_assistant_text(text: str) -> _FakeMessage:
    """Build an assistant message with plain text."""
    return _FakeMessage(role="assistant", content=text)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolResultHistoryPreservation(unittest.TestCase):
    """Verify that TOOL_RESULT mode preserves full conversation history."""

    def setUp(self) -> None:
        from factory.api_server_core.anthropic_messages import extract_anthropic_messages
        self.extract = extract_anthropic_messages
        self.pm = _FakePromptMode()

    def test_single_tool_result_preserves_prior_user_text(self):
        """After one tool round, the original user text must still be present."""
        req = _FakeReq(messages=[
            _make_user_text("Read file X and fix the bug"),
            _make_assistant_tool_use("tu_1", "Read", {"file_path": "/tmp/X.py"}),
            _make_tool_result_only_user("tu_1", "contents of X.py here"),
        ])
        result = self.extract(
            req,
            self.pm.TOOL_RESULT,
            prompt_mode=self.pm,
            tools_prompt="",
        )
        # Must have more than 2 messages (system + 3 conversation messages)
        self.assertGreater(len(result), 2,
                           "TOOL_RESULT should preserve full history, not strip to 2")
        # The original user text must appear somewhere in the messages
        all_text = " ".join(m["content"] for m in result)
        self.assertIn("Read file X and fix the bug", all_text,
                       "Original user request must be preserved in history")

    def test_multi_tool_round_preserves_all(self):
        """After 3 tool rounds, all prior context must be present."""
        req = _FakeReq(messages=[
            _make_user_text("Analyze all Python files in the project"),
            _make_assistant_tool_use("tu_1", "Glob", {"pattern": "**/*.py"}),
            _make_tool_result_only_user("tu_1", "file1.py\nfile2.py\nfile3.py"),
            _make_assistant_tool_use("tu_2", "Read", {"file_path": "file1.py"}),
            _make_tool_result_only_user("tu_2", "# file1 contents\ndef foo(): pass"),
            _make_assistant_tool_use("tu_3", "Read", {"file_path": "file2.py"}),
            _make_tool_result_only_user("tu_3", "# file2 contents\ndef bar(): pass"),
        ])
        result = self.extract(
            req,
            self.pm.TOOL_RESULT,
            prompt_mode=self.pm,
            tools_prompt="",
        )
        all_text = " ".join(m["content"] for m in result)
        # All prior exchanges should be visible
        self.assertIn("Analyze all Python files", all_text)
        self.assertIn("file1.py", all_text)
        self.assertIn("file2.py", all_text)
        # System message present
        self.assertEqual(result[0]["role"], "system")

    def test_system_prompt_includes_tool_result_system_prompt(self):
        """System message must contain tool_result_system_prompt content."""
        req = _FakeReq(messages=[
            _make_user_text("do something"),
            _make_assistant_tool_use("tu_1", "Bash", {"command": "ls"}),
            _make_tool_result_only_user("tu_1", "file.txt"),
        ])
        result = self.extract(
            req,
            self.pm.TOOL_RESULT,
            prompt_mode=self.pm,
            tools_prompt="",
        )
        system_msg = result[0]
        self.assertEqual(system_msg["role"], "system")
        # Should contain tool result guidance (substantial text)
        self.assertTrue(len(system_msg["content"]) > 50,
                        "System prompt should contain substantial tool result guidance")

    def test_tools_prompt_appended_to_system(self):
        """When tools_prompt is provided, it must be appended to the system message."""
        req = _FakeReq(messages=[
            _make_user_text("do something"),
            _make_assistant_tool_use("tu_1", "Bash", {"command": "ls"}),
            _make_tool_result_only_user("tu_1", "file.txt"),
        ])
        result = self.extract(
            req,
            self.pm.TOOL_RESULT,
            prompt_mode=self.pm,
            tools_prompt="Available tools: Read, Write, Bash",
        )
        system_msg = result[0]
        self.assertIn("Available tools: Read, Write, Bash", system_msg["content"])

    def test_preserves_assistant_text_turns(self):
        """Assistant text turns (not just tool_use) must also be preserved."""
        req = _FakeReq(messages=[
            _make_user_text("What is 2+2?"),
            _make_assistant_text("The answer is 4."),
            _make_user_text("Now read config.yaml"),
            _make_assistant_tool_use("tu_1", "Read", {"file_path": "config.yaml"}),
            _make_tool_result_only_user("tu_1", "key: value"),
        ])
        result = self.extract(
            req,
            self.pm.TOOL_RESULT,
            prompt_mode=self.pm,
            tools_prompt="",
        )
        all_text = " ".join(m["content"] for m in result)
        self.assertIn("What is 2+2", all_text, "First user message must be preserved")
        self.assertIn("answer is 4", all_text, "First assistant text must be preserved")
        self.assertIn("config.yaml", all_text, "Second user message must be preserved")

    def test_no_messages_empty_result(self):
        """With empty messages, NORMAL mode should still return at least system message."""
        req = _FakeReq(messages=[])
        result = self.extract(
            req,
            self.pm.NORMAL,
            prompt_mode=self.pm,
            tools_prompt="",
        )
        # NORMAL mode with empty messages: system + possibly empty
        self.assertTrue(len(result) >= 1, "Should return at least system message")


class TestNormalModeUnchanged(unittest.TestCase):
    """Verify NORMAL mode behavior is not affected by TOOL_RESULT fix."""

    def setUp(self) -> None:
        from factory.api_server_core.anthropic_messages import extract_anthropic_messages
        self.extract = extract_anthropic_messages
        self.pm = _FakePromptMode()

    def test_normal_mode_full_history(self):
        """NORMAL mode must preserve all messages as before."""
        req = _FakeReq(messages=[
            _make_user_text("Hello"),
            _make_assistant_text("Hi there!"),
            _make_user_text("How are you?"),
        ])
        result = self.extract(
            req,
            self.pm.NORMAL,
            prompt_mode=self.pm,
            tools_prompt="",
        )
        # system + 3 conversation messages
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["role"], "system")
        self.assertIn("Hello", result[1]["content"])
        self.assertIn("Hi there", result[2]["content"])
        self.assertIn("How are you", result[3]["content"])

    def test_normal_mode_with_tool_exchanges(self):
        """NORMAL mode must preserve tool exchanges too."""
        req = _FakeReq(messages=[
            _make_user_text("Read file.py"),
            _make_assistant_tool_use("tu_1", "Read", {"file_path": "file.py"}),
            _make_tool_result_only_user("tu_1", "file contents here"),
            _make_user_text("Now edit it"),
        ])
        result = self.extract(
            req,
            self.pm.NORMAL,
            prompt_mode=self.pm,
            tools_prompt="",
        )
        all_text = " ".join(m["content"] for m in result)
        self.assertIn("Read file.py", all_text)
        self.assertIn("Now edit it", all_text)


if __name__ == "__main__":
    unittest.main()
