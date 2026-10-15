from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path

import requests
from paroquant.inference import create_generator
from paroquant.inference.base import BaseGenerator, GenerationParams, GenerationResult, GenerationStats


class ContextOverflowError(Exception):
    """Raised when the prompt exceeds the model's context window."""

    def __init__(self, message: str, *, prompt_chars: int = 0, ctx_size: int = 0) -> None:
        super().__init__(message)
        self.prompt_chars = prompt_chars
        self.ctx_size = ctx_size


DEFAULT_LLAMA_SERVER_CANDIDATES = (
    str(Path.home() / ".local/bin/igorek-llama-server"),
    "llama-server",
)


QWEN_STOP_SEQUENCES = [
    "<|im_end|>",
    "<|endoftext|>",
    "<|fim_pad|>",
    "<|repo_name|>",
    "<|file_sep|>",
]


def is_qwen_model_path(model_path: str) -> bool:
    lowered = model_path.lower()
    return "qwen3.6" in lowered or "qwen" in lowered


def resolve_llama_server_bin(requested_bin: str) -> str:
    candidate = requested_bin.strip()
    if candidate and candidate != "llama-server":
        return candidate
    for path in DEFAULT_LLAMA_SERVER_CANDIDATES:
        if path == "llama-server":
            return path
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return "llama-server"


def resolve_runtime_backend(model_path: str, requested_backend: str) -> str:
    backend = requested_backend.lower().strip()
    if backend == "openrouter":
        return "openrouter"
    if backend == "auto":
        return "gguf" if model_path.lower().endswith(".gguf") else "transformers"
    if backend not in {"transformers", "gguf"}:
        raise ValueError(f"Unsupported backend: {requested_backend}")
    return backend


def _extra_args_include_fit(extra_args: list[str]) -> bool:
    return any(arg == "-fit" or arg.startswith("-fit=") for arg in extra_args)


def _extra_args_override_gpu_layers(extra_args: list[str]) -> bool:
    return any(
        arg in {"-ngl", "--n-gpu-layers"} or arg.startswith("-ngl=") or arg.startswith("--n-gpu-layers=")
        for arg in extra_args
    )


def create_runtime_generator(
    *,
    backend: str,
    model_path: str,
    llama_server_bin: str = "llama-server",
    llama_server_host: str = "127.0.0.1",
    llama_server_port: int = 8011,
    llama_ctx_size: int = 16384,
    llama_gpu_layers: int = 999,
    llama_server_extra_args: list[str] | None = None,
    llama_autodetect_mmproj: bool = True,
    llama_server_command: list[str] | None = None,
) -> BaseGenerator:
    resolved_backend = resolve_runtime_backend(model_path, backend)
    if resolved_backend == "openrouter":
        from factory.api_server_core.openrouter_runtime import OpenRouterGenerator

        return OpenRouterGenerator(model=model_path)
    if resolved_backend == "gguf":
        return LlamaServerGenerator(
            model=model_path,
            server_bin=resolve_llama_server_bin(llama_server_bin),
            host=llama_server_host,
            port=llama_server_port,
            ctx_size=llama_ctx_size,
            gpu_layers=llama_gpu_layers,
            extra_args=llama_server_extra_args or [],
            autodetect_mmproj=llama_autodetect_mmproj,
            custom_command=llama_server_command or [],
        )
    return create_generator("transformers", model_path)


class LlamaServerGenerator(BaseGenerator):
    backend = "gguf"

    def __init__(
        self,
        *,
        model: str,
        server_bin: str,
        host: str,
        port: int,
        ctx_size: int,
        gpu_layers: int,
        extra_args: list[str],
        autodetect_mmproj: bool,
        custom_command: list[str],
    ) -> None:
        self.model_path = str(Path(model).expanduser().resolve())
        self.server_bin = server_bin
        self.host = host
        self.port = int(port)
        self.ctx_size = int(ctx_size)
        self.gpu_layers = int(gpu_layers)
        self.extra_args = list(extra_args)
        self.autodetect_mmproj = bool(autodetect_mmproj)
        self.custom_command = list(custom_command)
        self.base_url = f"http://{self.host}:{self.port}"
        self.is_qwen_model = is_qwen_model_path(self.model_path)
        self.tokenizer = QwenLlamaServerTokenizer() if self.is_qwen_model else LlamaServerTokenizer()
        self.process: subprocess.Popen[str] | None = None
        self._stderr_path: str | None = None
        self._start_server()

    def _start_server(self) -> None:
        if not Path(self.model_path).is_file():
            raise FileNotFoundError(f"GGUF model not found: {self.model_path}")
        retry_ports = [self.port]
        for _ in range(4):
            retry_ports.append(self._pick_free_port(self.host))

        last_error: Exception | None = None
        last_stderr = ""
        for candidate_port in retry_ports:
            if candidate_port <= 0:
                continue
            self.port = candidate_port
            self.base_url = f"http://{self.host}:{self.port}"
            self.process = self._launch_process(candidate_port)
            startup_timeout = int(os.environ.get("LLAMA_SERVER_STARTUP_TIMEOUT", "45"))
            deadline = time.time() + startup_timeout
            while time.time() < deadline:
                if self.process.poll() is not None:
                    last_stderr = self._read_stderr()
                    if "couldn't bind HTTP server socket" in last_stderr:
                        break
                    raise RuntimeError(
                        f"llama-server exited early with code {self.process.returncode}: {last_stderr.strip()}"
                    )
                for path in ("/health", "/v1/models", "/props"):
                    try:
                        response = requests.get(
                            f"{self.base_url}{path}",
                            timeout=2,
                            headers={"Accept": "application/json"},
                        )
                        if response.ok:
                            return
                    except requests.RequestException as exc:
                        last_error = exc
                time.sleep(1)
            else:
                last_stderr = self._read_stderr()
                raise RuntimeError(f"Timed out waiting for llama-server at {self.base_url}: {last_stderr.strip()}") from last_error

        raise RuntimeError(f"Failed to start llama-server after port retries: {last_stderr.strip()}") from last_error

    def _launch_process(self, port: int) -> subprocess.Popen[str]:
        if self._stderr_path and Path(self._stderr_path).exists():
            try:
                Path(self._stderr_path).unlink()
            except OSError:
                pass
        stderr_file = tempfile.NamedTemporaryFile(
            prefix="llama-server-stderr-",
            suffix=".log",
            delete=False,
        )
        self._stderr_path = stderr_file.name
        stderr_file.close()
        if self.custom_command:
            cmd = list(self.custom_command)
        else:
            cmd = [
                self.server_bin,
                "-m",
                self.model_path,
                "--host",
                self.host,
                "--port",
                str(port),
                "-c",
                str(self.ctx_size),
            ]
            if not _extra_args_override_gpu_layers(self.extra_args) and not _extra_args_include_fit(self.extra_args):
                cmd.extend(["-ngl", str(self.gpu_layers)])
            if self.extra_args:
                cmd.extend(self.extra_args)
            if self.autodetect_mmproj:
                # Auto-detect mmproj (multimodal projector) next to model file
                model_dir = Path(self.model_path).parent
                mmproj_candidates = sorted(model_dir.glob("mmproj*.gguf"))
                if mmproj_candidates:
                    cmd.extend(["--mmproj", str(mmproj_candidates[0])])
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=open(self._stderr_path, "w", encoding="utf-8"),
            env=os.environ.copy(),
            text=True,
        )

    def _read_stderr(self) -> str:
        if not self._stderr_path:
            return ""
        try:
            return Path(self._stderr_path).read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _port_in_use(host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                return sock.connect_ex((host, port)) == 0
        except OSError:
            return False

    @staticmethod
    def _pick_free_port(host: str) -> int:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind((host, 0))
                return int(sock.getsockname()[1])
        except OSError:
            return 0

    def _completion_payload(self, prompt: str, params: GenerationParams) -> dict[str, object]:
        payload: dict[str, object] = {
            "prompt": prompt,
            "n_predict": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "repeat_penalty": params.repetition_penalty,
            "stream": False,
        }
        if params.top_k > 0:
            payload["top_k"] = params.top_k
        if self.is_qwen_model:
            payload["stop"] = QWEN_STOP_SEQUENCES
        return payload

    def _completion_request(self, prompt: str, params: GenerationParams) -> dict[str, object]:
        response = requests.post(
            f"{self.base_url}/completion",
            json=self._completion_payload(prompt, params),
            headers={"Content-Type": "application/json"},
            timeout=(10, 600),
        )
        if response.status_code == 400:
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("error", body))[:500]
            except Exception:
                detail = response.text[:500]
            raise ContextOverflowError(
                f"llama-server 400: {detail}",
                prompt_chars=len(prompt),
                ctx_size=self.ctx_size,
            )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(f"llama-server completion error: {payload['error']}")
        if not isinstance(payload, dict):
            raise RuntimeError("llama-server completion returned non-object payload")
        return payload

    @staticmethod
    def _stats_from_payload(payload: dict[str, object], content: str, latency: float) -> GenerationStats:
        timings = payload.get("timings")
        predicted_n = 0
        predicted_per_second = 0.0
        ttft = None
        if isinstance(timings, dict):
            predicted_n = int(timings.get("predicted_n") or 0)
            predicted_per_second = float(timings.get("predicted_per_second") or 0.0)
            prompt_ms = timings.get("prompt_ms")
            if prompt_ms is not None:
                ttft = float(prompt_ms) / 1000.0
        if predicted_n <= 0:
            predicted_n = max(1, len(content.encode("utf-8")) // 4)
        return GenerationStats(
            num_tokens=predicted_n,
            latency=latency,
            ttft=ttft,
            tps=predicted_per_second if predicted_per_second > 0 else (predicted_n / latency if latency > 0 else 0.0),
        )

    def _chat_completion_payload(self, messages: list[dict[str, object]], params: GenerationParams) -> dict[str, object]:
        payload: dict[str, object] = {
            "messages": messages,
            "max_tokens": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "repeat_penalty": params.repetition_penalty,
            "stream": False,
        }
        if params.top_k > 0:
            payload["top_k"] = params.top_k
        return payload

    def _chat_completion_request(self, messages: list[dict[str, object]], params: GenerationParams) -> dict[str, object]:
        response = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json=self._chat_completion_payload(messages, params),
            headers={"Content-Type": "application/json"},
            timeout=(10, 600),
        )
        if response.status_code == 400:
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("error", body))[:500]
            except Exception:
                detail = response.text[:500]
            raise ContextOverflowError(
                f"llama-server chat 400: {detail}",
                prompt_chars=len(str(messages)),
                ctx_size=self.ctx_size,
            )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(f"llama-server chat completion error: {payload['error']}")
        if not isinstance(payload, dict):
            raise RuntimeError("llama-server chat completion returned non-object payload")
        return payload

    @staticmethod
    def _stats_from_chat_payload(payload: dict[str, object], content: str, latency: float) -> GenerationStats:
        timings = payload.get("timings")
        predicted_n = 0
        predicted_per_second = 0.0
        ttft = None
        if isinstance(timings, dict):
            predicted_n = int(timings.get("predicted_n") or 0)
            predicted_per_second = float(timings.get("predicted_per_second") or 0.0)
            prompt_ms = timings.get("prompt_ms")
            if prompt_ms is not None:
                ttft = float(prompt_ms) / 1000.0
        usage = payload.get("usage")
        if isinstance(usage, dict):
            completion_tokens = int(usage.get("completion_tokens") or 0)
            if completion_tokens > 0:
                predicted_n = completion_tokens
        if predicted_n <= 0:
            predicted_n = max(1, len(content.encode("utf-8")) // 4)
        return GenerationStats(
            num_tokens=predicted_n,
            latency=latency,
            ttft=ttft,
            tps=predicted_per_second if predicted_per_second > 0 else (predicted_n / latency if latency > 0 else 0.0),
        )

    async def generate(
        self,
        prompt: str,
        params: GenerationParams,
        on_text=None,
        messages: list[dict[str, object]] | None = None,
    ) -> GenerationResult:
        start = time.perf_counter()
        try:
            if messages:
                payload = await asyncio.to_thread(self._chat_completion_request, messages, params)
                content = ""
                choices = payload.get("choices")
                if isinstance(choices, list) and choices:
                    msg = choices[0].get("message")
                    if isinstance(msg, dict):
                        content = str(msg.get("content") or "")
                stats = self._stats_from_chat_payload(payload, content, time.perf_counter() - start)
            else:
                payload = await asyncio.to_thread(self._completion_request, prompt, params)
                content = str(payload.get("content") or "")
                stats = self._stats_from_payload(payload, content, time.perf_counter() - start)
        except ContextOverflowError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Generator error: {exc}") from exc
        if on_text and content:
            on_text(content)
        return GenerationResult(
            backend=self.backend,
            prompt=prompt,
            output_text=content,
            stats=stats,
        )

    async def stream_generate(self, prompt: str, params: GenerationParams, messages: list[dict[str, object]] | None = None) -> AsyncIterator[str]:
        try:
            if messages:
                payload = await asyncio.to_thread(self._chat_completion_request, messages, params)
                content = ""
                choices = payload.get("choices")
                if isinstance(choices, list) and choices:
                    msg = choices[0].get("message")
                    if isinstance(msg, dict):
                        content = str(msg.get("content") or "")
            else:
                payload = await asyncio.to_thread(self._completion_request, prompt, params)
                content = str(payload.get("content") or "")
        except ContextOverflowError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Generator error: {exc}") from exc
        if content:
            yield content

    async def close(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                await asyncio.to_thread(self.process.wait, 10)
            except Exception:
                self.process.kill()
                await asyncio.to_thread(self.process.wait, 5)
        if self._stderr_path:
            try:
                Path(self._stderr_path).unlink(missing_ok=True)
            except OSError:
                pass


class LlamaServerTokenizer:
    chat_template = "llama_server_gemma4"

    @staticmethod
    def encode(text: str, add_special_tokens: bool = False) -> list[int]:
        if not text:
            return []
        pieces = re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)
        token_count = max(1, len(pieces))
        if add_special_tokens:
            token_count += 1
        return list(range(token_count))

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool = False,
        add_generation_prompt: bool = True,
        enable_thinking: bool = False,
    ) -> str | list[int]:
        del enable_thinking
        rendered: list[str] = ["<bos>"]
        for message in messages:
            role = str(message.get("role", "user")).strip().lower()
            content = str(message.get("content", ""))
            if role == "assistant":
                tag = "model"
            else:
                tag = role or "user"
            rendered.append(f"<|turn>{tag}\n{content}<turn|>\n")
        if add_generation_prompt:
            rendered.append("<|turn>model\n")
        prompt = "".join(rendered)
        if tokenize:
            return self.encode(prompt, add_special_tokens=False)
        return prompt


class QwenLlamaServerTokenizer(LlamaServerTokenizer):
    chat_template = "llama_server_qwen"

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool = False,
        add_generation_prompt: bool = True,
        enable_thinking: bool = False,
    ) -> str | list[int]:
        rendered: list[str] = []
        for message in messages:
            role = str(message.get("role", "user")).strip().lower()
            content = str(message.get("content", ""))
            if role == "assistant":
                tag = "assistant"
            elif role == "system":
                tag = "system"
            else:
                tag = "user"
            rendered.append(f"<|im_start|>{tag}\n{content}<|im_end|>\n")
        if add_generation_prompt:
            rendered.append("<|im_start|>assistant\n")
        prompt = "".join(rendered)
        if tokenize:
            return self.encode(prompt, add_special_tokens=False)
        return prompt
