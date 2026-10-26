"""Model transport failures must not turn incomplete streams into successful answers."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from factory.core import cc_backend_executor as executor
from test_cc_stream_wire_contracts import wire as wire


def test_provider_timeout_is_reported_and_does_not_return_a_partial_answer(wire):
    wire.stall = True
    log = []
    result, _ = executor._http_post_streaming_openai(
        wire.url, {'model': 'fixture', 'messages': []}, {}, 1, log_fn=log.append
    )
    assert result['error_code'] == 'STREAM.INTERRUPTED'
    assert 'choices' not in result
    assert any('read timeout' in message for message in log)


def test_provider_disconnect_before_headers_is_reported_as_transport_failure():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        result, _ = executor._http_post_streaming_openai(
            f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
            {'model': 'fixture', 'messages': []}, {}, 2,
        )
        assert 'transport:remote_disconnected' in result['error']
        assert 'choices' not in result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


def test_read_reset_after_partial_tool_data_cannot_return_a_tool_call(monkeypatch):
    from unittest.mock import Mock

    response = Mock(status=200, fp=None)
    response.readline.side_effect = [
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"partial","function":{"name":"Write","arguments":"{"}}]}}]}\n',
        b'\n',
        ConnectionResetError('fixture reset'),
    ]
    connection = Mock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(executor.http.client, 'HTTPConnection', lambda *args, **kwargs: connection)
    result, _ = executor._http_post_streaming_openai(
        'http://127.0.0.1:18991/v1/chat/completions',
        {'model': 'fixture', 'messages': []}, {}, 2,
    )
    assert result['error_code'] == 'STREAM.INTERRUPTED'
    assert result['diagnostic']['phase'] == 'sse_read'
    assert 'choices' not in result
    connection.close.assert_called_once()
