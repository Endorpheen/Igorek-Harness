import json
import multiprocessing
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_mobile_worker_end_to_end import _run_fixture_worker


def test_goal_uses_real_worker_and_model_tool_until_completion(tmp_path, monkeypatch):
    root = tmp_path / "owned"
    data, work, home = root / "data", root / "work", root / "home"
    profiles = data / "profiles"
    profiles.mkdir(parents=True)
    work.mkdir()
    home.mkdir()
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            if len(requests) == 2:
                delta = {"tool_calls": [{"index": 0, "id": "call-goal", "type": "function",
                                         "function": {"name": "update_goal", "arguments": '{"status":"complete"}'}}]}
                reason = "tool_calls"
            else:
                delta = {"content": "First step" if len(requests) == 1 else "Verified complete"}
                reason = "stop"
            events = [{"model": "fixture-model", "choices": [{"delta": delta}]},
                      {"model": "fixture-model", "choices": [{"delta": {}, "finish_reason": reason}]}]
            wire = b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events) + b"data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    server_thread.start()
    (profiles / "fixture.json").write_text(json.dumps({
        "backend": "cc-backend", "provider": "openrouter", "model": "fixture-model",
        "api_format": "openai", "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "filesystem_mode": "workspace", "mcp_servers": [], "checkpoints": {"enabled": False},
    }))
    for name, value in {"IGOREK_FACTORY_HOME": str(data), "FACTORY_DATA_DIR": str(data),
                        "FACTORY_PROFILES_DIR": str(profiles), "FACTORY_VAULT_ENABLED": "false",
                        "MCP_SERVERS": "", "IGOREK_ISOLATION_MODE": "off"}.items():
        monkeypatch.setenv(name, value)
    from factory.core.mobile_bridge.service import MobileService
    from factory.core.runtime import get_db_path
    from factory.core.schema import initialize_database
    initialize_database(get_db_path())
    service = MobileService(profile="fixture", workdir=str(work), available_profiles=lambda: ["fixture"])
    session = service.create_session(profile="fixture", cwd=str(work))["session_id"]
    service.goal_action(session, "create", objective="Finish the fixture")
    assert service.advance_goals() == 1
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    process = context.Process(target=_run_fixture_worker, args=(root, stop))

    def wait_for_task(task_id):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            task = service.get_task(task_id)
            if task["status"] in {"ok", "fail", "dead_letter", "cancelled", "timeout"}:
                return task
            assert process.is_alive(), f"worker exited: {process.exitcode}"
            time.sleep(0.05)
        raise AssertionError("fixture task did not finish")

    try:
        first = service.get_session(session)["running_task_id"]
        process.start()
        assert wait_for_task(first)["status"] == "ok"
        service.finalize_turns()
        assert service.advance_goals() == 1
        second = service.get_session(session)["running_task_id"]
        assert second != first and wait_for_task(second)["status"] == "ok"
        service.finalize_turns()
        assert service.get_session(session)["goal"]["status"] == "complete"
        assert service.advance_goals() == 0
        assert len(requests) == 3
        assert {item["function"]["name"] for item in requests[1]["tools"]} >= {"get_goal", "update_goal"}
        assert any("Finish the fixture" in str(message.get("content", "")) for message in requests[1]["messages"])
    finally:
        stop.set()
        if process.pid is not None:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(10)
        server.shutdown()
        server.server_close()
        server_thread.join(5)
        assert not server_thread.is_alive()
