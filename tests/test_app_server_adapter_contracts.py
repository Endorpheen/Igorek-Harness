"""App-server transport contracts without launching Codex or a model."""

import io
import json
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import codex_app_server_adapter as a


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(
        a.subprocess,
        "Popen",
        Mock(side_effect=AssertionError("Unexpected Codex process")),
    )
    return a.CodexAppServerSession(
        cwd=str(tmp_path),
        model=None,
        model_provider=None,
        env=None,
        developer_instructions=None,
        notification_handler=Mock(),
        server_request_handler=Mock(),
        error_handler=Mock(),
    )


def process():
    return SimpleNamespace(
        stdin=io.StringIO(),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        poll=Mock(return_value=None),
        terminate=Mock(),
        wait=Mock(),
        kill=Mock(),
    )


@pytest.mark.parametrize("result", [None, {}, [1, 2], "Привет", 0])
def test_request_matches_its_response_and_preserves_result(
    adapter, monkeypatch, result
):
    sent = []

    def send(payload):
        sent.append(payload)
        adapter._handle_message({"id": payload["id"], "result": result})

    monkeypatch.setattr(adapter, "_send", send)
    assert adapter.request("fixture", {"key": "value"}, timeout_s=0.1) == result
    assert sent == [{"id": 1, "method": "fixture", "params": {"key": "value"}}]
    assert adapter._pending == {}


def test_request_error_keeps_protocol_detail_and_cleans_pending(adapter, monkeypatch):
    monkeypatch.setattr(
        adapter,
        "_send",
        lambda payload: adapter._handle_message(
            {"id": payload["id"], "error": {"code": -32000, "message": "fixture отказ"}}
        ),
    )
    with pytest.raises(RuntimeError, match="fixture отказ"):
        adapter.request("fixture", {})
    assert adapter._pending == {}


def test_timed_out_request_does_not_leak_pending_entry(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "_send", Mock())
    with pytest.raises(TimeoutError, match="fixture"):
        adapter.request("fixture", {}, timeout_s=0)
    assert adapter._pending == {}


@pytest.mark.parametrize("failure", ["not-running", "broken-pipe"])
def test_send_failure_removes_request_registration(adapter, failure):
    if failure == "broken-pipe":
        adapter._proc = process()
        adapter._proc.stdin = SimpleNamespace(
            write=Mock(side_effect=BrokenPipeError("fixture pipe closed")), flush=Mock()
        )
    with pytest.raises((RuntimeError, BrokenPipeError)):
        adapter.request("fixture", {}, timeout_s=0)
    assert adapter._pending == {}


@pytest.mark.parametrize("invalid", [[1], {}, True, 1.0, "1", None])
def test_malformed_response_id_cannot_consume_another_pending_request(adapter, invalid):
    pending = a._PendingResponse(event=threading.Event())
    adapter._pending[1] = pending
    adapter._handle_response({"id": invalid, "result": "wrong response"})
    assert not pending.event.is_set() and adapter._pending[1] is pending
    adapter._handle_response({"id": 1, "result": "correct response"})
    assert pending.event.is_set() and pending.result == "correct response"


@pytest.mark.parametrize("message", [[], None, 42, "invalid", True])
def test_nonobject_frame_does_not_prevent_next_notification(adapter, message):
    adapter._handle_message(message)
    adapter._handle_message({"method": "fixture/notice", "params": {"text": "ok"}})
    adapter.notification_handler.assert_called_once_with(
        "fixture/notice", {"text": "ok"}
    )
    adapter.error_handler.assert_called_once()


@pytest.mark.parametrize("params", [None, [], "invalid", {"value": 1}])
def test_server_requests_and_notifications_route_separately(adapter, params):
    expected = params if isinstance(params, dict) else {}
    adapter._handle_message(
        {"id": "server-request", "method": "fixture/ask", "params": params}
    )
    adapter._handle_message({"method": "fixture/notice", "params": params})
    adapter._handle_message({})
    adapter.server_request_handler.assert_called_once_with(
        "fixture/ask", "server-request", expected
    )
    adapter.notification_handler.assert_called_once_with("fixture/notice", expected)


def test_wire_send_is_one_unicode_json_line_and_flush(adapter):
    proc = process()
    adapter._proc = proc
    adapter._send({"id": 2, "method": "fixture", "params": {"text": "Привет 🐙"}})
    lines = proc.stdin.getvalue().splitlines()
    assert len(lines) == 1 and "Привет" in lines[0]
    assert json.loads(lines[0])["params"]["text"] == "Привет 🐙"


def test_unexpected_stdout_eof_releases_pending_waiters(adapter):
    proc = process()
    proc.poll.return_value = 23
    adapter._proc = proc
    pending = a._PendingResponse(event=threading.Event())
    adapter._pending[7] = pending
    adapter._read_stdout()
    assert pending.event.is_set() and pending.error
    assert adapter._pending == {}
    assert "closed" in adapter.error_handler.call_args.args[0]


def test_stdout_skips_bad_json_and_continues_to_valid_notification(adapter):
    proc = process()
    proc.stdout = io.StringIO(
        '\n{bad JSON\n{"method":"fixture/notice","params":{"ok":true}}\n'
    )
    adapter._proc = proc
    adapter._read_stdout()
    adapter.notification_handler.assert_called_once_with("fixture/notice", {"ok": True})
    assert "non-JSON" in adapter.error_handler.call_args_list[0].args[0]


def test_stderr_filters_known_loopback_noise_and_suppresses_closed_session(adapter):
    adapter._proc = process()
    adapter._proc.stderr = io.StringIO(
        "\n bwrap: loopback: unavailable\nfixture warning\n"
    )
    adapter._read_stderr()
    assert any(
        "fixture warning" in call.args[0]
        for call in adapter.error_handler.call_args_list
    )
    assert not any(
        "bwrap:" in call.args[0] for call in adapter.error_handler.call_args_list
    )
    adapter.error_handler.reset_mock()
    adapter._closed = True
    adapter._proc.stderr = io.StringIO("fixture warning\n")
    adapter._read_stderr()
    adapter.error_handler.assert_not_called()


@pytest.mark.parametrize("outcome", ["normal", "timeout", "already-exited", "absent"])
def test_close_releases_waiters_and_reaps_owned_process(adapter, outcome):
    proc = process() if outcome != "absent" else None
    if proc:
        proc.poll.return_value = 0 if outcome == "already-exited" else None
        if outcome == "timeout":
            proc.wait.side_effect = [subprocess.TimeoutExpired("fixture", 3), 0]
    adapter._proc = proc
    pending = a._PendingResponse(event=threading.Event())
    adapter._pending[1] = pending
    adapter.close()
    assert adapter._closed and adapter._proc is None
    assert pending.event.is_set() and pending.error and adapter._pending == {}
    if proc and outcome != "already-exited":
        proc.terminate.assert_called_once_with()
        assert proc.wait.call_count == (2 if outcome == "timeout" else 1)
    if proc and outcome == "timeout":
        proc.kill.assert_called_once_with()
    adapter.close()


@pytest.fixture
def startup(adapter, monkeypatch):
    proc = process()
    popen = Mock(return_value=proc)
    monkeypatch.setattr(a.subprocess, "Popen", popen)
    targets = []

    class Thread:
        def __init__(self, target, daemon):
            assert daemon
            targets.append(target)

        def start(self):
            pass

    monkeypatch.setattr(a, "threading", SimpleNamespace(Thread=Thread))
    return adapter, proc, popen, targets


@pytest.mark.parametrize("resume", [False, True])
def test_start_preserves_requested_model_cwd_and_approval_policy(
    startup, monkeypatch, resume
):
    adapter, proc, popen, targets = startup
    adapter.model = "fixture-model"
    adapter.model_provider = "fixture-provider"
    adapter.developer_instructions = "Fixture instruction"
    adapter.env = {"FIXTURE_FLAG": "yes"}
    request = Mock(side_effect=[{}, {"thread": {"id": "fixture-thread"}}])
    monkeypatch.setattr(adapter, "request", request)
    adapter.start(thread_id="previous" if resume else None)
    assert adapter.thread_id == "fixture-thread"
    assert len(targets) == 2
    assert popen.call_args.args == (["codex", "app-server", "--listen", "stdio://"],)
    assert popen.call_args.kwargs["cwd"] == adapter.cwd
    assert popen.call_args.kwargs["env"]["FIXTURE_FLAG"] == "yes"
    assert request.call_args_list[0].args[0] == "initialize"
    method, params = request.call_args_list[1].args
    assert method == ("thread/resume" if resume else "thread/start")
    assert (
        params["model"] == "fixture-model"
        and params["modelProvider"] == "fixture-provider"
    )
    assert params["developerInstructions"] == "Fixture instruction"
    assert params["cwd"] == adapter.cwd and params["approvalPolicy"] == "untrusted"
    assert (
        params["approvalsReviewer"] == "user" and params["sandbox"] == "workspace-write"
    )
    if resume:
        assert params["threadId"] == "previous"
    adapter.start()
    assert popen.call_count == 1


@pytest.mark.parametrize(
    "result", [None, {}, {"thread": []}, {"thread": {}}, {"thread": {"id": ""}}]
)
def test_bad_start_response_closes_process_before_propagating_error(
    startup, monkeypatch, result
):
    adapter, proc, popen, targets = startup
    monkeypatch.setattr(adapter, "request", Mock(side_effect=[{}, result]))
    with pytest.raises(RuntimeError, match="did not return thread id"):
        adapter.start()
    assert adapter._proc is None
    proc.terminate.assert_called_once_with()


def test_initialize_failure_closes_process(startup, monkeypatch):
    adapter, proc, popen, targets = startup
    error = TimeoutError("fixture handshake timeout")
    monkeypatch.setattr(adapter, "request", Mock(side_effect=error))
    with pytest.raises(TimeoutError) as raised:
        adapter.start()
    assert raised.value is error and adapter._proc is None
    proc.terminate.assert_called_once_with()


def test_send_message_requires_thread_and_preserves_unicode(adapter, monkeypatch):
    with pytest.raises(RuntimeError, match="not initialized"):
        adapter.send_user_message("fixture")
    adapter._thread_id = "fixture-thread"
    request = Mock(return_value={"turn": {"id": "fixture-turn"}})
    monkeypatch.setattr(adapter, "request", request)
    assert adapter.send_user_message("Привет 🐙", timeout_s=4) == {
        "turn": {"id": "fixture-turn"}
    }
    method, params = request.call_args.args
    assert method == "turn/start" and params["threadId"] == "fixture-thread"
    assert params["input"] == [
        {"type": "text", "text": "Привет 🐙", "text_elements": []}
    ]
    assert (
        params["approvalPolicy"] == "untrusted"
        and params["approvalsReviewer"] == "user"
    )
    assert request.call_args.kwargs == {"timeout_s": 4}


def test_stderr_eof_does_not_preempt_a_response_still_buffered_on_stdout(adapter):
    proc = process()
    proc.poll.return_value = 23
    adapter._proc = proc
    pending = a._PendingResponse(event=threading.Event())
    adapter._pending[7] = pending
    adapter._read_stderr()
    assert not pending.event.is_set() and adapter._pending[7] is pending
    adapter._handle_response({"id": 7, "result": "buffered response"})
    assert pending.result == "buffered response" and pending.event.is_set()
