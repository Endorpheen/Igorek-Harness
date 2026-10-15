"""Slow headers consume a bounded slot and cannot stall independent requests."""
import http.client
from http.server import BaseHTTPRequestHandler
import socket
import threading
import time

import pytest

from factory.core.http_server import BoundedHTTPServer, HeaderDeadlineMixin


class Handler(HeaderDeadlineMixin, BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/slow-response':
            time.sleep(.2)
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'OK')


@pytest.fixture
def server():
    class SmallServer(BoundedHTTPServer):
        max_requests = 2
        header_timeout = .1

    server = SmallServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def get(server, path='/'):
    client = http.client.HTTPConnection(*server.server_address, timeout=1)
    try:
        client.request('GET', path)
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()


def test_incomplete_header_expires_without_blocking_a_valid_request(server):
    with socket.create_connection(server.server_address, timeout=1) as peer:
        peer.sendall(b'GET / HTTP/1.1\r\nHost: fixture\r\nX-Incomplete:')
        assert get(server) == (200, b'OK')
        assert peer.recv(1) == b''
    assert get(server) == (200, b'OK')


def test_header_deadline_does_not_expire_a_valid_long_response(server):
    assert get(server, '/slow-response') == (200, b'OK')


def test_excess_clients_are_closed_and_slots_recover(server):
    peers = [socket.create_connection(server.server_address, timeout=1) for _ in range(3)]
    try:
        assert peers[2].recv(1) == b''
        time.sleep(.15)
        assert get(server) == (200, b'OK')
    finally:
        for peer in peers:
            peer.close()
