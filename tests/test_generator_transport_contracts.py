"""HTTP/process adapter contracts with data-only optional inference types.

The optional model engine is not installed or executed. Real adapter code runs
against fake HTTP responses and process handles; no GPU or owner ports are used.
"""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def adapters(monkeypatch, tmp_path):
    http = types.ModuleType("requests")
    http.RequestException = OSError
    http.ConnectionError = ConnectionError
    http.post = Mock(side_effect=AssertionError("Unexpected external request"))
    http.get = Mock(side_effect=AssertionError("Unexpected external request"))
    http.Session = Mock(side_effect=AssertionError("Unexpected external session"))
    monkeypatch.setitem(sys.modules, "requests", http)
    package = types.ModuleType("paroquant")
    inference = types.ModuleType("paroquant.inference")
    base = types.ModuleType("paroquant.inference.base")
    base.BaseGenerator = object
    base.GenerationParams = base.GenerationStats = base.GenerationResult = (
        SimpleNamespace
    )
    inference.create_generator = Mock(return_value="transformers-fixture")
    package.inference = inference
    inference.base = base
    for name, module in [
        ("paroquant", package),
        ("paroquant.inference", inference),
        ("paroquant.inference.base", base),
    ]:
        monkeypatch.setitem(sys.modules, name, module)
    names = [
        "factory.api_server_core.generator_runtime",
        "factory.api_server_core.openrouter_runtime",
    ]
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    g = importlib.import_module(names[0])
    o = importlib.import_module(names[1])
    calls = []
    processes = []

    def launch(command, **kwargs):
        calls.append(command)
        kwargs["stderr"].close()
        process = Mock(returncode=None)
        process.poll.return_value = None
        processes.append(process)
        return process

    monkeypatch.setattr(g.subprocess, "Popen", launch)
    monkeypatch.setattr(
        g.LlamaServerGenerator, "_pick_free_port", staticmethod(lambda _: 18182)
    )
    monkeypatch.setattr(g.requests, "get", Mock(return_value=SimpleNamespace(ok=True)))
    monkeypatch.setattr(
        g.requests,
        "post",
        Mock(side_effect=AssertionError("Unconfigured HTTP request")),
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-fixture-key")
    monkeypatch.delenv("FACTORY_OPENROUTER_API_KEY", raising=False)

    def create(**kwargs):
        model = tmp_path / kwargs.pop("filename", "fixture.gguf")
        model.write_bytes(b"fixture")
        args = dict(
            backend="gguf",
            model_path=str(model),
            llama_server_bin="/never-execute/llama-fixture",
            llama_server_port=18181,
        )
        args.update(kwargs)
        return g.create_runtime_generator(**args)

    yield SimpleNamespace(
        g=g, o=o, create=create, calls=calls, processes=processes, inference=inference
    )
    for name in names:
        sys.modules.pop(name, None)


@pytest.fixture
def params():
    return SimpleNamespace(
        max_tokens=17, temperature=0.5, top_p=0.9, top_k=20, repetition_penalty=1.1
    )


def response(body, status=200):
    result = Mock(status_code=status, text="fixture detail")
    result.json.return_value = body
    result.raise_for_status.return_value = None
    return result


@pytest.mark.parametrize(
    "model,backend,expected",
    [
        ("file.GGUF", "auto", "gguf"),
        ("remote-model", "auto", "transformers"),
        ("file.gguf", "openrouter", "openrouter"),
        ("file", "transformers", "transformers"),
    ],
)
def test_backend_resolution_is_explicit(adapters, model, backend, expected):
    assert adapters.g.resolve_runtime_backend(model, backend) == expected
    with pytest.raises(ValueError):
        adapters.g.resolve_runtime_backend(model, "unsupported")


@pytest.mark.parametrize(
    "extra,expected_default",
    [
        ([], True),
        (["-ngl", "4"], False),
        (["--n-gpu-layers=4"], False),
        (["-fit", "on"], False),
        (["-fit=off"], False),
    ],
)
def test_launch_command_preserves_explicit_offload_flags(
    adapters, extra, expected_default
):
    generator = adapters.create(llama_server_extra_args=extra)
    command = adapters.calls[-1]
    assert command[:3] == ["/never-execute/llama-fixture", "-m", generator.model_path]
    assert ("999" in command) is expected_default
    if extra:
        assert command[-len(extra) :] == extra
    assert "--port" in command and command[command.index("--port") + 1] == "18181"
    asyncio.run(generator.close())
    adapters.processes[-1].terminate.assert_called_once()


def test_custom_command_and_projector_detection_are_distinct(adapters, tmp_path):
    (tmp_path / "mmproj-fixture.gguf").write_bytes(b"fixture")
    generator = adapters.create()
    assert "--mmproj" in adapters.calls[-1]
    asyncio.run(generator.close())
    generator = adapters.create(llama_server_command=["never-execute", "custom"])
    assert adapters.calls[-1] == ["never-execute", "custom"]
    asyncio.run(generator.close())


def test_creation_of_other_backends_does_not_launch_llama(adapters):
    assert (
        adapters.g.create_runtime_generator(
            backend="transformers", model_path="fixture"
        )
        == "transformers-fixture"
    )
    adapters.inference.create_generator.assert_called_once_with(
        "transformers", "fixture"
    )
    assert (
        adapters.g.create_runtime_generator(
            backend="openrouter", model_path="fixture"
        ).model
        == "fixture"
    )
    assert adapters.calls == []


def test_missing_model_fails_before_process_creation(adapters, tmp_path):
    with pytest.raises(FileNotFoundError):
        adapters.g.create_runtime_generator(
            backend="gguf", model_path=str(tmp_path / "missing.gguf")
        )
    assert adapters.calls == []


@pytest.mark.parametrize(
    "filename,qwen", [("qwen-fixture.gguf", True), ("gemma-fixture.gguf", False)]
)
def test_completion_payload_preserves_sampling_and_model_stops(
    adapters, params, filename, qwen
):
    generator = adapters.create(filename=filename)
    payload = generator._completion_payload("prompt", params)
    assert (
        payload["prompt"] == "prompt"
        and payload["n_predict"] == 17
        and payload["top_k"] == 20
    )
    assert ("stop" in payload) is qwen and not payload["stream"]
    params.top_k = 0
    assert "top_k" not in generator._completion_payload("prompt", params)
    messages = [{"role": "user", "content": "hello"}]
    assert generator._chat_completion_payload(messages, params)["messages"] == messages
    asyncio.run(generator.close())


@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize(
    "kind", ["ok", "context", "nonobject", "error", "bad-json-context"]
)
def test_completion_http_errors_and_context_metadata(
    adapters, params, monkeypatch, chat, kind
):
    generator = adapters.create()
    body = {"content": "hello", "choices": [{"message": {"content": "hello"}}]}
    status = 200
    if kind in {"context", "bad-json-context"}:
        status = 400
        body = {"error": "context overflow"}
    if kind == "nonobject":
        body = []
    if kind == "error":
        body = {"error": "fixture failure"}
    reply = response(body, status)
    if kind == "bad-json-context":
        reply.json.side_effect = ValueError("fixture invalid JSON")
    post = Mock(return_value=reply)
    monkeypatch.setattr(adapters.g.requests, "post", post)
    call = generator._chat_completion_request if chat else generator._completion_request
    request = [{"role": "user", "content": "prompt"}] if chat else "prompt"
    if status == 400:
        with pytest.raises(adapters.g.ContextOverflowError) as error:
            call(request, params)
        assert error.value.ctx_size == 16384 and error.value.prompt_chars > 0
    elif kind in {"nonobject", "error"}:
        with pytest.raises(RuntimeError):
            call(request, params)
    else:
        assert call(request, params) == body
    assert post.call_args.kwargs["timeout"] == (10, 600)
    assert post.call_args.args[0].startswith("http://127.0.0.1:18181/")
    asyncio.run(generator.close())


@pytest.mark.parametrize("chat", [False, True])
def test_async_generate_and_stream_use_real_transport_adapter(
    adapters, params, monkeypatch, chat
):
    generator = adapters.create()
    payload = {
        "content": "hello",
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"completion_tokens": 5},
        "timings": {"predicted_n": 4, "predicted_per_second": 10, "prompt_ms": 125},
    }
    monkeypatch.setattr(
        adapters.g.requests, "post", Mock(return_value=response(payload))
    )
    received = []
    messages = [{"role": "user", "content": "prompt"}] if chat else None
    result = asyncio.run(
        generator.generate("prompt", params, on_text=received.append, messages=messages)
    )
    assert result.output_text == "hello" and received == ["hello"]
    assert (
        result.stats.num_tokens == (5 if chat else 4)
        and result.stats.ttft == 0.125
        and result.stats.tps == 10
    )

    async def collect():
        return [
            chunk
            async for chunk in generator.stream_generate(
                "prompt", params, messages=messages
            )
        ]

    assert asyncio.run(collect()) == ["hello"]
    asyncio.run(generator.close())


def test_transport_failures_keep_exception_cause(adapters, params, monkeypatch):
    generator = adapters.create()
    failure = adapters.g.requests.ConnectionError("fixture offline")
    monkeypatch.setattr(adapters.g.requests, "post", Mock(side_effect=failure))
    with pytest.raises(RuntimeError, match="Generator error") as error:
        asyncio.run(generator.generate("prompt", params))
    assert error.value.__cause__ is failure
    asyncio.run(generator.close())


def test_close_escalates_only_its_process_when_wait_fails(adapters):
    generator = adapters.create()
    process = adapters.processes[-1]
    process.wait.side_effect = [TimeoutError("fixture wait"), None]
    stderr = Path(generator._stderr_path)
    asyncio.run(generator.close())
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert not stderr.exists()


@pytest.mark.parametrize(
    "tokenizer_type", ["LlamaServerTokenizer", "QwenLlamaServerTokenizer"]
)
def test_local_tokenizers_render_roles_and_optional_generation_prompt(
    adapters, tokenizer_type
):
    tokenizer = getattr(adapters.g, tokenizer_type)()
    messages = [
        {"role": "system", "content": "Rules"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Reply"},
    ]
    prompt = tokenizer.apply_chat_template(messages)
    assert all(text in prompt for text in ["Rules", "Hello", "Reply"])
    assert tokenizer.apply_chat_template(messages, tokenize=True) == tokenizer.encode(
        prompt
    )
    assert (
        tokenizer.apply_chat_template(messages, add_generation_prompt=False) != prompt
    )
    assert tokenizer.encode("") == [] and len(tokenizer.encode("Hello!", True)) == 3


@pytest.mark.parametrize(
    "status,message",
    [(401, "invalid API key"), (402, "insufficient credits"), (429, "rate limited")],
)
def test_openrouter_provider_status_is_actionable(adapters, status, message):
    with pytest.raises(RuntimeError, match=message):
        adapters.o._check_response_status(response({}, status))


def test_openrouter_key_precedence_missing_and_fallback(adapters, monkeypatch):
    assert adapters.o._resolve_api_key() == "synthetic-fixture-key"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("FACTORY_OPENROUTER_API_KEY", " synthetic-fallback ")
    assert adapters.o._resolve_api_key() == "synthetic-fallback"
    monkeypatch.delenv("FACTORY_OPENROUTER_API_KEY")
    with pytest.raises(RuntimeError, match="not found"):
        adapters.o._resolve_api_key()


def test_openrouter_generate_stream_and_session_cleanup(adapters, params, monkeypatch):
    fake = Mock()
    reply = response(
        {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"completion_tokens": 7},
        }
    )
    reply.iter_lines.return_value = iter(
        [
            "comment",
            "",
            "data: invalid",
            'data: {"choices":[]}',
            'data: {"choices":[{"delta":{"content":"hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            "data: [DONE]",
        ]
    )
    fake.post.return_value = reply
    fake.headers = {}
    monkeypatch.setattr(adapters.o.requests, "Session", lambda: fake)
    generator = adapters.o.OpenRouterGenerator(model="fixture/model")
    received = []
    result = asyncio.run(generator.generate("prompt", params, on_text=received.append))
    assert (
        result.output_text == "hello"
        and result.stats.num_tokens == 7
        and received == ["hello"]
    )
    assert fake.post.call_args.kwargs["json"]["messages"] == [
        {"role": "user", "content": "prompt"}
    ]

    async def collect():
        return [chunk async for chunk in generator.stream_generate("prompt", params)]

    assert asyncio.run(collect()) == ["hello"]
    asyncio.run(generator.close())
    fake.close.assert_called_once()
    assert generator._session is None
    assert fake.headers["Authorization"] == "Bearer synthetic-fixture-key"


def test_openrouter_tokenizer_and_message_projection(adapters, params):
    generator = adapters.o.OpenRouterGenerator(model="fixture")
    messages = generator._build_messages("unused", [{"role": "user", "content": 123}])
    assert messages == [{"role": "user", "content": "123"}]
    tokenizer = generator.tokenizer
    assert tokenizer.encode("") == []
    assert (
        tokenizer.apply_chat_template(messages, add_generation_prompt=False)
        == "user: 123"
    )
    assert tokenizer.apply_chat_template(messages, tokenize=True) == tokenizer.encode(
        "user: 123\nassistant: "
    )
    assert generator._build_payload(messages, params, stream=True)["stream"]
    asyncio.run(generator.close())
