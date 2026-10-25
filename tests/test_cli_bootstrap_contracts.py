"""Bootstrap contracts using temporary profiles, real SQLite and scripted input."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from factory.core import cli_bootstrap as b

REAL_PORT_PROBE = b._find_available_port


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    for key in ("IGOREK_FACTORY_HOME", "FACTORY_DATA_DIR"):
        monkeypatch.setenv(key, str(root))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(b, "PACKAGE_ROOT", tmp_path / "package")
    b.PACKAGE_ROOT.mkdir()
    monkeypatch.setattr(
        b, "get_claude_settings_path", lambda: tmp_path / "claude" / "settings.json"
    )
    monkeypatch.setattr(b, "resolve_codex_bin", lambda: str(tmp_path / "codex-fixture"))
    monkeypatch.setattr(
        b, "resolve_claude_bin", lambda: str(tmp_path / "claude-fixture")
    )
    (tmp_path / "codex-fixture").write_text("fixture, never executed")
    (tmp_path / "claude-fixture").write_text("fixture, never executed")
    monkeypatch.setattr(b, "_find_available_port", lambda: (18180, False, True))
    return root


def answers(monkeypatch, *values):
    stream = iter(values)
    monkeypatch.setattr("builtins.input", lambda _: next(stream))


@pytest.mark.parametrize(
    "raw,expected", [("", "fallback"), ("  user value  ", "user value")]
)
def test_prompt_trims_and_keeps_default(monkeypatch, raw, expected):
    answers(monkeypatch, raw)
    assert b._prompt("Name", "fallback") == expected


def test_secret_prompt_uses_getpass_without_printing_secret(monkeypatch, capsys):
    monkeypatch.setattr(b.getpass, "getpass", lambda _: "synthetic-secret")
    assert b._prompt("Secret", secret=True) == "synthetic-secret"
    assert "synthetic-secret" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "function,args",
    [
        (b._prompt, ("Name",)),
        (b._prompt_choice, ("Choice", [("one", "One")], "one")),
        (b._prompt_confirm, ("Confirm",)),
        (b._prompt_checklist, ("Items", [("one", "One", True)])),
    ],
)
@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_prompt_interruptions_are_explicit(monkeypatch, function, args, error):
    def interrupt(_):
        raise error()

    monkeypatch.setattr("builtins.input", interrupt)
    with pytest.raises(
        ValueError if error is EOFError else KeyboardInterrupt, match="Инициализация"
    ):
        function(*args)


@pytest.mark.parametrize("raw,expected", [("", "one"), ("2", "two"), ("TWO", "two")])
def test_choice_accepts_default_number_and_name(monkeypatch, raw, expected):
    answers(monkeypatch, raw)
    assert (
        b._prompt_choice("Choice", [("one", "One"), ("two", "Two")], "one") == expected
    )


def test_choice_rejects_unknown_selection(monkeypatch):
    answers(monkeypatch, "absent")
    with pytest.raises(ValueError, match="Неизвестный выбор"):
        b._prompt_choice("Choice", [("one", "One")], "one")


@pytest.mark.parametrize(
    "raw,expected",
    [("", False), ("yes", True), ("да", True), ("НЕТ", False), ("n", False)],
)
def test_confirmation_localized_answers(monkeypatch, raw, expected):
    answers(monkeypatch, raw)
    assert b._prompt_confirm("Confirm") is expected


def test_invalid_confirmation_is_not_silently_approved(monkeypatch):
    answers(monkeypatch, "whatever")
    with pytest.raises(ValueError):
        b._prompt_confirm("Confirm")


@pytest.mark.parametrize(
    "backend,provider,expected",
    [
        ("codex", None, ("codex", "openai-codex")),
        ("cc-backend", "ignored", ("cc-backend", "cc-backend")),
        ("claude", None, ("claude", "z.ai")),
        ("claude", "lmstudio-anthropic", ("claude", "lmstudio-anthropic")),
    ],
)
def test_backend_provider_compatibility(backend, provider, expected):
    assert (
        b._resolve_backend_selection(
            backend=backend, provider=provider, interactive=False
        )
        == expected
    )


@pytest.mark.parametrize(
    "backend,provider", [(None, None), ("unsupported", None), ("claude", "unsupported")]
)
def test_missing_or_unsupported_backend_is_actionable(backend, provider):
    with pytest.raises(ValueError):
        b._resolve_backend_selection(
            backend=backend, provider=provider, interactive=False
        )


def test_interactive_backend_selection_uses_scripted_choices(monkeypatch):
    answers(monkeypatch, "claude", "lmstudio-anthropic")
    assert b._resolve_backend_selection(
        backend=None, provider=None, interactive=True
    ) == ("claude", "lmstudio-anthropic")


@pytest.mark.parametrize("backend", ["codex", "claude", "cc-backend"])
def test_preflight_checks_existence_without_executing_binary(backend):
    result = b._preflight_backend_or_raise(backend)
    assert result["backend"] == backend
    assert bool(result["binary"]) is (backend != "cc-backend")


def test_missing_backend_binary_has_install_hint(monkeypatch):
    monkeypatch.setattr(b, "resolve_codex_bin", lambda: "/missing/fixture-codex")
    with pytest.raises(RuntimeError, match="npm install"):
        b._preflight_backend_or_raise("codex")


@pytest.mark.parametrize(
    "model,effort,valid",
    [
        ("gpt-5.4", "none", True),
        ("gpt-5.4-mini", "xhigh", True),
        ("gpt-5.3", "none", False),
        ("gpt-5.4", "wrong", False),
        ("gpt-5.3", " MEDIUM ", True),
    ],
)
def test_reasoning_effort_matches_model_capabilities(model, effort, valid):
    if valid:
        assert (
            b._validate_codex_reasoning_effort(model, effort) == effort.strip().lower()
        )
    else:
        with pytest.raises(ValueError, match="reasoning effort"):
            b._validate_codex_reasoning_effort(model, effort)


def test_reasoning_and_execution_modes_require_explicit_input(monkeypatch):
    assert (
        b._resolve_codex_reasoning_effort(
            model="fixture", reasoning_effort=None, interactive=False
        )
        == b.DEFAULT_CODEX_REASONING_EFFORT
    )
    assert not b._resolve_codex_dangerous_mode(dangerous_mode=None, interactive=False)
    answers(monkeypatch, "low", "danger")
    assert (
        b._resolve_codex_reasoning_effort(
            model="fixture", reasoning_effort=None, interactive=True
        )
        == "low"
    )
    assert b._resolve_codex_dangerous_mode(dangerous_mode=None, interactive=True)
    assert not b._resolve_codex_dangerous_mode(dangerous_mode=False, interactive=True)


def test_environment_file_preserves_comments_and_unrelated_values(tmp_path):
    path = tmp_path / "synthetic.env"
    b._write_env_file(
        path,
        [
            "# Keep comment",
            "OLD=preserved",
            "TOKEN=before",
            "TOKEN=duplicate",
            "bad line",
        ],
    )
    b._upsert_env_values(path, {"TOKEN": "synthetic-new", "NEW": "new"})
    lines, values = b._read_env_file(path)
    assert values == {"OLD": "preserved", "TOKEN": "synthetic-new", "NEW": "new"}
    assert lines[0] == "# Keep comment" and "bad line" in lines
    assert path.read_text().endswith("\n")


def test_env_path_precedence_is_limited_to_fixture(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    assert b._resolve_env_file_path(data) == b.PACKAGE_ROOT / ".env"
    (data / ".env.local").write_text("fixture")
    assert b._resolve_env_file_path(data) == data / ".env.local"
    (b.PACKAGE_ROOT / ".env").write_text("fixture")
    assert b._resolve_env_file_path(data) == b.PACKAGE_ROOT / ".env"
    assert b._resolve_env_file_path(data, str(data / "explicit")) == data / "explicit"


@pytest.mark.parametrize(
    "provided,existing,expected",
    [("new", "old", "new"), (None, " old ", "old"), (None, None, "fallback")],
)
def test_noninteractive_env_resolution_does_not_prompt(provided, existing, expected):
    assert (
        b._env_keep_or_prompt(
            key="FIXTURE",
            label="Value",
            existing=existing,
            interactive=False,
            provided=provided,
            default="fallback",
        )
        == expected
    )


def test_interactive_env_can_keep_or_replace_without_echoing_existing(
    monkeypatch, capsys
):
    answers(monkeypatch, "yes", "no", "replacement")
    assert (
        b._env_keep_or_prompt(
            key="FIXTURE", label="Value", existing="synthetic-old", interactive=True
        )
        == "synthetic-old"
    )
    assert (
        b._env_keep_or_prompt(
            key="FIXTURE", label="Value", existing="synthetic-old", interactive=True
        )
        == "replacement"
    )
    assert "synthetic-old" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "existing,expected", [("true", True), ("OFF", False), ("unknown", False)]
)
def test_existing_boolean_environment_is_resolved(existing, expected):
    assert (
        b._resolve_bool_choice(
            label="Fixture",
            interactive=False,
            provided=None,
            existing=existing,
            default=False,
        )
        is expected
    )
    assert b._resolve_bool_choice(
        label="Fixture",
        interactive=False,
        provided=True,
        existing=existing,
        default=False,
    )


def test_checklist_toggles_only_valid_numbers(monkeypatch):
    answers(monkeypatch, "1, 2, 99, bad")
    assert b._prompt_checklist(
        "Fixture", [("one", "One", True), ("two", "Two", False)]
    ) == ["two"]


def test_component_toolset_and_display_choices(monkeypatch):
    assert b._resolve_component_selection(
        interactive=False,
        setup_telegram=True,
        toolsets_provided=None,
        display_provided=None,
    ) == {
        "setup_telegram": True,
        "configure_toolsets": False,
        "configure_display": False,
    }
    answers(monkeypatch, "1,2,3")
    assert all(
        b._resolve_component_selection(
            interactive=True,
            setup_telegram=False,
            toolsets_provided=None,
            display_provided=None,
        ).values()
    )
    assert b._resolve_toolsets(interactive=False, provided=[" file ", "", "web"]) == [
        "file",
        "web",
    ]
    assert b._resolve_toolsets(interactive=False, provided=None) == []
    answers(monkeypatch, "")
    assert b._resolve_toolsets(interactive=True, provided=None, previous=["web"]) == [
        "web"
    ]
    assert (
        b._resolve_display_style(interactive=False, provided=" MINIMAL ") == "minimal"
    )
    assert b._resolve_display_style(interactive=False, provided=None) == ""
    answers(monkeypatch, "compact")
    assert b._resolve_display_style(interactive=True, provided=None) == "compact"


@pytest.mark.parametrize(
    "choice,expected", [("write", True), ("edit", False), ("abort", None)]
)
def test_review_requires_explicit_write_or_returns_to_edit(
    monkeypatch, choice, expected
):
    args = dict(
        backend="codex",
        provider="openai-codex",
        model="fixture",
        reasoning_effort="low",
        dangerous_mode=False,
        enabled_toolsets=["file"],
        display_style="compact",
        telegram_enabled=False,
        interactive=True,
    )
    answers(monkeypatch, choice)
    if expected is None:
        with pytest.raises(ValueError, match="отменена"):
            b._review_and_confirm(**args)
    else:
        assert b._review_and_confirm(**args) is expected


@pytest.mark.parametrize(
    "backend,provider,kwargs",
    [
        ("claude", "z.ai", {}),
        ("claude", "lmstudio-anthropic", {"base_url": "http://127.0.0.1:1/"}),
        ("codex", "openai-codex", {"reasoning_effort": "low", "dangerous_mode": True}),
        ("cc-backend", "cc-backend", {"base_url": "http://127.0.0.1:1"}),
    ],
)
def test_profile_payload_is_backend_specific(backend, provider, kwargs):
    payload = b._profile_payload(
        backend,
        provider,
        "fixture",
        enabled_toolsets=["file"],
        display_style="minimal",
        **kwargs,
    )
    assert payload["backend"] == backend and payload["model"] == "fixture"
    assert payload["enabled_toolsets"] == ["file"] and payload["display"] == {
        "tool_display": "minimal"
    }
    if backend == "cc-backend":
        assert payload["filesystem_mode"] == "user-read"
    elif provider == "lmstudio-anthropic":
        assert payload["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:1"
    elif backend == "codex":
        assert (
            payload["reasoning_effort"] == "low"
            and payload["dangerously_bypass_approvals_and_sandbox"]
        )


@pytest.mark.parametrize(
    "backend,provider", [("claude", "lmstudio-anthropic"), ("invalid", "invalid")]
)
def test_invalid_profile_payload_does_not_write(backend, provider):
    with pytest.raises(ValueError):
        b._profile_payload(backend, provider, "fixture")


def write_profiles(directory, **overrides):
    args = dict(
        profiles_dir=directory,
        backend="cc-backend",
        provider="cc-backend",
        model="fixture",
        base_url=None,
        api_key=None,
        reasoning_effort=None,
        dangerous_mode=False,
        profile_name="fixture",
        force=False,
        interactive=False,
    )
    args.update(overrides)
    return b._write_profiles(**args)


def test_existing_profiles_are_preserved_unless_forced_or_confirmed(
    tmp_path, monkeypatch
):
    directory = tmp_path / "profiles"
    written, names, skipped = write_profiles(directory)
    original = (directory / "fixture.json").read_text()
    assert len(written) == 1 and names == ["fixture"] and skipped == []
    assert write_profiles(directory)[2] == ["fixture"]
    assert write_profiles(directory, model="new")[2] == ["fixture"]
    assert (directory / "fixture.json").read_text() == original
    answers(monkeypatch, "no", "yes")
    assert write_profiles(directory, model="new", interactive=True)[2] == ["fixture"]
    assert write_profiles(directory, model="new", interactive=True)[0] == [
        directory / "fixture.json"
    ]
    assert json.loads((directory / "fixture.json").read_text())["model"] == "new"
    write_profiles(directory, model="forced", force=True)
    assert json.loads((directory / "fixture.json").read_text())["model"] == "forced"


def test_claude_settings_merge_and_force_have_different_semantics():
    path = b.get_claude_settings_path()
    b._write_json(
        path,
        {
            "unrelated": True,
            "env": {"KEEP": "kept"},
            "spinnerTipsOverride": {"tips": ["custom"]},
        },
    )
    b._write_claude_settings("synthetic-key", "fixture", False)
    merged = b._load_json(path)
    assert merged["unrelated"] and merged["env"]["KEEP"] == "kept"
    assert merged["env"]["ANTHROPIC_AUTH_TOKEN"] == "synthetic-key" and merged[
        "spinnerTipsOverride"
    ] == {"tips": ["custom"]}
    b._write_claude_settings("synthetic-key", "next", True)
    assert "unrelated" not in b._load_json(path)


@pytest.mark.parametrize(
    "backend,provider,extras",
    [
        ("cc-backend", None, {}),
        ("codex", None, {"reasoning_effort": "low"}),
        ("claude", "z.ai", {"api_key": "synthetic-key"}),
        ("claude", "lmstudio-anthropic", {"base_url": "http://127.0.0.1:1"}),
    ],
)
def test_real_init_and_doctor_round_trip(isolated, backend, provider, extras):
    result = b.init_cli_environment(
        data_dir=str(isolated),
        backend=backend,
        provider=provider,
        model="fixture",
        interactive=False,
        **extras,
    )
    assert result["status"] == "ok" and result["model"] == "fixture"
    assert Path(result["db_path"]).is_file() and Path(result["workspace_dir"]).is_dir()
    assert result["profiles_present"][result["profile_names"][0]]["model"] == "fixture"
    assert result["human_summary"].startswith("Инициализация завершена")
    checks = b.doctor_cli_environment(str(isolated))
    assert checks["status"] == "ok" and checks["db_tables"]["tasks"]
    assert "task_id" in checks["db_columns"]["tasks_required_columns"]
    assert checks["bootstrap_exists"] and checks["memory_index_exists"]


def test_init_keeps_existing_effective_profile(isolated):
    b.init_cli_environment(
        data_dir=str(isolated),
        backend="codex",
        model="existing",
        reasoning_effort="low",
        interactive=False,
    )
    result = b.init_cli_environment(
        data_dir=str(isolated),
        backend="codex",
        model="requested",
        reasoning_effort="high",
        interactive=False,
    )
    assert result["model"] == "existing" and result["requested_model"] == "requested"
    assert result["reasoning_effort"] == "low" and result["profiles_skipped"] == [
        "codex"
    ]


def test_doctor_reports_missing_database_and_corrupt_profile(isolated):
    profiles = isolated / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "broken.json").write_text("{")
    checks = b.doctor_cli_environment(str(isolated))
    assert checks["status"] == "error" and not checks["db_exists"]
    assert "error" in checks["profiles"]["broken"]


def telegram_args(root):
    return dict(
        data_root=root,
        profile_names=["fixture"],
        interactive=False,
        force=False,
        env_file=str(root / "synthetic.env"),
        setup_telegram=True,
        telegram_bot_token="synthetic-token",
        telegram_allowed_user_ids="123",
        telegram_profile=None,
        telegram_auto_approve=False,
        telegram_auto_approve_scope="turn",
    )


def test_telegram_configuration_only_writes_fixture_and_hides_secrets(isolated, capsys):
    args = telegram_args(isolated)
    result = b._configure_telegram_env(**args)
    assert (
        result["enabled"]
        and result["profile"] == "fixture"
        and not result["auto_approve"]
    )
    _, env = b._read_env_file(Path(args["env_file"]))
    assert (
        env["FACTORY_TELEGRAM_BOT_TOKEN"] == "synthetic-token"
        and len(env["FACTORY_API_KEY"]) == 64
    )
    assert "synthetic-token" not in json.dumps(result) and not capsys.readouterr().out
    args.update(
        telegram_auto_approve=True, telegram_auto_approve_scope="invalid", force=True
    )
    assert b._configure_telegram_env(**args)["auto_approve_scope"] == "session"
    assert (
        "FACTORY_TELEGRAM_ALLOWED_CHAT_IDS"
        in b._read_env_file(Path(args["env_file"]))[1]
    )


@pytest.mark.parametrize("field", ["telegram_bot_token", "telegram_allowed_user_ids"])
def test_incomplete_telegram_setup_has_no_partial_file(isolated, field):
    args = telegram_args(isolated)
    args[field] = ""
    with pytest.raises(ValueError):
        b._configure_telegram_env(**args)
    assert not Path(args["env_file"]).exists()


def test_disabled_telegram_setup_writes_nothing(isolated):
    args = telegram_args(isolated)
    args["setup_telegram"] = False
    assert b._configure_telegram_env(**args) is None
    assert not Path(args["env_file"]).exists()


@pytest.mark.parametrize(
    "failures,expected",
    [
        ([], (18180, True, True)),
        ([OSError()], (18181, False, True)),
        ([PermissionError()], (18180, True, False)),
        ([OSError(), OSError()], (18180, False, True)),
    ],
)
def test_port_probe_reports_busy_and_unavailable_without_binding_owner_ports(
    monkeypatch, failures, expected
):
    sock = Mock()
    sock.__enter__ = Mock(return_value=sock)
    sock.__exit__ = Mock(return_value=False)
    sock.bind.side_effect = failures + [None] * 3
    monkeypatch.setattr(b.socket, "socket", lambda *_: sock)
    assert REAL_PORT_PROBE(start_port=18180, max_attempts=2) == expected
