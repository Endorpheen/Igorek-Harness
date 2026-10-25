"""Chat lifecycle contracts with real temporary history and fake task/UI boundaries."""

import copy
import io
import json
from unittest.mock import Mock

import pytest
from rich.console import Console

from factory.core import chat_session as c
from factory.core.schema import initialize_database
from factory.core.runtime import get_db_path
from factory.core.tui.cells.history_cell import TextCell


@pytest.fixture
def session(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    profiles = root / "profiles"
    profiles.mkdir(parents=True)
    for name, model in [("fixture", "model-one"), ("second", "model-two")]:
        (profiles / (name + ".json")).write_text(
            json.dumps(
                {
                    "backend": "cc-backend",
                    "model": model,
                    "base_url": "http://127.0.0.1:1",
                    "api_format": "openai",
                    "filesystem_mode": "workspace",
                }
            )
        )
    for key in ("FACTORY_DATA_DIR", "IGOREK_FACTORY_HOME"):
        monkeypatch.setenv(key, str(root))
    monkeypatch.setenv("FACTORY_PROFILES_DIR", str(profiles))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FACTORY_SESSION_EMBEDDINGS_ENABLED", "off")
    monkeypatch.setattr(
        c.ChatSession, "_make_mouse_controller", lambda _: Mock(_app=None)
    )
    monkeypatch.setattr(c.ChatSession, "_get_update_status", lambda _: None)
    monkeypatch.setattr(c, "_terminal_width", lambda: 80)
    initialize_database(get_db_path())
    memory = Mock()
    memory.review_turn.return_value = []
    memory.foundation_context.return_value = ""
    output = io.StringIO()
    result = c.ChatSession(
        Console(file=output, width=80, force_terminal=False),
        profile="fixture",
        session_name="fixture-session",
        model="model-one",
        branch="main",
        cwd=str(tmp_path),
        data_dir=str(root),
        mm=memory,
        interval_s=0.001,
        start_fullscreen=False,
    )
    result.output_fixture = output
    return result


def entries(session):
    return c._load_entries(session.session_path)


def test_constructor_and_startup_preserve_existing_history(session):
    c._write_session_meta(
        session.session_path,
        session_id=session.session_name,
        profile=session.profile,
        model=session.model,
    )
    c._append_entry(session.session_path, role="user", text="hello")
    c._append_entry(
        session.session_path,
        role="assistant",
        text="answer",
        task_id="fixture-task",
        status="ok",
    )
    session._startup()
    assert session.sl.task_ids == ["fixture-task"] and session._startup_lines
    assert [entry["text"] for entry in entries(session)] == ["hello", "answer"]
    assert c._read_session_meta(session.session_path)["profile"] == "fixture"


def test_empty_startup_writes_metadata_without_inventing_turns(session):
    session._startup()
    assert (
        entries(session) == []
        and c._read_session_meta(session.session_path)["model"] == "model-one"
    )
    assert (
        session.widget.model == "model-one" and not session.reasoning_state["visible"]
    )


@pytest.mark.parametrize(
    "command,expected",
    [
        ("/help", "/resume"),
        ("/debug", "Debug: on"),
        ("/status", "fixture-session"),
        ("/history", "History is empty"),
        ("/model", "model-one"),
        ("/provider", "Provider:"),
        ("/profile", "fixture"),
    ],
)
def test_local_commands_report_current_session_state(session, command, expected):
    session._handle_command(command)
    assert expected in session.output_fixture.getvalue()


def test_clear_resets_only_current_history_and_scroll(session):
    c._append_entry(session.session_path, role="user", text="discard")
    session._fs_history_cells = [TextCell("discard")]
    session._fs_scroll = {"offset": 10, "follow": False}
    session._cmd_clear()
    assert (
        entries(session) == []
        and session._fs_history_cells == []
        and session._fs_scroll == {"offset": 0, "follow": True}
    )


def test_profile_change_updates_metadata_and_releases_old_session_approvals(session):
    session._startup()
    session.approved_session_rules = [{"fixture": "old"}]
    callback = Mock()
    session.on_profile_change = callback
    session._cmd_profile("/profile second")
    callback.assert_called_once_with("second")
    assert (
        session.profile == "second"
        and session.model == "model-two"
        and session.widget.model == "model-two"
    )
    assert session.approved_session_rules == [] and session.model_override is None
    assert c._read_session_meta(session.session_path)["profile"] == "second"


def test_failed_profile_change_preserves_active_binding(session):
    session.on_profile_change = Mock(side_effect=RuntimeError("fixture cannot switch"))
    session._cmd_profile("/profile second")
    assert session.profile == "fixture" and session.model == "model-one"
    assert "fixture cannot switch" in session.output_fixture.getvalue()


def test_unknown_command_and_missing_profile_are_actionable(session):
    session._handle_command("/unknown")
    session._cmd_profile("/profile missing")
    assert "Unknown command" in session.output_fixture.getvalue()
    assert session.profile == "fixture"


def test_history_command_includes_both_roles_and_skips_no_messages(session):
    c._append_entry(session.session_path, role="user", text="user fixture")
    c._append_entry(session.session_path, role="assistant", text="assistant fixture")
    session._cmd_history()
    assert (
        "user fixture" in session.output_fixture.getvalue()
        and "assistant fixture" in session.output_fixture.getvalue()
    )


@pytest.mark.parametrize(
    "elapsed,label", [(0, "0s"), (61, "1m 01s"), (3661, "1h 01m 01s")]
)
def test_elapsed_labels_keep_units(elapsed, label):
    assert c._elapsed_label(elapsed) == label


def test_trace_reader_advances_past_blank_and_malformed_lines(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text('{"kind":"one"}\n\nnot-json\n{"kind":"two"}\n')
    assert c._read_new_trace_events(str(path), 0) == (
        [{"kind": "one"}, {"kind": "two"}],
        4,
    )
    assert c._read_new_trace_events(str(path), 4) == ([], 4)
    assert c._read_new_trace_events(str(tmp_path / "missing"), 3) == ([], 3)


def prepare_turn(session, monkeypatch, outcomes):
    from factory.core import cli_chat_rich as rich

    monkeypatch.setattr(rich, "_set_terminal_title", lambda _: None)
    monkeypatch.setattr(session, "_notify_turn_complete", Mock())
    monkeypatch.setattr(session, "_extract_images", lambda text: (text, []))
    calls = []
    pending = iter(outcomes)

    def execute(prompt, meta, files):
        calls.append((prompt, copy.deepcopy(meta), files))
        return next(pending)

    monkeypatch.setattr(session, "_execute_one_turn", execute)
    return calls


@pytest.mark.parametrize("status", ["ok", "fail", "dead_letter"])
def test_completed_turn_persists_actual_status_and_answer(session, monkeypatch, status):
    calls = prepare_turn(
        session,
        monkeypatch,
        [{"task_id": "fixture-task", "status": status, "output_text": "actual answer"}],
    )
    session._execute_user_turn("question")
    saved = entries(session)
    assert (
        len(saved) == 2
        and saved[0]["role"] == "user"
        and saved[0]["text"] == "question"
    )
    assert (
        saved[1]["status"] == status
        and saved[1]["text"] == "actual answer"
        and saved[1]["task_id"] == "fixture-task"
    )
    assert (
        calls[0][1]["cwd"] == session.cwd
        and calls[0][1]["session"] == "fixture-session"
    )
    session.mm.review_turn.assert_called_once()


def test_no_task_result_does_not_invent_answer_or_history(session, monkeypatch):
    prepare_turn(session, monkeypatch, [None])
    session.mq.enqueue("follow-up")
    session._execute_user_turn("question")
    assert entries(session) == [] and session.pending_inputs == ["follow-up"]
    session.mm.review_turn.assert_not_called()


def test_approved_tool_is_resumed_and_user_turn_is_saved_once(
    session, monkeypatch, tmp_path
):
    blocked = {
        "tool": "Write",
        "target": str(tmp_path / "artifact.txt"),
        "signature": "fixture-signature",
        "input": {"file_path": str(tmp_path / "artifact.txt"), "content": "fixture"},
    }
    calls = prepare_turn(
        session,
        monkeypatch,
        [
            {
                "task_id": "first",
                "status": "ok",
                "output_text": "approval required",
                "approval_blocked_tools": [blocked],
            },
            {"task_id": "second", "status": "ok", "output_text": "finished"},
        ],
    )
    monkeypatch.setattr(session, "_ask_approval", lambda _: "allow_session")
    session._execute_user_turn("write fixture")
    assert len(calls) == 2 and calls[1][1]["approved_tool_calls"] == [
        "fixture-signature"
    ]
    assert calls[1][1]["approved_session_rules"] and session.approved_session_rules
    assert [e["text"] for e in entries(session) if e["role"] == "user"] == [
        "write fixture"
    ]
    assert entries(session)[-1]["text"] == "finished"
    assert not (tmp_path / "artifact.txt").exists()


def test_rejection_never_resubmits_blocked_action(session, monkeypatch, tmp_path):
    blocked = {
        "tool": "Write",
        "target": str(tmp_path / "artifact.txt"),
        "signature": "fixture-signature",
        "input": {"file_path": str(tmp_path / "artifact.txt"), "content": "fixture"},
    }
    calls = prepare_turn(
        session,
        monkeypatch,
        [
            {
                "task_id": "first",
                "status": "ok",
                "output_text": "approval required",
                "approval_blocked_tools": [blocked],
            }
        ],
    )
    monkeypatch.setattr(session, "_ask_approval", lambda _: "reject")
    session._execute_user_turn("write fixture")
    assert len(calls) == 1 and not session.approved_session_rules
    assert sum(e["role"] == "user" for e in entries(session)) == 1
    assert entries(session)[-1]["text"] and not (tmp_path / "artifact.txt").exists()


def test_fullscreen_turn_keeps_previous_cells_and_resets_reasoning(
    session, monkeypatch
):
    session._tui_fullscreen = True
    previous = TextCell("prior answer")
    session.widget.committed_cells = [previous]
    session.reasoning_state = {
        "visible": True,
        "lines": ["old"],
        "expanded_lines": ["old"],
    }
    prepare_turn(
        session,
        monkeypatch,
        [{"task_id": "fixture", "status": "ok", "output_text": "new answer"}],
    )
    session._execute_user_turn("new question")
    assert (
        previous in session._fs_history_cells and not session.reasoning_state["visible"]
    )
    assert (
        session.widget.committed_cells[0].role == "user"
        and session.widget.committed_cells[0].text == "new question"
    )
    assert entries(session)[-1]["text"] == "new answer"


def test_persistent_history_viewer_keeps_roles_and_current_committed_cells(session):
    c._append_entry(session.session_path, role="user", text="stored question")
    c._append_entry(
        session.session_path, role="assistant", text="stored answer\nsecond line"
    )
    c._append_entry(session.session_path, role="system", text="stored context")
    session.widget.committed_cells = [TextCell("live fixture")]
    text = "\n".join(session._history_lines_for_viewer())
    assert all(
        value in text
        for value in ["stored question", "stored answer", "second line", "live fixture"]
    )
    assert session._current_viewer_items()


def test_reasoning_toggle_only_changes_display_state(session):
    session.reasoning_state = {
        "visible": False,
        "lines": ["thought"],
        "expanded_lines": ["thought"],
    }
    session._mouse_toggle_reasoning_cell("one")
    assert session.reasoning_state["cell_overrides"] == {"one": True}
    session._mouse_toggle_reasoning()
    assert (
        session.reasoning_state["visible"]
        and session.reasoning_state["cell_overrides"] == {}
    )


@pytest.mark.parametrize("status", ["ok", "fail", "dead_letter"])
def test_one_turn_headless_poll_finishes_and_reports_task_status(
    session, monkeypatch, tmp_path, status
):
    from factory.core import cli_chat_active as active

    monkeypatch.setattr(
        active, "_create_chat_application", lambda **_: (None, None, None)
    )
    monkeypatch.setattr(
        c,
        "get_task",
        lambda _: {
            "task_id": "fixture-task",
            "status": status,
            "output_text": "fixture answer",
        },
    )
    trace = tmp_path / "empty-trace"
    trace.write_text("")
    monkeypatch.setattr(c, "_trace_path_for_task", lambda _: str(trace))
    session.submit_task = Mock(return_value={"task_id": "fixture-task"})
    session.before_turn = Mock()
    result = session._execute_one_turn("prompt", {"source": "fixture"})
    assert result["status"] == status and result["_worked_for_label"].startswith(
        "Worked for "
    )
    session.before_turn.assert_called_once_with("fixture")
    assert session.sl.task_ids == ["fixture-task"]
    assert session.submit_task.call_args.kwargs["text"] == "prompt"


def test_missing_task_is_explicit_not_success(session, monkeypatch, tmp_path):
    from factory.core import cli_chat_active as active

    monkeypatch.setattr(
        active, "_create_chat_application", lambda **_: (None, None, None)
    )
    monkeypatch.setattr(c, "get_task", lambda _: None)
    monkeypatch.setattr(c, "_trace_path_for_task", lambda _: str(tmp_path / "missing"))
    session.submit_task = Mock(return_value={"task_id": "missing"})
    with pytest.raises(RuntimeError, match="Task not found"):
        session._execute_one_turn("prompt", {})


def test_classic_run_dispatches_pending_input_and_cleans_mouse(session, monkeypatch):
    monkeypatch.setattr(session, "_startup", lambda: None)
    monkeypatch.setattr(session, "_goodbye", Mock())
    handled = []
    monkeypatch.setattr(session, "_handle_input", handled.append)
    session.pending_inputs = ["hello", "/exit"]
    assert session.run() == 0 and handled == ["hello"]
    session._mouse_controller.disable.assert_called_once()
    session._mouse_controller.release_output.assert_called_once()


def test_fullscreen_exit_does_not_cancel_already_completed_task(session, monkeypatch):
    monkeypatch.setattr(session, "_startup", lambda: None)
    monkeypatch.setattr(session, "_goodbye", lambda: None)
    session.exit_on_interrupt = True
    session._current_task_id = "fixture-task"

    def finish():
        session._active = False

    monkeypatch.setattr(session, "run_fullscreen", finish)
    monkeypatch.setattr(c, "get_task", lambda _: {"status": "ok"})
    cancel = Mock()
    monkeypatch.setattr(c, "cancel_task", cancel)
    assert session.run() == 0
    cancel.assert_not_called()


@pytest.mark.parametrize("positional", [False, True])
def test_widget_full_state_extension_preserves_constructor_session_metadata(positional):
    widget = (
        c.ChatWidget(80, "fixture-model", "fixture-branch", "/tmp/fixture")
        if positional
        else c.ChatWidget(
            model="fixture-model", branch="fixture-branch", cwd="/tmp/fixture"
        )
    )
    assert (widget.model, widget.branch, widget.cwd) == (
        "fixture-model",
        "fixture-branch",
        "/tmp/fixture",
    )
