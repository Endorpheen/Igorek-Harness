"""Worker retries after real parsed tool output, using synthetic executors only."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import worker as w
from test_worker_execution_contracts import task
from test_worker_execution_contracts import worker_factory as worker_factory


def stream(*events):
    return '\n'.join(json.dumps(event) for event in events)


def tools(*,final=None,prompt=10,completion=3,context=128000):
    events=[{'type':'agent_message','message':'Checking first.'},
        {'type':'response_item','payload':{'type':'function_call','name':'exec_command','arguments':json.dumps({'cmd':'cat fixture.txt'}),'call_id':'fixture-call'}},
        {'type':'response_item','payload':{'type':'function_call_output','call_id':'fixture-call','output':'Process exited with code 0\nOutput:\nverified fixture evidence'}}]
    if final is not None:events.append({'type':'agent_message','message':final})
    events.append({'type':'turn.completed','usage':{'input_tokens':prompt,'output_tokens':completion,'model_context_window':context}})
    return stream(*events)


def completed(text,*,returncode=0,stderr=''):
    return (SimpleNamespace(returncode=returncode,stdout=text,stderr=stderr),'fixture-security')


@pytest.fixture
def runner(worker_factory,tmp_path,monkeypatch):
    worker=worker_factory('codex')
    latest={'rollout':tools()}
    monkeypatch.setattr(w,'_recent_codex_rollout_text',lambda **kwargs:latest['rollout'])
    def run(retry,*,limit=5,initial=None):
        worker_factory.security.execute_command.side_effect=[completed(tools() if initial is None else initial),retry]
        return worker.execute_task(task(metadata={'cwd':str(tmp_path)},limits_timeout_s=limit))
    return worker,worker_factory.security,latest,run


def test_synthesis_combines_usage_and_returns_only_a_post_tool_answer(runner):
    worker,security,latest,run=runner
    reply=stream({'type':'agent_message','message':'Final fixture answer.'},
        {'type':'turn.completed','usage':{'input_tokens':5,'output_tokens':2,'model_context_window':256000}})
    result=run(completed(reply))
    assert result['status']=='ok' and result['output']['text']=='Final fixture answer.'
    assert result['codex_synthesis_retry'] is True
    assert result['usage']['prompt']==15 and result['usage']['completion']==5
    assert result['usage_ext']['model_context_window']==256000
    assert security.execute_command.call_count==2
    assert 'verified fixture evidence' in security.execute_command.call_args.kwargs['input_payload']


def test_synthesis_with_its_own_tools_requires_the_later_answer(runner):
    worker,security,latest,run=runner
    result=run(completed(tools(final='Final after another tool.',prompt=5,completion=2)))
    assert result['status']=='ok' and result['output']['text']=='Final after another tool.'
    assert result['usage']['prompt']==15


@pytest.mark.parametrize('retry',[
    completed(''),completed(tools()),
    completed('',returncode=7,stderr='fixture provider failed'),
    completed('',returncode=7),
    RuntimeError('fixture unavailable'),subprocess.TimeoutExpired('fixture',5),
])
def test_missing_post_tool_answer_never_becomes_false_success(runner,retry):
    worker,security,latest,run=runner
    result=run(retry)
    assert result['status']=='fail'
    assert 'did not produce a final assistant response' in result['error']
    assert 'Checking first.' not in result['output']['text']
    assert 'codex_synthesis_retry' not in result
    assert security.execute_command.call_count==2


@pytest.mark.parametrize('limit,expected',[(0,180),(5,5),(180,180),(600,180)])
def test_synthesis_timeout_is_bounded_by_task_and_retry_limit(runner,limit,expected):
    worker,security,latest,run=runner
    result=run(completed(stream({'type':'agent_message','message':'Final.'})),limit=limit)
    assert result['status']=='ok'
    assert security.execute_command.call_args.kwargs['timeout_s']==expected


def test_missing_rollout_context_fails_without_an_empty_retry(runner):
    worker,security,latest,run=runner
    latest['rollout']=''
    result=run(AssertionError('must not retry without evidence'))
    assert result['status']=='fail' and security.execute_command.call_count==1


def test_existing_post_tool_answer_needs_no_synthesis(runner):
    worker,security,latest,run=runner
    latest['rollout']=tools(final='Already final.')
    result=run(AssertionError('unnecessary second call'),initial=tools(final='Already final.'))
    assert result['status']=='ok' and result['output']['text']=='Already final.'
    assert security.execute_command.call_count==1
    assert 'codex_synthesis_retry' not in result


def test_failed_initial_command_stays_failed_despite_a_later_answer(worker_factory,tmp_path,monkeypatch):
    worker=worker_factory('codex');output=tools(final='Partial fixture answer.')
    monkeypatch.setattr(w,'_recent_codex_rollout_text',lambda **kwargs:output)
    worker_factory.security.execute_command.return_value=completed(output,returncode=3)[0],'fixture-security'
    result=worker.execute_task(task(metadata={'cwd':str(tmp_path)}))
    assert result['status']=='fail' and result['output']['text']=='Partial fixture answer.'
    assert worker_factory.security.execute_command.call_count==1


def test_rollout_monitor_is_stopped_even_when_command_raises(worker_factory,tmp_path,monkeypatch):
    worker=worker_factory('codex');stop=Mock();thread=Mock()
    monkeypatch.setattr(w,'_start_codex_rollout_trace_monitor',lambda **kwargs:(stop,thread))
    worker_factory.security.execute_command.side_effect=RuntimeError('fixture startup failed')
    result=worker.execute_task(task(metadata={'cwd':str(tmp_path)}))
    assert result['status']=='fail' and result['error']=='fixture startup failed'
    stop.set.assert_called();thread.join.assert_called_with(timeout=2)
