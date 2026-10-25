"""Actual process pipes exercise the adapter, using only an owned Python peer."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from factory.core import codex_app_server_adapter as a


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, (
            "Owned protocol peer did not reach expected state"
        )
        time.sleep(0.01)


@pytest.mark.parametrize(
    "mode", ["normal", "resume", "error", "malformed", "exit", "ignore"]
)
def test_owned_process_protocol_lifecycle(tmp_path, monkeypatch, mode):
    original_popen = subprocess.Popen
    peer = Path(__file__).with_name("native_app_server_peer.py")
    wire = tmp_path / "wire.jsonl"
    processes = []

    def spawn(argv, **kwargs):
        assert argv == ["codex", "app-server", "--listen", "stdio://"]
        process = original_popen(
            [sys.executable, "-u", str(peer), mode, str(wire)], **kwargs
        )
        processes.append(process)
        return process

    monkeypatch.setattr(a.subprocess, "Popen", spawn)
    notifications = []
    requests = []
    errors = []
    approval_seen = threading.Event()

    def server_request(method, request_id, params):
        requests.append((method, request_id, params))
        approval_seen.set()

    session = a.CodexAppServerSession(
        cwd=str(tmp_path),
        model="fixture-model",
        model_provider=None,
        env={"FIXTURE_FLAG": "yes"},
        developer_instructions="Fixture only",
        notification_handler=lambda method, params: notifications.append(
            (method, params)
        ),
        server_request_handler=server_request,
        error_handler=errors.append,
    )
    original_request = session.request

    def bounded(method, params, timeout_s=30):
        return original_request(method, params, timeout_s=min(timeout_s, 2))

    monkeypatch.setattr(session, "request", bounded)
    outcomes = []
    waiter = None
    try:
        session.start(thread_id="previous-thread" if mode == "resume" else None)
        assert session.thread_id == (
            "previous-thread" if mode == "resume" else "fixture-thread"
        )
        if mode == "error":
            with pytest.raises(RuntimeError, match="fixture refused"):
                session.send_user_message("Привет 🐙")
        elif mode == "exit":
            with pytest.raises(RuntimeError, match="closed"):
                session.send_user_message("Привет 🐙")
        elif mode == "ignore":

            def ask():
                try:
                    session.send_user_message("Привет 🐙")
                except Exception as error:
                    outcomes.append(error)

            waiter = threading.Thread(target=ask)
            waiter.start()
            wait_for(
                lambda: (
                    wire.exists() and '"turn/start"' in wire.read_text(encoding="utf-8")
                )
            )
            session.close()
            waiter.join(timeout=1)
            assert not waiter.is_alive(), (
                "Closing a session did not release its pending caller"
            )
            assert len(outcomes) == 1 and isinstance(outcomes[0], RuntimeError)
            assert "closed" in str(outcomes[0])
        else:
            result = session.send_user_message("Привет 🐙")
            assert result == {"turn": {"id": "fixture-turn"}}
            assert approval_seen.wait(timeout=2)
            assert notifications == [("fixture/notice", {"text": "Привет 🐙"})]
            assert requests[0][0:2] == (
                "item/commandExecution/requestApproval",
                "fixture-approval",
            )
            session.resolve_server_request(
                request_id="fixture-approval",
                method=requests[0][0],
                params=requests[0][2],
                remember_for_session=False,
                approved=False,
            )
            wait_for(lambda: '"decline"' in wire.read_text(encoding="utf-8"))
            if mode == "malformed":
                assert any("non-JSON" in error for error in errors)
                assert any("non-object" in error for error in errors)
        assert session._pending == {}
        session.close()
        assert all(process.poll() is not None for process in processes)
        sent = [
            json.loads(line) for line in wire.read_text(encoding="utf-8").splitlines()
        ]
        assert sent[0]["method"] == "initialize"
        turn = next(item for item in sent if item.get("method") == "turn/start")
        assert turn["params"]["input"][0]["text"] == "Привет 🐙"
        assert session._proc is None
        coverage_file = os.environ.get("COVERAGE_FILE")
        if coverage_file:
            artifact = (
                Path(coverage_file).parent
                / (
                    Path(coverage_file).name.removeprefix(".coverage.")
                    + "-native-artifacts"
                )
                / mode
            )
            artifact.mkdir(parents=True)
            (artifact / "wire.jsonl").write_text(
                wire.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (artifact / "acceptance.json").write_text(
                json.dumps(
                    {
                        "mode": mode,
                        "owned_python_peer_pids": [
                            process.pid for process in processes
                        ],
                        "owned_peer_exit_codes": [
                            process.returncode for process in processes
                        ],
                        "pending_requests_empty": True,
                        "session_closed": True,
                        "real_codex_or_model_launched": False,
                    },
                    indent=2,
                )
                + "\n"
            )
    finally:
        session.close()
        if waiter:
            waiter.join(timeout=3)
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX C locale encoding contract")
def test_ascii_parent_locale_keeps_utf8_protocol_in_both_directions(tmp_path):
    env = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
    }
    probe = Path(__file__).with_name("native_app_server_ascii_client.py")
    result = subprocess.run(
        [sys.executable, str(probe), str(tmp_path / "ascii")],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["parent_encoding"] == "ascii"
    assert report["exception"] is None
    assert report["result"] == {"turn": {"id": "fixture-turn"}}
    assert report["notices"] == [["fixture/notice", {"text": "Привет 🐙"}]]
    assert report["pending_empty"] and all(
        code is not None for code in report["owned_exit_codes"]
    )
    coverage_file = os.environ.get("COVERAGE_FILE")
    if coverage_file:
        artifact = (
            Path(coverage_file).parent
            / (
                Path(coverage_file).name.removeprefix(".coverage.")
                + "-native-artifacts"
            )
            / "ascii-locale"
        )
        artifact.mkdir(parents=True)
        (artifact / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
