"""Slash-command controller contracts with temporary settings and fake dialogs."""

import json
import subprocess
from unittest.mock import Mock
from urllib import request

import pytest

from factory.core import (
    chat_session as c,
    cli_chat as chats,
    cli_chat_rich as ui,
    cli_skills,
    codex_provider,
)
from factory.core.tui import resume_picker as picker
from tests.test_chat_session_lifecycle_contracts import session as session


@pytest.fixture
def controller(session, tmp_path, monkeypatch):
    session.sl.settings_path = tmp_path / "ui-settings.json"
    session.console.width = 180

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected real process or external request")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(request, "urlopen", forbidden)
    session.sl.settings_path.write_text(
        json.dumps(
            {
                "unrelated": "preserved",
                "theme": "monokai",
                "statusLine": {"items": ["model"]},
                "terminalTitle": {"items": ["model"]},
            }
        )
    )
    return session


@pytest.mark.parametrize(
    "command,section",
    [
        ("/statusline reset", "statusLine"),
        ("/statusline default", "statusLine"),
        ("/title reset", "terminalTitle"),
        ("/title default", "terminalTitle"),
        ("/theme reset", "theme"),
        ("/theme default", "theme"),
    ],
)
def test_settings_reset_preserves_unrelated_fields(controller, command, section):
    controller._handle_command(command)
    saved = json.loads(controller.sl.settings_path.read_text())
    assert saved["unrelated"] == "preserved"
    if section == "theme":
        assert "theme" not in saved
    else:
        assert saved[section]["items"] == []
    assert "reset" in controller.output_fixture.getvalue().lower()


@pytest.mark.parametrize(
    "command,expected",
    [
        ("/statusline", "Items"),
        ("/title", "Preview"),
        ("/theme status", "Theme"),
        ("/theme show", "Theme"),
    ],
)
def test_settings_summary_commands_report_configuration(controller, command, expected):
    controller._handle_command(command)
    output = controller.output_fixture.getvalue()
    assert expected.lower() in output.lower() and "ui-settings.json" in output


@pytest.mark.parametrize(
    "command,callback",
    [
        ("/statusline setup", "_statusline_setup"),
        ("/title setup", "_terminal_title_setup"),
        ("/theme", "_theme_setup"),
    ],
)
def test_setup_commands_open_only_the_requested_dialog(
    controller, monkeypatch, command, callback
):
    launch = Mock()
    monkeypatch.setattr(ui, callback, launch)
    controller._handle_command(command)
    launch.assert_called_once_with(controller.console, controller.sl)


@pytest.mark.parametrize(
    "command,enabled",
    [("/skills enable fixture-skill", True), ("/skills disable fixture-skill", False)],
)
def test_skill_toggle_uses_current_data_root(controller, monkeypatch, command, enabled):
    change = Mock(return_value={"skill_id": "fixture-skill"})
    monkeypatch.setattr(cli_skills, "set_skill_enabled", change)
    controller._handle_command(command)
    change.assert_called_once_with(
        "fixture-skill", enabled=enabled, data_dir=controller.data_dir
    )
    assert (
        "enabled" if enabled else "disabled"
    ) in controller.output_fixture.getvalue()


@pytest.mark.parametrize(
    "command", ["/skills", "/skills status", "/skills show", "/skills list"]
)
def test_skill_summary_aliases_use_current_data_root(controller, monkeypatch, command):
    summary = Mock(return_value="fixture skill summary")
    monkeypatch.setattr(ui, "_skills_summary", summary)
    controller._handle_command(command)
    summary.assert_called_once_with(data_dir=controller.data_dir)
    assert "fixture skill summary" in controller.output_fixture.getvalue()


@pytest.mark.parametrize("command", ["/skills enable", "/skills unknown"])
def test_incomplete_skill_command_does_not_change_skills(
    controller, monkeypatch, command
):
    change = Mock()
    monkeypatch.setattr(cli_skills, "set_skill_enabled", change)
    controller._handle_command(command)
    change.assert_not_called()
    assert "Usage: /skills" in controller.output_fixture.getvalue()


def test_skill_management_and_local_activation_dispatch(controller, monkeypatch):
    manage = Mock()
    activate = Mock(return_value={"message": "fixture activation complete"})
    monkeypatch.setattr(ui, "_skills_manage", manage)
    monkeypatch.setattr(cli_skills, "activate_local_skills", activate)
    controller._handle_command("/skills manage")
    controller._handle_command("/skills activate-local")
    manage.assert_called_once_with(controller.console, data_dir=controller.data_dir)
    activate.assert_called_once_with(data_dir=controller.data_dir)
    assert "fixture activation complete" in controller.output_fixture.getvalue()


def test_skill_failure_is_visible_and_does_not_close_chat(controller, monkeypatch):
    monkeypatch.setattr(
        cli_skills,
        "set_skill_enabled",
        Mock(side_effect=ValueError("fixture skill missing")),
    )
    controller._handle_command("/skills enable missing")
    assert (
        controller._active
        and "fixture skill missing" in controller.output_fixture.getvalue()
    )


@pytest.mark.parametrize(
    "command,selection,active",
    [
        ("/resume", picker.SessionSelection.EXIT, False),
        ("/fork", picker.SessionSelection.EXIT, False),
        ("/resume", picker.SessionSelection.START_FRESH, True),
        ("/fork", picker.SessionSelection.START_FRESH, True),
    ],
)
def test_picker_exit_and_cancel_preserve_distinct_controller_states(
    controller, monkeypatch, command, selection, active
):
    monkeypatch.setattr(
        chats, "list_sessions", lambda: [{"session_id": "past-fixture"}]
    )
    dialog = Mock(return_value=(selection, None))
    monkeypatch.setattr(picker, "run_resume_picker", dialog)
    switch = Mock()
    fork = Mock()
    monkeypatch.setattr(controller, "_switch_session", switch)
    monkeypatch.setattr(chats, "fork_session", fork)
    controller._handle_command(command)
    assert controller._active is active
    switch.assert_not_called()
    fork.assert_not_called()
    assert dialog.call_args.kwargs["action"] == (
        picker.SessionPickerAction.RESUME
        if command == "/resume"
        else picker.SessionPickerAction.FORK
    )


@pytest.mark.parametrize("command", ["/resume", "/fork"])
def test_empty_session_list_never_opens_picker(controller, monkeypatch, command):
    monkeypatch.setattr(chats, "list_sessions", lambda: [])
    dialog = Mock()
    monkeypatch.setattr(picker, "run_resume_picker", dialog)
    controller._handle_command(command)
    dialog.assert_not_called()
    assert "No past sessions" in controller.output_fixture.getvalue()


def test_resume_explicit_id_skips_picker_and_selected_row_routes_correctly(
    controller, monkeypatch
):
    switch = Mock()
    monkeypatch.setattr(controller, "_switch_session", switch)
    controller._handle_command("/resume direct-fixture")
    switch.assert_called_once_with("direct-fixture")
    switch.reset_mock()
    monkeypatch.setattr(
        chats, "list_sessions", lambda: [{"session_id": "past-fixture"}]
    )
    monkeypatch.setattr(
        picker,
        "run_resume_picker",
        lambda *_, **__: (
            picker.SessionSelection.RESUME,
            {"session_id": "past-fixture"},
        ),
    )
    controller._handle_command("/resume")
    switch.assert_called_once_with("past-fixture")


def test_fork_copies_only_selected_temporary_history_and_keeps_current_session(
    controller, monkeypatch, tmp_path
):
    controller._startup()
    c._append_entry(controller.session_path, role="user", text="fixture source turn")
    source = chats._session_path(controller.session_name)
    assert source.is_relative_to(tmp_path)
    before = set(source.parent.glob("*.jsonl"))
    monkeypatch.setattr(
        chats, "list_sessions", lambda: [{"session_id": controller.session_name}]
    )
    monkeypatch.setattr(
        picker,
        "run_resume_picker",
        lambda *_, **__: (
            picker.SessionSelection.FORK,
            {"session_id": controller.session_name},
        ),
    )
    controller._handle_command("/fork")
    created = set(source.parent.glob("*.jsonl")) - before
    assert len(created) == 1
    copied = created.pop()
    assert c._load_entries(copied) == c._load_entries(source)
    assert c._read_session_meta(copied)["session_id"] == copied.stem
    assert controller.session_path == source and controller._active


@pytest.mark.parametrize("selected", ["model-two", None])
def test_model_recommendation_applies_only_confirmed_selection(
    controller, monkeypatch, selected
):
    monkeypatch.setattr(
        codex_provider,
        "model_catalog_for_provider",
        lambda _: [{"id": "model-two", "transport": "openai"}],
    )
    choose = Mock(return_value=selected)
    monkeypatch.setattr(ui, "_model_recommendation_prompt", choose)
    controller._handle_command("/models recommend")
    expected = selected or "model-one"
    assert (
        controller.model == controller.sl.model == controller.widget.model == expected
    )
    assert controller.model_override == ("model-two" if selected else None)


def test_model_list_marks_active_model_and_does_not_change_binding(
    controller, monkeypatch
):
    monkeypatch.setattr(
        codex_provider,
        "model_catalog_for_provider",
        lambda _: [
            {"id": "model-one", "transport": "openai"},
            {"id": "model-two", "transport": "anthropic"},
        ],
    )
    controller._handle_command("/models")
    output = controller.output_fixture.getvalue()
    assert all(
        value in output for value in ["model-one", "model-two", "openai", "anthropic"]
    )
    assert controller.model == "model-one" and controller.model_override is None


@pytest.mark.parametrize(
    "command", ["/model model-two", "/model reset", "/model default"]
)
def test_model_change_keeps_session_status_and_widget_consistent(
    controller, monkeypatch, command
):
    monkeypatch.setattr(
        codex_provider, "model_is_compatible_with_profile", lambda *_, **__: True
    )
    if command != "/model model-two":
        controller.model = "temporary-model"
        controller.sl.model = "temporary-model"
        controller.widget.model = "temporary-model"
    controller._handle_command(command)
    expected = "model-two" if command == "/model model-two" else "model-one"
    assert (
        controller.model == controller.sl.model == controller.widget.model == expected
    )
    assert controller.model_override == (
        "model-two" if expected == "model-two" else None
    )


def test_incompatible_model_keeps_current_binding(controller, monkeypatch):
    monkeypatch.setattr(
        codex_provider, "model_is_compatible_with_profile", lambda *_, **__: False
    )
    monkeypatch.setattr(
        codex_provider, "model_transport_for_provider", lambda *_: "anthropic"
    )
    controller._handle_command("/model incompatible")
    assert (
        controller.model
        == controller.sl.model
        == controller.widget.model
        == "model-one"
    )
    assert "not compatible" in controller.output_fixture.getvalue()


@pytest.mark.parametrize("failure", [False, True])
def test_model_catalog_empty_and_failed_queries_leave_chat_usable(
    controller, monkeypatch, failure
):
    catalog = (
        Mock(side_effect=RuntimeError("fixture catalog failed"))
        if failure
        else Mock(return_value=[])
    )
    monkeypatch.setattr(codex_provider, "model_catalog_for_provider", catalog)
    controller._handle_command("/models")
    assert controller._active and controller.model == "model-one"
    assert (
        "fixture catalog failed" if failure else "No recommended models"
    ) in controller.output_fixture.getvalue()


@pytest.mark.parametrize(
    "command,fragment",
    [
        ("/provider invalid", "Invalid provider"),
        ("/provider openai", "openai"),
        ("/provider openrouter", "openrouter"),
    ],
)
def test_provider_commands_report_supported_choice(controller, command, fragment):
    controller._handle_command(command)
    assert fragment in controller.output_fixture.getvalue()


@pytest.mark.parametrize("failure", [None, "domain", "unexpected"])
def test_smarthelp_uses_fake_agent_boundary_and_reports_result(
    controller, monkeypatch, failure
):
    from factory.core import smarthelp

    def query(question, **kwargs):
        assert question == "fixture question" and kwargs["workdir"] == controller.cwd
        if failure == "domain":
            raise smarthelp.SmarthelpError("fixture rejected")
        if failure == "unexpected":
            raise RuntimeError("fixture parsing failed")
        callback = kwargs["progress_callback"]
        callback("other.event", tool_name="ignored")
        callback("tool.started", tool_name="Read", args={"file_path": "fixture.py"})
        return "fixture answer", "fixture log", {}

    monkeypatch.setattr(smarthelp, "smarthelp_agent_query", query)
    controller._handle_command("/smarthelp fixture question")
    text = controller.output_fixture.getvalue()
    assert (
        "fixture rejected"
        if failure == "domain"
        else "fixture parsing failed"
        if failure
        else "fixture answer"
    ) in text
    assert controller._active
