"""Owned Linux guest PTY: real hardening, geometry, exit status and safe cleanup."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import time

import coverage
import pytest


@pytest.mark.skipif(sys.platform!='linux',reason='Linux capset/no_new_privs and owned PTY acceptance')
@pytest.mark.parametrize('exit_status',[0,7,'signal'])
def test_real_guest_pty_hardening_resize_and_reaped_child_cleanup(tmp_path,monkeypatch,exit_status):
    from factory.core.isolation import guest_terminal as g
    repo=Path(os.environ.get('IGOREK_FACTORY_REPO',Path(__file__).resolve().parents[1]))
    source=repo/'factory/core/isolation/guest_terminal.py';digest=hashlib.sha256(source.read_bytes()).hexdigest()
    parent_file=os.environ.get('COVERAGE_FILE')
    artifact=(Path(parent_file).parent/(Path(parent_file).name.removeprefix('.coverage.')+'-guest-pty-artifacts')/str(exit_status)) if parent_file else tmp_path/'native'
    artifact.mkdir(parents=True)
    preexec=artifact/'preexec.json';report=artifact/'report.json';data=artifact/'child.coverage';helper=artifact/'report.py'
    helper.write_text('''import json,os,sys
from pathlib import Path
status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc/self/status').read_text().splitlines() if ':' in line}
Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'ppid':os.getppid(),'cwd':os.getcwd(),'size':list(os.get_terminal_size(0)),'uid':os.getuid(),'nnp':status['NoNewPrivs'],'caps':[status[key] for key in ['CapEff','CapPrm','CapInh']]}))
print('OWNED-PTY-REPORT',flush=True)
if sys.argv[2]=='signal':
    import signal
    os.kill(os.getpid(),signal.SIGTERM)
else:sys.exit(int(sys.argv[2]))
''')
    home=tmp_path/'home';home.mkdir();work=tmp_path/'work';work.mkdir()
    monkeypatch.setenv('HOME',str(home));monkeypatch.setenv('LANG','C.UTF-8')
    actual_exec=os.execve;actual_kill=os.kill
    saved_pid=None;reaped=False;signal_after_reap=[]
    def execute(shell,args,env):
        status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc/self/status').read_text().splitlines() if ':' in line}
        current=coverage.Coverage.current();captured=current is not None
        if current is not None:
            current.stop();child=coverage.CoverageData(basename=str(data));child.update(current.get_data());child.write()
        preexec.write_text(json.dumps({'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'coverage_saved':captured,'nnp':status['NoNewPrivs'],'caps':[status[key] for key in ['CapEff','CapPrm','CapInh']]}))
        actual_exec(shell,args,env)
    def kill(pid,signum):
        if reaped and pid==saved_pid:
            signal_after_reap.append([pid,signum]);return
        actual_kill(pid,signum)
    monkeypatch.setattr(g.os,'execve',execute);monkeypatch.setattr(g.os,'kill',kill)
    pty=g.GuestPty(g.TerminalTuning(shell='/bin/bash',workdir=str(work),no_new_privs=True,drop_capabilities=True))
    expected=128+signal.SIGTERM if exit_status=='signal' else exit_status
    output=bytearray();observed=None
    try:
        assert pty.read(0) is None
        with pytest.raises(g.TerminalProtocolError,match='terminal_closed'):pty.write(b'not sent')
        pty.start(31,101);saved_pid=pty.pid;assert saved_pid is not None
        with pytest.raises(g.TerminalProtocolError,match='terminal_exists'):pty.start(1,1)
        pty.resize(17,63)
        command='exec '+shlex.join([sys.executable,'-I',str(helper),str(report),str(exit_status)])+'\n'
        pty.write(command.encode())
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            chunk=pty.read(.05)
            if chunk:
                output.extend(chunk)
                for _ in range(chunk.count(b'\x1b[6n')):pty.write(b'\x1b[1;1R')
            if report.exists():
                observed=pty.poll_exit()
                if observed is not None:reaped=True;break
        assert observed==expected,output.decode(errors='replace')[-1500:]
        assert report.exists() and preexec.exists()
        result=json.loads(report.read_text());proof=json.loads(preexec.read_text())
        assert result['pid']==saved_pid and result['ppid']==os.getpid() and result['uid']==os.getuid()
        assert result['cwd']==str(work) and result['size']==[63,17]
        assert result['nnp']==proof['nnp']=='1'
        assert all(int(value,16)==0 for value in result['caps']+proof['caps'])
        assert proof['source_sha256']==digest==hashlib.sha256(source.read_bytes()).hexdigest()
        assert pty.poll_exit()==expected
    finally:
        pty.close();pty.close();(artifact/'terminal.log').write_bytes(output)
    assert signal_after_reap==[], 'Cleanup must never signal a PID after waitpid has reaped it'
    parent=coverage.Coverage.current()
    if parent is not None:
        assert proof['coverage_saved'] and parent.config.branch
        child=coverage.CoverageData(basename=str(data));child.read();assert child.has_arcs() and child.lines(str(source))
        parent.get_data().update(child)
    (artifact/'parent-acceptance.json').write_text(json.dumps({'native_linux_pty':True,'source_sha256':digest,'geometry':[63,17],'no_new_privs':True,'capabilities_zero':True,'exit_status':int(expected),'reaped_pid_not_signalled':True}))
