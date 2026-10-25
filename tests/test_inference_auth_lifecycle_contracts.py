"""ASGI authentication, failure correlation and model-lifecycle contracts."""

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from inference_api_support import (
    inference_app as inference_app,
    inference_server as inference_server,
)


MESSAGE = {"messages": [{"role": "user", "content": "question"}]}


@pytest.mark.parametrize(
    "headers,accepted",
    [
        ({}, False),
        ({"x-api-key": "wrong"}, False),
        ({"Authorization": "Basic fake"}, False),
        ({"Authorization": "Bearer"}, False),
        ({"Authorization": "Bearer wrong"}, False),
        ({"x-api-key": "synthetic-key"}, True),
        ({"Authorization": "Bearer synthetic-key"}, True),
        ({"x-api-key": "synthetic-key", "Authorization": "Bearer synthetic-key"}, True),
        ({"x-api-key": "synthetic-key", "Authorization": "Bearer wrong"}, False),
        ({"x-api-key": "wrong", "Authorization": "Bearer synthetic-key"}, False),
    ],
)
def test_both_credential_headers_must_match_when_present(
    inference_server, monkeypatch, headers, accepted
):
    env = inference_server
    monkeypatch.setenv("IGOREK_INFERENCE_API_KEY", " synthetic-key ")
    response = env.client.post("/v1/chat/completions", json=MESSAGE, headers=headers)
    assert response.status_code == (200 if accepted else 401), response.text
    assert bool(env.generator.calls) is accepted
    assert response.headers["x-request-id"].startswith("req_")
    if not accepted:
        body = response.json()
        assert body["error"]["type"] == "authentication_error"
        assert (
            body["request_id"] == response.headers["x-request-id"]
            and "synthetic-key" not in response.text
        )


@pytest.mark.parametrize("path", ["/health", "/v1/health", "/v1/models"])
def test_public_health_and_model_metadata_do_not_require_key(
    inference_server, monkeypatch, path
):
    env = inference_server
    monkeypatch.setenv("IGOREK_INFERENCE_API_KEY", "synthetic-key")
    env.api.SERVER_READY = False
    response = env.client.get(path, headers={"Authorization": "bad"})
    assert response.status_code == 200 and env.generator.calls == []


@pytest.mark.parametrize(
    "path", ["/v1/chat/completions", "/v1/messages", "/v1/messages/count_tokens"]
)
def test_paid_upstream_without_local_key_is_rejected_before_any_generation(
    inference_server, path
):
    env = inference_server
    env.api.MODEL_BACKEND = "openrouter"
    response = env.client.post(path, json=MESSAGE)
    assert (
        response.status_code == 503
        and "Configure IGOREK_INFERENCE_API_KEY" in response.json()["error"]["message"]
    )
    assert env.generator.calls == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("CONTEXT_COMPRESSION_API_KEY", "synthetic-compression-key"),
        ("CONTEXT_COMPRESSION_BASE_URL", "http://127.0.0.1:1"),
    ],
)
def test_paid_or_proxy_compression_requires_request_authentication(
    inference_server, monkeypatch, key, value
):
    env = inference_server
    monkeypatch.setenv("CONTEXT_COMPRESSION_ENABLED", "1")
    monkeypatch.setenv(key, value)
    response = env.client.post("/v1/messages/count_tokens", json=MESSAGE)
    assert response.status_code == 503 and env.generator.calls == []
    assert value not in response.text


def test_overlong_authentication_configuration_does_not_accept_any_request(
    inference_server, monkeypatch
):
    env = inference_server
    monkeypatch.setenv("IGOREK_INFERENCE_API_KEY", "x" * 4097)
    response = env.client.post(
        "/v1/chat/completions", json=MESSAGE, headers={"x-api-key": "x"}
    )
    assert response.status_code == 503 and env.generator.calls == []
    assert "configuration" in response.json()["error"]["message"]


def test_overlong_supplied_key_is_rejected_without_echo(inference_server, monkeypatch):
    env = inference_server
    monkeypatch.setenv("IGOREK_INFERENCE_API_KEY", "synthetic-key")
    response = env.client.post(
        "/v1/chat/completions", json=MESSAGE, headers={"x-api-key": "x" * 4097}
    )
    assert (
        response.status_code == 401
        and len(response.content) < 3000
        and env.generator.calls == []
    )


def test_http_exception_preserves_allowed_retry_headers_only(inference_server):
    env = inference_server
    env.generator.outputs.append(
        HTTPException(
            429,
            detail={
                "error": {"type": "rate_limit_error", "message": "fixture throttled"}
            },
            headers={
                "Retry-After": "7",
                "WWW-Authenticate": "Bearer",
                "X-Internal-Secret": "synthetic-private",
            },
        )
    )
    response = env.client.post("/v1/chat/completions", json=MESSAGE)
    assert response.status_code == 429, response.text
    assert (
        response.headers["retry-after"] == "7"
        and response.headers["www-authenticate"] == "Bearer"
    )
    assert "x-internal-secret" not in response.headers
    assert response.json()["error"]["message"] == "fixture throttled"
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_unhandled_generation_error_keeps_request_id_header(inference_server):
    env = inference_server
    env.generator.outputs.append(RuntimeError("fixture failure"))
    response = env.client.post("/v1/chat/completions", json=MESSAGE)
    assert (
        response.status_code == 500
        and response.json()["error"]["message"] == "Internal Server Error"
    )
    assert response.headers.get("x-request-id") == response.json()["request_id"]
    assert "fixture failure" not in response.text


def test_upstream_exception_secret_is_not_returned_or_written_to_trace(
    inference_server,
):
    env = inference_server
    env.generator.outputs.append(RuntimeError("api_key=synthetic-private-secret"))
    response = env.client.post("/v1/messages", json=MESSAGE)
    assert response.status_code == 502, response.text
    assert "synthetic-private-secret" not in response.text
    trace = env.api.TRACE_LOG.read_text()
    assert "synthetic-private-secret" not in trace
    assert "Generation failed" in response.json()["error"]["message"]


def test_application_lifespan_closes_engine_and_clears_readiness(inference_app):
    env = inference_app
    assert env.api.SERVER_READY is False
    with TestClient(env.api.app) as client:
        assert (
            client.get("/health").json() == {"status": "ok"}
            and env.api.CONTEXT_COMPRESSOR is not None
        )
    assert (
        env.generator.closed
        and not env.api.SERVER_READY
        and env.api.CONTEXT_COMPRESSOR is None
    )


def test_missing_model_configuration_does_not_start_engine(inference_app):
    env = inference_app
    env.api.MODEL_PATH = None
    with pytest.raises(RuntimeError, match="MODEL_PATH"):
        with TestClient(env.api.app):
            pytest.fail("Application should not start")
    assert (
        env.api.GENERATOR is None
        and not env.api.SERVER_READY
        and not env.generator.closed
    )


def test_context_setup_failure_releases_already_created_engine(
    inference_app, monkeypatch
):
    env = inference_app
    monkeypatch.setenv("CONTEXT_COMPRESSION_THRESHOLD", "not-a-number")
    with pytest.raises(ValueError):
        with TestClient(env.api.app):
            pytest.fail("Application should not start")
    assert (
        env.generator.closed
        and not env.api.SERVER_READY
        and env.api.CONTEXT_COMPRESSOR is None
    )


def test_http_empty_anthropic_messages_rejects_before_generation(inference_server):
    env = inference_server
    response = env.client.post("/v1/messages", json={"messages": []})
    assert (
        response.status_code == 400
        and "messages must not be empty" in response.json()["error"]["message"]
    )
    assert env.generator.calls == []
