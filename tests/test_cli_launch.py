"""Lifecycle acceptance for independent foreground clients and queue workers."""
import json
from contextlib import closing
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from factory.core import worker_presence as presence
from factory.core.cli_session_runtime import SessionRuntime
from factory.core.runtime import get_profile_config


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    profiles = root / "profiles"
    profiles.mkdir(parents=True)
    for name in ("one", "two"):
        (profiles / (name + ".json")).write_text(json.dumps({
            "backend": "cc-backend", "model": "fixture-model",
            "api_format": "openai",
            "base_url": "http://127.0.0.1:1", "filesystem_mode": "workspace",
        }))
    for key in ("IGOREK_FACTORY_HOME", "FACTORY_DATA_DIR"):
        monkeypatch.setenv(key, str(root))
    monkeypatch.setenv("FACTORY_PROFILES_DIR", str(profiles))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("FACTORY_ENV_FILES_DIR", str(tmp_path / "env"))
    monkeypatch.setenv("FACTORY_SESSION_EMBEDDINGS_ENABLED", "off")
    monkeypatch.setenv("FACTORY_TELEGRAM_ENABLED", "false")
    monkeypatch.setenv("FACTORY_VAULT_ENABLED", "false")
    monkeypatch.setenv("IGOREK_ISOLATION_MODE", "off")
    monkeypatch.setenv("MCP_SERVERS", "")
    for key in ("FACTORY_EXTERNAL_CODEX_HOME", "FACTORY_EXTERNAL_CLAUDE_HOME",
                "FACTORY_EXTERNAL_OPENCODE_DB", "FACTORY_WALLY_CREDENTIALS"):
        monkeypatch.setenv(key, str(tmp_path / "absent" / key))
    return root


def until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail("Expected runtime lifecycle transition did not occur")


def _stop_shared_runtime_when_idle(lifecycle):
    try:
        lifecycle("stop")
        return True
    except RuntimeError as exc:
        if str(exc) != "Shared runtime is busy; finish or cancel active tasks before stopping it":
            raise
        return False


def test_shared_runtime_stop_retries_the_busy_transition():
    lifecycle = Mock(side_effect=[RuntimeError("Shared runtime is busy; finish or cancel active tasks before stopping it"), {"stopping": True}])
    assert not _stop_shared_runtime_when_idle(lifecycle)
    assert _stop_shared_runtime_when_idle(lifecycle)
    assert lifecycle.call_args_list == [(("stop",),), (("stop",),)]


def test_shared_runtime_stop_does_not_hide_other_failures():
    lifecycle = Mock(side_effect=RuntimeError("unexpected fixture failure"))
    with pytest.raises(RuntimeError, match="unexpected fixture failure"):
        _stop_shared_runtime_when_idle(lifecycle)


def test_existing_consumer_is_reused_and_never_stopped(home):
    config = get_profile_config("one")
    with presence.WorkerPresence("one", config):
        with SessionRuntime() as runtime:
            runtime._start = Mock(side_effect=AssertionError("Duplicate worker"))
            runtime.select("one")
        assert presence.consumers("one", config)
    assert not presence.consumers("one", config)


def test_same_name_in_different_data_homes_is_independent(home, monkeypatch, tmp_path):
    config = get_profile_config("one")
    with presence.WorkerPresence("one", config):
        monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path / "other"))
        assert not presence.consumers("one", config)


def test_binding_mismatch_fails_without_starting_another_worker(home):
    config = get_profile_config("one")
    with presence.WorkerPresence("one", {**config, "model": "different"}):
        runtime = SessionRuntime()
        with pytest.raises(RuntimeError, match="different configuration"):
            runtime.select("one")
        assert not runtime.clients


def test_two_real_clients_share_worker_and_last_exit_stops_it(home):
    config = get_profile_config("one")
    first, second = SessionRuntime(), SessionRuntime()
    try:
        first.select("one")
        second.select("one")
        assert len(list((presence.profile_directory("one") / "workers").glob("*.json"))) == 1
        first.close()
        time.sleep(0.6)
        assert presence.consumers("one", config)
    finally:
        first.close()
        second.close()
    until(lambda: not presence.consumers("one", config))


def test_switch_releases_only_old_profile_and_retains_other_client(home):
    first, second = SessionRuntime(), SessionRuntime()
    try:
        first.select("one")
        second.select("one")
        first.select("two")
        assert presence.consumers("one", get_profile_config("one"))
        assert presence.consumers("two", get_profile_config("two"))
        first.close()
        until(lambda: not presence.consumers("two", get_profile_config("two")))
        assert presence.consumers("one", get_profile_config("one"))
    finally:
        first.close()
        second.close()
    until(lambda: not presence.consumers("one", get_profile_config("one")))


def test_command_help_has_no_runtime_side_effects(home):
    result = subprocess.run([sys.executable, "-m", "factory.cli_launch", "--help"],
                            text=True, capture_output=True, check=True)
    assert "--profile" in result.stdout
    assert not (home / "run").exists()


def test_missing_profiles_noninteractive_requires_setup(home):
    for path in (home / "profiles").glob("*.json"):
        path.unlink()
    result = subprocess.run([sys.executable, "-m", "factory.cli_launch"],
                            input="", text=True, capture_output=True)
    assert result.returncode == 2
    assert "igorek-factory init" in result.stderr


def test_profile_switch_updates_route_and_clears_old_permissions(home):
    from rich.console import Console
    from factory.core.chat_session import ChatSession
    from types import SimpleNamespace
    session = ChatSession.__new__(ChatSession)
    session.console = Console(file=open(os.devnull, "w"))
    session.profile = "one"
    session.session_name = "fixture-session"
    session.session_path = home / "chat.jsonl"
    session.cwd, session.branch = str(home), ""
    session.sl, session.widget = SimpleNamespace(), SimpleNamespace()
    session.on_profile_change = Mock()
    session.approved_session_rules = [{"old": True}]
    session.model_override = "old-model"
    try:
        session._cmd_profile("/profile two")
        session.on_profile_change.assert_called_once_with("two")
        assert session.profile == session.sl.profile == "two"
        assert session.pc["backend"] == "cc-backend"
        assert session.model_override is None and session.approved_session_rules == []
        session.on_profile_change.side_effect = RuntimeError("Unavailable")
        session._cmd_profile("/profile one")
        assert session.profile == "two"
    finally:
        session.console.file.close()


@pytest.mark.skipif(os.name != "posix", reason="Real POSIX terminal geometry acceptance")
@pytest.mark.parametrize("switch,shared,classic", [(True,False,False),(False,False,False),(True,True,False),(False,True,False),(True,False,True),(False,False,True),(True,True,True),(False,True,True),(True,False,"legacy")])
def test_real_terminal_switch_reply_ctrl_c_and_other_client_survival(home, switch, shared, classic):
    import fcntl
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from factory.core.cli_daemon import lifecycle
    import pty
    import select
    import sqlite3
    import struct
    import termios
    import threading

    requests = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            model = "fixture-two" if "/two/" in self.path else "fixture-one"
            body = {"data": [{"id": model}], "default_generation_settings": {"n_ctx": 32768}}
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if not switch:
                time.sleep(2)
            if body.get("stream"):
                raw = ('data: ' + json.dumps({"choices": [{"index": 0, "delta": {"content": "PHONE_FIXTURE_OK"}, "finish_reason": None}]})
                       + '\n\ndata: ' + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                       + '\n\ndata: [DONE]\n\n').encode()
                content_type = "text/event-stream"
            else:
                raw = json.dumps({"choices": [{"message": {"role": "assistant", "content": "PHONE_FIXTURE_OK"}, "finish_reason": "stop"}]}).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name in ("one", "two"):
        path = home / "profiles" / (name + ".json")
        config = json.loads(path.read_text())
        config["base_url"] = f"http://127.0.0.1:{server.server_port}/{name}/v1"
        config["model"] = "fixture-" + name
        path.write_text(json.dumps(config))
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 110, 0, 0))
    other = SessionRuntime()
    process = None
    transcript = bytearray()
    def pump(seconds=0.1):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    raw = os.read(master, 65536)
                except OSError:
                    return
                transcript.extend(raw)
                for _ in range(raw.count(b"\x1b[6n")):
                    os.write(master, b"\x1b[1;1R")
    def wait(predicate):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            pump()
            if predicate():
                return
            if process.poll() is not None:
                if predicate():
                    return
                pytest.fail("CLI exited unexpectedly: " + transcript.decode(errors="replace")[-5000:])
        pytest.fail("CLI terminal transition timed out: " + transcript.decode(errors="replace")[-5000:])
    try:
        if shared:
            lifecycle("start")
        other.select("one")
        launch = """import time
from factory.core import worker_presence
original = worker_presence.write_private
def slow_selection(path, value):
    original(path, value)
    if path.name == 'selection.json' and value.get('profile') == 'two':
        time.sleep(0.4)
worker_presence.write_private = slow_selection
from factory.cli_launch import main
raise SystemExit(main())
"""
        command = [sys.executable, "-c", launch, "--profile", "one"]
        if classic:
            command.append("--no-alt-screen")
        process = subprocess.Popen(command,
                                   stdin=slave, stdout=slave, stderr=slave,
                                   cwd=home,
                                   env={**os.environ, "TERM": "xterm-256color",
                                        "IGOREK_TUI_CLASSIC_STYLE": "legacy" if classic == "legacy" else "modern",
                                        "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
        os.close(slave)
        wait(lambda: bool(list((home / "chat_sessions").glob("*.jsonl"))))
        wait(lambda: not (termios.tcgetattr(master)[3] & termios.ICANON))
        pump(0.1)
        if switch:
            os.write(master, b"/profile two\r")
            wait(lambda: json.loads((presence.profile_directory("one").parent / "selection.json").read_text()).get("profile") == "two")
        os.write(master, b"Say hello\r")
        if switch:
            wait(lambda: any('"role": "assistant"' in p.read_text() and "PHONE_FIXTURE_OK" in p.read_text()
                             for p in (home / "chat_sessions").glob("*.jsonl")))
        else:
            wait(lambda: bool(requests))
        assert requests[-1]["model"] == ("fixture-two" if switch else "fixture-one")
        os.write(master, b"\x03")
        wait(lambda: process.poll() is not None)
        assert process.returncode == 0
        if classic:
            assert b"\x1b[?1049h" not in transcript
            assert b"\x1b[?1000h" not in transcript
        if switch and not shared:
            until(lambda: not presence.consumers("two", get_profile_config("two")))
        assert presence.consumers("one", get_profile_config("one"))
        with closing(sqlite3.connect(home / "database/factory.db")) as connection:
            assert connection.execute("SELECT DISTINCT profile FROM tasks").fetchall() == [("two" if switch else "one",)]
        if not switch:
            from factory.core.cli_tasks import create_task, get_task
            created = create_task("Second client remains usable", profile="one")
            until(lambda: get_task(created["task_id"])["status"] == "ok")
            assert "PHONE_FIXTURE_OK" in get_task(created["task_id"])["output_text"]
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        os.close(master)
        other.close()
        if shared:
            until(lambda: not any(_active_tasks(home)))

            until(lambda: _stop_shared_runtime_when_idle(lifecycle))
        server.shutdown()
        server.server_close()
    until(lambda: not presence.consumers("one", get_profile_config("one")))


def _active_tasks(home):
    import sqlite3
    with closing(sqlite3.connect(home / "database/factory.db")) as connection:
        return connection.execute("SELECT task_id FROM tasks WHERE status IN ('processing','waiting_approval','cancelling')").fetchall()


@pytest.mark.skipif(os.name != "posix", reason="Owner-local POSIX daemon acceptance")
def test_daemon_lifecycle_survives_client_and_is_idempotent(home):
    from factory.core.cli_daemon import lifecycle
    assert not lifecycle("version")["running"]
    try:
        first = lifecycle("start")
        assert first["running"] and first["scheduler"]
        assert lifecycle("start")["running"]
        with SessionRuntime() as runtime:
            runtime.select("one")
            assert runtime.shared
        assert lifecycle("version")["running"]
        assert presence.consumers("one", get_profile_config("one"))
    finally:
        lifecycle("stop")
    assert not lifecycle("version")["running"]


def test_scheduler_has_one_owner_per_database(home):
    from factory.core.scheduler import TaskScheduler
    path = str(home / "database/factory.db")
    first, second = TaskScheduler(path), TaskScheduler(path)
    try:
        first.start()
        second.start()
        assert first.running and not second.running
        second.stop()
        assert first.running
        first.stop()
        second.start()
        assert second.running
    finally:
        first.stop()
        second.stop()


def test_classic_header_is_compact_and_width_safe():
    import io
    from rich.console import Console
    from factory.core.cli_classic import header
    for width in (28, 80, 120):
        stream = io.StringIO()
        header(Console(file=stream, width=width), profile="phone", model="fixture-model-long-name",
               version="0.2.4", cwd="/example/a/long/workspace/path")
        lines = stream.getvalue().splitlines()
        assert "Игорёк" in lines[0]
        assert len(lines) <= 8
        assert all(len(line) <= width for line in lines)


def test_daemon_stop_refuses_busy_worker_and_releases_acquired_locks(home):
    import threading
    from factory.core.cli_daemon import Daemon
    daemon = Daemon()
    idle, busy = threading.Lock(), threading.Lock()
    daemon.execution_locks = {"one": idle, "two": busy}
    busy.acquire()
    try:
        with pytest.raises(RuntimeError, match="busy"):
            daemon.dispatch({"id": daemon.identity, "action": "stop"})
        assert not daemon.stop.is_set()
        assert idle.acquire(blocking=False)
        idle.release()
    finally:
        busy.release()


def test_daemon_profile_workers_serialize_process_local_environment(home, monkeypatch):
    import threading
    from factory.core.cli_daemon import Daemon
    from factory.core.worker import _temporary_env
    ready, threads, values = set(), [], []
    entered, release = threading.Event(), threading.Event()
    daemon = Daemon()
    class FixtureWorker:
        def __init__(self, profile, **_):
            self.profile = profile
        def run(self, *, stop_event, execution_lock):
            ready.add(self.profile)
            threads.append(threading.current_thread())
            with execution_lock:
                with _temporary_env({"IGOREK_FIXTURE_PROFILE_ENV": self.profile}):
                    if self.profile == "one":
                        entered.set()
                        release.wait(timeout=5)
                    values.append(os.environ["IGOREK_FIXTURE_PROFILE_ENV"])
    monkeypatch.setattr("factory.core.worker.Worker", FixtureWorker)
    monkeypatch.setattr("factory.core.cli_daemon.consumers", lambda profile, _: profile in ready)
    try:
        daemon.ensure("one")
        assert entered.wait(timeout=5)
        daemon.ensure("two")
        assert values == []
        assert daemon.execution_locks["one"] is daemon.execution_locks["two"]
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)
    assert values == ["one", "two"]
    assert "IGOREK_FIXTURE_PROFILE_ENV" not in os.environ
    from factory.core.schema import initialize_database
    initialize_database(str(home / "database/factory.db"))
    daemon.dispatch({"id": daemon.identity, "action": "stop"})
    assert daemon.stop.is_set()


@pytest.mark.skipif(os.name != "posix", reason="Owner-local POSIX scheduled-task acceptance")
def test_daemon_executes_schedule_without_any_cli_client(home, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from factory.core.cli_daemon import lifecycle
    from factory.core.scheduler import TaskScheduler
    import sqlite3
    import threading
    calls = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            raw = json.dumps({"data": [{"id": "fixture-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            raw = ('data: ' + json.dumps({"choices": [{"index": 0, "delta": {"content": "SCHEDULE_FIXTURE_OK"}, "finish_reason": None}]})
                   + '\n\ndata: ' + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                   + '\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    path = home / "profiles/one.json"
    config = json.loads(path.read_text())
    config["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
    path.write_text(json.dumps(config))
    from factory.core import cli_daemon
    assert Path(cli_daemon.get_data_root()).resolve() == home.resolve()
    owned_processes = []
    real_popen = subprocess.Popen

    def owned_popen(argv, *args, **kwargs):
        process = real_popen(argv, *args, **kwargs)
        if list(argv[:3]) == [sys.executable, "-m", "factory.core.cli_daemon"]:
            owned_processes.append(process)
        return process

    monkeypatch.setattr(cli_daemon.subprocess, "Popen", owned_popen)
    try:
        lifecycle("start")
        scheduler = TaskScheduler(str(home / "database/factory.db"))
        identity = scheduler.create_schedule("Fixture", "one", "Reply with a short answer",
                                             schedule_type="hourly", time_value="00:01", tz="UTC")
        with closing(sqlite3.connect(home / "database/factory.db")) as connection:
            connection.execute("UPDATE schedules SET next_run_at=? WHERE schedule_id=?", (int(time.time()) - 1, identity))
            connection.commit()
        until(lambda: "SCHEDULE_FIXTURE_OK" in str(scheduler.get_schedule(identity).last_output or ""), timeout=40)
        assert len(calls) == 1
        assert lifecycle("version")["running"]
    finally:
        try:
            until(lambda: not _active_tasks(home))

            def stop_when_idle():
                try:
                    lifecycle("stop")
                except RuntimeError as error:
                    if str(error) != "Shared runtime is busy; finish or cancel active tasks before stopping it":
                        raise
                    return False
                return True

            until(stop_when_idle)
        finally:
            for process in owned_processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            server.shutdown()
            server.server_close()
