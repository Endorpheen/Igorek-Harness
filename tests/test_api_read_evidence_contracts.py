"""Contract tests for read-evidence summarization helpers."""

from types import SimpleNamespace

import pytest

from inference_api_support import inference_app as inference_app


def _read_exchange(result_text: str, index: int = 0) -> SimpleNamespace:
    return SimpleNamespace(result_text=result_text, is_error=False)


def test_summarize_read_result_strips_numbered_prefix(inference_app):
    api = inference_app.api
    assert api._summarize_read_result("src/app.py", "1→alpha\n2→beta") == "src/app.py: alpha beta"


def test_summarize_read_result_skips_empty_lines(inference_app):
    api = inference_app.api
    assert api._summarize_read_result("src/app.py", "1→alpha\n\n2→beta\n") == "src/app.py: alpha beta"


def test_summarize_read_result_excludes_system_reminder_tail(inference_app):
    api = inference_app.api
    result = api._summarize_read_result(
        "src/app.py", "1→alpha\n<system-reminder>secret</system-reminder>\n2→beta"
    )
    assert result == "src/app.py: alpha"


def test_summarize_read_result_limits_snippet_to_first_eight_lines(inference_app):
    api = inference_app.api
    result = api._summarize_read_result("src/app.py", "\n".join(f"{i}→line{i}" for i in range(1, 11)))
    assert result == "src/app.py: line1 line2 line3 line4 line5 line6 line7 line8"


@pytest.mark.parametrize("text", ["", "\n", "   \n"])
def test_summarize_read_result_empty_text_returns_fallback(inference_app, text):
    api = inference_app.api
    assert api._summarize_read_result("src/app.py", text) == "src/app.py: file read successfully"


def test_summarize_read_result_empty_target_reports_unknown_file(inference_app):
    api = inference_app.api
    assert api._summarize_read_result("", "1→alpha") == "unknown file: alpha"
    assert api._summarize_read_result("", "") == "unknown file: file read successfully"


def test_summarize_read_result_truncates_long_snippet(inference_app):
    api = inference_app.api
    snippet = "a" * 300
    truncated = snippet[:237].rstrip() + "..."
    assert api._summarize_read_result("src/app.py", "1→" + snippet) == f"src/app.py: {truncated}"


def test_summarize_read_result_preserves_unicode_text(inference_app):
    api = inference_app.api
    result = api._summarize_read_result("f.py", "1→Привет мир\n2→Ünicode ✓ 日本語")
    assert result == "f.py: Привет мир Ünicode ✓ 日本語"


def test_sample_head_tail_short_list_returned_as_is(inference_app):
    api = inference_app.api
    assert api._sample_head_tail(["a", "b"]) == ["a", "b"]
    assert api._sample_head_tail(["only"], 8) == ["only"]


def test_sample_head_tail_long_list_uses_head_and_tail_with_default_limit(inference_app):
    api = inference_app.api
    items = [f"item{i}" for i in range(10)]
    expected = [f"item{i}" for i in range(4)] + ["..."] + [f"item{i}" for i in range(7, 10)]
    assert api._sample_head_tail(items, 8) == expected


def test_sample_head_tail_limit_ten_uses_half_head_and_tail(inference_app):
    api = inference_app.api
    items = [f"item{i}" for i in range(12)]
    expected = [f"item{i}" for i in range(5)] + ["..."] + [f"item{i}" for i in range(8, 12)]
    assert api._sample_head_tail(items, 10) == expected


def test_sample_head_tail_deduplicates_head_tail_overlap(inference_app):
    api = inference_app.api
    items = ["a", "b", "c", "d", "e", "f", "g", "h", "a", "b"]
    assert api._sample_head_tail(items, 8) == ["a", "b", "c", "d", "...", "h"]


def test_summarize_read_evidence_without_exchanges_returns_fallback(inference_app):
    api = inference_app.api
    assert api._summarize_read_evidence("missing.py", []) == "missing.py: file read successfully"


def test_summarize_read_evidence_aggregates_categories(inference_app):
    api = inference_app.api
    exchange = _read_exchange(
        "1→import os\n2→from typing import List\n3→MAX_ITEMS = 10\n4→def helper():\n5→    return MAX_ITEMS"
    )
    result = api._summarize_read_evidence("src/app.py", [exchange])
    assert result == "src/app.py: lines 1-5; imports os, typing; constants MAX_ITEMS; sections helper"


def test_summarize_read_evidence_repeated_exchanges_are_deduplicated(inference_app):
    api = inference_app.api
    text = "1→import os\n2→MAX_ITEMS = 10\n3→def main():\n4→    pass"
    first = api._summarize_read_evidence("src/app.py", [_read_exchange(text, 0)])
    second = api._summarize_read_evidence("src/app.py", [_read_exchange(text, 0), _read_exchange(text, 1)])
    assert first == second


def test_summarize_read_evidence_without_categories_falls_back_to_last_exchange(inference_app):
    api = inference_app.api
    exchange = _read_exchange("plain text without markers")
    result = api._summarize_read_evidence("src/app.py", [exchange])
    assert result == "src/app.py: plain text without markers"


def test_summarize_read_evidence_more_than_eight_ranges_uses_first_and_last_four(inference_app):
    api = inference_app.api
    exchanges = [_read_exchange(f"{i}→x", i) for i in range(1, 10)]
    result = api._summarize_read_evidence("src/app.py", exchanges)
    assert result == "src/app.py: lines 1-1, 2-2, 3-3, 4-4, ..., 6-6, 7-7, 8-8, 9-9"


def test_summarize_read_evidence_limits_long_imports_and_sections(inference_app):
    api = inference_app.api
    lines = [f"{i}→import module_{i}" for i in range(1, 13)]
    exchange = _read_exchange("\n".join(lines))
    result = api._summarize_read_evidence("src/app.py", [exchange])
    expected_imports = "imports module_1, module_2, module_3, module_4, ..., module_10, module_11, module_12"
    assert expected_imports in result


def test_extract_ls_directories_collects_real_directories(inference_app):
    api = inference_app.api
    result = api._extract_ls_directories(
        "drwxr-xr-x  2 user group 4096 Jan  1 10:00 backend\ndrwxr-xr-x  2 user group 4096 Jan  1 10:00 frontend"
    )
    assert result == ["backend", "frontend"]


def test_extract_ls_directories_ignores_headers_and_files(inference_app):
    api = inference_app.api
    result = api._extract_ls_directories("total 4\nbackend:\nREADME.md")
    assert result == []


def test_extract_ls_directories_ignores_short_lines_and_invalid_names(inference_app):
    api = inference_app.api
    result = api._extract_ls_directories("drwxr-xr-x  2 user\nbackend:\tdrwxr-xr-x  2 user group 4096 Jan  1 10:00 bad(name\n")
    assert result == []


def test_extract_ls_directories_deduplicates_and_strips_trailing_slash(inference_app):
    api = inference_app.api
    result = api._extract_ls_directories(
        "drwxr-xr-x  2 user group 4096 Jan  1 10:00 backend/\ndrwxr-xr-x  2 user group 4096 Jan  1 10:00 backend"
    )
    assert result == ["backend"]


def test_directory_scan_targets_from_command_parses_ls_arguments(inference_app):
    api = inference_app.api
    assert api._directory_scan_targets_from_command("ls backend/") == ["backend"]
    assert api._directory_scan_targets_from_command("ls -a backend") == ["backend"]


def test_directory_scan_targets_from_command_ignores_non_ls_commands(inference_app):
    api = inference_app.api
    assert api._directory_scan_targets_from_command("rg --files backend") == []
    assert api._directory_scan_targets_from_command("ls") == []


def test_project_analysis_trackable_dirs_exclude_internal_dirs(inference_app):
    api = inference_app.api
    assert api._project_analysis_trackable_dirs(["backend", ".git", "__pycache__", "frontend"]) == ["backend", "frontend"]


def test_project_analysis_deep_branch_candidates_are_case_insensitive_and_deduplicated(inference_app):
    api = inference_app.api
    assert api._project_analysis_deep_branch_candidates(["Backend", "backend", "frontend"]) == ["Backend", "backend", "frontend"]
    assert api._project_analysis_deep_branch_candidates(["misc"]) == []


def test_project_analysis_covered_dirs_uses_scanned_targets(inference_app):
    api = inference_app.api
    result = api._project_analysis_covered_dirs(
        ["ls .", "ls backend", "ls frontend"],
        ["backend", "frontend", ".git"],
    )
    assert result == ["backend", "frontend"]


def test_project_analysis_covered_deep_branches_uses_second_scan(inference_app):
    api = inference_app.api
    result = api._project_analysis_covered_deep_branches(
        ["ls .", "ls backend", "ls backend/src", "ls frontend"],
        ["backend"],
    )
    assert result == ["backend"]


def test_project_analysis_covered_deep_branches_skip_first_two_scans(inference_app):
    api = inference_app.api
    result = api._project_analysis_covered_deep_branches(["ls .", "ls backend"], ["backend"])
    assert result == []


def test_project_analysis_has_enough_context_positive_case(inference_app):
    api = inference_app.api
    state = SimpleNamespace(
        current_workflow="project_analysis",
        workflow_step_count=2,
        collected_directories=["backend"],
        remaining_directories=[],
        remaining_deep_branches=[],
        covered_directories=["backend", "frontend"],
        collected_summaries=["summary-1", "summary-2"],
    )
    assert api._project_analysis_has_enough_context(state) is True


def test_project_analysis_has_enough_context_requires_workflow(inference_app):
    api = inference_app.api
    state = SimpleNamespace(
        current_workflow="file_investigation",
        workflow_step_count=2,
        collected_directories=["backend"],
        remaining_directories=[],
        remaining_deep_branches=[],
        covered_directories=["backend", "frontend"],
        collected_summaries=["summary-1", "summary-2"],
    )
    assert api._project_analysis_has_enough_context(state) is False


def test_project_analysis_has_enough_context_requires_step_at_least_two(inference_app):
    api = inference_app.api
    state = SimpleNamespace(
        current_workflow="project_analysis",
        workflow_step_count=1,
        collected_directories=["backend"],
        remaining_directories=[],
        remaining_deep_branches=[],
        covered_directories=["backend", "frontend"],
        collected_summaries=["summary-1", "summary-2"],
    )
    assert api._project_analysis_has_enough_context(state) is False


def test_project_analysis_has_enough_context_requires_collected_directories(inference_app):
    api = inference_app.api
    state = SimpleNamespace(
        current_workflow="project_analysis",
        workflow_step_count=2,
        collected_directories=[],
        remaining_directories=[],
        remaining_deep_branches=[],
        covered_directories=["backend", "frontend"],
        collected_summaries=["summary-1", "summary-2"],
    )
    assert api._project_analysis_has_enough_context(state) is False


def test_project_analysis_has_enough_context_requires_no_remaining_directories(inference_app):
    api = inference_app.api
    state = SimpleNamespace(
        current_workflow="project_analysis",
        workflow_step_count=2,
        collected_directories=["backend"],
        remaining_directories=["frontend"],
        remaining_deep_branches=[],
        covered_directories=["backend", "frontend"],
        collected_summaries=["summary-1", "summary-2"],
    )
    assert api._project_analysis_has_enough_context(state) is False


def test_project_analysis_has_enough_context_requires_no_remaining_deep_branches(inference_app):
    api = inference_app.api
    state = SimpleNamespace(
        current_workflow="project_analysis",
        workflow_step_count=2,
        collected_directories=["backend"],
        remaining_directories=[],
        remaining_deep_branches=["backend/src"],
        covered_directories=["backend", "frontend"],
        collected_summaries=["summary-1", "summary-2"],
    )
    assert api._project_analysis_has_enough_context(state) is False


def test_project_analysis_has_enough_context_requires_two_covered_dirs_and_summaries(inference_app):
    api = inference_app.api
    state = SimpleNamespace(
        current_workflow="project_analysis",
        workflow_step_count=2,
        collected_directories=["backend"],
        remaining_directories=[],
        remaining_deep_branches=[],
        covered_directories=["backend"],
        collected_summaries=["summary-1", "summary-2"],
    )
    assert api._project_analysis_has_enough_context(state) is False

    state.covered_directories = ["backend", "frontend"]
    state.collected_summaries = ["summary-1"]
    assert api._project_analysis_has_enough_context(state) is False


def test_resolve_python_module_path_resolves_regular_module(inference_app, tmp_path):
    api = inference_app.api
    package_dir = tmp_path / "mypkg"
    package_dir.mkdir()
    (package_dir / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert api._resolve_python_module_path("mypkg.module", tmp_path) == str(tmp_path / "mypkg" / "module.py")


def test_resolve_python_module_path_resolves_package(inference_app, tmp_path):
    api = inference_app.api
    package_dir = tmp_path / "appkit"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    assert api._resolve_python_module_path("appkit", tmp_path) == str(tmp_path / "appkit" / "__init__.py")


def test_resolve_python_module_path_rejects_relative_or_missing_modules(inference_app, tmp_path):
    api = inference_app.api
    assert api._resolve_python_module_path(".relative", tmp_path) is None
    assert api._resolve_python_module_path("missing.module", tmp_path) is None


def test_extract_related_python_files_limits_related_files(inference_app, tmp_path):
    api = inference_app.api
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    for i in range(1, 15):
        (repo_dir / f"mod_{i}.py").write_text("X = 1\n", encoding="utf-8")
    result = api._extract_related_python_files(
        "repo/mod_0.py",
        "\n".join(f"import repo.mod_{i}" for i in range(1, 15)),
        str(tmp_path),
    )
    assert len(result) == 12
