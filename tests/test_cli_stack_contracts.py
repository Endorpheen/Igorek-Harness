"""CLI stack lifecycle contracts with synthetic child processes and HTTP peers."""

import io
import os
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib import error

import pytest

from factory.core import cli_run as c


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for key in list(os.environ):
        if key.startswith(("FACTORY_", "IGOREK_", "MCP_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    preset = tmp_path / "builtin.env"
    preset.write_text("BUILTIN_ONLY=default\nSHARED=builtin\n")
    monkeypatch.setattr(c, "_resolve_builtin_gateway_preset_path", lambda: preset)
    monkeypatch.setattr(
        c,
        "_resolve_gateway_env_files",
        lambda explicit=None: [Path(explicit)] if explicit else [],
    )
    monkeypatch.setattr(
        c,
        "build_vault_services",
        Mock(side_effect=AssertionError("Unexpected real Vault")),
    )
    monkeypatch.setattr(
        c.urllib_request,
        "urlopen",
        Mock(side_effect=AssertionError("Unexpected real HTTP")),
    )
    return tmp_path


def test_env_files_override_builtin_but_explicit_environment_wins(
    isolated, monkeypatch
):
    path = isolated / "explicit.env"
    path.write_text("SHARED=file\nFILE_ONLY=from-file\nEXPLICIT=file\n")
    monkeypatch.setenv("EXPLICIT", "from-env")
    c._bootstrap_stack_env(env_file=str(path))
    assert os.environ["SHARED"] == "file" and os.environ["BUILTIN_ONLY"] == "default"
    assert (
        os.environ["EXPLICIT"] == "from-env" and os.environ["FILE_ONLY"] == "from-file"
    )
    assert os.environ["FACTORY_STACK_ENV_BOOTSTRAPPED"] == "true"


def test_later_env_files_win_without_overriding_process_environment(
    isolated, monkeypatch
):
    first = isolated / "first.env"
    second = isolated / "second.env"
    first.write_text("SHARED=first\n")
    second.write_text("SHARED=second\n")
    monkeypatch.setattr(
        c, "_resolve_gateway_env_files", lambda explicit=None: [first, second]
    )
    c._bootstrap_stack_env()
    assert os.environ["SHARED"] == "second"


def test_bootstrap_sentinel_does_not_load_files_or_broker(monkeypatch):
    monkeypatch.setenv("FACTORY_STACK_ENV_BOOTSTRAPPED", "true")
    load = Mock(side_effect=AssertionError("Unexpected file reload"))
    monkeypatch.setattr(c, "_load_env_file", load)
    c._bootstrap_stack_env()
    load.assert_not_called()


@pytest.mark.parametrize("existing", ["", "explicit"])
def test_env_defaults_preserve_even_explicit_empty_values(monkeypatch, existing):
    monkeypatch.setenv("SHARED", existing)
    c._apply_env_defaults({"SHARED": "default", "NEW": "new"})
    assert os.environ["SHARED"] == existing and os.environ["NEW"] == "new"


@pytest.mark.parametrize("enabled,services", [("false", "tavily"), ("true", "")])
def test_optional_vault_configuration_does_not_open_broker(
    monkeypatch, enabled, services
):
    monkeypatch.setenv("FACTORY_VAULT_ENABLED", enabled)
    monkeypatch.setenv("FACTORY_VAULT_SERVICES", services)
    assert c._materialize_gateway_vault_env() == {}
    c.build_vault_services.assert_not_called()


def test_gateway_broker_only_logs_names_and_preserves_synthetic_env(
    monkeypatch, capsys
):
    monkeypatch.setenv("FACTORY_VAULT_ENABLED", "true")
    monkeypatch.setenv("FACTORY_VAULT_SERVICES", "fixture")
    monkeypatch.setenv("FACTORY_VAULT_TTL_SECONDS", "bad")
    broker = Mock()
    broker.get_ephemeral_env.return_value = SimpleNamespace(
        allowed=True, env={"SYNTHETIC_KEY": "synthetic-secret"}, reason="allowed"
    )
    monkeypatch.setattr(
        c, "build_vault_services", lambda **_: SimpleNamespace(broker=broker)
    )
    result = c._materialize_gateway_vault_env()
    assert (
        result == {"SYNTHETIC_KEY": "synthetic-secret"}
        and os.environ["SYNTHETIC_KEY"] == "synthetic-secret"
    )
    assert broker.get_ephemeral_env.call_args.kwargs["ttl"] == 900
    assert "synthetic-secret" not in capsys.readouterr().out
    monkeypatch.delenv("SYNTHETIC_KEY")


def test_denied_gateway_broker_does_not_publish_partial_env(monkeypatch):
    monkeypatch.setenv("FACTORY_VAULT_ENABLED", "true")
    monkeypatch.setenv("FACTORY_VAULT_SERVICES", "one,two")
    broker = Mock()
    broker.get_ephemeral_env.side_effect = [
        SimpleNamespace(allowed=True, env={"SYNTHETIC_KEY": "synthetic-secret"}),
        SimpleNamespace(allowed=False, env={}, reason="fixture denied"),
    ]
    monkeypatch.setattr(
        c, "build_vault_services", lambda **_: SimpleNamespace(broker=broker)
    )
    with pytest.raises(RuntimeError, match="fixture denied"):
        c._materialize_gateway_vault_env()
    assert "SYNTHETIC_KEY" not in os.environ


class Response:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_http_readiness_accepts_only_ready_success_status(monkeypatch):
    monkeypatch.setattr(c.time, "sleep", lambda _: None)
    values = iter([0, 0, 6])
    monkeypatch.setattr(c.time, "time", lambda: next(values))
    monkeypatch.setattr(
        c.urllib_request,
        "urlopen",
        Mock(
            side_effect=error.HTTPError(
                "http://fixture", 404, "not found", {}, io.BytesIO(b"not ready")
            )
        ),
    )
    assert not c._wait_http_ready("http://127.0.0.1:1", timeout_s=5)


def test_http_readiness_tries_supported_health_endpoints(monkeypatch):
    requests = []

    def open_url(url, **kwargs):
        requests.append(url)
        if url.endswith("/health"):
            raise error.URLError("fixture unavailable")
        return Response(200)

    monkeypatch.setattr(c.urllib_request, "urlopen", open_url)
    assert c._wait_http_ready("http://127.0.0.1:1")
    assert requests == ["http://127.0.0.1:1/health", "http://127.0.0.1:1/v1/models"]


def test_embeddings_model_lookup_prefers_retrieval_model(isolated, monkeypatch):
    directory = isolated / "models/embeddings"
    directory.mkdir(parents=True)
    (directory / "other.gguf").write_bytes(b"fixture")
    preferred = directory / "jina-embeddings-v5-text-nano-retrieval-q4.gguf"
    preferred.write_bytes(b"fixture")
    assert c._resolve_session_embeddings_model_path(str(isolated)) == str(preferred)
    monkeypatch.setenv(
        "FACTORY_SESSION_EMBEDDINGS_MODEL_PATH", str(isolated / "explicit.gguf")
    )
    assert c._resolve_session_embeddings_model_path(str(isolated)) == str(
        isolated / "explicit.gguf"
    )


class Process:
    def __init__(self, code=None):
        self.returncode = code
        self.stdout = io.StringIO("fixture output\n")
        self.terminated = False
        self.killed = False
        self.waits = []
        self.timeout_once = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.timeout_once:
            self.timeout_once = False
            raise subprocess.TimeoutExpired("fixture", timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class Thread:
    def __init__(self, **fields):
        self.fields = fields
        self.started = False

    def start(self):
        self.started = True


@pytest.fixture
def embeddings(isolated, monkeypatch):
    path = isolated / "model.gguf"
    path.write_bytes(b"fixture")
    monkeypatch.setenv("FACTORY_SESSION_EMBEDDINGS_MODEL_PATH", str(path))
    monkeypatch.setenv("FACTORY_SESSION_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setattr(c.threading, "Thread", Thread)
    process = Process()
    launch = Mock(return_value=process)
    monkeypatch.setattr(c.subprocess, "Popen", launch)
    return SimpleNamespace(
        path=path, process=process, launch=launch, register=Mock(), stream=Mock()
    )


def start_embeddings(env):
    return c._start_optional_session_embeddings_runtime(
        stream_output_fn=env.stream, register_process_fn=env.register, host="127.0.0.1"
    )


def test_existing_embeddings_service_is_reused_without_launching(
    embeddings, monkeypatch
):
    monkeypatch.setattr(c, "_wait_http_ready", lambda *_, **__: True)
    assert start_embeddings(embeddings) == (None, "http://127.0.0.1:8022")
    embeddings.launch.assert_not_called()
    embeddings.register.assert_not_called()


def test_embeddings_command_preserves_quoted_extra_argument(embeddings, monkeypatch):
    monkeypatch.setenv(
        "FACTORY_SESSION_EMBEDDINGS_EXTRA_ARGS", '--alias "fixture embeddings"'
    )
    readiness = iter([False, True])
    monkeypatch.setattr(c, "_wait_http_ready", lambda *_, **__: next(readiness))
    process, url = start_embeddings(embeddings)
    assert process is embeddings.process and url == "http://127.0.0.1:8022"
    command = embeddings.launch.call_args.args[0]
    assert (
        command[command.index("--alias") + 1] == "fixture embeddings"
        and '"fixture' not in command
    )
    embeddings.register.assert_called_once_with(process)


def test_embeddings_startup_failure_is_optional(embeddings, monkeypatch):
    monkeypatch.setattr(c, "_wait_http_ready", lambda *_, **__: False)
    embeddings.launch.side_effect = OSError("fixture no binary")
    assert start_embeddings(embeddings) == (None, None)
    embeddings.register.assert_not_called()


def test_embeddings_timeout_terminates_only_its_created_process(
    embeddings, monkeypatch
):
    monkeypatch.setattr(c, "_wait_http_ready", lambda *_, **__: False)
    assert start_embeddings(embeddings) == (None, None)
    assert embeddings.process.terminated and embeddings.process.waits == [5]


def test_embeddings_disabled_does_not_probe_http_or_model(monkeypatch):
    monkeypatch.setenv("FACTORY_SESSION_EMBEDDINGS_ENABLED", "off")
    probe = Mock()
    monkeypatch.setattr(c, "_wait_http_ready", probe)
    assert c._start_optional_session_embeddings_runtime(
        stream_output_fn=Mock(), register_process_fn=Mock(), host="127.0.0.1"
    ) == (None, None)
    probe.assert_not_called()


@pytest.fixture
def stack(isolated, monkeypatch):
    monkeypatch.setattr(c, "_bootstrap_stack_env", lambda **_: None)
    monkeypatch.setattr(c, "_recover_isolation_startup", lambda: None)
    monkeypatch.setattr(
        c, "_start_optional_session_embeddings_runtime", lambda **_: (None, None)
    )
    monkeypatch.setattr(
        c,
        "get_profile_config",
        lambda name: {"backend": "cc-backend", "model": "fixture"},
    )
    monkeypatch.setattr(c.threading, "Thread", Thread)
    signals = {}
    monkeypatch.setattr(
        c.signal,
        "signal",
        lambda number, callback: signals.__setitem__(number, callback),
    )
    processes = []
    commands = []

    def launch(command, **kwargs):
        process = Process()
        processes.append(process)
        commands.append((command, kwargs))
        return process

    monkeypatch.setattr(c.subprocess, "Popen", launch)

    def interrupt():
        signals[signal.SIGINT]()

    monkeypatch.setattr(c.signal, "pause", interrupt, raising=False)
    return SimpleNamespace(processes=processes, commands=commands, signals=signals)


def test_local_stack_starts_only_configured_workers_and_cleans_owned_children(
    stack, monkeypatch
):
    monkeypatch.setenv("FACTORY_WORKER_PROFILES", "extra")
    with pytest.raises(SystemExit) as result:
        c.run_local_stack(profile="active", api_port=18180)
    assert result.value.code == 0 and len(stack.processes) == 3
    assert stack.commands[0][0][-5:] == [
        "api",
        "--host",
        "127.0.0.1",
        "--port",
        "18180",
    ]
    assert [
        entry[0][entry[0].index("--profile") + 1] for entry in stack.commands[1:]
    ] == ["active", "extra"]
    assert all(
        process.terminated and process.waits == [5] for process in stack.processes
    )


def test_telegram_child_is_pinned_to_selected_profile_and_api_url(stack, monkeypatch):
    monkeypatch.setenv("FACTORY_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("FACTORY_TELEGRAM_BOT_TOKEN", "synthetic-token")
    monkeypatch.setenv("FACTORY_TELEGRAM_PROFILE", "stale")
    with pytest.raises(SystemExit):
        c.run_local_stack(profile="active", api_port=18180)
    command, options = stack.commands[-1]
    assert command[-3:] == ["telegram", "--profile", "active"]
    assert options["env"]["FACTORY_API_BASE_URL"] == "http://127.0.0.1:18180"


def test_gateway_explicit_data_dir_overrides_conflicting_env(
    stack, isolated, monkeypatch
):
    monkeypatch.setenv("FACTORY_DATA_DIR", str(isolated / "old"))
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(isolated / "old"))
    delegate = Mock(return_value=0)
    monkeypatch.setattr(c, "run_local_stack", delegate)
    assert c.run_gateway_stack(profile="active", data_dir=str(isolated / "chosen")) == 0
    assert os.environ["FACTORY_DATA_DIR"] == str(isolated / "chosen") and os.environ[
        "IGOREK_FACTORY_HOME"
    ] == str(isolated / "chosen")


def test_failed_child_triggers_stack_cleanup(stack, monkeypatch):
    calls = 0

    def launch(command, **kwargs):
        nonlocal calls
        calls += 1
        process = Process(7 if calls == 1 else None)
        stack.processes.append(process)
        return process

    monkeypatch.setattr(c.subprocess, "Popen", launch)
    with pytest.raises(SystemExit):
        c.run_local_stack(profile="active")
    assert not stack.processes[0].terminated and stack.processes[1].terminated


def test_invalid_active_profile_is_rejected_before_creating_any_child(
    stack, monkeypatch
):
    monkeypatch.setattr(
        c, "get_profile_config", Mock(side_effect=ValueError("fixture missing profile"))
    )
    with pytest.raises(ValueError, match="fixture missing profile"):
        c.run_local_stack(profile="missing")
    assert stack.processes == []


def test_failed_worker_launch_cleans_already_created_api(stack, monkeypatch):
    api = Process()
    calls = []

    def launch(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            stack.processes.append(api)
            return api
        raise OSError("fixture worker launch failed")

    monkeypatch.setattr(c.subprocess, "Popen", launch)
    with pytest.raises(OSError, match="fixture worker launch failed"):
        c.run_local_stack(profile="active")
    assert api.terminated and api.waits == [5]


def test_embeddings_timeout_kills_then_reaps_its_child(embeddings, monkeypatch):
    monkeypatch.setattr(c, "_wait_http_ready", lambda *_, **__: False)
    embeddings.process.timeout_once = True
    assert start_embeddings(embeddings) == (None, None)
    assert (
        embeddings.process.terminated
        and embeddings.process.killed
        and embeddings.process.waits == [5, 5]
    )


def test_stack_shutdown_kills_then_reaps_timed_out_children(stack, monkeypatch):
    def launch(command, **kwargs):
        process = Process()
        process.timeout_once = True
        stack.processes.append(process)
        return process

    monkeypatch.setattr(c.subprocess, "Popen", launch)
    with pytest.raises(SystemExit):
        c.run_local_stack(profile="active")
    assert all(
        process.terminated and process.killed and process.waits == [5, 5]
        for process in stack.processes
    )


def test_invalid_quoted_embedding_arguments_remain_optional(embeddings, monkeypatch):
    monkeypatch.setattr(c, "_wait_http_ready", lambda *_, **__: False)
    monkeypatch.setenv("FACTORY_SESSION_EMBEDDINGS_EXTRA_ARGS", '--alias "unfinished')
    assert start_embeddings(embeddings) == (None, None)
    embeddings.launch.assert_not_called()


def test_invalid_embeddings_port_is_optional(embeddings, monkeypatch):
    monkeypatch.setenv("FACTORY_SESSION_EMBEDDINGS_PORT", "not-a-port")
    assert start_embeddings(embeddings) == (None, None)
    embeddings.launch.assert_not_called()


def test_reader_thread_start_failure_cleans_its_already_created_child(
    stack, monkeypatch
):
    class BrokenThread(Thread):
        def start(self):
            raise RuntimeError("fixture reader failed")

    monkeypatch.setattr(c.threading, "Thread", BrokenThread)
    with pytest.raises(RuntimeError, match="fixture reader failed"):
        c.run_local_stack(profile="active")
    assert (
        len(stack.processes) == 1
        and stack.processes[0].terminated
        and stack.processes[0].waits == [5]
    )


def test_repeated_shutdown_signal_does_not_interrupt_cleanup(stack, monkeypatch):
    def launch(command, **kwargs):
        process = Process()

        def terminate():
            process.terminated = True
            stack.signals[signal.SIGTERM]()

        process.terminate = terminate
        stack.processes.append(process)
        return process

    monkeypatch.setattr(c.subprocess, "Popen", launch)
    with pytest.raises(SystemExit):
        c.run_local_stack(profile="active")
    assert all(
        process.terminated and process.waits == [5] for process in stack.processes
    )


@pytest.mark.parametrize(
    "platform,extra,expected",
    [
        ("win32", r"--log-file C:\Models\fixture.log", r"C:\Models\fixture.log"),
        (
            "win32",
            r'--log-file "C:\Model Files\fixture.log"',
            r"C:\Model Files\fixture.log",
        ),
        ("linux", r"--log-file fixture\ log.txt", "fixture log.txt"),
    ],
)
def test_embedding_extra_args_preserve_platform_path_syntax(
    embeddings, monkeypatch, platform, extra, expected
):
    monkeypatch.setattr(c.sys, "platform", platform)
    monkeypatch.setenv("FACTORY_SESSION_EMBEDDINGS_EXTRA_ARGS", extra)
    readiness = iter([False, True])
    monkeypatch.setattr(c, "_wait_http_ready", lambda *_, **__: next(readiness))
    assert start_embeddings(embeddings)[0] is embeddings.process
    command = embeddings.launch.call_args.args[0]
    assert command[command.index("--log-file") + 1] == expected


def test_stack_cleans_owned_embedding_child_with_api_and_workers(stack, monkeypatch):
    child = Process()

    def start(**kwargs):
        kwargs["register_process_fn"](child)
        return child, "http://127.0.0.1:1"

    monkeypatch.setattr(c, "_start_optional_session_embeddings_runtime", start)
    with pytest.raises(SystemExit):
        c.run_local_stack(profile="active")
    assert child.terminated and child.waits == [5]
    assert all(proc.terminated for proc in stack.processes)


def test_optional_embedding_exit_keeps_main_children_alive_until_user_shutdown(
    stack, monkeypatch, capsys
):
    child = Process(7)

    def start(**kwargs):
        kwargs["register_process_fn"](child)
        return child, "http://127.0.0.1:1"

    monkeypatch.setattr(c, "_start_optional_session_embeddings_runtime", start)

    def interrupt():
        assert all(not proc.terminated for proc in stack.processes)
        stack.signals[signal.SIGINT]()

    monkeypatch.setattr(c.signal, "pause", interrupt)
    with pytest.raises(SystemExit):
        c.run_local_stack(profile="active")
    assert not child.terminated and child.waits == [5]
    assert "continuing without embeddings" in capsys.readouterr().out
