"""Real PTY acceptance for the persistent multi-turn terminal application."""

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import coverage
import pytest

from native_terminal_support import wait_for_terminal


@pytest.mark.skipif(
    os.name != "posix", reason="Real POSIX PTY, window-size and SGR mouse acceptance"
)
@pytest.mark.parametrize(
    "mode",
    [
        "queue",
        "cancel",
        "commands",
        "inline",
        "interrupt",
        "leave-fullscreen",
        "interrupt-before-submit",
        "idle-interrupt",
    ],
)
def test_persistent_session_queue_commands_cancellation_and_lifecycle(tmp_path, mode):
    import fcntl
    import pty
    import select
    import struct
    import termios
    from factory.core import chat_session as source_module

    root = tmp_path / "runtime"
    profiles = root / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "fixture.json").write_text(
        json.dumps(
            {
                "backend": "cc-backend",
                "model": "fixture-model",
                "api_format": "openai",
                "base_url": "http://127.0.0.1:1",
                "filesystem_mode": "workspace",
            }
        )
    )
    repo = Path(__file__).resolve().parents[1]
    assert (
        Path(source_module.__file__).resolve() == repo / "factory/core/chat_session.py"
    )
    cov_parent = os.environ.get("COVERAGE_FILE")
    artifact = (
        (
            Path(cov_parent).parent
            / (Path(cov_parent).name.removeprefix(".coverage.") + "-native-artifacts")
            / mode
        )
        if cov_parent
        else tmp_path / "artifacts"
    )
    artifact.mkdir(parents=True)
    data_file = artifact / "child.coverage"
    env = {
        **os.environ,
        "HOME": str(root / "home"),
        "FACTORY_DATA_DIR": str(root),
        "IGOREK_FACTORY_HOME": str(root),
        "FACTORY_PROFILES_DIR": str(profiles),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "FACTORY_SESSION_EMBEDDINGS_ENABLED": "off",
        "FACTORY_VAULT_ENABLED": "false",
        "IGOREK_TUI_MOUSE": "1",
        "TERM": "xterm-256color",
        "PYTHONPATH": str(repo),
    }
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 110, 0, 0))
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            "--source=factory,desktop",
            "--data-file=" + str(data_file),
            str(repo / "tests/native_persistent_session_child.py"),
            str(root),
            mode,
            str(artifact),
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=repo,
        env=env,
    )
    os.close(slave)
    transcript = bytearray()
    cursor_replies = 0

    def pump(seconds=0.1):
        nonlocal cursor_replies
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if select.select([master], [], [], 0.03)[0]:
                try:
                    raw = os.read(master, 65536)
                except OSError:
                    return
                transcript.extend(raw)
                for _ in range(raw.count(b"\x1b[6n")):
                    os.write(master, b"\x1b[1;1R")
                    cursor_replies += 1

    def wait_for(predicate, timeout=7):
        return wait_for_terminal(
            predicate,
            process=process,
            pump=pump,
            timeout=timeout,
            diagnostic=lambda: transcript.decode(errors="replace")[-2500:],
        )

    def events(kind):
        path = artifact / "events.jsonl"
        if not path.exists():
            return []
        result = []
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("kind") == kind:
                result.append(row)
        return result

    try:
        wait_for(lambda: not (termios.tcgetattr(master)[3] & termios.ICANON))
        pump(0.25)
        if mode == "commands":
            os.write(master, b"/profile\r")
            wait_for(lambda: len(events("command")) == 1)
            os.write(master, b"/status\r")
            wait_for(lambda: len(events("command")) == 2)
            assert events("submitted") == []
            pump(0.1)
            assert not (termios.tcgetattr(master)[3] & termios.ICANON)
        if mode == "leave-fullscreen":
            os.write(master, b"/tui classic\r")
        else:
            os.write(master, b"first native turn\r")
            if mode == "interrupt-before-submit":
                wait_for(lambda: len(events("submitting")) == 1)
            else:
                wait_for(lambda: len(events("submitted")) == 1)
            if mode == "idle-interrupt":
                wait_for(lambda: len(events("completed")) == 1)
                pump(0.15)
                os.write(master, b"\x03")
            elif mode in {"interrupt", "interrupt-before-submit"}:
                os.write(master, b"\x03")
            else:
                if mode == "cancel":
                    os.write(master, b"\x1b")
                    wait_for(lambda: len(events("cancelled")) == 1)
                    wait_for(lambda: len(events("completed")) == 1)
                    assert process.poll() is None
                    os.write(master, b"recovery native turn\r")
                else:
                    os.write(master, b"second native turn\r")
                if mode == "queue":
                    os.write(master, b"\x1b[<64;2;2M\x1b[<65;2;2M")
                    fcntl.ioctl(
                        master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0)
                    )
                    os.kill(process.pid, signal.SIGWINCH)
                wait_for(lambda: len(events("completed")) == 2, timeout=12)
                assert process.poll() is None
                os.write(master, b"/exit\r")
        wait_for(lambda: (artifact / "report.json").exists(), timeout=12)
        wait_for(lambda: process.poll() is not None, timeout=3)
        assert process.returncode == 0
        report = json.loads((artifact / "report.json").read_text())
        assert report["applications"] == 1
        assert report["invalid_exit_calls"] == 0
        assert (
            report["external_app_cleared"]
            and report["loop_cleared"]
            and not report["in_turn"]
        )
        for name, digest in report["source_hashes"].items():
            assert hashlib.sha256((repo / name).read_bytes()).hexdigest() == digest
        if mode == "leave-fullscreen":
            assert not report["fullscreen"] and report["submitted"] == []
        elif mode in {"interrupt", "interrupt-before-submit"}:
            assert not report["active"]
            assert report["cancelled"] == ["native-task-1"]
            assert not any(
                row.get("role") == "assistant" and row.get("status") == "ok"
                for row in report["history"]
            )
        elif mode == "idle-interrupt":
            assert not report["active"]
            assert report["cancelled"] == []
            assert len(report["completed"]) == 1
        else:
            assert len(report["submitted"]) == len(report["completed"]) == 2
            assert not report["active"] and report["pending"] == []
            assert report["submitted"][0] == "first native turn"
            assert report["submitted"][1] == (
                "recovery native turn" if mode == "cancel" else "second native turn"
            )
            assert "VISIBLE_NATIVE_ANSWER_native-task-2" in re.sub(
                r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", transcript.decode(errors="replace")
            )
            if mode == "cancel":
                assert report["cancelled"] == ["native-task-1"]
            else:
                assert report["cancelled"] == []
                assert [
                    row["text"]
                    for row in report["history"]
                    if row.get("role") == "user"
                ] == ["first native turn", "second native turn"]
            if mode == "queue":
                assert report["terminal_size"] == [100, 40]
                assert len(report["mouse"]) >= 2
            if mode == "commands":
                assert report["commands"] == ["/profile", "/status"]
            if mode == "inline":
                assert cursor_replies > 0
        child = coverage.CoverageData(basename=str(data_file))
        child.read()
        assert child.has_arcs() and child.lines(
            str(repo / "factory/core/chat_session.py")
        )
        active = coverage.Coverage.current()
        if active is not None:
            assert active.config.branch
            active.get_data().update(child)
        (artifact / "parent-acceptance.json").write_text(
            json.dumps(
                {
                    "cursor_replies": cursor_replies,
                    "source_hashes_verified": True,
                    "child_branch_coverage_verified": True,
                    "native_input_verified": True,
                    "single_persistent_application_verified": True,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        pump(0.05)
        os.close(master)
        (artifact / "terminal.bin").write_bytes(transcript)
        (artifact / "terminal.txt").write_text(
            re.sub(
                r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", transcript.decode(errors="replace")
            )
        )
