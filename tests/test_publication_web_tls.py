"""Silent TLS clients cannot stall the local Web UI accept loop."""
import http.client
import socket
import ssl
import threading
import time

import pytest

from factory.web_ui.server import _Server, _certificate_paths


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    cert, key, _ = _certificate_paths()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = _Server(0, 'http://127.0.0.1:1')
    server.enable_tls(context)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_silent_tls_peer_does_not_block_a_regular_web_request(web):
    silent = socket.create_connection(web.server_address, timeout=1)
    browser = http.client.HTTPSConnection(*web.server_address,
                                         context=ssl._create_unverified_context(), timeout=1)
    try:
        time.sleep(.05)
        browser.request('GET', '/')
        response = browser.getresponse()
        assert response.status == 200
        assert b'<html' in response.read()
    finally:
        browser.close()
        silent.close()


def test_incomplete_tls_handshake_expires_and_shutdown_reclaims_pending_peers(web):
    web.tls_handshake_timeout = .1
    peer = socket.create_connection(web.server_address, timeout=1)
    try:
        peer.sendall(b'\x16\x03\x01\x00')
        assert peer.recv(1) == b''
    finally:
        peer.close()
    web.tls_handshake_timeout = 30
    peer = socket.create_connection(web.server_address, timeout=1)
    deadline = time.monotonic() + 1
    while not web._tls_pending and time.monotonic() < deadline:
        time.sleep(.01)
    assert web._tls_pending
    started = time.monotonic()
    web.server_close()
    assert time.monotonic() - started < 1
    peer.close()
