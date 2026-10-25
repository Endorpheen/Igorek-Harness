"""OpenAI SSE framing and cancellation over an isolated loopback HTTP server."""

import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from factory.core import cc_backend_executor as e


def event(value, *, pretty=False, boundary=True):
    raw = json.dumps(value, ensure_ascii=False, indent=2 if pretty else None)
    return (
        "\n".join("data: " + line for line in raw.splitlines())
        + ("\n\n" if boundary else "\n")
    ).encode()


def chunk(text="", *, finish=None, reasoning=None, tools=None):
    delta = {}
    if text:
        delta["content"] = text
    if reasoning:
        delta["reasoning_content"] = reasoning
    if tools is not None:
        delta["tool_calls"] = tools
    choice = {"delta": delta}
    if finish is not None:
        choice["finish_reason"] = finish
    return {"model": "fixture-model", "choices": [choice]}


@pytest.fixture
def wire():
    config = SimpleNamespace(
        chunks=[],
        status=200,
        headers={},
        stall=False,
        requests=[],
        started=threading.Event(),
        release=threading.Event(),
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            config.requests.append(
                {
                    "path": self.path,
                    "body": json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    ),
                }
            )
            self.send_response(config.status)
            self.send_header("Content-Type", "text/event-stream")
            for key, value in config.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.flush()
            config.started.set()
            if config.stall:
                config.release.wait(5)
            try:
                for data in config.chunks:
                    self.wfile.write(data)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    config.url = f"http://127.0.0.1:{server.server_port}/v1/chat/completions?fixture=1"
    try:
        yield config
    finally:
        config.release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


def call(wire, **kwargs):
    return e._http_post_streaming_openai(
        wire.url,
        {"model": "fixture-model", "messages": []},
        {"Content-Type": "application/json"},
        3,
        **kwargs,
    )[0]


def test_multiline_sse_data_is_one_json_event(wire):
    wire.chunks = [
        event(chunk("Привет 🐙", finish="stop"), pretty=True),
        b"data: [DONE]\n\n",
    ]
    result = call(wire)
    assert result["choices"][0]["message"]["content"] == "Привет 🐙"
    assert result["choices"][0]["finish_reason"] == "stop"


@pytest.mark.parametrize("boundary", [False, True])
def test_multiline_tool_arguments_are_preserved_at_boundary_or_eof(wire, boundary):
    tool = {
        "index": 0,
        "id": "call-fixture",
        "type": "function",
        "function": {"name": "Read", "arguments": '{"file_path":"fixture.py"}'},
    }
    wire.chunks = [
        event(chunk(finish="tool_calls", tools=[tool]), pretty=True, boundary=boundary)
    ]
    result = call(wire)
    actual = result["choices"][0]["message"]["tool_calls"][0]
    assert actual["id"] == "call-fixture" and actual["function"]["name"] == "Read"
    assert json.loads(actual["function"]["arguments"]) == {"file_path": "fixture.py"}


@pytest.mark.parametrize(
    "error",
    [
        {"message": "fixture provider failure", "code": "server_error"},
        "fixture provider failure",
        {},
    ],
)
def test_provider_error_never_returns_partial_text_or_tools_as_success(wire, error):
    partial = {
        "index": 0,
        "id": "partial-call",
        "function": {"name": "Write", "arguments": '{"file_path":"unfinished'},
    }
    wire.chunks = [
        event(chunk("partial answer", tools=[partial])),
        event({"error": error}),
        b"data: [DONE]\n\n",
    ]
    result = call(wire)
    assert result.get("error") and "choices" not in result
    assert result["error_code"] == "STREAM.INTERRUPTED"
    assert result["diagnostic"]["phase"] == "sse_event"
    if isinstance(error, dict) and error.get("code"):
        assert result["provider_error_code"] == "server_error"


def test_provider_stream_error_message_is_redacted(wire):
    wire.chunks = [
        event(
            {
                "error": {
                    "message": "token=synthetic-fixture-secret",
                    "code": "server_error",
                }
            }
        ),
        b"data: [DONE]\n\n",
    ]
    result = call(wire)
    assert result.get("error") and "synthetic-fixture-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "ending", [b"data: [DONE]\n\n", b"data:[DONE]\n", b"data: [DONE]"]
)
def test_done_marker_and_comments_remain_compatible(wire, ending):
    wire.chunks = [
        b": keepalive\n\nevent: message\n",
        event(chunk("fixture text")),
        ending,
    ]
    result = call(wire)
    assert result["choices"][0]["message"]["content"] == "fixture text"


def test_arguments_accumulate_by_index_and_usage_does_not_replace_content(
    wire, tmp_path
):
    tools = [
        {
            "index": 2,
            "id": "two",
            "function": {"name": "Read", "arguments": '{"file_path":'},
        },
        {
            "index": 0,
            "id": "zero",
            "function": {"name": "Bash", "arguments": '{"command":"pwd"}'},
        },
    ]
    wire.chunks = [
        event(chunk("answer", reasoning="fixture thought", tools=tools)),
        event(
            chunk(
                tools=[{"index": 2, "function": {"arguments": '"file.py"}'}}],
                finish="tool_calls",
            )
        ),
        event({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 5}}),
        b"data: [DONE]\n\n",
    ]
    result = call(
        wire, trace_dir=str(tmp_path), round_num=2, parent_task_id="fixture-parent"
    )
    message = result["choices"][0]["message"]
    assert (
        message["content"] == "answer"
        and message["reasoning_content"] == "fixture thought"
    )
    assert [tool["id"] for tool in message["tool_calls"]] == ["zero", "two"]
    assert json.loads(message["tool_calls"][1]["function"]["arguments"]) == {
        "file_path": "file.py"
    }
    assert result["usage"] == {"prompt_tokens": 7, "completion_tokens": 5}
    rows = [
        json.loads(line)
        for line in (tmp_path / "cc_backend_trace.jsonl").read_text().splitlines()
    ]
    assert {row["kind"] for row in rows} == {
        "model.text_delta",
        "model.reasoning_delta",
    }
    assert all(
        row["round"] == 3 and row["parent_task_id"] == "fixture-parent" for row in rows
    )


def test_stream_request_preserves_input_and_query(wire):
    wire.chunks = [event(chunk("complete", finish="stop"))]
    payload = {
        "model": "fixture-model",
        "messages": [],
        "stream_options": {"include_usage": False, "include_obfuscation": False},
    }
    saved = copy.deepcopy(payload)
    result, _ = e._http_post_streaming_openai(wire.url, payload, {}, 3)
    assert "choices" in result and payload == saved
    assert wire.requests[0]["path"] == "/v1/chat/completions?fixture=1"
    assert wire.requests[0]["body"]["stream_options"] == saved["stream_options"]
    assert wire.requests[0]["body"]["stream"] is True


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "AUTH.REJECTED"),
        (429, "HTTP.RATE_LIMITED"),
        (503, "HTTP.SERVICE_UNAVAILABLE"),
    ],
)
def test_http_error_keeps_safe_diagnostic_boundary(wire, status, code):
    wire.status = status
    wire.headers = {"x-request-id": "req_fixture12345678", "retry-after": "2"}
    wire.chunks = [b"token=synthetic-fixture-secret"]
    result = call(wire)
    assert result["error_code"] == code and result["http_status"] == status
    assert result["diagnostic"]["phase"] == "http_body"
    assert result["diagnostic"]["provider_request_id"] == "req_fixture12345678"
    assert result["diagnostic"]["retry_after_s"] == 2
    assert "synthetic-fixture-secret" not in json.dumps(result)


def test_cancel_wakes_a_real_socket_waiting_for_first_token(wire, monkeypatch):
    wire.stall = True
    entered = threading.Event()
    original = e.http.client.HTTPResponse.readline

    def readline(response, *args, **kwargs):
        entered.set()
        return original(response, *args, **kwargs)

    monkeypatch.setattr(e.http.client.HTTPResponse, "readline", readline)
    cancelled = threading.Event()
    result = []
    thread = threading.Thread(
        target=lambda: result.append(call(wire, cancel_check=cancelled.is_set)),
        daemon=True,
    )
    thread.start()
    try:
        assert wire.started.wait(2) and entered.wait(2)
        start = time.monotonic()
        cancelled.set()
        thread.join(2.5)
        assert not thread.is_alive() and time.monotonic() - start < 2.5
        assert result[0].get("__interrupted__") and "choices" not in result[0]
    finally:
        wire.release.set()
        thread.join(4)


def test_cancel_callback_failure_does_not_discard_a_valid_stream(wire):
    wire.chunks = [event(chunk("answer", finish="stop"))]

    def broken():
        raise RuntimeError("fixture callback failed")

    assert (
        call(wire, cancel_check=broken)["choices"][0]["message"]["content"] == "answer"
    )


@pytest.mark.parametrize("tail", [[], [event(chunk("partial"))]])
def test_eof_without_terminal_event_never_exposes_a_success(wire, tail):
    wire.chunks = tail
    result = call(wire)
    assert result["error_code"] == "STREAM.INTERRUPTED" and "choices" not in result
