"""Actual FastAPI middleware, validation and inference envelopes; no real model."""

import json

import pytest

from inference_api_support import (
    inference_app as inference_app,
    inference_server as inference_server,
)


@pytest.mark.parametrize("path", ["/health", "/v1/health"])
def test_health_routes_reflect_lifespan_readiness(inference_server, path):
    env = inference_server
    response = env.client.get(path)
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert response.headers["x-request-id"].startswith("req_")
    env.api.SERVER_READY = False
    assert env.client.get(path).json() == {"status": "loading"}


def test_models_exposes_configured_model_not_request_alias(inference_server):
    response = inference_server.client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["data"] == [
        {"id": "fixture-model", "object": "model", "owned_by": "local"}
    ]


def test_chat_completion_preserves_sampling_and_usage(inference_server):
    env = inference_server
    env.generator.outputs.append("actual reply")
    response = env.client.post(
        "/v1/chat/completions",
        json={
            "model": "requested-alias",
            "messages": [{"role": "user", "content": "question"}],
            "max_tokens": 17,
            "temperature": 0.3,
            "top_p": 0.8,
            "top_k": 20,
            "repetition_penalty": 1.1,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "fixture-model" and body["choices"][0]["message"] == {
        "role": "assistant",
        "content": "actual reply",
    }
    assert body["usage"]["completion_tokens"] == len("actual reply")
    assert body["usage"]["total_tokens"] == body["usage"]["prompt_tokens"] + len(
        "actual reply"
    )
    params = env.generator.calls[0]["params"]
    assert (
        params.max_tokens,
        params.temperature,
        params.top_p,
        params.top_k,
        params.repetition_penalty,
    ) == (17, 0.3, 0.8, 20, 1.1)


@pytest.mark.parametrize(
    "payload,status,detail",
    [
        ({"messages": []}, 400, "messages must not be empty"),
        (
            {"messages": [{"role": "user", "content": "question"}], "stream": True},
            400,
            "stream=true",
        ),
    ],
)
def test_unsupported_chat_request_returns_correlated_error(
    inference_server, payload, status, detail
):
    env = inference_server
    response = env.client.post("/v1/chat/completions", json=payload)
    assert response.status_code == status, response.text
    body = response.json()
    assert detail in body["error"]["message"]
    assert (
        body["request_id"] == response.headers["x-request-id"]
        and body["diagnostic"]["http_status"] == status
    )
    assert env.generator.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"messages": "invalid"},
        {"messages": [{"role": "user"}]},
        {
            "messages": [{"role": "user", "content": "question"}],
            "max_tokens": "invalid",
        },
    ],
)
def test_schema_rejection_occurs_before_generation(inference_server, payload):
    env = inference_server
    response = env.client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 422, response.text
    assert env.generator.calls == []


def test_not_ready_private_request_is_rejected_before_generation(inference_server):
    env = inference_server
    env.api.SERVER_READY = False
    response = env.client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "question"}]},
    )
    assert (
        response.status_code == 503
        and response.json()["error"]["message"] == "Loading model"
    )
    assert env.generator.calls == []


def test_anthropic_text_uses_real_normalization_and_response_pipeline(inference_server):
    env = inference_server
    env.generator.outputs.append("actual reply")
    response = env.client.post(
        "/v1/messages",
        json={
            "model": "fixture-model",
            "messages": [{"role": "user", "content": "question"}],
            "max_tokens": 20,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (
        body["content"] == [{"type": "text", "text": "actual reply"}]
        and body["role"] == "assistant"
    )
    assert (
        body["usage"]["output_tokens"] == len("actual reply")
        and body["stop_reason"] == "end_turn"
    )
    assert "question" in env.generator.calls[-1]["prompt"]


def test_count_tokens_runs_normalization_without_generation(inference_server):
    env = inference_server
    response = env.client.post(
        "/v1/messages/count_tokens",
        json={"messages": [{"role": "user", "content": "question"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["input_tokens"] > len("question")
    assert env.generator.calls == []


def test_anthropic_sse_stream_preserves_fragment_text_and_usage(inference_server):
    env = inference_server
    response = env.client.post(
        "/v1/messages",
        json={
            "messages": [{"role": "user", "content": "question"}],
            "stream": True,
            "max_tokens": 20,
        },
    )
    assert response.status_code == 200, response.text
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[0]["type"] == "message_start" and events[-1]["type"] == "message_stop"
    content = "".join(
        event["delta"]["text"]
        for event in events
        if event.get("type") == "content_block_delta"
        and event["delta"].get("type") == "text_delta"
    )
    assert content == "Hello world"
    last = next(event for event in events if event["type"] == "message_delta")
    assert last["usage"]["output_tokens"] == len("Hello world")
