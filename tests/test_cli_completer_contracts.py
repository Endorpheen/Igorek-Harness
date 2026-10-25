"""Real prompt-toolkit completions preserve unrelated composer text."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from factory.core import cli_chat_idle as idle
from factory.core.skills_manager import skills_manager
from factory.core.tui.bottom_pane import file_search_popup


@pytest.fixture
def completer(monkeypatch):
    monkeypatch.setattr(
        file_search_popup,
        "_list_repo_files",
        Mock(return_value=["src/main.py", "docs/README.md"]),
    )
    monkeypatch.setattr(skills_manager, "is_loaded", True)
    monkeypatch.setattr(
        skills_manager,
        "skills",
        {"draw": SimpleNamespace(name="draw", description="Create an image")},
    )
    return idle._make_idle_completer()


def completions(completer, text, cursor=None):
    return list(
        completer.get_completions(
            Document(text, cursor_position=cursor), CompleteEvent()
        )
    )


@pytest.mark.parametrize(
    "text", ["", "hello", "ordinary @text", "normal $text", "normal /help"]
)
def test_normal_prose_has_no_global_completions(completer, text):
    assert completions(completer, text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@MAIN", "src/main.py"),
        ("@readme", "docs/README.md"),
        ("$IMAGE", "$draw "),
        ("/HELP", "/help "),
    ],
)
def test_matching_is_case_insensitive_and_applies_to_document(
    completer, text, expected
):
    result = completions(completer, text)
    assert result
    item = result[0]
    document = Document(text)
    applied = document.text_before_cursor[: len(text) + item.start_position] + item.text
    assert applied == expected


@pytest.mark.parametrize(
    "text",
    [
        "/help\nkeep my text",
        "@main\nkeep my text",
        "$draw\nkeep my text",
        "/help\n",
        "@main\n",
        "$draw\n",
    ],
)
def test_first_line_trigger_does_not_complete_over_following_lines(completer, text):
    assert completions(completer, text) == []


def test_completion_before_newline_preserves_text_after_cursor(completer):
    text = "/hel\nkeep my text"
    item = completions(completer, text, 4)[0]
    document = Document(text, cursor_position=4)
    assert (
        document.text_before_cursor[: 4 + item.start_position]
        + item.text
        + document.text_after_cursor
        == "/help \nkeep my text"
    )


def test_file_list_is_cached_between_queries(completer):
    assert completions(completer, "@main")
    assert completions(completer, "@README")
    file_search_popup._list_repo_files.assert_called_once()
