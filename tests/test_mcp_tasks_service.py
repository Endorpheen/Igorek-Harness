import json

import pytest

from factory.core.mcp_tasks.service import McpTasks, HostVault
from factory.core.mcp_tasks.gmail import SERVICE, SCOPE
from factory.core.vault.lifecycle import VaultLifecycle


@pytest.fixture
def service(tmp_path):
    vault = VaultLifecycle(tmp_path / 'vault')
    vault.create('fixture-password')
    config = {'name':'fixture','model':'fixture','base_url':'http://127.0.0.1:1234/v1','api_format':'openai'}
    record = vault.add_credential('desktop-profile:fixture','fixture-key','api_key',imported_from='desktop-provider-setup',secret_metadata={'desktop_profile':config,'verified':True})
    vault.check_provider_profile(record.id,lambda *args:{'status':'verified'})
    vault.add_credential(SERVICE,json.dumps({'client_id':'fixture.apps.googleusercontent.com','refresh_token':'fixture-refresh','scope':SCOPE}),'oauth_token',imported_from='desktop-gmail')
    valid = {'first','second'}
    service = McpTasks(root=tmp_path, lifecycle=vault, consumer_valid=lambda identity: identity in valid)
    service._ready()
    yield service, record.id, valid
    service.runner.clear()
    if service.runner.thread:
        service.runner.thread.join(3)
    vault.lock()


def grant(service, cid):
    return service.dispatch('first','configure',{'server':'gmail','credential_id':cid,'access':'local_only','password':'fixture-password'})


def test_default_deny_owner_password_and_no_secret_in_catalog(service):
    s,cid,_ = service
    assert s.dispatch('first','list') == {'entries':[]}
    assert s.dispatch('first','configure',{'server':'gmail','credential_id':cid,'access':'allow','password':'wrong'})['error']=='mcp_access_denied'
    assert grant(s,cid)=={'saved':True}
    entries=s.dispatch('first','list')['entries']
    assert entries[0]['local'] and entries[0]['readonly']
    assert 'fixture-refresh' not in json.dumps(s.dispatch('first','catalog'))
    assert not (s.root/'mcp/access.json').exists()


def test_device_revoked_during_model_call_never_gets_result(service,monkeypatch):
    s,cid,valid=service
    grant(s,cid)
    def model(*args):
        valid.remove('first')
        return {'choices':[{'message':{'content':'private-result'}}]}
    monkeypatch.setattr('factory.core.mcp_tasks.private.post_json',model)
    s.dispatch('first','start',{'server':'gmail','credential_id':cid,'message':'fixture'})
    s.runner.thread.join(3)
    assert s.dispatch('first','status')['error']=='mcp_access_denied'
    assert s.runner.status()['result']==''
    assert s.dispatch('second','status')['job']['result']==''


def test_other_device_cannot_read_or_clear_result(service,monkeypatch):
    s,cid,_=service
    grant(s,cid)
    monkeypatch.setattr('factory.core.mcp_tasks.private.post_json',lambda *a:{'choices':[{'message':{'content':'private-result'}}]})
    s.dispatch('first','start',{'server':'gmail','credential_id':cid,'message':'fixture'})
    s.runner.thread.join(3)
    assert s.dispatch('second','status')['error']=='task_other_window'
    assert s.dispatch('second','clear')['error']=='task_other_window'
    assert s.dispatch('first','status')['job']['result']=='private-result'
    s.dispatch('first','clear')
    assert s.dispatch('second','status')['job']['result']==''


@pytest.mark.parametrize('action,fields',[('start',{'server':'gmail'}),('list',{'extra':'x'}),('unknown',{}),('start',{'server':'gmail','credential_id':'a','message':'x'*1025})])
def test_strict_shapes(service,action,fields):
    assert service[0].dispatch('first',action,fields)['error']=='invalid_request'


def test_host_profiles_rebind_when_credentials_change(tmp_path):
    from factory.core.vault.vault import Vault
    root=tmp_path/'vault';root.mkdir()
    Vault(root/'vault.db',root/'master_key_salt.bin','host-password')
    (root/'passphrase.txt').write_text('host-password');(root/'passphrase.txt').chmod(0o600)
    profiles=tmp_path/'profiles';profiles.mkdir()
    file=profiles/'fixture.json'
    config={'backend':'cc-backend','api_format':'openai','model':'fixture','base_url':'https://example.test/v1','env':{'API_KEY':'fixture-secret'}}
    file.write_text(json.dumps(config))
    vault=HostVault(root)
    first=vault.list_provider_profiles()[0]
    assert first['status']=='configured'
    assert 'fixture-secret' not in json.dumps(first)
    config['env']['API_KEY']='replacement';file.write_text(json.dumps(config))
    assert vault.list_provider_profiles()[0]['config']!=first['config']


def test_device_configuration_requires_owner_capability_and_confirmation(service):
    s,cid,valid=service
    fields={'server':'gmail','credential_id':cid,'access':'local_only','confirmation':'owner_confirmed'}
    assert s.dispatch('first','configure_device',fields)['error']=='mcp_access_denied'
    s.owner_device_check=lambda identity: identity=='first'
    assert s.dispatch('first','configure_device',{**fields,'confirmation':'missing'})['error']=='mcp_access_denied'
    assert s.dispatch('second','configure_device',fields)['error']=='mcp_access_denied'
    assert s.dispatch('first','configure_device',fields)=={'saved':True}
    assert s.dispatch('first','list')['entries'][0]['access']=='local_only'
    valid.remove('first')
    assert s.dispatch('first','configure_device',{**fields,'access':'allow'})['error']=='mcp_access_denied'
    assert s.access.grants()['gmail'][cid]['access']=='local_only'


def test_main_chat_mcp_followups_keep_only_bound_private_context(service,monkeypatch):
    s,cid,_=service
    grant(s,cid)
    calls=[]
    def model(url,payload,headers):
        calls.append(json.loads(json.dumps(payload['messages'])))
        return {'choices':[{'message':{'content':'private-summary-'+str(len(calls))}}]}
    monkeypatch.setattr('factory.core.mcp_tasks.private.post_json',model)
    for text in ['read selected mail','tell me more about it']:
        s.dispatch('first','start',{'server':'gmail','credential_id':cid,'message':text})
        s.runner.thread.join(3)
    assert len(calls[0])==2
    assert [item['role'] for item in calls[1]]==['system','user','assistant','user']
    assert calls[1][2]['content']=='private-summary-1'
    s.dispatch('first','clear')
    s.dispatch('second','start',{'server':'gmail','credential_id':cid,'message':'new context'})
    s.runner.thread.join(3)
    assert len(calls[2])==2
    assert 'private-summary-1' not in json.dumps(calls[2])
    s.dispatch('second','configure',{'server':'gmail','credential_id':cid,'access':'deny','password':'fixture-password'})
    assert s.runner.status()['error']=='mcp_access_denied'
    assert s.runner.history==[]


def test_host_profile_uses_declared_vault_provider_without_global_secret_env(tmp_path,monkeypatch):
    from factory.core.vault.vault import Vault
    root=tmp_path/'vault';root.mkdir()
    vault=Vault(root/'vault.db',root/'master_key_salt.bin','host-password')
    vault.add_credential('opencode','fixture-provider-secret','api_key')
    vault.add_credential('telegram','unrelated-secret','api_key')
    (root/'passphrase.txt').write_text('host-password');(root/'passphrase.txt').chmod(0o600)
    profiles=tmp_path/'profiles';profiles.mkdir()
    config={'backend':'cc-backend','provider':'opencode','api_format':'openai','model':'fixture','base_url':'https://example.test/v1','vault_services':['opencode','telegram'],'env':{}}
    (profiles/'fixture.json').write_text(json.dumps(config))
    host=HostVault(root);profile=host.list_provider_profiles()[0]
    monkeypatch.delenv('OPENCODE_API_KEY',raising=False)
    monkeypatch.delenv('FACTORY_TELEGRAM_BOT_TOKEN',raising=False)
    headers=host.provider_runtime_headers(profile['credential_id'],profile['config'],'openai')
    assert headers['Authorization']=='Bearer fixture-provider-secret'
    assert 'unrelated-secret' not in json.dumps(headers)
    import os
    assert 'OPENCODE_API_KEY' not in os.environ
    assert 'FACTORY_TELEGRAM_BOT_TOKEN' not in os.environ
    (root/'policy.yaml').write_text('agents: {}\n')
    monkeypatch.setenv('OPENCODE_API_KEY','must-not-bypass-policy')
    with pytest.raises(PermissionError):
        host.provider_runtime_headers(profile['credential_id'],profile['config'],'openai')


def test_host_profile_keeps_explicit_environment_auth_without_vault_service(tmp_path):
    from factory.core.vault.vault import Vault
    root=tmp_path/'vault';root.mkdir()
    Vault(root/'vault.db',root/'master_key_salt.bin','host-password')
    (root/'passphrase.txt').write_text('host-password');(root/'passphrase.txt').chmod(0o600)
    profiles=tmp_path/'profiles';profiles.mkdir()
    config={'backend':'cc-backend','provider':'openrouter','api_format':'openai','model':'fixture','base_url':'https://example.test/v1','env':{'OPENROUTER_API_KEY':'explicit-fixture-key'}}
    (profiles/'fixture.json').write_text(json.dumps(config))
    host=HostVault(root);profile=host.list_provider_profiles()[0]
    assert host.provider_runtime_headers(profile['credential_id'],profile['config'],'openai')['Authorization']=='Bearer explicit-fixture-key'
