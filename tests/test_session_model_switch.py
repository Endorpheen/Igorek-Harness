import json
import sqlite3

import pytest

from test_mobile_profile_switch import setup as setup
from factory.core.mobile_bridge.service import MobileConflict, MobileInvalid
from factory.core.runtime import get_db_path


@pytest.fixture
def catalog(setup):
    owner, service, agent, saved, workdir = setup
    original = service.profile_control
    def control(operation, name):
        if operation == 'models':
            return {'models': ['gpt-6.1-sol', 'gpt-6.1-terra'], 'models_status': 'ready'} if name == 'cloud' else {'models': [], 'models_status': 'unsupported'}
        result = original(operation, name)
        if operation == 'catalog':
            for entry in result.get('profiles', []):
                entry['models'] = ['gpt-6.1-sol', 'gpt-6.1-terra'] if entry['name'] == 'cloud' else []
        return result
    service.profile_control = control
    return owner, service, agent, saved, workdir


def test_model_switch_keeps_worker_chat_history_and_routes_next_task(catalog):
    _, service, agent, _, workdir = catalog
    sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    first = service.send_message(sid, 'First message')
    with sqlite3.connect(get_db_path()) as db:
        db.execute("UPDATE tasks SET status='ok' WHERE task_id=?", (first['task_id'],))
    before = service.get_session(sid)
    changed = service.switch_session_model(sid, 'cloud', 'gpt-6.1-terra')
    assert agent.starts == []
    assert changed['session_id'] == sid and changed['messages'] == before['messages']
    assert changed['meta']['profile'] == 'cloud' and changed['meta']['model'] == 'gpt-6.1-terra'
    next_task = service.send_message(sid, 'Next message')
    with sqlite3.connect(get_db_path()) as db:
        row = db.execute('SELECT profile,metadata FROM tasks WHERE task_id=?', (next_task['task_id'],)).fetchone()
        db.execute("UPDATE tasks SET status='ok' WHERE task_id=?", (next_task['task_id'],))
    assert row[0] == 'cloud' and json.loads(row[1])['model_override'] == 'gpt-6.1-terra'
    local = service.switch_session_profile(sid, 'local')
    assert local['meta']['model'] == 'local-model' and not local['meta'].get('model_override')
    task = service.send_message(sid, 'Local message')
    with sqlite3.connect(get_db_path()) as db:
        row = db.execute('SELECT metadata FROM tasks WHERE task_id=?', (task['task_id'],)).fetchone()
    assert 'model_override' not in json.loads(row[0])


@pytest.mark.parametrize('profile,model', [
    ('cloud', 'unknown'), ('local', 'gpt-6.1-terra'), ('cloud', ''), ('cloud', None),
])
def test_switch_rejects_unavailable_or_stale_choices(catalog, profile, model):
    _, service, agent, _, workdir = catalog
    sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    before = service.get_session(sid)['meta']['model']
    with pytest.raises((MobileConflict, MobileInvalid)):
        service.switch_session_model(sid, profile, model)
    assert agent.starts == [] and service.get_session(sid)['meta']['model'] == before


@pytest.mark.parametrize('status', ['queued', 'processing', 'waiting_approval', 'waiting_user_input', 'cancelling'])
def test_switch_refuses_busy_chat(catalog, status):
    _, service, agent, _, workdir = catalog
    sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    before = service.get_session(sid)['meta']['model']
    task = service.send_message(sid, 'Running')
    with sqlite3.connect(get_db_path()) as db:
        db.execute('UPDATE tasks SET status=? WHERE task_id=?', (status, task['task_id']))
    with pytest.raises(MobileConflict):
        service.switch_session_model(sid, 'cloud', 'gpt-6.1-terra')
    assert agent.starts == [] and service.get_session(sid)['meta']['model'] == before


def test_locked_vault_disables_model_switch(catalog):
    owner, service, agent, _, workdir = catalog
    sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    owner.service.lock()
    with pytest.raises(MobileInvalid):
        service.switch_session_model(sid, 'cloud', 'gpt-6.1-terra')
    assert agent.starts == []


def test_catalog_exposes_pair_only_for_compatible_verified_profile(setup, monkeypatch):
    owner, _, _, _, _ = setup
    def entry(name, model, api_format='openai-responses', status='verified'):
        return {'status': status, 'credential_id': name, 'config': {
            'name': name, 'model': model, 'api_format': api_format,
            'backend': 'cc-backend', 'base_url': 'https://api.openai.com/v1'}}
    monkeypatch.setattr(owner.providers, 'list_profiles', lambda: [
        entry('compatible', 'gpt-6.1-sol'), entry('other-format', 'gpt-6.1-sol', 'openai'),
        entry('other-model', 'unrelated-model'), entry('unverified', 'gpt-6.1-sol', status='unverified')])
    monkeypatch.setattr(owner.chatgpt, 'catalog', lambda _: {'models': [{'id':'fixture-new','name':'New model'}]})
    live = owner.mobile_profile_control('models', 'compatible')
    assert live['models'] == ['fixture-new'] and live['models_status'] == 'ready'
    result = owner.mobile_profile_control('catalog', '')
    items = {item['name']: item for item in result['profiles']}
    assert items['compatible']['models'] == ['fixture-new']
    assert items['other-format'].get('models', []) == []
    assert owner.mobile_profile_control('models', 'other-model')['models'] == ['fixture-new']
    assert 'unverified' not in items


def test_go_model_readback_matches_the_confirmed_wire_id_migration(setup):
    from factory.core.runtime import get_profile_path
    from factory.core.cli_chat import update_session_meta
    _, service, _, _, workdir = setup
    config = {'name': 'cloud', 'backend': 'cc-backend', 'provider': 'custom', 'api_format': 'openai',
              'base_url': 'https://opencode.ai/zen/go/v1', 'model': 'deepseek-v4-flash', 'env': {}}
    path = get_profile_path('cloud');path.parent.mkdir(parents=True, exist_ok=True);path.write_text(json.dumps(config))
    sid = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    from factory.core.cli_chat import _session_path
    update_session_meta(_session_path(sid), {'model': 'deepseek-v4-flash', 'model_override': 'deepseek-v4-flash'})
    result = service.get_session(sid)
    assert result['model'] == 'deepseek-v4.1-flash'
    assert result['meta']['model'] == 'deepseek-v4.1-flash'
    assert result['meta']['model_override'] == 'deepseek-v4.1-flash'
