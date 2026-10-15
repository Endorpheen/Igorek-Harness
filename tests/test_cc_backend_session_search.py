from __future__ import annotations

from typing import Any

from factory.core.cc_backend_tools import execute_tool


def test_session_search_tool_uses_runtime_provider() -> None:
    calls: list[dict[str, Any]] = []

    def provider(args: dict[str, Any]) -> str:
        calls.append(args)
        return '{"success":true,"count":1,"semantic_available":true}'

    result = execute_tool(
        "SessionSearch",
        {"query": "remembered detail", "mode": "semantic", "limit": 2},
        "/workspace",
        session_search_provider=provider,
    )

    assert calls == [
        {"query": "remembered detail", "mode": "semantic", "limit": 2}
    ]
    assert result == '{"success":true,"count":1,"semantic_available":true}'



def test_memory_forget_tool_is_registered_and_calls_runtime_provider() -> None:
    from factory.core.cc_backend_tools import get_tool_schemas

    schemas = get_tool_schemas()
    assert any(item.get("name") == "memory_forget" for item in schemas)
    calls: list[dict[str, Any]] = []

    def provider(args: dict[str, Any]) -> str:
        calls.append(args)
        return '{"changed":true,"action":"forget"}'

    result = execute_tool(
        "memory_forget",
        {"target": "Synthetic saved fact — 42"},
        "/workspace",
        memory_forget_provider=provider,
    )
    assert calls == [{"target": "Synthetic saved fact — 42"}]
    assert result == '{"changed":true,"action":"forget"}'


def test_memory_forget_requires_owner_confirmation_not_message_match() -> None:
    import json
    from unittest.mock import patch

    target = "Synthetic saved fact — 42"
    with patch("factory.core.memory_policy.edit_saved_memory_fact") as edit:
        denied = execute_tool(
            "memory_forget", {"target": target}, "/workspace",
            user_message=f"Delete {target}",
            user_input_provider=lambda _: {"answers": {"memory_forget_decision": {"selected_options": ["Сохранить факт"]}}},
        )
        assert json.loads(denied)["reason"] == "owner_declined"
        edit.assert_not_called()
        edit.return_value = {"changed": True, "rejected": False, "action": "forget", "target": target}
        allowed = execute_tool(
            "memory_forget", {"target": target}, "/workspace",
            user_message="Explain this saved fact",
            user_input_provider=lambda _: {"answers": {"memory_forget_decision": {"selected_options": ["Удалить факт"]}}},
        )
    assert json.loads(allowed)["changed"] is True
    edit.assert_called_once_with(target)
