"""Worker-to-executor tool policy and history contracts without provider calls."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import worker as w,cc_backend_executor as executor
from test_worker_execution_contracts import task
from test_worker_execution_contracts import worker_factory as worker_factory


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_goal_continuation_stops_if_backend_lacks_goal_tools(worker_factory, backend):
    worker = worker_factory(backend)
    result = worker.execute_task(task(metadata={"goal_continuation": True}))
    assert result["status"] == "fail"
    assert "requires a backend with goal tools" in result["error"]


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_plan_mode_never_runs_on_backends_without_enforced_read_only_tools(worker_factory, backend):
    result = worker_factory(backend).execute_task(task(metadata={"plan_mode": True}))
    assert result["status"] == "fail"
    assert "Plan mode is not supported" in result["error"]


@pytest.fixture
def cc(worker_factory,tmp_path,monkeypatch):
    worker=worker_factory();worker.config.update(provider='openrouter',api_format='openai',enabled_toolsets=['all'])
    monkeypatch.setenv('IGOREK_ISOLATION_MODE','off')
    monkeypatch.setattr(w,'_reload_enabled_mcp_servers_for_cc_backend',lambda *args:[])
    monkeypatch.setattr(w,'list_mcp_capabilities_for_profile',lambda *args,**kwargs:[])
    monkeypatch.setattr(w,'estimate_cost_for_openrouter_model',lambda *args:0)
    schemas=[{'name':name,'input_schema':{'type':'object'}} for name in ['Read','Grep','Glob','Write','delegate_task','request_user_input','update_plan','VaultStore']]
    monkeypatch.setattr(w,'get_tool_schemas',lambda:list(schemas))
    execute=Mock(return_value={'status':'ok','output_text':'fixture answer','usage':{'prompt':5,'completion':2}})
    monkeypatch.setattr(executor,'execute_cc_backend_task',execute)
    def run(*,metadata=None,text='fixture user text',stable=False,skills='fixture skill context',foundation='fixture foundation'):
        worker.config['stable_prompt_prefix']=stable
        result=worker._execute_cc_backend(task(metadata=metadata or {}),str(tmp_path),str(tmp_path/'runtime'),text,skills,foundation_context=foundation)
        return result,execute.call_args.kwargs
    return SimpleNamespace(worker=worker,run=run,execute=execute,root=tmp_path,schemas=schemas)


@pytest.mark.parametrize('stable',[False,True])
@pytest.mark.parametrize('source',['cli_chat_rich','mobile_bridge','scheduler'])
def test_supported_surfaces_and_stable_prefix_control_wire_tool_availability(cc,stable,source):
    result,packet=cc.run(metadata={'source':source},stable=stable)
    names={schema['name'] for schema in packet['enabled_tools']}
    assert result['status']=='ok'
    assert ('request_user_input' in names)==(stable or source!='scheduler')
    assert 'VaultStore' not in names
    assert packet['user_message']=='fixture user text'
    assert 'fixture foundation' in packet['system_prompt']


def test_goal_tools_and_guidance_are_only_attached_to_current_goal_turn(cc):
    from factory.core import thread_goals

    goal = thread_goals.create_goal('goal-session', 'Finish the work')
    metadata = {'source': 'mobile_bridge', 'session': 'goal-session',
                'goal_id': goal['goal_id'], 'goal_revision': goal['revision']}
    _, packet = cc.run(metadata=metadata)
    assert {'get_goal', 'update_goal'} <= {item['name'] for item in packet['enabled_tools']}
    assert 'user explicitly set an ongoing goal' in packet['system_prompt']
    thread_goals.update_goal('goal-session', 'paused', by_user=True)
    _, packet = cc.run(metadata=metadata)
    assert {'get_goal', 'update_goal'}.isdisjoint({item['name'] for item in packet['enabled_tools']})
    assert 'user explicitly set an ongoing goal' not in packet['system_prompt']


@pytest.mark.parametrize('stable',[False,True])
@pytest.mark.parametrize('name',['fixture-skill','igorek-self-inspection'])
def test_explicit_skill_restrictions_reach_execution_policy(cc,monkeypatch,stable,name):
    skill=SimpleNamespace(name=name,must_write=True)
    manager=SimpleNamespace(is_loaded=True,find_explicitly_mentioned_skills=Mock(return_value=[skill]))
    monkeypatch.setattr(w,'SKILLS_AVAILABLE',True);monkeypatch.setattr(w,'skills_manager',manager,raising=False)
    result,packet=cc.run(metadata={'source':'mobile_bridge'},stable=stable)
    names={schema['name'] for schema in packet['enabled_tools']}
    if stable:
        assert 'delegate_task' in names
        assert executor._tool_denied_by_policy('delegate_task',packet['metadata'])
    else:assert 'delegate_task' not in names
    if name=='igorek-self-inspection':
        if stable:
            assert executor._tool_denied_by_policy('Write',packet['metadata'])
            assert not executor._tool_denied_by_policy('Read',packet['metadata'])
        else:assert names=={'Read','Grep','Glob'}
    assert packet['metadata']['must_write_skills']==[name]


@pytest.mark.parametrize('stable',[False,True])
def test_plan_mode_overrides_write_skill_requirement_at_executor_boundary(cc,monkeypatch,stable):
    manager=SimpleNamespace(is_loaded=True,find_explicitly_mentioned_skills=lambda *args,**kwargs:[SimpleNamespace(name='fixture-skill',must_write=True)])
    monkeypatch.setattr(w,'SKILLS_AVAILABLE',True);monkeypatch.setattr(w,'skills_manager',manager,raising=False)
    result,packet=cc.run(metadata={'plan_mode':True},stable=stable)
    assert 'must_write_skills' not in packet['metadata']
    for name in ['Write','Bash','delegate_task']:
        assert executor._tool_denied_by_policy(name,packet['metadata'])
    for name in ['Read','Grep','Glob','update_plan']:
        assert not executor._tool_denied_by_policy(name,packet['metadata'])


@pytest.mark.parametrize('text,expected',[
    ('Context\nПоследнее сообщение пользователя:\nactual request','actual request'),
    ('Задача пользователя:\nactual task','actual task'),
    ('Ты продолжаешь локальный CLI-диалог\n\nold context\n\nlatest paragraph','latest paragraph'),
    ('Последнее сообщение пользователя:\n','Последнее сообщение пользователя:\n'),
    ('Задача пользователя:\n','Задача пользователя:\n'),
    ('ordinary fixture message','ordinary fixture message'),
])
def test_factory_wrapper_is_removed_from_user_message_only(cc,text,expected):
    result,packet=cc.run(text=text)
    assert packet['user_message']==expected


def test_structured_history_keeps_raw_user_message_and_history_separate(cc):
    history=[{'role':'user','content':'older fixture'}]
    result,packet=cc.run(metadata={'structured_session_history':True,'session_user_message':'raw current request','history_messages':history},text='wrapped transport message',stable=False)
    assert packet['user_message']=='raw current request' and packet['history_messages']==history
    assert 'wrapped transport message' not in packet['system_prompt']


def test_project_instructions_are_loaded_from_the_actual_temporary_project(cc):
    (cc.root/'AGENTS.md').write_text('Follow fixture-specific coding conventions.')
    result,packet=cc.run()
    assert 'Follow fixture-specific coding conventions.' in packet['system_prompt']


@pytest.mark.parametrize('invalid',[None,[],17,'not-config'])
def test_invalid_optional_checkpoint_shape_is_not_forwarded_to_executor(cc,invalid):
    cc.worker.config['checkpoints']=invalid
    result,packet=cc.run()
    assert packet['checkpoint_config'] is None


def test_responses_transport_uses_selected_model_and_refreshable_headers(cc):
    cc.worker.config['api_format']='openai-responses'
    headers=Mock(side_effect=[{'Authorization':'Bearer synthetic-one'},{'Authorization':'Bearer synthetic-two'}])
    cc.worker.auth_headers_provider=headers
    result,packet=cc.run(metadata={'model_override':'chosen-fixture-model'})
    assert packet['model']=='chosen-fixture-model' and packet['request_headers']=={'Authorization':'Bearer synthetic-one'}
    assert packet['request_headers_provider']()=={'Authorization':'Bearer synthetic-two'}


def test_output_usage_and_diagnostics_are_preserved_at_worker_boundary(cc):
    cc.execute.return_value={'status':'fail','output_text':'fixture failure','failure_reason':'tool_error','failure_details':'specific fixture reason',
        'usage':{'prompt':5,'completion':2,'model_context_window':128000,'current_usage':{'input_tokens':5}},'diagnostic':{'code':'TOOL.EXECUTION_FAILED'}}
    result,packet=cc.run()
    assert result['status']=='fail' and result['error']=='specific fixture reason'
    assert result['diagnostic']=={'code':'TOOL.EXECUTION_FAILED'}
    assert result['usage_ext']['model_context_window']==128000 and result['usage_ext']['input_tokens']==5
