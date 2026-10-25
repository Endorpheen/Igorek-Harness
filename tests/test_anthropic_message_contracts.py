import json
from types import SimpleNamespace as NS

import pytest

from factory.api_server_core import anthropic_messages as api


class Modes:
    NORMAL = "normal"
    TOOL_RESULT = "tool_result"
    SUGGESTION = "suggestion"
    STARTUP_REMINDER = "startup_reminder"


def message(role, content):
    return NS(role=role, content=content)


def request(messages=(), **kwargs):
    data = dict(messages=list(messages), system=None, tools=[], model_extra=None,
                max_tokens=123, temperature=.4, top_p=.9, stream=True)
    data.update(kwargs)
    return NS(**data)


def call(identity, name, arguments):
    return {"type": "tool_use", "id": identity, "name": name, "input": arguments}


def result(identity, text, **kwargs):
    return {"type": "tool_result", "tool_use_id": identity, "content": text, **kwargs}


@pytest.mark.parametrize("messages, expected", [
    ([], Modes.NORMAL),
    ([message("user", "Hello")], Modes.NORMAL),
    ([message("user", [{"type": "text", "text": "<system-reminder>hidden</system-reminder>"}])], Modes.STARTUP_REMINDER),
    ([message("assistant", "Earlier answer"), message("user", [{"type": "text", "text": "<system-reminder>hidden</system-reminder>"}])], Modes.NORMAL),
    ([message("user", [result("a", "content")])], Modes.TOOL_RESULT),
    ([message("user", "[SUGGESTION MODE: autocomplete]")], Modes.SUGGESTION),
    ([message("user", "Fix the parser.\n[SUGGESTION MODE: autocomplete]")], Modes.NORMAL),
    ([message("user", "[SUGGESTION MODE: autocomplete]\nInstructions for completion")], Modes.SUGGESTION),
    ([message("user", [result("a", "content"), {"type": "text", "text": "Now explain the error."}])], Modes.NORMAL),
])
def test_prompt_mode_preserves_visible_intent_and_tool_boundaries(messages, expected):
    assert api.detect_prompt_mode(request(messages), Modes) == expected


def test_targets_are_collected_from_actual_assistant_calls_with_supported_path_aliases():
    req = request([
        message("user", [call("fake", "Edit", {"path": "untrusted.py"})]),
        message("assistant", "No tools here"),
        message("assistant", [
            call("a", "Read", {"file_path": "a.py", "path": "ignored.py"}),
            call("b", "Edit", {"path": "b.py"}),
            call("c", "NotebookEdit", {"notebook_path": "c.ipynb"}),
            call("d", "Bash", {"path": "not-a-file-tool"}),
            call("e", "Edit", []),
            call("f", "Edit", {"file_path": 3}),
            {"type": "text", "text": "Edit mentioned in prose"},
        ]),
    ])
    assert api.extract_tool_targets(req, {"Read", "Edit", "NotebookEdit"}) == ["a.py", "b.py", "c.ipynb"]


def test_parallel_tool_result_is_paired_by_id_instead_of_the_last_advertised_call():
    req = request([
        message("user", "Read a.py and edit b.py"),
        message("assistant", [call("a", "Read", {"file_path": "a.py"}), call("b", "Edit", {"file_path": "b.py"})]),
        message("user", [result("b", "b.py edited"), result("a", "a.py contents")]),
    ])
    user, name, payload, args = api.extract_latest_tool_exchange(req)
    assert user == "Read a.py and edit b.py"
    assert name == "Read"
    assert args == {"file_path": "a.py"}
    assert "a.py contents" in payload and "b.py edited" not in payload


def test_pending_future_call_cannot_take_ownership_of_an_earlier_result():
    req = request([
        message("user", "Read a.py"),
        message("assistant", [call("a", "Read", {"file_path": "a.py"})]),
        message("user", [result("a", "completed a")]),
        message("assistant", [call("b", "Edit", {"file_path": "b.py"})]),
    ])
    _, name, payload, args = api.extract_latest_tool_exchange(req)
    assert name == "Read" and args == {"file_path": "a.py"}
    assert "completed a" in payload


def test_orphan_result_is_not_bound_to_an_unrelated_call():
    req = request([message("user", "Original request"), message("assistant", [call("a", "Edit", {"file_path": "a.py"})]),
                   message("user", [result("unknown", "orphan payload")])])
    _, name, payload, args = api.extract_latest_tool_exchange(req)
    assert name == "" and args == {}
    assert "orphan payload" in payload


def test_empty_latest_result_keeps_its_identity_and_does_not_reuse_old_output():
    req = request([message("user", "Run both checks"), message("assistant", [call("a", "Bash", {"command": "first"})]),
                   message("user", [result("a", "OLD_OUTPUT")]), message("assistant", [call("b", "Bash", {"command": "second"})]),
                   message("user", [result("b", "")])])
    _, name, payload, args = api.extract_latest_tool_exchange(req)
    assert name == "Bash" and args == {"command": "second"}
    assert "OLD_OUTPUT" not in payload
    assert "id=b" in payload


def test_empty_error_result_cannot_be_rendered_as_successful_completion():
    payload = api.flatten_block_content([result("failed", "", is_error=True)], role="user")
    assert "id=failed" in payload and "error=true" in payload
    assert "Tool completed successfully" not in api.build_tool_result_summary("Bash", payload, {})


def test_legacy_result_without_an_id_retains_the_nearest_preceding_call():
    req = request([message("assistant", [call("a", "Read", {"path": "a.py"})]),
                   message("user", [{"type": "tool_result", "content": "legacy text"}])])
    _, name, payload, args = api.extract_latest_tool_exchange(req)
    assert name == "Read" and args == {"path": "a.py"} and "legacy text" in payload


def test_exchange_without_a_result_keeps_the_pending_input_without_inventing_output():
    req = request([message("user", "Read a.py"), message("assistant", [call("a", "Read", {"file_path": "a.py"})])])
    user, name, payload, args = api.extract_latest_tool_exchange(req)
    assert (user, name, args) == ("Read a.py", "Read", {"file_path": "a.py"})
    assert json.loads(payload) == args


def test_oai_conversion_keeps_reasoning_separate_and_correlates_parallel_results():
    req = request([
        message("assistant", [{"type": "text", "text": "Visible reply"}, {"type": "thinking", "thinking": "PRIVATE_THOUGHT"},
                              call("a", "Read", {"path": "ёж.py"}), call("b", "Bash", {"command": "pwd"})]),
        message("user", [result("b", [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}]), result("a", "source")]),
    ], system=[{"type": "text", "text": "System A"}, {"type": "ignored", "text": "DO_NOT_INCLUDE"}, {"type": "text", "text": "System B"}])
    body = api.convert_anthropic_to_oai_body(req, extract_tools=lambda r: r.tools)
    assert body["messages"][0] == {"role": "system", "content": "System ASystem B"}
    assistant = body["messages"][1]
    assert assistant["content"] == [{"type": "text", "text": "Visible reply"}]
    assert assistant["reasoning_content"] == "PRIVATE_THOUGHT"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"path": "ёж.py"}
    assert body["messages"][2:] == [{"role": "tool", "tool_call_id": "b", "content": "line1line2"},
                                   {"role": "tool", "tool_call_id": "a", "content": "source"}]


@pytest.mark.parametrize("source, expected", [
    ({"type": "base64", "media_type": "image/png", "data": "fixture"}, "data:image/png;base64,fixture"),
    ({"type": "url", "url": "https://images.example/fixture.png"}, "https://images.example/fixture.png"),
    ({"type": "url", "url": ""}, None),
    ({"type": "other"}, None),
    (None, None),
])
def test_oai_image_conversion_handles_supported_sources_without_inventing_urls(source, expected):
    req = request([message("user", [{"type": "image", "source": source}])])
    body = api.convert_anthropic_to_oai_body(req, extract_tools=lambda r: [])
    if expected is None:
        assert body["messages"] == []
    else:
        assert body["messages"] == [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": expected}}]}]


@pytest.mark.parametrize("choice, expected", [({"type": "auto"}, "auto"), ({"type": "any"}, "required"),
                                             ({"type": "unknown"}, None), (None, None)])
def test_proxy_retains_supported_choice_modes_and_generation_parameters(choice, expected):
    tool = {"name": "Read", "description": "Read text", "input_schema": {"type": "object"}}
    req = request([], tools=[tool], max_tokens=0, model_extra={"tool_choice": choice, "stop_sequences": [],
                   "thinking": {"type": "enabled", "budget_tokens": "12"}, "metadata": {"user_id": "fixture-user"}})
    body = api.convert_anthropic_to_oai_body(req, extract_tools=lambda r: r.tools)
    assert body.get("tool_choice") == expected
    assert body["tools"][0]["function"] == {"name": "Read", "description": "Read text", "parameters": {"type": "object"}}
    assert body["max_tokens"] == 32000 and body["stop"] == []
    assert body["thinking_budget_tokens"] == 12 and body["__metadata_user_id"] == "fixture-user"
    assert (body["temperature"], body["top_p"], body["stream"]) == (.4, .9, True)


@pytest.mark.parametrize("visible, expected", [("Actual user request", "Actual user request"), ("", "Привет")])
def test_startup_reminder_mode_uses_only_the_visible_request(visible, expected):
    content = [{"type": "text", "text": "<system-reminder>HIDDEN_MARKER</system-reminder>"}]
    if visible:
        content.append({"type": "text", "text": visible})
    messages = api.extract_anthropic_messages(request([message("user", content)]), Modes.STARTUP_REMINDER,
                                               prompt_mode=Modes, tools_prompt="TOOL_SCHEMA")
    assert messages[-1] == {"role": "user", "content": expected}
    assert "TOOL_SCHEMA" in messages[0]["content"]
    assert "HIDDEN_MARKER" not in json.dumps(messages)


def test_suggestion_mode_keeps_completion_separate_from_tool_execution_context():
    prompt = "[SUGGESTION MODE: autocomplete]"
    messages = api.extract_anthropic_messages(request([message("user", "Earlier request"), message("user", prompt)]),
                                               Modes.SUGGESTION, prompt_mode=Modes, tools_prompt="DO_NOT_ADVERTISE_TOOLS")
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"] == prompt
    assert "DO_NOT_ADVERTISE_TOOLS" not in json.dumps(messages)


def test_normal_history_preserves_user_facts_while_excluding_structured_hidden_blocks():
    req = request([message("user", "My project is called Octopus"), message("assistant", "Understood"),
                   message("user", [{"type": "thinking", "thinking": "HIDDEN_MARKER"},
                                    {"type": "text", "text": "<system-reminder>HIDDEN_MARKER</system-reminder>"},
                                    {"type": "text", "text": "Continue that project"}])])
    messages = api.extract_anthropic_messages(req, Modes.NORMAL, prompt_mode=Modes, tools_prompt="TOOLS")
    assert messages[1:] == [{"role": "user", "content": "My project is called Octopus"},
                            {"role": "assistant", "content": "Understood"},
                            {"role": "user", "content": "Continue that project"}]
    assert "HIDDEN_MARKER" not in json.dumps(messages)


def test_latest_actionable_request_survives_a_tool_round_and_an_autocomplete_request():
    req = request([message("user", "Fix the actual source"), message("assistant", [call("a", "Read", {"path": "a.py"})]),
                   message("user", [result("a", "contents")]), message("assistant", "Processing"),
                   message("user", "[SUGGESTION MODE: autocomplete]")])
    assert api.extract_latest_user_request_text(req) == "Fix the actual source"


@pytest.mark.parametrize("prefix", ["User draft\nUser draft", "Original user request:\nUser draft\nTool result:\n"])
def test_embedded_drafts_exclude_protocol_labels_and_duplicate_lines(prefix):
    assert api.extract_embedded_suggestion_draft(prefix + "\n[SUGGESTION MODE: autocomplete]") == "User draft"


def test_non_object_tool_input_does_not_invent_arguments_in_the_result_context():
    req = request([message("assistant", [call("a", "Read", ["invalid arguments"])]), message("user", [result("a", "result")])])
    _, name, payload, args = api.extract_latest_tool_exchange(req)
    assert name == "Read" and args == {}
    assert "invalid arguments" not in payload and "result" in payload
