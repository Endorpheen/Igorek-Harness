from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import requests

from paroquant.inference import GenerationParams, build_prompt

from factory.api_server_core.openrouter_runtime import OPENROUTER_BASE_URL


@dataclass(frozen=True)
class ContextCompressionClientConfig:
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 180.0
    temperature: float = 0.1


class ContextCompressionClient:
    """Side-channel summarizer client for context compression.

    Resolution order:
    1. Explicit OpenAI-compatible endpoint from env/config.
    2. Direct call to local llama-server chat completions for GGUF runtime.
    3. Direct call to OpenRouter when backend=openrouter.
    4. Local generator fallback for transformers/local runtimes.
    """

    def __init__(
        self,
        *,
        generator: Any,
        model_name: str,
        config: ContextCompressionClientConfig,
    ) -> None:
        self.generator = generator
        self.model_name = model_name
        self.config = config

    async def generate_summary(self, prompt: str, *, max_tokens: int) -> str:
        prompt = str(prompt or "").strip()
        if not prompt:
            return ""

        explicit_base_url = str(self.config.base_url or "").strip()
        backend = str(getattr(self.generator, "backend", "") or "").strip().lower()

        if explicit_base_url:
            return await asyncio.to_thread(
                self._request_openai_compatible,
                explicit_base_url.rstrip("/"),
                self.config.model or self.model_name,
                prompt,
                max_tokens,
                self.config.api_key or "",
            )

        if backend == "gguf":
            base_url = str(getattr(self.generator, "base_url", "") or "").strip().rstrip("/")
            if base_url:
                return await asyncio.to_thread(
                    self._request_llama_server,
                    base_url,
                    prompt,
                    max_tokens,
                )

        if backend == "openrouter":
            model = self.config.model or str(getattr(self.generator, "model", "") or self.model_name)
            api_key = str(getattr(self.generator, "api_key", "") or self.config.api_key or "").strip()
            if api_key and model:
                return await asyncio.to_thread(
                    self._request_openrouter,
                    model,
                    prompt,
                    max_tokens,
                    api_key,
                )

        return await self._request_local_generator(prompt, max_tokens)

    def _request_openai_compatible(
        self,
        base_url: str,
        model: str,
        prompt: str,
        max_tokens: int,
        api_key: str,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": self.config.temperature,
                "stream": False,
            },
            timeout=(10, self.config.timeout_seconds),
        )
        response.raise_for_status()
        payload = response.json()
        return self._extract_completion_text(payload)

    def _request_llama_server(self, base_url: str, prompt: str, max_tokens: int) -> str:
        response = requests.post(
            f"{base_url}/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            json={
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": self.config.temperature,
                "stream": False,
            },
            timeout=(10, self.config.timeout_seconds),
        )
        response.raise_for_status()
        payload = response.json()
        return self._extract_completion_text(payload)

    def _request_openrouter(self, model: str, prompt: str, max_tokens: int, api_key: str) -> str:
        response = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/igorek-agentfactory",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": self.config.temperature,
                "stream": False,
            },
            timeout=(10, self.config.timeout_seconds),
        )
        response.raise_for_status()
        payload = response.json()
        return self._extract_completion_text(payload)

    async def _request_local_generator(self, prompt: str, max_tokens: int) -> str:
        tokenizer = getattr(self.generator, "tokenizer", None)
        if tokenizer is None:
            raise RuntimeError("Context compression client: generator tokenizer is unavailable")
        prompt_text = build_prompt(tokenizer, [{"role": "user", "content": prompt}], False)
        result = await self.generator.generate(
            prompt_text,
            GenerationParams(
                max_tokens=max_tokens,
                temperature=self.config.temperature,
                top_p=1.0,
                top_k=-1,
                repetition_penalty=1.0,
            ),
        )
        return str(getattr(result, "output_text", "") or "").strip()

    @staticmethod
    def _extract_completion_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        message = choices[0].get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text") or "")
                    if text:
                        parts.append(text)
            return "\n".join(parts).strip()
        try:
            return json.dumps(content, ensure_ascii=False).strip()
        except Exception:
            return str(content or "").strip()


def build_context_compression_client_config() -> ContextCompressionClientConfig:
    timeout_raw = os.environ.get("CONTEXT_COMPRESSION_TIMEOUT_SECONDS", "").strip()
    try:
        timeout_seconds = float(timeout_raw) if timeout_raw else 180.0
    except Exception:
        timeout_seconds = 180.0
    return ContextCompressionClientConfig(
        model=os.environ.get("CONTEXT_COMPRESSION_MODEL", "").strip() or None,
        base_url=os.environ.get("CONTEXT_COMPRESSION_BASE_URL", "").strip() or None,
        api_key=os.environ.get("CONTEXT_COMPRESSION_API_KEY", "").strip() or None,
        timeout_seconds=max(30.0, timeout_seconds),
        temperature=0.1,
    )
