"""Real PTY task-turn acceptance with synthetic provider/task boundaries."""

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
    "mode", ["normal", "mouse", "interrupt", "escape", "cancelled"]
)
def test_native_turn_keys_mouse_resize_and_external_cancellation(tmp_path, mode):
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
            str(repo / "tests/native_active_turn_child.py"),
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

    try:
        wait_for(lambda: not (termios.tcgetattr(master)[3] & termios.ICANON))
        pump(0.2)
        if mode == "normal":
            os.write(master, b"queued native follow-up\r")
        elif mode == "mouse":
            os.write(master, b"\x1b[<64;2;2M\x1b[<65;2;2M\x1b[<0;2;29M\x1b[<0;2;29m")
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
            os.kill(process.pid, signal.SIGWINCH)
        elif mode == "interrupt":
            os.write(master, b"\x03")
        elif mode == "escape":
            os.write(master, b"\x1b")
        wait_for(lambda: (artifact / "report.json").exists(), timeout=7)
        wait_for(lambda: process.poll() is not None, timeout=3)
        assert process.returncode == 0
        report = json.loads((artifact / "report.json").read_text())
        if mode in {"normal", "interrupt"}:
            assert cursor_replies > 0
        assert report["finished_exit_calls"] == 0
        for name, digest in report["source_hashes"].items():
            assert hashlib.sha256((repo / name).read_bytes()).hexdigest() == digest
        if mode in {"interrupt", "escape"}:
            assert (
                report["cancelled"] == ["native-task"]
                and report["result_status"] is None
            )
            assert report["elapsed"] < 4
        elif mode == "cancelled":
            assert report["result_status"] == "cancelled" and report["cancelled"] == []
        else:
            assert report["result_status"] == "ok" and report["cancelled"] == []
            assert b"VISIBLE_NATIVE_ANSWER" in transcript
            if mode == "normal":
                assert report["pending"] == ["queued native follow-up"]
            else:
                assert report["terminal_size"] == [100, 40]
                assert len(report["mouse"]) >= 4
                assert any(row["after"].get("offset", 0) > 0 for row in report["mouse"])
        child = coverage.CoverageData(basename=str(data_file))
        child.read()
        assert child.has_arcs() and child.lines(
            str(repo / "factory/core/cli_chat_active.py")
        )
        active = coverage.Coverage.current()
        if active is not None:
            assert active.config.branch
            active.get_data().update(child)
            assert set(
                child.lines(str(repo / "factory/core/cli_chat_active.py"))
            ).issubset(
                active.get_data().lines(str(repo / "factory/core/cli_chat_active.py"))
            )
        (artifact / "parent-acceptance.json").write_text(
            json.dumps(
                {
                    "cursor_replies": cursor_replies,
                    "source_hashes_verified": True,
                    "child_branch_coverage_verified": True,
                    "native_input_verified": True,
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
