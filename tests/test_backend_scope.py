"""Backend identity, rather than a loopback transport, controls local repairs."""
import json

import pytest

from factory.core import cc_backend_executor as executor
from factory.core.llama_image_input import LlamaImageInput
from test_cc_stream_wire_contracts import wire, event, chunk  # noqa: F401
from test_executor_tool_loop_edges import loop  # noqa: F401
from test_executor_workflow_guards import response
from test_llama_image_input import IMAGE, REJECTION, metadata_server, payload  # noqa: F401
from test_local_history_output_guard import ECHO


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
@pytest.mark.parametrize('owner', ['cloud', 'unknown'])
def test_localhost_cloud_proxy_and_unknown_backend_preserve_original_reply(loop, metadata_server, api, owner):
    state=metadata_server
    state['props']={}
    state['models']={'data':[{'id':'fixture','owned_by':owner}]}
    run=loop(api,[response(api,ECHO)],base_url=state['url'])
    assert run.result['status']=='ok' and run.result['output_text']==ECHO
    assert len(run.requests)==1 and run.calls==[]
    assert not any(row.get('reason')=='local_tool_history_echo' for row in run.trace)


@pytest.mark.parametrize('api', ['openai','anthropic'])
def test_confirmed_llama_keeps_bounded_history_recovery(loop, metadata_server, api):
    state=metadata_server
    state['models']={'data':[{'id':'fixture','owned_by':'llamacpp'}]}
    run=loop(api,[response(api,ECHO),response(api,'Current answer')],base_url=state['url'])
    assert run.result['status']=='ok' and run.result['output_text']=='Current answer'
    assert run.calls==[] and len(run.requests)==2
    assert any(row.get('reason')=='local_tool_history_echo' for row in run.trace)


def test_cloud_selected_model_cannot_inherit_other_local_model_capabilities(metadata_server):
    state=metadata_server
    state['models']={'data':[
        {'id':'fixture','owned_by':'cloud','architecture':{'input_modalities':['text','image']}},
        {'id':'other-local-model','owned_by':'llamacpp','architecture':{'input_modalities':['text']}}]}
    original=payload()
    policy=LlamaImageInput(state['url'],'fixture',{})
    assert policy.prepare(original) is original
    assert policy.recover(original,REJECTION) is None


def test_unknown_loopback_backend_cannot_trigger_image_recovery(metadata_server):
    state=metadata_server
    state['props']={};state['models']={}
    policy=LlamaImageInput(state['url'],'fixture',{})
    assert policy.prepare(payload())['messages'][0]['content'][1]==IMAGE
    assert policy.recover(payload(),REJECTION) is None


def test_stream_transport_does_not_guess_backend_from_localhost(wire,monkeypatch):
    records=[]
    monkeypatch.setattr(executor,'append_cc_backend_trace',lambda directory,record:records.append(record))
    wire.chunks=[event(chunk(ECHO,finish='stop')),b'data: [DONE]\n\n']
    body,_=executor._http_post_streaming_openai(wire.url,{'model':'fixture','messages':[]},{},3,trace_dir='fixture')
    assert body['choices'][0]['message']['content']==ECHO
    assert ''.join(row['text'] for row in records if row['kind']=='model.text_delta')==ECHO


@pytest.mark.parametrize('known',[False,True])
def test_bash_web_repair_uses_same_backend_identity(loop,metadata_server,known):
    state=metadata_server
    state['props']={}
    state['models']={'data':[{'id':'fixture','owned_by':'llamacpp' if known else 'cloud'}]}
    tool={'id':'fetch','name':'Bash','input':{'command':'curl -sL https://example.test/article'}}
    bodies=[response('openai',tool=tool),response('openai','Checked')]
    if known:
        bodies.append(response('openai',json.dumps({'status':'complete','requires_tools':True,'evidence_ids':['fetch']})))
    run=loop('openai',bodies,
             names=['Bash','web_extract'],base_url=state['url'],raw='{"success":true}')
    assert run.result['status']=='ok' and len(run.calls)==1
    assert run.calls[0][0]==('web_extract' if known else 'Bash')
    assert 'backend_capabilities' not in json.dumps(run.requests)


@pytest.mark.parametrize('owner,selected,vision,expected', [
    ('llamacpp','fixture',False,'llama_cpp'),
    ('llamacpp','fixture',True,'llama_cpp'),
    ('llamacpp','fixture',None,'llama_cpp'),
    ('cloud','fixture',False,'unknown'),
    ('llamacpp','other',False,'unknown'),
])
def test_identity_and_vision_are_separate_and_bound_to_selected_model(metadata_server,owner,selected,vision,expected):
    state=metadata_server
    state['models']={'data':[{'id':selected,'owned_by':owner,'architecture':{
        'input_modalities':['text','image'] if vision is True else ['text'] if vision is False else None}}]}
    state['props']={}
    policy=LlamaImageInput(state['url'],'fixture',{})
    observed=policy.resolve()
    assert observed.backend==expected
    assert observed.vision is (vision if expected=='llama_cpp' else None)
    assert observed.matches(state['url'],'fixture')
    request_count=len(state['requests'])
    assert policy.resolve()==observed
    policy.prepare(payload())
    assert len(state['requests'])==request_count


def test_alias_identity_is_model_bound_and_cloud_duplicates_are_ambiguous(metadata_server):
    state=metadata_server
    row={'id':'canonical','aliases':['fixture'],'owned_by':'llamacpp',
         'architecture':{'input_modalities':['text']}}
    state['models']={'data':[row]}
    assert LlamaImageInput(state['url'],'fixture',{}).resolve().backend=='llama_cpp'
    state['models']['data'].append({'id':'fixture','owned_by':'cloud'})
    assert LlamaImageInput(state['url'],'fixture',{}).resolve().backend=='unknown'


@pytest.mark.parametrize('alias',[None,'',[], 'other'])
def test_props_fallback_requires_exact_selected_alias(metadata_server,alias):
    state=metadata_server
    state['models']={}
    state['props']['model_alias']=alias
    assert LlamaImageInput(state['url'],'fixture',{}).resolve().backend=='unknown'


@pytest.mark.parametrize('url,model,api,expected',[
    ('http://127.0.0.1:8081/v1/chat/completions','fixture','openai',True),
    ('http://127.0.0.1:8081/v1/messages','fixture','anthropic',True),
    ('http://127.0.0.1:8081/v1','other','openai',False),
    ('http://127.0.0.1:8082/v1','fixture','openai',False),
    ('http://127.0.0.1:8081/other/v1','fixture','openai',False),
    ('https://cloud.example.test/v1','fixture','openai',False),
    ('http://127.0.0.1:8081/v1?provider=cloud','fixture','openai',False),
    ('http://127.0.0.1:8081/v1','fixture','openai-responses',False),
])
def test_resolved_identity_cannot_be_reused_for_another_route_model_or_transport(url,model,api,expected):
    from factory.core.model_backend_capabilities import ModelBackendCapabilities
    from factory.core.local_history_output import local_history_guard_enabled
    capabilities=ModelBackendCapabilities('llama_cpp',False,'http://127.0.0.1:8081/v1','fixture')
    assert local_history_guard_enabled(url,api,capabilities=capabilities,model=model) is expected


@pytest.mark.parametrize('confirmed',[False,True])
def test_same_resolved_identity_controls_live_stream_without_metadata_requests(wire,monkeypatch,confirmed):
    from factory.core.model_backend_capabilities import ModelBackendCapabilities
    wire.url=wire.url.split("?",1)[0]
    records=[]
    monkeypatch.setattr(executor,'append_cc_backend_trace',lambda directory,record:records.append(record))
    wire.chunks=[event(chunk(ECHO[:10])),event(chunk(ECHO[10:],finish='stop')),b'data: [DONE]\n\n']
    capabilities=ModelBackendCapabilities('llama_cpp' if confirmed else 'unknown',None,wire.url,'fixture')
    body,_=executor._http_post_streaming_openai(wire.url,{'model':'fixture','messages':[]},{},3,
        trace_dir='fixture',backend_capabilities=capabilities)
    assert body['choices'][0]['message']['content']==ECHO
    assert ''.join(row['text'] for row in records if row['kind']=='model.text_delta')==('' if confirmed else ECHO)
    assert len(wire.requests)==1


def test_cloud_owner_is_respected_even_when_props_looks_like_llama(metadata_server):
    state=metadata_server
    state['models']={'data':[{'id':'fixture','owned_by':'cloud'}]}
    policy=LlamaImageInput(state['url'],'fixture',{})
    assert policy.resolve().backend=='unknown'
    assert policy.prepare(payload())['messages'][0]['content'][1]==IMAGE
    assert [row[0] for row in state['requests']]==['/v1/models']
