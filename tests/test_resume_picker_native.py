"""Resume/fork picker acceptance through an owned PTY, never user history."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import coverage
import pytest
from native_terminal_support import wait_for_terminal


@pytest.mark.skipif(os.name!='posix',reason='POSIX PTY geometry/raw key and terminal restoration acceptance')
@pytest.mark.parametrize('mode,keys,expected,chosen',[
    ('enter',[b'\r'],'resume','session-000'),
    ('down',[b'\x1b[B',b'\r'],'resume','session-001'),
    ('page',[b'\x1b[6~',b'\x1b[5~',b'\r'],'resume','session-000'),
    ('sort',[b'\t',b'\r'],'resume','session-039'),
    ('query',[b'beta\r'],'resume','session-005'),
    ('unicode',['осьминог\r'.encode()],'resume','session-009'),
    ('fork',[b'\r'],'fork','session-000'),
    ('escape',[b'\x1b'],'start_fresh',None),
    ('interrupt',[b'\x03'],'exit',None),
    ('up',[b'\x1b[B',b'\x1b[A',b'\r'],'resume','session-000'),
    ('backspace',[b'beta\x7fa\r'],'resume','session-005'),
    ('no-match',[b'no-match\r\x03'],'exit',None),
    ('partial-escape',[b'\x1b['],'start_fresh',None),
])
def test_owned_picker_keys_query_and_terminal_restore(tmp_path,mode,keys,expected,chosen):
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time
    repo=Path(os.environ.get('IGOREK_FACTORY_REPO',Path(__file__).resolve().parents[1]))
    names=['factory/core/tui/resume_picker.py','factory/core/tui/tui.py','factory/core/tui/render/renderable.py']
    frozen={name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in names}
    parent_file=os.environ.get('COVERAGE_FILE')
    artifact=(Path(parent_file).parent/(Path(parent_file).name.removeprefix('.coverage.')+'-picker-artifacts')/mode) if parent_file else tmp_path/'native'
    artifact.mkdir(parents=True)
    peer=artifact/'peer.py';report=artifact/'report.json';data=artifact/'child.coverage'
    peer.write_text(textwrap.dedent('''
        import hashlib,json,os,sys
        from pathlib import Path
        from factory.core.tui.resume_picker import run_resume_picker,SessionPickerAction
        artifact=Path(sys.argv[1]);repo=Path(sys.argv[2]);mode=sys.argv[3]
        sessions=[{'session_id':f'session-{i:03}','path':str(artifact/f'{i}.jsonl'),'first_user_message':'beta' if i==5 else 'осьминог' if i==9 else 'alpha','model':'fixture','updated_at':1000-i,'created_at':2000+i,'message_count':2} for i in range(40)]
        result,selected=run_resume_picker(sessions,action=SessionPickerAction.FORK if mode=='fork' else SessionPickerAction.RESUME)
        names=['factory/core/tui/resume_picker.py','factory/core/tui/tui.py','factory/core/tui/render/renderable.py']
        (artifact/'report.json').write_text(json.dumps({'selection':result.value,'chosen':selected['session_id'] if selected else None,'size':list(os.get_terminal_size(0)),'pid':os.getpid(),'source_hashes':{name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in names}}))
    '''),encoding='utf-8')
    env={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL','SYSTEMROOT','WINDIR'}}
    env.update(HOME=str(tmp_path/'home'),USERPROFILE=str(tmp_path/'home'),FACTORY_DATA_DIR=str(tmp_path/'data'),IGOREK_FACTORY_HOME=str(tmp_path/'data'),PYTHONPATH=str(repo),TERM='xterm-256color',LANG='C.UTF-8',PYTHONUNBUFFERED='1')
    master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',20,100,0,0));initial=termios.tcgetattr(slave)
    process=subprocess.Popen([sys.executable,'-m','coverage','run','--branch','--source=factory,desktop','--data-file='+str(data),str(peer),str(artifact),str(repo),mode],stdin=slave,stdout=slave,stderr=slave,cwd=repo,env=env)
    os.close(slave);output=bytearray();replies=0
    def pump(seconds=.03):
        nonlocal replies
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if select.select([master],[],[],.01)[0]:
                try:raw=os.read(master,65536)
                except OSError:return
                output.extend(raw)
                for _ in range(raw.count(b'\x1b[6n')):os.write(master,b'\x1b[1;1R');replies+=1
    def wait(predicate,timeout=6):
        return wait_for_terminal(predicate,process=process,pump=pump,timeout=timeout,diagnostic=lambda:output.decode(errors='replace')[-1400:])
    try:
        title=b'Fork session' if mode=='fork' else b'Resume session'
        wait(lambda:title in output and not(termios.tcgetattr(master)[3]&termios.ICANON))
        for index,key in enumerate(keys):
            old=len(output);os.write(master,key)
            if index<len(keys)-1:wait(lambda:len(output)>old and not(termios.tcgetattr(master)[3]&termios.ICANON))
        wait(lambda:report.exists(),timeout=3)
        wait(lambda:process.poll() is not None,timeout=3)
        assert process.returncode==0
        result=json.loads(report.read_text());assert result['source_hashes']==frozen
        assert result['selection']==expected and result['chosen']==chosen and result['size']==[100,20]
        assert result['pid']==process.pid
        restored=termios.tcgetattr(master)
        assert restored[3]&(termios.ICANON|termios.ECHO)==initial[3]&(termios.ICANON|termios.ECHO)
        assert b'\x1b[?1049h' in output and b'\x1b[?1049l' in output
        child=coverage.CoverageData(basename=str(data));child.read();assert child.has_arcs() and child.lines(str(repo/names[0]))
        parent=coverage.Coverage.current()
        if parent is not None:assert parent.config.branch;parent.get_data().update(child)
        (artifact/'parent-acceptance.json').write_text(json.dumps({'native_pty':True,'geometry':[100,20],'cursor_replies':replies,'terminal_restored':True,'source_hashes_verified':True,'mode':mode,'selection':expected,'chosen':chosen}))
    finally:
        if process.poll() is None:process.kill();process.wait(3)
        os.close(master);(artifact/'terminal.log').write_bytes(output)
