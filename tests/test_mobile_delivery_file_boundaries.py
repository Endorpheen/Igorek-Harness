"""Mobile result delivery and attachment scope over owned SQLite and session files."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.mobile_bridge import service as m
from factory.core.mobile_bridge.store import MobileStore
from factory.core import cli_chat,cli_tasks
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database,open_db


@pytest.fixture
def mobile(tmp_path,monkeypatch):
    root=tmp_path/'data';work=tmp_path/'work';work.mkdir();profiles=root/'profiles';profiles.mkdir(parents=True)
    (profiles/'fixture.json').write_text(json.dumps({'backend':'cc-backend','model':'fixture','base_url':'http://127.0.0.1:1','api_format':'openai'}))
    for key in ['FACTORY_DATA_DIR','IGOREK_FACTORY_HOME']:monkeypatch.setenv(key,str(root))
    monkeypatch.setenv('FACTORY_PROFILES_DIR',str(profiles));monkeypatch.setenv('HOME',str(tmp_path/'home'));monkeypatch.setenv('FACTORY_SESSION_EMBEDDINGS_ENABLED','off');monkeypatch.setenv('FACTORY_VAULT_ENABLED','false')
    db=get_db_path();initialize_database(db);store=MobileStore(db_path=db)
    external=Mock();external.list_sessions.return_value={'sessions':[]}
    service=m.MobileService(profile='fixture',workdir=str(work),store=store,data_dir=str(root),external_sessions=external,isolation_status=lambda:'none',available_profiles=lambda:['fixture'])
    session='owned-session';path=cli_chat._session_path(session);cli_chat._write_session_meta(path,session_id=session,profile='fixture',cwd=str(work))
    service._foundation=''
    def tasks():
        with open_db(db) as conn:
            conn.row_factory=sqlite3.Row
            return [dict(row) for row in conn.execute('SELECT * FROM tasks')]
    return SimpleNamespace(service=service,store=store,db=db,root=root,work=work,session=session,path=path,tasks=tasks,external=external)


def test_opencode_result_queues_one_readonly_followup_and_journals_exact_task(mobile):
    f=mobile;cli_chat._append_entry(f.path,role='user',text='fixture original question')
    run={'id':'owned-run','session':f.session,'profile':'fixture','goal':'fixture goal','result':'fixture verified result','source':str(f.work)}
    assert f.service.deliver_opencode_result(run) is True
    rows=f.tasks();assert len(rows)==1
    metadata=json.loads(rows[0]['metadata'])
    assert metadata['opencode_result_for']=='owned-run' and metadata['session']==f.session
    assert metadata['restrict_tools_to']==['Read','Grep','Glob'] and metadata['cwd']==str(f.work)
    assert 'fixture goal' in rows[0]['input_text'] and 'fixture verified result' in rows[0]['input_text']
    events=f.store.list_events(session_id=f.session)
    assert events[-1]['kind']=='task.submitted' and events[-1]['task_id']==rows[0]['task_id']
    assert events[-1]['payload']['text']==''
    with open_db(f.db) as conn:conn.execute("UPDATE tasks SET status='ok' WHERE task_id=?",(rows[0]['task_id'],))
    assert f.service.deliver_opencode_result(run) is True and len(f.tasks())==1
    assert len(f.store.list_events(session_id=f.session))==len(events)


def test_busy_session_defers_result_without_creating_second_task(mobile):
    f=mobile
    cli_tasks.create_task(text='fixture active',profile='fixture',metadata={'source':'mobile_bridge','session':f.session,'cwd':str(f.work)})
    run={'id':'owned-run','session':f.session,'profile':'fixture','goal':'fixture','result':'fixture','source':str(f.work)}
    assert f.service.deliver_opencode_result(run) is False and len(f.tasks())==1
    assert f.store.list_events(session_id=f.session)==[]


def test_unavailable_result_profile_is_rejected_before_queuing(mobile):
    f=mobile;f.service.available_profiles=lambda:[]
    run={'id':'owned-run','session':f.session,'profile':'fixture','goal':'fixture','result':'fixture','source':str(f.work)}
    with pytest.raises(m.MobileInvalid):f.service.deliver_opencode_result(run)
    assert f.tasks()==[] and f.store.list_events(session_id=f.session)==[]


@pytest.mark.parametrize('relative',['.delegate_child','.delegate_child/.delegate_nested'])
def test_child_files_include_only_owned_delegate_workspace(mobile,monkeypatch,relative):
    f=mobile;child=f.work/relative;child.mkdir(parents=True)
    output=child/'result.txt';output.write_text('fixture result')
    parent=f.work/'parent.txt';parent.write_text('fixture unrelated parent')
    record={'root_session_id':f.session,'session_id':'child-session','workdir':'/workspace/'+relative,'summary':str(output)}
    monkeypatch.setattr(f.service,'_agent_record',lambda identity:record if identity=='child-session' else None)
    for path in [output,parent]:
        f.store.append_event(kind='tool.result',session_id='child-session',payload={'status':'done','tool_name':'Write','tool_input':{'file_path':'/workspace/'+path.relative_to(f.work).as_posix()}})
    found=f.service._session_files('child-session')
    assert [path for _,path in found]==[output]
    assert found[0][0]['name']=='result.txt'


@pytest.mark.parametrize('candidate',['root','outside','ordinary-subdir','empty'])
def test_invalid_delegate_workdir_never_exposes_parent_session_files(mobile,monkeypatch,tmp_path,candidate):
    f=mobile;(f.work/'parent.txt').write_text('fixture parent')
    paths={'root':str(f.work),'outside':str(tmp_path),'ordinary-subdir':str(f.work/'ordinary'),'empty':''}
    record={'root_session_id':f.session,'session_id':'child-session','workdir':paths[candidate],'summary':str(f.work/'parent.txt')}
    monkeypatch.setattr(f.service,'_agent_record',lambda identity:record if identity=='child-session' else None)
    assert f.service._session_files('child-session')==[]


def test_root_source_and_change_index_publish_only_relative_visible_paths(mobile,monkeypatch):
    f=mobile;(f.work/'source.md').write_text('source');(f.work/'changed.txt').write_text('change')
    monkeypatch.setattr(f.service._source_index,'file_paths',lambda _:['source.md'])
    monkeypatch.setattr(f.service,'session_changes',lambda _:{'files':[{'path':''},{'path':str(f.work/'absolute.txt')},{'path':'changed.txt'}]})
    found=f.service.list_session_files(f.session)
    assert {item['path'] for item in found}=={'source.md','changed.txt'}
    assert all(not Path(item['path']).is_absolute() for item in found)


def test_journal_task_reassignment_preserves_metadata_and_malformed_lines(tmp_path):
    path=tmp_path/'history.jsonl'
    meta='{"type":"meta","session_id":"fixture"}'
    path.write_text(meta+'\nnot-json\n\n'+json.dumps({'role':'user','message_id':'owned-message','task_id':'old','text':'fixture'})+'\n'+json.dumps({'role':'user','message_id':'other-message','task_id':'keep'})+'\n')
    m._reassign_journal_task_id(path,{'owned-message'},'new-task')
    lines=path.read_text().splitlines();assert json.loads(lines[0])==json.loads(meta)
    assert 'not-json' in lines and not path.with_name(path.name+'.tmp').exists()
    rows=[json.loads(line) for line in lines if line.startswith('{')]
    assert next(row for row in rows if row.get('message_id')=='owned-message')['task_id']=='new-task'
    assert next(row for row in rows if row.get('message_id')=='other-message')['task_id']=='keep'


@pytest.mark.parametrize('missing_ids',[True,False])
def test_unmatched_journal_reassignment_does_not_rewrite_bytes(tmp_path,missing_ids):
    path=tmp_path/'history.jsonl';content='\n{ "message_id" : "keep" }\n';path.write_text(content)
    m._reassign_journal_task_id(path,set() if missing_ids else {'missing'},'new-task')
    assert path.read_text()==content


def test_session_options_reject_nonfolders_and_survive_external_catalog_failure(mobile,monkeypatch,tmp_path):
    f=mobile;other=tmp_path/'other';other.mkdir();file=tmp_path/'not-a-folder';file.write_text('fixture')
    f.external.list_sessions.side_effect=OSError('fixture unavailable')
    monkeypatch.setattr(m,'list_sessions',lambda:[{'cwd':'relative'},{'cwd':str(file)},{'cwd':str(other)},{'cwd':str(f.work)}])
    f.service.available_profiles=lambda:['fixture','second']
    f.service.profile_control=lambda *args:{'profiles':[{'name':'fixture'}],'can_switch':True}
    options=f.service.session_options(workdir_limit=2)
    assert options['workdirs']==[str(f.work),str(other)] and options['default_cwd']==str(f.work)
    assert options['default_profile']=='fixture' and options['profile_switch_supported'] is True
    assert options['profile_catalog']==[{'name':'fixture'}]
