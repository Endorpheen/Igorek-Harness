from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli_chat_active import (
    _running_composer_status_lines,
    _running_footer_status_lines,
    _running_status_lines,
)


def test_running_status_uses_codex_like_working_label():
    lines = _running_status_lines({"t0": time.monotonic() - 9})

    assert lines
    assert lines[0].startswith("  • Working (")
    assert "esc to interrupt" in lines[0]
    assert " · " in lines[0]
    assert "Thinking" not in lines[0]


def test_done_status_uses_worked_for_label():
    lines = _running_status_lines({"t0": time.monotonic() - 90, "task_done": True})

    assert lines
    assert lines[0].startswith("  Worked for ")
    assert "Thinking" not in lines[0]


def test_composer_status_omits_footer_statusline():
    class StatusLine:
        class Config:
            padding = 0
        config = Config()

        def toolbar_text(self):
            return "project footer"

    running = {"t0": time.monotonic() - 3}

    composer_lines = _running_composer_status_lines(running)
    fallback_lines = _running_status_lines(running, StatusLine())
    footer_lines = _running_footer_status_lines(StatusLine())

    assert "project footer" not in "\n".join(composer_lines)
    assert "project footer" in "\n".join(fallback_lines)
    assert footer_lines == ["  project footer"]
