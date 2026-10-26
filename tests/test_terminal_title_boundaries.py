"""Title configuration recovery and OSC output use owned files/fake stdout only."""

import io
import json

import pytest

from factory.core import cli_chat_terminal as t


@pytest.mark.parametrize(
    "content", ["{", "[]", "null", '{"terminalTitle":{"items":"bad"}}']
)
def test_corrupt_title_configuration_has_safe_default_without_rewriting(
    tmp_path, content
):
    path = tmp_path / "settings.json"
    path.write_text(content)
    assert t.load_terminal_title_config(path).items == ()
    assert path.read_text() == content


def test_saving_after_corrupt_settings_creates_usable_owned_config(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{")
    t.save_terminal_title_items(["model", "model", "unknown"], settings_path=path)
    assert json.loads(path.read_text()) == {"terminalTitle": {"items": ["model"]}}
    assert t.load_terminal_title_config(path).items == ("model",)


@pytest.mark.parametrize(
    "used,expected",
    [(3, "ctx 3 used"), (1000000, "ctx 1.0M used"), ("bad", "ctx used")],
)
def test_title_token_format_is_bounded_and_tolerates_missing_numeric_data(
    used, expected
):
    assert (
        t.render_terminal_title(
            {"context_window": {"current_context_tokens": used}}, ("context-used",)
        )
        == expected
    )


@pytest.mark.parametrize("suffix", ["", "/project"])
def test_title_shortens_only_owned_home_prefix(tmp_path, monkeypatch, suffix):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert (
        t.render_terminal_title({"cwd": str(tmp_path) + suffix}, ("current-dir",))
        == "~" + suffix
    )


def test_long_user_title_is_bounded_and_control_characters_cannot_escape_osc():
    title = t.render_terminal_title(
        {}, (), user_text="\x1b]0;fixture\a\u202e" + ("x" * 300)
    )
    assert len(title) <= 60 and title.endswith("…")
    assert "\x1b" not in title and "\a" not in title and "\u202e" not in title


@pytest.mark.parametrize("empty", [False, True])
def test_title_and_completion_write_only_to_explicit_fake_tty(monkeypatch, empty):
    class Output(io.StringIO):
        def isatty(self):
            return True

    output = Output()
    monkeypatch.setattr(t.sys, "stdout", output)
    t._set_terminal_title("" if empty else "fixture\nvalue\a")
    t._notify_turn_complete()
    assert output.getvalue() == ("\a" if empty else "\x1b]0;fixture value\x1b\\\a")


def test_closed_output_does_not_break_session(monkeypatch):
    class Output:
        def isatty(self):
            return True

        def write(self, text):
            raise OSError("fixture output closed")

    monkeypatch.setattr(t.sys, "stdout", Output())
    t._set_terminal_title("fixture")
    t._notify_turn_complete()
