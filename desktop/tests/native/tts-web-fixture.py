"""Serve the actual chat assets inside an isolated native TTS acceptance test."""
import json
import sys
import threading
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[3]), sys.argv[1]]
from factory.web_ui.server import _Server

server = _Server(0, "http://127.0.0.1:1")
thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
thread.start()
print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}"}), flush=True)
try:
    sys.stdin.readline()
finally:
    server.shutdown()
    server.server_close()
    thread.join(2)
