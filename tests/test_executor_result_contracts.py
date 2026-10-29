"""Protocol outcomes must not depend on response wording or output length."""
import copy
import json
import pickle
import subprocess

import pytest

from factory.core import cc_backend_executor as executor, cc_backend_tools as tools
from factory.core.fact_checker import check_git_status, check_pwd_output, check_tool_result_fact
from factory.core.session_budget import DECISION_CONTINUE, SessionBudget
from test_executor_tool_loop_edges import loop  # noqa: F401
from test_executor_workflow_guards import response


@pytest.mark.parametrize("api", ["openai", "anthropic"])
@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("shape", ["text", "reasoning", "tool"])
def test_output_limit_cannot_finish_successfully_or_execute_partial_tools(loop, api, remote, shape):
    tool = {"id": "partial-write", "name": "Write", "input": {"file_path": "never.txt", "content": "partial"}}
    body = response(api, "Partial answer", tool=tool if shape == "tool" else None)
    if api == "openai":
        body["choices"][0]["finish_reason"] = "length"
        if shape == "reasoning":
            body["choices"][0]["message"] = {"content": "", "reasoning_content": "partial reasoning"}
    else:
        body["stop_reason"] = "max_tokens"
        if shape == "reasoning":
            body["content"] = [{"type": "thinking", "thinking": "partial reasoning"}]
    run = loop(api, [body], names=["Write"], base_url="https://provider.example.test" if remote else "http://127.0.0.1:1")
    assert run.result["status"] == "fail"
    assert run.result["failure_reason"] == "model_output_limit"
    assert run.result["usage"]["completion"] == 3
    assert run.calls == [] and len(run.requests) == 1
    assert not (run.work / "never.txt").exists()
    assert run.trace[-1]["kind"] == "task.finish" and run.trace[-1]["status"] == "fail"
    if shape == "text":
        assert run.result["partial_output_text"] == "Partial answer"
    else:
        assert "partial reasoning" not in run.result["output_text"]


@pytest.mark.parametrize("api", ["openai", "anthropic"])
def test_finished_text_after_tool_result_is_not_forced_to_repeat_branch(loop, api):
    tool = {"id": "branch", "name": "Bash", "input": {"command": "git branch --show-current"}}
    run = loop(api, [response(api, "Checking", tool=tool), response(api, "Проверка завершена.")], names=["Bash"], raw="main")
    assert run.result["status"] == "ok"
    assert run.result["output_text"] == "Проверка завершена."
    assert len(run.calls) == 1 and len(run.requests) == 2
    from factory.core.session_compaction import read_compaction_guards
    assert not any(row["kind"] == "tool.result" for row in read_compaction_guards(str(run.work.parent / "artifacts")))
    assert not any(row["kind"] in {"fact_check.passed", "fact_check.correction_forced"} for row in run.trace)


@pytest.mark.parametrize("checker", [check_tool_result_fact, check_pwd_output, check_git_status])
@pytest.mark.parametrize("text", ["Проверка завершена.", "The result is ready.", "wrong-token", ""])
def test_legacy_fact_checks_abstain_without_structured_claims(checker, text):
    records = [{"type": "tool_result", "tool_name": "Bash", "content": "main\n"}]
    assert checker(records, text) == (False, "")


@pytest.mark.parametrize("value, expected", [
    ({"performed": True}, True), ({"ok": True}, True), ({"success": True}, True),
    ({"success": False}, False), ({"error": "fixture error " * 100}, False),
    ({"success": True, "error": "fixture"}, False),
    ({"isError": True, "content": [{"type": "text", "text": "successfully"}]}, False),
    ({"is_error": True}, False), ({"detail": {"error": "fixture"}}, False),
    ({"exit_code": 0}, True), ({"exit_code": 2}, False),
    ({"exit_code": "0"}, None), ({"success": "false"}, None),
    ({"status": "failed"}, False), ({"status": "completed"}, True),
    ({"status": "pending"}, None), ({"success": True, "status": "pending"}, None), ({"data": {"error": "quoted"}}, None),
    ({"text": '"success":true successfully'}, None),
])
def test_tool_outcome_uses_explicit_envelope_fields(value, expected):
    assert executor._result_indicates_success(json.dumps(value)) is expected


@pytest.mark.parametrize("text", ["", "OK", "successfully", "fixture output " * 100, '[{"error":"data"}]', '{broken'])
def test_opaque_output_is_unknown_instead_of_inferred_from_words_or_length(text):
    assert executor._result_indicates_success(text) is None


@pytest.mark.parametrize("code", [0, 1, -9])
def test_bash_outcome_uses_actual_returncode_even_when_stdout_looks_like_error_or_success(code):
    value = tools._format_bash_result(subprocess.CompletedProcess(["fixture"], code, stdout='{"success":true,"error":"data"}', stderr=""))
    assert executor._result_indicates_success(value) is (code == 0)
    assert isinstance(value, str)


def test_unknown_round_breaks_failure_streak_without_becoming_success():
    budget = SessionBudget()
    budget.record_round_productivity(True)
    budget.record_round_productivity(False)
    budget.record_round_productivity(None)
    budget.record_round_productivity(False)
    assert budget.evaluate_after_tool_round().action == DECISION_CONTINUE
    assert budget.productive_rounds == 1 and budget.non_productive_rounds == 2
    assert budget.snapshot()["unknown_outcome_rounds"] == 1
    assert budget.consecutive_non_productive == 1


@pytest.mark.parametrize("raw, expected", [('{"performed":true}', True), (json.dumps({"error": "fixture " * 100}), False), ("opaque", None)])
def test_web_loop_receives_structured_outcome_without_fact_check_or_false_retry(loop, raw, expected):
    tool = {"id": "search", "name": "web_search", "input": {"query": "fixture"}}
    run = loop("openai", [response("openai", tool=tool), response("openai", "Final answer")], names=["web_search"], raw=raw)
    assert run.result["status"] == "ok" and len(run.calls) == 1
    result_event = next(row for row in run.trace if row["kind"] == "tool.result")
    assert result_event["succeeded"] is expected
    following = json.dumps(run.requests[1], ensure_ascii=False)
    assert ("Предыдущий инструмент не дал результата" in following) is (expected is False)


@pytest.mark.parametrize("kind", ["synthesis", "write", "image"])
@pytest.mark.parametrize("api", ["anthropic", "openai"])
def test_truncated_terminal_text_is_not_presented_as_complete(tmp_path, monkeypatch, kind, api):
    from types import SimpleNamespace
    from test_executor_terminal_response_contracts import invoke
    body = response(api, "Partial terminal answer")
    if api == "openai":
        body["choices"][0]["finish_reason"] = "length"
    else:
        body["stop_reason"] = "max_tokens"
    monkeypatch.setattr(executor, "_http_post", lambda *args, **kwargs: (body, 1))
    env = SimpleNamespace(root=tmp_path, logs=lambda text: None)
    if kind == "synthesis":
        result = invoke(env, kind, api=api)
        assert result["status"] == "fail" and result["failure_reason"] == "model_output_limit"
        assert result["usage"]["completion"] == 14 and result["tool_calls_count"] == 4
        assert result["partial_output_text"] == "Partial terminal answer"
    else:
        common = dict(url="http://127.0.0.1:1/v1/" + ("chat/completions" if api == "openai" else "messages"),
                      model="fixture", system_prompt="fixture", headers={}, timeout_s=3,
                      workdir=str(tmp_path), log_fn=env.logs, is_openai_api=api == "openai")
        if kind == "write":
            result = executor._make_forced_write_postamble(**common, file_path="fixture.py", raw_result="saved")
        else:
            result = executor._make_terminal_fal_response_call(**common, tool_name="fal_generate_image", fallback_text="Fixture artifact ready")
        assert result[0] == ("Файл сохранён: fixture.py" if kind == "write" else "Fixture artifact ready")
        assert result[1:] == (2, 3)


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, lambda value: pickle.loads(pickle.dumps(value))])
def test_execution_receipt_survives_in_process_copy_and_pickling(operation):
    from factory.core.tool_result_outcome import ToolResultText
    value = ToolResultText("OK", True)
    copied = operation(value)
    assert copied == "OK" and copied.succeeded is True
    assert json.loads(json.dumps({"content": value})) == {"content": "OK"}


@pytest.mark.parametrize("name", ["Read", "Grep", "Glob", "Bash"])
def test_raw_tool_data_cannot_impersonate_an_execution_envelope(name):
    assert executor._result_indicates_success('{"error":"example data"}', tool_name=name) is None


def test_real_file_tools_report_execution_outcome_without_parsing_contents(tmp_path):
    from factory.core.tool_result_outcome import ToolResultText
    content = '{"error":"document data"}'
    written = tools.execute_tool("Write", {"file_path": "fixture.txt", "content": content}, str(tmp_path))
    read = tools.execute_tool("Read", {"file_path": "fixture.txt"}, str(tmp_path))
    missing = tools.execute_tool("Read", {"file_path": "missing.txt"}, str(tmp_path))
    invalid = tools.execute_tool("Read", {}, str(tmp_path))
    changed = tools.execute_tool("Edit", {"file_path": "fixture.txt", "old_string": "document", "new_string": "fixture"}, str(tmp_path))
    rejected = tools.execute_tool("Edit", {"file_path": "fixture.txt", "old_string": "absent", "new_string": "fixture"}, str(tmp_path))
    for value in (written, read, changed):
        assert isinstance(value, ToolResultText) and value.succeeded is True
    for value in (missing, invalid, rejected):
        assert isinstance(value, ToolResultText) and value.succeeded is False
    assert read == content


@pytest.mark.parametrize("api", ["openai", "anthropic"])
def test_invalid_tool_input_has_error_receipt_even_for_raw_content_tools(loop, api):
    tool = {"id": "missing", "name": "Read", "input": {}}
    run = loop(api, [response(api, tool=tool), response(api, "Input unavailable")], names=["Read"])
    event = next(row for row in run.trace if row["kind"] == "tool.result")
    assert event["succeeded"] is False and run.calls == []
    if api == "anthropic":
        block = next(block for block in run.requests[1]["messages"][-1]["content"] if block["type"] == "tool_result")
        assert block["is_error"] is True and block["tool_use_id"] == "missing"


@pytest.mark.parametrize("limited", [False, True])
def test_budget_synthesis_has_only_its_actual_terminal_verdict(loop, monkeypatch, limited):
    from factory.core.agent_loop_limits import CcBackendLoopLimits
    monkeypatch.setattr(executor, "load_cc_backend_loop_limits", lambda: CcBackendLoopLimits(repeated_tool_batch_limit=1))
    tool = {"id": "search", "name": "web_search", "input": {"query": "fixture"}}
    final = response("openai", "Synthesis answer")
    if limited:
        final["choices"][0]["finish_reason"] = "length"
    run = loop("openai", [response("openai", tool=tool), final], names=["web_search"], raw='{"success":true}')
    finishes = [row for row in run.trace if row["kind"] == "task.finish"]
    assert len(finishes) == 1
    assert finishes[0]["status"] == run.result["status"] == ("fail" if limited else "ok")
    assert run.result["tool_calls_count"] == 1 and len(run.calls) == 1


@pytest.mark.parametrize("received", ["matching", "unrelated", "truncated", "error"])
@pytest.mark.parametrize("succeeded", [False, True])
def test_compaction_releases_only_tool_results_consumed_by_completed_model_request(tmp_path, received, succeeded):
    from factory.core.session_compaction import build_compaction_dry_run_plan, read_compaction_guards
    records = [
        {"kind": "tool.result", "tool_use_id": "receipt", "succeeded": succeeded},
        {"kind": "model.round_response", "stop_reason": "max_tokens" if received == "truncated" else "tool_use",
         "http_error": "fixture" if received == "error" else "",
         "consumed_tool_result_ids": ["other" if received == "unrelated" else "receipt"]},
    ]
    (tmp_path / "cc_backend_trace.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))
    guards = read_compaction_guards(str(tmp_path))
    assert bool(guards) is (received != "matching")
    messages = [
        {"role": "user", "content": "fixture"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "receipt", "name": "Bash", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "receipt", "content": "fixture"}]},
        {"role": "assistant", "content": "Current answer"},
        {"role": "user", "content": "New question"},
    ]
    plan = build_compaction_dry_run_plan(messages=messages, trace_events=guards, keep_tail_messages=2)
    assert ("receipt" in plan.preserved_tool_result_ids) is (received != "matching")
    if received == "matching":
        assert plan.can_compact and 1 in plan.summarize_message_indexes and 2 in plan.summarize_message_indexes
    elif succeeded is False:
        assert plan.blocked and plan.reason == "unresolved_failed_tool_result"
