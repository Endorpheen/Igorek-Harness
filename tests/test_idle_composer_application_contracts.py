"""Application composer callbacks use real buffers, layout and key bindings."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import to_plain_text
from prompt_toolkit.output import DummyOutput

from factory.core import cli_chat_idle as idle


@pytest.fixture
def composer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        idle,
        "Application",
        lambda **kwargs: SimpleNamespace(**kwargs, invalidate=Mock(), exit=Mock()),
    )
    monkeypatch.setattr(
        idle,
        "_clipboard_image_path",
        Mock(side_effect=AssertionError("Unexpected clipboard image read")),
    )
    monkeypatch.setattr(
        idle,
        "_clipboard_text",
        Mock(side_effect=AssertionError("Unexpected clipboard text read")),
    )
    return idle._create_idle_composer_application


def press(app, buffer, key, arg=None):
    key = {"enter": "c-m", "tab": "c-i"}.get(key, key)
    binding = next(
        binding
        for binding in app.key_bindings.bindings
        if tuple(getattr(item, "value", item) for item in binding.keys) == (key,)
    )
    event = SimpleNamespace(
        app=app, current_buffer=buffer, key_sequence=[SimpleNamespace(arg=arg)]
    )
    binding.handler(event)
    return event


def footer(app):
    window = next(
        window
        for window in app.layout.find_all_windows()
        if window.style == "class:status"
    )
    return to_plain_text(window.content.text())


@pytest.mark.parametrize(
    "text,expected",
    [("  Привет 🐙  ", "Привет 🐙"), ("first\nsecond", "first\nsecond"), (" ", None)],
)
def test_enter_submits_trimmed_text_once_and_ignores_empty(composer, text, expected):
    app, buffer = composer()
    buffer.document = Document(text)
    press(app, buffer, "enter")
    if expected is None:
        app.exit.assert_not_called()
        assert buffer.text == text
    else:
        app.exit.assert_called_once_with(result=expected)
        assert buffer.text == ""
    assert app.full_screen is False and app.erase_when_done is True
    assert app.refresh_interval == 0.1


def test_newline_tab_and_transcript_do_not_trigger_model(composer, monkeypatch):
    app, buffer = composer()
    buffer.document = Document("first")
    press(app, buffer, "c-j")
    assert buffer.text == "first\n"
    buffer.document = Document("/st")
    monkeypatch.setattr(
        idle,
        "_completion_to_apply",
        lambda buf: Completion("/status ", start_position=-3),
    )
    press(app, buffer, "tab")
    assert buffer.text == "/status "
    monkeypatch.setattr(idle, "_completion_to_apply", lambda buf: None)
    press(app, buffer, "tab")
    app.exit.assert_not_called()
    press(app, buffer, "c-t")
    app.exit.assert_called_once_with(result="__transcript__")


@pytest.mark.parametrize("key", ["/", "@", "$"])
@pytest.mark.parametrize("existing", ["", "word"])
def test_completion_prefix_opens_popup_only_at_empty_input(
    composer, monkeypatch, key, existing
):
    app, buffer = composer()
    buffer.document = Document(existing)
    start = Mock()
    monkeypatch.setattr(buffer, "start_completion", start)
    press(app, buffer, key)
    assert buffer.text == existing + key
    if not existing:
        start.assert_called_once_with(select_first=False)
        app.invalidate.assert_called_once_with()
    else:
        start.assert_not_called()
        app.invalidate.assert_not_called()


@pytest.mark.parametrize("width", [40, 100])
def test_question_shortcuts_and_escape_toggle_footer(composer, monkeypatch, width):
    monkeypatch.setenv("COLUMNS", str(width))
    monkeypatch.setenv("LINES", "24")
    app, buffer = composer()
    initial = footer(app)
    press(app, buffer, "?")
    assert "shortcuts" in footer(app)
    press(app, buffer, "escape")
    assert footer(app) == initial
    buffer.document = Document("question")
    press(app, buffer, "?")
    assert buffer.text == "question?"
    press(app, buffer, "c-c")
    app.exit.assert_called_once_with(result=None)


@pytest.mark.parametrize("kind", ["image", "text", "empty", "repeat"])
def test_paste_uses_one_explicit_source_without_duplicate_attachment(
    composer, monkeypatch, kind
):
    image = Mock(return_value="/fixture/image.png" if kind == "image" else None)
    text = Mock(return_value="Привет\nfixture" if kind == "text" else "")
    monkeypatch.setattr(idle, "_clipboard_image_path", image)
    monkeypatch.setattr(idle, "_clipboard_text", text)
    app, buffer = composer()
    buffer.document = Document("question")
    press(app, buffer, "c-v", arg=2 if kind == "repeat" else None)
    expected = {
        "image": "question 📎 /fixture/image.png ",
        "text": "questionПривет\nfixture",
        "empty": "question",
        "repeat": "question",
    }[kind]
    assert buffer.text == expected
    if kind == "repeat":
        image.assert_not_called()
        text.assert_not_called()
    else:
        image.assert_called_once_with()
        assert text.call_count == int(kind != "image")
    assert app.invalidate.call_count == int(kind in {"image", "text"})


@pytest.mark.parametrize("exits", [False, True])
def test_reasoning_toggle_and_visibility_filter(composer, exits):
    state = {"visible": False, "lines": ["brief"], "expanded_lines": ["detail"]}
    app, buffer = composer(reasoning_state=state, reasoning_toggle_exits=exits)
    press(app, buffer, "c-o")
    assert state["visible"] is True
    if exits:
        assert isinstance(
            app.exit.call_args.kwargs["exception"], idle._IdleReasoningToggle
        )
    else:
        app.invalidate.assert_called_once_with()
    controls = [window.content for window in app.layout.find_all_windows()]
    values = [
        to_plain_text(control.text())
        for control in controls
        if hasattr(control, "text") and callable(control.text)
    ]
    assert any("detail" in text for text in values)
    empty_app, empty_buffer = composer()
    press(empty_app, empty_buffer, "c-o")
    empty_app.invalidate.assert_not_called()


@pytest.mark.parametrize("output_fails", [False, True])
def test_mouse_integration_keeps_notification_and_handles_unavailable_output(
    composer, monkeypatch, output_fails
):
    output = DummyOutput()
    monkeypatch.setattr(idle, "create_output", lambda: output)
    frame_builder = Mock()
    monkeypatch.setattr(idle, "_idle_mouse_frame_builder", frame_builder)
    mouse = SimpleNamespace(
        install_bindings=Mock(),
        mouse_support_filter=Mock(return_value=True),
        wrap_output=Mock(return_value=output),
        set_app=Mock(),
        current_notification=Mock(return_value="fixture copied"),
    )
    if output_fails:
        mouse.wrap_output.side_effect = OSError("fixture output unavailable")
    app, buffer = composer(mouse_controller=mouse)
    mouse.install_bindings.assert_called_once_with(app.key_bindings)
    mouse.set_app.assert_called_once_with(app)
    frame_builder.assert_called_once_with(app, mouse, {})
    assert app.mouse_support is True
    assert app.output is (None if output_fails else output)
    assert "fixture copied" in footer(app)


def test_missing_dependency_returns_pair_without_starting_ui(composer, monkeypatch):
    monkeypatch.setattr(idle, "BufferControl", None)
    assert composer() == (None, None)
