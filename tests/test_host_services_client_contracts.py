"""Contract tests for the stdio host services client transport."""

import importlib.util
import io
import json
from pathlib import Path

import pytest

SOURCE_PATH = str(Path(__file__).resolve().parents[1] / "desktop" / "opencode-host-services-client.py")
HOST_SERVICES_UNAVAILABLE_MESSAGE = "Host services unavailable or access revoked"
ERROR_CODE = -32000
MAX_REQUEST_CHARS = 65536
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RESPONSE_READ_LIMIT = MAX_RESPONSE_BYTES + 1


class FakePath:
    token = "igorek-fake-token-7f3a9c2b"

    def __init__(self, *args, **kwargs):
        self.args = args

    def read_text(self):
        return FakePath.token


class FakeResponse:
    def __init__(self, status=200, body=b""):
        self.status = status
        self.body = body
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size is None or size < 0:
            return self.body
        return self.body[:size]


class FakeConnection:
    last_instance = None
    default_response = None

    def __init__(self, *args, **kwargs):
        self.closed = False
        self.requests = []
        self.response = FakeConnection.default_response
        if self.response is None:
            self.response = FakeResponse(200, b"{}")
        FakeConnection.last_instance = self

    def request(self, method, path, body, headers):
        self.requests.append({"method": method, "path": path, "body": body, "headers": headers})

    def getresponse(self):
        response = self.response
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


class FakeSocket:
    instances = []

    def __init__(self, family=None, type=None):
        self.family = family
        self.type = type
        self.timeouts = []
        self.connected_to = []
        FakeSocket.instances.append(self)

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def connect(self, path):
        self.connected_to.append(path)


class FakeSocketModule:
    AF_UNIX = 1
    SOCK_STREAM = 1
    socket = FakeSocket


def load_client_module():
    spec = importlib.util.spec_from_file_location(
        "opencode_host_services_client_under_test", SOURCE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(monkeypatch):
    module = load_client_module()
    monkeypatch.setattr(module, "Path", FakePath)
    monkeypatch.setattr(module, "Connection", FakeConnection)
    FakeConnection.last_instance = None
    FakeConnection.default_response = None
    return module


def request_line(message):
    return json.dumps(message) + "\n"


def test_successful_request_returns_jsonrpc_reply(client, capsys, monkeypatch):
    module = client
    line = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    FakeConnection.default_response = FakeResponse(200, json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"tools": [{"name": "read_file"}]}}).encode())
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(line + "\n"))
    module.main()

    output = capsys.readouterr().out
    reply = json.loads(output.strip())
    assert reply["id"] == 7
    assert reply["result"] == {"tools": [{"name": "read_file"}]}

    connection = FakeConnection.last_instance
    assert connection is not None
    assert connection.closed is True
    assert len(connection.requests) == 1
    sent = connection.requests[0]
    assert sent["method"] == "POST"
    assert sent["path"] == "/mcp"
    assert sent["body"] == (line + "\n").encode()
    assert sent["headers"]["Authorization"] == "Bearer " + FakePath.token
    assert sent["headers"]["Content-Type"] == "application/json"


def test_accepted_202_status_returns_jsonrpc_reply(client, capsys, monkeypatch):
    module = client
    line = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    FakeConnection.default_response = FakeResponse(202, json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"tools": [{"name": "ping"}]}}).encode())
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(line + "\n"))
    module.main()

    reply = json.loads(capsys.readouterr().out.strip())
    assert reply["id"] == 3
    assert reply["result"] == {"tools": [{"name": "ping"}]}


def test_notification_without_id_is_not_printed(client, capsys, monkeypatch):
    module = client
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    FakeConnection.default_response = FakeResponse(200, b"{}")
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(json.dumps(notification) + "\n"))
    module.main()

    assert capsys.readouterr().out == ""
    connection = FakeConnection.last_instance
    assert connection is not None
    assert connection.closed is True


def test_request_over_65536_chars_is_not_sent(client, capsys, monkeypatch):
    module = client
    line = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {"data": "a" * 70000}})
    assert len(line) > MAX_REQUEST_CHARS
    FakeConnection.default_response = FakeResponse(200, b"{}")
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(line + "\n"))
    module.main()

    assert capsys.readouterr().out == ""
    assert FakeConnection.last_instance is None


def test_response_over_2mib_is_rejected(client, capsys, monkeypatch):
    module = client
    body = json.dumps({"jsonrpc": "2.0", "id": 5, "result": {"data": "a" * (MAX_RESPONSE_BYTES + 16)}}).encode()
    assert len(body) > MAX_RESPONSE_BYTES
    FakeConnection.default_response = FakeResponse(200, body)
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(request_line({"jsonrpc": "2.0", "id": 5, "method": "tools/call"})))
    module.main()

    reply = json.loads(capsys.readouterr().out.strip())
    assert reply["id"] == 5
    assert reply["error"]["code"] == ERROR_CODE
    assert reply["error"]["message"] == HOST_SERVICES_UNAVAILABLE_MESSAGE
    assert FakeConnection.last_instance.response.read_sizes == [RESPONSE_READ_LIMIT]


def test_error_status_returns_common_jsonrpc_error(client, capsys, monkeypatch):
    module = client
    FakeConnection.default_response = FakeResponse(500, b'{"error": {"code": 500}}')
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(request_line({"jsonrpc": "2.0", "id": 11, "method": "tools/call"})))
    module.main()

    reply = json.loads(capsys.readouterr().out.strip())
    assert reply["id"] == 11
    assert reply["error"]["code"] == ERROR_CODE
    assert FakeConnection.last_instance.closed is True


def test_invalid_json_response_returns_common_jsonrpc_error(client, capsys, monkeypatch):
    module = client
    FakeConnection.default_response = FakeResponse(200, b"<not-json>")
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(request_line({"jsonrpc": "2.0", "id": 12, "method": "tools/call"})))
    module.main()

    reply = json.loads(capsys.readouterr().out.strip())
    assert reply["id"] == 12
    assert reply["error"]["code"] == ERROR_CODE


def test_transport_exception_returns_common_jsonrpc_error(client, capsys, monkeypatch):
    module = client
    FakeConnection.default_response = RuntimeError("socket failure")
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(request_line({"jsonrpc": "2.0", "id": 13, "method": "tools/call"})))
    module.main()

    output = capsys.readouterr().out
    reply = json.loads(output.strip())
    assert reply["id"] == 13
    assert reply["error"]["code"] == ERROR_CODE
    assert "socket failure" not in output
    assert FakePath.token not in output
    assert FakeConnection.last_instance.closed is True


def test_common_error_has_no_raw_reason_or_token(client, capsys, monkeypatch):
    module = client
    FakeConnection.default_response = FakeResponse(403, b"denied")
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(request_line({"jsonrpc": "2.0", "id": 21, "method": "tools/call"})))
    module.main()

    output = capsys.readouterr().out
    reply = json.loads(output.strip())
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] == 21
    assert reply["error"]["code"] == ERROR_CODE
    assert reply["error"]["message"] == HOST_SERVICES_UNAVAILABLE_MESSAGE
    assert "data" not in reply["error"]
    assert FakePath.token not in output


def test_connection_closed_on_error_after_transport_failure(client, capsys, monkeypatch):
    module = client
    FakeConnection.default_response = RuntimeError("boom")
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(request_line({"jsonrpc": "2.0", "id": 14, "method": "tools/call"})))
    module.main()

    output = capsys.readouterr().out
    reply = json.loads(output.strip())
    assert reply["error"]["code"] == ERROR_CODE
    assert "boom" not in output
    assert FakePath.token not in output
    connection = FakeConnection.last_instance
    assert connection is not None
    assert connection.closed is True


def test_empty_stdin_is_quiet(client, capsys, monkeypatch):
    module = client
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(""))
    module.main()

    assert capsys.readouterr().out == ""
    assert FakeConnection.last_instance is None


def test_connection_connect_uses_fake_unix_socket(monkeypatch):
    module = load_client_module()
    monkeypatch.setattr(module, "socket", FakeSocketModule)

    connection = module.Connection("localhost", timeout=100)
    connection.connect()

    socket = FakeSocket.instances[-1]
    assert socket.timeouts == [100]
    assert socket.connected_to == ["/run/igorek-host-services.sock"]
