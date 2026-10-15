import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from factory.core.chatgpt_usage import UsageStore, weekly_window


def event(primary=None, secondary=None, **extra):
    return {'type': 'codex.rate_limits', 'rate_limits': {'primary': primary, 'secondary': secondary}, **extra}


def window(percent=27, minutes=10080):
    return {'used_percent': percent, 'window_minutes': minutes, 'reset_at': 1792000000}


@pytest.mark.parametrize('slot', ['primary', 'secondary'])
def test_weekly_is_selected_by_duration_not_position(slot):
    result = weekly_window(event=event(**{slot: window()}))
    assert result['percent'] == 27 and result['resetsAt']
    assert weekly_window(event=event(primary=window(minutes=300))) is None


@pytest.mark.parametrize('percent', [None, 'nan', 'inf', -1, 101])
def test_invalid_quota_is_unavailable_not_zero(percent):
    assert weekly_window(event=event(primary=window(percent))) is None


def test_weekly_headers_and_other_limit_family():
    headers = {f'x-codex-primary-{key}': value for key, value in {
        'used-percent': '8', 'window-minutes': '10080', 'reset-at': '1792000000'}.items()}
    assert weekly_window(headers=headers)['percent'] == 8
    assert weekly_window(event=event(primary=window(), metered_limit_name='codex_other')) is None


def test_store_is_account_and_binding_scoped_and_redacts_everything_else(tmp_path):
    config = {'name': 'codexpro', 'model': 'fixture'}
    store = UsageStore(tmp_path, 'account-a', config)
    store.observe(event=event(primary=window(31), private='fixture-secret', credits={'balance': 'fixture-secret'}))
    data = UsageStore(tmp_path, 'account-a', config).load()
    assert set(data['usage']) == {'weekly'} and data['usage']['weekly']['percent'] == 31
    assert UsageStore(tmp_path, 'account-b', config).load()['usage'] is None
    assert UsageStore(tmp_path, 'account-a', {**config, 'model': 'other'}).load()['usage'] is None
    assert 'fixture-secret' not in store.path.read_text()
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_desktop_quota_routes_selected_profile_without_querying_other_credentials(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
    import provider_setup
    profile = {'credential_id': 'account-a', 'config': {'name': 'codexpro', 'model': 'fixture',
        'base_url': 'https://api.openai.com/v1', 'api_format': 'openai-responses'}}
    calls=[]
    management = SimpleNamespace(home=tmp_path, list_provider_profiles=lambda: [profile],
        chatgpt_access_token=lambda identity, config: calls.append(identity) or 'fixture-token')
    UsageStore(tmp_path, 'account-a', profile['config']).observe(event=event(primary=window(42)))
    monkeypatch.setattr(provider_setup, 'desktop_opencode_usage', lambda *a, **k: pytest.fail('wrong provider queried'))
    management.codex_quota=SimpleNamespace(usage=lambda name: calls.append(name) or {'usage': {'weekly': {'percent':42,'status':'ok','resetsAt':''}}, 'fetched_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'source':'codex_account'})
    result=provider_setup.desktop_provider_usage(management,'codexpro')
    assert result['provider']=='codex' and result['usage']['weekly']['percent']==42
    assert calls==['codexpro']
    provider_setup.desktop_provider_usage(management,'codexpro')
    assert calls==['codexpro']
    provider_setup.desktop_provider_usage(management,'codexpro',force=True)
    assert calls==['codexpro','codexpro']


def test_slow_previous_profile_quota_cannot_overwrite_current_profile():
    import subprocess
    source = Path(__file__).resolve().parents[1] / 'factory/web_ui/static/app.js'
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');let profile='first';const pending=[];
const ctx={state:{paired:true,quota:{}},window:{IgorekDesktop:{},clearTimeout(){},setTimeout},OPENCODE_USAGE_TTL_MS:60000,Date,
 currentSessionProfile:()=>profile,renderPlanSection(){},encode:encodeURIComponent,
 api:()=>new Promise(resolve=>pending.push(resolve))};vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function currentQuotaModel()'),source.indexOf('function refreshOpenCodeUsageIfVisible()')),ctx);
(async()=>{
 const first=ctx.loadOpenCodeUsage();profile='second';const second=ctx.loadOpenCodeUsage();
 pending[1]({provider:'codex',usage:{weekly:{percent:68}}});await second;
 pending[0]({provider:'codex',usage:{weekly:{percent:11}}});await first;
 assert.equal(ctx.state.quota.profile,'second');assert.equal(ctx.state.quota.data.weekly.percent,68);
 const empty=ctx.loadOpenCodeUsage(true);pending[2]({provider:'codex',usage:{weekly:{percent:null}}});await empty;
 assert.equal(ctx.state.quota.data,null,'missing quota is not zero');
})().catch(error=>{console.error(error);process.exit(1)});
'''
    result = subprocess.run(['node', '-e', script, str(source)], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('slot', ['primary_window', 'secondary_window'])
def test_native_codex_usage_shape_accepts_weekly_in_either_slot(slot):
    from factory.core.chatgpt_usage import account_limit_windows
    data={'rate_limit': {slot: {'limit_window_seconds':604800,'used_percent':23,'reset_at':1792000000}}}
    assert account_limit_windows(data)['weekly']['percent']==23


def test_usage_http_request_is_fixed_and_never_returns_credentials_or_private_fields():
    from factory.core.chatgpt_usage import fetch_account_usage, USAGE_URL
    calls=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def read(self,_): return json.dumps({'rate_limit':{'primary_window':{
            'used_percent':19,'limit_window_seconds':604800,'reset_at':1792000000}},'private':'fixture-secret'}).encode()
    class Opener:
        def open(self,request,timeout):
            calls.append(request)
            assert request.full_url==USAGE_URL and request.get_method()=='GET'
            assert request.get_header('Authorization')=='Bearer fixture-token' and timeout==8
            return Response()
    result=fetch_account_usage('fixture-token',opener=Opener())
    assert result['source']=='account_usage' and result['usage']['weekly']['percent']==19
    assert 'fixture-token' not in str(result) and 'fixture-secret' not in str(result)
    assert len(calls)==1


@pytest.mark.parametrize('status', [401,403,429,500])
def test_quota_denial_preserves_only_static_code_and_http_status(status):
    import urllib.error
    from factory.core.chatgpt_usage import UsageError,fetch_account_usage
    class Opener:
        def open(self,*a,**k):raise urllib.error.HTTPError('https://chatgpt.com/backend-api/wham/usage',status,'fixture-secret',{},None)
    with pytest.raises(UsageError) as error:fetch_account_usage('fixture-token',opener=Opener())
    assert error.value.http_status==status
    assert 'fixture-secret' not in str(error.value) and 'fixture-token' not in str(error.value)


def test_usage_does_not_follow_redirect_with_bearer_token():
    import urllib.error
    from factory.core.chatgpt_auth import NoRedirect
    request=__import__('urllib.request',fromlist=['Request']).Request('https://chatgpt.com/backend-api/wham/usage',headers={'Authorization':'Bearer fixture-token'})
    assert NoRedirect().redirect_request(request,None,302,'redirect',{},'https://other.invalid/steal') is None


def test_codex_cache_keeps_account_and_supported_windows(tmp_path):
    store=UsageStore(tmp_path,'account',{'name':'profile'})
    store.save({'usage':{'weekly':{'percent':44,'status':'ok','resetsAt':''},'rolling':{'percent':12,'status':'ok','resetsAt':''}},
        'source':'codex_local','account_email':'fixture@example.invalid','account_key':'a'*64,'fetched_at':'2026-10-09T00:00:00+00:00'})
    result=store.load();assert result['source']=='codex_local' and result['account_key']=='a'*64
    assert result['usage']['weekly']['percent']==44 and result['usage']['rolling']['percent']==12


def test_replaced_codex_account_hides_even_fresh_cached_numbers(tmp_path):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
    import provider_setup
    from datetime import datetime,UTC
    from factory.core.chatgpt_usage import UsageError
    profile={'credential_id':'p','config':{'name':'profile','api_format':'openai-responses'}}
    store=UsageStore(tmp_path,'p',profile['config']);store.save({'usage':{'weekly':{'percent':44,'resetsAt':''}},'source':'codex_local','account_key':'old','fetched_at':datetime.now(UTC).isoformat()})
    def reject(*args):raise UsageError('quota_account_mismatch')
    management=SimpleNamespace(home=tmp_path,list_provider_profiles=lambda:[profile],codex_quota=SimpleNamespace(cache_key=reject))
    result=provider_setup.desktop_provider_usage(management,'profile')
    assert result['usage'] is None and result['quota_error']['code']=='quota_account_mismatch'
