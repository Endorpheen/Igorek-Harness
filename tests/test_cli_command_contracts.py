"""CLI packet and persistence contracts without starting runtimes or providers."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from factory import cli


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    for key in ("FACTORY_DATA_DIR", "IGOREK_FACTORY_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FACTORY_VAULT_ENABLED", "false")
    assert Path(cli.get_db_path()).is_relative_to(tmp_path)
    return tmp_path


def invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["igorek-factory", *args])
    result = cli.main()
    output = capsys.readouterr()
    return result, output


def parsed(monkeypatch, capsys, *args):
    result, output = invoke(monkeypatch, capsys, *args)
    assert result == 0 and not output.err
    return json.loads(output.out)


def test_mcp_real_registry_round_trip_never_launches_commands(
    state, monkeypatch, capsys
):
    prefix = ["mcp", "--data-dir", str(state)]
    result = parsed(
        monkeypatch,
        capsys,
        *prefix,
        "add-stdio",
        "fixture",
        "--env",
        "SYNTHETIC=value=part",
        "--description",
        "Fixture tool",
        "--capability",
        "read",
        "--tool-hint",
        "fixture.read",
        "--disabled",
        "--",
        "never-execute-fixture",
        "--arg",
    )
    assert result["server"]["command"] == "never-execute-fixture"
    assert result["server"]["args"] == ["--arg"] and result["server"]["env"] == {
        "SYNTHETIC": "value=part"
    }
    assert result["server"]["disabled"]
    result = parsed(
        monkeypatch,
        capsys,
        *prefix,
        "add-http",
        "remote",
        "http://127.0.0.1:1/mcp",
        "--header",
        "X-Fixture=one=two",
        "--description",
        "Fixture",
        "--capability",
        "read",
        "--tool-hint",
        "read",
    )
    assert result["server"]["url"] == "http://127.0.0.1:1/mcp" and result["server"][
        "headers"
    ] == {"X-Fixture": "one=two"}
    profiles = state / "profiles"
    profiles.mkdir(exist_ok=True)
    (profiles / "fixture.json").write_text(
        json.dumps(
            {
                "backend": "cc-backend",
                "model": "fixture",
                "base_url": "http://127.0.0.1:1",
            }
        )
    )
    assert (
        "remote"
        in parsed(monkeypatch, capsys, *prefix, "enable", "fixture", "remote")[
            "mcp_servers"
        ]
    )
    assert (
        "remote"
        in parsed(monkeypatch, capsys, *prefix, "list", "--profile", "fixture")[
            "enabled_for_profile"
        ]
    )
    assert (
        "remote"
        not in parsed(monkeypatch, capsys, *prefix, "disable", "fixture", "remote")[
            "mcp_servers"
        ]
    )
    assert parsed(monkeypatch, capsys, *prefix, "remove", "remote")["status"] == "ok"
    assert (
        parsed(monkeypatch, capsys, *prefix, "remove", "remote")["status"] == "missing"
    )


def test_mcp_telemetry_filters_and_parses_json(state, monkeypatch, capsys):
    db = cli.initialize_database(cli.get_db_path())
    with cli.open_db(db) as conn:
        for identifier, event, profile, details, stamp in [
            ("one", "mcp_tool_call", "fixture", '{"ok":true}', 10),
            ("two", "mcp_error", "other", "plain", 20),
            ("three", "unrelated", "fixture", "{}", 30),
        ]:
            conn.execute(
                "INSERT INTO events(event_id,event_type,profile,details,created_at) VALUES(?,?,?,?,?)",
                (identifier, event, profile, details, stamp),
            )
        conn.commit()
    prefix = ["mcp", "--data-dir", str(state), "telemetry"]
    result = parsed(monkeypatch, capsys, *prefix, "--tail", "5")
    assert [item["event_id"] for item in result["items"]] == ["two", "one"] and result[
        "items"
    ][1]["details"] == {"ok": True}
    result = parsed(
        monkeypatch,
        capsys,
        *prefix,
        "--profile",
        "fixture",
        "--event-type",
        "mcp_tool_call",
        "--tail",
        "0",
    )
    assert (
        result["tail"] == 1
        and result["count"] == 1
        and result["items"][0]["event_id"] == "one"
    )


def test_mcp_stdio_requires_command_and_valid_env_pairs(state, monkeypatch, capsys):
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, capsys, "mcp", "add-stdio", "fixture")
    assert error.value.code == 2
    with pytest.raises(ValueError, match="KEY=VALUE"):
        invoke(
            monkeypatch,
            capsys,
            "mcp",
            "add-stdio",
            "fixture",
            "--env",
            "invalid",
            "--",
            "never-execute",
        )


@pytest.mark.parametrize(
    "tokens,method,expected",
    [
        (["status"], "status", {}),
        (["me"], "me", {}),
        (
            ["feed", "--limit", "3", "--sort", "new"],
            "feed",
            {"limit": 3, "sort": "new"},
        ),
        (
            ["posts", "--limit", "2", "--submolt", "fixture"],
            "posts",
            {"limit": 2, "submolt": "fixture"},
        ),
        (
            ["comments", "post-id", "--limit", "4"],
            "comments",
            {"post_id": "post-id", "limit": 4},
        ),
        (
            ["actions", "--source", "manual", "--status", "ok"],
            "actions",
            {"source": "manual", "status": "ok"},
        ),
        (["post-analysis", "--hours", "2"], "post_analysis", {"hours": 2}),
        (["persona-list"], "persona_list", {}),
        (["persona-show"], "persona_show", {}),
        (
            ["persona-set", "--mode", "fixed", "--fixed", "witty"],
            "persona_set",
            {"mode": "fixed", "fixed": "witty"},
        ),
        (
            [
                "generate-post-draft",
                "--profile",
                "fixture",
                "--submolt",
                "fixture",
                "--brief",
                "brief",
            ],
            "generate_post",
            {"profile": "fixture", "brief": "brief"},
        ),
        (["post-drafts", "--limit", "3"], "post_drafts", {"limit": 3}),
        (["show-post-draft", "draft-id"], "show_post_draft", {}),
        (
            ["edit-post-draft", "draft-id", "--title", "Title", "--content", "Body"],
            "update_post_draft",
            {"title": "Title", "content": "Body"},
        ),
        (["approve-post-draft", "draft-id"], "approve_post", {}),
        (["send-post-draft", "draft-id"], "send_post", {}),
        (["submolts", "--limit", "2"], "submolts", {"limit": 2}),
        (["subscribe", "fixture"], "subscribe_submolt", {}),
        (
            ["search", "query", "--type", "posts", "--author", "author"],
            "search",
            {"query": "query", "result_type": "posts", "author": "author"},
        ),
        (
            ["heartbeat-check", "--feed-limit", "2"],
            "heartbeat_check",
            {"feed_limit": 2},
        ),
        (
            ["heartbeat-tick", "--force", "--interval", "60"],
            "heartbeat_tick",
            {"force": True, "interval_s": 60},
        ),
        (["inbox", "--limit", "2"], "inbox", {"limit": 2}),
        (["review-inbox", "--limit", "2"], "review_inbox", {"limit": 2}),
        (["reactions", "--min-score", "5"], "reactions", {"min_score": 5}),
        (["next-reaction", "--min-score", "5"], "next_reaction", {"min_score": 5}),
        (
            ["generate-drafts", "--profile", "fixture", "--force", "--limit", "2"],
            "generate_drafts",
            {"profile": "fixture", "force": True, "limit": 2},
        ),
        (["drafts", "--limit", "2"], "drafts", {"limit": 2}),
        (["next-draft"], "next_draft", {}),
        (["show-draft", "candidate"], "show_draft", {}),
        (["edit-draft", "candidate", "--text", "text"], "update_draft", {}),
        (["approve-draft", "candidate"], "approve_draft", {}),
        (["send-draft", "candidate"], "send_draft", {}),
    ],
)
def test_boltbook_dispatch_packets_without_network_or_model(
    tokens, method, expected, monkeypatch, capsys
):
    boundary = Mock(return_value={"fixture": "accepted"})
    monkeypatch.setattr(cli, "boltbook_" + method, boundary)
    result = parsed(monkeypatch, capsys, "boltbook", *tokens)
    assert result == {"fixture": "accepted"} and boundary.call_count == 1
    for key, value in expected.items():
        assert boundary.call_args.kwargs[key] == value
    if tokens[0] in {
        "show-post-draft",
        "edit-post-draft",
        "approve-post-draft",
        "send-post-draft",
    }:
        assert boundary.call_args.args[0] == "draft-id"
    if tokens[0] in {"show-draft", "edit-draft", "approve-draft", "send-draft"}:
        assert boundary.call_args.args[0] == "candidate"


@pytest.mark.parametrize(
    "tokens,method",
    [
        (["status"], "status"),
        (["scan", "--path", "fixture"], "scan"),
        (["list"], "list_credentials"),
        (["show-metadata", "fixture"], "show_metadata"),
        (["delete", "fixture", "--yes"], "delete"),
        (["verify", "--service", "fixture"], "verify"),
        (["audit", "--limit", "3"], "audit"),
    ],
)
def test_vault_dispatch_never_accesses_real_vault(tokens, method, monkeypatch, capsys):
    boundary = Mock(return_value={"status": "fixture"})
    monkeypatch.setattr(cli.vault_cli, method, boundary)
    assert (
        parsed(monkeypatch, capsys, "vault", *tokens) == {"status": "fixture"}
        and boundary.call_count == 1
    )


def test_vault_failure_is_visible_without_success_json(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.vault_cli, "status", Mock(side_effect=RuntimeError("fixture unavailable"))
    )
    code, output = invoke(monkeypatch, capsys, "vault", "status")
    assert code == 2 and "fixture unavailable" in output.err and not output.out


def test_auth_stores_trimmed_synthetic_secret_without_echo(monkeypatch, capsys):
    boundary = Mock(return_value={"status": "ok", "credential": {"service": "fixture"}})
    monkeypatch.setattr(cli.vault_cli, "add", boundary)
    code, output = invoke(
        monkeypatch, capsys, "auth", "login", "fixture", "--key", " synthetic-secret "
    )
    assert code == 0 and "synthetic-secret" not in output.out + output.err
    assert (
        boundary.call_args.kwargs["secret"] == "synthetic-secret"
        and boundary.call_args.kwargs["replace_existing"]
    )


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"status": "ok", "result_text": "done\n"}, (0, "done")),
        ({"status": "fail", "error_message": "failure"}, (1, "failure")),
        ({"status": "ok", "output_text": "fallback"}, (0, "fallback")),
    ],
)
def test_ask_plain_output_and_exit_status(result, expected, monkeypatch, capsys):
    boundary = Mock(return_value=result)
    monkeypatch.setattr(cli, "ask_task", boundary)
    code, output = invoke(
        monkeypatch,
        capsys,
        "ask",
        "question",
        "--profile",
        "fixture",
        "--timeout",
        "3",
        "--interval",
        "0.1",
    )
    assert (code, output.out.strip()) == expected
    assert boundary.call_args.kwargs == {
        "text": "question",
        "profile": "fixture",
        "timeout_s": 3,
        "interval_s": 0.1,
    }


def test_ask_json_preserves_failure_object(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "ask_task",
        Mock(return_value={"status": "fail", "error_message": "fixture"}),
    )
    assert parsed(monkeypatch, capsys, "ask", "question", "--json")["status"] == "fail"


def test_task_create_show_and_list_use_real_isolated_database(monkeypatch, capsys):
    created = parsed(
        monkeypatch, capsys, "task", "create", "fixture text", "--profile", "fixture"
    )
    identifier = created["task_id"]
    assert (
        parsed(monkeypatch, capsys, "task", "show", identifier)["input_text"]
        == "fixture text"
    )
    rows = parsed(monkeypatch, capsys, "task", "list", "--limit", "1")
    assert rows["tasks"][0]["task_id"] == identifier
    code, output = invoke(monkeypatch, capsys, "task", "show", "missing")
    assert code == 1 and json.loads(output.out) == {"error": "Task not found"}


@pytest.mark.parametrize(
    "command,target", [("run", "run_local_stack"), ("gateway", "run_gateway_stack")]
)
def test_runtime_options_are_passed_without_starting_services(
    command, target, monkeypatch, capsys
):
    boundary = Mock(return_value=7)
    monkeypatch.setattr(cli, target, boundary)
    code, _ = invoke(
        monkeypatch,
        capsys,
        command,
        "--profile",
        "fixture",
        "--host",
        "127.0.0.1",
        "--port",
        "18181",
        "--poll-interval",
        "2",
    )
    assert (
        code == 7
        and boundary.call_args.kwargs["api_port"] == 18181
        and boundary.call_args.kwargs["profile"] == "fixture"
    )


def test_telegram_whitelists_and_options_are_normalized_without_sending(
    monkeypatch, capsys
):
    boundary = Mock(return_value=0)
    monkeypatch.setattr(cli, "run_telegram_bridge", boundary)
    code, _ = invoke(
        monkeypatch,
        capsys,
        "telegram",
        "--token",
        "synthetic-token",
        "--profile",
        "fixture",
        "--allowed-user-ids",
        " 1,2,,1 ",
        "--allowed-chat-ids",
        " 3,4 ",
        "--workdir",
        "/tmp/fixture",
        "--poll-timeout",
        "7",
        "--auto-approve-scope",
        "turn",
    )
    assert code == 0 and boundary.call_args.kwargs["allowed_user_ids"] == {"1", "2"}
    assert (
        boundary.call_args.kwargs["allowed_chat_ids"] == {"3", "4"}
        and boundary.call_args.kwargs["poll_timeout_s"] == 7
    )


@pytest.mark.parametrize(
    "command,substring",
    [("launch-help", "igorek-factory run"), ("env-help", "FACTORY_DATA_DIR")],
)
def test_help_documents_actual_entrypoints(command, substring, monkeypatch, capsys):
    code, output = invoke(monkeypatch, capsys, command)
    assert code == 0 and substring in output.out


def test_doctor_human_summary_explains_each_missing_requirement(capsys):
    cli._print_doctor_followup(
        {
            "status": "error",
            "profiles": {"one": {"backend": "codex"}, "two": {"backend": "claude"}},
            "claude_settings_required": True,
        }
    )
    text = capsys.readouterr().out
    assert all(
        part in text
        for part in [
            "не найдена база",
            "не найдена папка",
            "settings.json",
            "CLI claude недоступен",
            "CLI codex недоступен",
            "one [codex]",
        ]
    )
    cli._print_doctor_followup(
        {
            "status": "ok",
            "db_exists": True,
            "profiles_dir_exists": True,
            "profiles": {"fixture": {"backend": "cc-backend"}},
            "mcp_servers": ["fixture"],
        }
    )
    assert "Проверка окружения пройдена" in capsys.readouterr().out


@pytest.mark.parametrize(
    "exception,code",
    [
        (ValueError("fixture bad"), 2),
        (RuntimeError("fixture absent"), 2),
        (KeyboardInterrupt(), 130),
    ],
)
def test_init_failures_have_stable_exit_status(exception, code, monkeypatch, capsys):
    monkeypatch.setattr(cli, "init_cli_environment", Mock(side_effect=exception))
    actual, output = invoke(
        monkeypatch, capsys, "init", "--non-interactive", "--backend", "cc-backend"
    )
    assert actual == code and output.err and not output.out


def test_init_forwards_model_permissions_and_toolset_options(monkeypatch, capsys):
    boundary = Mock(return_value={"status": "ok"})
    monkeypatch.setattr(cli, "init_cli_environment", boundary)
    assert parsed(
        monkeypatch,
        capsys,
        "init",
        "--backend",
        "codex",
        "--non-interactive",
        "--enabled-toolsets",
        "file, web,,",
        "--tool-display",
        "minimal",
        "--dangerous-mode",
        "--reasoning-effort",
        "low",
    ) == {"status": "ok"}
    options = boundary.call_args.kwargs
    assert (
        options["enabled_toolsets"] == ["file", "web"]
        and options["tool_display"] == "minimal"
    )
    assert (
        options["dangerous_mode"]
        and options["reasoning_effort"] == "low"
        and not options["interactive"]
    )
