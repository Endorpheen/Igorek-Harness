import importlib
import sys
import tomllib
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from factory import cli
from factory.core import cli_chat


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("flag", ["--tui", "--rich", "--plain", "--experimental-app-server",
                                  "--json", "--memory-messages", "--memory-chars"])
def test_retired_ui_switch_is_rejected_by_real_entrypoint(monkeypatch, tmp_path, flag):
    monkeypatch.setattr(sys, "argv", ["igorek-factory", "chat", "--data-dir", str(tmp_path), flag])
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_chat_dispatches_only_to_mouse_capable_rich_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path))
    rich = importlib.import_module("core.cli_chat_rich")
    run = Mock(return_value=17)
    monkeypatch.setattr(rich, "run_rich_chat", run)
    monkeypatch.setattr(sys, "argv", ["igorek-factory", "chat", "--data-dir", str(tmp_path),
                                     "--profile", "fixture", "--session", "fixture-session"])
    assert cli.main() == 17
    assert run.call_args.kwargs["session"] == "fixture-session"
    assert run.call_args.kwargs["profile"] == "fixture"
    assert run.call_args.kwargs["data_dir"] == str(tmp_path)


def test_only_current_ui_and_shared_session_helpers_remain():
    for name in ("cli_chat_textual", "tui_splash", "codex_native_slash", "codex_usage_client", "codex_subagents"):
        assert not (ROOT / "factory/core" / (name + ".py")).exists(), name
    assert not hasattr(cli_chat, "chat_loop")
    assert not hasattr(cli_chat, "_render_prefixed")
    assert callable(cli_chat._build_prompt)
    assert callable(cli_chat._load_entries)
    assert callable(cli_chat.fork_session)
    assert (ROOT / "factory/core/tui/mouse_controller.py").is_file()
    assert (ROOT / "factory/core/tui/chatwidget.py").is_file()
    assert (ROOT / "factory/core/codex_app_server_runtime.py").is_file()


@pytest.mark.parametrize("command", ["router", "orchestrator"])
def test_retired_pipeline_commands_are_not_exposed(command):
    with pytest.raises(SystemExit) as error:
        cli._build_parser().parse_args([command])
    assert error.value.code == 2
    for name in ("router", "orchestrator", "orchestration_aggregator"):
        assert not (ROOT / "factory/core" / (name + ".py")).exists()
    assert not (ROOT / "factory/config/chain.yaml").exists()


def test_resume_command_does_not_reintroduce_retired_ui_switch():
    assert cli_chat.format_resume_command("fixture", profile="fixture") == (
        "igorek-factory chat --resume fixture --profile fixture"
    )


def test_textual_dependency_removed_and_active_markdown_declared():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert "textual" not in project["dependencies"]
    assert "markdown-it-py" in project["dependencies"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert "textual" not in {p["name"] for p in lock["package"]}


def test_current_cli_exit_finishes_application_once(monkeypatch):
    from prompt_toolkit.application import Application
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from factory.core.chat_session import ChatSession
    from factory.core import cli_chat_active

    session = object.__new__(ChatSession)
    session._active = True
    session._fs_submitted = ["/exit"]
    session.reasoning_state = {}
    session.sl = SimpleNamespace()
    session.mq = SimpleNamespace(pending_count=0, peek_all=lambda: [])
    session._mouse_controller = SimpleNamespace(
        set_wheel_handler=lambda *args: None,
        set_capture_allowed=lambda *args: None,
        clear_app=lambda: None,
    )
    session._seed_fullscreen_history_from_session = lambda: None
    with create_pipe_input() as pipe:
        application = Application(input=pipe, output=DummyOutput())
        exit_call = Mock(wraps=application.exit)
        monkeypatch.setattr(application, "exit", exit_call)
        monkeypatch.setattr(cli_chat_active, "_create_chat_application", lambda **kwargs: (application, None, None))
        assert session.run_fullscreen() == 0
    exit_call.assert_called_once_with()
    assert session._active is False
