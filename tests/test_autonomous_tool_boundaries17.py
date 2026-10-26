"""Autonomous tasks bypass prompts while retaining their tool restrictions."""
import pytest

from test_executor_workflow_guards import execute as execute, response


def write_call():
    return {'id':'fixture-write','name':'Write','input':{'file_path':'out.txt','content':'fixture'}}


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_autonomous_task_executes_authorized_write_without_prompt(execute,api):
    result,_,calls,events,_ = execute(api,[response(api,tool=write_call()),response(api,'Done')],names=['Write'],metadata={'auto_approve':True,'restrict_tools_to':['Read','Write']})
    assert result['status']=='ok'
    assert calls==[('Write',{'file_path':'out.txt','content':'fixture'})]
    assert not any(e['kind']=='tool.approval_required' for e in events)


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_mobile_setting_disabling_prompts_allows_write(execute,api):
    result,_,calls,events,_ = execute(api,[response(api,tool=write_call()),response(api,'Done')],names=['Write'],metadata={'source':'mobile_bridge','platform':'android'},source_platform='android',approvals_enabled_provider=lambda:False)
    assert result['status']=='ok'
    assert calls==[('Write',{'file_path':'out.txt','content':'fixture'})]
    assert not any(e['kind']=='tool.approval_required' for e in events)


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_mobile_enabled_confirmations_hold_write_before_tool_dispatch(execute,api):
    result,_,calls,events,_ = execute(api,[response(api,tool=write_call())],names=['Write'],metadata={'source':'mobile_bridge','platform':'android'},source_platform='android',approvals_enabled_provider=lambda:True)
    assert result['status']=='waiting_approval'
    assert calls==[]
    assert any(e['kind']=='tool.approval_required' for e in events)
    assert result['approval_blocked_tools'][0]['tool']=='Write'


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_autonomous_metadata_does_not_override_tool_allowlist(execute,api):
    result,_,calls,events,_ = execute(api,[response(api,tool=write_call()),response(api,'Denied as instructed')],names=['Read','Write'],metadata={'auto_approve':True,'restrict_tools_to':['Read']})
    assert calls==[]
    assert not any(e['kind']=='tool.approval_required' for e in events)
    assert result['status']=='ok'


@pytest.mark.parametrize('api',['anthropic','openai'])
def test_terminal_task_without_autonomy_still_requires_approval(execute,api):
    result,_,calls,events,_ = execute(api,[response(api,tool=write_call())],names=['Write'],metadata={'source':'cli_chat_rich'},source_platform='cli')
    assert result['status']=='fail'
    assert calls==[]
    assert any(e['kind']=='tool.approval_required' for e in events)
