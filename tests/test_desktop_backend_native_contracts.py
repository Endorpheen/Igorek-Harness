"""Fresh desktop backend processes own only their isolated data and sockets."""

import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import queue
import signal
import socket
import ssl
import subprocess
import sys
import threading
from urllib import request

import coverage
import pytest

from factory.core.schema import open_db

pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="Native POSIX desktop pipe and signal lifecycle acceptance",
)


@pytest.mark.parametrize(
    "mode",
    ["stop", "eof", "signal", "invalid-control", "oversized-control", "startup-error"],
)
def test_fresh_backend_protocol_https_pairing_and_cleanup(tmp_path, mode):
    repo = Path(__file__).resolve().parents[1]
    source = repo / "desktop/backend.py"
    inventory = (
        subprocess.check_output(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=repo
        )
        .decode()
        .split("\0")
    )
    hashes = {
        p: hashlib.sha256((repo / p).read_bytes()).hexdigest()
        for p in set(inventory)
        if p
        and (p.endswith(".py") or p in {"pyproject.toml", "uv.lock"})
        and (repo / p).is_file()
    }
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    if mode == "startup-error":
        (root / "database").write_text("fixture unusable database directory")
    parent_coverage = os.environ.get("COVERAGE_FILE")
    artifact = (
        (
            Path(parent_coverage).parent
            / (
                Path(parent_coverage).name.removeprefix(".coverage.")
                + "-native-artifacts"
            )
            / mode
        )
        if parent_coverage
        else tmp_path / "artifacts"
    )
    artifact.mkdir(parents=True)
    data_file = artifact / "child.coverage"
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL"}
    }
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "FACTORY_DATA_DIR": str(root),
            "IGOREK_FACTORY_HOME": str(root),
            "FACTORY_ENV_FILES_DIR": str(tmp_path / "env"),
            "PYTHONPATH": str(repo),
            "PYTHONUNBUFFERED": "1",
            "FACTORY_SESSION_EMBEDDINGS_ENABLED": "off",
        }
    )
    rows = queue.Queue()
    frames = []
    web_port = None
    reader = None
    with (artifact / "stderr.log").open("w") as stderr:
        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "coverage",
                "run",
                "--branch",
                "--source=" + str(repo / "factory") + "," + str(repo / "desktop"),
                "--data-file=" + str(data_file),
                str(source),
                str(root),
            ],
            cwd=repo,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
        )

        def read_stdout():
            for line in process.stdout:
                rows.put(line)
            rows.put(None)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()

        def frame(timeout=20):
            try:
                line = rows.get(timeout=timeout)
            except queue.Empty:
                raise AssertionError(
                    "Native backend did not emit its next protocol frame"
                ) from None
            assert line is not None, (
                "Native backend exited before its next protocol frame"
            )
            value = json.loads(line)
            frames.append(
                {
                    key: value[key]
                    for key in ("type", "id", "code", "error")
                    if key in value
                }
            )
            return value

        def send(value):
            process.stdin.write(
                (value if isinstance(value, str) else json.dumps(value)) + "\n"
            )
            process.stdin.flush()

        try:
            ready = frame()
            if mode == "startup-error":
                assert ready == {"type": "error", "code": "backend_start_failed"}
                assert process.wait(timeout=10) == 1
            else:
                assert ready["type"] == "ready"
                assert (
                    isinstance(ready["pairing_code"], str)
                    and len(ready["pairing_code"]) >= 6
                )
                certificate = Path(ready["cert"])
                assert certificate.is_relative_to(tmp_path) and certificate.is_file()
                assert (
                    isinstance(ready["factory_version"], str)
                    and ready["factory_version"]
                )
                web_port = ready["web_port"]
                assert type(web_port) is int and 0 < web_port < 65536
                send({"id": "status", "action": "status"})
                status = frame()
                assert (
                    status["type"] == "owner_reply"
                    and status["id"] == "status"
                    and "error" not in status
                )
                assert status["result"]["agent"] == {"profile": "", "running": False}
                assert status["result"]["state"] != "unlocked"
                assert (root / "database/factory.db").is_file()
                with open_db(str(root / "database/factory.db")) as conn:
                    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
                assert (root.stat().st_mode & 0o777) == 0o700
                if mode == "stop":
                    base = f"https://127.0.0.1:{web_port}"
                    cookies = http.cookiejar.CookieJar()
                    opener = request.build_opener(
                        request.ProxyHandler({}),
                        request.HTTPSHandler(
                            context=ssl.create_default_context(cafile=str(certificate))
                        ),
                        request.HTTPCookieProcessor(cookies),
                    )

                    def get(path):
                        with opener.open(base + path, timeout=5) as response:
                            return response.status, response.read()

                    def post(path, body):
                        req = request.Request(
                            base + path,
                            data=json.dumps(body).encode(),
                            headers={
                                "Content-Type": "application/json",
                                "Origin": base,
                            },
                            method="POST",
                        )
                        with opener.open(req, timeout=5) as response:
                            return response.status, json.load(response)

                    code, page = get("/")
                    assert code == 200 and b"<html" in page.lower()
                    assert json.loads(get("/api/status")[1]) == {"paired": False}
                    code, paired = post("/api/pair", {"code": ready["pairing_code"]})
                    assert code == 201 and paired["paired"] is True
                    assert list(cookies) and all(cookie.secure for cookie in cookies)
                    assert json.loads(get("/api/status")[1])["paired"] is True
                    code, logged_out = post("/api/logout", {})
                    assert code == 200 and logged_out["logged_out"] is True
                    assert json.loads(get("/api/status")[1]) == {"paired": False}
                    send({"type": "browser_reply", "id": "unmatched", "result": {}})
                    send({"type": "opencode_reply", "id": "unmatched", "result": {}})
                    send({"id": "after-replies", "action": "status"})
                    assert frame()["id"] == "after-replies"
                elif mode == "invalid-control":
                    send("{bad JSON")
                    invalid = frame()
                    assert (
                        invalid["type"] == "owner_reply"
                        and invalid["id"] is None
                        and invalid.get("error")
                    )
                    send({"id": "after-invalid", "action": "status"})
                    assert frame()["id"] == "after-invalid"
                if mode == "eof":
                    process.stdin.close()
                elif mode == "signal":
                    process.send_signal(signal.SIGTERM)
                elif mode == "oversized-control":
                    try:
                        process.stdin.write("x" * (24 * 1024 * 1024 + 2))
                        process.stdin.flush()
                    except BrokenPipeError:
                        pass
                else:
                    send("stop")
                assert process.wait(timeout=12) == 0
            reader.join(timeout=2)
            assert not reader.is_alive()
            assert rows.get(timeout=2) is None
            assert all(
                hashlib.sha256((repo / path).read_bytes()).hexdigest() == digest
                for path, digest in hashes.items()
            )
            if web_port is not None:
                with socket.socket() as sock:
                    sock.settimeout(1)
                    assert sock.connect_ex(("127.0.0.1", web_port)) != 0
            child = coverage.CoverageData(basename=str(data_file))
            child.read()
            assert child.has_arcs() and child.lines(str(source))
            active = coverage.Coverage.current()
            if active is not None:
                assert active.config.branch
                active.get_data().update(child)
            (artifact / "parent-acceptance.json").write_text(
                json.dumps(
                    {
                        "mode": mode,
                        "exit_code": process.returncode,
                        "same_canonical_source_verified": True,
                        "source_inventory_items": len(hashes),
                        "child_branch_coverage_verified": True,
                        "native_process_and_pipe": True,
                        "native_https_pairing": mode == "stop",
                        "model_worker_started": False,
                        "owner_services_changed": False,
                        "protocol_frames_without_secrets": frames,
                    },
                    indent=2,
                )
                + "\n"
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdin and not process.stdin.closed:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            reader.join(timeout=2)
            process.stdout.close()
