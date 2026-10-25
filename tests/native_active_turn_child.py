"""Synthetic task boundary for real terminal acceptance; no worker is launched."""

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
    session._tui_fullscreen = mode in {"mouse", "escape", "cancelled"}
    session._fs_history_cells = [
        TextCell("\n".join(f"fixture history row {i}" for i in range(55)))
    ]
    trace = root / "trace.jsonl"
    events = [
        {
            "kind": "model.reasoning_delta",
            "round": 1,
            "text": "SYNTHETIC_PRIVATE_THOUGHT",
        },
        {"kind": "model.text_delta", "round": 1, "text": "VISIBLE_NATIVE_ANSWER"},
        {
            "kind": "model.round_response",
            "round": 1,
            "text_preview": "VISIBLE_NATIVE_ANSWER",
            "has_tools": False,
        },
        {
            "kind": "task.finish",
            "status": "ok",
            "output_preview": "VISIBLE_NATIVE_ANSWER",
        },
    ]
    if mode == "cancelled":
        events = [{"kind": "task.finish", "status": "cancelled"}]
    trace.write_text("".join(json.dumps(row) + "\n" for row in events))
    session.submit_task = lambda **kwargs: {"task_id": "native-task"}
    session._pending_user_input_request = lambda task: None
    c._trace_path_for_task = lambda task: str(trace)
    started = time.monotonic()
    cancelled = []
    calls = []

    def get_task(task):
        calls.append(task)
        terminal = time.monotonic() - started >= 1.5
        status = (
            ("cancelled" if mode == "cancelled" else "ok") if terminal else "processing"
        )
        if mode in {"interrupt", "escape"}:
            status = "processing"
        return {
            "task_id": task,
            "status": status,
            "output_text": "VISIBLE_NATIVE_ANSWER" if status == "ok" else "",
        }

    c.get_task = get_task
    c.cancel_task = lambda task: cancelled.append(task)
    clipboard = SimpleNamespace(
        last_backend="fixture", last_error=None, submit=lambda *args: False
    )
    session._mouse_controller.clipboard = clipboard
    dispatch = session._mouse_controller.dispatch
    mouse = []

    def observed(event):
        before = dict(session._fs_scroll)
        value = dispatch(event)
        mouse.append(
            {
                "raw": event.data,
                "verdict": value,
                "before": before,
                "after": dict(session._fs_scroll),
            }
        )
        return value

    session._mouse_controller.dispatch = observed
    from factory.core import cli_chat_active as active

    original_factory = active._create_chat_application
    finished_exit_calls = []

    def create_application(*args, **kwargs):
        app, buffer, lines = original_factory(*args, **kwargs)
        original_exit = app.exit

        def exit_application(*exit_args, **exit_kwargs):
            if not app.is_running:
                finished_exit_calls.append(True)
            return original_exit(*exit_args, **exit_kwargs)

        app.exit = exit_application
        return app, buffer, lines

    active._create_chat_application = create_application
    report_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = session._execute_one_turn(
            "Synthetic native turn", {"source": "fixture"}
        )
        result_status = result.get("status") if isinstance(result, dict) else None
        report = {
            "result_status": result_status,
            "finished_exit_calls": len(finished_exit_calls),
            "cancelled": cancelled,
            "pending": session.mq.peek_all(),
            "mouse": mouse,
            "terminal_size": list(shutil.get_terminal_size()),
            "get_task_calls": len(calls),
            "elapsed": time.monotonic() - started,
            "source_hashes": {
                p: hashlib.sha256((repo / p).read_bytes()).hexdigest()
                for p in [
                    "factory/core/chat_session.py",
                    "factory/core/cli_chat_active.py",
                ]
            },
        }
        (report_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
    finally:
        session._mouse_controller.disable()
        session._mouse_controller.release_output()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
