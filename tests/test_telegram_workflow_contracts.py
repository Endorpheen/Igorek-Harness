"""Telegram state, selection and routing contracts with no real message delivery."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib import parse

import pytest

from factory.core import telegram_bridge as t, scheduler
from factory.core.schema import initialize_database
from factory.core.runtime import get_db_path
from factory.core.user_input_store import (
    create_user_input_request,
    get_user_input_request,
)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    data = tmp_path / "data"
    profiles = data / "profiles"
    profiles.mkdir(parents=True)
    for name, backend, provider in [
        ("fixture", "codex", "openai-codex"),
        ("local", "cc-backend", "local"),
        ("unsupported", "claude", "z.ai"),
    ]:
        (profiles / (name + ".json")).write_text(
            json.dumps(
                {
                    "backend": backend,
                    "provider": provider,
                    "model": "fixture-model",
                    "base_url": "http://127.0.0.1:1",
                }
            )
        )
    for key in ("FACTORY_DATA_DIR", "IGOREK_FACTORY_HOME"):
        monkeypatch.setenv(key, str(data))
    monkeypatch.setenv("FACTORY_PROFILES_DIR", str(profiles))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    initialize_database(get_db_path())
    executor = Mock()
    monkeypatch.setattr(t, "ThreadPoolExecutor", lambda **_: executor)
    monkeypatch.setattr(
        t.urllib_request,
        "urlopen",
        Mock(side_effect=AssertionError("Unexpected live transport")),
    )
    result = t.TelegramBridge(
        token="synthetic-token",
        profile="fixture",
        runtime_api_base_url="http://127.0.0.1:1",
        runtime_api_key="synthetic-key",
        allowed_user_ids={"user-1"},
        allowed_chat_ids={"chat-1"},
        workdir=str(tmp_path),
        poll_timeout_s=0,
    )
    result._sessions_path.parent.mkdir(parents=True, exist_ok=True)
    result.telegram = Mock()
    result.telegram.send_message.return_value = {"ok": True}
    result.gateway_runtime.app_server = Mock()
    result.gateway_runtime.app_server.close_session.return_value = {}
    result.memory_manager = Mock()
    result.memory_manager.handle_explicit_user_memory.return_value = (False, "", [])
    result.memory_manager.review_turn.return_value = []
    result.memory_manager.format_memory_update_message.return_value = ""
    return result


@pytest.fixture
def session(bridge):
    return bridge._get_or_create_state(
        chat_id="chat-1", user_id="user-1", username="fixture"
    )


def sent(bridge):
    return "\n".join(
        str(call.args[1]) for call in bridge.telegram.send_message.call_args_list
    )


def test_constructor_requires_allowlist_and_normalizes_scope(bridge, tmp_path):
    assert bridge.poll_timeout_s == 1 and bridge.auto_approve_scope == "session"
    with pytest.raises(ValueError, match="allowed user_id"):
        t.TelegramBridge(
            token="synthetic",
            profile="fixture",
            runtime_api_base_url="http://127.0.0.1:1",
            runtime_api_key="",
            allowed_user_ids=set(),
            workdir=str(tmp_path),
        )
    assert "/help" not in [item["command"] for item in bridge._bot_commands()]
    assert any(item["command"] == "approve" for item in bridge._bot_commands())


def test_session_round_trip_retains_selection_and_delivery_signatures(bridge, session):
    session.model_override = "selected"
    session.delivered_files = {"/tmp/fixture": "sha"}
    session.smarthelp_active = True
    session.pending_task_id = "task"
    bridge._save_sessions()
    loaded = bridge._load_sessions()["chat-1"]
    assert (
        loaded.model_override == "selected"
        and loaded.pending_task_id == "task"
        and loaded.delivered_files == {"/tmp/fixture": "sha"}
        and loaded.smarthelp_active
    )
    assert loaded.lock is not session.lock


@pytest.mark.parametrize(
    "payload", [[], {}, "bad json", {"chat-1": None}, {"": {"user_id": "user"}}]
)
def test_unusable_session_documents_do_not_invent_state(bridge, payload):
    bridge._sessions_path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload)
    )
    assert bridge._load_sessions() == {}


def test_legacy_delivery_list_is_loaded_for_one_time_resend(bridge):
    bridge._sessions_path.write_text(
        json.dumps(
            {"chat-1": {"user_id": "user-1", "delivered_files": ["/tmp/fixture", " "]}}
        )
    )
    state = bridge._load_sessions()["chat-1"]
    assert (
        state.delivered_files == {"/tmp/fixture": ""}
        and state.chat_session == "telegram-chat-chat-1"
    )


@pytest.mark.parametrize("cursor", ["broken", -1, {"bad": 1}])
def test_corrupt_cursor_does_not_crash_loading_other_sessions(bridge, cursor):
    bridge._sessions_path.write_text(
        json.dumps(
            {
                "chat-1": {"user_id": "user-1", "event_after_seq": cursor},
                "chat-2": {"user_id": "user-1", "event_after_seq": 8},
            }
        )
    )
    states = bridge._load_sessions()
    assert (
        states["chat-1"].event_after_seq == 0 and states["chat-2"].event_after_seq == 8
    )


@pytest.mark.parametrize(
    "user,chat,allowed",
    [
        ("user-1", "chat-1", True),
        ("intruder", "chat-1", False),
        ("user-1", "other-chat", False),
    ],
)
def test_user_and_chat_allowlists_are_both_required(bridge, user, chat, allowed):
    assert bridge._is_allowed(user_id=user, chat_id=chat) is allowed


def test_incoming_update_is_queued_only_after_authorization(bridge):
    message = {
        "message_id": 42,
        "chat": {"id": "chat-1"},
        "from": {"id": "user-1", "username": "fixture"},
        "text": " question ",
    }
    bridge._handle_update({"message": message})
    options = bridge._update_executor.submit.call_args.kwargs
    assert (
        options["chat_id"] == "chat-1"
        and options["text"] == "question"
        and options["reply_to_message_id"] == 42
    )
    bridge._update_executor.submit.reset_mock()
    message["from"]["id"] = "intruder"
    bridge._handle_update({"message": message})
    bridge._update_executor.submit.assert_not_called()
    assert "Доступ запрещён" in sent(bridge)


def test_empty_or_structurally_invalid_updates_are_ignored(bridge):
    for update in [
        {},
        {"message": None},
        {"message": {}},
        {"message": {"chat": {}, "from": {}}},
        {"message": {"chat": {"id": "chat-1"}, "from": {"id": "user-1"}}},
    ]:
        bridge._handle_update(update)
    bridge._update_executor.submit.assert_not_called()


def test_processing_errors_are_reported_and_do_not_leave_session_locked(
    bridge, monkeypatch
):
    monkeypatch.setattr(
        bridge, "_dispatch_message", Mock(side_effect=RuntimeError("fixture failure"))
    )
    bridge._process_message(
        chat_id="chat-1",
        user_id="user-1",
        username="fixture",
        text="question",
        reply_to_message_id=42,
    )
    state = bridge._session_for_chat("chat-1")
    assert state.last_user_message_id == 42 and "fixture failure" in sent(bridge)
    assert state.lock.acquire(blocking=False)
    state.lock.release()


def test_profile_switch_clears_provider_model_and_runtime_binding(bridge, session):
    session.provider_override = "openrouter"
    session.model_override = "old"
    session.runtime_session_id = "old"
    assert bridge._set_profile_override(session, " local ") == "local"
    assert (
        session.provider_override is None
        and session.model_override is None
        and session.runtime_session_id is None
    )
    assert bridge._load_sessions()["chat-1"].profile_override == "local"
    with pytest.raises(ValueError, match="поддерживаются"):
        bridge._set_profile_override(session, "unsupported")
    assert session.profile_override == "local"


def test_provider_and_model_switches_have_backend_guards(bridge, session, monkeypatch):
    monkeypatch.setattr(
        t, "suggested_models_for_provider", lambda _: ["fixture/recommended"]
    )
    assert bridge._set_provider_override(session, " OPENROUTER ") == "openrouter"
    assert session.model_override == "fixture/recommended"
    assert bridge._set_model_override(session, " custom/model ") == "custom/model"
    assert "custom/model" in bridge._runtime_selection_summary(session)
    assert bridge._set_provider_override(session, "openai") == "openai"
    assert session.model_override is None
    with pytest.raises(ValueError):
        bridge._set_provider_override(session, "unsupported")
    bridge._set_profile_override(session, "local")
    with pytest.raises(ValueError, match="codex"):
        bridge._set_model_override(session, "model")
    with pytest.raises(ValueError, match="codex"):
        bridge._set_provider_override(session, "openai")


@pytest.mark.parametrize(
    "command,substring",
    [
        ("/help", "Команды:"),
        ("/help@fixture", "Команды:"),
        ("/session", "profile: fixture"),
        ("/provider", "provider:"),
        ("/model", "model:"),
        ("/profile", "profile:"),
        ("/smarthelp", "SmartHelp Agent:"),
        ("/approvals", "Pending approvals нет"),
    ],
)
def test_read_only_commands_do_not_invoke_provider(
    bridge, session, command, substring, monkeypatch
):
    forward = Mock()
    monkeypatch.setattr(bridge, "_forward_user_message", forward)
    bridge._dispatch_message(session, command, attachments=[])
    assert substring in sent(bridge)
    forward.assert_not_called()


@pytest.mark.parametrize(
    "command,error_text",
    [
        ("/provider invalid", "Используй /provider"),
        ("/profile unsupported", "поддерживаются"),
        ("/model ", "model:"),
        ("/plugins", ""),
    ],
)
def test_command_selection_failures_remain_user_visible(
    bridge, session, command, error_text, monkeypatch
):
    forward = Mock()
    monkeypatch.setattr(bridge, "_forward_user_message", forward)
    if command == "/plugins":
        bridge._set_profile_override(session, "local")
        error_text = "только для codex"
    bridge._dispatch_message(session, command, attachments=[])
    assert error_text in sent(bridge)
    forward.assert_not_called()


def test_smarthelp_mode_is_persistent_and_normal_text_is_routed(
    bridge, session, monkeypatch
):
    answer = Mock()
    monkeypatch.setattr(bridge, "_send_smarthelp_agent_answer", answer)
    bridge._dispatch_message(session, "/smarthelp on", attachments=[])
    assert (
        session.smarthelp_active and bridge._load_sessions()["chat-1"].smarthelp_active
    )
    bridge._dispatch_message(session, "question", attachments=[])
    answer.assert_called_once_with(session, "question")
    bridge._dispatch_message(session, "/smarthelp off", attachments=[])
    assert not session.smarthelp_active


def test_explicit_memory_and_normal_text_follow_separate_routes(
    bridge, session, monkeypatch
):
    forward = Mock()
    monkeypatch.setattr(bridge, "_forward_user_message", forward)
    bridge.memory_manager.handle_explicit_user_memory.return_value = (
        True,
        "memory saved",
        [],
    )
    bridge._dispatch_message(session, "/remember fixture", attachments=[])
    assert "memory saved" in sent(bridge)
    forward.assert_not_called()
    bridge.memory_manager.handle_explicit_user_memory.return_value = (False, "", [])
    bridge._dispatch_message(
        session, "question", attachments=[{"path": "/tmp/fixture"}]
    )
    forward.assert_called_once_with(
        session, "question", attachments=[{"path": "/tmp/fixture"}]
    )


@pytest.fixture
def schedule_api(monkeypatch):
    api = Mock()
    api.list_schedules.return_value = []
    api.create_schedule.return_value = "schedule-fixture"
    api.delete_schedule.return_value = True
    monkeypatch.setattr(scheduler, "get_scheduler", lambda: api)
    return api


@pytest.mark.parametrize("argument", ["", "list", "ls"])
def test_schedule_list_reports_empty_state(bridge, session, schedule_api, argument):
    bridge._handle_schedule_command(session, argument)
    assert "Нет отложенных задач" in sent(bridge)
    schedule_api.create_schedule.assert_not_called()


def test_schedule_list_and_numeric_removal_use_same_order(
    bridge, session, schedule_api
):
    item = SimpleNamespace(
        schedule_id="schedule-1",
        to_dict=lambda: {
            "schedule_id": "schedule-1",
            "name": "Fixture",
            "enabled": False,
            "schedule_type": "once",
            "time_value": "12:30",
            "run_count": 2,
            "last_output": "result",
        },
    )
    schedule_api.list_schedules.return_value = [item]
    bridge._handle_schedule_command(session, "list")
    assert "Fixture" in sent(bridge) and "result" in sent(bridge)
    bridge._handle_schedule_command(session, "remove 1")
    schedule_api.delete_schedule.assert_called_once_with("schedule-1")


@pytest.mark.parametrize("argument", ["remove 0", "remove -1", "remove 2"])
def test_out_of_range_schedule_number_cannot_delete_other_entry(
    bridge, session, schedule_api, argument
):
    schedule_api.list_schedules.return_value = [SimpleNamespace(schedule_id="one")]
    bridge._handle_schedule_command(session, argument)
    schedule_api.delete_schedule.assert_not_called()
    assert "вне диапазона" in sent(bridge)


@pytest.mark.parametrize(
    "argument", ["25:00 task", "12:60 task", "wrong task", "12:30"]
)
def test_invalid_schedule_times_never_create_a_job(
    bridge, session, schedule_api, argument
):
    bridge._handle_schedule_command(session, argument)
    schedule_api.create_schedule.assert_not_called()
    assert sent(bridge)


def test_schedule_creation_records_delivery_without_running_job(
    bridge, session, schedule_api
):
    bridge._handle_schedule_command(session, "12:30 create fixture report")
    packet = schedule_api.create_schedule.call_args.kwargs
    assert (
        packet["agent_id"] == "fixture"
        and packet["schedule_type"] == "once"
        and packet["time_value"] == "12:30"
    )
    assert packet["payload"] == "create fixture report" and packet["metadata"][
        "deliver"
    ] == {"platform": "telegram", "chat_id": "chat-1"}
    assert "Задача создана" in sent(bridge)


def test_schedule_service_failure_is_visible(bridge, session, schedule_api):
    schedule_api.create_schedule.side_effect = RuntimeError("fixture unavailable")
    bridge._handle_schedule_command(session, "12:30 fixture")
    assert "fixture unavailable" in sent(bridge)


def queued_request(task_id="task-1"):
    return create_user_input_request(
        task_id=task_id,
        payload={
            "context": {"server_name": "fixture-server", "reason": "fixture reason"}
        },
    )


def test_same_pending_and_workspace_task_does_not_duplicate_mcp_approval(
    bridge, session
):
    session.pending_task_id = session.workspace_task_id = "task-1"
    request = queued_request()
    assert [item["request_id"] for item in bridge._queued_mcp_approvals(session)] == [
        request["request_id"]
    ]


@pytest.mark.parametrize("argument", ["0", "-1", "3", "unknown"])
def test_invalid_queued_approval_selection_never_resolves_any_request(
    bridge, session, argument, monkeypatch
):
    session.pending_task_id = "task-1"
    session.workspace_task_id = "task-2"
    queued_request()
    queued_request("task-2")
    resolve = Mock()
    monkeypatch.setattr(bridge, "_resolve_queued_mcp_approval", resolve)
    bridge._handle_approval_command(session, command="/approve", argument=argument)
    resolve.assert_not_called()


@pytest.mark.parametrize(
    "command,action",
    [("/approve", "once"), ("/approve_session", "session"), ("/reject", "reject")],
)
def test_queued_approval_by_exact_id_keeps_requested_scope(
    bridge, session, command, action, monkeypatch
):
    session.pending_task_id = "task-1"
    request = queued_request()
    resolve = Mock()
    monkeypatch.setattr(bridge, "_resolve_queued_mcp_approval", resolve)
    bridge._handle_approval_command(
        session, command=command, argument=request["request_id"]
    )
    resolve.assert_called_once_with(session, task_id="task-1", action=action)


@pytest.mark.parametrize("action", ["cancel", "reject"])
def test_reject_or_cancel_resolves_user_input_and_releases_chat(
    bridge, session, action, monkeypatch
):
    session.pending_task_id = "task-1"
    request = queued_request()
    monkeypatch.setattr(t, "get_task", lambda _: {"status": "waiting_approval"})
    cancel = Mock()
    monkeypatch.setattr(t, "cancel_task", cancel)
    bridge._resolve_queued_mcp_approval(session, task_id="task-1", action=action)
    assert get_user_input_request(request["request_id"])["status"] == "cancelled"
    assert session.pending_task_id is None
    cancel.assert_called_once_with("task-1")


def test_image_downloads_with_same_name_do_not_overwrite_each_other(
    bridge, monkeypatch
):
    monkeypatch.setattr(t.time, "time", lambda: 1234)
    bridge.telegram.get_file.return_value = {"file_path": "photos/fixture.png"}
    bridge.telegram.download_file_bytes.side_effect = [b"first-image", b"second-image"]
    args = dict(
        chat_id="chat-1",
        file_id="fixture",
        suggested_name="image.png",
        default_extension=".png",
        kind="image",
    )
    first = bridge._download_telegram_file(**args)
    second = bridge._download_telegram_file(**args)
    assert first["path"] != second["path"]
    assert (
        Path(first["path"]).read_bytes() == b"first-image"
        and Path(second["path"]).read_bytes() == b"second-image"
    )


def test_photo_and_image_document_are_saved_with_supported_media_kinds(bridge):
    bridge.telegram.get_file.return_value = {"file_path": "photos/image.png"}
    bridge.telegram.download_file_bytes.return_value = b"fixture"
    message = {
        "photo": [{"file_id": "small"}, {"file_id": "large"}],
        "document": {
            "file_id": "document",
            "file_name": "image.png",
            "mime_type": "image/png",
        },
    }
    files = bridge._extract_telegram_attachments(chat_id="chat-1", message=message)
    assert len(files) == 2 and all(item["kind"] == "image" for item in files)
    assert [call.args[0] for call in bridge.telegram.get_file.call_args_list] == [
        "large",
        "document",
    ]
    text = bridge._compose_runtime_message("question", files)
    assert "Telegram attachments:" in text and "визуальный input" in text


def test_failed_media_download_is_reported_without_fake_attachment(bridge):
    bridge.telegram.get_file.side_effect = OSError("fixture cannot download")
    assert (
        bridge._download_telegram_file(
            chat_id="chat-1",
            file_id="fixture",
            suggested_name="image",
            default_extension=".png",
            kind="image",
        )
        is None
    )
    assert "fixture cannot download" in sent(bridge)


def test_turn_outcome_uses_confirmed_messages_files_and_timeout_status(
    bridge, session, tmp_path, monkeypatch
):
    path = tmp_path / "answer.txt"
    path.write_text("fixture")
    files = Mock()
    monkeypatch.setattr(bridge, "_send_output_files", files)
    bridge._send_turn_outcome(
        "chat-1",
        {"status": "completed", "messages": ["actual answer"], "files": [str(path)]},
    )
    assert "actual answer" in sent(bridge) and files.call_args.args[1] == [str(path)]
    bridge._send_turn_outcome("chat-1", {"status": "timeout"})
    assert "не завершил ответ вовремя" in sent(bridge)


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


def test_bot_json_transport_encodes_structured_fields_and_omits_none(monkeypatch):
    received = []
    monkeypatch.setattr(
        t.urllib_request,
        "urlopen",
        lambda request, **kwargs: (
            received.append((request, kwargs)) or Response(b'{"ok":true}')
        ),
    )
    api = t.TelegramBotApi("synthetic-token", timeout_s=3)
    assert api._request_json(
        "fixture", {"structured": {"text": "русский"}, "missing": None, "number": 2}
    ) == {"ok": True}
    request, kwargs = received[0]
    packet = parse.parse_qs(request.data.decode())
    assert (
        json.loads(packet["structured"][0]) == {"text": "русский"}
        and packet["number"] == ["2"]
        and "missing" not in packet
    )
    assert kwargs["timeout"] == 3


@pytest.mark.parametrize("body", [b"", b"[]", b'{"ok":false}'])
def test_bot_json_transport_rejects_unsuccessful_payload(monkeypatch, body):
    monkeypatch.setattr(
        t.urllib_request, "urlopen", lambda *_args, **_kwargs: Response(body)
    )
    with pytest.raises(RuntimeError, match="Telegram API fixture failed"):
        t.TelegramBotApi("synthetic")._request_json("fixture", {})


def test_message_edit_not_modified_is_not_retried(monkeypatch):
    api = t.TelegramBotApi("synthetic")
    request = Mock(side_effect=RuntimeError("message is not modified"))
    monkeypatch.setattr(api, "_request_json", request)
    assert api.edit_message_text("chat", 1, "text") is None and request.call_count == 1


def test_message_edit_html_failure_falls_back_to_plain_text(monkeypatch):
    api = t.TelegramBotApi("synthetic")
    request = Mock(side_effect=[RuntimeError("bad html"), {"ok": True}])
    monkeypatch.setattr(api, "_request_json", request)
    monkeypatch.setattr(t, "_inject_custom_emojis", lambda text: text)
    assert api.edit_message_text("chat", 1, "**bold**") == {"ok": True}
    assert (
        "parse_mode" not in request.call_args.args[1]
        and "<b>" not in request.call_args.args[1]["text"]
    )


@pytest.mark.parametrize("argument,task_id", [("1", "task-1"), ("2", "task-2")])
def test_valid_queued_approval_number_selects_exactly_one_task(
    bridge, session, argument, task_id, monkeypatch
):
    session.pending_task_id = "task-1"
    session.workspace_task_id = "task-2"
    queued_request()
    queued_request("task-2")
    resolve = Mock()
    monkeypatch.setattr(bridge, "_resolve_queued_mcp_approval", resolve)
    bridge._handle_approval_command(session, command="/approve", argument=argument)
    resolve.assert_called_once_with(session, task_id=task_id, action="once")
