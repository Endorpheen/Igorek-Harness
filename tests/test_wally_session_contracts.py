"""Session cache, refresh, transport and account shapes with synthetic credentials."""
import io
import json
import os
from unittest.mock import Mock
import urllib.error

import pytest

from factory.core import wally_auth as auth


@pytest.fixture
def local(tmp_path,monkeypatch):
    for key in list(os.environ):
        if key.startswith('WALLY_') or key==auth.WALLY_CREDENTIALS_ENV:
            monkeypatch.delenv(key,raising=False)
    path=tmp_path/'credentials.json'
    monkeypatch.setenv(auth.WALLY_CREDENTIALS_ENV,str(path))
    monkeypatch.setattr(auth,'_CACHE',{'session':None,'fetched_at':0.0})
    monkeypatch.setattr(auth.time,'time',lambda:10000)
    monkeypatch.setattr(auth.urllib.request,'urlopen',Mock(side_effect=AssertionError('Unexpected network')))
    return path


@pytest.mark.parametrize('value,expected',[(None,None),('',None),('  ',None),('invalid',None),('9999',9999),(9999,9999),(-1,None),(0,None),('2026-10-26T00:00:00Z',1792972800),('2026-10-26T00:00:00',1792972800)])
def test_expiry_parser_accepts_supported_timestamps_or_missing_values(value,expected):
    assert auth._coerce_expires_at(value)==expected


@pytest.mark.parametrize('value',[float('nan'),float('inf'),float('-inf')])
def test_nonfinite_expiry_does_not_crash_a_session_catalog(value):
    assert auth._coerce_expires_at(value) is None


def test_environment_session_overrides_file_and_cache_is_explicitly_bypassable(local,monkeypatch):
    local.write_text(json.dumps({'access_token':'synthetic-file','refresh_token':'synthetic-refresh','expires_at':20000}))
    monkeypatch.setenv(auth.WALLY_ACCESS_TOKEN_ENV,'synthetic-env')
    monkeypatch.setenv(auth.WALLY_EXPIRES_AT_ENV,'20000')
    first=auth.load_wally_session()
    assert first.access_token=='synthetic-env' and first.source=='env'
    monkeypatch.setenv(auth.WALLY_ACCESS_TOKEN_ENV,'synthetic-new')
    assert auth.load_wally_session() is first
    assert auth.load_wally_session(use_cache=False).access_token=='synthetic-new'


def test_expired_env_without_refresh_uses_owned_credentials_file(local,monkeypatch):
    local.write_text(json.dumps({'access_token':'synthetic-file','refresh_token':'synthetic-refresh','expires_at':20000}))
    monkeypatch.setenv(auth.WALLY_ACCESS_TOKEN_ENV,'synthetic-expired')
    monkeypatch.setenv(auth.WALLY_EXPIRES_AT_ENV,'1')
    assert auth.load_wally_session(use_cache=False).access_token=='synthetic-file'


@pytest.mark.parametrize('text',['[]','null','bad JSON','{}'])
def test_missing_or_invalid_credentials_file_returns_no_session(local,text):
    assert auth.load_wally_session(use_cache=False) is None
    local.write_text(text)
    assert auth.load_wally_session(use_cache=False) is None
    with pytest.raises(auth.WallyAuthError,match='session not found'):auth.get_wally_session()


def test_refresh_persists_rotated_credentials_and_preserves_unrelated_metadata(local,monkeypatch):
    local.write_text(json.dumps({'access_token':'synthetic-old','refresh_token':'synthetic-refresh','console_url':'https://example.com','email':'fixture@example.invalid','plan':'fixture','expires_at':1,'custom':'retain'}))
    call=Mock(return_value=io.BytesIO(json.dumps({'access_token':'synthetic-rotated','refresh_token':'synthetic-new-refresh','expires_in':3600}).encode()))
    monkeypatch.setattr(auth.urllib.request,'urlopen',call)
    token=auth.get_wally_access_token()
    assert token=='synthetic-rotated'
    request=call.call_args.args[0]
    assert request.full_url=='https://example.com/auth/cli/refresh' and json.loads(request.data)=={'refresh_token':'synthetic-refresh'}
    saved=json.loads(local.read_text())
    assert saved['custom']=='retain' and saved['expires_at']==13600 and saved['refresh_token']=='synthetic-new-refresh'
    assert saved['email']=='fixture@example.invalid' and saved['plan']=='fixture'
    if os.name=='posix':assert local.stat().st_mode & 0o777==0o600
    assert auth.load_wally_session() is auth._CACHE['session']


def test_refresh_without_refresh_token_fails_before_network(local):
    session=auth.WallySession('synthetic','', 'https://example.com',1,'','','fixture')
    with pytest.raises(auth.WallyAuthError,match='no refresh token'):auth.refresh_wally_session(session)
    assert not auth.urllib.request.urlopen.called


@pytest.mark.parametrize('body',[b'not JSON',b'[]',b'null'])
def test_http_payload_rejects_invalid_shapes(local,monkeypatch,body):
    monkeypatch.setattr(auth.urllib.request,'urlopen',Mock(return_value=io.BytesIO(body)))
    with pytest.raises(auth.WallyAuthError,match='non-JSON|unexpected payload'):
        auth._http_json('https://example.com/fixture')


@pytest.mark.parametrize('error',[urllib.error.URLError('fixture offline'),TimeoutError('fixture timeout'),OSError('fixture IO')])
def test_network_failure_is_explicit_and_never_leaks_the_request_token(local,monkeypatch,error):
    monkeypatch.setattr(auth.urllib.request,'urlopen',Mock(side_effect=error))
    with pytest.raises(auth.WallyAuthError) as exc:
        auth._http_json('https://example.com/fixture',token='synthetic-sensitive')
    assert 'network failure' in str(exc.value) and 'synthetic-sensitive' not in str(exc.value)


def test_http_status_failure_is_distinct_from_connection_failure(local,monkeypatch):
    monkeypatch.setattr(auth.urllib.request,'urlopen',Mock(side_effect=urllib.error.HTTPError('https://example.com',429,'fixture quota',{},io.BytesIO(b'fixture limit'))))
    with pytest.raises(auth.WallyAuthError,match='HTTP 429.*fixture limit'):
        auth._http_json('https://example.com/fixture')


@pytest.mark.parametrize('days',[0,7])
def test_account_summary_converts_microdollars_and_does_not_return_tokens(local,monkeypatch,days):
    local.write_text(json.dumps({'access_token':'synthetic-access','refresh_token':'synthetic-refresh','console_url':'https://example.com','expires_at':20000}))
    request=Mock(side_effect=[io.BytesIO(json.dumps({'email':'fixture@example.invalid','plan':'fixture','tokens_this_month':20}).encode()),io.BytesIO(json.dumps({'credit':{'balance_micros':1234567,'granted_micros':2000000,'spent_micros':765433},'totals':{'requests':3,'prompt_tokens':10,'completion_tokens':8,'cached_tokens':2}}).encode())])
    monkeypatch.setattr(auth.urllib.request,'urlopen',request)
    info=auth.wally_account_info(days=days)
    assert info['balance_usd']==1.234567 and info['requests']==3 and info['cached_tokens']==2
    assert f'days={max(1,days)}' in request.call_args.args[0].full_url
    assert 'synthetic-access' not in json.dumps(info) and 'synthetic-refresh' not in json.dumps(info)


@pytest.mark.parametrize('error_index',[0,1])
def test_account_errors_preserve_available_metadata_without_fabricating_usage(local,monkeypatch,error_index):
    local.write_text(json.dumps({'access_token':'synthetic','console_url':'https://example.com','expires_at':20000,'plan':'fixture'}))
    responses=[io.BytesIO(b'{"email":"fixture@example.invalid"}'),io.BytesIO(b'{}')]
    responses[error_index]=urllib.error.URLError('fixture offline')
    monkeypatch.setattr(auth.urllib.request,'urlopen',Mock(side_effect=responses))
    info=auth.wally_account_info()
    assert ('error' in info)==(error_index==0) and ('usage_error' in info)==(error_index==1)
    assert 'balance_usd' not in info and info['plan']=='fixture'


def test_model_catalog_skips_malformed_entries_and_keeps_zero_prices(local,monkeypatch):
    local.write_text(json.dumps({'access_token':'synthetic','console_url':'https://example.com','expires_at':20000}))
    payload={'models':[None,{}, {'id':' fixture-model ','display_name':'Fixture','input_per_mtok':1200000,'output_per_mtok':None,'cached_input_per_mtok':0}]}
    monkeypatch.setattr(auth.urllib.request,'urlopen',Mock(return_value=io.BytesIO(json.dumps(payload).encode())))
    assert auth.wally_model_catalog()==[{'id':'fixture-model','display_name':'Fixture','input_per_mtok':1.2,'output_per_mtok':None,'cached_input_per_mtok':0.0}]


def test_model_list_deduplicates_ids_and_preserves_available_catalog_fallback(local,monkeypatch):
    local.write_text(json.dumps({'access_token':'synthetic','console_url':'https://example.com','expires_at':20000}))
    monkeypatch.setattr(auth.urllib.request,'urlopen',Mock(side_effect=[io.BytesIO(b'{"data":[{"id":"a"},{"id":"a"},"b",null]}'),urllib.error.URLError('fixture offline')]))
    assert auth.list_wally_models()==['a','b']
    assert auth.list_wally_models()==list(auth._FALLBACK_MODELS)
