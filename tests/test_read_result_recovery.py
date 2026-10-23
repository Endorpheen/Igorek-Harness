import json
from pathlib import Path
import re
import os
import subprocess
import sys

import pytest

from factory.core import cc_backend_tools as tools
from factory.core.tool_result_store import maybe_persist_tool_result


def saved_path(result):
    return re.search(r"^Full output saved to: (.+)$", result, re.M).group(1)


def test_original_result_outside_workspace_can_be_read_through_its_runtime_context(tmp_path):
    work = tmp_path / "work"
    artifacts = tmp_path / "runtime"
    work.mkdir()
    content = "line of fixture output\n" * 900
    from factory.core.tool_result_access import ToolResultAccess
    access = ToolResultAccess.create(str(work), str(artifacts), "task-a", "chat-a", tmp_path / "data.db")
    result = maybe_persist_tool_result(content=content, tool_name="Bash", tool_use_id="call-a", workdir=str(artifacts), access=access)
    path = saved_path(result)
    output = tools.execute_tool("Read", {"file_path": path, "limit": 2}, str(work),
                                tool_result_access=access)
    assert output == "line of fixture output\n" * 2


def test_source_file_of_12301_characters_is_returned_by_one_read_without_persistence(tmp_path):
    content = "x" * 12301
    target = tmp_path / "source.py"
    target.write_text(content)
    result = tools.execute_tool("Read", {"file_path": str(target)}, str(tmp_path))
    assert result == content
    output = maybe_persist_tool_result(content=result, tool_name="Read", tool_use_id="read-a", workdir=str(tmp_path))
    assert output == content
    assert not (tmp_path / ".tool_results").exists()


def test_long_unicode_line_can_be_reconstructed_without_losing_its_remainder(tmp_path):
    content = "α🐙" * 18000 + "\nlast line\n"
    target = tmp_path / "long.txt"
    target.write_text(content)
    args = {"file_path": str(target)}
    parts = []
    for _ in range(20):
        result = tools.execute_tool("Read", args, str(tmp_path), read_max_chars=2048)
        assert len(result) <= 2048
        marker = "\n\n[Read page truncated; continue on the same file with "
        if marker not in result:
            parts.append(result)
            break
        body, continuation = result.rsplit(marker, 1)
        parts.append(body)
        args = {"file_path": str(target), **json.loads(continuation[:-1])}
    assert "".join(parts) == content


@pytest.fixture
def issued(tmp_path):
    from factory.core.tool_result_access import ToolResultAccess
    work = tmp_path / "work"
    work.mkdir()
    scope = ToolResultAccess.create(str(work), str(tmp_path / "runtime"), "task-a", "chat-a", tmp_path / "data.db")
    content = "saved fixture output\n" * 700
    path = saved_path(maybe_persist_tool_result(content=content, tool_name="Bash", tool_use_id="call-a",
                                               workdir=scope.artifact_workdir, access=scope))
    return scope, path, content


@pytest.mark.parametrize("session,task,same_workspace,allowed", [
    ("chat-a", "task-a", True, True),
    ("chat-a", "next-task", True, True),
    ("chat-b", "task-b", True, False),
    ("chat-b", "task-a", True, False),
    ("", "task-a", True, False),
    ("chat-a", "next-task", False, False),
])
def test_archive_authority_uses_runtime_task_session_and_workspace(issued, tmp_path, session, task, same_workspace, allowed):
    from factory.core.tool_result_access import ToolResultAccess
    owner, path, _ = issued
    work = Path(owner.workdir) if same_workspace else tmp_path / "other-work"
    work.mkdir(exist_ok=True)
    reader = ToolResultAccess.create(str(work), str(tmp_path / "other-runtime"), task, session, owner.database)
    result = tools.execute_tool("Read", {"file_path": path, "limit": 1}, str(work), tool_result_access=reader)
    if allowed:
        assert result == "saved fixture output\n"
    else:
        assert "error" in json.loads(result)
        assert "saved fixture output" not in result


def test_resumed_process_can_read_an_issued_result(issued):
    scope, path, _ = issued
    script = """import sys,json
from factory.core.tool_result_access import ToolResultAccess
from factory.core.cc_backend_tools import execute_tool
p=json.load(sys.stdin)
s=ToolResultAccess.create(p['work'],p['artifacts'],'next-task','chat-a',p['database'])
print(execute_tool('Read',{'file_path':p['path'],'limit':1},p['work'],tool_result_access=s),end='')
"""
    result = subprocess.run([sys.executable, "-c", script], input=json.dumps({"work": scope.workdir,
        "artifacts": scope.artifact_workdir, "database": scope.database, "path": path}), text=True,
        capture_output=True, timeout=20, check=True)
    assert result.stdout == "saved fixture output\n"


def test_unissued_neighbor_and_model_supplied_context_do_not_grant_access(issued):
    scope, path, _ = issued
    private = Path(path).with_name("unissued.txt")
    private.write_text("NEIGHBOR_CANARY")
    result = tools.execute_tool("Read", {"file_path": str(private), "tool_result_access": {"session_id": "chat-a"}},
                                scope.workdir, tool_result_access=scope)
    assert "error" in json.loads(result)
    assert "NEIGHBOR_CANARY" not in result


@pytest.mark.parametrize("operation,arguments", [
    ("Write", {"content": "changed"}),
    ("Edit", {"old_string": "saved", "new_string": "changed"}),
])
def test_read_grant_does_not_grant_mutation(issued, operation, arguments):
    scope, path, content = issued
    result = tools.execute_tool(operation, {"file_path": path, **arguments}, scope.workdir, tool_result_access=scope)
    assert "error" in json.loads(result)
    assert Path(path).read_text() == content


@pytest.mark.parametrize("change", ["remove", "replace", "file-link", "directory-link"])
def test_missing_changed_and_linked_archives_are_not_read_as_the_original(issued, tmp_path, change):
    scope, path, content = issued
    target = Path(path)
    outside = tmp_path / "outside"
    outside.mkdir()
    private = outside / target.name
    private.write_text("OUTSIDE_CANARY")
    if change == "remove":
        target.unlink()
    elif change == "replace":
        target.write_text("REPLACEMENT_CANARY")
    elif change == "file-link":
        target.unlink()
        target.symlink_to(private)
    else:
        target.parent.rename(target.parent.with_name("previous"))
        target.parent.symlink_to(outside, target_is_directory=True)
    result = tools.execute_tool("Read", {"file_path": path}, scope.workdir, tool_result_access=scope)
    assert "error" in json.loads(result)
    assert "CANARY" not in result
    assert content not in result
    if change == "remove":
        assert "removed" in result


def test_repeated_delivery_reuses_an_unchanged_archive_and_reused_ids_keep_old_content(issued):
    scope, path, content = issued
    before = Path(path).stat().st_mtime_ns
    repeated = maybe_persist_tool_result(content=content, tool_name="Bash", tool_use_id="call-a",
                                         workdir=scope.artifact_workdir, access=scope)
    assert saved_path(repeated) == path
    assert Path(path).stat().st_mtime_ns == before
    changed = maybe_persist_tool_result(content="different output\n" * 900, tool_name="Bash", tool_use_id="call-a",
                                        workdir=scope.artifact_workdir, access=scope)
    assert saved_path(changed) != path
    assert Path(path).read_text() == content


def test_changed_archive_identity_cannot_be_promised_again_as_readable(issued):
    scope, path, content = issued
    target = Path(path)
    previous = target.stat()
    os.utime(target, ns=(previous.st_atime_ns, previous.st_mtime_ns + 10_000_000))
    result = maybe_persist_tool_result(content=content, tool_name="Bash", tool_use_id="call-a",
                                       workdir=scope.artifact_workdir, access=scope)
    assert "Full output saved to:" not in result
    assert "could not be saved" in result


def test_failed_registration_does_not_offer_an_unreadable_path(tmp_path):
    from factory.core.tool_result_access import ToolResultAccess
    database = tmp_path / "directory-not-database"
    database.mkdir()
    scope = ToolResultAccess.create(str(tmp_path / "work"), str(tmp_path / "runtime"), "task", "chat", database)
    output = maybe_persist_tool_result(content="large output\n" * 2000, tool_name="Bash", tool_use_id="fixture",
                                       workdir=scope.artifact_workdir, access=scope)
    assert "Full output saved to:" not in output
    assert "could not be saved" in output
    assert len(output) < 4000


def test_storage_scope_is_checked_before_creating_any_file(issued, tmp_path):
    scope, _, content = issued
    unrelated = tmp_path / "unrelated-output-directory"
    result = maybe_persist_tool_result(content=content, tool_name="Bash", tool_use_id="wrong-root",
                                       workdir=str(unrelated), access=scope)
    assert "could not be saved" in result
    assert not unrelated.exists()


def test_aggregate_budget_does_not_hide_a_bounded_read_page(tmp_path):
    from factory.core.tool_result_store import ToolResultStoreConfig, enforce_turn_budget
    page = "read page\n" * 1000
    items = [{"tool_name": "Read", "content": page, "tool_use_id": "read"},
             {"tool_name": "Bash", "content": "command output\n" * 2000, "tool_use_id": "bash"}]
    result = enforce_turn_budget(items, workdir=str(tmp_path), config=ToolResultStoreConfig(turn_budget_chars=12000))
    assert result[0]["content"] == page
    assert "Full output saved to:" in result[1]["content"]
    assert not (tmp_path / ".tool_results/read.txt").exists()


@pytest.mark.parametrize("text,offset,limit", [("a\u2028b\nc\n", 1, 1), ("a\r\nb\r\nc", 1, 1), ("", 0, None)])
def test_page_read_keeps_existing_line_and_newline_semantics(tmp_path, text, offset, limit):
    target = tmp_path / "text.txt"
    target.write_bytes(text.encode())
    result = tools.execute_tool("Read", {"file_path": str(target), "offset": offset, "limit": limit}, str(tmp_path))
    assert result == ("b\n" if text else "")


def test_continuation_honors_an_explicit_line_range(tmp_path):
    target = tmp_path / "range.txt"
    expected = "long line " * 1000 + "\n"
    target.write_text("skip\n" + expected + "DO_NOT_INCLUDE\n")
    args = {"file_path": str(target), "offset": 1, "limit": 1}
    parts = []
    for _ in range(10):
        result = tools.execute_tool("Read", args, str(tmp_path), read_max_chars=2048)
        marker = "\n\n[Read page truncated; continue on the same file with "
        if marker not in result:
            parts.append(result)
            break
        body, continuation = result.rsplit(marker, 1)
        parts.append(body)
        args = {"file_path": str(target), **json.loads(continuation[:-1])}
    assert "".join(parts) == expected


@pytest.mark.parametrize("cursor", [-1, True, 1.5, "bad"])
def test_invalid_cursor_is_rejected_without_silent_truncation(tmp_path, cursor):
    target = tmp_path / "text.txt"
    target.write_text("abc\n")
    result = tools.execute_tool("Read", {"file_path": str(target), "cursor": cursor}, str(tmp_path))
    assert "cursor" in json.loads(result)["error"]


def test_numeric_cursor_from_a_local_model_uses_the_existing_coercion(tmp_path):
    target = tmp_path / "text.txt"
    target.write_text("abcdef\n")
    assert tools.execute_tool("Read", {"file_path": str(target), "cursor": "3"}, str(tmp_path)) == "def\n"


def test_a_very_large_line_limit_cannot_overflow_the_continuation_budget(tmp_path):
    target = tmp_path / "large.txt"
    target.write_text("x" * 10000)
    result = tools.execute_tool("Read", {"file_path": str(target), "limit": 10**500}, str(tmp_path), read_max_chars=512)
    assert result.startswith("x")
    assert "Read page truncated" in result
    assert len(result) <= 512


def test_read_budget_adapts_to_the_context_without_new_model_requests():
    from factory.core.read_paging import read_budget
    assert read_budget(4096) < read_budget(64000) < read_budget(131072) < read_budget(262144)
    assert read_budget(64000) > 12301
    assert read_budget(262144, 1000) == 1000


def test_cleanup_retains_results_while_their_task_is_in_history(issued, tmp_path, capsys):
    import sqlite3
    from factory.core.schema import initialize_database
    from factory.core.workspace_manager import WorkspaceManager
    scope, path, _ = issued
    initialize_database(scope.database)
    with sqlite3.connect(scope.database) as conn:
        conn.execute("INSERT INTO tasks (task_id,status) VALUES (?,?)", (scope.task_id, "ok"))
    os.utime(scope.artifact_workdir, (1, 1))
    manager = WorkspaceManager(db_path=scope.database, workspace_root=str(tmp_path))
    manager.cleanup_old_workspaces(days_old=7)
    assert Path(path).is_file()
    with sqlite3.connect(scope.database) as conn:
        conn.execute("DELETE FROM tasks WHERE task_id=?", (scope.task_id,))
    manager.cleanup_old_workspaces(days_old=7)
    assert not Path(path).exists()


@pytest.mark.parametrize("evidence", ["runtime-spill", "raw-tool-text", "model-message", "other-path"])
def test_legacy_archives_require_runtime_spill_evidence(tmp_path, evidence):
    import sqlite3
    from factory.core.tool_result_access import ToolResultAccess
    work, runtime = tmp_path / "work", tmp_path / "runtime"
    work.mkdir()
    (runtime / ".tool_results").mkdir(parents=True)
    target = runtime / ".tool_results/old-call.txt"
    target.write_text("legacy fixture\n")
    database = tmp_path / "data.db"
    sqlite3.connect(database).close()
    pointer = str(target) if evidence != "other-path" else str(target.with_name("other.txt"))
    event = {"kind": "tool.result" if evidence != "model-message" else "model.round_response",
             "raw_result_len": 12000, "result_for_model_len": 12000 if evidence == "raw-tool-text" else 3000,
             "context_output": "<persisted-output>\nFull output saved to: " + pointer + "\n"}
    (runtime / "cc_backend_trace.jsonl").write_text(json.dumps(event) + "\n")
    scope = ToolResultAccess.create(str(work), str(runtime), "task", "chat", database)
    result = tools.execute_tool("Read", {"file_path": str(target)}, str(work), tool_result_access=scope)
    if evidence == "runtime-spill":
        assert result == "legacy fixture\n"
    else:
        assert "error" in json.loads(result)
        assert "legacy fixture" not in result


def test_legacy_result_can_be_recovered_by_the_next_task_in_the_same_chat(tmp_path, monkeypatch):
    from factory.core.schema import initialize_database, open_db
    from factory.core.tool_result_access import ToolResultAccess
    from factory.core import runtime as runtime_module
    work = tmp_path / "project"
    work.mkdir()
    old_task = "abcdef12-old-task"
    runtime = tmp_path / "tasks" / "temp_execution_abcdef12"
    (runtime / ".tool_results").mkdir(parents=True)
    target = runtime / ".tool_results/legacy.txt"
    target.write_text("old chat output\n")
    database = tmp_path / "data.db"
    initialize_database(str(database))
    with open_db(str(database)) as conn:
        conn.execute("INSERT INTO tasks (task_id,metadata) VALUES (?,?)", (old_task,
            json.dumps({"session": "chat-a", "cwd": str(work)})))
    event = {"kind": "tool.result", "raw_result_len": 20000, "result_for_model_len": 3000,
        "context_output": "<persisted-output>\nFull output saved to: " + str(target) + "\n"}
    (runtime / "cc_backend_trace.jsonl").write_text(json.dumps(event) + "\n")
    monkeypatch.setattr(runtime_module, "get_workspace_root", lambda: str(tmp_path / "tasks"))
    scope = ToolResultAccess.create(str(work), str(tmp_path / "next-runtime"), "next-task", "chat-a", database)
    assert tools.execute_tool("Read", {"file_path": str(target)}, str(work), tool_result_access=scope) == "old chat output\n"


def test_cleanup_keeps_legacy_results_before_the_first_read_migrates_them(tmp_path):
    from factory.core.schema import initialize_database, open_db
    from factory.core.workspace_manager import WorkspaceManager
    root = tmp_path / "tasks"
    legacy = root / "temp_execution_abcdef12"
    (legacy / ".tool_results").mkdir(parents=True)
    target = legacy / ".tool_results/result.txt"
    target.write_text("legacy result")
    database = tmp_path / "data.db"
    initialize_database(str(database))
    with open_db(str(database)) as conn:
        conn.execute("INSERT INTO tasks (task_id,status) VALUES (?,?)", ("abcdef12-task", "ok"))
    os.utime(legacy, (1, 1))
    WorkspaceManager(db_path=str(database), workspace_root=str(root)).cleanup_old_workspaces(7)
    assert target.read_text() == "legacy result"


def test_chat_history_keeps_results_after_the_producing_task_is_removed(issued, tmp_path):
    from factory.core.schema import initialize_database, open_db
    from factory.core.workspace_manager import WorkspaceManager
    scope, path, _ = issued
    initialize_database(scope.database)
    with open_db(scope.database) as conn:
        conn.execute("INSERT INTO tasks (task_id,status,metadata) VALUES (?,?,?)",
                     ("next-task", "ok", json.dumps({"session": scope.session_id})))
    os.utime(scope.artifact_workdir, (1, 1))
    manager = WorkspaceManager(db_path=scope.database, workspace_root=str(tmp_path))
    manager.cleanup_old_workspaces(7)
    assert Path(path).is_file()
    with open_db(scope.database) as conn:
        conn.execute("DELETE FROM tasks")
    manager.cleanup_old_workspaces(7)
    assert not Path(path).exists()
