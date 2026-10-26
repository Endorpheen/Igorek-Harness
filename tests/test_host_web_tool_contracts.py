"""Controlled host web/search entry points with synthetic network boundaries."""
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.error

import pytest

from factory.core import cc_backend_tools as tools
from factory.core import web_tools_runtime


@pytest.fixture
def upstream(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY','synthetic-fixture-key')
    monkeypatch.setattr(web_tools_runtime,'active_web_tools',lambda:None)
    response = io.BytesIO(b'{"results":[]}')
    call=Mock(return_value=response)
    monkeypatch.setattr(tools.urllib.request,'urlopen',call)
    return call


def test_actual_request_serializes_utf8_and_explicit_auth_without_exposing_config(upstream):
    payload={'query':'Привет 🐙','max_results':2}
    assert tools._tavily_request('/search',payload)=={'results':[]}
    request=upstream.call_args.args[0]
    assert request.full_url==tools.TAVILY_BASE_URL+'/search' and request.get_method()=='POST'
    assert json.loads(request.data.decode('utf-8'))==payload
    assert request.get_header('Authorization')=='Bearer synthetic-fixture-key'
    assert upstream.call_args.kwargs=={'timeout':60}


@pytest.mark.parametrize('key',[None,'','   '])
def test_missing_key_fails_before_any_network_request(upstream,monkeypatch,key):
    if key is None:
        monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    else:
        monkeypatch.setenv('TAVILY_API_KEY',key)
    with pytest.raises(ValueError,match='not configured'):
        tools._tavily_request('search',{'query':'fixture'})
    assert not upstream.called


@pytest.mark.parametrize('body',[b'not JSON', b'[]', b'null', b'3'])
def test_invalid_upstream_payload_has_a_clear_error(upstream,body):
    upstream.return_value=io.BytesIO(body)
    with pytest.raises(RuntimeError,match='invalid JSON|non-object JSON'):
        tools._tavily_request('search',{'query':'fixture'})


@pytest.mark.parametrize('body',[b'fixture quota exceeded',None])
def test_http_failures_include_status_and_available_reason(upstream,body):
    upstream.side_effect=urllib.error.HTTPError('https://example.com',429,'fixture quota',{},None if body is None else io.BytesIO(body))
    with pytest.raises(RuntimeError,match='429.*fixture quota'):
        tools._tavily_request('search',{'query':'fixture'})


def test_connection_failure_is_distinct_from_json_parse_failure(upstream):
    upstream.side_effect=urllib.error.URLError('fixture connection refused')
    with pytest.raises(RuntimeError,match='connection error.*fixture connection refused'):
        tools._tavily_request('search',{'query':'fixture'})


def test_installed_provider_override_is_used_without_reading_host_key(upstream,monkeypatch):
    provider=SimpleNamespace(request=Mock(return_value={'results':[{'url':'https://example.com'}]}))
    monkeypatch.setattr(web_tools_runtime,'active_web_tools',lambda:provider)
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    assert tools._tavily_request('search',{'query':'fixture'})=={'results':[{'url':'https://example.com'}]}
    provider.request.assert_called_once_with('search',{'query':'fixture'})
    assert not upstream.called


@pytest.mark.parametrize('query',[None, '', '   ', 8, 'x'*4097])
def test_isolated_search_rejects_malformed_queries_before_network(upstream,query):
    with pytest.raises(ValueError,match='query'):
        tools.execute_isolated_web_tool('search',{'query':query})
    assert not upstream.called


@pytest.mark.parametrize('limit',[False, True, 0, 11, '3', None, 2.5])
def test_isolated_search_bounds_are_not_coerced_from_booleans_or_strings(upstream,limit):
    with pytest.raises(ValueError,match='limit'):
        tools.execute_isolated_web_tool('search',{'query':'fixture','limit':limit})
    assert not upstream.called


def test_isolated_search_uses_actual_normalizer_and_removes_echoed_host_secret(upstream):
    upstream.return_value=io.BytesIO(json.dumps({'results':[None,{'title':' fixture ', 'url':'https://example.com','content':'synthetic-fixture-key'}]}).encode())
    result=json.loads(tools.execute_isolated_web_tool('search',{'query':'fixture','limit':3}))
    assert result=={'success':True,'data':{'web':[{'title':'fixture','url':'https://example.com','description':'[redacted]','position':2}]}}
    assert json.loads(upstream.call_args.args[0].data)['max_results']==3


@pytest.mark.parametrize('urls', [[],None,'https://example.com', ['https://example.com']*11])
def test_isolated_extraction_requires_a_bounded_list(upstream,urls):
    with pytest.raises(ValueError):
        tools.execute_isolated_web_tool('extract',{'urls':urls})
    assert not upstream.called


@pytest.mark.parametrize('url', ['file:///tmp/fixture','http://localhost','http://test.localhost','http://printer.local','http://host.internal','http://device.lan','http://127.0.0.1','http://[::1]','http://10.0.0.1','http://169.254.169.254','https://user:pass@example.com','https://example.com:444','https://example.com:bad'])
def test_isolated_extraction_refuses_internal_or_credentialed_urls_before_request(upstream,url):
    with pytest.raises(ValueError):
        tools.execute_isolated_web_tool('extract',{'urls':[url]})
    assert not upstream.called


def test_isolated_extraction_preserves_success_and_per_document_failure_details(upstream):
    upstream.return_value=io.BytesIO(json.dumps({'results':[None,{'url':'https://example.com/a','title':' A ','raw_content':'body','content':'fallback'}, {'url':'https://example.com/b','content':'fallback'}], 'failed_results':[None,{'url':'https://example.com/c','error':'fixture blocked'}], 'failed_urls':['', 'https://example.com/d']}).encode())
    result=json.loads(tools.execute_isolated_web_tool('extract',{'urls':['https://example.com/a','https://8.8.8.8']}))
    documents=result['data']['documents']
    assert result['success'] and len(documents)==4
    assert documents[0]['content']=='body' and documents[1]['content']=='fallback'
    assert documents[2]['error']=='fixture blocked' and documents[3]['error']=='extraction failed'
    assert documents[0]['metadata']=={'sourceURL':'https://example.com/a','title':'A'}


@pytest.mark.parametrize('operation',['', 'unknown', 'bash'])
def test_unsupported_operation_cannot_reach_network(upstream,operation):
    with pytest.raises(ValueError,match='unsupported'):
        tools.execute_isolated_web_tool(operation,{'query':'fixture'})
    assert not upstream.called


@pytest.mark.parametrize('args',[
    {'query':8}, {'query':'x'*4097}, {'limit':True}, {'limit':0}, {'limit':6},
    {'limit':'3'}, {'role_filter':None}, {'role_filter':'x'*65}, {'mode':'unknown'},
])
def test_isolated_session_search_validates_before_touching_session_store(monkeypatch,args):
    search=Mock(side_effect=AssertionError('Unexpected session store access'))
    monkeypatch.setattr(tools,'_tool_session_search',search)
    with pytest.raises(ValueError):
        tools.execute_isolated_session_search({'query':'fixture',**args},current_session='fixture-session')
    assert not search.called


@pytest.mark.parametrize('mode',['lexical','semantic','hybrid'])
def test_isolated_session_search_forwards_valid_fields_and_session_identity(monkeypatch,mode):
    search=Mock(return_value='{"success":true}')
    monkeypatch.setattr(tools,'_tool_session_search',search)
    assert tools.execute_isolated_session_search({'query':'fixture','limit':2,'mode':mode},current_session='fixture-session')=='{"success":true}'
    search.assert_called_once_with({'query':'fixture','limit':2,'mode':mode,'role_filter':'user,assistant'},current_session='fixture-session')
