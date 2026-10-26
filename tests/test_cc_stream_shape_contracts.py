"""Malformed provider SSE shapes must never escape or yield partial success."""
import json

import pytest

from factory.core import cc_backend_executor as executor
from test_cc_stream_wire_contracts import wire as wire, event, chunk


@pytest.mark.parametrize('boundary',[False,True])
@pytest.mark.parametrize('malformed',[
    [], {'choices':'bad'}, {'choices':[{'finish_reason':['stop'],'delta':{}}]}, {'choices':['bad']}, {'choices':[None]}, {'choices':[{'delta':'bad'}]},
    {'choices':[{'delta':{'content':['bad']}}]},
    {'choices':[{'delta':{'reasoning_content':{'bad':1}}}]},
    {'choices':[{'delta':{'tool_calls':{}}}]},
    {'choices':[{'delta':{'tool_calls':[None]}}]},
    {'choices':[{'delta':{'tool_calls':[{'index':True,'function':{}}]}}]},
    {'choices':[{'delta':{'tool_calls':[{'index':-1,'function':{}}]}}]},
    {'choices':[{'delta':{'tool_calls':[{'index':0,'id':{},'function':{}}]}}]},
    {'choices':[{'delta':{'tool_calls':[{'index':0,'function':[]} ]}}]},
    {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'name':[]}}]}}]},
    {'choices':[{'delta':{'tool_calls':[{'index':'bad','function':{}}]}}]},
    {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':{}}}]}}]},
])
def test_invalid_event_shape_has_one_protocol_error_at_boundary_or_eof(wire,boundary,malformed):
    wire.chunks=[event(chunk('partial fixture answer')),event(malformed,boundary=boundary)]
    result,_=executor._http_post_streaming_openai(wire.url,{'model':'fixture','messages':[]},{},2)
    assert result.get('error_code')=='PROTOCOL.INVALID_RESPONSE'
    assert result['diagnostic']['phase']=='sse_event' and 'choices' not in result


@pytest.mark.parametrize('delta',[{},None])
def test_nullable_terminal_delta_and_usage_only_frame_remain_compatible(wire,delta):
    wire.chunks=[event(chunk('fixture answer')),event({'choices':[{'delta':delta,'finish_reason':'stop'}]}),event({'choices':[],'usage':{'prompt_tokens':3,'completion_tokens':4}}),b'data: [DONE]\n\n']
    result,_=executor._http_post_streaming_openai(wire.url,{'model':'fixture','messages':[]},{},2)
    assert result['choices'][0]['message']['content']=='fixture answer'
    assert result['usage']=={'prompt_tokens':3,'completion_tokens':4}


def test_nullable_optional_tool_fields_can_be_completed_by_later_valid_fragments(wire):
    wire.chunks=[event({'choices':[{'delta':{'tool_calls':[{'index':0,'id':None,'function':None}]}}]}),event({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'fixture-id','function':{'name':'Read','arguments':'{"file_path":"fixture.py"}'}}]},'finish_reason':'tool_calls'}]}),b'data: [DONE]\n\n']
    result,_=executor._http_post_streaming_openai(wire.url,{'model':'fixture','messages':[]},{},2)
    call=result['choices'][0]['message']['tool_calls'][0]
    assert call['id']=='fixture-id' and call['function']['name']=='Read'
    assert json.loads(call['function']['arguments'])=={'file_path':'fixture.py'}
