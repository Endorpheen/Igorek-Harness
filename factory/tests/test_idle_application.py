"""Tests for the unified idle Application in cli_chat_active.

The idle Application shares the active turn's visual identity and key
bindings while keeping an input field available without a task lifecycle.
These tests cover unwanted post-submit autofill, Ctrl+C, duplicated reasoning
and the order of the input and history panels.
"""
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import cli_chat_active as _active
from core import cli_chat_rich_shared as _shared
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.layout import ConditionalContainer, FloatContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.menus import CompletionsMenu


class _FakeStatusLine:
    class Config:
        padding = 0

    config = Config()

    def toolbar_text(self):
        return "project · /home/user"


class _AnsiStatusLine(_FakeStatusLine):
    def toolbar_text(self):
        return (
            "\033[38;5;222mproject\033[0m "
            "\033[2m·\033[0m "
            "\033[38;5;114m/home/user\033[0m"
        )


def _make_running_state():
    return {
        "reasoning_state": {"visible": False, "lines": [], "expanded_lines": []},
        "show_shortcuts": False,
    }


def _all_bound_keys(app):
    """Flatten all bound keys across all bindings into a set of key strings.

    prompt_toolkit represents Enter as ``Keys.ControlM`` and Tab as
    ``Keys.ControlI`` — we normalise via ``key.value`` so the test can
    match the canonical key name.
    """
    keys: set[str] = set()
    for binding in app.key_bindings.bindings:
        for key in binding.keys:
            keys.add(getattr(key, "value", str(key)))
    return keys


def _find_binding(app, key_name: str):
    for binding in app.key_bindings.bindings:
        for key in binding.keys:
            if getattr(key, "value", str(key)) == key_name:
                return binding
    return None


def _root_hsplit(app):
    container = app.layout.container
    if isinstance(container, FloatContainer):
        return container.content
    return container


def _iter_layout_nodes(node):
    yield node
    children = getattr(node, "children", None)
    if children is not None:
        for child in children:
            yield from _iter_layout_nodes(child)
    content = getattr(node, "content", None)
    if content is not None and content is not node:
        yield from _iter_layout_nodes(content)


def _find_input_window(app, buffer=None):
    for node in _iter_layout_nodes(_root_hsplit(app)):
        if not isinstance(node, Window):
            continue
        if not isinstance(node.content, BufferControl):
            continue
        if buffer is not None and node.content.buffer is not buffer:
            continue
        return node
    return None


def _find_composer_surface(app, buffer):
    for node in _iter_layout_nodes(_root_hsplit(app)):
        if not isinstance(node, HSplit):
            continue
        children = getattr(node, "children", [])
        if any(
            isinstance(child, Window)
            and isinstance(child.content, BufferControl)
            and child.content.buffer is buffer
            for child in children
        ):
            return node
    return None


def _flatten_formatted_text_from_container(container) -> str:
    rendered = []
    children = getattr(container, "children", None)
    if children is not None:
        for child in children:
            rendered.append(_flatten_formatted_text_from_container(child))
        return "".join(rendered)

    content_container = getattr(container, "content", None)
    if content_container is not None and not isinstance(container, Window):
        return _flatten_formatted_text_from_container(content_container)

    content = getattr(container, "content", None)
    text_fn = getattr(content, "text", None)
    if not callable(text_fn):
        return ""
    item = text_fn()
    if isinstance(item, list):
        flat = ""
        for frag in item:
            flat += frag[1] if isinstance(frag, tuple) else str(frag)
        return flat
    return str(item)


def _window_height_value(window) -> int:
    height = window.height() if callable(window.height) else window.height
    return int(getattr(height, "preferred", height))


def test_idle_application_returns_app_buffer_output():
    app, buffer, output_lines = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    assert isinstance(app, Application)
    assert isinstance(buffer, Buffer)
    assert isinstance(output_lines, list)


def test_idle_application_returns_none_when_prompt_toolkit_missing(monkeypatch):
    monkeypatch.setattr(_active, "Application", None)

    app, buffer, output_lines = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    assert app is None
    assert buffer is None
    assert output_lines is None


def test_idle_application_layout_uses_hsplit():
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    assert isinstance(app.layout.container, FloatContainer)
    assert isinstance(_root_hsplit(app), HSplit)


def test_idle_application_layout_has_completion_menu_float():
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    floats = getattr(app.layout.container, "floats", [])
    assert any(isinstance(item.content, CompletionsMenu) for item in floats)


def test_idle_application_buffer_has_idle_completer():
    _, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    assert buffer.completer is not None


def test_idle_application_slash_completion_offers_commands():
    _, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    completions = list(buffer.completer.get_completions(Document("/"), None))

    assert completions
    assert any(str(item.text).startswith("/help") for item in completions)


def test_idle_application_completion_triggers_are_bound():
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    assert buffer.completer is not None
    assert _find_binding(app, "/") is not None
    assert _find_binding(app, "@") is not None
    assert _find_binding(app, "$") is not None


def test_idle_application_layout_has_min_three_line_input_field():
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    composer = _find_composer_surface(app, buffer)
    input_window = _find_input_window(app, buffer)

    assert composer is not None, "expected Codex-style composer surface HSplit"
    assert input_window is not None, "expected an input Window backed by the buffer"
    assert len(composer.children) == 3, "composer surface must include top/input/bottom rows"
    assert _window_height_value(input_window) == 1, (
        f"textarea must start at 1 line tall, got {input_window.height!r}"
    )


def test_idle_application_input_field_grows_with_multiline_text(monkeypatch):
    monkeypatch.setattr(
        _active.shutil,
        "get_terminal_size",
        lambda *a, **kw: _active.shutil.os.terminal_size((120, 24)),
    )
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    input_window = _find_input_window(app, buffer)
    assert input_window is not None

    buffer.text = "one"
    assert _window_height_value(input_window) == 1

    buffer.text = "\n".join(str(i) for i in range(6))
    assert _window_height_value(input_window) == 6

    buffer.text = "\n".join(str(i) for i in range(20))
    assert _window_height_value(input_window) == 16


def test_idle_application_input_field_counts_wrapped_lines(monkeypatch):
    monkeypatch.setattr(
        _active.shutil,
        "get_terminal_size",
        lambda *a, **kw: _active.shutil.os.terminal_size((14, 24)),
    )
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    input_window = _find_input_window(app, buffer)
    assert input_window is not None

    buffer.text = "x" * 35

    # terminal width 14 minus the 4-column input margin => 10 usable columns.
    assert _window_height_value(input_window) == 4


def test_idle_application_input_field_caps_to_available_terminal_rows(monkeypatch):
    monkeypatch.setattr(
        _active.shutil,
        "get_terminal_size",
        lambda *a, **kw: _active.shutil.os.terminal_size((120, 12)),
    )
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    input_window = _find_input_window(app, buffer)
    assert input_window is not None

    buffer.text = "\n".join(str(i) for i in range(50))

    assert _window_height_value(input_window) == 4


def test_idle_application_input_field_wraps_long_paste():
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    input_window = _find_input_window(app, buffer)
    assert input_window is not None

    assert input_window.wrap_lines() is True


def test_running_application_ctrl_v_inserts_clipboard_image_path(monkeypatch):
    monkeypatch.setattr(_shared, "_clipboard_image_path", lambda: "/tmp/screenshot.png")
    app, buffer, _ = _active._create_running_application(
        status_line=_FakeStatusLine(), running_state={"t0": 0, "preview_items": []}
    )

    c_v = _find_binding(app, "c-v")
    assert c_v is not None

    class _FakeApp:
        invalidated = False

        def invalidate(self):
            self.invalidated = True

    fake_app = _FakeApp()
    fake_event = type(
        "E",
        (),
        {"current_buffer": buffer, "app": fake_app, "key_sequence": []},
    )()

    c_v.handler(fake_event)

    assert buffer.text == "📎 /tmp/screenshot.png "
    assert fake_app.invalidated is True


def test_running_application_keeps_status_in_output_not_dedicated_pane():
    state = {"t0": 0, "preview_items": [], "show_shortcuts": True}
    app, buffer, _ = _active._create_running_application(
        status_line=_FakeStatusLine(), running_state=state,
    )
    container = _root_hsplit(app)
    assert isinstance(container, HSplit)
    output, working, separator, composer, footer = container.children
    assert isinstance(output, ConditionalContainer)
    assert isinstance(working, ConditionalContainer)
    assert isinstance(separator, Window)
    assert isinstance(composer, HSplit)
    assert isinstance(footer, Window)
    assert _find_input_window(app, buffer) in list(_iter_layout_nodes(composer))
    assert _flatten_formatted_text_from_container(output)
    assert "project" in _flatten_formatted_text_from_container(footer)
    assert not working.filter()
    state["task_active"] = True
    assert working.filter()
    assert "Working" in _flatten_formatted_text_from_container(working)


def test_idle_application_has_no_dedicated_auxiliary_panes():
    """Reasoning/worked/shortcut text must not reserve separate bottom panes.

    The bottom anchor should stay stable: output/scrollback above, then
    separator, input, statusline. Auxiliary text is appended to output instead
    of getting its own Window/ConditionalContainer.
    """
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    container = _root_hsplit(app)
    assert len(container.children) == 5
    output_container = container.children[0]
    working_window = container.children[1]
    separator_window = container.children[2]
    composer_surface = container.children[3]
    footer_window = container.children[4]

    assert isinstance(output_container, ConditionalContainer)
    assert output_container.filter() is False
    assert isinstance(working_window, ConditionalContainer)
    assert working_window.filter() is False
    assert isinstance(separator_window, Window)
    assert isinstance(composer_surface, HSplit)
    assert _find_input_window(app) is not None
    assert isinstance(footer_window, Window)


def test_idle_application_input_field_is_always_visible():
    """Input is NOT wrapped in ConditionalContainer — visible regardless of state.

    Previous attempt wrapped the input in ConditionalContainer with a
    ``task_done`` filter, which hid the input after a task finished.
    """
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    input_window = _find_input_window(app)
    assert input_window is not None, "idle input must always be visible in composer surface"


def test_idle_application_accept_handler_resets_buffer():
    """Buffer must be reset on submit to prevent autofill (Bug 2 from previous attempt)."""
    running_state = _make_running_state()
    _, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    buffer.text = "  hello world  "
    buffer.accept_handler(buffer)

    assert running_state["submitted_text"] == "hello world"
    assert buffer.text == "", (
        "buffer must be reset on submit so the next prompt starts empty "
        "(autofill regression guard)"
    )


def test_idle_application_accept_handler_rejects_empty():
    running_state = _make_running_state()
    _, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    buffer.text = "   "
    buffer.accept_handler(buffer)

    assert "submitted_text" not in running_state


def test_idle_application_accept_handler_strips_control_chars():
    running_state = _make_running_state()
    _, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    buffer.text = "ping\x00with\x07control"
    buffer.accept_handler(buffer)

    assert "submitted_text" in running_state
    assert "\x00" not in running_state["submitted_text"]
    assert "\x07" not in running_state["submitted_text"]


def test_idle_application_binds_idle_hotkeys():
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    bound_keys = _all_bound_keys(app)

    expected = {
        "c-m", "c-j", "c-i", "?", "c-t", "c-o", "c-v", "escape", "c-c",
        "/", "@", "$",
    }
    missing = expected - bound_keys
    assert not missing, f"missing hotkeys: {sorted(missing)} (bound: {sorted(bound_keys)})"


def test_idle_application_ctrl_c_exits_with_interrupt_sentinel():
    """Ctrl+C must close the Application without raising KeyboardInterrupt.

    Raising KeyboardInterrupt from prompt_toolkit run_async can leave an
    unhandled task exception in the terminal. Pin the structural contract:
    Ctrl+C returns the same sentinel as Escape interrupt.
    """
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    c_c = _find_binding(app, "c-c")
    assert c_c is not None

    class _FakeApp:
        exception = None
        result = None

        def exit(self, *, result=None, exception=None, **_kwargs):
            self.result = result
            self.exception = exception

    fake_app = _FakeApp()
    c_c.handler(type("E", (), {"app": fake_app})())

    assert fake_app.result == "__INTERRUPT__"
    assert fake_app.exception is None


def test_idle_application_show_shortcuts_toggle():
    """Pressing `?` on empty buffer toggles the shortcut overlay (no text inserted)."""
    running_state = _make_running_state()
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    assert running_state.get("show_shortcuts") is False
    question_binding = _find_binding(app, "?")
    assert question_binding is not None

    class _FakeApp:
        def invalidate(self):
            pass

    fake_event = type("E", (), {"current_buffer": buffer, "app": _FakeApp()})()
    question_binding.handler(fake_event)

    assert running_state["show_shortcuts"] is True
    assert buffer.text == "", "? on empty buffer must NOT insert a literal '?'"


def test_idle_application_show_shortcuts_inserts_text_when_buffer_nonempty():
    running_state = _make_running_state()
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    buffer.text = "hal"
    buffer.cursor_position = len(buffer.text)
    question_binding = _find_binding(app, "?")
    assert question_binding is not None

    class _FakeApp:
        def invalidate(self):
            pass

    fake_event = type("E", (), {"current_buffer": buffer, "app": _FakeApp()})()
    question_binding.handler(fake_event)

    assert running_state.get("show_shortcuts") is False, (
        "? on non-empty buffer must NOT toggle overlay"
    )
    assert buffer.text == "hal?", f"? on non-empty buffer must insert literal '?', got {buffer.text!r}"


def test_idle_application_c_o_toggles_reasoning_visibility():
    running_state = {
        "reasoning_state": {
            "visible": False,
            "lines": ["some line"],
            "expanded_lines": [],
        },
        "show_shortcuts": False,
    }
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    c_o = _find_binding(app, "c-o")
    assert c_o is not None

    class _FakeApp:
        def invalidate(self):
            pass

    fake_event = type("E", (), {"app": _FakeApp()})()
    c_o.handler(fake_event)
    assert running_state["reasoning_state"]["visible"] is True

    c_o.handler(fake_event)
    assert running_state["reasoning_state"]["visible"] is False


def test_idle_application_c_o_noop_when_no_reasoning():
    running_state = {
        "reasoning_state": {
            "visible": False,
            "lines": [],
            "expanded_lines": [],
        },
        "show_shortcuts": False,
    }
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    c_o = _find_binding(app, "c-o")
    assert c_o is not None

    class _FakeApp:
        def invalidate(self):
            pass

    fake_event = type("E", (), {"app": _FakeApp()})()
    c_o.handler(fake_event)

    assert running_state["reasoning_state"]["visible"] is False


def test_idle_application_escape_clears_shortcut_overlay():
    running_state = {
        "reasoning_state": {"visible": False, "lines": [], "expanded_lines": []},
        "show_shortcuts": True,
    }
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    esc = _find_binding(app, "escape")
    assert esc is not None

    class _FakeApp:
        def invalidate(self):
            pass

    esc.handler(type("E", (), {"app": _FakeApp()})())

    assert running_state["show_shortcuts"] is False


def test_idle_application_footer_renders_statusline():
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    container = _root_hsplit(app)
    footer = container.children[-1]
    text = footer.content.text()

    flat = ""
    for line in text:
        if isinstance(line, tuple):
            flat += line[1]
        else:
            flat += str(line)

    assert "project" in flat, f"footer must include the statusline project text, got {flat!r}"


def test_idle_application_footer_parses_ansi_statusline():
    app, _, _ = _active._create_idle_application(
        status_line=_AnsiStatusLine(), running_state=_make_running_state()
    )

    container = _root_hsplit(app)
    footer = container.children[-1]
    text = footer.content.text()
    flat = "".join(fragment[1] if isinstance(fragment, tuple) else str(fragment) for fragment in text)

    assert "\033[" not in flat
    assert "project" in flat
    assert "/home/user" in flat
    assert any(
        isinstance(fragment, tuple) and "fg:" in fragment[0] and "project" in fragment[1]
        for fragment in text
    )


def test_idle_application_renders_last_worked_for_status():
    state = _make_running_state()
    state["last_worked_for"] = "Worked for 7m 27s"
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=state
    )

    flat = _flatten_formatted_text_from_container(_root_hsplit(app))
    assert "Worked for 7m 27s" in flat


def test_idle_application_output_height_hugs_worked_for_line():
    state = _make_running_state()
    state["last_worked_for"] = "Worked for 1m 25s"
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=state
    )

    output_container = _root_hsplit(app).children[0]
    output_window = output_container.content

    assert isinstance(output_window, Window)
    assert _window_height_value(output_window) == 1


def test_idle_application_output_height_caps_long_scrollback(monkeypatch):
    monkeypatch.setattr(
        _active.shutil,
        "get_terminal_size",
        lambda *a, **kw: _active.shutil.os.terminal_size((80, 12)),
    )
    app, buffer, output_lines = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )
    output_lines.extend(f"line {i}" for i in range(50))

    output_container = _root_hsplit(app).children[0]
    output_window = output_container.content

    assert isinstance(output_window, Window)
    assert _window_height_value(output_window) == 7
    buffer.text = "\n".join(str(i) for i in range(8))
    assert _window_height_value(output_window) == 4


def test_idle_application_separator_uses_dashes():
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    container = _root_hsplit(app)
    separator_window = None
    for child in container.children:
        if isinstance(child, Window) and getattr(child, "style_string", "") == "class:separator":
            separator_window = child
            break

    if separator_window is not None:
        text = separator_window.content.text()
        flat = ""
        for line in text:
            if isinstance(line, tuple):
                flat += line[1]
            else:
                flat += str(line)
        assert "─" in flat, f"separator must render unicode box-drawing dashes, got {flat!r}"


def test_idle_application_input_field_uses_gray_background_style():
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    style = app.style
    inline_style = style.style_rules
    css_rules = " ".join(rule[0] + "=" + rule[1] for rule in inline_style)
    assert "input-field" in css_rules

    resolved = style.get_attrs_for_style_str("class:input-field")
    bgcolor = getattr(resolved, "bgcolor", None)
    assert bgcolor in ("303030", "#303030"), (
        f"input-field must have gray background (#303030), got bgcolor={bgcolor!r}"
    )


def test_idle_application_is_not_full_screen():
    app, _, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=_make_running_state()
    )

    assert app.full_screen is False, (
        "idle Application must not be full_screen=True — it shares the "
        "terminal with the rest of the chat output"
    )


def test_idle_application_c_t_submits_transcript_sentinel():
    running_state = _make_running_state()
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    c_t = _find_binding(app, "c-t")
    assert c_t is not None

    class _FakeApp:
        def __init__(self):
            self.exited = False
            self.result = None

        def exit(self, result=None):
            self.exited = True
            self.result = result

    fake_app = _FakeApp()
    fake_event = type("E", (), {"current_buffer": buffer, "app": fake_app})()
    c_t.handler(fake_event)

    assert running_state.get("submitted_text") == "__transcript__", (
        f"Ctrl+T must submit the __transcript__ sentinel, "
        f"got {running_state.get('submitted_text')!r}"
    )
    assert fake_app.exited is True, "Ctrl+T must call app.exit() to return from app.run()"


def test_idle_application_c_j_inserts_newline():
    running_state = _make_running_state()
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    buffer.text = "line1"
    buffer.cursor_position = len(buffer.text)
    c_j = _find_binding(app, "c-j")
    assert c_j is not None

    class _FakeApp:
        def invalidate(self):
            pass

    fake_event = type("E", (), {"current_buffer": buffer, "app": _FakeApp()})()
    c_j.handler(fake_event)

    assert buffer.text == "line1\n", f"Ctrl+J must insert a newline at cursor, got {buffer.text!r}"


def test_idle_application_enter_exits_app_with_submitted_text():
    running_state = _make_running_state()
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    buffer.text = "hello"
    enter = _find_binding(app, "c-m")
    assert enter is not None, "Enter binding (c-m) must exist"

    class _FakeApp:
        def __init__(self):
            self.exited = False
            self.result = None

        def exit(self, result=None):
            self.exited = True
            self.result = result

    fake_app = _FakeApp()
    fake_event = type("E", (), {"current_buffer": buffer, "app": fake_app})()
    enter.handler(fake_event)

    assert fake_app.exited is True, "Enter on non-empty buffer must exit the app"
    assert fake_app.result == "hello", f"Enter must exit with the text, got {fake_app.result!r}"


def test_idle_application_enter_on_empty_does_not_exit():
    running_state = _make_running_state()
    app, buffer, _ = _active._create_idle_application(
        status_line=_FakeStatusLine(), running_state=running_state
    )

    enter = _find_binding(app, "c-m")
    assert enter is not None, "Enter binding (c-m) must exist"

    class _FakeApp:
        def __init__(self):
            self.exited = False
            self.result = None

        def exit(self, result=None):
            self.exited = True
            self.result = result

    fake_app = _FakeApp()
    fake_event = type("E", (), {"current_buffer": buffer, "app": fake_app})()
    enter.handler(fake_event)

    assert fake_app.exited is False, "Enter on empty buffer must NOT exit the app"
