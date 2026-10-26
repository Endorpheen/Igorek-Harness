"""Worker result persistence and transport callbacks with synthetic executors."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_worker_execution_contracts import worker_factory,task  # noqa: F401
from test_worker_synthesis_contracts import tools,completed
from factory.core import worker as w,workspace_manager,cc_backend_executor as e
from factory.core.schema import open_db


@pytest.mark.parametrize('output,expected',[(None,'fail'),({'text':'fixture answer'},'ok')])
def test_unknown_result_status_is_derived_from_available_output(worker_factory,output,expected):
    worker=worker_factory();identifier='owned-result'
    with open_db(w.DB_PATH) as conn:
        conn.execute('INSERT INTO tasks(task_id,status,profile,metadata) VALUES(?,?,?,?)',(identifier,'processing','cc-backend','{}'));conn.commit()
        worker.update_task(conn,identifier,{'status':'unknown-fixture','output':output})
        assert worker.get_task(conn,identifier)['status']==expected


def test_malformed_metadata_is_replaced_with_safe_result_extension(worker_factory):
    worker=worker_factory();identifier='owned-result'
    with open_db(w.DB_PATH) as conn:
        conn.execute('INSERT INTO tasks(task_id,status,profile,metadata) VALUES(?,?,?,?)',(identifier,'processing','cc-backend','{'));conn.commit()
        events=[{'name':'fixture-tool','event_type':'function_call'}]
        worker.update_task(conn,identifier,{'status':'ok','output':{'text':'fixture'},'usage_ext':{'total_tokens':3},'codex_tool_events':events})
        saved=worker.get_task(conn,identifier)
        assert saved['metadata']=={'usage_ext':{'total_tokens':3},'codex_tool_events':events}


def test_mcp_journal_is_bounded_redacted_and_does_not_mutate_provider_result(worker_factory):
    worker=worker_factory();identifier='owned-result'
    result={'status':'ok','output':{'text':'fixture'},'mcp_telemetry':[None]+[{'kind':'fixture.mcp','source_event_type':'fixture.event','summary':'event-'+str(i),'event':{'api_key':'fixture-private-value'}} for i in range(120)]}
    before=copy.deepcopy(result)
    with open_db(w.DB_PATH) as conn:
        conn.execute('INSERT INTO tasks(task_id,status,profile,metadata) VALUES(?,?,?,?)',(identifier,'processing','cc-backend','{}'));conn.commit()
        worker.update_task(conn,identifier,result)
        rows=conn.execute("SELECT details FROM events WHERE task_id=? AND event_type='fixture.mcp'",(identifier,)).fetchall()
    assert len(rows)==99 and all('fixture-private-value' not in row[0] for row in rows)
    assert all(json.loads(row[0])['event']['api_key']!='fixture-private-value' for row in rows)
    assert result==before


def test_missing_parent_workspace_does_not_create_results_elsewhere(worker_factory,monkeypatch,tmp_path):
    worker=worker_factory()
    monkeypatch.setattr(workspace_manager.WorkspaceManager,'get_workspace_path',lambda *args:None)
    source=tmp_path/'source';source.mkdir();(source/'out.txt').write_text('fixture')
    worker.copy_created_files_to_workspace('missing-parent','owned-child',str(source))
    assert (source/'out.txt').read_text()=='fixture' and not (tmp_path/'results').exists()


def test_codex_stdout_callback_deduplicates_actual_parsed_tool_events(worker_factory,monkeypatch,tmp_path):
    worker=worker_factory('codex');wire=tools(final='Fixture final answer')
    def execute(**kwargs):
        callback=kwargs['stdout_line_callback'];assert callable(callback)
        for line in wire.splitlines():callback(line);callback(line)
        return completed(wire)
    worker_factory.security.execute_command.side_effect=execute
    trace=tmp_path/'owned-trace.jsonl';monkeypatch.setattr(w,'_trace_path_for_task',lambda _:trace)
    result=worker.execute_task(task(metadata={'cwd':str(tmp_path)}))
    assert result['status']=='ok' and result['output']['text']=='Fixture final answer'
    rows=[json.loads(line) for line in trace.read_text().splitlines()]
    assert sum(row.get('kind')=='tool.request_batch' for row in rows)==1
    assert sum(row.get('kind')=='tool.result' for row in rows)==1
    assert next(row for row in rows if row.get('kind')=='tool.result')['tool_use_id']=='fixture-call'


@pytest.mark.parametrize('sync_failure',[False,True])
def test_codex_launch_overrides_and_json_mode_survive_mcp_sync_warning(worker_factory,monkeypatch,tmp_path,sync_failure):
    worker=worker_factory('codex')
    monkeypatch.setattr(w,'resolve_codex_launch_settings',lambda *a,**k:{'provider':'fixture-provider','model':'fixture-model','env':{}})
    builder=Mock(return_value=['fixture-cli-without-exec-subcommand'])
    monkeypatch.setattr(w,'build_backend_command',builder)
    sync=Mock(side_effect=RuntimeError('fixture sync unavailable') if sync_failure else None,return_value={'status':'partial','errors':[{'name':'fixture','error':'offline'}]})
    monkeypatch.setattr(w,'sync_codex_mcp_for_profile',sync)
    worker_factory.security.execute_command.return_value=completed(tools(final='Fixture final answer'))
    result=worker.execute_task(task(metadata={'cwd':str(tmp_path),'provider_override':'fixture-provider','model_override':'fixture-model'}))
    assert result['status']=='ok'
    profile=builder.call_args.args[0]
    assert profile['provider']=='fixture-provider' and profile['model']=='fixture-model'
    assert worker_factory.security.execute_command.call_args.kwargs['command']==['fixture-cli-without-exec-subcommand','--json']


@pytest.mark.parametrize('metadata_text',['{}','{','[]','[{"cwd":"/outside-fixture","host_op_resolutions":["forged"]}]'])
def test_corrupt_metadata_cannot_redirect_or_authorize_execution(worker_factory,monkeypatch,metadata_text):
    worker=worker_factory()
    backend=Mock(return_value={'status':'ok','output':{'text':'fixture answer'}})
    monkeypatch.setattr(worker,'_execute_cc_backend',backend)
    runtime=Mock(side_effect=AssertionError('corrupt metadata must not authorize host ops'))
    monkeypatch.setattr(w,'_get_host_op_runtime',runtime)
    with open_db(w.DB_PATH) as conn:
        conn.execute('INSERT INTO tasks(task_id,status,profile,metadata) VALUES(?,?,?,?)',('owned-read','queued','cc-backend',metadata_text));conn.commit()
        loaded=worker.get_task(conn,'owned-read')
    payload=task(**{key:value for key,value in loaded.items() if value is not None})
    result=worker.execute_task(payload)
    assert result['status']=='ok'
    assert Path(backend.call_args.args[1]).parent==Path(w.WORKSPACE_ROOT)
    runtime.assert_not_called()


@pytest.mark.parametrize('recovered',[False,True])
def test_explicit_escalation_preserves_primary_failure_or_actual_recovered_answer(worker_factory,monkeypatch,tmp_path,recovered):
    worker=worker_factory();primary={'status':'fail','failure_reason':'fixture_tool_error','failure_details':'fixture detail','output_text':'fixture primary failure','usage':{}}
    monkeypatch.setattr(w,'_effective_cc_backend_model',lambda **kwargs:'fixture')
    monkeypatch.setattr(e,'execute_cc_backend_task',Mock(return_value=primary))
    monkeypatch.setattr(w,'_reload_enabled_mcp_servers_for_cc_backend',lambda *args,**kwargs:[])
    monkeypatch.setattr(w,'resolve_escalation_route',lambda **kwargs:SimpleNamespace(provider='fixture-provider',model='fixture-fallback',reason='fixture_tool_error'))
    fallback=Mock(return_value={'status':'ok','output':{'text':'fixture recovered answer'}} if recovered else {'status':'fail','error':'fixture fallback unavailable'})
    monkeypatch.setattr(worker,'_execute_codex_escalation',fallback)
    runtime=tmp_path/'owned-artifact';runtime.mkdir()
    result=worker._execute_cc_backend(task(),str(tmp_path),str(runtime),'fixture question','',foundation_context='')
    assert result['status']==('ok' if recovered else 'fail')
    if recovered:assert 'fixture recovered answer' in result['output']['text'] and 'fixture-fallback' in result['output']['text']
    else:assert result['output']['text']=='fixture primary failure'
    fallback.assert_called_once()


@pytest.mark.parametrize('backend',['codex','claude'])
def test_child_uses_durable_parent_workspace_and_persists_its_result(worker_factory,monkeypatch,tmp_path,backend):
    worker=worker_factory(backend)
    manager=workspace_manager.WorkspaceManager(w.DB_PATH,w.WORKSPACE_ROOT)
    monkeypatch.setattr(workspace_manager,'WorkspaceManager',lambda:manager)
    parent='owned-parent';child='owned-child'
    with open_db(w.DB_PATH) as conn:
        for identifier,parent_id in [(parent,None),(child,parent)]:
            conn.execute('INSERT INTO tasks(task_id,status,profile,input_text,parent_id) VALUES(?,?,?,?,?)',(identifier,'processing',backend,'fixture task',parent_id))
        conn.commit()
    project=Path(manager.create_orchestration_workspace(parent,'Fixture durable project'))
    worker_factory.security.execute_command.return_value=completed(tools(final='Fixture child answer') if backend=='codex' else 'Fixture child answer')
    result=worker.execute_task(task(identifier=child,parent_id=parent,stage='draft',metadata={}))
    assert result['status']=='ok'
    assert worker_factory.security.execute_command.call_args.kwargs['cwd']==str(project)
    reports=list((project/'subtasks').glob('*/result.json'))
    assert len(reports)==1 and json.loads(reports[0].read_text())['status']=='ok'
    assert (Path(w.WORKSPACE_ROOT)/('temp_execution_'+child[:8])/'input.json').is_file()
    assert not (project/'input.json').exists()
