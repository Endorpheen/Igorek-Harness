import importlib.util
import json
import socket
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from factory.core.host_services import HostServices, UnixHTTPConnection
from factory.core.isolation.errors import IsolationError

spec = importlib.util.spec_from_file_location('opencode_host_services', Path(__file__).parents[1] / 'desktop/opencode_host_services.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def endpoint(tmp_path):
    root = tmp_path / 'services'
    root.mkdir(mode=0o700)
    output = tmp_path / 'outputs'
    project = tmp_path / 'project'
    output.mkdir()
    project.mkdir()
    cfg = {'version': 1, 'name': 'demo', 'unit': 'igorek-host-service-demo.service',
           'socket': str(root / 'service.sock'), 'token': 'fixture-secret', 'outputs': str(output),
           'output_extensions': ['.wav'], 'tools': {'generate': {'input_schema': {
               'type': 'object', 'properties': {'seconds': {'type': 'integer', 'minimum': 5, 'maximum': 120}},
               'required': ['seconds']}}}}
    config = root / 'demo.json'
    config.write_text(json.dumps(cfg))
    config.chmod(0o600)
    state = {'unlocked': True}
    manager = HostServices(root, allowed=lambda profile=None: state['unlocked'],
                           runner=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='ActiveState=inactive\nSubState=dead\nMainPID=0\n'))
    manager.install()
    server = module.HostServicesMcp(tmp_path / 'mcp', manager)
    grant = server.configure('grant', {'services': ['demo'], 'projects': [str(project)]})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, manager, grant, project, output, state
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()


def request(endpoint, method, params=None, token=None):
    server, manager, grant, project, output, state = endpoint
    message = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params is not None:
        message['params'] = params
    conn = UnixHTTPConnection(str(server.path))
    try:
        try:
            conn.request('POST', '/mcp', json.dumps(message), {'Authorization': 'Bearer ' + (grant['token'] if token is None else token)})
        except BrokenPipeError:
            # A header-level denial may finish before the client uploads its body.
            # Read and assert the HTTP response rather than retrying the request.
            pass
        response = conn.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if response.status == 200 else None
    finally:
        conn.close()


def call(endpoint, arguments, **extra):
    return request(endpoint, 'tools/call', {'name': 'host_service', 'arguments': arguments, **extra})[1]


def test_mcp_handshake_catalog_and_scope_do_not_disclose_service_credentials(endpoint):
    assert request(endpoint, 'initialize')[1]['result']['capabilities'] == {'tools': {}}
    reply = request(endpoint, 'tools/list')[1]
    tool = reply['result']['tools'][0]
    assert tool['name'] == 'host_service'
    assert tool['inputSchema']['properties']['service']['enum'] == ['demo']
    assert 'fixture-secret' not in json.dumps(reply)
    assert endpoint[2]['token'] not in json.dumps(reply)
    assert call(endpoint, {'action': 'describe', 'service': 'demo'})['result']['structuredContent']['tools']['generate']


def test_bad_tokens_lock_and_revocation_fail_closed(endpoint):
    assert request(endpoint, 'tools/list', token='wrong')[0] == 403
    endpoint[-1]['unlocked'] = False
    assert request(endpoint, 'tools/list')[0] == 403
    with pytest.raises(ValueError):
        endpoint[0].configure('grant', {'services': ['demo'], 'projects': [str(endpoint[3])]})
    endpoint[0].configure('revoke', {'token': endpoint[2]['token']})
    endpoint[-1]['unlocked'] = True
    assert request(endpoint, 'tools/list')[0] == 403


@pytest.mark.parametrize('fields', [
    {'action': 'invoke', 'service': 'other', 'method': 'generate', 'arguments': {'seconds': 5}},
    {'action': 'invoke', 'service': 'demo', 'method': 'shell', 'arguments': {}},
    {'action': 'invoke', 'service': 'demo', 'method': 'generate', 'arguments': {'seconds': 121}},
    {'action': 'invoke', 'service': 'demo', 'method': 'generate', 'arguments': {'seconds': 5, 'command': 'id'}},
    {'action': 'status', 'service': 'demo', 'url': 'http://localhost/private'},
    {'action': 'status', 'service': 'demo', 'workspace': '/etc'},
])
def test_mcp_cannot_widen_enrolled_methods_network_or_filesystem(endpoint, fields):
    with patch.object(endpoint[1], 'rpc') as rpc:
        assert 'error' in call(endpoint, fields)
        rpc.assert_not_called()


def test_valid_invocation_accepts_opencode_metadata_and_preserves_server_policy(endpoint):
    with patch.object(endpoint[1], 'rpc', return_value={'structuredContent': {'job_id': 'fixed'}}) as rpc:
        reply = call(endpoint, {'action': 'invoke', 'service': 'demo', 'method': 'generate', 'arguments': {'seconds': 120}},
                     _meta={'ai.opencode/sessionID': 'ses_fixture'})
        assert reply['result']['structuredContent'] == {'job_id': 'fixed'}
        assert rpc.call_args.args[2] == {'name': 'generate', 'arguments': {'seconds': 120}}


def test_files_export_reuses_safe_result_identity_and_approved_workspace(endpoint):
    source = endpoint[4] / 'result.wav'
    source.write_bytes(b'fixture-audio')
    result = call(endpoint, {'action': 'files', 'service': 'demo'})['result']['structuredContent']['files'][0]
    exported = call(endpoint, {'action': 'export', 'service': 'demo', 'file_id': result['id']})['result']['structuredContent']
    assert Path(exported['path']).parent == endpoint[3] / 'service_results'
    assert Path(exported['path']).read_bytes() == source.read_bytes()


def test_grant_cannot_use_an_unregistered_service_or_sensitive_workspace(endpoint):
    server = endpoint[0]
    with pytest.raises(FileNotFoundError):
        server.configure('grant', {'services': ['unknown'], 'projects': [str(endpoint[3])]})
    with pytest.raises(IsolationError, match='sensitive_path_denied'):
        server.configure('grant', {'services': ['demo'], 'projects': ['/etc']})


def test_active_socket_is_preserved_and_dead_socket_is_recovered(tmp_path):
    manager = SimpleNamespace(allowed=lambda profile=None: True)
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(root / 'api.sock'))
    stale.close()
    server = module.HostServicesMcp(root, manager)
    try:
        with pytest.raises(ValueError, match='already_running'):
            module.HostServicesMcp(root, manager)
    finally:
        server.server_close()


def test_owner_delegation_requires_unlock_and_revocation_survives_interface_lock(endpoint, tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1] / 'desktop'))
    from vault_owner import OwnerVault
    owner = OwnerVault(tmp_path / 'owner')
    owner.opencode_services = endpoint[0]
    endpoint[1].allowed = lambda profile=None: owner.service.status()['state'] == 'unlocked' and not owner.interface_locked
    config = json.dumps({'services': ['demo'], 'projects': [str(endpoint[3])]}) + ' ' * 2000
    request = {'id': 'delegate', 'action': 'opencode_services', 'operation': 'grant', 'config': config}
    try:
        assert 'error' in owner.dispatch(request)
        owner.service.create('fixture-password')
        granted = owner.dispatch(request)['result']
        assert len(granted['token']) == 64
        owner.interface_locked = True
        assert owner.dispatch(request)['error'] == 'interface_locked'
        revoked = owner.dispatch({'id': 'revoke', 'action': 'opencode_services', 'operation': 'revoke',
                                  'config': json.dumps({'token': granted['token']})})
        assert revoked['result'] == {'revoked': True}
        assert not endpoint[0].authorized(granted['token'])
    finally:
        owner.close()


def test_revocation_does_not_wait_for_a_running_service_call(endpoint):
    server, manager, grant, *_ = endpoint
    entered, release = threading.Event(), threading.Event()
    def pending(*args):
        entered.set()
        release.wait(3)
        return {'structuredContent': {'job_id': 'admitted'}}
    message = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'host_service', 'arguments': {'action': 'invoke', 'service': 'demo',
        'method': 'generate', 'arguments': {'seconds': 5}}}}
    with patch.object(manager, 'rpc', side_effect=pending):
        caller = threading.Thread(target=server.dispatch, args=(grant['token'], message))
        caller.start()
        assert entered.wait(1)
        revoked = threading.Event()
        threading.Thread(target=lambda: (server.configure('revoke', {'token': grant['token']}), revoked.set())).start()
        try:
            assert revoked.wait(.5)
            assert not server.authorized(grant['token'])
        finally:
            release.set()
            caller.join(timeout=2)
