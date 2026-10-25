"""Idle composer completion and key contracts without touching a real terminal."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import to_plain_text
from prompt_toolkit.validation import ValidationError

from factory.core import cli_chat_idle as idle


@pytest.fixture
def prompts(tmp_path, monkeypatch):
    from factory.core.tui.bottom_pane import command_popup, file_search_popup

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        idle, "PromptSession", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(
        file_search_popup,
        "_list_repo_files",
        lambda cwd: ["src/main.py", "docs/help.md", "src/other.py"],
    )
    monkeypatch.setattr(
        command_popup,
        "_COMMANDS",
        [
            ("/statusline", "Configure line"),
            ("/status", "Current state"),
            ("/skills", "Skills"),
            ("/exit", "Exit"),
        ],
    )
    monkeypatch.setattr(
        idle,
        "_clipboard_image_path",
        Mock(side_effect=AssertionError("Unexpected clipboard access")),
    )
    return idle._make_prompt_session


def completions(session, text):
    return list(
        session.completer.get_completions(
            Document(text, cursor_position=len(text)),
            CompleteEvent(completion_requested=True),
        )
    )


def invoke(session, key, text=""):
    key = {"tab": "c-i", "enter": "c-m"}.get(key, key)
    binding = next(
        binding
        for binding in session.key_bindings.bindings
        if len(binding.keys) == 1
        and getattr(binding.keys[0], "value", binding.keys[0]) == key
    )
    event = SimpleNamespace(
        current_buffer=Buffer(document=Document(text)),
        app=SimpleNamespace(invalidate=Mock(), exit=Mock()),
    )
    binding.handler(event)
    return event


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/", ["/statusline ", "/status ", "/skills ", "/exit "]),
        ("/s", ["/statusline ", "/status ", "/skills "]),
        ("/STATUS", ["/status ", "/statusline "]),
        ("/exit", ["/exit "]),
        ("/unknown", []),
        ("ordinary text", []),
        ("", []),
    ],
)
def test_slash_completion_matches_prefix_and_prioritizes_exact_command(
    prompts, text, expected
):
    session = prompts()
    result = completions(session, text)
    assert [row.text for row in result] == expected
    assert all(row.start_position == -len(text) for row in result)


def test_file_completion_filters_and_reuses_discovered_files(prompts, monkeypatch):
    from factory.core.tui.bottom_pane import file_search_popup

    discover = Mock(return_value=["src/main.py", "docs/help.md", "src/other.py"])
    monkeypatch.setattr(file_search_popup, "_list_repo_files", discover)
    session = prompts()
    rows = completions(session, "@SRC/")
    assert [row.text for row in rows] == ["src/main.py", "src/other.py"]
    assert all(row.start_position == -5 for row in rows)
    assert [row.text for row in completions(session, "@help")] == ["docs/help.md"]
    assert len(completions(session, "@")) == 3
    discover.assert_called_once()


def test_file_and_skill_popups_bound_result_count(prompts, monkeypatch):
    from factory.core.tui.bottom_pane import file_search_popup
    from factory.core import skills_manager

    monkeypatch.setattr(
        file_search_popup,
        "_list_repo_files",
        lambda cwd: [f"file{i}.py" for i in range(30)],
    )
    catalog = {
        str(i): SimpleNamespace(name=f"skill{i}", description="x" * 80)
        for i in range(30)
    }
    monkeypatch.setattr(
        skills_manager,
        "skills_manager",
        SimpleNamespace(is_loaded=True, skills=catalog),
    )
    session = prompts()
    assert len(completions(session, "@")) == 20
    rows = completions(session, "$")
    assert len(rows) == 20
    assert rows[0].display_meta_text == "x" * 60


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$", ["$alpha ", "$beta "]),
        ("$ALPHA", ["$alpha "]),
        ("$charts", ["$beta "]),
        ("$absent", []),
    ],
)
def test_skill_completion_matches_name_or_description(
    prompts, monkeypatch, text, expected
):
    from factory.core import skills_manager

    manager = SimpleNamespace(
        is_loaded=True,
        skills={
            "alpha": SimpleNamespace(name="alpha", description=None),
            "beta": SimpleNamespace(name="beta", description="Build charts"),
        },
    )
    monkeypatch.setattr(skills_manager, "skills_manager", manager)
    result = completions(prompts(), text)
    assert [row.text for row in result] == expected
    assert all(row.start_position == -len(text) for row in result)


def test_skill_lazy_loading_is_silent_and_not_repeated(prompts, monkeypatch, capsys):
    from factory.core import skills_manager

    manager = SimpleNamespace(is_loaded=False, skills={}, load_settings=Mock())

    def load():
        print("fixture skills loading output")
        manager.skills = {"alpha": SimpleNamespace(name="alpha", description="fixture")}
        manager.is_loaded = True

    manager.load_skills = Mock(side_effect=load)
    monkeypatch.setattr(skills_manager, "skills_manager", manager)
    session = prompts()
    assert completions(session, "$alpha")[0].text == "$alpha "
    assert completions(session, "$alpha")[0].text == "$alpha "
    manager.load_settings.assert_called_once_with()
    manager.load_skills.assert_called_once_with()
    assert "fixture skills loading output" not in capsys.readouterr().out


def test_skill_catalog_failure_leaves_input_usable(prompts, monkeypatch):
    from factory.core import skills_manager

    monkeypatch.setattr(
        skills_manager,
        "skills_manager",
        SimpleNamespace(
            is_loaded=False,
            load_settings=Mock(side_effect=OSError("fixture DB unavailable")),
        ),
    )
    session = prompts()
    assert completions(session, "$alpha") == []
    assert completions(session, "/exit")[0].text == "/exit "


@pytest.mark.parametrize("text", ["", " ", "\n\t"])
def test_prompt_validator_rejects_empty_messages(prompts, text):
    with pytest.raises(ValidationError):
        prompts().validator.validate(Document(text))


def test_prompt_validator_accepts_nonempty_unicode(prompts):
    assert prompts().validator.validate(Document("Привет 🐙")) is None


def test_newline_key_edits_buffer_without_submitting(prompts):
    event = invoke(prompts(), "c-j", "first")
    assert event.current_buffer.text == "first\n"
    event.app.exit.assert_not_called()


def test_tab_applies_selected_completion_and_handles_absence(prompts, monkeypatch):
    session = prompts()
    monkeypatch.setattr(
        idle,
        "_completion_to_apply",
        lambda buffer: Completion("/status ", start_position=-3),
    )
    assert invoke(session, "tab", "/st").current_buffer.text == "/status "
    monkeypatch.setattr(idle, "_completion_to_apply", lambda buffer: None)
    assert invoke(session, "tab", "/st").current_buffer.text == "/st"


def test_transcript_hotkey_submits_local_command(prompts):
    session = prompts()
    seen = []
    binding = next(
        binding
        for binding in session.key_bindings.bindings
        if tuple(getattr(key, "value", key) for key in binding.keys) == ("c-t",)
    )
    buffer = Buffer(
        validator=session.validator,
        accept_handler=lambda buf: seen.append(buf.text) or True,
    )
    binding.handler(SimpleNamespace(current_buffer=buffer))
    assert seen == ["__transcript__"]


def test_enter_submits_current_text(prompts):
    session = prompts()
    seen = []
    binding = next(
        binding
        for binding in session.key_bindings.bindings
        if tuple(getattr(key, "value", key) for key in binding.keys) == ("c-m",)
    )
    buffer = Buffer(
        document=Document("Привет"),
        validator=session.validator,
        accept_handler=lambda buf: seen.append(buf.text) or True,
    )
    binding.handler(SimpleNamespace(current_buffer=buffer))
    assert seen == ["Привет"]


@pytest.mark.parametrize("width", [40, 100])
def test_empty_question_mark_opens_help_and_escape_closes_it(
    prompts, monkeypatch, width
):
    monkeypatch.setenv("COLUMNS", str(width))
    monkeypatch.setenv("LINES", "24")
    session = prompts()
    invoke(session, "?")
    overlay = to_plain_text(session.bottom_toolbar())
    assert "ctrl+j" in overlay and "shortcuts" in overlay
    invoke(session, "escape")
    assert "Enter — send" in to_plain_text(session.bottom_toolbar())
    assert invoke(session, "?", "question").current_buffer.text == "question?"


@pytest.mark.parametrize("fallback", ["ansi", "html", "plain"])
def test_toolbar_puts_workspace_on_separate_line(prompts, monkeypatch, fallback):
    if fallback != "ansi":
        monkeypatch.setattr(idle, "ANSI", None)
    if fallback == "plain":
        monkeypatch.setattr(idle, "HTML", None)
    status = SimpleNamespace(
        toolbar_text=lambda: "profile · model · ~/fixture/workspace",
        config=SimpleNamespace(padding=2),
    )
    session = prompts(status_line=status)
    text = to_plain_text(session.bottom_toolbar())
    assert "Enter — send" in text and "  profile · model\n~/fixture/workspace" in text


@pytest.mark.parametrize(
    "visible,lines,expanded,expected",
    [
        (False, ["", "brief"], ["detail"], "brief"),
        (True, ["brief"], ["detail"], "detail"),
        (False, [], ["detail"], "Размышления модели доступны"),
        (False, [], [], ""),
    ],
)
def test_reasoning_prompt_shows_only_selected_detail(
    prompts, visible, lines, expanded, expected
):
    state = {"visible": visible, "lines": lines, "expanded_lines": expanded}
    text = to_plain_text(prompts(reasoning_state=state).message())
    assert expected in text
    if not visible:
        assert "detail" not in text


@pytest.mark.parametrize("toggle_exits", [False, True])
def test_reasoning_toggle_invalidates_or_exits_for_redraw(prompts, toggle_exits):
    state = {"visible": False, "lines": ["brief"], "expanded_lines": ["detail"]}
    session = prompts(reasoning_state=state, reasoning_toggle_exits=toggle_exits)
    event = invoke(session, "c-o")
    assert state["visible"] is True
    if toggle_exits:
        assert isinstance(
            event.app.exit.call_args.kwargs["exception"], idle._IdleReasoningToggle
        )
        event.app.invalidate.assert_not_called()
    else:
        event.app.invalidate.assert_called_once_with()
        event.app.exit.assert_not_called()


def test_reasoning_toggle_without_reasoning_is_noop(prompts):
    event = invoke(prompts(), "c-o")
    event.app.invalidate.assert_not_called()
    event.app.exit.assert_not_called()


def test_factory_allows_disabled_toolbar_and_missing_prompt_dependency(
    prompts, monkeypatch
):
    assert prompts(toolbar_enabled=False).bottom_toolbar is None
    monkeypatch.setattr(idle, "PromptSession", None)
    assert prompts() is None
