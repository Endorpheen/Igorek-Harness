"""Forced workflow actions from actual normalized model/tool history."""

import asyncio
import shlex
import pytest
from inference_api_support import READ, WRITE, EDIT, BASH, body, state, inference_app as inference_app, inference_server as inference_server


def exchange_payload(env, name, tool_input, output, *, error=False, tools=None):
    payload = body(env, "Inspect the fixture and explain the result", tools or [READ, WRITE, EDIT, BASH])
    payload["messages"] += [{"role": "assistant", "content": [{"type": "tool_use", "id": "fixture-call", "name": name, "input": tool_input}]}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "fixture-call", "content": output, "is_error": error}]}]
    return payload


def decide(env, current):
    return asyncio.run(env.api._resolve_tool_turn(None, current))


@pytest.mark.parametrize("name", ["Read", "Edit", "Write", "Update", "MultiEdit"])
def test_recoverable_file_error_forces_bounded_read_without_repeating_mutation(inference_server, name):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("unchanged fixture")
    current = state(env, exchange_payload(env, name, {"file_path": str(path)}, "Permission denied opening fixture", error=True))
    current.current_workflow, current.workflow_step = "failure_recovery", "retry_once"
    decision = decide(env, current)
    assert decision.source == "forced_failure_recovery"
    assert decision.tool_use["name"] == "Read"
    assert decision.tool_use["input"] == {"file_path": str(path), "offset": 1, "limit": 400}
    assert not env.generator.calls and path.read_text() == "unchanged fixture"


@pytest.mark.parametrize("command", ["rm -rf fixture", "printf hello", "touch file"])
def test_arbitrary_failed_shell_command_is_not_automatically_repeated(inference_server, command):
    env = inference_server
    current = state(env, exchange_payload(env, "Bash", {"command": command}, "Error: fixture failure", error=True))
    assert env.api._failure_recovery_tool_use(current) is None
    assert not env.generator.calls


@pytest.mark.parametrize("workflow", ["failure_recovery", "tavily_research"])
def test_stopped_workflow_never_asks_model_for_another_tool(inference_server, workflow):
    env = inference_server
    current = state(env, body(env, "Inspect fixture", [READ, BASH]))
    current.current_workflow, current.workflow_step = workflow, "stop"
    current.workflow_step_count = 9
    assert decide(env, current) is None
    assert not env.generator.calls


@pytest.mark.parametrize("step,targets,expected", [("scan_root", [], "ls -la"), ("scan_subdirs", ["src", "src/", "", ".", "..", "tests/"], "ls -la src/ tests/"), ("scan_deep", ["src/core", "src/core/", "docs/api"], "ls -la src/core/ docs/api/")])
def test_project_scan_emits_only_deduplicated_directory_listing(inference_server, step, targets, expected):
    env = inference_server
    current = state(env, body(env, "Inspect fixture project", [BASH]))
    current.current_workflow, current.workflow_step = "project_analysis", step
    current.explicit_user_absolute_path = None
    current.remaining_directories = targets
    current.remaining_deep_branches = targets
    decision = decide(env, current)
    assert decision.source == "forced_project_analysis_scan"
    expected = f"ls -la {env.root}/" if step == "scan_root" else expected
    assert decision.tool_use["name"] == "Bash" and decision.tool_use["input"]["command"] == expected
    assert not env.generator.calls


@pytest.mark.parametrize("step", ["scan_subdirs", "scan_deep", "summarize"])
def test_empty_scan_and_missing_bash_cannot_emit_listing(inference_server, step):
    env = inference_server
    current = state(env, body(env, "Inspect fixture project", [READ]))
    current.current_workflow, current.workflow_step = "project_analysis", step
    assert env.api._project_analysis_forced_scan_tool_use(current) is None
    current.tool_map["Bash"] = BASH
    current.remaining_directories = ["", ".", ".."]
    current.remaining_deep_branches = ["", ".", ".."]
    assert env.api._project_analysis_forced_scan_tool_use(current) is None


def test_file_investigation_forces_first_read_and_no_model_call(inference_server):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("fixture")
    current = state(env, body(env, "Read sample.py", [READ]))
    current.current_workflow, current.workflow_step = "file_investigation", "read_primary"
    current.collected_targets = []
    current.resolved_active_path, current.active_file_exists = str(path), True
    decision = decide(env, current)
    assert decision.source == "forced_file_investigation_read_primary"
    assert decision.tool_use["input"] == {"file_path": str(path), "offset": 1, "limit": 400}
    assert not env.generator.calls


@pytest.mark.parametrize("blocker", ["missing-file", "already-read", "no-read-tool", "wrong-step"])
def test_file_investigation_cannot_emit_invalid_primary_read(inference_server, blocker):
    env = inference_server
    current = state(env, body(env, "Read sample.py", [READ]))
    current.current_workflow, current.workflow_step = "file_investigation", "read_primary"
    current.resolved_active_path, current.active_file_exists = str(env.root / "sample.py"), True
    current.collected_targets = []
    if blocker == "missing-file": current.active_file_exists = False
    elif blocker == "already-read": current.collected_targets = [current.resolved_active_path]
    elif blocker == "no-read-tool": current.tool_map.clear()
    else: current.workflow_step = "summarize"
    assert env.api._file_investigation_forced_read_tool_use(current) is None


@pytest.mark.parametrize("already_fetched", [[], ["https://example.com/first"]])
def test_research_fetch_selects_next_unread_source_and_quotes_url(inference_server, already_fetched):
    env = inference_server
    current = state(env, body(env, "Research fixture topic", [BASH]))
    current.current_workflow, current.workflow_step = "tavily_research", "fetch_sources"
    current.collected_targets = already_fetched
    current.candidate_related_files = ["", "https://example.com/first", "https://example.com/second?q=a&x=b"]
    decision = decide(env, current)
    expected = current.candidate_related_files[2 if already_fetched else 1]
    assert decision.source == "forced_tavily_research"
    assert shlex.split(decision.tool_use["input"]["command"]) == ["curl", "-sL", "--", expected]
    assert not env.generator.calls


def test_research_search_emits_plan_without_executing_network(inference_server):
    env = inference_server
    current = state(env, body(env, "Find current Python release notes Authorization: Bearer fixture-not-a-real-key", [BASH]))
    current.current_workflow, current.workflow_step = "tavily_research", "search"
    decision = decide(env, current)
    assert decision.source == "forced_tavily_research"
    assert "tavily" in decision.tool_use["input"]["command"].lower()
    assert not env.generator.calls


@pytest.mark.parametrize("case", ["no-bash", "all-fetched", "wrong-workflow", "summarize"])
def test_research_does_not_refetch_sources_or_emit_tools_after_summary(inference_server, case):
    env = inference_server
    current = state(env, body(env, "Research fixture topic", [BASH]))
    current.current_workflow, current.workflow_step = "tavily_research", "fetch_sources"
    current.candidate_related_files = ["https://example.com/first"]
    if case == "no-bash": current.tool_map.clear()
    elif case == "all-fetched": current.collected_targets = current.candidate_related_files[:]
    elif case == "wrong-workflow": current.current_workflow = None
    else: current.workflow_step = "summarize"
    assert env.api._tavily_research_forced_tool_use(current) is None


@pytest.mark.parametrize("offset,expected", [(1, 3), (3, 5), ("invalid", 3), ({"unexpected": 1}, 3), ([1], 3)])
def test_read_continuation_keeps_bounded_progress_with_malformed_history_offsets(inference_server, offset, expected):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("fixture file remains untouched")
    first = offset if isinstance(offset, int) else 1
    output = f"{first}→first line\n{first + 1}→second line"
    current = state(env, exchange_payload(env, "Read", {"file_path": str(path), "offset": offset, "limit": 2}, output))
    current.current_workflow, current.workflow_step = "file_investigation", "read_primary_continuation"
    current.collected_targets = [str(path)]
    decision = decide(env, current)
    assert decision.source == "forced_read_continuation"
    assert decision.tool_use["input"] == {"file_path": str(path), "offset": expected, "limit": 2}
    assert not env.generator.calls and path.read_text() == "fixture file remains untouched"
