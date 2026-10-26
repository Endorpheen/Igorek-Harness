"""Rich-prompt user input form — structured input overlay for CC-backend requests.

Port of Codex bottom_pane/request_user_input/ with interactive prompt_toolkit UX.
Supports: text, secret (masked), select (single choice), multiselect (multiple choices).
"""
from __future__ import annotations

import shutil

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.mouse_events import MouseEventType
except Exception:
    Application = None
    KeyBindings = None
    FormattedText = None
    Layout = None
    Window = None
    FormattedTextControl = None
    MouseEventType = None

from .tui.bottom_pane.request_user_input_form import (
    RequestUserInputForm,
    UserInputAnswer,
    UserInputOption,
    UserInputQuestion,
    UserInputRequest,
    UserInputResponse,
)


def user_input_request_from_payload(payload: dict) -> UserInputRequest:
    questions = []
    for raw in payload.get("questions") or []:
        options = raw.get("options")
        questions.append(
            UserInputQuestion(
                id=str(raw["id"]),
                header=str(raw["header"]),
                question=str(raw["question"]),
                options=[
                    UserInputOption(
                        label=str(item["label"]),
                        description=str(item.get("description") or ""),
                    )
                    for item in options
                ]
                if isinstance(options, list) and options
                else None,
                is_secret=bool(raw.get("is_secret")),
                is_multiselect=bool(raw.get("is_multiselect")),
            )
        )
    return UserInputRequest(
        item_id=str(payload.get("item_id") or "request_user_input"),
        thread_id=str(payload.get("thread_id") or ""),
        turn_id=str(payload.get("turn_id") or ""),
        questions=questions,
    )


def serialize_user_input_response(response: UserInputResponse) -> dict:
    return {
        "cancelled": False,
        "answers": {
            q_id: {
                "question_id": answer.question_id,
                "selected_options": list(answer.selected_options),
                "text_value": answer.text_value,
                "notes": answer.notes,
            }
            for q_id, answer in response.answers.items()
        },
    }


def _build_prompt_fragments(
    form: RequestUserInputForm,
    action_handler,
    *,
    extra_actions: tuple = (("submit",), ("cancel",)),
) -> list[tuple]:
    """Build prompt_toolkit fragments for the form.

    Option rows become clickable ``(style, text, handler)`` fragments; a
    trailing clickable submit/cancel row is appended. ``action_handler`` maps
    an action id to a prompt_toolkit mouse handler.
    """
    fragments: list[tuple] = []
    for text, action in form.render_line_specs():
        if fragments:
            fragments.append(("", "\n"))
        if action is None:
            fragments.append(("", text))
        else:
            fragments.append(("", text, action_handler(action)))
    if extra_actions:
        fragments.append(("", "\n"))
        labels = {"submit": "  [ Submit ]", "cancel": "    [ Cancel ]"}
        for action in extra_actions:
            label = labels.get(action[0], f"    [ {action[0]} ]")
            fragments.append(("", label, action_handler(action)))
    fragments.append(("", "\n"))
    return fragments


def _is_form_click(mouse_event) -> bool:
    """True only for a real left-button press during the form.

    Hover (``MOUSE_MOVE``) and release must not trigger actions, otherwise
    moving the pointer over ``[ Cancel ]`` cancels the form and press+release
    fires twice.
    """
    if MouseEventType is None:
        return True
    event_type = getattr(mouse_event, "event_type", None)
    return event_type is None or event_type is MouseEventType.MOUSE_DOWN


def _dispatch_form_action(form: RequestUserInputForm, action, app) -> None:
    """Apply a mouse click action to the form and the running Application.

    Fragment mouse handlers receive only a ``MouseEvent`` (no ``.app``), so
    the Application is passed in explicitly.
    """
    if action is None or app is None:
        return
    kind = action[0]
    if kind == "option":
        form.click_option(action[1], action[2])
        app.invalidate()
    elif kind == "submit":
        result = form.submit()
        if result:
            app.exit(result=result)
        else:
            app.invalidate()
    elif kind == "cancel":
        form.cancel()
        app.exit(result=None)


def _create_form_application(form, key_bindings, render, app_holder):
    """Build the interactive form Application.

    ``erase_when_done=True`` matters: the form is shown via ``run_in_terminal``
    (temporarily leaving the fullscreen alt-screen), so a lingering form would
    stay in the main buffer and the next form would render below it.
    """
    control = FormattedTextControl(render, show_cursor=False)
    root = Window(control, dont_extend_height=True)
    app = Application(
        layout=Layout(root),
        key_bindings=key_bindings,
        mouse_support=True,
        full_screen=False,
        erase_when_done=True,
    )
    app_holder["app"] = app
    return app


def _ask_user_input(
    console,
    request: UserInputRequest,
) -> UserInputResponse | None:
    """Show interactive structured input form and return collected answers.

    Args:
        console: Rich console for output.
        request: Structured input request with questions.

    Returns:
        UserInputResponse with answers, or None if cancelled.
    """
    _drain_stdin()

    form = RequestUserInputForm(request)
    width = shutil.get_terminal_size((120, 24)).columns
    form.set_width(width)

    # ── Fallback: plain input (no prompt_toolkit) ──
    if (
        Application is None
        or KeyBindings is None
        or FormattedText is None
        or Layout is None
        or Window is None
        or FormattedTextControl is None
    ):
        while not form.done:
            _clear_and_render(console, form)
            try:
                key = _read_key_plain()
            except (EOFError, KeyboardInterrupt):
                form.cancel()
                break
            question = form.current_question
            answer = form.current_answer
            if question and question.field_type in ("text", "secret") and answer is not None:
                if len(key) == 1:
                    answer.text_value += key
                elif key == "backspace":
                    answer.text_value = answer.text_value[:-1]
                else:
                    form.handle_key(key)
            else:
                form.handle_key(key)
        if form.cancelled:
            return None
        return form.submit()

    # ── Interactive prompt_toolkit session ──
    from prompt_toolkit.filters import Condition

    editing_text = Condition(
        lambda: form.current_question is not None
        and form.current_question.field_type in ("text", "secret")
    )

    app_holder: dict = {"app": None}

    def _action_handler(action):
        """Wrap a form action id into a prompt_toolkit mouse handler.

        Only an actual button press (``MOUSE_DOWN``) triggers an action.
        Hover (``MOUSE_MOVE``) and release must be ignored: otherwise merely
        moving the cursor over ``[ Cancel ]`` cancels the form, and press +
        release would fire the action twice.
        """
        def _handler(mouse_event):
            if not _is_form_click(mouse_event):
                return NotImplemented
            _dispatch_form_action(form, action, app_holder.get("app"))
            return None
        return _handler

    def _rp():
        form.set_width(shutil.get_terminal_size((120, 24)).columns)
        return FormattedText(_build_prompt_fragments(form, _action_handler))

    kb = KeyBindings()

    @kb.add("tab")
    def _(e):
        form.handle_key("tab")
        e.app.invalidate()

    @kb.add("s-tab")
    def _(e):
        form.handle_key("s-tab")
        e.app.invalidate()

    @kb.add("up")
    @kb.add("k", filter=~editing_text)
    def _(e):
        form.handle_key("up")
        e.app.invalidate()

    @kb.add("down")
    @kb.add("j", filter=~editing_text)
    def _(e):
        form.handle_key("down")
        e.app.invalidate()

    @kb.add("enter")
    def _(e):
        q = form.current_question
        if q and q.field_type in ("select", "multiselect"):
            form.handle_key("enter")
            e.app.invalidate()
        else:
            result = form.submit()
            if result:
                e.app.exit(result=result)
            else:
                e.app.invalidate()

    @kb.add("escape")
    @kb.add("c-c")
    def _(e):
        form.cancel()
        e.app.exit(result=None)

    @kb.add("c-s")
    def _(e):
        result = form.submit()
        if result:
            e.app.exit(result=result)
        else:
            e.app.invalidate()

    @kb.add("space", filter=~editing_text)
    def _(e):
        form.handle_key(" ")
        e.app.invalidate()

    @kb.add("backspace", filter=editing_text)
    def _(e):
        answer = form.current_answer
        if answer is not None and answer.text_value:
            answer.text_value = answer.text_value[:-1]
        e.app.invalidate()

    # Character input for text/secret fields
    @kb.add("<any>")
    def _(e):
        q = form.current_question
        if q and q.field_type in ("text", "secret"):
            key = e.key_sequence[0].key if e.key_sequence else ""
            if len(key) == 1:
                ans = form.current_answer
                if ans is not None:
                    ans.text_value += key
        e.app.invalidate()

    # A custom Application (rather than PromptSession) is required for mouse
    # support: PromptSession splits a multi-line prompt into before/input
    # fragments and drops the fragment mouse handlers, so clicks would never
    # reach our options. A plain FormattedTextControl keeps the handlers.
    app = _create_form_application(form, kb, _rp, app_holder)

    try:
        result = app.run()
    except (EOFError, KeyboardInterrupt):
        form.cancel()
        return None

    if form.cancelled or result is None:
        return None
    if isinstance(result, UserInputResponse):
        return result
    return form.submit()


def _drain_stdin() -> None:
    """Drain any pending stdin so our prompt starts fresh."""
    import sys, select
    try:
        while select.select([sys.stdin], [], [], 0)[0]:
            try:
                if not sys.stdin.read(1):
                    break
            except Exception:
                break
    except (OSError, ValueError, TypeError):
        return


def _clear_and_render(console, form: RequestUserInputForm) -> None:
    """Clear screen and render form (fallback mode)."""
    for line in form.render_lines():
        console.print(line, markup=False)


def _read_key_plain() -> str:
    """Read a single keypress from stdin (fallback mode)."""
    import sys, termios, tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = ch + sys.stdin.read(2)
            if seq == "\x1b[A":
                return "up"
            elif seq == "\x1b[B":
                return "down"
            elif seq == "\x1b[D":
                return "backtab"
            elif seq == "\x1b[C":
                return "tab"
            return "escape"
        elif ch == "\t":
            return "tab"
        elif ch == "\r":
            return "enter"
        elif ch == "\x7f":
            return "backspace"
        elif ch == "\x03":
            return "c-c"
        elif ch == "\x13":
            return "c-s"
        elif ch == " ":
            return " "
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


__all__ = [
    "_ask_user_input",
    "serialize_user_input_response",
    "user_input_request_from_payload",
]
