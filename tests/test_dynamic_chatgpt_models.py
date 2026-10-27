import http.client
import json
import sqlite3
import threading
from unittest import mock

import pytest

from test_mobile_profile_switch import setup as setup
from factory.core.chatgpt_auth import ChatGPTAuthError
from factory.core.mobile_bridge.service import MobileInvalid
from factory.core.runtime import get_db_path
from vault_owner import _profile_provider_icon


@pytest.mark.parametrize(('url', 'api_format', 'expected'), [
    ('https://api.openai.com/v1', 'openai', 'openai'),
    ('https://provider.invalid/v1', 'openai-responses', 'openai'),
    ('https://openrouter.ai/api/v1', 'openai', 'openrouter'),
    ('https://opencode.ai/zen/go/v1', 'openai', 'opencode'),
    ('https://api.z.ai/api/coding/paas/v4', 'openai', 'zai'),
    ('https://api.inceptionlabs.ai/v1', 'openai', 'inception'),
    ('http://127.0.0.1:8081/v1', 'openai', 'local'),
    ('https://api.openai.com.attacker.invalid/v1', 'openai', 'custom'),
    ('http://[malformed', 'openai', 'custom'),
])
def test_provider_icons_use_connection_identity_without_guessing_from_names(url, api_format, expected):
    assert _profile_provider_icon({'base_url': url, 'api_format': api_format, 'name': 'openai'}) == expected


@pytest.fixture
def chatgpt(setup, monkeypatch):
    owner, service, agent, saved, workdir = setup
    original = owner.providers.list_profiles
    def profiles():
        rows = original()
        for row in rows:
            if row['config']['name'] == 'cloud':
                row['config']['api_format'] = 'openai-responses'
                row['config']['model'] = 'fixture-default'
        return rows
    monkeypatch.setattr(owner.providers, 'list_profiles', profiles)
    catalog = mock.Mock(return_value={'models':[
        {'id':'fixture-default', 'name':'Default model'},
        {'id':'fixture-new', 'name':'New model'},
    ]})
    monkeypatch.setattr(owner.chatgpt, 'catalog', catalog)
    return owner, service, agent, workdir, catalog


def test_model_choices_come_from_live_account_and_reuse_cache(chatgpt):
    owner, service, _, _, catalog = chatgpt
    result = service.session_options(model_profile='cloud')
    row = next(item for item in result['profile_catalog'] if item['name'] == 'cloud')
    assert row['model'] == 'fixture-default'
    assert row['provider_icon'] == 'openai'
    assert next(item for item in result['profile_catalog'] if item['name'] == 'local')['provider_icon'] == 'local'
    assert all('base_url' not in item for item in result['profile_catalog'])
    assert row['models'] == ['fixture-default', 'fixture-new']
    assert row['model_labels']['fixture-new'] == 'New model'
    assert row['models_status'] == 'ready'
    assert service.session_options(model_profile='cloud')['profile_catalog']
    assert catalog.call_count == 1
    assert owner.mobile_profile_control('models', 'local').get('models', []) == []


def test_opencode_go_choices_include_models_for_all_catalog_transports(setup, monkeypatch):
    from factory.core import opencode_go_catalog
    owner, service, agent, _, workdir = setup
    original = owner.providers.list_profiles
    def profiles():
        rows = original()
        for row in rows:
            if row['config']['name'] == 'cloud':
                row['config'].update(base_url='https://opencode.ai/zen/go/v1', api_format='openai')
        return rows
    monkeypatch.setattr(owner.providers, 'list_profiles', profiles)
    fetch = mock.Mock(return_value={
        'deepseek-v4.1-flash': ('openai', 'https://opencode.ai/inference/go/openai/v1', 'DeepSeek'),
        'qwen3.8-max': ('anthropic', 'https://opencode.ai/inference/go/anthropic/v1', 'Qwen'),
    })
    monkeypatch.setattr(opencode_go_catalog, 'get_opencode_go_routes', fetch)
    row = next(item for item in service.session_options(model_profile='cloud')['profile_catalog'] if item['name'] == 'cloud')
    assert row['models'] == ['deepseek-v4.1-flash', 'qwen3.8-max']
    assert row['model_labels'] == {'deepseek-v4.1-flash': 'DeepSeek', 'qwen3.8-max': 'Qwen'}
    sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    assert service.switch_session_model(sid, 'cloud', 'deepseek-v4.1-flash')['meta']['model'] == 'deepseek-v4.1-flash'
    assert service.switch_session_model(sid, 'cloud', 'qwen3.8-max')['meta']['model'] == 'qwen3.8-max'
    with pytest.raises(MobileInvalid):
        service.switch_session_model(sid, 'cloud', 'unknown')
    assert fetch.call_count == 1 and agent.starts == []
    owner._models_cache.clear()
    fetch.return_value = {}
    row = next(item for item in service.session_options(model_profile='cloud')['profile_catalog'] if item['name'] == 'cloud')
    assert row['models_status'] == 'unavailable'
    with pytest.raises(MobileInvalid):
        service.switch_session_model(sid, 'cloud', 'deepseek-v4.1-flash')
    fetch.return_value = {
        'qwen3.8-max': ('anthropic', 'https://opencode.ai/inference/go/anthropic/v1', 'Qwen')}
    owner._models_cache.clear()
    task = service.send_message(sid, 'Next task')
    with sqlite3.connect(get_db_path()) as db:
        metadata = json.loads(db.execute('SELECT metadata FROM tasks WHERE task_id=?', (task['task_id'],)).fetchone()[0])
    assert metadata['model_override'] == 'qwen3.8-max'


def test_selected_model_reaches_next_task_without_restarting_worker(chatgpt):
    _, service, agent, workdir, _ = chatgpt
    sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    service.session_options(model_profile='cloud')
    with pytest.raises(MobileInvalid):
        service.switch_session_model(sid, 'cloud', 'not-in-catalog')
    changed = service.switch_session_model(sid, 'cloud', 'fixture-new')
    assert changed['meta']['model'] == 'fixture-new' and agent.starts == []
    task = service.send_message(sid, 'Next task')
    with sqlite3.connect(get_db_path()) as db:
        metadata = json.loads(db.execute('SELECT metadata FROM tasks WHERE task_id=?', (task['task_id'],)).fetchone()[0])
    assert metadata['model_override'] == 'fixture-new'


def test_unavailable_catalog_preserves_config_and_refuses_unknown_models(chatgpt):
    owner, service, agent, workdir, catalog = chatgpt
    catalog.side_effect = ChatGPTAuthError('chatgpt_network')
    sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    before = service.get_session(sid)['meta']['model']
    result = service.session_options(model_profile='cloud')
    row = next(item for item in result['profile_catalog'] if item['name'] == 'cloud')
    assert row['models_status'] == 'unavailable'
    assert row['models'] == ['fixture-default']
    with pytest.raises(MobileInvalid):
        service.switch_session_model(sid, 'cloud', 'fixture-new')
    assert service.get_session(sid)['meta']['model'] == before
    restored = service.switch_session_model(sid, 'cloud', 'fixture-default')
    assert restored['meta']['model'] == 'fixture-default'
    assert agent.starts == [] and owner.providers.list_profiles()[0]['config']['name']


def test_unrelated_session_options_do_not_wait_for_slow_catalog(chatgpt):
    owner, service, _, _, catalog = chatgpt
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    result = []
    def slow(_):
        started.set()
        assert release.wait(5)
        return {'models': [{'id': 'fixture-new', 'name': 'New model'}]}
    catalog.side_effect = slow
    worker = threading.Thread(target=lambda: owner.mobile_profile_control('models', 'cloud'))
    worker.start()
    assert started.wait(3)
    def options():
        result.append(service.session_options())
        finished.set()
    probe = threading.Thread(target=options)
    probe.start()
    try:
        assert finished.wait(2), 'Unrelated session options waited for the network'
        row = next(item for item in result[0]['profile_catalog'] if item['name'] == 'cloud')
        assert row['model'] == 'fixture-default'
    finally:
        release.set()
        worker.join(5)
        probe.join(5)
    assert not worker.is_alive() and not probe.is_alive()


def test_paired_http_options_returns_only_selected_account_models(chatgpt):
    from factory.core.mobile_bridge.server import API_PREFIX, BridgeConfig, MobileBridge, build_server
    _, service, _, workdir, _ = chatgpt
    bridge = MobileBridge(config=BridgeConfig(profile='cloud', workdir=str(workdir)))
    bridge.service = service
    server = build_server(bridge, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(method, path, body=None, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=10)
        headers = {'Content-Type':'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        connection.request(method, API_PREFIX + path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse(); payload = response.read(); status = response.status
        connection.close()
        return status, json.loads(payload) if payload else {}
    try:
        assert request('GET', '/session-options?model_profile=cloud')[0] == 401
        code = bridge.store.create_pairing_code()['code']
        status, paired = request('POST', '/pair/complete', {'code':code, 'name':'Fixture phone'})
        assert status == 201
        status, options = request('GET', '/session-options?model_profile=cloud', token=paired['token'])
        assert status == 200
        row = next(item for item in options['profile_catalog'] if item['name'] == 'cloud')
        assert row['models'] == ['fixture-default', 'fixture-new']
        assert 'credential_id' not in json.dumps(options)
        sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
        status, response = request('POST', '/sessions/'+sid+'/model', {'profile':'cloud','model':'fixture-new'}, paired['token'])
        assert status == 200 and response['meta']['model'] == 'fixture-new'
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
        assert not thread.is_alive()


def test_catalog_refresh_exposes_new_models_without_replacing_default(chatgpt):
    owner, service, agent, workdir, catalog = chatgpt
    sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    service.session_options(model_profile='cloud')
    identity = next(item['credential_id'] for item in owner.providers.list_profiles() if item['config']['name'] == 'cloud')
    owner._models_cache[identity]['expires'] = 0
    catalog.return_value = {'models':[{'id':'fixture-replacement', 'name':'Replacement'}]}
    row = next(item for item in service.session_options(model_profile='cloud')['profile_catalog'] if item['name'] == 'cloud')
    assert row['models'] == ['fixture-replacement']
    assert row['model'] == 'fixture-default'
    with pytest.raises(MobileInvalid):
        service.switch_session_model(sid, 'cloud', 'fixture-new')
    assert service.switch_session_model(sid, 'cloud', 'fixture-replacement')['meta']['model'] == 'fixture-replacement'
    assert agent.starts == []


def test_rechecks_model_availability_if_profile_changes_during_request(chatgpt):
    _, service, agent, workdir, _ = chatgpt
    sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    control = service.profile_control
    calls = 0
    def changing(operation, name):
        nonlocal calls
        if operation == 'models':
            calls += 1
            return {'models_status':'ready', 'models':['fixture-new' if calls == 1 else 'different-model']}
        return control(operation, name)
    service.profile_control = changing
    before = service.get_session(sid)['meta']['model']
    with pytest.raises(MobileInvalid):
        service.switch_session_model(sid, 'cloud', 'fixture-new')
    assert calls == 2 and service.get_session(sid)['meta']['model'] == before
    assert agent.starts == []


def test_starting_chatgpt_profile_warms_catalog_without_waiting(chatgpt, monkeypatch):
    owner, service, agent, _, catalog = chatgpt
    started, release = threading.Event(), threading.Event()
    def slow(_):
        started.set()
        assert release.wait(5)
        return {'models':[{'id':'fixture-new','name':'New model'}]}
    catalog.side_effect = slow
    monkeypatch.setattr(owner.image, 'status', lambda: {'running':False})
    identity = next(item['credential_id'] for item in owner.providers.list_profiles() if item['config']['name'] == 'cloud')
    try:
        assert owner._start_agent_profile(identity) is None
        assert started.wait(3)
        assert agent.starts == ['cloud']
    finally:
        release.set()
        profile = next(item for item in owner.providers.list_profiles() if item['config']['name'] == 'cloud')
        assert owner._chatgpt_models(profile)['models'] == ['fixture-new']
        assert not owner._models_pending
        row = next(item for item in service.session_options()['profile_catalog'] if item['name'] == 'cloud')
        assert row['models'] == ['fixture-new']
