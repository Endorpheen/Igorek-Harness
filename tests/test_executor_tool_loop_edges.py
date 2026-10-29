"""Actual tool-loop recovery and terminal transitions with synthetic boundaries."""
import copy
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_completion_control import finish as finish_signal

from factory.core import cc_backend_executor as e, cc_backend_tools as tools, desktop_browser
from test_executor_workflow_guards import response
from test_tool_call_salvage import FIXTURES


@pytest.fixture
def loop(tmp_path,monkeypatch):
    work=tmp_path/'work';work.mkdir();(work/'input.txt').write_text('fixture input')
    artifact=tmp_path/'artifacts';artifact.mkdir()
    monkeypatch.setenv('FACTORY_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('IGOREK_FACTORY_HOME',str(tmp_path/'data'))
    monkeypatch.setenv('FACTORY_VAULT_ENABLED','false')
    monkeypatch.setattr(e,'_resolve_backend_context_window',lambda **k:(65536,'fixture'))
    monkeypatch.setattr(e,'is_interrupt_requested',lambda:False)
    monkeypatch.setattr(e,'task_should_stop',lambda _:False)
    monkeypatch.setattr(tools,'web_tools_available',lambda:True)
    schemas={row['name']:row for row in tools.get_tool_schemas()}
    schemas.setdefault('desktop_browser',desktop_browser.SCHEMA)
    for name in ['create_schedule','fal_generate_image']:
        schemas.setdefault(name,{'name':name,'description':'Fixture tool boundary','input_schema':{'type':'object','properties':{}}})
    def run(api,bodies,*,names=(),raw='fixture tool output',metadata=None,**kwargs):
        replies=deque(bodies);requests=[];calls=[]
        def post(url,payload,*args,**options):
            requests.append(copy.deepcopy(payload))
            assert replies,'Unbounded model retry in fixture'
            return replies.popleft(),1
        def execute(name,arguments,*args,**options):
            calls.append((name,copy.deepcopy(arguments)))
            if isinstance(raw,Exception):raise raw
            return raw
        monkeypatch.setattr(e,'_http_post',post);monkeypatch.setattr(e,'_http_post_streaming_openai',post);monkeypatch.setattr(e,'execute_tool',execute)
        result=e.execute_cc_backend_task(base_url=kwargs.pop('base_url','http://127.0.0.1:1'),model='fixture',api_format=api,system_prompt='fixture system',user_message='Respond to fixture',workdir=str(work),artifact_workdir=str(artifact),enabled_tools=[schemas[n] for n in names],metadata={'model_context_window':65536,**(metadata or {})},approvals_enabled_provider=lambda:False,**kwargs)
        trace=[json.loads(row) for row in (artifact/'cc_backend_trace.jsonl').read_text().splitlines()]
        return SimpleNamespace(result=result,requests=requests,calls=calls,trace=trace,work=work)
    return run


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_recorded_textual_read_call_is_recovered_once_before_final_answer(loop,api):
    fixture=(FIXTURES/'bracket_read_tool_use.txt').read_text()
    fixture=fixture.replace('/home/example/AI/paroquant-test/Ledger.md','input.txt')
    run=loop(api,[response(api,fixture),response(api,'Fixture read complete')],names=['Read'])
    assert run.result['status']=='ok' and run.result['output_text']=='Fixture read complete'
    assert run.calls==[('Read',{'file_path':str(run.work/'input.txt'),'offset':1,'limit':80})]
    assert any(row['kind']=='planner.salvage_success' for row in run.trace)
    assert len(run.requests)==2


@pytest.mark.parametrize('api',['anthropic','openai'])
@pytest.mark.parametrize('text',['Read(file_path=None)','Read(file_path=[])'])
def test_repeated_invalid_salvage_never_executes_and_has_bounded_failure(loop,api,text):
    run=loop(api,[response(api,text),response(api,text)],names=['Read'])
    assert run.result['status']=='fail' and run.result['failure_reason']=='invalid_salvaged_tool_call'
    assert run.calls==[] and len(run.requests)==2
    assert sum(row['kind']=='planner.salvage_rejected' for row in run.trace)==2


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_redirected_fetch_uses_only_dedicated_web_tool(loop,monkeypatch,api):
    from factory.core.llama_image_input import LlamaImageInput
    def confirmed(policy):
        policy.backend = "llama_cpp"
        return None
    monkeypatch.setattr(LlamaImageInput, "_probe", confirmed)
    tool={'id':'fetch-one','name':'Bash','input':{'command':'curl -sL https://example.test/article'}}
    run=loop(api,[response(api,tool=tool),response(api,'Fixture source checked'), finish_signal(api, 'Fixture source checked', requires=True, ids=['fetch-one'])],names=['Bash','web_extract'])
    assert run.result['status']=='ok'
    assert run.calls==[('web_extract',{'urls':['https://example.test/article']})]
    assert 'Redirected: curl/wget -> web_extract' in json.dumps(run.requests[1])


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_schedule_success_completes_without_another_model_round(loop,api):
    tool={'id':'schedule-one','name':'create_schedule','input':{'name':'Fixture schedule','schedule':'2026-12-01 12:00','payload':{'text':'fixture'}}}
    run=loop(api,[response(api,tool=tool)],names=['create_schedule'],raw=json.dumps({'status':'created','message':'Fixture schedule recorded'}))
    assert run.result['status']=='ok' and run.result['output_text']=='Fixture schedule recorded'
    assert len(run.requests)==1 and len(run.calls)==1
    assert run.trace[-1]['kind']=='task.finish' and run.trace[-1]['reason']=='schedule_created'


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_terminal_skill_deletion_is_reported_from_tool_result(loop,api):
    tool={'id':'delete-one','name':'skill_manage','input':{'action':'delete','name':'fixture-skill'}}
    run=loop(api,[response(api,tool=tool)],names=['skill_manage'],raw='{"success":true}')
    assert run.result['status']=='ok' and 'fixture-skill' in run.result['output_text']
    assert len(run.requests)==1 and run.trace[-1]['reason']=='terminal_skill_manage_delete'


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_browser_screenshot_receipt_adds_only_matching_image_to_next_request(loop,monkeypatch,api):
    image={'type':'image','source':{'type':'base64','media_type':'image/jpeg','data':'Zml4dHVyZQ=='}}
    monkeypatch.setattr(desktop_browser,'_IMAGES',{'owned-receipt':copy.deepcopy(image),'other-receipt':{'keep':True}})
    tool={'id':'shot-one','name':'desktop_browser','input':{'action':'screenshot'}}
    run=loop(api,[response(api,tool=tool),response(api,'Fixture screenshot inspected')],names=['desktop_browser'],raw='{"screenshot":{"id":"owned-receipt"}}')
    assert run.result['status']=='ok' and len(run.calls)==1
    assert 'owned-receipt' not in desktop_browser._IMAGES and desktop_browser._IMAGES['other-receipt']=={'keep':True}
    payload=json.dumps(run.requests[1])
    assert 'Zml4dHVyZQ==' in payload


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_unknown_structured_tool_does_not_reach_executor(loop,api):
    tool={'id':'unknown-one','name':'nonexistent_fixture_tool','input':{}}
    run=loop(api,[response(api,tool=tool),response(api,'Fixture tool unavailable')],names=['Read'])
    assert run.result['status']=='ok' and run.calls==[]
    if api=='openai':
        assert len(run.requests)==1 and 'unsupported tool' in run.result['output_text']
    else:
        assert "Unknown tool 'nonexistent_fixture_tool'" in json.dumps(run.requests[1])


def test_telegram_transport_suffix_is_added_once_to_model_request(loop):
    run=loop('anthropic',[response('anthropic','Fixture reply')],source_platform='telegram')
    assert run.result['status']=='ok' and '/no_think' in run.requests[0]['system']


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_pending_invalid_batch_items_are_filtered_without_executing_them(loop,api):
    forced={'name':'Read','input':{'file_path':'input.txt'},'tool_use_id':'forced-one','pending_tool_calls':[None,{}, {'id':'missing-fields','name':'Read','input':{}}]}
    run=loop(api,[response(api,'Fixture unavailable fields explained')],names=['Read'],metadata={'forced_tool_call':forced})
    assert run.result['status']=='ok' and run.calls==[('Read',{'file_path':'input.txt'})]
    assert 'missing required field' in json.dumps(run.requests[0])


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_normal_missing_required_field_never_reaches_tool_executor(loop,api):
    tool={'id':'missing-one','name':'Read','input':{}}
    run=loop(api,[response(api,tool=tool),response(api,'Fixture required field unavailable')],names=['Read'])
    assert run.calls==[] and run.result['status']=='ok'
    assert 'missing required field' in json.dumps(run.requests[1])


@pytest.mark.parametrize('api',['anthropic','openai'])
@pytest.mark.parametrize('fixture_name',['deepseek_dsml_quoted_report.txt','arxiv_markdown_list_not_tool_artifact.txt'])
def test_quoted_report_and_research_prose_do_not_become_tool_calls(loop,api,fixture_name):
    text=(FIXTURES/fixture_name).read_text()
    run=loop(api,[response(api,text)],names=['Read','Bash','Write'])
    assert run.result['status']=='ok' and run.calls==[] and len(run.requests)==1
    assert not any(row['kind']=='planner.salvage_success' for row in run.trace)


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_ordinary_code_example_is_preserved_as_answer_without_execution(loop,api):
    text="Example:\n```python\nprint('fixture')\n```"
    run=loop(api,[response(api,text)],names=['Read','Bash','Write'])
    assert run.result['status']=='ok' and run.result['output_text']==text
    assert run.calls==[] and len(run.requests)==1


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_changed_prompt_cannot_repeat_same_media_generator(loop,api):
    first={'id':'media-one','name':'fal_generate_image','input':{'prompt':'first'}}
    second={'id':'media-two','name':'fal_generate_image','input':{'prompt':'second'}}
    run=loop(api,[response(api,tool=first),response(api,tool=second),response(api,'Fixture generation already requested')],names=['fal_generate_image'],raw='fixture generation queued')
    assert run.result['status']=='ok' and run.calls==[('fal_generate_image',{'prompt':'first'})]
    assert len(run.requests)==3


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_readonly_task_policy_blocks_write_before_executor(loop,api):
    tool={'id':'write-one','name':'Write','input':{'file_path':'out.txt','content':'fixture'}}
    run=loop(api,[response(api,tool=tool),response(api,'Fixture policy denial explained')],names=['Read','Write'],metadata={'restrict_tools_to':['Read']})
    assert run.result['status']=='ok' and run.calls==[] and not (run.work/'out.txt').exists()
    assert 'not allowed for this task by policy' in json.dumps(run.requests[1])


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_suspended_context_filters_bad_entries_and_deduplicates_user_message(loop,api):
    context={'role':'user','content':[{'type':'text','text':'owned context fixture'}]}
    forced={'name':'Read','input':{'file_path':'input.txt'},'tool_use_id':'read-one','approval_context_messages':[context]}
    run=loop(api,[response(api,'Fixture resumed context handled')],names=['Read'],metadata={'forced_tool_call':forced,'approval_context_messages':[None,context,copy.deepcopy(context)]})
    assert run.result['status']=='ok' and len(run.calls)==1
    assert json.dumps(run.requests[0]).count('owned context fixture')==1


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_failed_steer_provider_does_not_abort_successful_answer(loop,api):
    provider=Mock(side_effect=RuntimeError('fixture queue unavailable'))
    run=loop(api,[response(api,'Fixture answer retained')],pending_steer_provider=provider)
    assert run.result['status']=='ok' and run.result['output_text']=='Fixture answer retained'
    assert run.calls==[] and provider.called


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_empty_and_wrong_type_steers_never_create_empty_user_turns(loop,api):
    provider=Mock(side_effect=[[None,{}, {'text':'  '},{'text':'fixture pending','message_id':'owned-message'}],[],[]])
    run=loop(api,[response(api,'Fixture first answer'),response(api,'Fixture pending message handled')],pending_steer_provider=provider)
    assert run.result['status']=='ok' and run.calls==[]
    assert len(run.requests)==2 and json.dumps(run.requests[1]).count('fixture pending')==1
    assert sum(row['kind']=='steer.delivered' for row in run.trace)==1
