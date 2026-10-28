"""OpenRouter chat choices use the existing bound provider catalogue."""
import json
import sqlite3

import pytest

from test_mobile_profile_switch import setup as setup
from factory.core.mobile_bridge.service import MobileInvalid
from factory.core.runtime import get_db_path


@pytest.fixture
def router(setup, monkeypatch):
    import provider_setup
    owner, service, agent, saved, workdir = setup
    profile = owner.providers.save('router', 'vendor/current', provider_setup.PRESETS['openrouter'][0], 'openai', 'fixture-router-key')
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified'})
    calls = []
    rows = [{'id': f'vendor/model-{i:03}', 'name': f'Model {i:03}',
             'supported_parameters': ['tools'], 'pricing': {'prompt': '1', 'completion': '2'}} for i in range(300)]
    rows += [{'id': 'vendor/free:free', 'name': 'Free model', 'supported_parameters': ['tools'],
              'pricing': {'prompt': '0', 'completion': '0'}},
             {'id': 'vendor/no-tools', 'supported_parameters': []}]
    def fetch(url, key=''):
        calls.append((url, key))
        return {'data': rows}
    monkeypatch.setattr(provider_setup, 'catalog_json', fetch)
    return owner, service, agent, profile, workdir, calls


def test_router_models_reach_chat_options_without_the_old_256_model_cutoff(router):
    owner, service, _, _, _, calls = router
    options = service.session_options(model_profile='router')
    choice = next(p for p in options['profile_catalog'] if p['name'] == 'router')
    assert choice['models_status'] == 'ready'
    assert len(choice['models']) == 301
    assert choice['models'][0] == 'vendor/free:free'
    assert choice['model_labels']['vendor/model-299'] == 'Model 299'
    assert 'vendor/no-tools' not in choice['models']
    assert calls == [('https://openrouter.ai/api/v1/models', 'fixture-router-key')]
    assert 'fixture-router-key' not in json.dumps(options)
    assert owner.mobile_profile_control('models', 'router')['models'] == choice['models']
    assert len(calls) == 1


def test_router_model_selection_preserves_profile_and_routes_next_message(router):
    _, service, agent, _, workdir, _ = router
    sid = service.create_session(profile='router', cwd=str(workdir), activate_profile=True)['session_id']
    starts = list(agent.starts)
    changed = service.switch_session_model(sid, 'router', 'vendor/model-299')
    assert changed['meta']['profile'] == 'router'
    assert changed['meta']['model'] == 'vendor/model-299'
    assert agent.starts == starts
    task = service.send_message(sid, 'Use selected model')
    with sqlite3.connect(get_db_path()) as db:
        profile, metadata = db.execute('SELECT profile,metadata FROM tasks WHERE task_id=?', (task['task_id'],)).fetchone()
    assert profile == 'router' and json.loads(metadata)['model_override'] == 'vendor/model-299'


def test_failed_router_catalog_preserves_current_model_and_rejects_unknown_selection(router, monkeypatch):
    import provider_setup
    owner, service, _, _, workdir, _ = router
    monkeypatch.setattr(provider_setup, 'catalog_json', lambda *_: (_ for _ in ()).throw(OSError('fixture unavailable')))
    sid = service.create_session(profile='router', cwd=str(workdir), activate_profile=True)['session_id']
    choice = next(p for p in service.session_options(model_profile='router')['profile_catalog'] if p['name'] == 'router')
    assert choice['models_status'] == 'unavailable'
    assert choice['models'] == ['vendor/current']
    before = service.get_session(sid)['meta']
    with pytest.raises(MobileInvalid):
        service.switch_session_model(sid, 'router', 'vendor/unknown')
    assert service.get_session(sid)['meta'] == before
    owner.service.lock()
    assert owner.mobile_profile_control('models', 'router') == {'error': 'vault_locked'}


@pytest.mark.parametrize('base,api_format', [
    ('https://openrouter.ai.evil.invalid/api/v1', 'openai'),
    ('https://openrouter.ai/api/v1', 'anthropic'),
    ('https://provider.invalid/v1', 'openai'),
])
def test_other_endpoints_are_not_given_the_openrouter_catalog(setup, monkeypatch, base, api_format):
    import provider_setup
    owner, _, _, _, _ = setup
    profile = owner.providers.save('other', 'model', base, api_format, 'fixture-key')
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified'})
    monkeypatch.setattr(provider_setup, 'catalog_json', lambda *_: pytest.fail('Unrelated provider fetched'))
    assert owner.mobile_profile_control('models', 'other')['models_status'] == 'unsupported'
