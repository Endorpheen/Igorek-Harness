"""Owned JSON-line protocol peer; never launches a model or executes a command."""

import json
from pathlib import Path
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

mode, log_path = sys.argv[1], Path(sys.argv[2])


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(message, ensure_ascii=False) + "\n")
    method = message.get("method")
    identity = message.get("id")
    if method is None:
        continue
    if method == "initialize":
        emit({"id": identity, "result": {"serverInfo": {"name": "owned fixture"}}})
    elif method in {"thread/start", "thread/resume"}:
        emit(
            {
                "id": identity,
                "result": {
                    "thread": {
                        "id": message.get("params", {}).get("threadId")
                        or "fixture-thread"
                    }
                },
            }
        )
    elif method == "turn/start":
        if mode == "exit":
            raise SystemExit(23)
        if mode == "ignore":
            continue
        if mode == "error":
            emit(
                {
                    "id": identity,
                    "error": {"code": -32000, "message": "fixture refused"},
                }
            )
            continue
        if mode == "malformed":
            print("{broken JSON", flush=True)
            emit([])
            emit({"id": [identity], "result": "invalid identity"})
        print("fixture diagnostic", file=sys.stderr, flush=True)
        emit({"method": "fixture/notice", "params": {"text": "Привет 🐙"}})
        emit(
            {
                "id": "fixture-approval",
                "method": "item/commandExecution/requestApproval",
                "params": {"command": ["fixture-never-executed"]},
            }
        )
        emit({"id": identity, "result": {"turn": {"id": "fixture-turn"}}})
