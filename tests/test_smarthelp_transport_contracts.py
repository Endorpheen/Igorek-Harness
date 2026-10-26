"""SmartHelp provider requests and progress callbacks with no external execution."""
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.error

import pytest

from factory.core import smarthelp
from factory.core import cc_backend_executor, cc_backend_tools


@pytest.fixture
def local(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','synthetic-fixture')
    monkeypatch.setenv('FACTORY_SMART_ROUTING_STRONG_MODEL','fixture-model')
    monkeypatch.setattr(smarthelp,'tempfile',SimpleNamespace(mkdtemp=lambda **kwargs:str(tmp_path/'artifacts')))
    monkeypatch.setattr(cc_backend_tools,'get_tool_schemas',lambda:[])
    monkeypatch.setattr(cc_backend_tools,'web_tools_available',lambda:False)
    return tmp_path


def test_query_serializes_utf8_and_returns_only_answer_and_safe_usage(local,monkeypatch):
    call=Mock(return_value=io.BytesIO(json.dumps({'choices':[{'message':{'content':' Fixture answer '}}],'usage':{'prompt_tokens':3,'completion_tokens':4}}).encode()))
    monkeypatch.setattr(smarthelp.urllib.request,'urlopen',call)
    answer,log=smarthelp.smarthelp_query('Привет 🐙')
    request=call.call_args.args[0]
    assert answer=='Fixture answer' and 'in=3 out=4' in log and 'synthetic-fixture' not in log
    assert json.loads(request.data)['messages'][-1]['content']=='Привет 🐙'
    assert request.get_header('Authorization')=='Bearer synthetic-fixture'
    assert call.call_args.kwargs=={'timeout':60}


@pytest.mark.parametrize('body',[{}, {'choices':[]},{'choices':[{'message':{}}]},{'choices':[{'message':{'content':'   '}}]}])
def test_empty_provider_answer_is_not_reported_as_success(local,monkeypatch,body):
    monkeypatch.setattr(smarthelp.urllib.request,'urlopen',Mock(return_value=io.BytesIO(json.dumps(body).encode())))
    with pytest.raises(smarthelp.SmarthelpError,match='Пустой ответ'):smarthelp.smarthelp_query('fixture')


@pytest.mark.parametrize('failure',[urllib.error.HTTPError('https://example.com',429,'fixture quota',{},io.BytesIO(b'fixture limit')),urllib.error.URLError('fixture offline')])
def test_provider_failure_is_an_actionable_smarthelp_error(local,monkeypatch,failure):
    monkeypatch.setattr(smarthelp.urllib.request,'urlopen',Mock(side_effect=failure))
    with pytest.raises(smarthelp.SmarthelpError):smarthelp.smarthelp_query('fixture')


@pytest.mark.parametrize('method',['plain','agent'])
def test_missing_key_fails_before_any_provider_or_executor_call(local,monkeypatch,method):
    monkeypatch.delenv('OPENROUTER_API_KEY',raising=False);monkeypatch.delenv('FACTORY_OPENROUTER_API_KEY',raising=False)
    call=Mock(side_effect=AssertionError('Unexpected provider call'))
    monkeypatch.setattr(smarthelp.urllib.request,'urlopen',call)
    monkeypatch.setattr(cc_backend_executor,'execute_cc_backend_task',call)
    with pytest.raises(smarthelp.SmarthelpError,match='не настроен'):
        if method=='plain':smarthelp.smarthelp_query('fixture')
        else:smarthelp.smarthelp_agent_query('fixture',workdir=str(local))
    assert not call.called


@pytest.mark.parametrize('name_key',['name','tool_name'])
def test_agent_progress_preserves_tool_identity_for_both_supported_callback_shapes(local,monkeypatch,name_key):
    progress=[]
    def execute(**kwargs):
        kwargs['progress_callback']('tool.started',**{name_key:'Read','args':{'file_path':'fixture.py'}})
        kwargs['progress_callback']('tool.completed',**{name_key:'Read','args':{'file_path':'fixture.py'},'result':'fixture bytes'})
        return {'status':'ok','output_text':' Fixture agent answer ','usage':{'prompt':5,'completion':6},'tool_calls_count':1}
    call=Mock(side_effect=execute);monkeypatch.setattr(cc_backend_executor,'execute_cc_backend_task',call)
    answer,log,events=smarthelp.smarthelp_agent_query('fixture',workdir=str(local),progress_callback=lambda event,**fields:progress.append((event,fields)))
    assert answer=='Fixture agent answer' and 'tools=1' in log and 'synthetic-fixture' not in log
    assert [e['tool'] for e in events]==['Read','Read']
    assert all(x[1]['tool_name']=='Read' and x[1]['preview']=='fixture.py' for x in progress)
    assert call.call_args.kwargs['filesystem_mode']=='workspace'
    assert call.call_args.kwargs['request_headers']['Authorization']=='Bearer synthetic-fixture'


@pytest.mark.parametrize('result',[{'status':'fail','failure_details':'fixture failure','output_text':''},{'status':'ok','output_text':'   '}])
def test_agent_failure_or_empty_answer_is_not_reported_as_completed_work(local,monkeypatch,result):
    monkeypatch.setattr(cc_backend_executor,'execute_cc_backend_task',Mock(return_value=result))
    with pytest.raises(smarthelp.SmarthelpError):smarthelp.smarthelp_agent_query('fixture',workdir=str(local))


@pytest.mark.parametrize('name,args,expected',[
    ('Bash',{'command':'ls\nfixture'},'ls fixture'),('Read',{'path':'file.py'},'file.py'),
    ('Grep',{'query':'needle'},'needle'),('Glob',{'path':'*.py'},'*.py'),
    ('web_search',{'query':'fixture'},'fixture'),('web_extract',{'urls':['https://example.com']},'https://example.com'),
    ('web_extract',{'url':'https://example.com'},'https://example.com'),
    ('delegate_task',{'prompt':'fixture goal'},'fixture goal'),('skill_view',{'name':'fixture'},'fixture'),
    ('Custom',{'a':1},'{"a": 1}'),('Custom',None,'{}'),
])
def test_tool_preview_is_a_bounded_single_line_without_execution(name,args,expected):
    assert smarthelp.build_tool_preview(name,args)==expected
    assert len(smarthelp.build_tool_preview(name,{'command':'x'*400,'pattern':'x'*400,'file_path':'x'*400,'query':'x'*400,'goal':'x'*400,'name':'x'*400}))<=80
