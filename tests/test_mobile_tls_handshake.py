import http.client
import json
import socket
import ssl
import threading
import time

import pytest

from factory.core.mobile_bridge.server import BridgeConfig, MobileBridge, build_server
from factory.web_ui.server import _certificate_paths


@pytest.fixture(params=['standalone', 'desktop'])
def endpoint(request, tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    cert, key, _ = _certificate_paths()
    bridge = MobileBridge(config=BridgeConfig(workdir=str(tmp_path)))
    access = None
    if request.param == 'desktop':
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parents[1] / 'desktop'))
        from mobile_access import DesktopMobileAccess
        access = DesktopMobileAccess(bridge, cert, key, tmp_path)
        access.port = 0
        access.listen()
        server, thread = access.server, access.thread
    else:
        bridge.config.tls_cert, bridge.config.tls_key = str(cert), str(key)
        server = build_server(bridge, host='127.0.0.1', port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    peers = []
    yield server, bridge, peers
    for peer in peers:
        peer.close()
    if access:
        access.close()
    else:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    bridge.stop()


def connection(server):
    return http.client.HTTPSConnection('127.0.0.1', server.server_address[1],
                                       context=ssl._create_unverified_context(), timeout=1)


def test_silent_tls_peer_does_not_block_another_phone(endpoint):
    server, bridge, peers = endpoint
    silent = socket.create_connection(('127.0.0.1', server.server_address[1]), timeout=1)
    peers.append(silent)
    time.sleep(.05)
    phone = connection(server)
    try:
        phone.request('GET', '/mobile/v1/health')
        reply = phone.getresponse()
        assert reply.status == 200
        assert json.loads(reply.read())['status'] == 'ok'
    finally:
        phone.close()


def test_incomplete_tls_peer_times_out_and_frees_a_handshake_slot(endpoint):
    server, bridge, peers = endpoint
    server.tls_handshake_timeout = .15
    silent = socket.create_connection(('127.0.0.1', server.server_address[1]), timeout=1)
    peers.append(silent)
    silent.sendall(b'\x16\x03\x01\x00')
    silent.settimeout(1)
    assert silent.recv(1) == b''
    phone = connection(server)
    try:
        phone.request('GET', '/mobile/v1/health')
        assert phone.getresponse().status == 200
    finally:
        phone.close()


def test_tls_timeout_does_not_expire_authenticated_http_keep_alive(endpoint):
    server, bridge, peers = endpoint
    server.tls_handshake_timeout = .1
    code = bridge.store.create_pairing_code()['code']
    phone = connection(server)
    try:
        phone.request('POST', '/mobile/v1/pair/complete', json.dumps({'code': code, 'name': 'TLS test', 'platform': 'android'}),
                      {'Content-Type': 'application/json'})
        paired = phone.getresponse()
        assert paired.status == 201
        token = json.loads(paired.read())['token']
        time.sleep(.25)
        phone.request('GET', '/mobile/v1/devices', headers={'Authorization': 'Bearer ' + token})
        authorized = phone.getresponse()
        assert authorized.status == 200
        authorized.read()
        phone.request('GET', '/mobile/v1/devices')
        denied = phone.getresponse()
        assert denied.status == 401
        denied.read()
    finally:
        phone.close()


def test_shutdown_does_not_wait_for_a_silent_tls_peer(endpoint):
    server, bridge, peers = endpoint
    server.tls_handshake_timeout = 30
    silent = socket.create_connection(('127.0.0.1', server.server_address[1]), timeout=1)
    peers.append(silent)
    time.sleep(.05)
    finished = threading.Event()
    def close():
        server.shutdown()
        server.server_close()
        finished.set()
    closer = threading.Thread(target=close, daemon=True)
    closer.start()
    try:
        assert finished.wait(2), 'Closing the listener waited for a TLS handshake'
    finally:
        silent.close()
        closer.join(timeout=2)


def test_handshake_limit_rejects_excess_peers_then_recovers(endpoint):
    server, bridge, peers = endpoint
    server._tls_slots = threading.BoundedSemaphore(2)
    server.tls_handshake_timeout = .4
    first = socket.create_connection(('127.0.0.1', server.server_address[1]), timeout=1)
    second = socket.create_connection(('127.0.0.1', server.server_address[1]), timeout=1)
    peers.extend([first, second])
    deadline = time.monotonic() + .2
    while len(server._tls_pending) < 2 and time.monotonic() < deadline:
        time.sleep(.005)
    assert len(server._tls_pending) == 2
    excess = socket.create_connection(('127.0.0.1', server.server_address[1]), timeout=1)
    peers.append(excess)
    excess.settimeout(.2)
    assert excess.recv(1) == b''
    first.settimeout(1)
    second.settimeout(1)
    assert first.recv(1) == second.recv(1) == b''
    phone = connection(server)
    try:
        phone.request('GET', '/mobile/v1/health')
        assert phone.getresponse().status == 200
    finally:
        phone.close()


def test_plaintext_client_cannot_reach_the_https_handler(endpoint):
    server, bridge, peers = endpoint
    peer = socket.create_connection(('127.0.0.1', server.server_address[1]), timeout=1)
    peers.append(peer)
    peer.sendall(b'GET /mobile/v1/health HTTP/1.1\r\nHost: localhost\r\n\r\n')
    try:
        assert peer.recv(1024) == b''
    except ConnectionResetError:
        pass


def test_authenticated_sse_keeps_delivering_task_updates_beside_a_silent_peer(endpoint):
    server, bridge, peers = endpoint
    code = bridge.store.create_pairing_code()['code']
    paired = bridge.store.complete_pairing(code=code, name='TLS SSE test')
    phone = connection(server)
    phone.timeout = 3
    response = None
    try:
        phone.request('GET', '/mobile/v1/events?after=0', headers={'Authorization': 'Bearer ' + paired['token']})
        response = phone.getresponse()
        assert response.status == 200
        assert b'stream.ready' in response.readline()
        response.readline()
        peers.append(socket.create_connection(('127.0.0.1', server.server_address[1]), timeout=1))
        seq = bridge.store.append_event(kind='task.finished', payload={'status': 'ok'}, task_id='tls-regression-task')
        while True:
            line = response.readline()
            assert line
            if line.startswith(b'data: '):
                event = json.loads(line[6:])
                if event.get('seq') == seq:
                    assert event['payload']['status'] == 'ok'
                    break
    finally:
        if response:
            response.close()
        phone.close()
