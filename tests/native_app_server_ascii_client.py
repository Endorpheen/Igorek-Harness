"""Probe the canonical transport under an ASCII parent process locale."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
source = (
    Path(__file__).resolve().parents[1] / "factory/core/codex_app_server_adapter.py"
)
spec = importlib.util.spec_from_file_location("ascii_transport_probe", source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
original_popen = subprocess.Popen
processes = []
peer = Path(__file__).with_name("native_app_server_peer.py")


def spawn(argv, **kwargs):
    assert argv == ["codex", "app-server", "--listen", "stdio://"]
    process = original_popen(
        [sys.executable, "-u", str(peer), "normal", str(root / "wire.jsonl")], **kwargs
    )
    processes.append(process)
    return process


subprocess.Popen = spawn
notices = []
session = module.CodexAppServerSession(
    cwd=str(root),
    model="fixture",
    model_provider=None,
    env={"PYTHONUTF8": "1"},
    developer_instructions=None,
    notification_handler=lambda method, params: notices.append((method, params)),
    server_request_handler=lambda *args: None,
    error_handler=lambda *args: None,
)
request = session.request
session.request = lambda method, params, timeout_s=30: request(
    method, params, timeout_s=min(timeout_s, 2)
)
report = {"parent_encoding": sys.stdout.encoding, "exception": None}
try:
    session.start()
    report["result"] = session.send_user_message("Привет 🐙")
    report["notices"] = notices
except Exception as error:
    report["exception"] = type(error).__name__
finally:
    session.close()
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()
    report["owned_exit_codes"] = [process.returncode for process in processes]
    report["pending_empty"] = not session._pending
    (root / "probe.json").write_text(json.dumps(report, ensure_ascii=True))
print(json.dumps(report, ensure_ascii=True))
