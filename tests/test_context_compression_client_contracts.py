"""Summaries use the intended endpoint and preserve failures for the caller."""

import asyncio
import importlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests

from inference_api_support import inference_app as inference_app


@pytest.fixture
def compression(inference_app):
    module = importlib.import_module(
        "factory.api_server_core.context_compression_client"
    )
    return SimpleNamespace(module=module, generator=inference_app.generator)


def client(env, **options):
    return env.module.ContextCompressionClient(
        generator=env.generator,
        model_name="default-summary-model",
        config=env.module.ContextCompressionClientConfig(**options),
    )


@pytest.mark.parametrize("prompt", [None, "", "  ", "\n\t"])
def test_empty_prompt_never_contacts_a_provider(compression, monkeypatch, prompt):
    post = Mock(side_effect=AssertionError("Unexpected provider request"))
    monkeypatch.setattr(compression.module.requests, "post", post)
    assert (
        asyncio.run(
            client(compression, base_url="https://fixture.invalid").generate_summary(
                prompt, max_tokens=20
            )
        )
        == ""
    )
    assert not compression.generator.calls
    post.assert_not_called()


@pytest.mark.parametrize("backend", ["gguf", "openrouter", "transformers"])
def test_explicit_endpoint_wins_and_runs_off_event_loop(
    compression, monkeypatch, backend
):
    env = compression
    env.generator.backend = backend
    env.generator.base_url = "https://wrong.invalid"
    env.generator.api_key = "wrong-fixture-key"
    env.generator.model = "wrong-model"
    main_thread = threading.get_ident()
    requests_seen = []

    def post(url, **kwargs):
        requests_seen.append((url, kwargs, threading.get_ident()))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": "  summary \n"}}]},
        )

    monkeypatch.setattr(env.module.requests, "post", post)
    summary = asyncio.run(
        client(
            env,
            base_url="  https://fixture.invalid/v1///  ",
            model="summary-only",
            api_key="fixture-key",
            timeout_seconds=47,
            temperature=0.25,
        ).generate_summary("  remember this  ", max_tokens=321)
    )
    assert summary == "summary"
    assert len(requests_seen) == 1
    url, request, worker_thread = requests_seen[0]
    assert worker_thread != main_thread
    assert url == "https://fixture.invalid/v1/chat/completions"
    assert request == {
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer fixture-key",
        },
        "json": {
            "model": "summary-only",
            "messages": [{"role": "user", "content": "remember this"}],
            "max_tokens": 321,
            "temperature": 0.25,
            "stream": False,
        },
        "timeout": (10, 47),
    }
    assert not env.generator.calls


@pytest.mark.parametrize(
    "backend,base,key,model,expected",
    [
        ("gguf", " https://llama.invalid/// ", "", "", "llama"),
        (" GGUF ", "https://llama.invalid", "", "", "llama"),
        ("gguf", " ", "", "", "local"),
        ("openrouter", "", " provider-key ", "provider-model", "router"),
        ("openrouter", "", "", "provider-model", "local"),
        ("transformers", "", "", "", "local"),
        ("unknown", "", "", "", "local"),
    ],
)
def test_backend_resolution_preserves_fallback(
    compression, monkeypatch, backend, base, key, model, expected
):
    env = compression
    env.generator.backend = backend
    env.generator.base_url = base
    env.generator.api_key = key
    env.generator.model = model
    instance = client(env)
    llama = Mock(return_value="llama summary")
    router = Mock(return_value="router summary")
    local = AsyncMock(return_value="local summary")
    monkeypatch.setattr(instance, "_request_llama_server", llama)
    monkeypatch.setattr(instance, "_request_openrouter", router)
    monkeypatch.setattr(instance, "_request_local_generator", local)
    assert (
        asyncio.run(instance.generate_summary("  prompt  ", max_tokens=45))
        == expected + " summary"
    )
    if expected == "llama":
        llama.assert_called_once_with(base.strip().rstrip("/"), "prompt", 45)
        router.assert_not_called()
        local.assert_not_awaited()
    elif expected == "router":
        router.assert_called_once_with(model, "prompt", 45, key.strip())
        llama.assert_not_called()
        local.assert_not_awaited()
    else:
        local.assert_awaited_once_with("prompt", 45)
        llama.assert_not_called()
        router.assert_not_called()


def test_openrouter_config_supplies_missing_key_and_overrides_model(
    compression, monkeypatch
):
    env = compression
    env.generator.backend = "openrouter"
    env.generator.api_key = ""
    env.generator.model = "provider-model"
    instance = client(env, model="summary-model", api_key=" config-key ")
    request = Mock(return_value="summary")
    monkeypatch.setattr(instance, "_request_openrouter", request)
    assert asyncio.run(instance.generate_summary("prompt", max_tokens=12)) == "summary"
    request.assert_called_once_with("summary-model", "prompt", 12, "config-key")


@pytest.mark.parametrize("route", ["openai", "llama", "router"])
def test_wire_request_keeps_protocol_and_sampling_fields(
    compression, monkeypatch, route
):
    env = compression
    instance = client(env, timeout_seconds=83, temperature=0.4)
    response = Mock()
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "first"},
                        {"type": "text", "text": "second"},
                    ]
                }
            }
        ]
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(env.module.requests, "post", post)
    if route == "openai":
        result = instance._request_openai_compatible(
            "https://fixture.invalid/v1", "summary-model", "prompt", 77, ""
        )
        expected_url = "https://fixture.invalid/v1/chat/completions"
    elif route == "llama":
        result = instance._request_llama_server("https://fixture.invalid", "prompt", 77)
        expected_url = "https://fixture.invalid/v1/chat/completions"
    else:
        result = instance._request_openrouter(
            "summary-model", "prompt", 77, "router-fixture-key"
        )
        expected_url = env.module.OPENROUTER_BASE_URL + "/chat/completions"
    assert result == "first\nsecond"
    response.raise_for_status.assert_called_once_with()
    request = post.call_args.kwargs
    assert post.call_args.args == (expected_url,)
    assert request["timeout"] == (10, 83)
    assert request["json"] == {
        **({"model": "summary-model"} if route != "llama" else {}),
        "messages": [{"role": "user", "content": "prompt"}],
        "max_tokens": 77,
        "temperature": 0.4,
        "stream": False,
    }
    assert request["headers"]["Content-Type"] == "application/json"
    if route == "router":
        assert request["headers"]["Authorization"] == "Bearer router-fixture-key"
        assert request["headers"]["HTTP-Referer"].startswith("https://github.com/")
    else:
        assert "Authorization" not in request["headers"]


@pytest.mark.parametrize("route", ["openai", "llama", "router"])
@pytest.mark.parametrize("failure", ["http", "json", "timeout"])
def test_provider_failures_reach_caller_without_second_generation(
    compression, monkeypatch, route, failure
):
    env = compression
    env.generator.backend = {
        "openai": "transformers",
        "llama": "gguf",
        "router": "openrouter",
    }[route]
    env.generator.base_url = "https://fixture.invalid"
    env.generator.api_key = "fixture-key"
    response = Mock()
    error = {
        "http": requests.HTTPError("fixture HTTP error"),
        "json": ValueError("fixture malformed JSON"),
        "timeout": requests.Timeout("fixture timeout"),
    }[failure]
    if failure == "http":
        response.raise_for_status.side_effect = error
    elif failure == "json":
        response.json.side_effect = error
    post = (
        Mock(side_effect=error) if failure == "timeout" else Mock(return_value=response)
    )
    monkeypatch.setattr(env.module.requests, "post", post)
    instance = client(
        env, base_url="https://fixture.invalid/v1" if route == "openai" else None
    )
    with pytest.raises(type(error), match="fixture") as raised:
        asyncio.run(instance.generate_summary("prompt", max_tokens=50))
    assert raised.value is error
    post.assert_called_once()
    assert not env.generator.calls
    if failure == "http":
        response.json.assert_not_called()


@pytest.mark.parametrize(
    "output,expected", [("  brief \n", "brief"), (None, ""), (42, "42")]
)
def test_local_generator_receives_summary_prompt_and_bounded_parameters(
    compression, output, expected
):
    env = compression

    async def generate(prompt, params):
        env.generator.calls.append({"prompt": prompt, "params": params, "kwargs": {}})
        return SimpleNamespace(output_text=output)

    env.generator.generate = generate
    assert (
        asyncio.run(
            client(env, temperature=0.15).generate_summary(
                "  long dialogue  ", max_tokens=88
            )
        )
        == expected
    )
    assert len(env.generator.calls) == 1
    call = env.generator.calls[0]
    assert json.loads(call["prompt"].split("\nassistant:")[0]) == [
        {"role": "user", "content": "long dialogue"}
    ]
    assert vars(call["params"]) == {
        "max_tokens": 88,
        "temperature": 0.15,
        "top_p": 1.0,
        "top_k": -1,
        "repetition_penalty": 1.0,
    }
    assert call["kwargs"] == {}


def test_local_generator_failure_is_not_replaced_with_empty_summary(compression):
    error = RuntimeError("fixture generation failed")
    compression.generator.outputs.append(error)
    with pytest.raises(RuntimeError, match="fixture generation failed") as raised:
        asyncio.run(client(compression).generate_summary("prompt", max_tokens=50))
    assert raised.value is error


def test_local_generator_requires_tokenizer(compression):
    compression.generator.tokenizer = None
    with pytest.raises(RuntimeError, match="tokenizer is unavailable"):
        asyncio.run(client(compression).generate_summary("prompt", max_tokens=50))
    assert not compression.generator.calls


@pytest.mark.parametrize(
    "timeout,expected",
    [("", 180), ("abc", 180), (" 45.5 ", 45.5), ("5", 30), ("-50", 30), ("30", 30)],
)
def test_environment_config_trims_values_and_bounds_timeout(
    compression, monkeypatch, timeout, expected
):
    for key, value in {
        "MODEL": " summary-model ",
        "BASE_URL": " https://fixture.invalid/v1/ ",
        "API_KEY": " fixture-key ",
        "TIMEOUT_SECONDS": timeout,
    }.items():
        monkeypatch.setenv("CONTEXT_COMPRESSION_" + key, value)
    config = compression.module.build_context_compression_client_config()
    assert vars(config) == {
        "model": "summary-model",
        "base_url": "https://fixture.invalid/v1/",
        "api_key": "fixture-key",
        "timeout_seconds": expected,
        "temperature": 0.1,
    }


def test_environment_defaults_do_not_invent_credentials(compression):
    config = compression.module.build_context_compression_client_config()
    assert vars(config) == {
        "model": None,
        "base_url": None,
        "api_key": None,
        "timeout_seconds": 180,
        "temperature": 0.1,
    }
