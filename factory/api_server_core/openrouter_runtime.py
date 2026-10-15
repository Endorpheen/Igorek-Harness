"""OpenRouter backend — proxies requests through OpenRouter's OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator

import requests
from paroquant.inference.base import BaseGenerator, GenerationParams, GenerationResult, GenerationStats

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
FACTORY_OPENROUTER_API_KEY_ENV = "FACTORY_OPENROUTER_API_KEY"


class OpenRouterTokenizer:
    """Rough tokenizer for token counting — estimates based on character length."""

    chat_template = "openrouter_proxy"

    @staticmethod
    def encode(text: str, add_special_tokens: bool = False) -> list[int]:
        if not text:
            return []
        estimated = max(1, len(text) // 4)
        return list(range(estimated))

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool = False,
        add_generation_prompt: bool = True,
        enable_thinking: bool = False,
    ) -> str | list[int]:
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"{role}: {content}")
        prompt = "\n".join(parts)
        if add_generation_prompt:
            prompt += "\nassistant: "
        if tokenize:
            return self.encode(prompt, add_special_tokens=False)
        return prompt


def _resolve_api_key() -> str:
    key = os.environ.get(OPENROUTER_API_KEY_ENV, "").strip()
    if key:
        return key
    key = os.environ.get(FACTORY_OPENROUTER_API_KEY_ENV, "").strip()
    if key:
        return key
    raise RuntimeError(
        f"OpenRouter API key not found. Set {OPENROUTER_API_KEY_ENV} or "
        f"{FACTORY_OPENROUTER_API_KEY_ENV} environment variable."
    )


def _check_response_status(response: requests.Response) -> None:
    if response.status_code == 401:
        raise RuntimeError("OpenRouter: invalid API key")
    if response.status_code == 402:
        raise RuntimeError("OpenRouter: insufficient credits")
    if response.status_code == 429:
        raise RuntimeError("OpenRouter: rate limited")
    response.raise_for_status()


class OpenRouterGenerator(BaseGenerator):
    """Generator that proxies to OpenRouter's chat completions API."""

    backend = "openrouter"

    def __init__(self, *, model: str) -> None:
        self.model = model
        self.api_key = _resolve_api_key()
        self.tokenizer = OpenRouterTokenizer()
        self._session: requests.Session | None = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/igorek-agentfactory",
                }
            )
        return self._session

    def _build_messages(
        self, prompt: str, messages: list[dict[str, object]] | None = None
    ) -> list[dict[str, str]]:
        if messages:
            return [
                {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
                for m in messages
            ]
        return [{"role": "user", "content": prompt}]

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        params: GenerationParams,
        *,
        stream: bool = False,
    ) -> dict[str, object]:
        return {
            "model": self.model,
            "messages": messages,
            "max_tokens": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "stream": stream,
        }

    def _sync_request(self, payload: dict[str, object]) -> dict[str, object]:
        response = self.session.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            json=payload,
            timeout=(10, 300),
        )
        _check_response_status(response)
        return response.json()

    def _sync_stream_request(self, payload: dict[str, object]) -> str:
        response = self.session.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            json=payload,
            timeout=(10, 300),
            stream=True,
        )
        _check_response_status(response)
        buffer = ""
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            delta = choices[0].get("delta")
            if not isinstance(delta, dict):
                continue
            token = delta.get("content")
            if token:
                buffer += token
        return buffer

    async def generate(
        self,
        prompt: str,
        params: GenerationParams,
        on_text=None,
        messages: list[dict[str, object]] | None = None,
    ) -> GenerationResult:
        start = time.perf_counter()
        oai_messages = self._build_messages(prompt, messages)
        payload = self._build_payload(oai_messages, params, stream=False)

        body = await asyncio.to_thread(self._sync_request, payload)

        content = ""
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message")
            if isinstance(msg, dict):
                content = str(msg.get("content") or "")

        usage = body.get("usage", {})
        output_tokens = int(usage.get("completion_tokens", 0))
        if output_tokens <= 0:
            output_tokens = max(1, len(content) // 4)
        latency = time.perf_counter() - start

        if on_text and content:
            on_text(content)

        return GenerationResult(
            backend=self.backend,
            prompt=prompt,
            output_text=content,
            stats=GenerationStats(
                num_tokens=output_tokens,
                latency=latency,
                ttft=None,
                tps=output_tokens / latency if latency > 0 else 0.0,
            ),
        )

    async def stream_generate(
        self,
        prompt: str,
        params: GenerationParams,
        messages: list[dict[str, object]] | None = None,
    ) -> AsyncIterator[str]:
        oai_messages = self._build_messages(prompt, messages)
        payload = self._build_payload(oai_messages, params, stream=True)
        content = await asyncio.to_thread(self._sync_stream_request, payload)
        if content:
            yield content

    async def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
