import http.client
import json
from pathlib import Path
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'desktop'))
from vault_owner import OwnerVault
from factory.core.cli_tasks import create_task
from factory.core.mobile_bridge.server import API_PREFIX, BridgeConfig, MobileBridge, build_server
from factory.core.mobile_bridge.service import MobileConflict, MobileInvalid, MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path / 'runtime'))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path / 'runtime'))
    initialize_database(get_db_path())
    owner = OwnerVault(tmp_path / 'owner')
    owner.service.create('fixture-profile-password')
    saved = {}
    for name, base, key in [('cloud', 'https://provider.invalid/v1', 'fixture-cloud-secret'),
                            ('local', 'http://127.0.0.1:8081/v1', '')]:
        saved[name] = owner.providers.save(name, name + '-model', base, 'openai', key)
        owner.service.check_provider_profile(saved[name]['credential_id'], lambda *_: {'status': 'verified', 'reason': 'fixture'})
    owner.providers.save('draft', 'draft-model', 'http://127.0.0.1:8081/v1', 'openai', '')
    workdir = tmp_path / 'project'; workdir.mkdir()
    service = MobileService(profile='cloud', workdir=str(workdir))
    class Agent:
        control_lock = threading.RLock()
        active = 'cloud'
        starts = []
        def status(self): return {'profile': self.active, 'running': bool(self.active)}
        def start(self, identity):
            profile = next(p for p in owner.providers.list_profiles() if p['credential_id'] == identity)
            self.active = profile['config']['name']; self.starts.append(self.active)
            service.profile = self.active
            return self.status()
        def close(self): pass
    agent = Agent()
    owner.agent = agent
    owner.browser_service = service
    service.available_profiles = lambda: [agent.active] if agent.active else []
    service.profile_control = owner.mobile_profile_control
    yield owner, service, agent, saved, workdir
    owner.close()


def test_paired_profile_catalog_and_switch_preserve_interface_pin_and_provider_credentials(setup):
    owner, service, agent, saved, _ = setup
    owner.interface_locked = True
    owner.pin_digest = b'fixture-pin-digest'
    catalog = service.session_options()
    assert catalog['profiles'] == ['cloud']
    assert {p['name'] for p in catalog['profile_catalog']} == {'cloud', 'local'}
    assert next(p for p in catalog['profile_catalog'] if p['name'] == 'local')['local'] is True
    assert 'fixture-cloud-secret' not in json.dumps(catalog)
    assert 'credential_id' not in json.dumps(catalog)
    service.activate_profile('local')
    service.activate_profile('cloud')
    assert agent.starts == ['local', 'cloud']
    assert owner.interface_locked and owner.pin_digest == b'fixture-pin-digest'
    assert owner.dispatch({'id': 'ui', 'action': 'start_agent', 'credential_id': saved['local']['credential_id']})['error'] == 'interface_locked'
    assert owner.lifecycle.provider_runtime_headers(saved['cloud']['credential_id'], saved['cloud']['config'], 'openai')['Authorization'] == 'Bearer fixture-cloud-secret'


@pytest.mark.parametrize('status', ['queued', 'processing', 'waiting_approval', 'waiting_user_input', 'cancelling'])
def test_switch_never_abandons_pending_or_running_tasks(setup, status):
    _, service, agent, _, _ = setup
    task = create_task(text='Keep this task', profile='cloud')
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute('UPDATE tasks SET status=? WHERE task_id=?', (status, task['task_id']))
    with pytest.raises(MobileConflict): service.activate_profile('local')
    assert agent.active == 'cloud' and agent.starts == []
    with sqlite3.connect(get_db_path()) as connection:
        assert connection.execute('SELECT status FROM tasks WHERE task_id=?', (task['task_id'],)).fetchone()[0] == status
    service.activate_profile('cloud')
    assert agent.active == 'cloud'


def test_locked_vault_and_unknown_or_unverified_profile_are_not_bypassed(setup):
    owner, service, agent, _, _ = setup
    for name in ['draft', 'unknown', '../local']:
        with pytest.raises(MobileInvalid): service.activate_profile(name)
    owner.service.lock()
    with pytest.raises(MobileInvalid): service.activate_profile('local')
    assert not service.session_options()['profile_catalog']
    assert agent.starts == []


def test_local_selection_preserves_existing_execution_mode_requirement(setup):
    from types import SimpleNamespace
    owner, service, agent, _, _ = setup
    owner.execution = SimpleNamespace(mode='kvm')
    with pytest.raises(MobileInvalid): service.activate_profile('local')
    assert agent.active == 'cloud'
    service.activate_profile('cloud')
    assert agent.starts == ['cloud']


def test_explicit_new_client_flag_switches_sessions_and_returns_to_the_original_profile(setup):
    _, service, agent, _, workdir = setup
    cloud = service.create_session(profile='cloud', cwd=str(workdir), idempotency_key='cloud')
    with pytest.raises(MobileInvalid): service.create_session(profile='local', cwd=str(workdir), idempotency_key='old-client')
    local = service.create_session(profile='local', cwd=str(workdir), idempotency_key='local', activate_profile=True)
    assert agent.active == 'local'
    assert service.create_session(profile='local', cwd=str(workdir), idempotency_key='local', activate_profile=True)['session_id'] == local['session_id']
    result = service.send_message(cloud['session_id'], 'Cloud task', idempotency_key='cloud-task', activate_profile=True)
    assert agent.active == 'cloud'
    with sqlite3.connect(get_db_path()) as connection:
        assert connection.execute('SELECT profile FROM tasks WHERE task_id=?', (result['task_id'],)).fetchone()[0] == 'cloud'
        connection.execute("UPDATE tasks SET status='ok' WHERE task_id=?", (result['task_id'],))
    local_task = service.send_message(local['session_id'], 'Local task', idempotency_key='local-task', activate_profile=True)
    assert agent.active == 'local'
    duplicate = service.send_message(cloud['session_id'], 'Cloud task', idempotency_key='cloud-task', activate_profile=True)
    assert duplicate['task_id'] == result['task_id'] and duplicate['duplicate']
    assert agent.active == 'local'


def test_legacy_standalone_bridge_remains_compatible_with_the_new_client_flag(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path / 'runtime'))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path / 'runtime'))
    initialize_database(get_db_path())
    service = MobileService(profile='cc-local-8081', workdir=str(tmp_path))
    assert service.session_options()['profile_switch_supported'] is False
    assert service.create_session(profile='cc-local-8081', cwd=str(tmp_path), activate_profile=True)['session_id']


def test_http_authentication_and_revocation_gate_profile_activation(setup):
    owner, service, agent, _, workdir = setup
    bridge = MobileBridge(config=BridgeConfig(profile='cloud', workdir=str(workdir)))
    bridge.service = service
    server = build_server(bridge, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def request(method, path, body=None, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=10)
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        connection.request(method, API_PREFIX + path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse(); raw = response.read(); status = response.status; connection.close()
        return status, json.loads(raw) if raw else {}
    try:
        payload = {'profile': 'local', 'cwd': str(workdir), 'activate_profile': True, 'idempotency_key': 'paired-local'}
        assert request('POST', '/sessions', payload)[0] == 401
        assert agent.starts == []
        code = bridge.store.create_pairing_code()['code']
        status, paired = request('POST', '/pair/complete', {'code': code, 'name': 'Fixture phone'})
        assert status == 201
        owner.interface_locked = True
        assert request('POST', '/sessions', payload, paired['token'])[0] == 201
        assert agent.active == 'local' and owner.interface_locked
        bridge.revoke_device(paired['device_id'])
        assert request('POST', '/sessions', {**payload, 'profile': 'cloud'}, paired['token'])[0] == 401
        assert agent.starts == ['local']
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
