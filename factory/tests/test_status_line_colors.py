from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.status_line import ANSI_RE, render_builtin_status_line, render_configured_status_line


def _status_input() -> dict:
    return {
        "session_id": "chat-1",
        "cwd": "/home/user/project",
        "version": "0.1.586",
        "backend": {"profile": "cc-minimax"},
        "model": {"id": "MiniMax-M3", "display_name": "MiniMax-M3"},
        "workspace": {
            "current_dir": "/home/user/project",
            "project_dir": "/home/user/project",
        },
        "git": {"branch": "notebook-sessions", "is_clean": False},
        "cost": {"total_cost_usd": 0.0},
        "context_window": {
            "current_context_tokens": 91_000,
            "context_window_size": 100_000,
            "used_percentage": 91,
        },
    }


def test_builtin_status_line_colors_segments_but_keeps_plain_text_readable():
    rendered = render_builtin_status_line(_status_input())

    assert "\033[" in rendered
    plain = ANSI_RE.sub("", rendered)
    assert "cc-minimax" in plain
    assert "MiniMax-M3" in plain
    assert "notebook-sessions*" in plain
    assert "ctx 91.0k/100.0k" in plain
    assert "91% used" in plain
    assert "/home/user/project" in plain


def test_configured_status_line_colors_selected_items():
    rendered = render_configured_status_line(
        _status_input(),
        ("profile", "git-branch", "context-used", "factory-version"),
    )

    assert "\033[" in rendered
    plain = ANSI_RE.sub("", rendered)
    assert plain == "cc-minimax · notebook-sessions* · ctx 91.0k/100.0k 91% used · 0.1.586"
