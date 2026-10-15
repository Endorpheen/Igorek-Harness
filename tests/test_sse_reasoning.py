"""Tests for SSE streaming parsing with reasoning_content (DeepSeek thinking mode)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sse_streams"


def _load_sse_fixture(name: str) -> list[bytes]:
    """Load SSE fixture into readline-compatible chunks.

    Real readline() returns b"data: ...\n" for data lines, b"\n" for blank
    lines (event separators), and b"" only for EOF.
    """
    raw = (FIXTURES_DIR / name).read_bytes()
    chunks: list[bytes] = []
    for line in raw.split(b"\n"):
        if line == b"":
            chunks.append(b"\n")
        else:
            chunks.append(line + b"\n")
    return chunks


def _make_readline(chunks: list[bytes]):
    it = iter(chunks)

    def readline():
        try:
            return next(it)
        except StopIteration:
            return b""

    return readline


class TestSSEReasoningContent(unittest.TestCase):
    """Test that _http_post_streaming_openai extracts reasoning_content from SSE."""

    @classmethod
    def setUpClass(cls):
        from factory.core import cc_backend_executor
        cls.module = cc_backend_executor

    def _run_streaming(self, fixture_name: str):
        chunks = _load_sse_fixture(fixture_name)

        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_conn.getresponse.return_value = mock_resp
        mock_resp.readline = _make_readline(chunks)
        mock_resp._fp = None

        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            body, lat_ms = self.module._http_post_streaming_openai(
                url="https://api.example.com/chat/completions",
                payload={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "test"}]},
                headers={},
                timeout_s=30,
            )
        return body, lat_ms

    def test_reasoning_and_tools_extracted(self):
        """reasoning_content + text + tool_calls all extracted from SSE."""
        body, _ = self._run_streaming("deepseek_reasoning_and_tools.txt")

        msg = body["choices"][0]["message"]
        self.assertEqual(msg["content"], "Выполняю команду.")
        self.assertEqual(msg["reasoning_content"], "Давай подумаю об этом.")
        self.assertEqual(len(msg["tool_calls"]), 1)
        self.assertEqual(msg["tool_calls"][0]["function"]["name"], "Bash")

        args = json.loads(msg["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(args, {"command": "ls"})

    def test_reasoning_roundtrip_through_anthropic(self):
        """reasoning_content survives: SSE -> OpenAI -> Anthropic -> OpenAI."""
        body, _ = self._run_streaming("deepseek_reasoning_only.txt")

        msg = body["choices"][0]["message"]
        self.assertEqual(msg["reasoning_content"], "Анализирую запрос.")
        self.assertEqual(msg["content"], "Вот ответ.")

        anthropic = self.module._openai_response_to_anthropic(body, set())
        reasoning_blocks = [b for b in anthropic["content"] if b.get("type") == "__reasoning"]
        text_blocks = [b for b in anthropic["content"] if b.get("type") == "text"]
        self.assertEqual(len(reasoning_blocks), 1)
        self.assertEqual(reasoning_blocks[0]["text"], "Анализирую запрос.")
        self.assertEqual(len(text_blocks), 1)
        self.assertEqual(text_blocks[0]["text"], "Вот ответ.")

        openai_msgs = self.module._anthropic_messages_to_openai([
            {"role": "assistant", "content": anthropic["content"]}
        ])
        self.assertEqual(len(openai_msgs), 1)
        self.assertEqual(openai_msgs[0]["role"], "assistant")
        self.assertEqual(openai_msgs[0]["reasoning_content"], "Анализирую запрос.")
        self.assertEqual(openai_msgs[0]["content"], "Вот ответ.")

    def test_no_reasoning_still_works(self):
        """When no reasoning_content in SSE, response has no reasoning_content key."""
        chunks = [
            b'data: {"id":"test3","choices":[{"index":0,"delta":{"content":"hello"},"finish_reason":null}]}\n',
            b'\n',
            b'data: {"id":"test3","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n',
            b'\n',
            b'data: [DONE]\n',
            b'\n',
        ]

        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_conn.getresponse.return_value = mock_resp
        mock_resp.readline = _make_readline(chunks)
        mock_resp._fp = None

        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            body, _ = self.module._http_post_streaming_openai(
                url="https://api.example.com/chat/completions",
                payload={"model": "test", "messages": []},
                headers={},
                timeout_s=30,
            )

        msg = body["choices"][0]["message"]
        self.assertEqual(msg["content"], "hello")
        self.assertNotIn("reasoning_content", msg)

    def test_reasoning_deltas_are_traced_once_during_stream(self):
        chunks = _load_sse_fixture("deepseek_reasoning_only.txt")
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_conn.getresponse.return_value = mock_resp
        mock_resp.readline = _make_readline(chunks)
        mock_resp._fp = None

        with tempfile.TemporaryDirectory() as tmp, patch(
            "http.client.HTTPSConnection", return_value=mock_conn
        ):
            self.module._http_post_streaming_openai(
                url="https://api.example.com/chat/completions",
                payload={"model": "deepseek-v4-flash", "messages": []},
                headers={},
                timeout_s=30,
                trace_dir=tmp,
            )
            events = [
                json.loads(line)
                for line in Path(tmp, "cc_backend_trace.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        reasoning = [event for event in events if event["kind"] == "model.reasoning_delta"]
        self.assertEqual([event["text"] for event in reasoning], ["Анализирую ", "запрос."])


if __name__ == "__main__":
    unittest.main()
