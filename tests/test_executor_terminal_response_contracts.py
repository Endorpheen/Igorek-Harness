"""Final-answer adapters preserve evidence, counters and safe failure states."""

import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import cc_backend_executor as e


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    post = Mock(
        return_value=(
            {
                "content": [{"type": "text", "text": "Fixture answer"}],
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
            5,
        )
    )
    monkeypatch.setattr(e, "_http_post", post)
    return SimpleNamespace(root=tmp_path, post=post, logs=Mock())


def invoke(env, kind="synthesis", *, api="anthropic"):
    kwargs = dict(
        url="http://127.0.0.1:1/v1/"
        + (
            "responses"
            if api == "responses"
            else "chat/completions"
            if api == "openai"
            else "messages"
        ),
        model="fixture-model",
        system_prompt="fixture system",
        headers={"x-fixture": "value"},
        timeout_s=3,
        workdir=str(env.root),
        log_fn=env.logs,
    )
    if kind == "synthesis":
        return e._make_synthesis_call(
            **kwargs,
            messages=[{"role": "user", "content": "fixture question"}],
            max_tokens=99,
            total_input_tokens=7,
            total_output_tokens=11,
            start_time=time.time(),
            tool_calls_count=4,
            is_openai_api=api != "anthropic",
        )
    if kind == "write":
        return e._make_forced_write_postamble(
            **kwargs, file_path="fixture.py", raw_result="fixture saved"
        )
    return e._make_terminal_fal_response_call(
        **kwargs,
        tool_name="fal_edit_image" if kind == "edit_image" else "fal_generate_image",
        fallback_text="Fixture artifact ready",
    )


@pytest.mark.parametrize("api", ["anthropic", "openai", "responses"])
def test_synthesis_uses_no_tools_and_preserves_protocol_contract(terminal, api):
    if api == "openai":
        terminal.post.return_value = (
            {
                "choices": [
                    {"message": {"content": "Fixture answer"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            },
            5,
        )
    result = invoke(terminal, api=api)
    assert result["status"] == "ok" and result["output_text"] == "Fixture answer"
    assert (
        result["usage"]["prompt"] == 9
        and result["usage"]["completion"] == 14
        and result["tool_calls_count"] == 4
    )
    args, options = terminal.post.call_args
    payload = args[1]
    assert (
        "tools" not in payload
        and "tool_choice" not in payload
        and payload["max_tokens"] == 99
    )
    assert payload["messages"][-1]["role"] == "user"
    if api == "openai":
        assert (
            "system" not in payload
            and payload["messages"][0]
            == {"role": "system", "content": "fixture system"}
            and payload["stream"] is False
        )
    else:
        assert payload["system"] == "fixture system"
    assert options["use_curl"] is (api != "anthropic")
    rows = [
        json.loads(line)
        for line in (terminal.root / "cc_backend_trace.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["kind"] == "task.finish" and rows[-1]["status"] == "ok"


@pytest.mark.parametrize("thinking", [False, True])
def test_synthesis_keeps_original_history_unchanged(terminal, thinking):
    messages = [
        {"role": "user", "content": "fixture request"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "fixture",
                    "name": "Read",
                    "input": {"file_path": "fixture.py"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "fixture",
                    "content": "branch: exact-FIXTURE",
                }
            ],
        },
    ]
    if thinking:
        messages[1]["content"].insert(
            0, {"type": "__reasoning", "text": "fixture thought"}
        )
    terminal.post.return_value = (
        {
            "choices": [
                {"message": {"content": "Fixture answer"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        },
        1,
    )
    original = copy.deepcopy(messages)
    result = e._make_synthesis_call(
        "http://127.0.0.1:1/v1/chat/completions",
        "fixture-model",
        "fixture system",
        messages,
        {},
        3,
        100,
        str(terminal.root),
        terminal.logs,
        1,
        2,
        time.time(),
        1,
        is_openai_api=True,
    )
    assert result["status"] == "ok" and result["output_text"] == "Fixture answer"
    assert messages == original
    payload = terminal.post.call_args.args[1]
    assert "exact-FIXTURE" in json.dumps(payload)
    assistant = next(row for row in payload["messages"] if row["role"] == "assistant")
    assert ("reasoning_content" in assistant) is thinking
    assert assistant["tool_calls"][0]["id"] == "fixture"


@pytest.mark.parametrize("kind", ["write", "image", "edit_image"])
def test_successful_artifact_postamble_retains_text_and_usage(terminal, kind):
    result = invoke(terminal, kind)
    assert result == ("Fixture answer", 2, 3)
    payload = terminal.post.call_args.args[1]
    assert "tools" not in payload and "tool_choice" not in payload
    assert payload["max_tokens"] == (200 if kind == "write" else 160)
    if kind == "edit_image":
        assert "отредактировано" in payload["messages"][0]["content"]


@pytest.mark.parametrize("kind", ["synthesis", "write", "image"])
@pytest.mark.parametrize(
    "case", ["nonobject", "no_usage", "no_content", "invalid_text", "invalid_usage"]
)
def test_malformed_provider_body_is_bounded_without_uncaught_exception(
    terminal, kind, case
):
    valid = {
        "content": [{"type": "text", "text": "  Ответ 🐙  "}],
        "usage": {"input_tokens": 2, "output_tokens": 3},
    }
    if case == "nonobject":
        body = None
    elif case == "no_usage":
        body = {**valid, "usage": None}
    elif case == "no_content":
        body = {**valid, "content": None}
    elif case == "invalid_text":
        body = {
            **valid,
            "content": [
                None,
                7,
                {"type": "text", "text": 7},
                {"type": "text", "text": None},
            ],
        }
    else:
        body = {**valid, "usage": {"input_tokens": "bad", "output_tokens": True}}
    terminal.post.return_value = (body, 1)
    result = invoke(terminal, kind)
    good_text = case in {"no_usage", "invalid_usage"}
    expected_usage = (
        (0, 0) if case in {"nonobject", "no_usage", "invalid_usage"} else (2, 3)
    )
    if kind == "synthesis":
        assert result["status"] == ("ok" if good_text else "fail")
        if good_text:
            assert result["output_text"] == "Ответ 🐙"
        assert result["usage"]["prompt"] == 7 + expected_usage[0]
        assert result["usage"]["completion"] == 11 + expected_usage[1]
        assert (
            type(result["usage"]["prompt"]) is int
            and type(result["usage"]["completion"]) is int
        )
        if case in {"nonobject", "no_content"}:
            assert result["error_code"] == "PROTOCOL.INVALID_RESPONSE"
    else:
        fallback = (
            "Файл сохранён: fixture.py" if kind == "write" else "Fixture artifact ready"
        )
        assert result[0] == ("Ответ 🐙" if good_text else fallback)
        assert result[1:] == expected_usage and all(type(v) is int for v in result[1:])


@pytest.mark.parametrize("kind", ["synthesis", "write", "image"])
def test_provider_error_is_safe_and_preserves_already_recorded_usage(terminal, kind):
    terminal.post.return_value = ({"error": "token=synthetic-terminal-secret"}, 1)
    result = invoke(terminal, kind)
    assert "synthetic-terminal-secret" not in json.dumps(result, ensure_ascii=False)
    assert "synthetic-terminal-secret" not in str(terminal.logs.call_args_list)
    if kind == "synthesis":
        assert (
            result["status"] == "fail"
            and result["usage"]["prompt"] == 7
            and result["usage"]["completion"] == 11
        )
    else:
        assert result[1:] == (0, 0)


@pytest.mark.parametrize("kind", ["write", "image"])
def test_tool_artifact_in_postamble_is_replaced_by_known_success_message(
    terminal, kind
):
    terminal.post.return_value = (
        {
            "content": [{"type": "text", "text": "<tool_call>Read</tool_call>"}],
            "usage": {"input_tokens": 2, "output_tokens": 3},
        },
        1,
    )
    result = invoke(terminal, kind)
    assert result[0] == (
        "Файл сохранён: fixture.py" if kind == "write" else "Fixture artifact ready"
    )
    assert result[1:] == (2, 3)


def test_empty_synthesis_is_not_reported_as_success(terminal):
    terminal.post.return_value = (
        {"content": [], "usage": {"input_tokens": 2, "output_tokens": 3}},
        1,
    )
    result = invoke(terminal)
    assert (
        result["status"] == "fail"
        and result["failure_reason"] == "synthesis_empty_after_stuck"
    )
    assert result["usage"]["prompt"] == 9 and result["usage"]["completion"] == 14
