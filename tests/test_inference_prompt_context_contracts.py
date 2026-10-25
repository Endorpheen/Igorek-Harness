"""Prompt evidence must remain bounded and associated with the requested file."""

import json

import pytest

from inference_api_support import (
    BASH,
    EDIT,
    READ,
    WRITE,
    body,
    state,
    inference_app as inference_app,
    inference_server as inference_server,
)


def history(
    env, exchanges, *, tools=None, user="Inspect and update the fixture project"
):
    payload = body(env, user, tools if tools is not None else [READ, EDIT, BASH])
    for index, (name, arguments, text, failed) in enumerate(exchanges):
        payload["messages"].extend(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"call-{index}",
                            "name": name,
                            "input": arguments,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"call-{index}",
                            "content": text,
                            "is_error": failed,
                        }
                    ],
                },
            ]
        )
    return payload


@pytest.mark.parametrize("filename", [None, ""])
def test_edit_without_focus_uses_only_recent_dialogue_tail(inference_server, filename):
    env = inference_server
    current = state(env, body(env, "hello", [EDIT]))
    current.latest_dialogue = "discard-me" + "x" * 4100 + "keep-me"
    result = env.api._build_edit_local_context(current, filename)
    assert (
        len(result) == 4000
        and result.endswith("keep-me")
        and "discard-me" not in result
    )


@pytest.mark.parametrize("field", ["file_path", "path", "notebook_path"])
def test_edit_context_keeps_latest_read_and_failure_for_matching_file(
    inference_server, field
):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("fixture")
    payload = history(
        env,
        [
            ("Read", {field: str(path)}, "1→old fixture body", False),
            ("Read", {field: str(path)}, "1→latest fixture body", False),
            (
                "Edit",
                {field: str(path)},
                "<tool_use_error>latest fixture failure</tool_use_error>",
                True,
            ),
            (
                "Read",
                {"file_path": str(env.root / "other.py")},
                "1→unrelated body",
                False,
            ),
        ],
    )
    current = state(env, payload)
    result = env.api._build_tool_input_prompt(
        current, EDIT, filename=str(path), previous_error="Retry exact old_string"
    )
    assert "latest fixture body" in result and "latest fixture failure" in result
    assert "old fixture body" not in result and "unrelated body" not in result
    assert "Retry exact old_string" in result and '"old_string"' in result


@pytest.mark.parametrize("failed", [False, True])
def test_edit_does_not_mix_same_basename_from_different_directories(
    inference_server, failed
):
    env = inference_server
    first, second = env.root / "first/main.py", env.root / "second/main.py"
    for path in [first, second]:
        path.parent.mkdir()
        path.write_text("fixture")
    name = "Edit" if failed else "Read"
    other = (
        "<tool_use_error>wrong file failure</tool_use_error>"
        if failed
        else "1→wrong file content"
    )
    payload = history(
        env,
        [
            ("Read", {"file_path": str(first)}, "1→correct file content", False),
            (name, {"file_path": str(second)}, other, failed),
        ],
    )
    current = state(env, payload)
    result = env.api._build_tool_input_prompt(current, EDIT, filename=str(first))
    assert "correct file content" in result and "wrong file" not in result


def test_edit_trims_read_tail_but_keeps_latest_relevant_lines(inference_server):
    env = inference_server
    path = env.root / "large.py"
    payload = history(
        env,
        [
            (
                "Read",
                {"file_path": str(path)},
                "1→discard-me\n" + "x" * 8100 + "\n200→keep-me",
                False,
            )
        ],
    )
    result = env.api._build_edit_local_context(state(env, payload), str(path))
    assert "discard-me" not in result and "200→keep-me" in result
    assert len(result) < 8300


@pytest.mark.parametrize("kind", ["unmatched_id", "unmatched_input", "nonfile_tool"])
def test_edit_does_not_associate_unmatched_results_with_target(inference_server, kind):
    env = inference_server
    path = str(env.root / "focus.py")
    payload = history(
        env, [("Read", {"file_path": path}, "1→unmatched fixture data", False)]
    )
    if kind == "unmatched_id":
        payload["messages"][-1]["content"][0]["tool_use_id"] = "unknown-call"
    elif kind == "unmatched_input":
        payload["messages"][-2]["content"][0]["input"] = {}
    else:
        payload["messages"][-2]["content"][0]["name"] = "Bash"
        payload["messages"][-2]["content"][0]["input"] = {"command": "echo fixture"}
    result = env.api._build_edit_local_context(state(env, payload), path)
    assert "unmatched fixture data" not in result and "No prior relevant" in result


@pytest.mark.parametrize("tool", [WRITE, BASH, READ])
def test_non_edit_prompt_retains_recent_dialogue_and_advertised_schema(
    inference_server, tool
):
    env = inference_server
    current = state(env, body(env, "fixture request", [tool]))
    current.latest_dialogue = "discard-me" + "x" * 12100 + "keep-me"
    result = env.api._build_tool_input_prompt(
        current, tool, filename=str(env.root / "out.txt")
    )
    assert "discard-me" not in result and "keep-me" in result
    assert "fixture request" in result and "Tool schema excerpt" in result
    assert str(env.root / "out.txt") in result
    assert all(name in result for name in tool["input_schema"]["required"])


@pytest.mark.parametrize("mode", ["scan", "summary"])
def test_project_analysis_prompt_uses_condensed_evidence(inference_server, mode):
    env = inference_server
    listing = "\n".join(
        f"-rw-r--r-- 1 user group 1 Oct 23 00:00 module{i}.py" for i in range(12)
    )
    payload = history(
        env, [("Bash", {"command": "ls -la src"}, "src:\n" + listing, False)]
    )
    current = state(env, payload)
    assert current.normalized.mode == env.api.PromptMode.TOOL_RESULT
    current.current_workflow = "project_analysis"
    current.workflow_step = "summarize" if mode == "summary" else "scan_subdirs"
    current.collected_summaries = (
        [f"evidence-{i}" for i in range(7)] if mode == "summary" else []
    )
    current.covered_directories = ["src", "tests"]
    current.covered_deep_branches = ["src/core"]
    result = env.api._effective_normalized_for_text_turn(current)
    assert (
        result is not current.normalized
        and result.compaction == current.normalized.compaction
    )
    assert result.prompt_messages[0] == current.normalized.prompt_messages[0]
    content = result.prompt_messages[-1]["content"]
    if mode == "summary":
        assert all(f"evidence-{i}" in content for i in range(3, 7))
        assert "evidence-0" not in content and "src/core" in content
    else:
        assert "module0.py" in content and "module11.py" not in content
        assert "Targets scanned: src" in content and "-rw-r--r--" not in content
    assert len(env.generator.calls) == 0


@pytest.mark.parametrize(
    "result,expected",
    [
        (
            {
                "success": True,
                "results": [
                    {
                        "session": "a",
                        "excerpt": "first excerpt",
                        "last_active": "today",
                        "source": "local",
                    }
                ],
            },
            "first excerpt",
        ),
        (
            {"success": True, "results": [{"preview": "preview fallback"}]},
            "preview fallback",
        ),
        (
            {"success": True, "results": [{"session": "header-only"}]},
            "session=header-only",
        ),
        (
            {"success": False, "results": [{"excerpt": "not successful"}]},
            "fallback evidence",
        ),
        ({"success": True, "results": [None, 4, {}]}, "fallback evidence"),
        ({"success": True, "results": []}, "fallback evidence"),
        ("not JSON", "fallback evidence"),
    ],
)
def test_session_summary_selects_valid_evidence_or_retains_fallback(
    inference_server, result, expected
):
    env = inference_server
    raw = result if isinstance(result, str) else json.dumps(result)
    payload = history(env, [("SessionSearch", {"query": "fixture"}, raw, False)])
    current = state(env, payload)
    current.current_workflow = "session_recall"
    current.workflow_step = "summarize"
    current.collected_summaries = ["fallback evidence"]
    normalized = env.api._effective_normalized_for_text_turn(current)
    assert expected in normalized.prompt_messages[-1]["content"]
    assert len(env.generator.calls) == 0


def test_session_evidence_skips_failed_newer_exchange_and_is_bounded(inference_server):
    env = inference_server
    results = [{"session": str(i), "excerpt": f"excerpt-{i}"} for i in range(8)]
    payload = history(
        env,
        [
            (
                "SessionSearch",
                {"query": "fixture"},
                json.dumps({"success": True, "results": results}),
                False,
            ),
            (
                "SessionSearch",
                {"query": "fixture"},
                "<tool_use_error>failed search</tool_use_error>",
                True,
            ),
        ],
    )
    request = env.api.AnthropicMessagesRequest(**payload)
    found = env.api._latest_session_search_result_summaries(request, limit=3)
    assert len(found) == 3 and "excerpt-2" in found[-1]
    assert all("failed search" not in row for row in found)


@pytest.mark.parametrize("workflow", [None, "unrecognized", "local_implementation"])
def test_ordinary_prompt_is_preserved_when_no_summary_rule_applies(
    inference_server, workflow
):
    env = inference_server
    current = state(env, body(env, "ordinary fixture request", []))
    current.current_workflow = workflow
    assert env.api._effective_normalized_for_text_turn(current) is current.normalized


def test_directory_summary_deduplicates_names_handles_symlinks_and_caps_sections(
    inference_server,
):
    env = inference_server
    lines = ["", "total 5", "malformed", "directory:"]
    lines += ["lrwxrwxrwx 1 u g 1 Oct 23 00:00 current -> target"] * 2
    lines += [f"-rw-r--r-- 1 u g 1 Oct 23 00:00 entry{i}" for i in range(12)]
    for i in range(12):
        lines += [f"section{i}:", "drwxr-xr-x 1 u g 1 Oct 23 00:00 child"]
    payload = history(
        env, [("Bash", {"command": "ls -la src tests"}, "\n".join(lines), False)]
    )
    current = state(env, payload)
    current.current_workflow = "project_analysis"
    current.workflow_step = "scan_subdirs"
    current.collected_summaries = []
    content = env.api._effective_normalized_for_text_turn(current).prompt_messages[-1][
        "content"
    ]
    assert content.count("current") == 1 and " -> target" not in content
    assert "entry11" not in content and "section11:" not in content
    assert "Targets scanned: src, tests" in content


def test_empty_directory_result_does_not_invent_entries(inference_server):
    env = inference_server
    payload = history(
        env, [("Bash", {"command": "ls -la src tests"}, "total 0", False)]
    )
    current = state(env, payload)
    current.current_workflow = "project_analysis"
    current.workflow_step = "scan_subdirs"
    current.collected_summaries = []
    content = env.api._effective_normalized_for_text_turn(current).prompt_messages[-1][
        "content"
    ]
    assert "src: no visible entries parsed" in content
    assert "tests: no visible entries parsed" in content


@pytest.mark.parametrize(
    "stored,focused",
    [
        ("sample.py", "absolute"),
        ("absolute", "sample.py"),
        ("./sample.py", "sample.py"),
    ],
)
def test_edit_matches_relative_and_absolute_paths_under_known_base(
    inference_server, stored, focused
):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("fixture")
    stored = str(path) if stored == "absolute" else stored
    focused = str(path) if focused == "absolute" else focused
    current = state(
        env,
        history(
            env, [("Read", {"file_path": stored}, "1→matching fixture body", False)]
        ),
    )
    assert "matching fixture body" in env.api._build_edit_local_context(
        current, focused
    )


def test_edit_normalizes_dot_segments_without_matching_other_directories(
    inference_server,
):
    env = inference_server
    path = env.root / "nested/sample.py"
    path.parent.mkdir()
    path.write_text("fixture")
    current = state(
        env,
        history(
            env,
            [
                (
                    "Read",
                    {"file_path": str(path.parent / ".." / "nested" / "sample.py")},
                    "1→matching fixture body",
                    False,
                )
            ],
        ),
    )
    assert "matching fixture body" in env.api._build_edit_local_context(
        current, str(path)
    )


@pytest.mark.parametrize(
    "scenario",
    [
        "relative_then_other_read",
        "cwd_changed_after_read",
        "cwd_changed_before_and_after_read",
        "failed_cd_does_not_move",
    ],
)
def test_edit_resolves_historical_relative_path_at_the_time_of_read(
    inference_server, scenario
):
    env = inference_server
    first, second = env.root / "first/main.py", env.root / "second/main.py"
    for path in [first, second]:
        path.parent.mkdir()
        path.write_text("fixture")
    if scenario == "relative_then_other_read":
        rows = [
            ("Read", {"file_path": "first/main.py"}, "1→correct relative file", False),
            ("Read", {"file_path": str(second)}, "1→wrong file", False),
        ]
    elif scenario == "cwd_changed_after_read":
        rows = [
            ("Read", {"file_path": "first/main.py"}, "1→correct relative file", False),
            ("Bash", {"command": "cd second"}, "", False),
        ]
    elif scenario == "cwd_changed_before_and_after_read":
        rows = [
            ("Bash", {"command": "cd first"}, "", False),
            ("Read", {"file_path": "main.py"}, "1→correct relative file", False),
            ("Bash", {"command": "cd ../second"}, "", False),
        ]
    else:
        rows = [
            (
                "Bash",
                {"command": "cd second"},
                "<tool_use_error>fixture cd failed</tool_use_error>",
                True,
            ),
            ("Read", {"file_path": "first/main.py"}, "1→correct relative file", False),
        ]
    current = state(env, history(env, rows))
    result = env.api._build_edit_local_context(current, str(first))
    assert "correct relative file" in result and "wrong file" not in result
