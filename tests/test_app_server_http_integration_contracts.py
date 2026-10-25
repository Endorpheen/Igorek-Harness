"""Real HTTP, runtime, approval database and owned process pipes work together."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from factory import factory_api as api
from factory.core import codex_app_server_adapter as adapter
from factory.core import codex_app_server_runtime as managed
from test_app_server_runtime_contracts import runtime as runtime
from test_factory_api_contracts import server as server


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Owned HTTP/process path did not finish"
        time.sleep(0.01)


@pytest.mark.parametrize("mode", ["normal", "error", "exit"])
def test_http_session_uses_real_managed_runtime_and_owned_peer(
    server, runtime, monkeypatch, tmp_path, mode
):
    monkeypatch.setattr(managed, "CodexAppServerSession", adapter.CodexAppServerSession)
    monkeypatch.setattr(api, "APP_SERVER_RUNTIME", runtime.manager)
    original_popen = subprocess.Popen
    peer = Path(__file__).with_name("native_app_server_peer.py")
    wire = tmp_path / "http-peer.jsonl"
    processes = []

    def spawn(argv, **kwargs):
        assert argv == ["codex", "app-server", "--listen", "stdio://"]
        process = original_popen(
            [sys.executable, "-u", str(peer), mode, str(wire)], **kwargs
        )
        processes.append(process)
        return process

    monkeypatch.setattr(adapter.subprocess, "Popen", spawn)
    session_id = None
    try:
        code, created, _ = server.request(
            "POST",
            "/app-server/sessions",
            {
                "profile": "fixture",
                "cwd": str(tmp_path),
                "chat_session": "fixture-chat",
            },
        )
        assert code == 201 and created["thread_id"] == "fixture-thread"
        session_id = created["session_id"]
        prefix = "/app-server/sessions/" + session_id
        assert server.request("GET", prefix)[1]["pending_requests"] == []
        code, result, _ = server.request(
            "POST", prefix + "/messages", {"text": "Привет 🐙"}
        )
        if mode == "normal":
            assert code == 202 and result == {"ok": True, "thread_id": "fixture-thread"}
            pending = server.request("GET", prefix)[1]["pending_requests"]
            assert len(pending) == 1 and pending[0]["request_id"] == "fixture-approval"
            approval_id = pending[0]["security_approval_id"]
            assert (
                runtime.bridge.store.list_approvals(session_id=session_id, limit=10)[0][
                    "status"
                ]
                == "pending"
            )
            assert not any(
                '"decline"' in line
                for line in wire.read_text(encoding="utf-8").splitlines()
            )
            code, decision, _ = server.request(
                "POST",
                prefix + "/approvals/fixture-approval",
                {"action": "reject", "remember_for_session": False},
            )
            assert code == 200 and decision["ok"]
            wait_for(lambda: '"decline"' in wire.read_text(encoding="utf-8"))
            assert (
                runtime.bridge.store.list_approvals(session_id=session_id, limit=10)[0][
                    "status"
                ]
                == "rejected"
            )
            assert decision["security_approval_id"] == approval_id
            assert server.request("GET", prefix)[1]["pending_requests"] == []
            events = server.request("GET", prefix + "/events?after=0&limit=100")[1][
                "events"
            ]
            assert any(row["kind"] == "client_resolution" for row in events)
        else:
            assert code == 500 and "error" in result
            assert ("fixture refused" if mode == "error" else "closed") in result[
                "error"
            ]
        assert server.request("DELETE", prefix)[0] == 200
        assert server.request("GET", prefix)[0] == 404
        assert all(process.poll() is not None for process in processes)
        coverage_file = os.environ.get("COVERAGE_FILE")
        if coverage_file:
            artifact = (
                Path(coverage_file).parent
                / (
                    Path(coverage_file).name.removeprefix(".coverage.")
                    + "-http-artifacts"
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
                        "actual_loopback_http": True,
                        "actual_managed_runtime": True,
                        "temporary_approval_sqlite": True,
                        "owned_python_peer_pids": [
                            process.pid for process in processes
                        ],
                        "owned_peer_exit_codes": [
                            process.returncode for process in processes
                        ],
                        "session_removed": True,
                        "real_codex_or_model_launched": False,
                    },
                    indent=2,
                )
                + "\n"
            )
    finally:
        if session_id in runtime.manager._sessions:
            runtime.manager.close_session(session_id)
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
