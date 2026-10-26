"""Real CLI directory-choice terminal acceptance with an owned chat boundary."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import coverage
import pytest

from native_terminal_support import wait_for_terminal


@pytest.mark.skipif(os.name!='posix',reason='Owned POSIX terminal geometry acceptance')
@pytest.mark.parametrize('mode',['session','current','cancel','control-d','missing','escape'])
def test_native_cli_resume_workdir_selection_and_terminal_restoration(tmp_path,mode):
    import fcntl
    import pty
    import select
    import struct
    import termios
    repo=Path(__file__).resolve().parents[1]
    parent=os.environ.get('COVERAGE_FILE')
    artifact=(Path(parent).parent/(Path(parent).name.removeprefix('.coverage.')+'-native-cwd')/mode) if parent else tmp_path/'proof'
    artifact.mkdir(parents=True)
    report=artifact/'result.json';data_file=artifact/'child.coverage';script=tmp_path/'cwd_child.py'
    script.write_text('''import hashlib,importlib,json,os,sys,termios,types
from pathlib import Path
root=Path(sys.argv[1]);mode=sys.argv[2];report=Path(sys.argv[3]);current=root/'current';saved=root/'saved'
current.mkdir(parents=True);saved.mkdir();os.chdir(current)
from factory import cli
chat=importlib.import_module('core.cli_chat');picker=importlib.import_module('core.tui.resume_picker')
chosen={'session_id':'native-fixture','cwd':str(saved/'missing') if mode=='missing' else str(saved)}
chat.list_sessions=lambda:[chosen]
picker.run_resume_picker=lambda sessions,**kwargs:(picker.SessionSelection.RESUME,chosen)
calls=[];rich=types.ModuleType('core.cli_chat_rich')
def run(**kwargs):calls.append({'session':kwargs['session'],'cwd':str(Path.cwd())});return 0
rich.run_rich_chat=run;sys.modules['core.cli_chat_rich']=rich
sys.argv=['igorek-factory','chat','--resume','--profile','fixture','--data-dir',str(root)]
old=termios.tcgetattr(sys.stdin.fileno());code=cli.main()
cwd=importlib.import_module('core.tui.cwd_prompt')
report.write_text(json.dumps({'return_code':code,'cwd':str(Path.cwd()),'calls':calls,'terminal_restored':termios.tcgetattr(sys.stdin.fileno())==old,'source_hashes':{str(Path(module.__file__).resolve()):hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() for module in [cli,cwd]}}))
''')
    root=tmp_path/'runtime'
    env={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL','SYSTEMROOT','WINDIR'}}
    env.update(HOME=str(root/'home'),USERPROFILE=str(root/'home'),FACTORY_DATA_DIR=str(root),IGOREK_FACTORY_HOME=str(root),FACTORY_ENV_FILES_DIR=str(root/'empty-env'),PYTHONPATH=str(repo),TERM='xterm-256color')
    master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',32,100,0,0))
    process=subprocess.Popen([sys.executable,'-m','coverage','run','--branch','--source=factory,desktop','--data-file='+str(data_file),str(script),str(root),mode,str(report)],
        stdin=slave,stdout=slave,stderr=slave,cwd=repo,env=env)
    os.close(slave);transcript=bytearray();replies=0
    def pump():
        nonlocal replies
        if select.select([master],[],[],.03)[0]:
            try:raw=os.read(master,65536)
            except OSError:return
            transcript.extend(raw)
            for _ in range(raw.count(b'\x1b[6n')):os.write(master,b'\x1b[1;1R');replies+=1
    def wait(predicate):
        wait_for_terminal(predicate,process=process,pump=pump,diagnostic=lambda:transcript.decode(errors='replace')[-2500:],timeout=7)
    try:
        wait(lambda:not (termios.tcgetattr(master)[3]&termios.ICANON) and b'Choose working directory' in transcript)
        keys={'session':b'\r','current':b'2','cancel':b'\x03','control-d':b'\x04','missing':b'\r','escape':b'\x1b'}
        os.write(master,keys[mode]);wait(lambda:report.exists());wait(lambda:process.poll() is not None)
        assert process.returncode==0
        result=json.loads(report.read_text());assert result['return_code']==0 and result['terminal_restored']
        for name,digest in result['source_hashes'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest
        assert set(result['source_hashes'])=={str(repo/'factory/cli.py'),str(repo/'factory/core/tui/cwd_prompt.py')}
        expected=root/('saved' if mode in {'session','escape'} else 'current')
        assert result['cwd']==str(expected)
        if mode in {'cancel','control-d'}:assert result['calls']==[]
        else:assert result['calls']==[{'session':'native-fixture','cwd':str(expected)}]
        if mode=='missing':assert b'Session directory not found' in transcript
        child=coverage.CoverageData(basename=str(data_file));child.read();assert child.has_arcs()
        for name in result['source_hashes']:assert child.lines(name)
        active=coverage.Coverage.current()
        if active:
            assert active.config.branch;active.get_data().update(child)
            for name in result['source_hashes']:assert set(child.lines(name)).issubset(active.get_data().lines(name))
        (artifact/'parent-acceptance.json').write_text(json.dumps({'source_hashes_verified':True,'child_branch_coverage_verified':True,'terminal_mode_restored':True,'cursor_replies':replies},indent=2)+'\n')
    finally:
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
        pump();os.close(master)
        (artifact/'terminal.bin').write_bytes(transcript)
        (artifact/'terminal.txt').write_text(re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',transcript.decode(errors='replace')))
