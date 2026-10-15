from __future__ import annotations

import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

from factory.core import sudo_broker


def _wait_for_request() -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pending = sudo_broker.pending_requests()
        if pending:
            return pending[0]
        time.sleep(0.02)
    raise AssertionError("sudo request was not published")


def test_sudo_password_is_one_shot_and_absent_from_database(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sudo_broker, "get_data_root", lambda: tmp_path)
    seen: list[tuple[list[str], str | None]] = []

    def fake_run(command, **kwargs):
        seen.append((list(command), kwargs.get("input")))
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr(sudo_broker.subprocess, "run", fake_run)
    result: list[int] = []
    runner = threading.Thread(
        target=lambda: result.append(sudo_broker.run_request(["/usr/bin/id", "-u"], cwd=str(tmp_path)))
    )
    runner.start()
    request = _wait_for_request()
    assert request["target"] == "/usr/bin/id -u"
    secret = "one-time-example-secret"
    assert sudo_broker.deliver_decision(request["request_id"], "allow_once", secret)["status"] == "delivered"
    runner.join(timeout=5)
    assert not runner.is_alive()
    assert result == [0]
    assert seen[1] == (["sudo", "-S", "-p", "", "--", "/usr/bin/id", "-u"], secret + "\n")
    assert sudo_broker.pending_requests() == []
    with pytest.raises(sudo_broker.SudoRequestError):
        sudo_broker.deliver_decision(request["request_id"], "allow_once", secret)
    database = tmp_path / "sudo_requests.sqlite3"
    assert secret.encode() not in database.read_bytes()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status, returncode FROM requests").fetchone() == ("completed", 0)


def test_sudo_reject_never_executes_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sudo_broker, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(
        sudo_broker.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("sudo executed after rejection"),
    )
    result: list[int] = []
    runner = threading.Thread(
        target=lambda: result.append(sudo_broker.run_request(["/usr/bin/true"], cwd=str(tmp_path)))
    )
    runner.start()
    request = _wait_for_request()
    sudo_broker.deliver_decision(request["request_id"], "reject")
    runner.join(timeout=5)
    assert not runner.is_alive()
    assert result == [1]
    assert sudo_broker.pending_requests() == []


def test_sudo_expired_request_cannot_receive_password(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sudo_broker, "get_data_root", lambda: tmp_path)
    request_id = sudo_broker.create_request(["/usr/bin/true"], str(tmp_path), str(tmp_path / "missing.sock"))
    with sqlite3.connect(tmp_path / "sudo_requests.sqlite3") as connection:
        connection.execute("UPDATE requests SET expires_at = 1 WHERE request_id = ?", (request_id,))
    assert sudo_broker.pending_requests() == []
    with pytest.raises(sudo_broker.SudoRequestError):
        sudo_broker.deliver_decision(request_id, "allow_once", "secret")


def test_bash_runs_approved_sudo_request_inside_guest(tmp_path: Path, monkeypatch) -> None:
    from factory.core import cc_backend_tools
    from factory.core.isolation import guest_bwrap

    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(list(command))
        assert kwargs["cwd"] == str(tmp_path)
        return subprocess.CompletedProcess(command, 0, "0\n", "")

    monkeypatch.setattr(guest_bwrap, "guest_mode_requested", lambda: True)
    monkeypatch.setattr(cc_backend_tools.subprocess, "run", fake_run)
    result = cc_backend_tools._tool_bash(
        {"command": "igorek-sudo -- /usr/bin/id -u", "timeout": 120000},
        str(tmp_path),
    )

    assert result.strip() == "0"
    assert seen == [["/usr/bin/sudo", "-n", "--", "/usr/bin/id", "-u"]]


def test_bash_routes_only_exact_sudo_request_outside_sandbox(tmp_path: Path, monkeypatch) -> None:
    from factory.core import cc_backend_tools

    assert cc_backend_tools._sudo_broker_command("igorek-sudo -- /usr/bin/id -u") == ["/usr/bin/id", "-u"]
    assert cc_backend_tools._sudo_broker_command("echo igorek-sudo -- /usr/bin/id") is None
    assert cc_backend_tools._sudo_broker_command("igorek-sudo --") is None

    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(list(command))
        return subprocess.CompletedProcess(command, 0, "approved\n", "")

    monkeypatch.setattr(cc_backend_tools.subprocess, "run", fake_run)
    monkeypatch.setattr(
        cc_backend_tools,
        "_bash_scope_denial",
        lambda *args, **kwargs: pytest.fail("sudo request entered the sandbox guard"),
    )
    result = cc_backend_tools._tool_bash(
        {"command": "igorek-sudo -- /usr/bin/id -u", "timeout": 120000},
        str(tmp_path),
    )
    assert result.strip() == "approved"
    assert seen[0][-3:] == ["--", "/usr/bin/id", "-u"]
