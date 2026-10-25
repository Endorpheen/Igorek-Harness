"""Real ASGI stream envelopes, stop boundaries, error privacy and media payloads."""

import json

import pytest

from inference_api_support import (
    BASH,
    body,
    inference_app as inference_app,
    inference_server as inference_server,
)


def events(response):
    assert response.status_code == 200, response.text
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def text_of(rows):
    return "".join(
        row["delta"]["text"]
        for row in rows
        if row.get("type") == "content_block_delta"
        and row["delta"].get("type") == "text_delta"
    )


def test_stop_sequence_crossing_chunks_never_leaks_marker_or_suffix(inference_server):
    env = inference_server
    env.generator.stream_chunks = ["abc<ST", "OP>must not be emitted"]
    response = env.client.post(
        "/v1/messages",
        json={
            "messages": [{"role": "user", "content": "question"}],
            "stream": True,
            "stop_sequences": ["<STOP>"],
        },
    )
    rows = events(response)
    assert text_of(rows) == "abc"
    final = next(row for row in rows if row["type"] == "message_delta")
    assert (
        final["delta"] == {"stop_reason": "stop_sequence", "stop_sequence": "<STOP>"}
        and final["usage"]["output_tokens"] == 3
    )
    assert rows[-1]["type"] == "message_stop"


def test_partial_stop_that_does_not_complete_is_released(inference_server):
    env = inference_server
    env.generator.stream_chunks = ["abc<ST", "x"]
    rows = events(
        env.client.post(
            "/v1/messages",
            json={
                "messages": [{"role": "user", "content": "question"}],
                "stream": True,
                "stop_sequences": ["<STOP>"],
            },
        )
    )
    assert text_of(rows) == "abc<STx"


def test_output_token_limit_has_explicit_stream_stop_reason(inference_server):
    env = inference_server
    env.generator.stream_chunks = ["abc"]
    rows = events(
        env.client.post(
            "/v1/messages",
            json={
                "messages": [{"role": "user", "content": "question"}],
                "stream": True,
                "max_tokens": 3,
            },
        )
    )
    final = next(row for row in rows if row["type"] == "message_delta")
    assert (
        final["delta"]["stop_reason"] == "max_tokens"
        and final["usage"]["output_tokens"] == 3
    )


def test_empty_stream_still_has_complete_envelope(inference_server):
    env = inference_server
    env.generator.stream_chunks = []
    rows = events(
        env.client.post(
            "/v1/messages",
            json={
                "messages": [{"role": "user", "content": "question"}],
                "stream": True,
            },
        )
    )
    assert (
        rows[0]["type"] == "message_start"
        and rows[-1]["type"] == "message_stop"
        and text_of(rows) == ""
    )
    assert (
        next(row for row in rows if row["type"] == "message_delta")["usage"][
            "output_tokens"
        ]
        == 0
    )


@pytest.mark.parametrize("kind", ["runtime", "overflow"])
def test_stream_generation_failure_ends_cleanly_with_actionable_message(
    inference_server, kind
):
    from factory.api_server_core.generator_runtime import ContextOverflowError

    env = inference_server
    env.generator.stream_chunks = [
        RuntimeError("fixture unavailable")
        if kind == "runtime"
        else ContextOverflowError("fixture overflow")
    ]
    rows = events(
        env.client.post(
            "/v1/messages",
            json={
                "messages": [{"role": "user", "content": "question"}],
                "stream": True,
            },
        )
    )
    assert rows[-1]["type"] == "message_stop"
    assert (
        "Ошибка генерации" if kind == "runtime" else "Контекст превышает"
    ) in text_of(rows)


def test_stream_error_redacts_credential_assignment(inference_server):
    env = inference_server
    env.generator.stream_chunks = [RuntimeError("api_key=synthetic-stream-secret")]
    response = env.client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "question"}], "stream": True},
    )
    rows = events(response)
    assert "synthetic-stream-secret" not in response.text and "[REDACTED]" in text_of(
        rows
    )


@pytest.mark.parametrize("stream", [False, True])
def test_hidden_only_reminder_does_not_reappear_as_fallback(inference_server, stream):
    env = inference_server
    hidden = "<system-reminder>hidden-fixture-control</system-reminder>"
    env.generator.outputs.append(hidden)
    env.generator.stream_chunks = [hidden]
    response = env.client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "question"}], "stream": stream},
    )
    assert response.status_code == 200, response.text
    if stream:
        assert text_of(events(response)) == ""
    else:
        assert all(not block.get("text") for block in response.json()["content"])
    assert "hidden-fixture-control" not in response.text


def test_open_hidden_reminder_is_held_until_closing_tag(inference_server):
    env = inference_server
    env.generator.stream_chunks = [
        "<system-reminder>hidden-fixture",
        " control</system-reminder>",
        "Visible answer",
    ]
    response = env.client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "question"}], "stream": True},
    )
    assert (
        text_of(events(response)) == "Visible answer"
        and "hidden-fixture" not in response.text
    )


def test_normal_unicode_and_code_are_preserved_in_stream(inference_server):
    env = inference_server
    env.generator.stream_chunks = ["Код:\n\n```python\n", "print('🐙')\n```"]
    rows = events(
        env.client.post(
            "/v1/messages",
            json={
                "messages": [{"role": "user", "content": "question"}],
                "stream": True,
            },
        )
    )
    assert text_of(rows) == "Код:\n\n```python\nprint('🐙')\n```"


@pytest.mark.parametrize("prefix", ["", "Visible preface"])
def test_textual_tool_output_is_validated_before_becoming_stream_tool_block(
    inference_server, prefix
):
    env = inference_server
    env.generator.outputs.extend(
        ['{"intent":"other"}', '{"intent":"other"}', '{"tool":null}']
    )
    tool = "<tool_call><function=Bash><parameter=command>pwd</parameter></function></tool_call>"
    env.generator.stream_chunks = ([prefix] if prefix else []) + [tool]
    rows = events(
        env.client.post(
            "/v1/messages", json=body(env, "Fixture request", [BASH], stream=True)
        )
    )
    blocks = [
        row["content_block"]
        for row in rows
        if row["type"] == "content_block_start"
        and row["content_block"]["type"] == "tool_use"
    ]
    if prefix:
        assert blocks == [] and text_of(rows) == prefix
    else:
        assert len(blocks) == 1 and blocks[0]["name"] == "Bash"
        partial = next(
            row["delta"]["partial_json"]
            for row in rows
            if row.get("delta", {}).get("type") == "input_json_delta"
        )
        assert json.loads(partial) == {"command": "pwd"}
        assert (
            next(row for row in rows if row["type"] == "message_delta")["delta"][
                "stop_reason"
            ]
            == "tool_use"
        )
    assert "<tool_call>" not in text_of(rows)


@pytest.mark.parametrize("nested", [False, True])
def test_multimodal_input_keeps_opaque_image_payload_in_engine_messages(
    inference_server, nested
):
    env = inference_server
    env.generator.backend = "gguf"
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Describe fixture image"}, image],
            }
        ]
    }
    if nested:
        payload["messages"] = [
            {"role": "user", "content": "Describe fixture image"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "image-1",
                        "name": "Read",
                        "input": {"file_path": str(env.root / "fixture.png")},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "image-1",
                        "content": [{"type": "text", "text": "Image fetched"}, image],
                    }
                ],
            },
        ]
    response = env.client.post("/v1/messages", json=payload)
    assert response.status_code == 200, response.text
    messages = env.generator.calls[-1]["kwargs"]["messages"]
    last = next(item for item in reversed(messages) if item["role"] == "user")
    assert any(
        part.get("image_url", {}).get("url") == "data:image/png;base64,AAAA"
        for part in last["content"]
    )
