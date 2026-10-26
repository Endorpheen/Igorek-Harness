"""Real CLI parsing with synthetic account, Vault and task boundaries."""
import getpass
from unittest.mock import Mock

import pytest

from test_cli_command_contracts import state, invoke, parsed  # noqa: F401
from factory import cli
from factory.core import wally_auth


@pytest.mark.parametrize('arguments,function,expected_args,expected_kwargs', [
    (['status','--days','3','--json'],'wally_account_info',(),{'days':3}),
    (['models','--json'],'list_wally_models',(),{}),
    (['models','--catalog','--json'],'wally_model_catalog',(),{}),
])
def test_wally_json_routes_are_fake_account_calls(state,monkeypatch,capsys,arguments,function,expected_args,expected_kwargs):
    payload={'fixture':True} if function=='wally_account_info' else ['fixture-model']
    call=Mock(return_value=payload)
    monkeypatch.setattr(wally_auth,function,call)
    actual=parsed(monkeypatch,capsys,'wally',*arguments)
    assert actual==(payload if function=='wally_account_info' else {'models':payload})
    call.assert_called_once_with(*expected_args,**expected_kwargs)


@pytest.mark.parametrize('arguments,function,payload,expected', [
    (['status','--days','4'],'wally_account_summary_lines',['Plan fixture'],'Plan fixture'),
    (['models'],'list_wally_models',['fixture-model'],'fixture-model'),
    (['models','--catalog'],'wally_model_catalog',[{'id':'fixture','display_name':'Fixture','input_per_mtok':1,'output_per_mtok':2}],'Fixture'),
])
def test_wally_terminal_output_is_readable_without_json(state,monkeypatch,capsys,arguments,function,payload,expected):
    monkeypatch.setattr(cli.sys.stdout,'isatty',lambda:True)
    monkeypatch.setattr(wally_auth,'wally_account_info',lambda **k:{'fixture':True})
    monkeypatch.setattr(wally_auth,function,Mock(return_value=payload))
    result,output=invoke(monkeypatch,capsys,'wally',*arguments)
    assert result==0 and expected in output.out and not output.err


def test_wally_denial_returns_nonzero_and_reason(state,monkeypatch,capsys):
    monkeypatch.setattr(wally_auth,'wally_account_info',Mock(side_effect=wally_auth.WallyAuthError('fixture denied')))
    result,output=invoke(monkeypatch,capsys,'wally','status','--json')
    assert result==2 and 'Wally: fixture denied' in output.err


@pytest.mark.parametrize('age,status,exitcode', [('0','ok',0),('7','failed',2)])
def test_free_model_selection_passes_explicit_flags_without_running_network(state,monkeypatch,capsys,age,status,exitcode):
    selector=Mock(return_value={'status':status})
    monkeypatch.setattr(cli,'update_openrouter_profile_model',selector)
    result,output=invoke(monkeypatch,capsys,'openrouter','update-free-model','--profile','fixture','--cached','--dry-run','--allow-no-tools','--min-context','32000','--max-age-days',age,'--exclude-model','one','--exclude-model','two','--limit','9')
    assert result==exitcode and status in output.out
    kwargs=selector.call_args.kwargs
    assert kwargs['profile']=='fixture' and kwargs['refresh'] is False and kwargs['dry_run'] is True
    assert kwargs['require_tools'] is False and kwargs['min_context']==32000
    assert kwargs['max_age_days']==(int(age) or None) and kwargs['exclude_models']=={'one','two'} and kwargs['limit']==9


@pytest.mark.parametrize('outcome', ['ok','denied','exception','empty'])
def test_auth_login_keeps_secret_out_of_output_and_never_uses_real_vault(state,monkeypatch,capsys,outcome):
    monkeypatch.setattr(getpass,'getpass',lambda _:'' if outcome=='empty' else ' fixture-secret ')
    add=Mock(return_value={'status':'ok','credential':{'service':'fixture'}} if outcome=='ok' else {'status':'error','error':'fixture denied'})
    if outcome=='exception':add.side_effect=RuntimeError('fixture unavailable')
    monkeypatch.setattr(cli.vault_cli,'add',add)
    result,output=invoke(monkeypatch,capsys,'auth','login','--provider','fixture')
    assert result==(0 if outcome=='ok' else 2)
    assert 'fixture-secret' not in output.out+output.err
    if outcome=='empty':
        add.assert_not_called()
    else:
        add.assert_called_once_with(service='fixture',alias='fixture',credential_type='api_key',secret='fixture-secret',replace_existing=True)


@pytest.mark.parametrize('arguments,function,args,kwargs', [
    (['rotate','fixture','--secret','fixture-new'],'rotate',('fixture','fixture-new'),{}),
    (['broker','env','fixture','--agent','fixture-agent','--ttl','60'],'broker_env',('fixture','fixture-agent',60),{}),
    (['broker','list','--agent','fixture-agent'],'broker_list',('fixture-agent',),{}),
    (['generate-skill','--agent','fixture-agent'],'generate_skill',('fixture-agent',False),{}),
    (['import','--from-env','fixture.env','--redact-source'],'import_credentials',(),{'from_env':'fixture.env','from_json':None,'redact_source':True,'replace_existing':False}),
    (['add','fixture','--secret','fixture-value'],'add',(),{'service':'fixture','alias':'default','credential_type':'api_key','secret':'fixture-value','replace_existing':False}),
])
def test_vault_routes_preserve_owner_flags_with_fake_operations(state,monkeypatch,capsys,arguments,function,args,kwargs):
    operation=Mock(return_value={'fixture':True})
    monkeypatch.setattr(cli.vault_cli,function,operation)
    assert parsed(monkeypatch,capsys,'vault',*arguments)=={'fixture':True}
    operation.assert_called_once_with(*args,**kwargs)


def test_task_wait_passes_bounded_poll_and_timeout_to_fake_waiter(state,monkeypatch,capsys):
    waiter=Mock(return_value={'task_id':'owned-task','status':'ok'})
    monkeypatch.setattr(cli,'wait_task',waiter)
    assert parsed(monkeypatch,capsys,'task','wait','owned-task','--interval','0.5','--timeout','2')['status']=='ok'
    waiter.assert_called_once_with('owned-task',interval_s=.5,timeout_s=2.)


@pytest.mark.parametrize('command,enabled',[('enable',True),('disable',False)])
def test_skill_enable_flag_is_preserved_by_cli_parser(state,monkeypatch,capsys,command,enabled):
    operation=Mock(return_value={'fixture':True})
    monkeypatch.setattr(cli,'set_skill_enabled',operation)
    assert parsed(monkeypatch,capsys,'skills',command,'fixture-skill')=={'fixture':True}
    operation.assert_called_once_with('fixture-skill',enabled=enabled,data_dir=None,path=None)


@pytest.mark.parametrize('command,operation,kwargs',[('list','list_skills',{'data_dir':None,'path':None}),('activate-local','activate_local_skills',{'data_dir':None}),('usage','skills_usage_summary',{'limit':20,'data_dir':None,'path':None})])
def test_skills_metadata_routes_are_separate_from_execution(state,monkeypatch,capsys,command,operation,kwargs):
    call=Mock(return_value={'fixture':True})
    monkeypatch.setattr(cli,operation,call)
    assert parsed(monkeypatch,capsys,'skills',command)=={'fixture':True}
    call.assert_called_once_with(**kwargs)


def test_skill_initializer_receives_all_owner_options(state,monkeypatch,capsys):
    operation=Mock(return_value={'fixture':True})
    monkeypatch.setattr(cli,'init_skill',operation)
    assert parsed(monkeypatch,capsys,'skills','init','fixture','--description','Fixture skill','--trigger','one','--trigger','two','--backend','codex','--with-scripts','--with-references','--with-assets','--force')=={'fixture':True}
    operation.assert_called_once_with(name='fixture',data_dir=None,path=None,description='Fixture skill',triggers=['one','two'],backends=['codex'],with_scripts=True,with_references=True,with_assets=True,force=True)


@pytest.mark.parametrize('read_only,status,json_output',[(True,'ready',True),(True,'needs_action',True),(True,'ready',False),(False,'ready',True)])
def test_doctor_status_keeps_exit_code_and_output_mode(state,monkeypatch,capsys,read_only,status,json_output):
    report={'status':status}
    checker=Mock(return_value=report)
    monkeypatch.setattr(cli,'check_host_readiness' if read_only else 'doctor_cli_environment',checker)
    monkeypatch.setattr(cli,'format_readiness',lambda _: 'fixture readiness')
    options=['doctor']+(['--read-only'] if read_only else [])+(['--json'] if json_output else [])
    result,output=invoke(monkeypatch,capsys,*options)
    assert result==(1 if read_only and status=='needs_action' else 0)
    assert (status in output.out) if json_output else ('fixture readiness' in output.out)
    checker.assert_called_once_with(data_dir=None)
