"""Owned POSIX terminal input and restoration, with verified child coverage."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import coverage
import pytest

from native_terminal_support import wait_for_terminal


@pytest.mark.skipif(os.name!='posix',reason='Owned POSIX PTY and termios input contract')
@pytest.mark.parametrize('raw,expected',[
    (b'\x1b[A','up'),(b'\x1b[B','down'),(b'\x1b[D','backtab'),(b'\x1b[C','tab'),
    (b'\x1b[Z','escape'),(b'\t','tab'),(b'\r','enter'),(b'\x7f','backspace'),
    (b'\x03','c-c'),(b'\x13','c-s'),(b' ',' '),('ё'.encode(),'ё'),
])
def test_owned_terminal_decodes_key_and_restores_terminal_mode(tmp_path,raw,expected):
    import pty
    import select
    import termios
    from factory.core import cli_chat_user_input as source
    repo=Path(__file__).resolve().parents[1]
    source_path=Path(source.__file__).resolve()
    assert source_path==repo/'factory/core/cli_chat_user_input.py'
    parent_file=os.environ.get('COVERAGE_FILE')
    artifact=(Path(parent_file).parent/(Path(parent_file).name.removeprefix('.coverage.')+'-native-keys')/raw.hex()) if parent_file else tmp_path/'proof'
    artifact.mkdir(parents=True)
    report=artifact/'result.json';data_file=artifact/'child.coverage';child=tmp_path/'key_child.py'
    child.write_text('''import hashlib,json,sys,termios
from pathlib import Path
from factory.core import cli_chat_user_input as source
old=termios.tcgetattr(sys.stdin.fileno())
key=source._read_key_plain()
result={"key":key,"terminal_restored":termios.tcgetattr(sys.stdin.fileno())==old,"source_sha256":hashlib.sha256(Path(source.__file__).read_bytes()).hexdigest()}
Path(sys.argv[1]).write_text(json.dumps(result))
''')
    env={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL'}}
    env.update(HOME=str(tmp_path),FACTORY_DATA_DIR=str(tmp_path),IGOREK_FACTORY_HOME=str(tmp_path),PYTHONPATH=str(repo),TERM='xterm-256color')
    master,slave=pty.openpty()
    process=subprocess.Popen([sys.executable,'-m','coverage','run','--branch','--source=factory,desktop','--data-file='+str(data_file),str(child),str(report)],
        stdin=slave,stdout=slave,stderr=slave,cwd=repo,env=env)
    os.close(slave);transcript=bytearray()
    def pump():
        if select.select([master],[],[],0.02)[0]:
            try:transcript.extend(os.read(master,65536))
            except OSError:pass
    def wait(predicate):
        wait_for_terminal(predicate,process=process,pump=pump,diagnostic=lambda:transcript.decode(errors='replace'),timeout=5)
    try:
        wait(lambda:not (termios.tcgetattr(master)[3]&termios.ICANON))
        os.write(master,raw)
        wait(lambda:report.exists())
        wait(lambda:process.poll() is not None)
        assert process.returncode==0
        result=json.loads(report.read_text())
        assert result['key']==expected and result['terminal_restored']
        assert result['source_sha256']==hashlib.sha256(source_path.read_bytes()).hexdigest()
        data=coverage.CoverageData(basename=str(data_file));data.read()
        assert data.has_arcs() and data.lines(str(source_path))
        active=coverage.Coverage.current()
        if active:
            assert active.config.branch
            active.get_data().update(data)
            assert set(data.lines(str(source_path))).issubset(active.get_data().lines(str(source_path)))
    finally:
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
        os.close(master)
        (artifact/'terminal.bin').write_bytes(transcript)
