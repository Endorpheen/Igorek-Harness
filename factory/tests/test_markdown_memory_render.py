from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.tui.markdown_render import render_markdown_to_lines


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _plain(lines: list[str]) -> str:
    return ANSI_RE.sub("", "\n".join(lines))


def test_memory_write_block_passes_through_as_raw_text():
    """MEMORY_WRITE tags are no longer processed — they pass through as raw text."""
    lines = render_markdown_to_lines(
        "[[MEMORY_WRITE]]\n"
        "scope: long\n"
        "fact: Пользователь просит после технических терминов давать простое объяснение.\n"
        "[[/MEMORY_WRITE]]",
        width=100,
    )
    plain = _plain(lines)
    assert "[[MEMORY_WRITE]]" in plain
    assert "[[/MEMORY_WRITE]]" in plain
    assert "простое объяснение" in plain


def test_memory_write_block_daily_scope_passes_through():
    lines = render_markdown_to_lines(
        "[[MEMORY_WRITE]]\n"
        "scope: daily\n"
        "fact: Сегодня короткий факт.\n"
        "[[/MEMORY_WRITE]]",
        width=80,
    )
    plain = _plain(lines)
    assert "daily" in plain
    assert "[[MEMORY_WRITE]]" in plain


def test_memory_write_block_surrounding_markdown_still_renders():
    text = (
        "Ответ пользователю.\n\n"
        "[[MEMORY_WRITE]]\n"
        "scope: long\n"
        "fact: Сохранённый факт.\n"
        "[[/MEMORY_WRITE]]\n\n"
        "Продолжение ответа."
    )
    lines = render_markdown_to_lines(text, width=80)
    plain = _plain(lines)
    assert "Ответ пользователю." in plain
    assert "Сохранённый факт" in plain
    assert "Продолжение ответа." in plain
    assert "[[MEMORY_WRITE]]" in plain
    assert "[[/MEMORY_WRITE]]" in plain


def test_memory_write_block_raw_tags_keep_original_alignment():
    """Raw tags produce lines with the same length (no transform applied)."""
    lines = render_markdown_to_lines(
        "[[MEMORY_WRITE]]\n"
        "scope: long\n"
        "fact: Факт.\n"
        "[[/MEMORY_WRITE]]",
        width=300,
    )
    plain_lines = [ANSI_RE.sub("", line) for line in lines]
    assert "[[MEMORY_WRITE]]" in plain_lines[0]
    assert len(lines) <= 5  # small input


def test_memory_write_block_narrow_width():
    lines = render_markdown_to_lines(
        "[[MEMORY_WRITE]]\n"
        "scope: long\n"
        "fact: Факт.\n"
        "[[/MEMORY_WRITE]]",
        width=20,
    )
    plain_lines = [ANSI_RE.sub("", line) for line in lines]
    assert "[[MEMORY_WRITE]]" in plain_lines[0]
    assert "[[/MEMORY_WRITE]]" in plain_lines[-1]
