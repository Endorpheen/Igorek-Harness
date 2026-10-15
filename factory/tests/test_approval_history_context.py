from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli_chat_rich import (
    _approval_context_messages_from_blocked,
    _approval_text_for_history,
    _merge_approval_context_messages,
)
from core.chat_session import (
    _approval_context_for_resume,
    _forced_tool_call_for_resume,
)


def test_approval_context_messages_from_blocked_keeps_structured_messages():
    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "/repo",
                }
            ],
        },
    ]

    assert _approval_context_messages_from_blocked(
        [
            {"approval_context_messages": messages},
            {"approval_context_messages": [{"role": "system", "content": []}]},
            {"approval_context_messages": [{"role": "assistant", "content": "bad"}]},
        ]
    ) == messages


def test_merge_approval_context_messages_deduplicates_without_dropping_order():
    first = {"role": "assistant", "content": [{"type": "text", "text": "A"}]}
    second = {"role": "user", "content": [{"type": "text", "text": "B"}]}

    assert _merge_approval_context_messages([first], [first, second]) == [
        first,
        second,
    ]


def test_approval_text_for_history_prefers_structural_assistant_text():
    blocked = [
        {
            "reason": "Для выполнения этого действия требуется подтверждение.",
            "assistant_text": "Current assistant answer before approval.",
        }
    ]

    assert (
        _approval_text_for_history(
            {"output_text": "Для выполнения этого действия требуется подтверждение."},
            blocked,
        )
        == "Current assistant answer before approval."
    )


def test_approval_context_for_resume_strips_tool_preamble_text():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will inspect the vault first."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "/repo",
                }
            ],
        },
    ]

    assert _approval_context_for_resume(messages) == [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "/repo",
                }
            ],
        },
    ]


def test_forced_tool_call_for_resume_drops_assistant_text_but_keeps_reason():
    blocked = {
        "tool": "Bash",
        "input": {"command": "pwd"},
        "source": "tool_call",
        "tool_use_id": "call_1",
        "assistant_text": "I will inspect the vault first.",
        "reason": "I will inspect the vault first.",
    }

    ftc = _forced_tool_call_for_resume(blocked, [])

    assert ftc == {
        "name": "Bash",
        "input": {"command": "pwd"},
        "source": "tool_call",
        "tool_use_id": "call_1",
        "reason": "I will inspect the vault first.",
    }
