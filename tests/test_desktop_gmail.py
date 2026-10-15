import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from gmail_connection import GmailClient, GmailConnection, SERVICE, SCOPE
from factory.core.vault.lifecycle import VaultLifecycle, VaultAuthenticationError
from factory.core.vault.vault import Vault
from mcp_access import McpAccess
from provider_setup import profile_config
from private_mcp import PrivateMcp


@pytest.fixture
def vault(tmp_path):
    value = VaultLifecycle(tmp_path / 'owner-vault')
    value.create('fixture-password')
    yield value
    value.lock()


def connection_data():
    return {'client_id':'fixture.apps.googleusercontent.com','client_secret':'fixture-secret','refresh_token':'fixture-refresh','scope':SCOPE}


def test_connection_is_encrypted_and_status_never_contains_tokens(vault):
    connection = GmailConnection(vault)
    assert not connection.status()['connected']
    connection._store(connection_data())
    assert connection.status() == {'configured':True,'connected':True,'pending':False,'error':''}
    assert b'fixture-refresh' not in vault.db_path.read_bytes()
    record = next(r for r in vault.list_credentials() if r.service == SERVICE)
    assert record.scopes == [SCOPE]


def test_host_import_requires_password_and_survives_reopen(vault, tmp_path, monkeypatch):
    host = tmp_path / 'host'
    host.mkdir()
    host_vault = Vault(host / 'vault.db', host / 'master_key_salt.bin', 'host-password')
    host_vault.add_credential(SERVICE,json.dumps(connection_data()),'oauth_token',imported_from='desktop-gmail')
    passfile = host / 'passphrase.txt'
    passfile.write_text('host-password')
    passfile.chmod(0o600)
    monkeypatch.setenv('FACTORY_GMAIL_HOST_VAULT',str(host))
    connection = GmailConnection(vault)
    with pytest.raises(VaultAuthenticationError):
        connection.import_host('wrong')
    assert not connection.status()['connected']
    assert connection.import_host('fixture-password')['connected']
    vault.lock()
    vault.unlock('fixture-password')
    assert GmailConnection(vault)._data() == connection_data()
    passfile.chmod(0o644)
    with pytest.raises(ValueError):
        connection.import_host('fixture-password')


def test_get_allows_only_read_message_paths_and_exact_scope(vault, monkeypatch):
    connection = GmailConnection(vault)
    connection._store(connection_data())
    requests=[]
    def request(url, **fields):
        requests.append((url,fields))
        if url.endswith('/token'):
            return {'access_token':'fixture-access','scope':SCOPE}
        return {'messages':[]}
    monkeypatch.setattr('gmail_connection.request_json',request)
    assert connection.get('messages',{'q':'in:inbox'}) == {'messages':[]}
    assert requests[-1][1]['token'] == 'fixture-access'
    for path in ['../profile','messages/a/trash','messages/send','https://other.test', 'profile']:
        if path == 'messages/send':
            continue
        with pytest.raises(ValueError):
            connection.get(path)
    monkeypatch.setattr('gmail_connection.request_json',lambda *a,**k:{'access_token':'fixture','scope':SCOPE+' https://mail.google.com/'})
    with pytest.raises(ValueError):
        connection.get('messages')


class FakeMail:
    def __init__(self):
        self.requests=[]
    def status(self):
        return {'connected':True}
    def get(self,path,params):
        self.requests.append((path,params))
        if path=='messages':
            return {'messages':[{'id':'a1'},{'id':'b2'}]}
        encoded=base64.urlsafe_b64encode(b'fixture message').decode()
        return {'payload':{'mimeType':'multipart/mixed','headers':[{'name':'Subject','value':'Fixture subject'}], 'parts':[
            {'mimeType':'text/plain','body':{'data':encoded}},
            {'mimeType':'text/plain','filename':'secret.txt','body':{'data':base64.urlsafe_b64encode(b'attachment-secret').decode()}}]},'snippet':'fixture'}


def test_latest_reads_selected_messages_without_attachments_or_writes():
    mail=FakeMail()
    client=GmailClient(mail,lambda name:True)
    client.start()
    value=json.loads(client.call_tool('gmail_latest',{'count':2}))
    assert len(value['messages'])==2
    assert value['messages'][0]['text']=='fixture message'
    assert 'attachment-secret' not in json.dumps(value)
    assert mail.requests[0][1]['maxResults']==2
    for name,args in [('send',{}),('gmail_read',{'id':'../a'}),('gmail_latest',{'count':True}),('gmail_latest',{'count':11}),('gmail_latest',{'count':2,'query':'all'})]:
        with pytest.raises(ValueError):client.call_tool(name,args)


def test_revoke_during_read_suppresses_result():
    mail=FakeMail()
    decisions=iter([True,False])
    client=GmailClient(mail,lambda name:next(decisions))
    with pytest.raises(PermissionError):client.call_tool('gmail_read',{'id':'a1'})
    assert len(mail.requests)==1
    client=GmailClient(mail,lambda name:False)
    with pytest.raises(PermissionError):client.call_tool('gmail_search',{'query':'anything'})
    assert len(mail.requests)==1


def test_disconnect_revoke_failure_keeps_token(vault,monkeypatch):
    connection=GmailConnection(vault)
    connection._store(connection_data())
    def fail(*a,**kw):raise OSError()
    monkeypatch.setattr('gmail_connection.request_json',fail)
    with pytest.raises(OSError):connection.disconnect('fixture-password')
    assert connection.status()['connected']
    monkeypatch.setattr('gmail_connection.request_json',lambda *a,**kw:{})
    assert not connection.disconnect('fixture-password')['connected']


def profile(vault,url):
    config=profile_config('fixture-model','fixture',url,'openai')
    record=vault.add_credential('desktop-profile:fixture-model','fixture-api-key','api_key',imported_from='desktop-provider-setup',secret_metadata={'desktop_profile':config})
    vault.check_provider_profile(record.id,lambda *args:{'status':'verified'})
    return record,config


def test_local_only_denies_cloud_before_model_or_mail(vault,tmp_path,monkeypatch):
    record,config=profile(vault,'https://example.test/v1')
    access=McpAccess(tmp_path,vault)
    access.save('gmail',record.id,'local_only','fixture-password')
    runner=PrivateMcp(tmp_path,vault,FakeMail())
    with pytest.raises(ValueError,match='mcp_access_denied'):
        runner.start(record.id,'gmail','read')
    assert runner.thread is None
    assert not access.authorize('gmail',config,private=True)


@pytest.mark.parametrize('url,mode',[('http://127.0.0.1:1234/v1','local_only'),('https://example.test/v1','allow')])
def test_private_requests_use_fresh_context_and_revoke_hides_answer(vault,tmp_path,monkeypatch,url,mode):
    record,config=profile(vault,url)
    access=McpAccess(tmp_path,vault)
    access.save('gmail',record.id,mode,'fixture-password')
    calls=[]
    def model(url,payload,headers):
        calls.append(json.loads(json.dumps(payload)))
        if len(payload['messages'])==2:
            return {'choices':[{'message':{'content':None,'tool_calls':[{'id':'t1','type':'function','function':{'name':'gmail_read','arguments':'{"id":"a1"}'}}]}}]}
        return {'choices':[{'message':{'content':'fixture summary'}}]}
    monkeypatch.setattr('private_mcp.post_json',model)
    runner=PrivateMcp(tmp_path,vault,FakeMail())
    for _ in range(2):
        runner.start(record.id,'gmail','read selected')
        runner.thread.join(3)
        assert runner.status()['result']=='fixture summary'
    assert len(calls)==4
    assert len(calls[0]['messages'])==len(calls[2]['messages'])==2
    assert {tool['function']['name'] for tool in calls[0]['tools']}=={'gmail_search','gmail_read','gmail_latest'}
    access.save('gmail',record.id,'deny','fixture-password')
    assert runner.status()['error']=='mcp_access_denied'
    assert runner.status()['result']==''
    assert not list(tmp_path.glob('**/*history*'))


def test_desktop_registry_selection_and_legacy_compatibility(tmp_path,monkeypatch):
    from factory.core import mcp_config
    config={'name':'fixture','mcp_servers':['legacy']}
    monkeypatch.setattr(mcp_config,'_load_profile',lambda *a:(tmp_path/'profile.json',config))
    assert mcp_config.get_profile_mcp_servers('fixture',str(tmp_path))==['legacy']
    folder=tmp_path/'mcp';folder.mkdir()
    path=folder/'access.json'
    path.write_text(json.dumps({'version':1,'grants':{'allowed':{'cid':{'access':'allow','config':config}},'private':{'cid':{'access':'local_only','config':config}}}}))
    assert mcp_config.get_profile_mcp_servers('fixture',str(tmp_path))==['allowed']
    for operation in [mcp_config.enable_mcp_server_for_profile,mcp_config.disable_mcp_server_for_profile]:
        with pytest.raises(PermissionError):operation('fixture','allowed',str(tmp_path))
    path.write_text('invalid')
    assert mcp_config.get_profile_mcp_servers('fixture',str(tmp_path))==[]


@pytest.mark.parametrize('reasoning', ['fixture reasoning', '', None])
def test_private_mcp_preserves_reasoning_for_model_after_gmail_tool(vault,tmp_path,monkeypatch,reasoning):
    record,config=profile(vault,'https://example.test/v1')
    McpAccess(tmp_path,vault).save('gmail',record.id,'allow','fixture-password')
    requests=[]
    def model(url,payload,headers):
        requests.append(json.loads(json.dumps(payload)))
        if len(requests)==1:
            return {'choices':[{'message':{'role':'assistant','content':None,'reasoning_content':reasoning,
                'tool_calls':[{'id':'call-latest','type':'function','function':{'name':'gmail_latest','arguments':'{"count":1}'}}]}}]}
        assert payload['messages'][-2]['reasoning_content']==(reasoning or '')
        return {'choices':[{'message':{'role':'assistant','content':'Fixture summary'}}]}
    monkeypatch.setattr('private_mcp.post_json',model)
    runner=PrivateMcp(tmp_path,vault,FakeMail());runner.start(record.id,'gmail','Latest fixture mail?');runner.thread.join(3)
    assert runner.status()['error']=='' and runner.status()['result']=='Fixture summary'
    assert len(requests)==2


@pytest.mark.parametrize('reason,code', [('timeout',None),('authentication',401),('rate_limit',429),('http_error',400),('fixture-private-provider-body',None)])
def test_private_mcp_reports_model_timeout_without_secret_or_mail(vault,tmp_path,monkeypatch,caplog,reason,code):
    from factory.core.mcp_tasks.transport import ProbeFailure
    record,config=profile(vault,'https://example.test/v1')
    McpAccess(tmp_path,vault).save('gmail',record.id,'allow','fixture-password')
    def model(*args):raise ProbeFailure(reason,code)
    monkeypatch.setattr('private_mcp.post_json',model)
    runner=PrivateMcp(tmp_path,vault,FakeMail());runner.start(record.id,'gmail','fixture-private-question');runner.thread.join(3)
    result=runner.status()
    expected={'stage':'model_request','reason':reason if reason!='fixture-private-provider-body' else 'internal_error'}
    if code:expected['http_status']=code
    assert result['failure']==expected
    assert result['error']=='private_request_failed' and result['result']==''
    assert 'fixture-private-question' not in json.dumps(result)
    assert 'fixture-private-provider-body' not in json.dumps(result)+caplog.text


def test_private_mcp_deepseek_go_uses_same_session_and_explicit_non_thinking(vault,tmp_path,monkeypatch):
    config=profile_config('fixture-go','deepseek-v4-flash','https://opencode.ai/zen/go/v1','openai')
    record=vault.add_credential('desktop-profile:fixture-go','fixture-api-key','api_key',imported_from='desktop-provider-setup',secret_metadata={'desktop_profile':config})
    vault.check_provider_profile(record.id,lambda *args:{'status':'verified'})
    McpAccess(tmp_path,vault).save('gmail',record.id,'allow','fixture-password')
    requests=[]
    def model(url,payload,headers):
        requests.append((payload,dict(headers)))
        assert payload['thinking']=={'type':'disabled'}
        assert headers['x-opencode-session'].startswith('igorek-mcp-')
        if len(requests)==1:
            return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call-1','type':'function','function':{'name':'gmail_latest','arguments':'{"count":1}'}}]}}]}
        return {'choices':[{'message':{'role':'assistant','content':'Fixture Go summary'}}]}
    monkeypatch.setattr('private_mcp.post_json',model)
    runner=PrivateMcp(tmp_path,vault,FakeMail());runner.start(record.id,'gmail','Latest fixture?');runner.thread.join(3)
    assert runner.status()['error']==''
    assert requests[0][1]['x-opencode-session']==requests[1][1]['x-opencode-session']


def test_private_mcp_gmail_auth_failure_is_distinct_from_model_failure(vault,tmp_path,monkeypatch):
    from urllib.error import HTTPError
    record,_=profile(vault,'https://example.test/v1')
    McpAccess(tmp_path,vault).save('gmail',record.id,'allow','fixture-password')
    monkeypatch.setattr('private_mcp.post_json',lambda *args:{'choices':[{'message':{'tool_calls':[{'id':'call-1','type':'function','function':{'name':'gmail_latest','arguments':'{"count":1}'}}]}}]})
    class ExpiredMail(FakeMail):
        def get(self,*args):raise HTTPError('https://private.invalid',401,'fixture-private-error',{},None)
    runner=PrivateMcp(tmp_path,vault,ExpiredMail());runner.start(record.id,'gmail','fixture');runner.thread.join(3)
    assert runner.status()['failure']=={'stage':'mcp_call','reason':'authentication','http_status':401}
    assert 'fixture-private-error' not in json.dumps(runner.status())
