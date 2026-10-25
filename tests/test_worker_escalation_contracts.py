"""Escalation and telemetry contracts with synthetic executor output."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import worker as w
from tests.test_worker_execution_contracts import worker_factory as worker_factory


class ImmediateThread:
    def __init__(self, *, target, **kwargs):
        self.target = target

    def start(self):
        self.target()

    def is_alive(self):
        return False


@pytest.fixture
def escalation(worker_factory, monkeypatch, tmp_path):
    worker = worker_factory()
    monkeypatch.setattr(w.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        w, "resolve_codex_launch_settings", lambda _: {"env": {"FIXTURE_ENV": "value"}}
    )
    fixture = Path(
        "tests/fixtures/codex_events/openrouter_handoff_probe.jsonl"
    ).read_text()
    worker_factory.security.execute_command.return_value = (
        SimpleNamespace(returncode=0, stdout=fixture, stderr=""),
        "escalated-security",
    )
    return SimpleNamespace(
        worker=worker,
        security=worker_factory.security,
        workdir=str(tmp_path),
        fixture=fixture,
    )


def invoke(env, **overrides):
    return env.worker._execute_codex_escalation(
        task={"task_id": "escalation-fixture", "stage": "main", "metadata": {}},
        workdir=env.workdir,
        route=SimpleNamespace(
            provider="openai-codex", model="fixture-model", reason="fixture escalation"
        ),
        handoff_text="Finish the fixture task.",
        skills_text="  fixture skill  ",
        foundation_context="  fixture foundation  ",
        timeout_s=5,
        start_time=None,
        **overrides,
    )


def test_escalation_parses_actual_jsonl_usage_tools_and_security(escalation):
    result = invoke(escalation)
    assert result["status"] == "ok"
    assert result["output"]["text"] == "Привет, Игорь!"
    assert result["usage"]["prompt"] == 52464
    assert result["usage"]["completion"] == 1031
    assert result["usage_ext"]["cached_input_tokens"] == 4480
    assert result["security"] == {
        "runtime_mode": "audit",
        "session_id": "escalated-security",
    }
    assert result["smart_routing"]["reason"] == "fixture escalation"
    assert len(result["codex_tool_events"]) == 2
    assert result["codex_tool_events"][0]["name"] == "exec_command"
    packet = escalation.security.execute_command.call_args.kwargs
    assert packet["env"]["FIXTURE_ENV"] == "value" and packet["backend"] == "codex"
    assert packet["cwd"] == escalation.workdir and packet["timeout_s"] == 5
    assert packet["task_metadata"]["primary_backend"] == "cc-backend"
    assert "fixture skill" in packet["input_payload"]
    assert "fixture foundation" in packet["input_payload"]
    assert packet["command"].count("--json") == 1


@pytest.mark.parametrize("returncode,expected", [(0, "ok"), (9, "fail")])
def test_escalation_plain_output_fallback_does_not_invent_usage(
    escalation, returncode, expected
):
    escalation.security.execute_command.return_value = (
        SimpleNamespace(
            returncode=returncode,
            stdout="plain fixture answer",
            stderr="fixture stderr",
        ),
        "fixture",
    )
    result = invoke(escalation)
    assert result["status"] == expected
    assert result["output"] == {
        "text": "plain fixture answer",
        "stderr": "fixture stderr",
    }
    assert result["usage"]["prompt"] == result["usage"]["completion"] == 0
    assert "usage_ext" not in result and "codex_tool_events" not in result


@pytest.mark.parametrize(
    "failure,expected",
    [
        (subprocess.TimeoutExpired("fixture", 5), "Escalation timeout after 5s"),
        (
            OSError("fixture missing binary"),
            "Escalation failed: fixture missing binary",
        ),
    ],
)
def test_escalation_failure_has_no_false_success(escalation, failure, expected):
    escalation.security.execute_command.side_effect = failure
    result = invoke(escalation)
    assert result["status"] == "fail" and result["error"] == expected
    assert result["usage"]["prompt"] == result["usage"]["completion"] == 0


@pytest.mark.parametrize(
    "command", [["codex", "exec"], ["fixture-launcher"], ["codex", "exec", "--json"]]
)
def test_escalation_json_flag_is_added_once(escalation, monkeypatch, command):
    monkeypatch.setattr(w, "build_backend_command", lambda *_, **__: list(command))
    invoke(escalation)
    actual = escalation.security.execute_command.call_args.kwargs["command"]
    assert actual.count("--json") == 1
    if "exec" in actual:
        assert actual[actual.index("exec") + 1] == "--json"


def test_escalation_stdout_callback_records_real_tool_events(escalation, monkeypatch):
    append = Mock()
    monkeypatch.setattr(w, "_append_codex_trace_event", append)
    original = escalation.security.execute_command.return_value

    def run(**packet):
        for line in escalation.fixture.splitlines():
            packet["stdout_line_callback"](line)
        return original

    escalation.security.execute_command.side_effect = run
    invoke(escalation)
    assert append.call_count >= 2
    assert all(
        call.args[0]
        == Path(w.WORKSPACE_ROOT) / "temp_execution_escalati" / "cc_backend_trace.jsonl"
        for call in append.call_args_list
    )
    assert {call.args[1]["kind"] for call in append.call_args_list} == {
        w.CcTraceEvent.TOOL_REQUEST_BATCH,
        w.CcTraceEvent.TOOL_RESULT,
    }


def test_escalation_progress_waits_for_completion_without_relaunch(
    escalation, monkeypatch, capsys
):
    class DelayedThread(ImmediateThread):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.pending = True
            self.joins = 0

        def start(self):
            pass

        def is_alive(self):
            return self.pending

        def join(self, timeout):
            assert timeout == 15
            self.joins += 1
            if self.joins == 2:
                self.target()
                self.pending = False

    monkeypatch.setattr(w.threading, "Thread", DelayedThread)
    assert invoke(escalation)["status"] == "ok"
    assert "still running" in capsys.readouterr().out
    escalation.security.execute_command.assert_called_once()


@pytest.mark.parametrize("backend", ["codex", "claude"])
@pytest.mark.parametrize(
    "event,expected",
    [
        ({"type": "mcp.error", "message": " fixture\n failure "}, "mcp_error"),
        ({"type": "tool_use", "name": "fixture_mcp"}, "mcp_tool_call"),
        ({"type": "mcp.ready"}, "mcp_signal"),
    ],
)
def test_mcp_telemetry_preserves_kind_and_original_event(
    worker_factory, backend, event, expected
):
    worker = worker_factory()
    collect = getattr(worker, f"_collect_{backend}_mcp_telemetry")
    result = collect(
        task_id="fixture",
        stdout_text="noise\n" + json.dumps(event),
        stderr_text="",
        enabled_servers=[],
    )
    assert len(result) == 1
    assert result[0]["kind"] == expected and result[0]["event"] == event
    assert result[0]["task_id"] == "fixture"
    if backend == "codex" and "message" in event:
        assert result[0]["summary"] == "mcp.error | fixture failure"


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_mcp_telemetry_ignores_unrelated_events_and_accepts_server_hint(
    worker_factory, backend
):
    worker = worker_factory()
    collect = getattr(worker, f"_collect_{backend}_mcp_telemetry")
    events = [
        {"type": "assistant", "message": "normal"},
        {"type": "", "message": "mcp"},
        {"type": "connected", "server": "FIXTURE"},
    ]
    result = collect(
        task_id="fixture",
        stdout_text="\n".join(map(json.dumps, events)),
        stderr_text="",
        enabled_servers=["fixture", ""],
    )
    assert [r["source_event_type"] for r in result] == ["connected"]
    assert result[0]["kind"] == "mcp_signal"
    assert (
        collect(
            task_id="fixture",
            stdout_text="not JSON",
            stderr_text="",
            enabled_servers=[],
        )
        == []
    )


def test_claude_result_error_is_telemetry_failure(worker_factory):
    worker = worker_factory()
    event = {"type": "result", "is_error": True, "parent_tool_use_id": "fixture"}
    result = worker._collect_claude_mcp_telemetry(
        task_id="fixture",
        stdout_text=json.dumps(event),
        stderr_text="",
        enabled_servers=[],
    )
    assert result[0]["kind"] == "mcp_error"


@pytest.mark.parametrize(
    "agents,claude,expected",
    [
        ("agent instructions", "claude fallback", "agent instructions"),
        (None, "claude fallback", "claude fallback"),
        ("", "claude fallback", ""),
        (None, None, ""),
    ],
)
def test_project_instructions_prefer_agents_without_duplicate_fallback(
    tmp_path, agents, claude, expected
):
    for name, value in [("AGENTS.md", agents), ("CLAUDE.md", claude)]:
        if value is not None:
            (tmp_path / name).write_text(value, encoding="utf-8")
    result = w._read_project_instructions(str(tmp_path))
    if expected:
        assert expected in result and "already loaded" in result
        assert ("Project Configuration (AGENTS.md)" in result) == (agents is not None)
    else:
        assert result == ""
    if agents is not None:
        assert "claude fallback" not in result


def test_unreadable_project_instructions_do_not_fail_task(tmp_path, monkeypatch):
    path = tmp_path / "AGENTS.md"
    path.write_text("fixture")
    original = Path.read_text

    def read(self, *args, **kwargs):
        if self == path:
            raise PermissionError("fixture denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert w._read_project_instructions(str(tmp_path)) == ""


def test_rollout_monitor_polls_once_drains_tail_and_deduplicates(tmp_path, monkeypatch):
    sessions = tmp_path / "codex/sessions"
    sessions.mkdir(parents=True)
    path = sessions / "rollout-fixture.jsonl"
    call = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "fixture_tool",
            "call_id": "fixture-call",
            "arguments": "{}",
        },
    }
    result = {
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": "fixture-call",
            "output": "fixture done",
        },
    }
    path.write_text(json.dumps(call) + "\n" + json.dumps(call) + "\n")
    trace = tmp_path / "trace.jsonl"

    class StopAfterPoll:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, timeout):
            assert timeout == 0.2
            with path.open("a") as handle:
                handle.write(json.dumps(result) + "\n")
            self.stopped = True

    monkeypatch.setattr(w.threading, "Event", StopAfterPoll)
    monkeypatch.setattr(w.threading, "Thread", ImmediateThread)
    lock = w.threading.Lock()
    seen = set()
    handle = w._start_codex_rollout_trace_monitor(
        codex_home=str(sessions.parent),
        trace_path=trace,
        started_at=0,
        seen_payloads=seen,
        seen_lock=lock,
    )
    assert handle is not None and handle[0].is_set()
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [record["kind"] for record in records] == [
        w.CcTraceEvent.TOOL_REQUEST_BATCH,
        w.CcTraceEvent.TOOL_RESULT,
    ]
    assert records[1]["tool_use_id"] == "fixture-call" and len(seen) == 2


@pytest.mark.parametrize("case", ["old", "unreadable"])
def test_rollout_monitor_skips_unavailable_records_without_false_events(
    tmp_path, monkeypatch, case
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "rollout-fixture.jsonl"
    path.write_text(
        '{"type":"response_item","payload":{"type":"function_call","name":"fixture"}}\n'
    )
    start = path.stat().st_mtime + 10 if case == "old" else 0
    if case == "unreadable":
        original = Path.open

        def denied(self, *args, **kwargs):
            if self == path:
                raise PermissionError("fixture denied")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied)

    class StopAfterPoll:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, timeout):
            self.stopped = True

    monkeypatch.setattr(w.threading, "Event", StopAfterPoll)
    monkeypatch.setattr(w.threading, "Thread", ImmediateThread)
    trace = tmp_path / "trace.jsonl"
    seen = set()
    handle = w._start_codex_rollout_trace_monitor(
        codex_home=str(tmp_path),
        trace_path=trace,
        started_at=start,
        seen_payloads=seen,
        seen_lock=w.threading.Lock(),
    )
    assert handle is not None and not trace.exists() and not seen


def test_rollout_monitor_without_home_does_not_create_reader():
    assert (
        w._start_codex_rollout_trace_monitor(
            codex_home=None,
            trace_path=Path("unused"),
            started_at=0,
            seen_payloads=set(),
            seen_lock=w.threading.Lock(),
        )
        is None
    )
