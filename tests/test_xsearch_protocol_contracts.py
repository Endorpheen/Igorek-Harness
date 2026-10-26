"""Bundled X-search framing, credentials and HTTP errors with an owned dotenv root."""
import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest

from factory.mcp_servers import xsearch_server as x
from test_bundled_mcp_stdio_contracts import exchange


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    for key in ['XSEARCH_BEARER_TOKEN','TWITTER_BEARER_TOKEN']:monkeypatch.delenv(key,raising=False)
    path=tmp_path/'factory/mcp_servers/xsearch_server.py';path.parent.mkdir(parents=True)
    monkeypatch.setattr(x,'__file__',str(path))
    call=Mock(return_value=io.BytesIO(b'{"data":[],"meta":{"result_count":0}}'))
    monkeypatch.setattr(x.urllib_request,'build_opener',Mock(return_value=type('Opener',(),{'open':call})()))
    return path.parents[1],call


def test_initialize_catalog_ping_and_notification_keep_protocol_ids(monkeypatch):
    responses=exchange(x,monkeypatch,[{'id':'init','method':'initialize'},{'method':'notifications/initialized'},{'id':'catalog','method':'tools/list'},{'id':'ping','method':'ping'},{'id':'unsupported','method':'other'}])
    assert [r['id'] for r in responses]==['init','catalog','ping','unsupported']
    assert responses[0]['result']['serverInfo']['name']==x.SERVER_NAME
    assert responses[1]['result']['tools'][0]['name']=='xsearch_query'
    assert responses[-1]['error']['code']==-32601


@pytest.mark.parametrize('frame',[None,[],[1],42,True,'request'])
def test_nonobject_frame_is_rejected_without_killing_the_next_request(monkeypatch,frame):
    rows=exchange(x,monkeypatch,[json.dumps(frame),{'id':'next','method':'ping'}])
    assert rows[0]['id'] is None and rows[0]['error']['code']==-32600
    assert rows[1]['id']=='next' and rows[1]['result']=={}


def test_invalid_json_cannot_reuse_the_previous_request_identity(monkeypatch):
    rows=exchange(x,monkeypatch,[{'id':'previous','method':'ping'},'{bad}',{'id':'next','method':'ping'}])
    assert [r['id'] for r in rows]==['previous',None,'next']


@pytest.mark.parametrize('params',[[],['bad'],'bad',0,False,True])
def test_nonobject_params_are_rejected_at_the_framing_boundary(monkeypatch,params):
    rows=exchange(x,monkeypatch,[{'id':'bad','method':'tools/call','params':params},{'id':'next','method':'tools/list'}])
    assert rows[0]['id']=='bad' and rows[0]['error']['code']==-32602
    assert 'params' in rows[0]['error']['message'].lower()
    assert rows[1]['result']['tools']


@pytest.mark.parametrize('arguments',[[],['bad'],'bad',0,False,True])
def test_nonobject_tool_arguments_are_rejected_before_provider_call(monkeypatch,arguments,isolated):
    _,call=isolated
    rows=exchange(x,monkeypatch,[{'id':'bad','method':'tools/call','params':{'name':'xsearch_query','arguments':arguments}},{'id':'next','method':'ping'}])
    assert rows[0]['id']=='bad' and rows[0]['error']['code']==-32602
    assert not call.called and rows[1]['result']=={}


def test_dotenv_priority_uses_owned_files_and_does_not_overwrite_inherited_values(isolated,monkeypatch):
    root,_=isolated
    monkeypatch.setenv('XSEARCH_FIXTURE_INHERITED','process')
    monkeypatch.setenv('XSEARCH_FIXTURE_VALUE','before-fixture')
    monkeypatch.delenv('XSEARCH_FIXTURE_VALUE')
    (root/'.env.gateway').write_text('# comment\nignored\nXSEARCH_FIXTURE_INHERITED=file\nXSEARCH_FIXTURE_VALUE="gateway"\n')
    (root/'.env').write_text('XSEARCH_FIXTURE_VALUE=dotenv\n')
    (root/'.env.local').write_text('XSEARCH_FIXTURE_VALUE=local\n')
    x._load_dotenv()
    assert x.os.environ['XSEARCH_FIXTURE_INHERITED']=='process' and x.os.environ['XSEARCH_FIXTURE_VALUE']=='gateway'


@pytest.mark.parametrize('key',['XSEARCH_BEARER_TOKEN','TWITTER_BEARER_TOKEN'])
def test_readonly_search_encodes_query_and_uses_explicit_bearer_without_network(isolated,monkeypatch,key):
    _,call=isolated;monkeypatch.setenv(key,'synthetic-fixture')
    assert x._search_x('Привет 🐙 & lang:en',12)['data']==[]
    request=call.call_args.args[0]
    params=parse_qs(urlsplit(request.full_url).query)
    assert params['query']==['Привет 🐙 & lang:en'] and params['max_results']==['12']
    assert request.get_header('Authorization')=='Bearer synthetic-fixture' and request.get_method()=='GET'
    assert call.call_args.kwargs=={'timeout':120}


def test_missing_bearer_fails_before_any_upstream_request(isolated):
    _,call=isolated
    with pytest.raises(RuntimeError,match='XSEARCH_BEARER_TOKEN'):x._search_x('fixture',10)
    assert not call.called


@pytest.mark.parametrize('reply',[b'',b'   ',b'not JSON'])
def test_empty_or_nonjson_provider_reply_is_explicit(isolated,monkeypatch,reply):
    _,call=isolated;monkeypatch.setenv('XSEARCH_BEARER_TOKEN','synthetic')
    call.return_value=io.BytesIO(reply)
    with pytest.raises(RuntimeError,match='пустой|не-JSON'):x._search_x('fixture',10)


@pytest.mark.parametrize('failure',[HTTPError('https://example.com',429,'fixture quota',{},io.BytesIO(b'fixture limit')),URLError('fixture offline')])
def test_provider_status_and_connection_failure_keep_distinct_explanations(isolated,monkeypatch,failure):
    _,call=isolated;monkeypatch.setenv('XSEARCH_BEARER_TOKEN','synthetic')
    call.side_effect=failure
    with pytest.raises(RuntimeError,match='429.*fixture limit|unavailable.*fixture offline'):x._search_x('fixture',10)


@pytest.mark.parametrize('limit,expected',[(1,10),(10,10),(50,50),(200,100),('20',20)])
def test_tool_results_limit_is_bounded_without_losing_request_context(monkeypatch,limit,expected):
    search=Mock(return_value={'data':[]});monkeypatch.setattr(x,'_search_x',search)
    result=x._handle_tool_call({'query':' fixture ','max_results':limit})
    search.assert_called_once_with(query='fixture',max_results=expected)
    assert result['content'][0]['type']=='text' and 'fixture' in result['content'][0]['text']


@pytest.mark.parametrize('args',[{}, {'query':''},{'query':'fixture','max_results':None},{'query':'fixture','max_results':'bad'}])
def test_invalid_tool_fields_fail_before_reading_provider_data(monkeypatch,args):
    search=Mock(side_effect=AssertionError('Unexpected provider call'));monkeypatch.setattr(x,'_search_x',search)
    with pytest.raises(RuntimeError):x._handle_tool_call(args)
    assert not search.called


def test_formatter_does_not_invent_an_author_or_post_url():
    text=x._format_search_response({'data':[{'id':'fixture','text':'Original fixture'}]},'fixture',10)
    assert 'URL: unavailable' in text and 'Автор: unknown' in text and 'Original fixture' in text
