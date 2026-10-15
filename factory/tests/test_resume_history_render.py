from __future__ import annotations

import sys
import json
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import cli_chat_rich as rich_chat
from core.cli_chat_rich import _replay_initial_history
from core.trace_taxonomy import CcTraceEvent


def test_resume_replays_full_user_message_without_500_char_truncation():
    long_user_text = "start\n" + ("x" * 520) + "\nunique-tail-after-limit"
    console = Console(record=True, width=120, force_terminal=False)

    _replay_initial_history(console, [{"role": "user", "text": long_user_text}])

    rendered = console.export_text()
    assert "unique-tail-after-limit" in rendered


def test_resume_restores_reasoning_state_from_last_task_trace(tmp_path, monkeypatch):
    trace_path = tmp_path / "cc_backend_trace.jsonl"
    events = [
        {"kind": CcTraceEvent.MODEL_REASONING_DELTA, "text": "Проверяю факты.\n"},
        {"kind": CcTraceEvent.MODEL_ROUND_RESPONSE, "text_full": "Готово."},
        {"kind": CcTraceEvent.TASK_FINISH, "status": "ok"},
    ]
    trace_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rich_chat, "_trace_path_for_task", lambda _task_id: trace_path)
    state = {"visible": False, "lines": [], "expanded_lines": []}

    rich_chat._restore_resume_reasoning_state(
        [{"role": "assistant", "text": "Готово.", "task_id": "task_12345678"}],
        state,
        width=120,
    )

    assert state["lines"]
    assert state["expanded_lines"]
    assert "Размышления модели" in "\n".join(state["lines"])
    assert "Проверяю факты" in "\n".join(state["expanded_lines"])
