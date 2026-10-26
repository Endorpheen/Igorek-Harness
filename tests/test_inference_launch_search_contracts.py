"""API launch configuration and corrected search plans at real decision boundaries."""
import asyncio
import json
import shlex
import sys
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from inference_api_support import BASH, body, state, inference_app as inference_app, inference_server as inference_server
from test_tool_turn_recovery_contracts import exchange_payload


@pytest.mark.parametrize('backend,model,expected', [
    ('transformers','./fixture-model','transformers'), ('gguf','./fixture.gguf','gguf'),
    ('auto','./fixture.gguf','gguf'), ('openrouter','fixture/model','openrouter'),
])
def test_cli_entrypoint_passes_actual_backend_options_without_starting_a_server(inference_app, monkeypatch, backend, model, expected):
    env = inference_app
    serve = Mock()
    monkeypatch.setattr(env.api.uvicorn,'run',serve)
    monkeypatch.setattr(sys,'argv',['fixture-api','--model',model,'--backend',backend,'--host','localhost','--port','18999','--llama-server-bin','/fixture/bin/llama-server','--llama-server-host','127.0.0.1','--llama-server-port','18998','--llama-ctx-size','65536','--llama-gpu-layers','24','--llama-server-extra-args','-fa on --alias "fixture model"','--llama-server-command','/fixture/bin/llama-server --host 127.0.0.1','--no-llama-autodetect-mmproj','--debug-trace'])
    env.api.main()
    serve.assert_called_once_with(env.api.app,host='localhost',port=18999,log_level='info')
    assert env.api.MODEL_BACKEND == expected
    assert env.api.MODEL_NAME == (model if backend == 'openrouter' else model.removeprefix('./'))
    assert env.api.LLAMA_SERVER_EXTRA_ARGS == ['-fa','on','--alias','fixture model']
    assert env.api.LLAMA_SERVER_COMMAND == ['/fixture/bin/llama-server','--host','127.0.0.1']
    assert env.api.LLAMA_CTX_SIZE == 65536 and env.api.LLAMA_GPU_LAYERS == 24
    assert env.api.LLAMA_SERVER_PORT == 18998 and not env.api.LLAMA_AUTODETECT_MMPROJ and env.api.DEBUG_TRACE
    assert env.generator.calls == []


@pytest.mark.parametrize('host',['127.0.0.1','localhost','::1'])
def test_local_api_defaults_do_not_require_an_auth_key(inference_app, monkeypatch, host):
    from factory.api_server_core.request_auth import KEY_ENV
    api = inference_app.api
    monkeypatch.delenv(KEY_ENV,raising=False)
    serve = Mock()
    monkeypatch.setattr(api.uvicorn,'run',serve)
    monkeypatch.setattr(sys,'argv',['fixture-api','--model','./fixture-model','--host',host])
    api.main()
    assert serve.call_args.kwargs['host'] == host
    assert api.LLAMA_SERVER_EXTRA_ARGS == [] and api.LLAMA_SERVER_COMMAND == []
    assert api.LLAMA_AUTODETECT_MMPROJ and api.LLAMA_CTX_SIZE == 32768


@pytest.mark.parametrize('key',['', '   ', None])
def test_nonlocal_listener_refuses_start_without_a_key(inference_app, monkeypatch, key, capsys):
    from factory.api_server_core.request_auth import KEY_ENV
    api = inference_app.api
    if key is None:
        monkeypatch.delenv(KEY_ENV,raising=False)
    else:
        monkeypatch.setenv(KEY_ENV,key)
    serve = Mock()
    monkeypatch.setattr(api.uvicorn,'run',serve)
    monkeypatch.setattr(sys,'argv',['fixture-api','--model','fixture','--host','0.0.0.0'])
    with pytest.raises(SystemExit) as exc:
        api.main()
    assert exc.value.code == 2
    assert KEY_ENV in capsys.readouterr().err and not serve.called


def test_nonlocal_listener_accepts_explicit_auth_without_echoing_key(inference_app, monkeypatch, capsys):
    from factory.api_server_core.request_auth import KEY_ENV
    api = inference_app.api
    monkeypatch.setenv(KEY_ENV,'synthetic-fixture-key')
    serve = Mock()
    monkeypatch.setattr(api.uvicorn,'run',serve)
    monkeypatch.setattr(sys,'argv',['fixture-api','--model','fixture','--host','0.0.0.0'])
    api.main()
    assert serve.call_args.kwargs['host'] == '0.0.0.0'
    assert 'synthetic-fixture-key' not in capsys.readouterr().out


@pytest.mark.parametrize('query',["author's notes",'say "hello"','Привет 🐙', 'x; $(echo NEVER_RUN)'])
def test_failed_search_is_reported_without_reexecuting_command(inference_server, query):
    env = inference_server
    command = 'curl https://api.tavily.com/search -H "Authorization: Bearer fixture" -d ' + shlex.quote(json.dumps({'query':query,'number':3}))
    error = json.dumps({'detail':[{'loc':['body','number'],'msg':'unknown field'}]})
    current = state(env,exchange_payload(env,'Bash',{'command':command},error,error=True,tools=[BASH]))
    current.current_workflow,current.workflow_step='failure_recovery','retry_once'
    decision = asyncio.run(env.api._resolve_tool_turn(None,current))
    assert decision is None
    response = asyncio.run(env.api._handle_text_turn(None,current))
    assert 'unknown field' in ''.join(block.get('text','') for block in response['content'])
    assert env.generator.calls == []


@pytest.mark.parametrize('stream',[False,True])
def test_corrected_search_plan_is_validated_and_serialized_to_protocol(inference_server, stream):
    env = inference_server
    payload = body(env,'Search fixture topic',[BASH],stream=stream)
    current = state(env,payload)
    command = 'curl https://api.tavily.com/search -H "Authorization: Bearer fixture" -d ' + shlex.quote(json.dumps({'query':"author's notes",'max_results':3}))
    decision=env.api.ToolTurnDecision(tool_use={'id':'search-fixture','name':'Bash','input':{'command':command}},source='fixture',preamble='Search fixture')
    response=asyncio.run(env.api._handle_tool_turn(None,current,decision))
    if stream:
        async def collect():
            chunks=[]
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode() if isinstance(chunk,bytes) else chunk)
            return ''.join(chunks)
        wire=asyncio.run(collect())
        events=[json.loads(x[6:]) for x in wire.splitlines() if x.startswith('data: ')]
        args=json.loads(next(e['delta']['partial_json'] for e in events if e.get('delta',{}).get('type')=='input_json_delta'))
        assert events[-1]['type']=='message_stop'
    else:
        args=next(x['input'] for x in response['content'] if x['type']=='tool_use')
        assert response['stop_reason']=='tool_use'
    assert args=={'command':command} and env.generator.calls==[]


@pytest.mark.parametrize('arguments',[None, 'not an object', {'command':8}, {}])
def test_invalid_bash_inputs_are_rejected_before_protocol_serialization(inference_server, arguments):
    env=inference_server
    current=state(env,body(env,'Search fixture topic',[BASH]))
    decision=env.api.ToolTurnDecision(tool_use={'id':'invalid-fixture','name':'Bash','input':arguments},source='fixture',preamble='Fixture')
    with pytest.raises(HTTPException) as exc:
        asyncio.run(env.api._handle_tool_turn(None,current,decision))
    assert exc.value.status_code==502 and env.generator.calls==[]
