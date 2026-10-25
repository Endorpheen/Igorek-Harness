"""Worker orchestration contracts with isolated files/SQLite and fake executors."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib import request

import pytest

from factory.core import worker as w, workspace_manager as workspace
from factory.core.schema import initialize_database, open_db


@pytest.fixture
def worker_factory(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    profiles = root / "profiles"
    profiles.mkdir(parents=True)
    for backend in ["claude", "codex", "cc-backend"]:
        (profiles / (backend + ".json")).write_text(
            json.dumps(
                {
                    "backend": backend,
                    "model": "fixture-model",
                    "provider": "openai-codex"
                    if backend == "codex"
                    else ("z.ai" if backend == "claude" else "local"),
                    "base_url": "http://127.0.0.1:1",
                    "env": {},
                }
            )
        )
    for key in ["FACTORY_DATA_DIR", "IGOREK_FACTORY_HOME"]:
        monkeypatch.setenv(key, str(root))
    monkeypatch.setenv("FACTORY_PROFILES_DIR", str(profiles))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FACTORY_VAULT_ENABLED", "false")
    for key in list(w.os.environ):
        if key.startswith("FACTORY_VAULT_") and key != "FACTORY_VAULT_ENABLED":
            monkeypatch.delenv(key, raising=False)
    db = root / "database/factory.db"
    initialize_database(db)
    monkeypatch.setattr(w, "DB_PATH", str(db))
    monkeypatch.setattr(w, "WORKSPACE_ROOT", str(root / "workspace"))
    monkeypatch.setattr(w, "SKILLS_AVAILABLE", False)
    security = SimpleNamespace(
        mode="audit",
        store=Mock(),
        execute_command=Mock(
            return_value=(
                SimpleNamespace(returncode=0, stdout="fixture answer", stderr=""),
                "security-fixture",
            )
        ),
    )
    monkeypatch.setattr(w, "SecurityRuntime", lambda: security)
    monkeypatch.setattr(
        w,
        "sync_codex_mcp_for_profile",
        Mock(
            return_value={
                "status": "ok",
                "added": [],
                "skipped_existing": [],
                "errors": [],
            }
        ),
    )
    monkeypatch.setattr(w, "_start_codex_rollout_trace_monitor", lambda **_: None)
    monkeypatch.setattr(w, "_recent_codex_rollout_text", lambda **_: "")
    monkeypatch.setattr(
        w.subprocess,
        "Popen",
        Mock(side_effect=AssertionError("Unexpected real process")),
    )
    monkeypatch.setattr(
        request, "urlopen", Mock(side_effect=AssertionError("Unexpected network"))
    )
    objects = []

    def create(backend="cc-backend"):
        result = w.Worker(backend)
        objects.append(result)
        return result

    create.root = root
    create.security = security
    return create


def task(identifier="task-fixture", **overrides):
    return {
        "task_id": identifier,
        "parent_id": None,
        "stage": "main",
        "input_text": "fixture question",
        "input_files": [],
        "capabilities": [],
        "metadata": {},
        "limits_timeout_s": 5,
        **overrides,
    }


@pytest.mark.parametrize("backend", ["cc-backend", "codex", "claude"])
def test_constructor_binds_profile_and_initializes_own_database(
    worker_factory, backend
):
    worker = worker_factory(backend)
    assert (
        worker.backend == backend
        and worker.model == "fixture-model"
        and worker.tasks_processed == 0
    )
    assert Path(w.WORKSPACE_ROOT).is_relative_to(worker_factory.root)
    with open_db(w.DB_PATH) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()


def test_cc_executor_receives_cwd_while_runtime_files_stay_in_task_directory(
    worker_factory, tmp_path, monkeypatch
):
    worker = worker_factory()
    cwd = tmp_path / "project"
    cwd.mkdir()
    execute = Mock(return_value={"status": "ok", "output": {"text": "answer"}})
    monkeypatch.setattr(worker, "_execute_cc_backend", execute)
    result = worker.execute_task(task(metadata={"cwd": str(cwd)}))
    assert result["status"] == "ok" and execute.call_args.args[1] == str(cwd)
    runtime = Path(execute.call_args.args[2])
    assert runtime.parent == Path(w.WORKSPACE_ROOT)
    payload = json.loads((runtime / "input.json").read_text())
    assert payload["task_id"] == "task-fixture" and payload["input"]["text"].endswith(
        "fixture question"
    )
    assert not (cwd / "input.json").exists()


def test_cc_executor_failure_is_returned_without_false_success(
    worker_factory, monkeypatch
):
    worker = worker_factory()
    monkeypatch.setattr(
        worker,
        "_execute_cc_backend",
        Mock(side_effect=RuntimeError("fixture backend failure")),
    )
    result = worker.execute_task(task())
    assert (
        result["status"] == "fail"
        and result["error"] == "fixture backend failure"
        and result["usage"]["completion"] == 0
    )


def test_host_op_refusal_prevents_executor_and_input_creation(
    worker_factory, monkeypatch
):
    worker = worker_factory()
    execute = Mock()
    monkeypatch.setattr(worker, "_execute_cc_backend", execute)
    monkeypatch.setattr(w, "_consume_host_op_resolutions", lambda *_: "fixture refused")
    result = worker.execute_task(task())
    assert (
        result["status"] == "fail" and "host_op_resolution_refused" in result["error"]
    )
    execute.assert_not_called()
    assert not (
        Path(w.WORKSPACE_ROOT) / "temp_execution_task-fix" / "input.json"
    ).exists()


@pytest.mark.parametrize("backend", ["claude", "codex"])
@pytest.mark.parametrize("returncode,expected", [(0, "ok"), (1, "fail")])
def test_cli_executor_status_and_security_metadata_are_preserved(
    worker_factory, backend, returncode, expected, tmp_path
):
    worker = worker_factory(backend)
    stdout = (
        json.dumps({"type": "agent_message", "message": "fixture answer"})
        if backend == "codex"
        else json.dumps({"type": "result", "result": "fixture answer"})
    )
    worker_factory.security.execute_command.return_value = (
        SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr="fixture stderr" if returncode else "",
        ),
        "security-fixture",
    )
    result = worker.execute_task(task(metadata={"cwd": str(tmp_path)}))
    assert result["status"] == expected and result["output"]["text"] == "fixture answer"
    assert result["security"] == {
        "runtime_mode": "audit",
        "session_id": "security-fixture",
    }
    packet = worker_factory.security.execute_command.call_args.kwargs
    assert (
        packet["backend"] == backend
        and packet["task_id"] == "task-fixture"
        and packet["cwd"] == str(tmp_path)
    )
    if backend == "codex":
        assert "--json" in packet["command"]
    else:
        assert json.loads(packet["input_payload"])["profile"] == "claude"


@pytest.mark.parametrize(
    "failure,expected",
    [
        (subprocess.TimeoutExpired("fixture", 5), "Timeout after 5s"),
        (RuntimeError("fixture unavailable"), "fixture unavailable"),
    ],
)
def test_executor_failures_have_explicit_failure_results(
    worker_factory, tmp_path, failure, expected
):
    worker = worker_factory("claude")
    worker_factory.security.execute_command.side_effect = failure
    result = worker.execute_task(task(metadata={"cwd": str(tmp_path)}))
    assert result["status"] == "fail" and result["error"] == expected


def test_structured_claude_result_file_takes_precedence_over_stdout(
    worker_factory, tmp_path
):
    worker = worker_factory("claude")
    runtime = Path(w.WORKSPACE_ROOT) / "temp_execution_task-fix"
    runtime.mkdir(parents=True)
    saved = {
        "task_id": "task-fixture",
        "status": "ok",
        "output": {"text": "file answer"},
        "usage": {"prompt": 3},
    }
    (runtime / "result.json").write_text(json.dumps(saved))
    assert worker.execute_task(task(metadata={"cwd": str(tmp_path)})) == saved


def test_invalid_claude_result_file_is_failure_not_silent_fallback(
    worker_factory, tmp_path
):
    worker = worker_factory("claude")
    runtime = Path(w.WORKSPACE_ROOT) / "temp_execution_task-fix"
    runtime.mkdir(parents=True)
    (runtime / "result.json").write_text("{")
    result = worker.execute_task(task(metadata={"cwd": str(tmp_path)}))
    assert result["status"] == "fail" and result["error"]


def test_unknown_backend_fails_closed(worker_factory):
    worker = worker_factory()
    worker.backend = "unsupported"
    result = worker.execute_task(task())
    assert result["status"] == "fail" and "Unsupported backend" in result["error"]
    worker_factory.security.execute_command.assert_not_called()


def test_child_without_cwd_uses_existing_parent_workdir(worker_factory, monkeypatch):
    worker = worker_factory()
    parent = Path(w.WORKSPACE_ROOT) / "temp_execution_parent-f"
    parent.mkdir(parents=True)
    execute = Mock(return_value={"status": "ok"})
    monkeypatch.setattr(worker, "_execute_cc_backend", execute)
    assert worker.execute_task(task(parent_id="parent-fixture"))["status"] == "ok"
    assert execute.call_args.args[1] == str(parent)


def test_copy_results_excludes_foundation_and_transport_files(
    worker_factory, tmp_path, monkeypatch
):
    worker = worker_factory()
    manager = workspace.WorkspaceManager(
        db_path=w.DB_PATH, workspace_root=w.WORKSPACE_ROOT
    )
    monkeypatch.setattr(workspace, "WorkspaceManager", lambda: manager)
    source = tmp_path / "workdir"
    source.mkdir()
    for name in ["artifact.txt", "MEMORY.md", "USER.md", "input.json", "result.json"]:
        (source / name).write_text(name)
    worker.copy_created_files_to_workspace("parent", "task-fixture", str(source))
    copied = Path(w.WORKSPACE_ROOT) / "parent/results/cc-backend_task-fix"
    assert (copied / "artifact.txt").read_text() == "artifact.txt" and not (
        copied / "MEMORY.md"
    ).exists()
    assert json.loads((copied / "copy_metadata.json").read_text())["files"] == [
        "artifact.txt"
    ]


def test_vault_configuration_merges_services_without_duplicates(
    worker_factory, monkeypatch
):
    worker = worker_factory()
    worker.config["vault"] = {
        "enabled": True,
        "services": ["tavily"],
        "agent": "profile-agent",
        "ttl_seconds": 120,
    }
    monkeypatch.setenv("FACTORY_VAULT_SERVICES", "tavily,openai")
    config = worker._vault_request_config(
        {
            "vault": {
                "services": ["openai", "custom"],
                "agent": "task-agent",
                "ttl_seconds": 30,
            }
        }
    )
    assert config == {
        "enabled": True,
        "services": ["tavily", "openai", "custom"],
        "agent": "task-agent",
        "ttl_seconds": 30,
    }
    assert (
        worker._vault_request_config({"vault": {"ttl_seconds": "bad"}})["ttl_seconds"]
        == 900
    )


def test_vault_disabled_and_desktop_provider_boundary_do_not_touch_broker(
    worker_factory, monkeypatch
):
    worker = worker_factory()
    broker = Mock()
    monkeypatch.setattr(w, "build_vault_services", broker)
    assert worker._materialize_vault_env(task_id="fixture") == {}
    worker.config["vault"] = {"services": ["tavily"]}
    worker.auth_headers_provider = Mock()
    with pytest.raises(PermissionError, match="Additional vault services"):
        worker._materialize_vault_env(task_id="fixture")
    broker.assert_not_called()


@pytest.mark.parametrize(
    "allowed,reason,fallback",
    [
        (True, "allowed", False),
        (False, "credential not found in vault", True),
        (False, "credential not found in vault", False),
        (False, "agent is not allowed", False),
    ],
)
def test_vault_broker_decisions_and_optional_fallback_never_print_secret(
    worker_factory, monkeypatch, capsys, allowed, reason, fallback
):
    worker = worker_factory()
    worker.config["vault"] = {"services": ["tavily"]}
    decision = SimpleNamespace(
        allowed=allowed,
        reason=reason,
        ttl_seconds=30,
        env={"TAVILY_API_KEY": "synthetic-secret"} if allowed else {},
    )
    broker = Mock()
    broker.get_ephemeral_env.return_value = decision
    monkeypatch.setattr(
        w, "build_vault_services", lambda **_: SimpleNamespace(broker=broker)
    )
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    if fallback:
        monkeypatch.setenv("TAVILY_API_KEY", "synthetic-secret")
    if not allowed and reason == "agent is not allowed":
        with pytest.raises(PermissionError, match="denied"):
            worker._materialize_vault_env(task_id="fixture")
    else:
        result = worker._materialize_vault_env(task_id="fixture")
        assert result == (
            {"TAVILY_API_KEY": "synthetic-secret"} if allowed or fallback else {}
        )
    assert "synthetic-secret" not in capsys.readouterr().out
    assert "synthetic-secret" not in str(
        worker.security_runtime.store.log_event.call_args_list
    )


@pytest.mark.parametrize(
    "status", ["ok", "fail", "waiting_approval", "retry", "cancelled"]
)
def test_update_persists_status_usage_and_completion_semantics(worker_factory, status):
    worker = worker_factory()
    identifier = "stored-task"
    with open_db(w.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO tasks(task_id,status,profile,metadata) VALUES(?,?,?,?)",
            (identifier, "processing", "cc-backend", '{"keep":"fixture"}'),
        )
        conn.commit()
        result = {
            "status": status,
            "output": {"text": "actual answer", "files": ["artifact.txt"]},
            "usage": {"prompt": 3, "completion": 5},
            "usage_ext": {"total_tokens": 8},
            "error": "fixture failure" if status == "fail" else "",
        }
        worker.update_task(conn, identifier, result)
        saved = worker.get_task(conn, identifier)
        assert (
            saved["status"] == status
            and saved["output_text"] == "actual answer"
            and saved["usage_prompt"] == 3
            and saved["usage_completion"] == 5
        )
        assert (saved["completed_at"] is None) is (status == "waiting_approval")
        assert saved["metadata"]["keep"] == "fixture" and saved["metadata"][
            "usage_ext"
        ] == {"total_tokens": 8}
        row = conn.execute(
            "SELECT event_type FROM events WHERE task_id=?", (identifier,)
        ).fetchone()
        assert row[0] == "task_" + status
        if status == "fail":
            assert saved["metadata"]["diagnostic"]["code"] == "TASK.EXECUTION_FAILED"


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"type": "result", "result": " final "}, "final"),
        (
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "one"},
                        {"type": "thinking", "thinking": "private"},
                        {"type": "text", "text": "two"},
                    ]
                },
            },
            "one\ntwo",
        ),
        ({"type": "assistant", "message": {"content": None}}, ""),
        ({"type": "result", "result": None}, ""),
    ],
)
def test_claude_final_extraction_keeps_text_and_omits_reasoning(payload, expected):
    assert (
        w.Worker._extract_claude_final_text("not-json\n" + json.dumps(payload))
        == expected
    )


def test_json_events_merge_valid_object_records_only():
    assert w.Worker._extract_json_events(
        'not-json\n[]\n{bad}\n{"type":"one"}', '{"type":"two"}\n{}'
    ) == [{"type": "one"}, {"type": "two"}]
