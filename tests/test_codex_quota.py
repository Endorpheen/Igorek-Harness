import base64
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from codex_quota import CodexQuotaConnection
from factory.core.chatgpt_usage import UsageError, account_limit_windows


@pytest.mark.parametrize('slot',['primary_window','secondary_window'])
def test_official_codex_weekly_window_is_selected_by_duration(slot):
    result=account_limit_windows({'rate_limit':{slot:{'used_percent':37,'limit_window_seconds':604800,'reset_at':1792000000}}})
    assert result['weekly']['percent']==37


def test_codex_bucket_wins_over_an_unrelated_quota_and_five_hour_is_not_weekly():
    data={'code_review_rate_limit':{'primary_window':{'used_percent':99,'limit_window_seconds':604800}},
          'rate_limit':{'secondary_window':{'used_percent':23,'limit_window_seconds':604800}}}
    assert account_limit_windows(data)['weekly']['percent']==23
    assert account_limit_windows({'rate_limit':{'primary_window':{'used_percent':20,'limit_window_seconds':18000}}})['rolling']['percent']==20


def test_quota_login_is_bound_to_confirmed_identity_in_separate_encrypted_record(tmp_path,monkeypatch):
    import threading
    class Lifecycle:
        _mutex=threading.RLock()
        def _require(self):return self
        def get_secret(self,key):return self.links.get(key)
        def add_credential(self,key,value,*args,**kwargs):self.links[key]=SimpleNamespace(secret=value)
        def list_provider_profiles(self):return [{'credential_id':'p','config':{'name':'codexpro'}}]
        def _chatgpt_record(self,*args):return 'vault','record',self.secret
        def _store_provider_secret(self,*args):pytest.fail('working model credentials must remain unchanged')
    lifecycle=Lifecycle();lifecycle.links={};lifecycle.secret=SimpleNamespace(secret=json.dumps({'subject':'same-user','email':'same@example.com'}));original=lifecycle.secret.secret
    def auth(subject,email):
        body=base64.urlsafe_b64encode(json.dumps({'sub':subject,'email':email}).encode()).decode().rstrip('=')
        return {'tokens':{'id_token':'header.'+body+'.signature','access_token':'fixture-secret','refresh_token':'fixture-refresh','account_id':'fixture-workspace'}}
    connection=CodexQuotaConnection(tmp_path,lifecycle)
    current=auth('other-user','same@example.com')
    monkeypatch.setattr(connection,'local_snapshot',lambda:current)
    with pytest.raises(UsageError,match='quota_login_required'):connection.usage('codexpro')
    assert not lifecycle.links
    pending=connection.begin('codexpro');connection.confirm('codexpro',pending['link_id'])
    link=lifecycle.links['desktop-codex-quota:p'].secret
    assert json.loads(link)['identity']==connection.identity(current)
    assert 'fixture-secret' not in link and 'fixture-refresh' not in link
    assert lifecycle.secret.secret==original
    current=auth('unexpected-user','same@example.com')
    with pytest.raises(UsageError,match='quota_account_mismatch'):connection.cache_key('codexpro')


def test_unconfirmed_counter_does_not_launch_a_helper_or_reuse_model_token(tmp_path,monkeypatch):
    import threading
    lifecycle=SimpleNamespace(_mutex=threading.RLock(),_require=lambda:True,
        list_provider_profiles=lambda:[{'credential_id':'p','config':{'name':'codexpro'}}],
        _chatgpt_record=lambda *a:(None,None,SimpleNamespace(secret=json.dumps({'subject':'u','email':'u@x.com'}))))
    lifecycle.get_secret=lambda key:None
    lifecycle._require=lambda:lifecycle
    connection=CodexQuotaConnection(tmp_path,lifecycle,fetcher=lambda *a,**k:pytest.fail('Unconfirmed quota made a request'))
    monkeypatch.setattr(__import__('subprocess'),'Popen',lambda *a,**k:pytest.fail('Counter launched a process'))
    with pytest.raises(UsageError,match='quota_login_required'):connection.usage('codexpro')
    connection.close()


def _quota_fixture(tmp_path,monkeypatch):
    import threading
    class Vault:
        def __init__(self):self.links={};self._mutex=threading.RLock();self.model=SimpleNamespace(secret=json.dumps({'subject':'different-siwc-sub','email':'same@example.com','client_id':'siwc-client'}));self.config={'name':'codexpro'}
        def _require(self):return self
        def get_secret(self,key):return self.links.get(key)
        def add_credential(self,key,value,*args,**kwargs):self.links[key]=SimpleNamespace(secret=value)
        def list_provider_profiles(self):return [{'credential_id':'p','config':self.config}]
        def _chatgpt_record(self,*args):return self,None,self.model
        def _store_provider_secret(self,*args):pytest.fail('model login changed')
    claims=base64.urlsafe_b64encode(json.dumps({'sub':'codex-sub','email':'same@example.com'}).encode()).decode().rstrip('=')
    auth={'tokens':{'id_token':'header.'+claims+'.signature','access_token':'fixture-token','refresh_token':'never-copy-refresh','account_id':'account-a'},'last_refresh':'2026-10-09T00:00:00Z'}
    local=tmp_path/'auth.json';local.write_text(json.dumps(auth));local.chmod(0o600)
    vault=Vault();connection=CodexQuotaConnection(tmp_path,vault,local_auth_path=local)
    def fetch(token,**kwargs):
        assert token=='fixture-token'
        assert kwargs=={'account_id':'account-a','expected_email':'same@example.com'}
        return {'usage':{'weekly':{'percent':44,'status':'ok','resetsAt':''}},'fetched_at':'2026-10-15T00:00:00+00:00'}
    connection.fetcher=fetch
    return connection,vault,local,auth


def test_local_codex_confirmation_reads_without_copying_refresh_or_changing_login(tmp_path,monkeypatch):
    connection,vault,local,auth=_quota_fixture(tmp_path,monkeypatch);original=local.read_bytes();model=vault.model.secret
    try:
        result=connection.begin('codexpro');assert result['state']=='confirm_account' and result['email']=='same@example.com'
        with pytest.raises(UsageError,match='quota_login_failed'):connection.confirm('codexpro','wrong-link')
        assert 'refresh_token' not in connection.local_snapshot()['tokens']
        assert not (tmp_path/'quota-sessions').exists()
        assert connection.confirm('codexpro',result['link_id'])['state']=='connected'
        usage=connection.usage('codexpro');assert usage['source']=='codex_local' and usage['usage']['weekly']['percent']==44
        saved=vault.links['desktop-codex-quota:p'].secret;assert 'fixture-token' not in saved and 'never-copy-refresh' not in saved
        assert local.read_bytes()==original and vault.model.secret==model
        assert usage['account_email']=='same@example.com' and len(usage['account_key'])==64
    finally:connection.close()


def test_changed_local_account_or_profile_cannot_reuse_connected_counter(tmp_path,monkeypatch):
    connection,vault,local,auth=_quota_fixture(tmp_path,monkeypatch)
    try:
        result=connection.begin('codexpro');connection.confirm('codexpro',result['link_id'])
        auth['tokens']['account_id']='account-b';local.write_text(json.dumps(auth))
        with pytest.raises(UsageError,match='quota_account_mismatch'):connection.cache_key('codexpro')
        with pytest.raises(UsageError,match='quota_account_mismatch'):connection.usage('codexpro')
        auth['tokens']['account_id']='account-a';local.write_text(json.dumps(auth));vault.config={'name':'codexpro','model':'new'}
        with pytest.raises(UsageError,match='quota_profile_changed'):connection.usage('codexpro')
    finally:connection.close()


def test_locked_counter_and_symlink_never_read_host_credentials(tmp_path,monkeypatch):
    connection,vault,local,auth=_quota_fixture(tmp_path,monkeypatch)
    try:
        connection.consumer_check=lambda:False
        with pytest.raises(UsageError,match='quota_locked'):connection.begin('codexpro')
        connection.consumer_check=lambda:True
        link=tmp_path/'link';link.symlink_to(local);connection.local_auth_path=link
        with pytest.raises(UsageError,match='quota_local_login_required'):connection.local_snapshot()
    finally:connection.close()


@pytest.mark.parametrize('percent',[None,True,-1,101,float('nan')])
def test_invalid_codex_window_never_becomes_zero_or_another_account(percent):
    with pytest.raises(UsageError):account_limit_windows({'rate_limit':{'primary_window':{'used_percent':percent,'limit_window_seconds':604800}}})
