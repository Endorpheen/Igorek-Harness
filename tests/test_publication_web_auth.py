"""Local Web UI operations validate the paired device, including revocation."""
import http.client
import json
import threading

import pytest

from factory.core.mobile_bridge.server import BridgeConfig, MobileBridge, build_server
from factory.core.mobile_bridge.store import MobileStore
from factory.web_ui import server as module


@pytest.fixture
def web(tmp_path):
    bridge = MobileBridge(config=BridgeConfig(workdir=str(tmp_path)),
                          store=MobileStore(str(tmp_path / 'factory.db')))
    upstream = build_server(bridge, host='127.0.0.1', port=0)
    local = module._Server(0, f'http://127.0.0.1:{upstream.server_port}')
    local.profile_usage_provider = lambda *args, **kwargs: {'provider': 'fixture', 'usage': {}}
    local.logs_provider = lambda *args: {'lines': ['private fixture']}
    local.logs_status_provider = lambda: {'units': ['fixture']}
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (upstream, local)]
    for thread in threads:
        thread.start()
    yield local, bridge
    for server in (local, upstream):
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join(timeout=2)
    bridge.stop()


def request(server, path, token):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
    connection.request('GET', path, headers={'Cookie': f'{module._COOKIE}={token}'})
    response = connection.getresponse()
    value = response.status, response.read()
    connection.close()
    return value


@pytest.mark.parametrize('path', ['/api/provider-usage?profile=fixture', '/api/model-info?profile=fixture',
                                   '/api/logs?unit=fixture', '/api/logs/status',
                                   '/api/sessions/session-fixture/tree'])
def test_forged_cookie_cannot_reach_local_operations(web, path):
    server, _ = web
    assert request(server, path, 'forged-fixture')[0] == 401


def test_valid_pairing_is_accepted_and_revocation_takes_effect_without_web_restart(web):
    server, bridge = web
    created = bridge.store.create_pairing_code()
    device = bridge.store.complete_pairing(code=created['code'], name='Browser fixture')
    status, data = request(server, '/api/provider-usage?profile=fixture', device['token'])
    assert status == 200
    assert json.loads(data)['provider'] == 'fixture'
    bridge.store.revoke_device(device['device_id'])
    assert request(server, '/api/provider-usage?profile=fixture', device['token'])[0] == 401
