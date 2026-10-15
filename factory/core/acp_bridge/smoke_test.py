#!/usr/bin/env python3
"""Smoke test for factory ACP bridge - verifies server responds to JSON-RPC."""

import json
import subprocess
import sys
import time
from pathlib import Path


def test_acp_bridge() -> bool:
    print("=== ACP Bridge smoke test ===\n")

    project_root = Path(__file__).resolve().parents[3]
    proc = subprocess.Popen(
        [sys.executable, "-m", "factory.core.acp_bridge.entry"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=project_root,
    )
    time.sleep(1)

    errors = []
    notifications = []
    next_msg_id = 0

    def send_jsonrpc(method, params=None):
        nonlocal next_msg_id
        next_msg_id += 1
        msg_id = next_msg_id
        req = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            req["params"] = params
        proc.stdin.write((json.dumps(req) + "\n").encode())
        proc.stdin.flush()
        while True:
            line = proc.stdout.readline()
            if not line:
                return None
            response = json.loads(line.decode().strip())
            if response.get("id") == msg_id:
                return response
            notifications.append(response)

    def send_notification(method, params=None):
        req = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            req["params"] = params
        proc.stdin.write((json.dumps(req) + "\n").encode())
        proc.stdin.flush()

    try:
        print("[1] Testing initialize...")
        resp = send_jsonrpc(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "smoke-test", "version": "1.0"},
            },
        )
        if resp and resp.get("result"):
            print(f"  OK protocol_version={resp['result'].get('protocolVersion')}")
            print(f"  OK agent={resp['result'].get('agentInfo', {}).get('name')}")
        else:
            errors.append(f"initialize failed: {resp}")
            print(f"  FAIL: {resp}")
            return False

        print("\n[2] Testing session/new...")
        resp = send_jsonrpc("session/new", {"cwd": "/tmp", "mcpServers": []})
        if resp and resp.get("result"):
            session_id = resp["result"].get("sessionId")
            print(f"  OK session_id={session_id[:8]}...")
        else:
            errors.append(f"session/new failed: {resp}")
            print(f"  FAIL: {resp}")
            return False

        print("\n[3] Testing session/list...")
        resp = send_jsonrpc("session/list", {})
        if resp and resp.get("result"):
            sessions = resp["result"].get("sessions", [])
            print(f"  OK {len(sessions)} session(s)")
        else:
            errors.append(f"session/list failed: {resp}")
            print(f"  FAIL: {resp}")

        print("\n[4] Testing session/prompt slash command...")
        resp = send_jsonrpc(
            "session/prompt",
            {
                "prompt": [{"type": "text", "text": "/version"}],
                "sessionId": session_id,
            },
        )
        if resp and resp.get("result"):
            print(f"  OK stop_reason={resp['result'].get('stopReason', '?')}")
        else:
            errors.append(f"session/prompt failed: {resp}")
            print(f"  FAIL: {resp}")

        print("\n[5] Testing session/cancel notification...")
        send_notification("session/cancel", {"sessionId": session_id})
        print("  OK cancel done")

        time.sleep(0.5)

    finally:
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()
        proc.terminate()
        proc.wait(timeout=5)

    print()
    if errors:
        print(f"FAILED: {errors}")
        return False

    print(f"  OK observed {len(notifications)} async notification(s)")
    print("=== ALL TESTS PASSED ===")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if test_acp_bridge() else 1)
