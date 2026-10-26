"""Approval reuse and native child-process text/timeout contracts on owned data."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import coverage
import pytest

from factory.core import security_runtime as runtime
from factory.core.security_store import SecurityStore


@pytest.fixture
def guard(tmp_path,monkeypatch):
    monkeypatch.setenv('FACTORY_SECURITY_RUNTIME_MODE','enforce')
    monkeypatch.setenv('FACTORY_BOLTBOOK_HEARTBEAT_ENABLED','false')
    monkeypatch.setenv('FACTORY_BOLTBOOK_AUTONOMOUS_COMMENTS_ENABLED','false')
    monkeypatch.setenv('FACTORY_BOLTBOOK_AUTONOMOUS_POSTS_ENABLED','false')
    store=SecurityStore(str(tmp_path/'security.db'))
    value=runtime.SecurityRuntime(store)
    policy={'static':{'allowed_tools':['codex'],'workspace_only':True},'dynamic':{'approval_required_for':[],'cloud_model_routes':[],'auto_approve_contexts':{}}}
    monkeypatch.setattr(value,'status',lambda:{'mode':value.mode,'active_policy':'fixture','policy':policy})
    run=Mock(return_value=subprocess.CompletedProcess(['fixture'],0,'fixture output',''))
    monkeypatch.setattr(runtime.subprocess,'run',run)
    def execute(**extra):
        return value.execute_command(command=['fixture','--dangerously-skip-permissions'],input_payload='fixture input',env={'FIXTURE':'yes'},cwd=str(tmp_path),timeout_s=2,task_id='fixture-task',profile='fixture',backend='codex',**extra)
    return SimpleNamespace(value=value,store=store,policy=policy,run=run,execute=execute)


@pytest.mark.parametrize('mode',['disabled','observe','enforce'])
def test_security_modes_preserve_explicit_observe_and_enforce_distinction(guard,mode):
    if mode=='enforce':
        with pytest.raises(PermissionError):guard.execute(security_mode=mode)
        assert not guard.run.called
    else:
        process,identity=guard.execute(security_mode=mode)
        assert process.stdout=='fixture output' and identity and guard.run.call_count==1
    approvals=guard.store.list_approvals()
    assert len(approvals)==(0 if mode=='disabled' else 1)


@pytest.mark.parametrize('proof',['none','wrong-id','wrong-resolver','retry','session'])
def test_interactive_approval_is_reused_only_with_matching_explicit_proof(guard,proof):
    with pytest.raises(PermissionError):guard.execute()
    approval=guard.store.list_approvals()[0]
    resolver='tui:session' if proof=='session' else 'tui:user'
    if proof=='wrong-resolver':resolver='api'
    guard.store.resolve_approval(approval['approval_id'],'approved',resolved_by=resolver)
    metadata={}
    if proof in {'retry','wrong-id','wrong-resolver'}:metadata={'interactive_retry_approval_ids':[approval['approval_id'] if proof!='wrong-id' else 'not-this-approval']}
    if proof=='session':metadata={'interactive_session_approval_ids':[approval['approval_id']]}
    if proof in {'retry','session'}:
        process,_=guard.execute(task_metadata=metadata)
        assert process.returncode==0 and guard.run.call_count==1
        assert len(guard.store.list_approvals())==1
    else:
        with pytest.raises(PermissionError):guard.execute(task_metadata=metadata)
        assert not guard.run.called and len(guard.store.list_approvals())==2


@pytest.mark.parametrize('allowed',[False,True])
def test_autonomous_autoapproval_requires_the_configured_target_and_flags(guard,monkeypatch,allowed):
    monkeypatch.setenv('FACTORY_BOLTBOOK_HEARTBEAT_ENABLED','true');monkeypatch.setenv('FACTORY_BOLTBOOK_AUTONOMOUS_COMMENTS_ENABLED','true')
    guard.policy['dynamic']['auto_approve_contexts']={'autonomous_boltbook':{'exec_binary':{'targets':['fixture' if allowed else 'other'],'command_prefix':'fixture','required_flags':['--dangerously-skip-permissions']}}}
    if allowed:
        process,_=guard.execute()
        assert process.returncode==0 and guard.store.list_approvals()[0]['resolved_by']=='policy:auto'
    else:
        with pytest.raises(PermissionError):guard.execute()
        assert not guard.run.called


def test_cloud_route_outside_allowlist_is_a_separate_approval_target(guard):
    guard.policy['dynamic']['approval_required_for']=['cloud_model_route']
    observations=guard.value._build_policy_observations(command=['fixture'],backend='codex',profile='fixture',model='fixture-model',provider='fixture-provider',cwd='/fixture',policy=guard.policy)
    assert observations==[{'approval_type':'cloud_model_route','target':'fixture-provider:fixture-model','reason':"Route 'fixture-provider:fixture-model' is outside cloud_model_routes allowlist"}]
    guard.policy['dynamic']['cloud_model_routes']=['fixture-provider:fixture-model']
    assert not guard.value._build_policy_observations(command=['fixture'],backend='codex',profile='fixture',model='fixture-model',provider='fixture-provider',cwd='/fixture',policy=guard.policy)


@pytest.mark.parametrize('mode',['unicode','invalid-bytes','timeout'])
def test_actual_ascii_parent_keeps_utf8_protocol_and_reaps_its_owned_child(tmp_path,mode):
    repo=Path(runtime.__file__).resolve().parents[2]
    source=Path(runtime.__file__).resolve();digest=hashlib.sha256(source.read_bytes()).hexdigest()
    cov_file=os.environ.get('COVERAGE_FILE')
    artifact=(Path(cov_file).parent/(Path(cov_file).name+'-native')/mode) if cov_file else tmp_path/'native'
    artifact.mkdir(parents=True)
    report=artifact/'report.json';data=artifact/'child.coverage';peer=artifact/'peer.py';sdk=artifact/'sdk.py'
    program='import time; time.sleep(60)' if mode=='timeout' else ("import sys\ndata=sys.stdin.read()\nsys.stdout.buffer.write((data + "+repr('\nПривет 🐙\n')+").encode('utf-8'))\nsys.stderr.buffer.write("+repr(b'stderr\xff\n' if mode=='invalid-bytes' else b'stderr fixture\n')+")\n")
    sdk.write_text(program,encoding='utf-8')
    peer.write_text(textwrap.dedent('''
        import hashlib,json,locale,os,subprocess,sys
        from pathlib import Path
        from factory.core import security_runtime as module
        report=Path(sys.argv[1]);mode=sys.argv[2];root=Path(sys.argv[3]);sdk=Path(sys.argv[4])
        processes=[];real=module.subprocess.Popen
        def spawn(*args,**kwargs):
            value=real(*args,**kwargs);processes.append(value);return value
        module.subprocess.Popen=spawn
        result={'parent_encoding':locale.getencoding(),'source':str(Path(module.__file__).resolve()),'source_sha256':hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()}
        callback=[]
        env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}
        try:
            value=module.SecurityRuntime.__new__(module.SecurityRuntime)._run_with_stdout_callback(command=[sys.executable,str(sdk)],input_payload='Ввод 🐙' if mode=='unicode' else 'fixture input',env=env,cwd=str(root),timeout_s=0.05 if mode=='timeout' else 3,stdout_line_callback=callback.append)
            result.update(stdout=value.stdout,stderr=value.stderr,returncode=value.returncode,callback=callback)
        except BaseException as exc:result['error']=type(exc).__name__
        result['spawned']=bool(processes)
        result['reaped_before_fixture_cleanup']=bool(processes and processes[0].returncode is not None)
        for process in processes:
            if process.poll() is None:process.kill()
            process.wait(timeout=2)
        report.write_text(json.dumps(result,ensure_ascii=True),encoding='ascii')
    '''),encoding='utf-8')
    env={k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP'}}
    env.update(HOME=str(tmp_path),USERPROFILE=str(tmp_path),FACTORY_DATA_DIR=str(tmp_path/'data'),IGOREK_FACTORY_HOME=str(tmp_path/'data'),PYTHONPATH=str(repo),LC_ALL='C',LANG='C',PYTHONCOERCECLOCALE='0',PYTHONUTF8='0',PYTHONIOENCODING='ascii')
    with (artifact/'parent.log').open('w') as log:
        child=subprocess.run([sys.executable,'-m','coverage','run','--branch','--source=factory,desktop','--data-file='+str(data),str(peer),str(report),mode,str(tmp_path),str(sdk)],cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=12)
    assert child.returncode==0,(artifact/'parent.log').read_text(errors='replace')
    result=json.loads(report.read_text())
    assert result['source']==str(source) and result['source_sha256']==digest==hashlib.sha256(source.read_bytes()).hexdigest()
    assert result['spawned']
    if os.name=='posix':assert result['parent_encoding'].lower() in {'ascii','ansi_x3.4-1968'}
    if mode=='timeout':
        assert result['error']=='TimeoutExpired' and result['reaped_before_fixture_cleanup']
    else:
        expected='Ввод 🐙' if mode=='unicode' else 'fixture input'
        assert 'error' not in result and result['returncode']==0
        assert result['stdout']==expected+'\nПривет 🐙\n' and result['callback']==[expected+'\n','Привет 🐙\n']
        assert result['stderr']==('stderr�\n' if mode=='invalid-bytes' else 'stderr fixture\n')
    child_data=coverage.CoverageData(basename=str(data));child_data.read()
    assert child_data.has_arcs() and child_data.lines(str(source))
    parent=coverage.Coverage.current()
    if parent is not None:
        assert parent.config.branch
        parent.get_data().update(child_data)
