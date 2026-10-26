"""Actual executor loop guard transitions with synthetic provider/tool boundaries."""
import copy
import json
from collections import deque

import pytest

from factory.core import cc_backend_executor as executor
from factory.core.cc_backend_tools import get_tool_schemas
from factory.core.iteration_budget import IterationBudget


def response(api,text='',tool=None,**extra):
    if api=='openai':
        message={'role':'assistant','content':text}
        if tool:message['tool_calls']=[{'id':tool['id'],'type':'function','function':{'name':tool['name'],'arguments':json.dumps(tool['input'])}}]
        return {'choices':[{'message':message,'finish_reason':'tool_calls' if tool else 'stop'}],'usage':{'prompt_tokens':2,'completion_tokens':3},**extra}
    content=[{'type':'text','text':text}] if text else []
    if tool:content.append({'type':'tool_use',**tool})
    return {'content':content,'usage':{'input_tokens':2,'output_tokens':3},**extra}


@pytest.fixture
def execute(tmp_path,monkeypatch):
    work=tmp_path/'work';work.mkdir();(work/'input.txt').write_text('fixture input')
    artifact=tmp_path/'artifacts';artifact.mkdir()
    monkeypatch.setenv('IGOREK_FACTORY_HOME',str(tmp_path/'data'))
    def run(api,bodies,*,names=(),metadata=None,tool_result='fixture tool result',tool_error=None,**kwargs):
        queue,requests,calls=deque(bodies),[],[]
        def post(url,payload,*args,**options):
            requests.append(copy.deepcopy(payload))
            if api == 'openai':
                assert 'system' not in payload
                assert payload['messages'][0]['role'] == 'system'
            assert queue,'Unexpected unbounded fixture model retry'
            return queue.popleft(),1
        def tool(name,arguments,*args,**options):
            calls.append((name,copy.deepcopy(arguments)))
            if tool_error:raise tool_error
            return tool_result
        monkeypatch.setattr(executor,'_http_post',post)
        monkeypatch.setattr(executor,'_http_post_streaming_openai',post)
        monkeypatch.setattr(executor,'execute_tool',tool)
        schemas=[x for x in get_tool_schemas() if x['name'] in names]
        for name in names:
            if name.startswith('fal_'):schemas.append({'name':name,'description':'Fixture artifact tool','input_schema':{'type':'object','properties':{'prompt':{'type':'string'}},'required':['prompt']}})
        result=executor.execute_cc_backend_task(base_url='http://127.0.0.1:1',api_format=api,model='fixture',system_prompt='fixture',user_message='Respond to fixture',workdir=str(work),artifact_workdir=str(artifact),enabled_tools=schemas,metadata={'model_context_window':65536,**(metadata or {})},**kwargs)
        events=[json.loads(x) for x in (artifact/'cc_backend_trace.jsonl').read_text().splitlines()]
        return result,requests,calls,events,work
    return run


@pytest.mark.parametrize('recover',[False,True])
def test_policy_denial_retries_are_bounded_and_recovery_uses_actual_later_text(execute,recover):
    denied=response('anthropic','Fixture refusal',policy_denial='fixture blocked scope')
    result,requests,calls,events,_=execute('anthropic',[denied,response('anthropic','Actual fixture answer') if recover else denied])
    assert len(requests)==2 and calls==[]
    if recover:assert result['status']=='ok' and result['output_text']=='Actual fixture answer'
    else:assert result['status']=='fail' and result['failure_reason']=='policy_denial_repeated'
    assert any(x['kind']=='policy.denial_retry' for x in events)


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_midstream_cancellation_never_becomes_a_completed_answer(execute,api):
    result,requests,calls,events,_=execute(api,[{'__interrupted__':True,'error':'interrupted'}])
    assert result['status']=='fail' and result['failure_reason']=='interrupted'
    assert len(requests)==1 and calls==[] and events[-1]['reason']=='interrupted'


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_explicit_round_limit_does_not_turn_an_incomplete_tool_turn_into_success(execute,api,tmp_path,monkeypatch):
    path=tmp_path/'limits.yaml';path.write_text('cc_backend:\n  max_tool_rounds: 1\n')
    monkeypatch.setenv('IGOREK_AGENT_LOOP_LIMITS_CONFIG',str(path))
    tool={'id':'read-one','name':'Read','input':{'file_path':'input.txt'}}
    result,requests,calls,events,_=execute(api,[response(api,tool=tool)],names=['Read'])
    assert result['status']=='fail' and result['failure_reason']=='max_tool_rounds_reached'
    assert len(requests)==1 and calls==[('Read',{'file_path':'input.txt'})]
    assert events[-1]['reason']=='max_rounds'


def test_exhausted_iteration_budget_stops_before_a_provider_request(execute):
    budget=IterationBudget(1);assert budget.consume()
    result,requests,calls,_,_=execute('anthropic',[],iteration_budget=budget)
    assert result['failure_reason']=='iteration_budget_exhausted' and requests==calls==[]


@pytest.mark.parametrize('source,status',[('android','waiting_approval'),('cli','fail')])
def test_resumed_batch_keeps_pending_ids_reasoning_and_remaining_calls_until_approval(execute,source,status):
    forced={'name':'Read','input':{'file_path':'input.txt'},'tool_use_id':'approved-read','source':'approval_resume','reasoning_content':'Fixture private reasoning','assistant_text':'Fixture visible preamble','pending_tool_calls':[{'id':'pending-write','name':'Write','input':{'file_path':'out.txt','content':'new'}},{'id':'remaining-read','name':'Read','input':{'file_path':'input.txt'}}]}
    result,requests,calls,_,work=execute('anthropic',[],names=['Read','Write'],metadata={'forced_tool_call':forced},source_platform=source)
    assert result['status']==status and requests==[] and calls==[('Read',{'file_path':'input.txt'})]
    blocked=result['approval_blocked_tools'][0]
    assert blocked['tool_use_id']=='pending-write' and blocked['pending_tool_calls'][0]['id']=='remaining-read'
    assert blocked['reasoning_content']=='Fixture private reasoning' and blocked['assistant_text']=='Fixture visible preamble'
    assert blocked['approval_context_messages'] and not (work/'out.txt').exists()


def test_forced_tool_failure_is_preserved_in_followup_context_without_false_file_success(execute):
    forced={'name':'Read','input':{'file_path':'input.txt'},'tool_use_id':'fixture-read'}
    result,requests,calls,_,_=execute('anthropic',[response('anthropic','Fixture read unavailable; no file was changed')],names=['Read'],metadata={'forced_tool_call':forced},tool_error=RuntimeError('fixture read unavailable'))
    assert result['status']=='ok' and 'read unavailable' in result['output_text']
    assert 'Error executing Read: fixture read unavailable' in json.dumps(requests[0]) and len(requests)==len(calls)==1


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_progress_consumer_exception_cannot_abort_a_valid_tool_and_final_answer(execute,api):
    def broken(*args,**kwargs):raise RuntimeError('fixture UI consumer unavailable')
    tool={'id':'read-one','name':'Read','input':{'file_path':'input.txt'}}
    result,requests,calls,_,_=execute(api,[response(api,'Reading fixture',tool),response(api,'Verified final fixture answer')],names=['Read'],progress_callback=broken)
    assert result['status']=='ok' and result['output_text']=='Verified final fixture answer'
    assert len(calls)==1 and len(requests)==2


@pytest.mark.parametrize('api',['anthropic','openai'])
@pytest.mark.parametrize('name',['fal_generate_image','fal_edit_image'])
def test_completed_media_tool_uses_one_terminal_response_without_repeating_generation(execute,api,name):
    tool={'id':'media-one','name':name,'input':{'prompt':'fixture'}}
    result,requests,calls,events,_=execute(api,[response(api,tool=tool),response(api,'Fixture artifact ready')],names=[name],tool_result='STATUS: COMPLETE\n- /fixture/generated.png\n')
    assert result['status']=='ok' and result['output_text']=='Fixture artifact ready'
    assert len(calls)==1 and len(requests)==2 and 'tools' not in requests[-1]
    assert result['usage']['prompt']==4 and result['usage']['completion']==6
    assert events[-1]['reason']=='terminal_fal_result' and events[-1]['saved_files']==['/fixture/generated.png']


@pytest.mark.parametrize('api',['anthropic','openai','openai-responses'])
def test_successful_forced_write_postamble_uses_the_same_transport_and_counts_usage(execute,api):
    forced={'name':'Write','input':{'file_path':'out.txt','content':'fixture'},'tool_use_id':'fixture-write','source':'structural_file_artifact'}
    result,requests,calls,_,_=execute(api,[response(api,'Fixture write result ready')],names=['Write'],metadata={'forced_tool_call':forced,'approved_session_tools':['Write']},tool_result='File written successfully: out.txt')
    assert result['status']=='ok' and result['output_text']=='Fixture write result ready'
    assert result['usage']['prompt']==2 and result['usage']['completion']==3
    assert len(requests)==len(calls)==1 and 'tools' not in requests[0]
