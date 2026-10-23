import json
import re

import pytest

from factory.core import cc_backend_executor as executor
from factory.core.tool_registry import registry


def strings(value):
    if isinstance(value, str):
        yield value
        try:
            parsed = json.loads(value)
        except ValueError:
            return
        if not isinstance(parsed, str):
            yield from strings(parsed)
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


@pytest.mark.parametrize("api_format", ["anthropic", "openai", "openai-responses"])
def test_real_executor_can_read_its_saved_output_without_another_archive(tmp_path, monkeypatch, api_format):
    work, artifacts = tmp_path / "project", tmp_path / "runtime"
    work.mkdir()
    content = "fixture line\n" * 2000
    requests = []
    original = executor.execute_tool

    def tool(name, args, *positional, **context):
        if name == "Bash":
            return content
        return original(name, args, *positional, **context)

    def model(url, payload, headers, *args, **kwargs):
        requests.append(payload)
        call = len(requests)
        if call == 1:
            name, arguments = "Bash", {"command": "fixture-output"}
        elif call == 2:
            output = next(text for text in strings(payload) if text.startswith("<persisted-output>"))
            path = re.search(r"^Full output saved to: (.+)$", output, re.M).group(1)
            name, arguments = "Read", {"file_path": path, "limit": 2}
        else:
            assert "fixture line\nfixture line\n" in list(strings(payload))
            assert '"bounded"' not in json.dumps(payload)
            assert "tool_result_access" not in json.dumps(payload)
            name = None
        if api_format == "openai":
            message = {"role": "assistant", "content": "Verified saved output."}
            if name:
                message = {"role": "assistant", "content": None, "tool_calls": [{"id": f"call-{call}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)}}]}
            return {"choices": [{"message": message, "finish_reason": "tool_calls" if name else "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, 1
        blocks = [{"type": "tool_use", "id": f"call-{call}", "name": name, "input": arguments}] if name else [
            {"type": "text", "text": "Verified saved output."}]
        return {"content": blocks, "stop_reason": "tool_use" if name else "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1}}, 1

    monkeypatch.setattr(executor, "execute_tool", tool)
    monkeypatch.setattr(executor, "_http_post_streaming_openai", model)
    monkeypatch.setattr(executor, "_http_post", model)
    monkeypatch.setattr(executor, "_resolve_backend_context_window", lambda **_: (65536, "fixture"))
    monkeypatch.setattr(executor, "is_interrupt_requested", lambda: False)
    monkeypatch.setattr(executor, "task_should_stop", lambda _: False)
    result = executor.execute_cc_backend_task(base_url="http://127.0.0.1:1/v1", model="fixture",
        system_prompt="Use the supplied tools.", user_message="Read the generated tool output.", workdir=str(work),
        artifact_workdir=str(artifacts), task_id="fixture-task", current_session="fixture-session",
        data_root=str(tmp_path / "durable"),
        api_format=api_format, enabled_tools=[registry.get_schema("Bash"), registry.get_schema("Read")],
        checkpoint_config={"enabled": False})
    assert result["status"] == "ok"
    assert len(requests) == 3
    assert len(list((artifacts / ".tool_results").glob("*.txt"))) == 1
    assert (tmp_path / "durable/database/factory.db").is_file()


def test_resumed_approval_batch_shares_the_read_budget(tmp_path, monkeypatch):
    target = tmp_path / "large.txt"
    target.write_text("q" * 20000)
    seen = []

    def model(url, payload, headers, *args, **kwargs):
        pages = [value for value in strings(payload) if value.startswith("q" * 64)]
        assert len(pages) == 3
        assert sum(map(len, pages)) <= 40000
        seen.extend(pages)
        return {"content": [{"type": "text", "text": "Read the three pages."}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1}}, 1

    monkeypatch.setattr(executor, "_http_post", model)
    monkeypatch.setattr(executor, "_resolve_backend_context_window", lambda **_: (65536, "fixture"))
    monkeypatch.setattr(executor, "is_interrupt_requested", lambda: False)
    monkeypatch.setattr(executor, "task_should_stop", lambda _: False)
    forced = {"name": "Read", "input": {"file_path": str(target)}, "tool_use_id": "forced-read",
              "pending_tool_calls": [{"name": "Read", "id": "pending-" + str(index), "input": {"file_path": str(target)}} for index in range(2)]}
    result = executor.execute_cc_backend_task(base_url="http://127.0.0.1:1", model="fixture",
        system_prompt="Use file tools.", user_message="Continue the prepared reads.", workdir=str(tmp_path),
        artifact_workdir=str(tmp_path / "runtime"), api_format="anthropic", metadata={"forced_tool_call": forced},
        enabled_tools=[registry.get_schema("Read")], checkpoint_config={"enabled": False})
    assert result["status"] == "ok"
    assert len(seen) == 3
