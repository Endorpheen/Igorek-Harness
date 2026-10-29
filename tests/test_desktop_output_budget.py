"""Local output budgets must not break Desktop's credential binding."""
import json

import pytest

from test_desktop_agent_runtime import setup as setup, verify, FakeWorker
from factory.core.runtime import get_profile_config


def publish(root, config, value):
    folder = root / 'profiles'
    folder.mkdir(exist_ok=True)
    path = folder / (config['name'] + '.json')
    path.write_text(json.dumps({**config, 'max_output_tokens': value}))
    return path


@pytest.mark.parametrize('budget', [2048, 65536, 98304, None])
def test_desktop_starts_with_runtime_option_and_retains_vault_binding(setup, tmp_path, budget):
    owner, runtime, service, profile = setup
    verify(owner, profile)
    path = publish(tmp_path, profile['config'], budget)
    before = path.read_bytes()
    assert runtime.start(profile['credential_id'])['running']
    loaded = get_profile_config(profile['config']['name'], profiles_dir=path.parent)
    assert loaded['max_output_tokens'] == budget
    headers = FakeWorker.instances[-1].auth_headers_provider(loaded, 'openai')
    assert headers == {'Authorization': 'Bearer private-test-key'}
    assert path.read_bytes() == before
    bound = next(p for p in owner.lifecycle.list_provider_profiles() if p['credential_id'] == profile['credential_id'])
    assert bound['config'] == profile['config'] and bound['status'] == 'verified'


@pytest.mark.parametrize('bad', [True, False, 0, -1, '65536', 65536.5])
def test_invalid_runtime_budget_never_starts_a_worker(setup, tmp_path, bad):
    owner, runtime, service, profile = setup
    verify(owner, profile)
    publish(tmp_path, profile['config'], bad)
    count = len(FakeWorker.instances)
    with pytest.raises(ValueError, match='max_output_tokens'):
        runtime.start(profile['credential_id'])
    assert len(FakeWorker.instances) == count and not runtime.status()['running']


@pytest.mark.parametrize('field,value', [
    ('model', 'other'), ('base_url', 'https://other.invalid/v1'), ('api_format', 'anthropic'),
    ('env', {'PRIVATE': 'fixture'}), ('filesystem_mode', 'full'), ('unrecognized', True),
])
def test_output_option_does_not_authorize_other_profile_changes(setup, tmp_path, field, value):
    owner, runtime, service, profile = setup
    verify(owner, profile)
    path = publish(tmp_path, profile['config'], 65536)
    altered = {**json.loads(path.read_text()), field: value}
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError):
        runtime.start(profile['credential_id'])
    assert not runtime.status()['running']


def test_profile_update_preserves_local_budget_and_requires_new_verification(setup, tmp_path):
    owner, runtime, service, profile = setup
    verify(owner, profile)
    path = publish(tmp_path, profile['config'], 65536)
    reply = owner.providers.update(profile['credential_id'], profile['config']['name'],
        'changed-model', profile['config']['base_url'], 'openai', '',
        before_update=runtime.prepare_profile_update)
    saved = json.loads(path.read_text())
    assert saved == {**reply['config'], 'max_output_tokens': 65536}
    record = next(p for p in owner.lifecycle.list_provider_profiles() if p['credential_id'] == profile['credential_id'])
    assert record['status'] == 'unverified'
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified', 'reason': 'ok'})
    assert runtime.start(profile['credential_id'])['running']


def test_worker_auth_still_rejects_routing_mutation_with_a_valid_budget(setup, tmp_path):
    owner, runtime, service, profile = setup
    verify(owner, profile)
    publish(tmp_path, profile['config'], 65536)
    runtime.start(profile['credential_id'])
    headers = FakeWorker.instances[-1].auth_headers_provider
    with pytest.raises(ValueError):
        headers({**profile['config'], 'max_output_tokens': 65536, 'base_url': 'https://other.invalid/v1'}, 'openai')
