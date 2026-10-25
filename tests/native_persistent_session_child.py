"""Synthetic task boundaries for the real persistent terminal session."""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock


def main():
    root, mode, report_dir = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    repo = Path(__file__).resolve().parents[1]
    from rich.console import Console
    from factory.core import chat_session as c
    from factory.core.runtime import get_db_path
    from factory.core.schema import initialize_database
    from factory.core.tui.cells.history_cell import TextCell

    assert Path(c.__file__).resolve() == repo / "factory/core/chat_session.py"
    assert Path(get_db_path()).is_relative_to(root)
    initialize_database(get_db_path())
    import socket
    import subprocess
    from urllib import request

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected external request or process in native fixture")

    socket.create_connection = forbidden
    subprocess.Popen = forbidden
    request.urlopen = forbidden
    c.ChatSession._get_update_status = lambda self: None
    c._branch = lambda: "fixture-branch"
    memory = Mock()
    memory.review_turn.return_value = []
    memory.foundation_context.return_value = ""
    session = c.ChatSession(
        Console(force_terminal=True),
        profile="fixture",
        session_name="native-fixture",
        model="fixture-model",
        branch="fixture-branch",
        cwd=str(root),
        data_dir=str(root),
        mm=memory,
        interval_s=0.01,
        start_fullscreen=False,
    )
    session._tui_fullscreen = mode != "inline"
    session._fs_history_cells = [
        TextCell("\n".join(f"fixture history row {i}" for i in range(55)))
    ]
    from factory.core import cli_chat_active as active
    from factory.core.cli_chat import _load_entries
    import threading

    report_dir.mkdir(parents=True, exist_ok=True)
    event_file = report_dir / "events.jsonl"
    task_rows = {}
    sequence = []
    cancelled = []
    completed = []
    commands = []
    mouse = []
    applications = []
    invalid_exit_calls = []
    lock = threading.Lock()

    def event(kind, **fields):
        with lock:
            with event_file.open("a") as stream:
                stream.write(json.dumps({"kind": kind, **fields}) + "\n")

    def submit_task(**kwargs):
        task_id = f"native-task-{len(sequence) + 1}"
        sequence.append(kwargs["text"])
        if mode == "interrupt-before-submit":
            event("submitting", task_id=task_id)
            time.sleep(1)
        task_rows[task_id] = time.monotonic()
        trace = root / (task_id + ".jsonl")
        answer = "VISIBLE_NATIVE_ANSWER_" + task_id
        trace.write_text(
            "".join(
                json.dumps(row) + "\n"
                for row in [
                    {"kind": "model.text_delta", "round": 1, "text": answer},
                    {
                        "kind": "model.round_response",
                        "round": 1,
                        "text_preview": answer,
                        "has_tools": False,
                    },
                    {"kind": "task.finish", "status": "ok", "output_preview": answer},
                ]
            )
        )
        event("submitted", task_id=task_id, text=kwargs["text"])
        return {"task_id": task_id}

    def get_task(task_id):
        status = (
            "cancelled"
            if task_id in cancelled
            else "ok"
            if time.monotonic() - task_rows[task_id] >= 1.2
            else "processing"
        )
        return {
            "task_id": task_id,
            "status": status,
            "output_text": "VISIBLE_NATIVE_ANSWER_" + task_id if status == "ok" else "",
        }

    def cancel_task(task_id):
        cancelled.append(task_id)
        event("cancelled", task_id=task_id)

    session.submit_task = submit_task
    session._build_prompt = lambda entries, text: text
    session._pending_user_input_request = lambda task: None
    c.get_task = get_task
    c.cancel_task = active.cancel_task = cancel_task
    c._trace_path_for_task = lambda task: str(root / (task + ".jsonl"))
    actual_execute = session._execute_user_turn

    def execute(text):
        try:
            return actual_execute(text)
        finally:
            completed.append(text)
            event("completed", text=text, queue=list(session._fs_submitted))

    session._execute_user_turn = execute
    actual_command = session._handle_command

    def command(text):
        value = actual_command(text)
        commands.append(text)
        event("command", text=text)
        return value

    session._handle_command = command
    session._mouse_controller.clipboard = SimpleNamespace(
        last_backend="fixture", last_error=None, submit=lambda *args: False
    )
    actual_dispatch = session._mouse_controller.dispatch

    def dispatch(value):
        result = actual_dispatch(value)
        mouse.append({"raw": value.data, "offset": session._fs_scroll.get("offset")})
        return result

    session._mouse_controller.dispatch = dispatch
    factory = active._create_chat_application

    def create_application(*args, **kwargs):
        app, buffer, lines = factory(*args, **kwargs)
        applications.append(app)
        original_exit = app.exit

        def exit_application(*args, **kwargs):
            if not app.is_running:
                invalid_exit_calls.append(True)
            return original_exit(*args, **kwargs)

        app.exit = exit_application
        return app, buffer, lines

    active._create_chat_application = create_application
    try:
        result = session.run_fullscreen()
        report = {
            "result": result,
            "submitted": sequence,
            "completed": completed,
            "commands": commands,
            "cancelled": cancelled,
            "applications": len(applications),
            "invalid_exit_calls": len(invalid_exit_calls),
            "mouse": mouse,
            "terminal_size": list(shutil.get_terminal_size()),
            "fullscreen": session._tui_fullscreen,
            "active": session._active,
            "pending": list(session._fs_submitted),
            "external_app_cleared": session._fs_external_app is None,
            "loop_cleared": session._fs_loop is None,
            "in_turn": session._fs_in_turn,
            "history": _load_entries(session.session_path),
            "threads_before_cleanup": [
                {"name": item.name, "daemon": item.daemon}
                for item in threading.enumerate()
            ],
            "source_hashes": {
                name: hashlib.sha256((repo / name).read_bytes()).hexdigest()
                for name in [
                    "factory/core/chat_session.py",
                    "factory/core/cli_chat_active.py",
                ]
            },
        }
        (report_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    finally:
        session._mouse_controller.disable()
        session._mouse_controller.release_output()
        (report_dir / "cleanup-complete.json").write_text(
            json.dumps({"complete": True, "monotonic": time.monotonic()})
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
