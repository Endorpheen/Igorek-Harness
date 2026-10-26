"""Generic argument/approval errors and existing scope fallback, without tool execution."""
import json
import sys
from unittest.mock import Mock

import pytest

from factory.core import cc_backend_tools as c,bash_scope_guard,mcp_bridge


@pytest.mark.parametrize('status',[[],{},None,True,1,'unknown'],ids=['array','object','null','bool','number','unknown'])
def test_bad_plan_status_is_an_argument_error(status):
    with pytest.raises(ValueError,match='status is invalid'):
        c._handle_update_plan({'plan':[{'step':'fixture','status':status}]})


@pytest.mark.parametrize('args',[
    {'plan':{}},{'plan':[None]},{'plan':[{'step':3,'status':'pending'}]},
    {'plan':[{'step':'fixture','status':'pending','extra':1}]},
    {'plan':[],'explanation':4},
    {'plan':[{'step':'one','status':'in_progress'},{'step':'two','status':'in_progress'}]},
],ids=['not-array','bad-item','bad-step','extra-field','bad-explanation','two-active'])
def test_invalid_plan_never_returns_success(args):
    with pytest.raises(ValueError):c._handle_update_plan(args)


def test_valid_plan_with_one_active_step_remains_accepted():
    plan=[{'step':'done','status':'completed'},{'step':'active','status':'in_progress'},{'step':'next','status':'pending'}]
    assert c._handle_update_plan({'plan':plan,'explanation':'fixture'})=='Plan updated'


@pytest.mark.parametrize('value',[[],None,True,1,'fixture'],ids=['array','null','bool','number','string'])
def test_nonobject_approval_response_is_explicitly_rejected(monkeypatch,value):
    raw=json.dumps(value);monkeypatch.setattr(c,'_handle_request_user_input',lambda *args,**kwargs:raw)
    result=c._ask_enable_mcp_approval(server_name='fixture',profile='fixture',reason='fixture',description='fixture',capabilities=[],task_id='owned-task',timeout_s=1)
    assert result=={'approved':False,'decision':'invalid_response','raw':raw}


@pytest.mark.parametrize('raw,decision',[('{','invalid_response'),('{"cancelled":true}','cancelled'),('{"answers":{}}','reject'),('{"answers":{"mcp_enable_decision":{"selected_options":["Включить"]}}}','accept')])
def test_approval_outcome_comes_from_structured_answer_only(monkeypatch,raw,decision):
    handler=Mock(return_value=raw);monkeypatch.setattr(c,'_handle_request_user_input',handler)
    result=c._ask_enable_mcp_approval(server_name='fixture',profile='fixture',reason='fixture reason',description='fixture description',capabilities=['read'],task_id='owned-task',timeout_s=1)
    assert result['decision']==decision and result['approved'] is (decision=='accept')
    context=handler.call_args.args[0]['context']
    assert context['server_name']=='fixture' and context['capabilities']==['read']


@pytest.mark.parametrize('outcome,expected',[('allow',None),('deny','fixture scope denied'),('warn',None)])
def test_scope_verdict_is_preserved_without_running_command(monkeypatch,capsys,tmp_path,outcome,expected):
    enforce=Mock(return_value=(outcome,'fixture scope denied'))
    monkeypatch.setattr(bash_scope_guard,'enforce_bash_scope',enforce)
    assert c._bash_scope_denial('never execute',workdir=str(tmp_path),filesystem_mode='workspace')==expected
    enforce.assert_called_once()
    if outcome=='warn':assert 'fixture scope denied' in capsys.readouterr().err


@pytest.mark.parametrize('stage',['enforce','import','log-unwritable'])
def test_existing_fail_open_policy_has_explicit_diagnostic_and_never_runs_command(monkeypatch,capsys,tmp_path,stage):
    home=tmp_path/'home'
    if stage=='log-unwritable':home.write_text('owned non-directory')
    else:home.mkdir()
    monkeypatch.setenv('HOME',str(home))
    if stage=='import':monkeypatch.setitem(sys.modules,'factory.core.bash_scope_guard',None)
    else:monkeypatch.setattr(bash_scope_guard,'enforce_bash_scope',Mock(side_effect=RuntimeError('fixture policy unavailable')))
    assert c._bash_scope_denial('never execute',workdir=str(tmp_path),filesystem_mode='workspace') is None
    assert 'fail-open' in capsys.readouterr().err
    if stage!='log-unwritable':
        rows=[json.loads(row) for row in (home/'.cache/igorek-bash-scope/denials.jsonl').read_text().splitlines()]
        assert len(rows)==1 and rows[0]['verdict']=='fail_open' and rows[0]['stage']==stage


def test_hot_reload_wrapper_preserves_typed_failure_without_launching(monkeypatch):
    reload=Mock(side_effect=RuntimeError('fixture reload failed'));monkeypatch.setattr(mcp_bridge,'reload_mcp_server_from_registry',reload)
    result=c._hot_reload_mcp_server('fixture',None)
    assert result=={'status':'error','server_name':'fixture','error':'RuntimeError: fixture reload failed','tools':[]}
    reload.assert_called_once_with('fixture',{})
