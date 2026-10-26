"""ACP editor requests with actual session state and a synthetic execution boundary."""
import asyncio
from collections import defaultdict, deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import acp
import pytest

from factory.core.acp_bridge import server, events
from factory.core.acp_bridge.session import SessionManager
from factory.core import cc_backend_executor


@pytest.fixture
def agent(tmp_path,monkeypatch):
    manager=SessionManager();state=manager.create_session(str(tmp_path))
    value=server.FactoryACPAgent(manager)
    connection=SimpleNamespace(session_update=AsyncMock())
    value.on_connect(connection)
    execute=Mock(return_value={'status':'ok','output_text':'Fixture editor answer','usage':{'prompt':3,'completion':4}})
    monkeypatch.setattr(cc_backend_executor,'execute_cc_backend_task',execute)
    return value,state,connection,execute


def test_normal_editor_prompt_reaches_executor_and_delivers_an_answer(agent):
    value,state,connection,execute=agent
    response=asyncio.run(value.prompt([acp.text_block('Fixture question')],state.session_id))
    assert response.stop_reason=='end_turn'
    execute.assert_called_once()
    arguments=execute.call_args.kwargs
    assert arguments['user_message']=='Fixture question' and arguments['workdir']==state.cwd
    assert arguments['source_platform']=='acp' and arguments['filesystem_mode']=='workspace'
    assert 'Fixture editor answer' in str(connection.session_update.call_args)


def test_unknown_slash_command_remains_a_regular_model_prompt(agent):
    value,state,_,execute=agent
    asyncio.run(value.prompt([acp.text_block('/unknown fixture')],state.session_id))
    execute.assert_called_once()
    assert execute.call_args.kwargs['user_message']=='/unknown fixture'


@pytest.mark.parametrize('command',['/help','/reset','/version','/tools'])
def test_builtin_commands_reply_locally_without_launching_an_agent(agent,command):
    value,state,connection,execute=agent
    state.history=[{'role':'user','content':'fixture history'}]
    response=asyncio.run(value.prompt([acp.text_block(command)],state.session_id))
    assert response.stop_reason=='end_turn' and not execute.called
    rendered=str(connection.session_update.call_args)
    assert 'Error executing' not in rendered
    if command=='/reset':assert state.history==[]
    if command=='/tools':assert 'Available tools' in rendered


def test_executor_error_is_delivered_as_error_text_instead_of_silent_end_turn(agent):
    value,state,connection,execute=agent
    execute.side_effect=RuntimeError('fixture execution unavailable')
    response=asyncio.run(value.prompt([acp.text_block('Fixture question')],state.session_id))
    assert response.stop_reason=='end_turn' and execute.called
    assert 'fixture execution unavailable' in str(connection.session_update.call_args)


def test_missing_and_empty_prompts_never_start_execution(agent):
    value,state,_,execute=agent
    assert asyncio.run(value.prompt([acp.text_block('text')],'missing')).stop_reason=='refusal'
    assert asyncio.run(value.prompt([acp.text_block('   ')],state.session_id)).stop_reason=='end_turn'
    assert not execute.called


def test_session_fork_is_independent_and_resume_keeps_existing_session(agent):
    value,state,_,_=agent
    state.history=[{'role':'user','content':{'nested':['one']}}]
    async def run():
        fork=await value.fork_session('/fixture/other',state.session_id)
        child=value.session_manager.get_session(fork.session_id)
        child.history[0]['content']['nested'].append('two')
        assert state.history[0]['content']['nested']==['one']
        await value.load_session('/fixture/resumed',state.session_id)
        assert state.cwd=='/fixture/resumed'
        assert await value.load_session('/fixture/missing','missing') is None
        await value.resume_session('/fixture/new','missing')
        await asyncio.sleep(0)
    asyncio.run(run())
    assert len(value.session_manager.list_sessions())==3


def test_agent_cancellation_uses_only_requested_session_state(agent):
    value,state,_,execute=agent
    other=value.session_manager.create_session('/fixture/other')
    async def run():
        await value.cancel(state.session_id)
        await value.cancel('missing')
    asyncio.run(run())
    assert state.cancel_event.is_set() and not other.cancel_event.is_set() and not execute.called


def test_actual_server_runner_can_await_transport_inside_an_existing_loop(monkeypatch):
    run=AsyncMock()
    monkeypatch.setattr(server.acp,'run_agent',run)
    asyncio.run(server._run_server())
    run.assert_awaited_once()


@pytest.mark.parametrize('tool,args,expected',[
    ('Bash',{'command':'x'*100},'bash: '+'x'*77+'...'),
    ('Read',{'file_path':'file.py'},'read: file.py'),
    ('Write',{'file_path':'file.py'},'write: file.py'),
    ('Edit',{'file_path':'file.py'},'edit: file.py'),
    ('delegate_task',{'goal':'g'*80},'delegate: '+'g'*57+'...'),
    ('delegate_task',{},'delegate task'),('Custom',{},'Custom'),
])
def test_tool_titles_are_readable_and_bounded(tool,args,expected):
    assert events._build_tool_title(tool,args)==expected


def test_progress_callback_pairs_repeated_same_tool_calls_fifo_and_bounds_preview():
    async def run():
        connection=SimpleNamespace(session_update=AsyncMock())
        ids=defaultdict(deque)
        callback=events.make_tool_progress_cb(connection,'fixture',asyncio.get_running_loop(),ids)
        def emit():
            callback('tool.started',name='Read',args='{"file_path":"one.py"}')
            callback('tool.started',name='Read',args='not JSON')
            callback('tool.completed',name='Read',result='x'*6000)
            callback('tool.completed',name='Read',result='second')
            callback('tool.completed',name='Read',result='unmatched')
        await asyncio.to_thread(emit)
        assert connection.session_update.await_count==4 and not ids
        updates=[call.args[1] for call in connection.session_update.call_args_list]
        assert updates[0].tool_call_id==updates[2].tool_call_id
        assert updates[1].tool_call_id==updates[3].tool_call_id
        assert updates[2].raw_output=='x'*6000 and 'truncated' in str(updates[2].content)
    asyncio.run(run())


def test_message_and_thinking_callbacks_preserve_channel_and_ignore_empty_text():
    async def run():
        connection=SimpleNamespace(session_update=AsyncMock())
        loop=asyncio.get_running_loop()
        message=events.make_message_cb(connection,'fixture',loop)
        thinking=events.make_thinking_cb(connection,'fixture',loop)
        await asyncio.to_thread(lambda:(message(''),thinking(''),message('answer'),thinking('thought')))
        updates=[call.args[1] for call in connection.session_update.call_args_list]
        assert [x.session_update for x in updates]==['agent_message_chunk','agent_thought_chunk']
    asyncio.run(run())
