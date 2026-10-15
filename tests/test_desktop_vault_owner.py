import importlib.util
import base64
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
_spec = importlib.util.spec_from_file_location('vault_owner', Path(__file__).resolve().parents[1] / 'desktop/vault_owner.py')
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


def test_mobile_network_configuration_is_owner_only_and_has_exact_schema(tmp_path):
    from types import SimpleNamespace
    owner=_module.OwnerVault(tmp_path)
    calls=[]
    owner.mobile_access=SimpleNamespace(restore=lambda:None,configure=lambda port,public_url:calls.append((port,public_url)) or {'port':int(port),'public_url':public_url})
    request={'id':'configure-mobile','action':'mobile_configure','port':'47823','public_url':''}
    try:
        assert owner.dispatch(request)['error']=='vault_locked'
        owner.dispatch({'id':'create','action':'create','password':'fixture-password'})
        reply=owner.dispatch(request)
        assert reply['result']['port']==47823 and calls==[('47823','')]
        assert owner.dispatch({**request,'url':'https://untrusted.invalid'})['error']=='invalid_request'
        assert owner.dispatch({key:value for key,value in request.items() if key!='port'})['error']=='invalid_request'
        owner.interface_locked=True
        assert owner.dispatch(request)['error']=='interface_locked'
    finally:
        owner.mobile_access=None;owner.close()


@pytest.mark.parametrize('reason', ['desktop_socket_policy_missing', 'desktop_socket_policy_unavailable', 'domain_start_failed'])
def test_owner_reports_only_allowlisted_socket_policy_errors(tmp_path, reason):
    from types import SimpleNamespace
    from factory.core.isolation.errors import IsolationError
    owner = _module.OwnerVault(tmp_path)
    def refuse(*args): raise IsolationError(reason, 'private-diagnostic-details')
    owner.dispatch({'id':'init-policy','action':'create','password':'fixture-password'})
    owner.agent = SimpleNamespace(start=refuse, close=lambda: None)
    reply = owner.dispatch({'id': 'policy', 'action': 'start_agent', 'credential_id': 'fixture'})
    assert reply['error'] == (reason if reason.startswith('desktop_socket_policy_') else 'isolation_unavailable')
    assert 'private-diagnostic-details' not in str(reply)
    owner.agent = None
    owner.close()


def test_owner_protocol_lifecycle_and_sanitized_errors(tmp_path):
    policy = tmp_path / 'vault' / 'policy.yaml'
    policy.parent.mkdir()
    policy.write_text('existing policy')
    owner = _module.OwnerVault(tmp_path)
    def call(action, **fields):
        return owner.dispatch({'id': 'request', 'action': action, **fields})
    assert call('status')['result']['state'] == 'not_initialized'
    assert call('create', password='test-password')['result']['state'] == 'unlocked'
    assert policy.read_text() == 'existing policy'
    assert call('lock')['result']['state'] == 'locked'
    failed = call('unlock', password='wrong-private-password')
    assert failed['error'] == 'authentication_failed'
    assert 'wrong-private-password' not in str(failed)
    assert call('unlock', password='test-password')['result']['state'] == 'unlocked'
    owner.close()
    assert call('status')['result']['state'] == 'locked'


@pytest.mark.parametrize('payload', [None, [], {'id': 'x', 'action': 'reveal_secret'},
    {'id': 'x', 'action': 'status', 'role': 'owner'},
    {'id': 'x', 'action': 'create', 'password': 42},
    {'id': 'x', 'action': 'create', 'password': 'x' * 1025},
    {'id': 'x', 'action': 'create', 'password': '  '},
    {'id': '', 'action': 'status'}])
def test_invalid_owner_requests_never_create_storage(tmp_path, payload):
    owner = _module.OwnerVault(tmp_path)
    assert owner.dispatch(payload)['error'] == 'invalid_request'
    assert not (tmp_path / 'vault').exists()
    owner.close()


def test_recovery_export_and_independent_verification_preserve_current_vault(tmp_path):
    owner = _module.OwnerVault(tmp_path)
    def call(action, **fields):
        return owner.dispatch({'id': 'recovery', 'action': action, **fields})
    assert call('generate_phrase')['error'] == 'vault_locked'
    call('create', password='vault-password')
    owner.service.add_credential('demo', 'test-only-secret', 'api_key')
    phrase = call('generate_phrase')['result']['phrase']
    assert len(phrase) >= 40
    assert call('export_recovery', password='wrong', recovery_phrase=phrase)['error'] == 'authentication_failed'
    backup = call('export_recovery', password='vault-password', recovery_phrase=phrase)['result']['backup']
    assert b'test-only-secret' not in base64.b64decode(backup)
    assert call('verify_recovery', backup=backup, recovery_phrase='wrong')['error'] == 'recovery_failed'
    assert call('verify_recovery', backup=backup, recovery_phrase=phrase)['result'] == {'verified': True, 'credential_count': 1}
    assert owner.service.reveal_secret('demo', 'vault-password').secret == 'test-only-secret'
    call('lock')
    assert call('export_recovery', password='vault-password', recovery_phrase=phrase)['error'] == 'vault_locked'
    assert call('verify_recovery', backup=backup, recovery_phrase=phrase)['result']['verified'] is True
    assert call('status')['result']['state'] == 'locked'
    assert call('verify_recovery', backup='not-base64!', recovery_phrase=phrase)['error'] == 'invalid_request'
    damaged = base64.b64encode(b'{"format":"unknown"}').decode()
    assert call('verify_recovery', backup=damaged, recovery_phrase=phrase)['error'] == 'recovery_failed'
    owner.close()


def test_first_run_restore_survives_restart_without_original_vault(tmp_path):
    source_root = tmp_path / 'source'
    source = _module.OwnerVault(source_root)
    source.service.create('old-password')
    source.service.add_credential('demo', 'test-only-secret', 'api_key', scopes=['test'])
    phrase = 'separately-saved-recovery-phrase'
    encoded = source.dispatch({'id': 'export', 'action': 'export_recovery',
        'password': 'old-password', 'recovery_phrase': phrase})['result']['backup']
    source.close()
    shutil.rmtree(source_root)
    root = tmp_path / 'fresh'
    target = _module.OwnerVault(root)
    reply = target.dispatch({'id': 'restore', 'action': 'restore_recovery',
        'backup': encoded, 'recovery_phrase': phrase, 'new_password': 'new-password'})
    assert reply['result']['restored'] is True
    assert reply['result']['state'] == 'unlocked'
    assert reply['result']['credential_count'] == 1
    assert target.service.reveal_secret('demo', 'new-password').secret == 'test-only-secret'
    assert target.service.list_credentials()[0]['scopes'] == ['test']
    target.close()
    restarted = _module.OwnerVault(root)
    assert restarted.service.status()['state'] == 'locked'
    assert restarted.dispatch({'id': 'unlock', 'action': 'unlock', 'password': 'old-password'})['error'] == 'authentication_failed'
    restarted.service.unlock('new-password')
    assert restarted.service.reveal_secret('demo', 'new-password').secret == 'test-only-secret'
    restarted.close()


@pytest.mark.parametrize('backup,phrase', [
    (base64.b64encode(b'{"format":"unknown"}').decode(), 'phrase'),
    ('invalid-base64!', 'phrase'),
    (base64.b64encode(b'not-json').decode(), 'phrase'),
])
def test_invalid_restore_leaves_first_run_storage_absent(tmp_path, backup, phrase):
    owner = _module.OwnerVault(tmp_path)
    reply = owner.dispatch({'id': 'restore', 'action': 'restore_recovery',
        'backup': backup, 'recovery_phrase': phrase, 'new_password': 'new-password'})
    assert reply['error'] in {'recovery_failed', 'invalid_request'}
    assert owner.service.status()['state'] == 'not_initialized'
    assert not (tmp_path / 'vault').exists()
    owner.close()


@pytest.mark.parametrize('locked', [False, True])
def test_restore_never_overwrites_existing_locked_or_open_vault(tmp_path, locked):
    owner = _module.OwnerVault(tmp_path)
    owner.service.create('existing-password')
    owner.service.add_credential('keep', 'existing-secret', 'api_key')
    backup = owner.service.export_recovery('existing-password', 'phrase')
    if locked:
        owner.service.lock()
    db = tmp_path / 'vault' / 'credentials' / 'vault.db'
    before = db.read_bytes()
    reply = owner.dispatch({'id': 'restore', 'action': 'restore_recovery',
        'backup': base64.b64encode(backup).decode(), 'recovery_phrase': 'phrase', 'new_password': 'replacement'})
    assert reply['error'] == 'vault_exists'
    assert db.read_bytes() == before
    if locked:
        owner.service.unlock('existing-password')
    assert owner.service.reveal_secret('keep', 'existing-password').secret == 'existing-secret'
    owner.close()


def test_provider_draft_and_key_roundtrip_with_recovery_and_standard_config(tmp_path):
    from factory.core.runtime import get_profile_config
    owner = _module.OwnerVault(tmp_path / 'source')
    owner.service.create('password')
    fields = {'name': 'my-provider', 'model': 'demo-model', 'base_url': 'https://provider.invalid/v1',
              'api_format': 'openai', 'api_key': 'test-private-api-key'}
    reply = owner.dispatch({'id': 'save', 'action': 'save_profile', **fields})
    assert 'test-private-api-key' not in str(reply)
    draft = reply['result']['saved_profile']
    assert draft['status'] == 'unverified'
    assert draft['config']['env'] == {}
    assert draft['config']['backend'] == 'cc-backend'
    assert owner.service.reveal_secret(draft['credential_id'], 'password').secret == 'test-private-api-key'
    profiles = tmp_path / 'temporary-config'; profiles.mkdir()
    (profiles / 'my-provider.json').write_text(__import__('json').dumps(draft['config']))
    assert get_profile_config('my-provider', profiles_dir=profiles)['model'] == 'demo-model'
    db = tmp_path / 'source/vault/credentials/vault.db'
    assert b'test-private-api-key' not in db.read_bytes()
    assert b'https://provider.invalid/v1' not in db.read_bytes()
    duplicate = owner.dispatch({'id': 'save', 'action': 'save_profile', **{**fields, 'api_key': 'replacement'}})
    assert duplicate['error'] == 'profile_exists'
    assert owner.service.reveal_secret(draft['credential_id'], 'password').secret == 'test-private-api-key'
    backup = owner.service.export_recovery('password', 'recovery-phrase')
    owner.close()
    restarted = _module.OwnerVault(tmp_path / 'source')
    restarted.service.unlock('password')
    assert restarted.dispatch({'id': 'list', 'action': 'list_profiles'})['result']['profiles'] == [draft]
    restarted.close()
    restored = _module.OwnerVault(tmp_path / 'restored')
    restored.service.restore_recovery(backup, 'recovery-phrase', 'new-password')
    assert restored.dispatch({'id': 'list', 'action': 'list_profiles'})['result']['profiles'] == [draft]
    restored.close()


@pytest.mark.parametrize('change', [
    {'name': '../escape'}, {'name': 'My Profile'}, {'model': 'bad\nmodel'},
    {'api_format': 'unknown'}, {'base_url': 'http://remote.invalid/v1'},
    {'base_url': 'https://user:password@provider.invalid/v1'},
    {'base_url': 'https://provider.invalid/v1?api_key=secret'},
    {'base_url': 'https://provider.invalid/v1#secret'},
    {'base_url': 'https://provider.invalid:99999/v1'}, {'api_key': 'bad\nkey'},
])
def test_invalid_provider_inputs_add_no_credentials(tmp_path, change):
    owner = _module.OwnerVault(tmp_path)
    owner.service.create('password')
    fields = {'name': 'demo', 'model': 'model', 'base_url': 'https://provider.invalid/v1',
              'api_format': 'openai', 'api_key': 'test-key', **change}
    assert owner.dispatch({'id': 'save', 'action': 'save_profile', **fields})['error'] == 'invalid_request'
    assert owner.service.list_credentials() == []
    owner.close()


@pytest.mark.parametrize('url', ['http://127.0.0.1:8000/v1', 'http://[::1]:8000/v1', 'http://localhost:8000/v1'])
def test_loopback_provider_and_anthropic_format_are_supported(tmp_path, url):
    owner = _module.OwnerVault(tmp_path)
    owner.service.create('password')
    reply = owner.dispatch({'id': 'save', 'action': 'save_profile', 'name': 'local', 'model': 'local-model',
                           'base_url': url, 'api_format': 'anthropic', 'api_key': 'test-local-key'})
    assert reply['result']['saved_profile']['config']['api_format'] == 'anthropic'
    owner.close()


def test_provider_access_is_blocked_while_locked_and_legacy_secret_metadata_is_unchanged(tmp_path):
    owner = _module.OwnerVault(tmp_path)
    assert owner.dispatch({'id': 'list', 'action': 'list_profiles'})['error'] == 'vault_locked'
    owner.service.create('password')
    owner.service.add_credential('legacy', 'old-key', 'api_key')
    assert owner.service.reveal_secret('legacy', 'password').metadata == {}
    owner.service.lock()
    assert owner.dispatch({'id': 'save', 'action': 'save_profile', 'name': 'demo', 'model': 'model',
        'base_url': 'https://provider.invalid/v1', 'api_format': 'openai', 'api_key': 'test-key'})['error'] == 'vault_locked'
    owner.service.unlock('password')
    assert len(owner.service.list_credentials()) == 1
    owner.close()


def test_provider_check_status_persists_and_locked_or_unrelated_records_cannot_use_key(tmp_path, monkeypatch):
    import provider_probe
    owner = _module.OwnerVault(tmp_path)
    owner.service.create('password')
    owner.service.add_credential('unrelated', 'unrelated-secret', 'api_key')
    saved = owner.dispatch({'id': 'save', 'action': 'save_profile', 'name': 'demo', 'model': 'model',
        'base_url': 'https://provider.invalid/v1', 'api_format': 'openai', 'api_key': 'test-key'})['result']['saved_profile']
    calls = []
    def checker(config, key):
        calls.append((config, key))
        return {'status': 'verified', 'reason': 'ok', 'connected': True, 'checked_at': '2026-10-06T00:00:00+00:00'}
    monkeypatch.setattr(provider_probe, 'check_connection', checker)
    unrelated = owner.service.list_credentials()[1] if owner.service.list_credentials()[0]['service'] == 'desktop-profile:demo' else owner.service.list_credentials()[0]
    assert owner.dispatch({'id': 'check', 'action': 'check_profile', 'credential_id': unrelated['id']})['error'] == 'invalid_request'
    assert calls == []
    checked = owner.dispatch({'id': 'check', 'action': 'check_profile', 'credential_id': saved['credential_id']})
    assert checked['result']['profiles'][0]['status'] == 'verified'
    assert 'test-key' not in str(checked)
    assert calls[0][1] == 'test-key'
    owner.close()
    assert owner.dispatch({'id': 'check', 'action': 'check_profile', 'credential_id': saved['credential_id']})['error'] == 'vault_locked'
    assert len(calls) == 1
    restarted = _module.OwnerVault(tmp_path)
    restarted.service.unlock('password')
    assert restarted.providers.list_profiles()[0]['status'] == 'verified'
    monkeypatch.setattr(provider_probe, 'check_connection', lambda config, key: {
        'status': 'failed', 'reason': 'authentication', 'connected': False, 'checked_at': '2026-10-06T01:00:00+00:00'})
    failed = restarted.dispatch({'id': 'check', 'action': 'check_profile', 'credential_id': saved['credential_id']})
    assert failed['result']['profiles'][0]['status'] == 'failed'
    assert restarted.service.reveal_secret(saved['credential_id'], 'password').secret == 'test-key'
    restarted.close()


def test_readiness_is_owner_only_read_only_and_available_before_vault_creation(tmp_path, monkeypatch):
    target = tmp_path / 'new-data'
    owner = _module.OwnerVault(target)
    before = list(tmp_path.iterdir())
    monkeypatch.setenv('PRIVATE_TOKEN', 'secret-never-print')
    result = owner.dispatch({'id': 'readiness', 'action': 'readiness'})
    assert result['result']['readiness']['mode'] == 'read_only'
    checks = {c['id']: c for c in result['result']['readiness']['checks']}
    assert checks['data_directory']['facts']['path'] == str(target)
    assert list(tmp_path.iterdir()) == before
    assert owner.service.status()['state'] == 'not_initialized'
    assert 'secret-never-print' not in str(result)
    assert owner.dispatch({'id': 'readiness', 'action': 'readiness', 'data_dir': '/tmp'})['error'] == 'invalid_request'
    owner.close()


def test_owner_password_change_preserves_profile_and_requires_current_password(tmp_path):
    owner = _module.OwnerVault(tmp_path)
    def call(action, **fields):
        return owner.dispatch({'id': 'password-change', 'action': action, **fields})
    call('create', password='old-test-password')
    profile = call('save_profile', name='password-demo', model='fixture-model',
                   base_url='https://provider.invalid/v1', api_format='openai', api_key='test-only-secret')
    record_id = profile['result']['saved_profile']['credential_id']
    before = call('list_profiles')['result']['profiles']
    assert call('change_password', current='wrong', new_password='new-test-password')['error'] == 'authentication_failed'
    result = call('change_password', current='old-test-password', new_password='new-test-password')
    assert result['result']['state'] == 'unlocked'
    assert 'test-only-secret' not in str(result)
    assert call('list_profiles')['result']['profiles'] == before
    call('lock')
    assert call('unlock', password='old-test-password')['error'] == 'authentication_failed'
    assert call('unlock', password='new-test-password')['result']['state'] == 'unlocked'
    assert call('list_profiles')['result']['profiles'][0]['credential_id'] == record_id
    owner.close()


def test_owner_password_change_refuses_running_executor_and_locked_vault(tmp_path):
    from types import SimpleNamespace
    owner = _module.OwnerVault(tmp_path)
    def call(action, **fields):
        return owner.dispatch({'id': 'password', 'action': action, **fields})
    call('create', password='old-test-password')
    owner.agent = SimpleNamespace(status=lambda: {'running': True})
    assert call('change_password', current='old-test-password', new_password='new-test-password')['error'] == 'stop_agent_first'
    owner.agent = None
    call('lock')
    assert call('change_password', current='old-test-password', new_password='new-test-password')['error'] == 'vault_locked'
    assert call('unlock', password='old-test-password')['result']['state'] == 'unlocked'
    owner.close()


def test_image_preparation_requires_unlocked_owner_and_stopped_agent(tmp_path, monkeypatch):
    owner = _module.OwnerVault(tmp_path)
    def call(action, **fields):
        return owner.dispatch({'id': 'image-request', 'action': action, **fields})
    assert call('image_status')['result']['image']['phase'] == 'idle'
    assert call('prepare_image', source='/tmp/source.qcow2')['error'] == 'vault_locked'
    call('create', password='test-image-password')
    assert call('prepare_image', source='relative.qcow2')['error'] == 'invalid_request'
    class Agent:
        def status(self): return {'running': True}
    owner.agent = Agent()
    assert call('prepare_image', source='/tmp/source.qcow2')['error'] == 'stop_agent_first'
    owner.agent = None
    calls = []
    monkeypatch.setattr(owner.image, 'start', lambda path, version: calls.append((path, version)) or {'phase': 'checking_source', 'running': True})
    reply = call('prepare_image', source='/tmp/source.qcow2')
    assert reply['result']['image']['running']
    assert calls[0][0] == '/tmp/source.qcow2'
    assert call('image_status', source='/tmp/source.qcow2')['error'] == 'invalid_request'
    owner.close()


def test_image_recovery_requires_unlocked_stopped_owner_and_explicit_action(tmp_path, monkeypatch):
    owner = _module.OwnerVault(tmp_path)
    def call(**fields): return owner.dispatch({'id': 'recover', 'action': 'recover_image', **fields})
    assert call(source='/tmp/source.qcow2')['error'] == 'vault_locked'
    owner.service.create('test-recovery-password')
    assert call(source='relative')['error'] == 'invalid_request'
    owner.agent = type('Agent', (), {'status': lambda self: {'running': True}})()
    assert call(source='/tmp/source.qcow2')['error'] == 'stop_agent_first'
    owner.agent = None
    calls = []
    monkeypatch.setattr(owner.image, 'start', lambda source, version, **kwargs: calls.append(kwargs) or {'running': True})
    assert call(source='/tmp/source.qcow2')['result']['image']['running']
    assert calls == [{'recover': True}]
    assert call(source='/tmp/source.qcow2', recover=True)['error'] == 'invalid_request'
    owner.close()


def test_profile_key_reveal_requires_reauthentication_and_preserves_metadata(tmp_path):
    import json
    owner = _module.OwnerVault(tmp_path)
    def call(action, **fields):
        return owner.dispatch({'id': 'reveal-test', 'action': action, **fields})
    try:
        assert call('reveal_profile_key', credential_id='missing', password='password')['error'] == 'vault_locked'
        call('create', password='fixture-password')
        saved = call('save_profile', name='fixture', model='fixture-model',
                     base_url='https://example.test/v1', api_format='openai', api_key='fixture-private-key')
        identity = saved['result']['saved_profile']['credential_id']
        before = call('list_profiles')
        assert 'fixture-private-key' not in json.dumps(before)
        failed = call('reveal_profile_key', credential_id=identity, password='wrong-private-password')
        assert failed['error'] == 'authentication_failed'
        assert 'wrong-private-password' not in json.dumps(failed)
        assert 'fixture-private-key' not in json.dumps(failed)
        reply = call('reveal_profile_key', credential_id=identity, password='fixture-password')
        assert reply['result'] == {'secret': 'fixture-private-key', 'expires_in_seconds': 15}
        assert call('list_profiles') == before
        owner.service.add_credential('other-service', 'other-private-key', 'api_key')
        assert call('reveal_profile_key', credential_id='other-service', password='fixture-password')['error'] == 'invalid_request'
        assert call('reveal_profile_key', credential_id='missing', password='fixture-password')['error'] == 'invalid_request'
        assert call('reveal_profile_key', credential_id=identity, password='fixture-password', role='owner')['error'] == 'invalid_request'
        call('lock')
        assert call('reveal_profile_key', credential_id=identity, password='fixture-password')['error'] == 'vault_locked'
    finally:
        owner.close()


@pytest.mark.parametrize('action,fields', [
    ('list_profiles', {}), ('reveal_profile_key', {'credential_id': 'fixture', 'password': 'password'}),
    ('unlock', {'password': 'password'}), ('create', {'password': 'password'}),
    ('export_recovery', {'password': 'password', 'recovery_phrase': 'phrase'}),
    ('change_password', {'current': 'password', 'new_password': 'new-password'}),
    ('generate_phrase', {}), ('check_profile', {'credential_id': 'fixture'}),
    ('start_agent', {'credential_id': 'fixture'}), ('stop_agent', {}),
    ('model_catalog', {'preset': 'openrouter', 'api_key': '', 'credential_id': ''}),
    ('prepare_image', {'source': '/tmp/fixture.qcow2'}), ('recover_image', {'source': '/tmp/fixture.qcow2'}),
    ('select_execution_mode', {'mode': 'off'}), ('readiness', {}), ('image_status', {}), ('execution_mode', {}),
    ('verify_recovery', {'backup': 'fixture', 'recovery_phrase': 'phrase'}),
    ('restore_recovery', {'backup': 'fixture', 'recovery_phrase': 'phrase', 'new_password': 'password'}),
    ('save_profile', {'name': 'fixture', 'model': 'fixture', 'base_url': 'https://example.test/v1', 'api_format': 'openai', 'api_key': 'fixture'}),
    ('update_profile', {'credential_id': 'fixture', 'name': 'fixture', 'model': 'fixture', 'base_url': 'https://example.test/v1', 'api_format': 'openai', 'api_key': ''}),
])
def test_locked_interface_blocks_owner_actions_on_backend(tmp_path, action, fields):
    owner = _module.OwnerVault(tmp_path)
    try:
        owner.dispatch({'id': 'create', 'action': 'create', 'password': 'password'})
        owner.dispatch({'id': 'enable-ui', 'action': 'configure_interface_lock', 'password': 'password', 'enabled': 'on'})
        locked = owner.dispatch({'id': 'lock-ui', 'action': 'lock_interface'})
        assert locked['result']['interface_locked'] is True
        assert locked['result']['state'] == 'unlocked'
        reply = owner.dispatch({'id': 'blocked', 'action': action, **fields})
        assert reply['error'] == 'interface_locked'
        assert owner.service.status()['state'] == 'unlocked'
        assert owner.service.list_credentials() == []
    finally:
        owner.close()


def test_interface_lock_preserves_active_consumer_and_needs_fresh_password(tmp_path):
    from types import SimpleNamespace
    from agent_runtime import AgentBusyError
    owner = _module.OwnerVault(tmp_path)
    def call(action, **fields):
        return owner.dispatch({'id': 'interface', 'action': action, **fields})
    try:
        assert call('lock_interface')['error'] == 'interface_lock_disabled'
        call('create', password='password')
        profile = call('save_profile', name='fixture', model='model', base_url='https://example.test/v1',
                       api_format='openai', api_key='runtime-only-key')['result']['saved_profile']
        owner.service.check_provider_profile(profile['credential_id'], lambda config, key: {'status': 'verified'})
        stops = []
        def busy_stop():
            stops.append(True)
            raise AgentBusyError()
        owner.agent = SimpleNamespace(status=lambda: {'running': True, 'profile': 'fixture'}, stop=busy_stop, close=lambda: None)
        assert call('configure_interface_lock', password='password', enabled='on')['result']['interface_lock_enabled'] is True
        assert call('lock_interface')['result']['interface_locked'] is True
        assert stops == []
        assert owner.lifecycle.provider_runtime_headers(profile['credential_id'], profile['config'], 'openai') == {'Authorization': 'Bearer runtime-only-key'}
        assert call('unlock_interface', password='wrong')['error'] == 'authentication_failed'
        assert call('status')['result']['interface_locked'] is True
        assert call('lock')['error'] == 'agent_busy'
        assert call('status')['result']['interface_locked'] is True
        assert call('unlock_interface', password='password')['result']['interface_locked'] is False
        assert call('list_profiles')['result']['profiles'][0]['credential_id'] == profile['credential_id']
        owner.agent = None
        call('lock_interface')
        assert call('lock')['result']['state'] == 'locked'
        assert call('status')['result']['interface_locked'] is False
        assert call('unlock_interface', password='password')['error'] == 'vault_locked'
    finally:
        owner.close()
    restarted = _module.OwnerVault(tmp_path)
    try:
        result = restarted.dispatch({'id': 'restart', 'action': 'status'})['result']
        assert result['state'] == 'locked' and result['interface_locked'] is False
    finally:
        restarted.close()


def test_optional_interface_lock_pin_and_restart(tmp_path):
    owner = _module.OwnerVault(tmp_path)
    def call(action, **fields):
        return owner.dispatch({'id': 'optional', 'action': action, **fields})
    try:
        assert call('status')['result']['interface_lock_enabled'] is False
        call('create', password='password')
        assert call('lock_interface')['error'] == 'interface_lock_disabled'
        assert call('set_interface_pin', password='password', pin='123456')['error'] == 'interface_lock_disabled'
        assert call('configure_interface_lock', password='wrong', enabled='on')['error'] == 'authentication_failed'
        assert call('configure_interface_lock', password='password', enabled='on')['result']['interface_lock_enabled'] is True
        for pin in ['123', 'abcdef', '１２３４５６']:
            assert call('set_interface_pin', password='password', pin=pin)['error'] == 'invalid_request'
        assert call('set_interface_pin', password='wrong', pin='123456')['error'] == 'authentication_failed'
        assert call('set_interface_pin', password='password', pin='123456')['result']['interface_pin_available'] is True
        call('lock_interface')
        for attempt in range(5):
            assert call('unlock_interface_pin', pin='999999')['error'] == ('pin_password_required' if attempt == 4 else 'pin_invalid')
        assert call('unlock_interface_pin', pin='123456')['error'] == 'pin_password_required'
        assert call('status')['result']['interface_pin_available'] is False
        call('unlock_interface', password='password')
        call('lock_interface')
        assert call('unlock_interface_pin', pin='123456')['result']['interface_locked'] is False
        assert call('export_recovery', password='123456', recovery_phrase='fixture')['error'] == 'authentication_failed'
        call('remove_interface_pin', password='password')
        assert call('status')['result']['interface_pin_enabled'] is False
        call('set_interface_pin', password='password', pin='123456')
    finally:
        owner.close()
    assert '123456' not in (tmp_path / 'interface-security.json').read_text()
    owner = _module.OwnerVault(tmp_path)
    try:
        assert call('status')['result']['interface_lock_enabled'] is True
        assert call('status')['result']['interface_pin_enabled'] is False
        assert call('status')['result']['state'] == 'locked'
        call('unlock', password='password')
        call('set_interface_pin', password='password', pin='123456')
        assert call('configure_interface_lock', password='password', enabled='off')['result']['interface_pin_enabled'] is False
        assert call('lock_interface')['error'] == 'interface_lock_disabled'
    finally:
        owner.close()


@pytest.mark.parametrize('action,fields', [
    ('configure_interface_lock', {'password': 'password', 'enabled': 'off'}),
    ('set_interface_pin', {'password': 'password', 'pin': '123456'}),
    ('remove_interface_pin', {'password': 'password'}),
    ('read_legacy_profile', {'source': '/tmp/fixture.json'}),
    ('delete_profile', {'credential_id':'fixture', 'password':'password'}),
    ('rename_profile', {'credential_id':'fixture', 'password':'password', 'new_name':'renamed'}),
])
def test_optional_controls_cannot_bypass_locked_interface(tmp_path, action, fields):
    owner = _module.OwnerVault(tmp_path)
    try:
        for a, f in [('create', {'password':'password'}), ('configure_interface_lock', {'password':'password','enabled':'on'}), ('lock_interface', {})]:
            owner.dispatch({'id':'fixture','action':a,**f})
        assert owner.dispatch({'id':'fixture','action':action,**fields})['error'] == 'interface_locked'
    finally:
        owner.close()


def test_legacy_profile_draft_import_has_no_secrets_or_side_effects(tmp_path):
    import json
    owner = _module.OwnerVault(tmp_path / 'owned')
    source = tmp_path / 'old-profile.json'
    data = {'backend':'cc-backend','model':'fixture-model','base_url':'https://example.test/v1','api_format':'openai',
            'env':{'OPENAI_API_KEY':'private-fixture'},'args':['do-not-run'],'mcp_servers':[{'command':'do-not-run'}]}
    source.write_text(json.dumps(data))
    def read(): return owner.dispatch({'id':'fixture','action':'read_legacy_profile','source':str(source)})
    try:
        assert read()['error'] == 'vault_locked'
        owner.dispatch({'id':'fixture','action':'create','password':'password'})
        assert read()['result']['legacy_profile'] == {'name':'old-profile','model':'fixture-model','base_url':'https://example.test/v1','api_format':'openai'}
        assert owner.providers.list_profiles() == []
        assert json.loads(source.read_text()) == data
        source.unlink(); source.symlink_to(tmp_path / 'missing')
        assert 'result' not in read()
        source.unlink(); source.write_text(json.dumps({**data,'backend':'codex'}))
        assert read()['error'] == 'invalid_request'
        source.write_text('x' * (1024*1024+1))
        assert read()['error'] == 'invalid_request'
    finally:
        owner.close()


def test_profile_rename_delete_password_binding_and_active_guard(tmp_path):
    from types import SimpleNamespace
    from agent_runtime import ProfileInUseError
    owner = _module.OwnerVault(tmp_path)
    def call(action, **fields): return owner.dispatch({'id':'manage','action':action,**fields})
    try:
        call('create',password='password')
        p = call('save_profile',name='first',model='model',base_url='https://example.test/v1',api_format='openai',api_key='fixture-key')['result']['saved_profile']
        cid=p['credential_id']
        owner.service.check_provider_profile(cid,lambda config,key:{'status':'verified'})
        assert call('rename_profile',credential_id=cid,password='wrong',new_name='second')['error']=='authentication_failed'
        assert call('rename_profile',credential_id=cid,password='password',new_name='openai-realtime')['error']=='invalid_request'
        def busy(previous, config): raise ProfileInUseError()
        owner.agent=SimpleNamespace(prepare_profile_update=busy,status=lambda:{'running':True,'profile':'first'},close=lambda:None)
        for action,fields in [('rename_profile',{'new_name':'second'}),('delete_profile',{})]:
            assert call(action,credential_id=cid,password='password',**fields)['error']=='stop_agent_first'
        owner.agent=None
        result=call('rename_profile',credential_id=cid,password='password',new_name='second')['result']
        assert result['profiles'][0]['config']['name']=='second'
        assert result['profiles'][0]['status']=='verified'
        assert result['profiles'][0]['credential_id']==cid
        config=result['profiles'][0]['config']
        assert owner.lifecycle.provider_runtime_headers(cid,config,'openai')=={'Authorization':'Bearer fixture-key'}
        assert call('delete_profile',credential_id='desktop-profile:second',password='password')['error']=='invalid_request'
        assert call('delete_profile',credential_id=cid,password='wrong')['error']=='authentication_failed'
        call('save_profile',name='other',model='model',base_url='https://example.test/v1',api_format='openai',api_key='other-key')
        assert call('rename_profile',credential_id=cid,password='password',new_name='other')['error']=='invalid_request'
        assert len(call('delete_profile',credential_id=cid,password='password')['result']['profiles'])==1
        assert owner.lifecycle.get_secret(cid) is None
    finally: owner.close()


def test_local_profile_without_key_and_remote_transition_guard(tmp_path):
    from types import SimpleNamespace
    owner=_module.OwnerVault(tmp_path)
    def call(action,**fields): return owner.dispatch({'id':'local','action':action,**fields})
    try:
        call('create',password='password')
        fields=dict(name='local-model',model='fixture-model',base_url='http://127.0.0.1:1234/v1',api_format='openai',api_key='')
        p=call('save_profile',**fields)['result']['saved_profile']
        cid=p['credential_id']
        owner.service.check_provider_profile(cid,lambda config,key:{'status':'verified'})
        assert owner.lifecycle.provider_runtime_headers(cid,p['config'],'openai')=={}
        assert call('save_profile',**{**fields,'name':'remote','base_url':'https://example.test/v1'})['error']=='invalid_request'
        assert call('update_profile',credential_id=cid,**{**fields,'base_url':'https://example.test/v1'})['error']=='invalid_request'
        assert call('list_profiles')['result']['profiles'][0]['status']=='verified'
        owner.execution=SimpleNamespace(mode='kvm')
        owner.agent=SimpleNamespace(start=lambda cid:pytest.fail('must not start'),close=lambda:None)
        assert call('start_agent',credential_id=cid)['error']=='local_requires_host_mode'
        owner.agent=None
        call('lock')
        assert call('save_profile',**fields)['error']=='vault_locked'
    finally:owner.close()


def test_owner_unlock_restores_mobile_access_and_network_failure_does_not_fail_unlock(tmp_path):
    from types import SimpleNamespace
    owner=_module.OwnerVault(tmp_path);calls=[]
    try:
        owner.dispatch({'id':'create','action':'create','password':'fixture-password'})
        owner.mobile_access=SimpleNamespace(restore=lambda:calls.append('restore'),close=lambda:calls.append('close'))
        owner.dispatch({'id':'lock','action':'lock'})
        reply=owner.dispatch({'id':'unlock','action':'unlock','password':'fixture-password'})
        assert reply['result']['state']=='unlocked' and calls==['close','restore']
        owner.dispatch({'id':'lock2','action':'lock'})
        def fail():raise OSError('private diagnostic')
        owner.mobile_access.restore=fail
        reply=owner.dispatch({'id':'unlock2','action':'unlock','password':'fixture-password'})
        assert reply['result']['state']=='unlocked' and reply['mobile_error']=='mobile_restore_failed'
        assert 'private diagnostic' not in str(reply)
    finally:owner.mobile_access=None;owner.close()


def test_quota_confirmation_is_owner_only_and_preserves_model_credentials(tmp_path,monkeypatch):
    import json
    from provider_setup import profile_config,desktop_provider_usage
    from factory.core.chatgpt_auth import token_set,SCOPES,RESOURCE
    owner=_module.OwnerVault(tmp_path);owner.service.create('fixture-password')
    saved=token_set({'access_token':'model-access','refresh_token':'model-refresh','id_token':'model-id','scope':SCOPES,'token_type':'Bearer','expires_in':3600},
        {'client_id':'model-client','subject':'model-subject','email':'fixture@example.invalid','ext_agent_host_id':'urn:uuid:00000000-0000-4000-8000-000000000000'})
    config=profile_config('fixture','fixture-model',RESOURCE,'openai-responses')
    record=owner.lifecycle.add_credential('desktop-profile:fixture',json.dumps(saved),'oauth',imported_from='desktop-provider-setup',secret_metadata={'desktop_profile':config})
    claims=base64.urlsafe_b64encode(json.dumps({'sub':'codex-subject','email':'fixture@example.invalid'}).encode()).decode().rstrip('=')
    auth={'tokens':{'id_token':'header.'+claims+'.signature','access_token':'counter-access','refresh_token':'counter-refresh','account_id':'fixture-workspace'}}
    monkeypatch.setattr(owner.codex_quota,'local_snapshot',lambda:auth)
    owner.codex_quota.fetcher=lambda *a,**k:{'usage':{'weekly':{'percent':44,'status':'ok','resetsAt':''}},'fetched_at':'2026-10-15T00:00:00+00:00'}
    original=owner.lifecycle._require().get_secret(record.id).secret
    try:
        call=lambda action,**fields:owner.dispatch({'id':'quota-fixture','action':action,'profile':'fixture',**fields})
        assert call('quota_begin')['result']['state']=='confirm_account'
        pending=call('quota_status')['result'];assert pending['state']=='confirm_account'
        assert call('quota_confirm',link_id=pending['link_id'],unexpected='field')['error']=='invalid_request'
        owner.interface_locked=True;assert call('quota_confirm',link_id=pending['link_id'])['error']=='interface_locked';owner.interface_locked=False
        assert call('quota_confirm',link_id=pending['link_id'])['result']['state']=='connected'
        result=desktop_provider_usage(owner.lifecycle,'fixture',force=True)
        assert result['usage']['weekly']['percent']==44 and result['account_email']=='fixture@example.invalid'
        assert owner.lifecycle._require().get_secret(record.id).secret==original
        assert 'counter-access' not in str(result)
    finally:owner.close()
