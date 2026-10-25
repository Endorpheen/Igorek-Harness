"""Run the enrolled adapter entrypoint against isolated ASGI requests."""

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="The enrolled service runner uses POSIX Unix sockets"
)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "desktop/host-service-runner.py"
    spec = importlib.util.spec_from_file_location("fixture_host_service_runner", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    control_root = tmp_path / "control"
    control_root.mkdir()
    app_root = tmp_path / "app"
    app_root.mkdir()
    adapter = control_root / "adapter.py"
    adapter.write_text(
        "def build(config):\n    import fixture_host_adapter_state\n    return fixture_host_adapter_state.build(config)\n"
    )
    policy = source.parents[1] / "factory/core/host_service_policy.py"
    (control_root / "policy.py").write_bytes(policy.read_bytes())
    config = {
        "environment": {"PATH": "/usr/bin:/bin", "FIXTURE_ALLOWED": "yes"},
        "app_root": str(app_root),
        "python_paths": [str(app_root / "first"), str(app_root / "second")],
        "bootstrap": str(adapter),
        "bootstrap_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
        "socket": str(tmp_path / "socket-dir/api.sock"),
        "token": "synthetic-runner-token",
        "idle_seconds": 30,
        "tools": {
            "generate": {
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "seconds": {"type": "integer", "minimum": 1, "maximum": 5}
                    },
                    "required": ["seconds"],
                }
            }
        },
    }
    control = control_root / "control.json"
    state = SimpleNamespace(
        module=module,
        config=config,
        control=control,
        adapter=adapter,
        sent=[],
        seen=[],
        busy=lambda: False,
        policy_checks=[],
        idle_samples=[],
        thread_targets=[],
        server=None,
        run=None,
    )

    async def app(scope, receive, send):
        state.seen.append(scope)
        if scope["type"] == "http":
            state.delivered = [await receive(), await receive()]
            if getattr(state, "app_hook", None):
                state.app_hook()
            if getattr(state, "app_error", None):
                raise state.app_error
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"fixture result"})

    def build(actual):
        state.built_config = actual
        return {
            "server": SimpleNamespace(streamable_http_app=lambda: app),
            "busy": lambda: state.busy(),
        }

    monkeypatch.setitem(
        sys.modules, "fixture_host_adapter_state", SimpleNamespace(build=build)
    )

    class Server:
        def __init__(self, config):
            self.config = config
            self.should_exit = False
            self.started = True
            state.server = self

        def run(self, *, sockets):
            state.socket_mode = stat.S_IMODE(
                Path(state.config["socket"]).stat().st_mode
            )
            state.socket_name = sockets[0].getsockname()
            try:
                if state.run:
                    state.run(self.config.app)
            finally:
                for sock in sockets:
                    sock.close()

    class Thread:
        def __init__(self, target, daemon):
            assert daemon
            state.thread_targets.append(target)

        def start(self):
            pass

    def server_config(app, **kwargs):
        state.uvicorn_options = kwargs
        return SimpleNamespace(app=app)

    monkeypatch.setitem(
        sys.modules, "uvicorn", SimpleNamespace(Server=Server, Config=server_config)
    )
    monkeypatch.setattr(module.threading, "Thread", Thread)
    monkeypatch.setattr(sys, "argv", [str(source), str(control)])
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.chdir(tmp_path)

    def start(run=None):
        state.run = run
        control.write_text(json.dumps(config))
        original_load = module.load

        def load(path, name):
            loaded = original_load(path, name)
            if name == "igorek_service_policy":
                original_validate = loaded.validate_rpc

                def validate(message, tools):
                    state.policy_checks.append(message)
                    return original_validate(message, tools)

                loaded.validate_rpc = validate
                original_idle = loaded.IdleShutdown

                class Idle(original_idle):
                    def expired(self, busy):
                        state.idle_samples.append(busy)
                        return super().expired(busy)

                loaded.IdleShutdown = Idle
                state.idle = None
                state.policy = loaded
            return loaded

        with patch.dict(os.environ, {"FIXTURE_PRIVATE": "must-not-leak"}):
            with patch.object(module, "load", load):
                module.main()
                state.environment = dict(os.environ)
                state.cwd = Path.cwd()
                state.paths = list(sys.path)
        return state

    state.start = start
    yield state


def request(
    runner,
    app,
    *,
    method="POST",
    path="/mcp",
    token="synthetic-runner-token",
    body=None,
    frames=None,
    kind="http",
):
    if body is None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    frames = (
        list(frames)
        if frames is not None
        else [{"type": "http.request", "body": body, "more_body": False}]
    )
    count = 0

    async def receive():
        nonlocal count
        count += 1
        return frames.pop(0) if frames else {"type": "http.disconnect"}

    async def send(message):
        runner.sent.append(message)

    scope = {
        "type": kind,
        "method": method,
        "path": path,
        "headers": [(b"authorization", ("Bearer " + token).encode())],
    }
    try:
        asyncio.run(app(scope, receive, send))
    finally:
        runner.receive_calls = count


def status(runner):
    return next(
        row["status"] for row in runner.sent if row["type"] == "http.response.start"
    )


def test_bootstrap_is_verified_and_environment_is_explicit(runner):
    runner.start(lambda app: request(runner, app))
    assert runner.built_config == runner.config
    assert runner.environment == {
        **runner.config["environment"],
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
    }
    assert runner.cwd == Path(runner.config["app_root"])
    assert runner.paths[:2] == runner.config["python_paths"]
    assert runner.socket_mode == 0o600
    assert runner.socket_name == runner.config["socket"]
    assert runner.uvicorn_options == {
        "lifespan": "on",
        "log_level": "warning",
        "limit_concurrency": 8,
    }
    assert len(runner.thread_targets) == 1 and status(runner) == 200


def test_changed_adapter_is_rejected_before_build_or_socket(runner):
    runner.adapter.write_text(
        "raise AssertionError('must not execute changed adapter')\n"
    )
    with pytest.raises(RuntimeError, match="service_adapter_changed"):
        runner.start()
    assert not hasattr(runner, "built_config")
    assert not Path(runner.config["socket"]).exists()
    assert runner.server is None


@pytest.mark.parametrize("token", ["", "wrong-token", "synthetic-runner-token-extra"])
def test_unauthorized_request_does_not_read_body_or_call_adapter(runner, token):
    runner.start(lambda app: request(runner, app, token=token))
    assert status(runner) == 401
    assert runner.receive_calls == 0
    assert not runner.seen and not runner.policy_checks
    assert runner.sent[-1]["body"] == b"Unauthorized"


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/mcp"), ("POST", "/other"), ("DELETE", "/mcp/"), ("POST", "/mcp/other")],
)
def test_only_enrolled_post_route_is_forwarded(runner, method, path):
    runner.start(lambda app: request(runner, app, method=method, path=path))
    assert status(runner) == 403 and runner.receive_calls == 0
    assert not runner.seen and not runner.policy_checks


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"[]",
        b"null",
        b'{"jsonrpc":"2.0","method":"resources/read"}',
        b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"unknown"}}',
        b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"generate","arguments":{"seconds":true}}}',
    ],
)
def test_invalid_rpc_is_denied_without_adapter_call(runner, body):
    runner.start(lambda app: request(runner, app, body=body))
    assert status(runner) == 403
    assert runner.sent[-1]["body"] == b'{"error":"service_policy_denied"}'
    assert not runner.seen


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_chunked_valid_rpc_is_replayed_once_with_disconnect(runner, path):
    body = b'{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"generate","arguments":{"seconds":3}}}'
    frames = [
        {"type": "http.request", "body": body[:33], "more_body": True},
        {"type": "http.request", "body": body[33:], "more_body": False},
    ]
    runner.start(lambda app: request(runner, app, path=path, frames=frames))
    assert status(runner) == 200
    assert runner.policy_checks == [json.loads(body)]
    assert len(runner.seen) == 1
    assert runner.delivered == [
        {"type": "http.request", "body": body, "more_body": False},
        {"type": "http.disconnect"},
    ]
    assert runner.receive_calls == 3


def test_client_disconnect_does_not_deliver_partial_request(runner):
    runner.start(
        lambda app: request(
            runner,
            app,
            frames=[
                {"type": "http.request", "body": b"{", "more_body": True},
                {"type": "http.disconnect"},
            ],
        )
    )
    assert not runner.sent and not runner.seen and not runner.policy_checks
    assert runner.receive_calls == 2


def test_oversized_body_is_rejected_before_parse(runner):
    runner.start(lambda app: request(runner, app, body=b"x" * (1024 * 1024 + 1)))
    assert status(runner) == 413
    assert runner.sent[-1]["body"] == b"Request too large"
    assert not runner.policy_checks and not runner.seen


def test_lifespan_is_forwarded_without_http_auth(runner):
    runner.start(lambda app: request(runner, app, kind="lifespan", token="wrong"))
    assert [scope["type"] for scope in runner.seen] == ["lifespan"]
    assert runner.receive_calls == 0 and not runner.sent


@pytest.mark.parametrize("busy_mode", ["idle", "busy", "busy_error", "starting"])
def test_auto_stop_keeps_starting_and_busy_services_alive(
    runner, monkeypatch, busy_mode
):
    def run(app):
        if busy_mode == "starting":
            runner.server.started = False
        runner.busy = lambda: busy_mode == "busy"
        if busy_mode == "busy_error":

            def failed():
                raise RuntimeError("fixture busy state unavailable")

            runner.busy = failed
        calls = 0

        def sleep(seconds):
            nonlocal calls
            assert seconds == 2
            calls += 1
            if calls == 1:
                runner.policy.IdleShutdown.expired = lambda self, active: (
                    runner.idle_samples.append(active) or (busy_mode == "idle")
                )
            else:
                runner.server.should_exit = True

        monkeypatch.setattr(runner.module.time, "sleep", sleep)
        runner.thread_targets[0]()
        assert runner.server.should_exit
        assert calls == (1 if busy_mode == "idle" else 2)

    runner.start(run)
    if busy_mode == "starting":
        assert runner.idle_samples == []
    else:
        assert runner.idle_samples[0] == (busy_mode in {"busy", "busy_error"})


@pytest.mark.parametrize("app_fails", [False, True])
def test_inflight_generation_prevents_idle_exit_and_releases_after_return(
    runner, monkeypatch, app_fails
):
    error = RuntimeError("fixture adapter failed")
    if app_fails:
        runner.app_error = error

    def run(app):
        active_samples = []
        runner.policy.IdleShutdown.expired = lambda self, active: (
            active_samples.append(active) or not active
        )
        sleeps = 0

        def sleep(seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps % 2 == 0:
                runner.server.should_exit = True

        monkeypatch.setattr(runner.module.time, "sleep", sleep)
        runner.busy = lambda: False
        runner.app_hook = runner.thread_targets[0]
        body = b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"generate","arguments":{"seconds":1}}}'
        if app_fails:
            with pytest.raises(RuntimeError, match="fixture adapter failed") as raised:
                request(runner, app, body=body)
            assert raised.value is error
        else:
            request(runner, app, body=body)
        assert active_samples == [True, True]
        runner.server.should_exit = False
        sleeps = 0
        active_samples.clear()
        runner.thread_targets[0]()
        assert active_samples == [False]
        assert sleeps == 1 and runner.server.should_exit

    runner.start(run)
