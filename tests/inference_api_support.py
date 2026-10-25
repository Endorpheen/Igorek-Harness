"""Real ASGI application fixture with a deterministic model-engine boundary."""

from collections import deque
from dataclasses import dataclass
import importlib
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import Mock
from urllib import request

import pytest
import requests
from fastapi.testclient import TestClient


@dataclass
class GenerationParams:
    max_tokens: int = 32000
    temperature: float = 0.6
    top_p: float = 1.0
    top_k: int = -1
    repetition_penalty: float = 1.0


@dataclass
class GenerationStats:
    num_tokens: int
    latency: float = 0.25
    ttft: float = 0.05
    tps: float = 4.0


@dataclass
class GenerationResult:
    backend: str
    prompt: str
    output_text: str
    stats: GenerationStats


class Tokenizer:
    chat_template = "fixture-template"

    def encode(self, text, add_special_tokens=False):
        return list(range(len(text)))

    def apply_chat_template(self, messages, **kwargs):
        return json.dumps(messages, ensure_ascii=False, sort_keys=True) + "\nassistant:"


class Generator:
    backend = "transformers"

    def __init__(self):
        self.tokenizer = Tokenizer()
        self.outputs = deque()
        self.stream_chunks = ["Hello", " world"]
        self.calls = []
        self.closed = False

    async def generate(self, prompt, params, **kwargs):
        self.calls.append({"prompt": prompt, "params": params, "kwargs": kwargs})
        output = self.outputs.popleft() if self.outputs else "Fixture answer"
        if isinstance(output, Exception):
            raise output
        return GenerationResult(
            self.backend,
            prompt,
            output,
            GenerationStats(len(self.tokenizer.encode(output))),
        )

    async def stream_generate(self, prompt, params, **kwargs):
        self.calls.append(
            {"prompt": prompt, "params": params, "kwargs": kwargs, "stream": True}
        )
        for chunk in self.stream_chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    async def close(self):
        self.closed = True


@pytest.fixture
def inference_app(monkeypatch, tmp_path):
    for key in list(__import__("os").environ):
        if key.startswith(
            (
                "IGOREK_INFERENCE_",
                "CONTEXT_COMPRESSION_",
                "OPENROUTER_",
                "FACTORY_OPENROUTER_",
            )
        ):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("IGOREK_API_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("CONTEXT_COMPRESSION_ENABLED", "0")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("FACTORY_DATA_DIR", "IGOREK_FACTORY_HOME"):
        monkeypatch.setenv(key, str(tmp_path / "data"))

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected external request or process")

    monkeypatch.setattr(request, "urlopen", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", forbidden)

    inference = types.ModuleType("paroquant.inference")
    base = types.ModuleType("paroquant.inference.base")
    package = types.ModuleType("paroquant")
    for module in (inference, base):
        module.GenerationParams = GenerationParams
        module.GenerationStats = GenerationStats
        module.GenerationResult = GenerationResult
    base.BaseGenerator = object
    inference.build_prompt = lambda tokenizer, messages, *args, **kwargs: (
        tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
    )
    inference.create_generator = forbidden
    package.inference = inference
    inference.base = base
    cuda = SimpleNamespace(is_available=lambda: False, empty_cache=Mock())
    torch = types.ModuleType("torch")
    torch.cuda = cuda
    for name, module in [
        ("paroquant", package),
        ("paroquant.inference", inference),
        ("paroquant.inference.base", base),
        ("torch", torch),
    ]:
        monkeypatch.setitem(sys.modules, name, module)
    modules = [
        "server",
        "generator_runtime",
        "openrouter_runtime",
        "context_compression_client",
        "request_state",
        "tool_planner_runtime",
        "anthropic_runtime",
    ]
    qualified = ["factory.api_server_core." + name for name in modules]
    for name in qualified:
        monkeypatch.delitem(sys.modules, name, raising=False)
    api = importlib.import_module("factory.api_server_core.server")
    generator = Generator()
    monkeypatch.setattr(api, "MODEL_PATH", str(tmp_path / "fixture-model"))
    monkeypatch.setattr(api, "MODEL_NAME", "fixture-model")
    monkeypatch.setattr(api, "DEBUG_TRACE", True)
    monkeypatch.setattr(api, "create_runtime_generator", lambda **kwargs: generator)
    yield SimpleNamespace(
        api=api, generator=generator, root=tmp_path, cuda=cuda, params=GenerationParams
    )
    for name in qualified:
        sys.modules.pop(name, None)


@pytest.fixture
def inference_server(inference_app):
    with TestClient(inference_app.api.app, raise_server_exceptions=False) as client:
        inference_app.client = client
        yield inference_app


READ = {
    "name": "Read",
    "description": "Read a local file",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["file_path"],
    },
}
WRITE = {
    "name": "Write",
    "description": "Write a local file",
    "input_schema": {
        "type": "object",
        "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["file_path", "content"],
    },
}
BASH = {
    "name": "Bash",
    "description": "Run a command",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}
EDIT = {
    "name": "Edit",
    "description": "Replace local text",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
}


def body(env, text, tools, **extra):
    return {
        "system": f"Primary working directory: {env.root}",
        "messages": [{"role": "user", "content": text}],
        "tools": tools,
        "max_tokens": 100,
        **extra,
    }


def state(env, payload):
    request = env.api.AnthropicMessagesRequest(**payload)
    normalized = env.api._normalize_anthropic_request(request)
    return env.api._build_turn_state(request, normalized)
