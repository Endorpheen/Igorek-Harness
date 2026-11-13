"""Paired-device unlock over real TLS without reopening privileged routes."""
import json
import http.client
import ssl
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from mobile_access import DesktopMobileAccess
from remote_vault import RemoteVault
from vault_owner import OwnerVault
from factory.core.mobile_bridge.server import BridgeConfig, MobileBridge, build_server
from factory.web_ui.server import _certificate_paths


@pytest.fixture
def remote(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    owner = OwnerVault(tmp_path)
    owner.service.create('fixture-code')
    owner.service.add_credential('fixture', 'fixture-private-value', 'api_key')
    bridge = MobileBridge(config=BridgeConfig(workdir=str(tmp_path)))
    changes = []
    controller = RemoteVault(owner, on_state=changes.append)
    bridge.vault_access = controller
    cert, key, _ = _certificate_paths()
    access = DesktopMobileAccess(bridge, cert, key, tmp_path)
    access.port = 0
    owner.mobile_access = access
    access.start()
    paired = bridge.store.complete_pairing(code=bridge.store.create_pairing_code()['code'],
                                          name='fixture phone', platform='android')
    token = paired['token']
    context = ssl._create_unverified_context()

    def request(path, *, method='GET', body=None, credential=token, headers=None):
        fields = {'Authorization': 'Bearer ' + credential, 'Content-Type': 'application/json'}
        fields.update(headers or {})
        req = urllib.request.Request(f'https://127.0.0.1:{access.port}/mobile/v1' + path,
            data=json.dumps(body).encode() if body is not None else None, headers=fields, method=method)
        try:
            response = urllib.request.urlopen(req, context=context, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response), dict(response.headers)

    from types import SimpleNamespace
    fixture = SimpleNamespace(owner=owner, bridge=bridge, access=access, controller=controller,
                              paired=paired, request=request, changes=changes, root=tmp_path)
    try:
        yield fixture
    finally:
        access.close()
        bridge.stop()
        owner.close()


def test_closed_vault_keeps_pairing_but_blocks_data_and_actions(remote):
    assert remote.owner.dispatch({'id': 'lock', 'action': 'lock'})['result']['state'] == 'locked'
    assert remote.access.server is not None
    assert remote.request('/vault/status')[1] == {
        'supported': True, 'state': 'locked', 'unlock_available': True}
    for method, path, body in [('GET', '/sessions', None), ('GET', '/events', None),
        ('GET', '/devices', None), ('GET', '/diagnostics', None),
        ('POST', '/sessions', {'profile': 'fixture'}), ('POST', '/pair/complete', {'code': 'fixture'})]:
        status, reply, _ = remote.request(path, method=method, body=body)
        assert status == 423 and reply['error'] == 'vault_locked'
        assert 'fixture-private-value' not in json.dumps(reply)
    assert remote.request('/vault/status', credential='invalid')[0] == 401
    failed = remote.request('/vault/unlock', method='POST', body={'password': 'wrong-fixture-code'})
    assert failed[0] == 403 and failed[1]['error'] == 'authentication_failed'
    assert 'wrong-fixture-code' not in json.dumps(failed)
    assert remote.owner.lifecycle.state == 'locked'
    assert remote.bridge.store.authenticate(remote.paired['token']) is not None
    status, body, headers = remote.request('/vault/unlock', method='POST', body={'password': 'fixture-code'})
    assert status == 200 and body['state'] == 'unlocked' and headers['Cache-Control'] == 'no-store'
    assert remote.changes == [{'state': 'unlocked', 'interface_locked': False}]
    assert remote.request('/sessions')[0] == 200
    assert remote.owner.service.reveal_secret('fixture', 'fixture-code').secret == 'fixture-private-value'
    assert 'wrong-fixture-code' not in remote.bridge.diagnostics.path.read_text()


def test_listener_and_existing_device_survive_restart_without_new_qr(remote):
    remote.access.close()
    remote.owner.close()
    restarted = OwnerVault(remote.root)
    restored = DesktopMobileAccess(remote.bridge, remote.access.cert, remote.access.key, remote.root)
    remote.bridge.vault_access = RemoteVault(restarted)
    restarted.mobile_access = restored
    try:
        with patch.object(remote.bridge.store, 'create_pairing_code', side_effect=AssertionError('No new QR')):
            assert restored.restore()['enabled']
        remote.access = restored
        assert remote.request('/vault/status')[1]['state'] == 'locked'
        assert remote.request('/sessions')[0] == 423
        assert remote.request('/vault/unlock', method='POST', body={'password': 'fixture-code'})[0] == 200
        assert remote.request('/sessions')[0] == 200
    finally:
        restored.close()
        restarted.close()


def test_unlock_throttling_is_independent_of_valid_device_authentication(remote):
    remote.owner.dispatch({'id': 'lock', 'action': 'lock'})
    now = [100.0]
    remote.controller.clock = lambda: now[0]
    for _ in range(5):
        assert remote.request('/vault/unlock', method='POST', body={'password': 'incorrect'})[0] == 403
        assert remote.request('/vault/status')[0] == 200
    status, body, headers = remote.request('/vault/unlock', method='POST', body={'password': 'fixture-code'})
    assert status == 429 and int(headers['Retry-After']) == body['retry_after']
    assert remote.owner.lifecycle.state == 'locked'
    now[0] += 61
    assert remote.request('/vault/unlock', method='POST', body={'password': 'fixture-code'})[0] == 200


@pytest.mark.parametrize('body', [{}, {'password': []}, {'password': ''}, {'password': 'a' * 1025},
    {'password': 'fixture-code', 'action': 'create'}, {'password': 'fixture-code', 'profile': 'fixture'}])
def test_invalid_unlock_envelopes_never_open_vault(remote, body):
    remote.owner.dispatch({'id': 'lock', 'action': 'lock'})
    assert remote.request('/vault/unlock', method='POST', body=body)[0] == 400
    assert remote.owner.lifecycle.state == 'locked'


def test_browser_origins_revoked_devices_and_plain_http_cannot_unlock(remote):
    remote.owner.dispatch({'id': 'lock', 'action': 'lock'})
    assert remote.request('/vault/unlock', method='POST', body={'password': 'fixture-code'},
                          headers={'Origin': 'https://example.test'})[0] == 403
    remote.bridge.store.revoke_device(remote.paired['device_id'])
    assert remote.request('/vault/unlock', method='POST', body={'password': 'fixture-code'})[0] == 401
    server = build_server(remote.bridge, host='127.0.0.1', port=0)
    server.vault_access = remote.controller
    paired = remote.bridge.store.complete_pairing(code=remote.bridge.store.create_pairing_code()['code'],
                                                 name='Second fixture', platform='android')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        conn.request('POST', '/mobile/v1/vault/unlock', json.dumps({'password': 'fixture-code'}),
                     {'Authorization': 'Bearer ' + paired['token'], 'Content-Type': 'application/json'})
        reply = conn.getresponse()
        assert reply.status == 403 and json.loads(reply.read())['error'] == 'native_tls_required'
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert remote.owner.lifecycle.state == 'locked'


def test_concurrent_correct_requests_open_once_and_both_return_current_state(remote):
    remote.owner.dispatch({'id': 'lock', 'action': 'lock'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: remote.request('/vault/unlock', method='POST',
                            body={'password': 'fixture-code'}), range(2)))
    assert all(row[0] == 200 and row[1]['state'] == 'unlocked' for row in replies)
    assert len(remote.changes) == 1


def test_status_is_available_while_an_owner_settings_operation_is_busy(remote):
    with remote.owner._dispatch_lock:
        assert remote.request('/vault/status')[1]['state'] == 'unlocked'


def test_voice_cleanup_failure_does_not_prevent_full_vault_lock(remote, monkeypatch):
    remote.bridge.voice_devices['fixture-call'] = remote.paired['device_id']
    def fail(call):
        raise OSError('Synthetic failed hangup')
    monkeypatch.setattr(remote.bridge.voice, 'hangup', fail)
    reply = remote.owner.dispatch({'id': 'lock', 'action': 'lock'})
    assert reply['result']['state'] == 'locked'
    assert not remote.bridge.voice_devices
    assert remote.request('/vault/status')[1]['state'] == 'locked'


def test_expected_locked_and_incorrect_code_responses_do_not_create_connection_incidents(remote):
    remote.owner.dispatch({'id': 'lock', 'action': 'lock'})
    assert remote.request('/sessions')[0] == 423
    assert remote.request('/vault/unlock', method='POST', body={'password': 'incorrect'})[0] == 403
    assert remote.bridge.diagnostics.snapshot()['incidents'] == []
