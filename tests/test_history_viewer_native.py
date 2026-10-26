"""Owned PTY history-viewer acceptance; the clipboard is an in-memory fixture."""
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


@pytest.mark.skipif(os.name!='posix',reason='POSIX PTY geometry, raw SGR mouse input and terminal restoration')
def test_real_viewer_mouse_copy_expansion_and_terminal_restoration(tmp_path):
    import fcntl
    import pty
    import select
    import struct
    import termios
    repo=Path(__file__).resolve().parents[1]
    names=['factory/core/tui/mouse_viewer.py','factory/core/tui/mouse_controller.py','factory/core/tui/mouse_frame.py']
    frozen={name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in names}
    parent_file=os.environ.get('COVERAGE_FILE')
    artifact=(Path(parent_file).parent/(Path(parent_file).name+'-viewer-native')) if parent_file else tmp_path/'native'
    artifact.mkdir(parents=True)
    peer=artifact/'peer.py';report=artifact/'report.json';data=artifact/'child.coverage'
    peer.write_text(textwrap.dedent('''
        import hashlib,json,sys
        from pathlib import Path
        from types import SimpleNamespace
        from factory.core.tui.mouse_controller import MouseController
        from factory.core.tui.mouse_viewer import MouseHistoryViewer,ViewerItem
        artifact=Path(sys.argv[1]);repo=Path(sys.argv[2]);copied=[]
        def copy(text):copied.append(text);return SimpleNamespace(message='Fixture copied')
        controller=MouseController(user_enabled=True,clipboard_service=SimpleNamespace(copy=copy))
        item=ViewerItem('fixture-tool',['tool header'],['tool header','expanded A','expanded B'],True)
        viewer=MouseHistoryViewer(items=[item,ViewerItem('bulk',[f'row {i}' for i in range(40)])],controller=controller)
        viewer.run()
        names=['factory/core/tui/mouse_viewer.py','factory/core/tui/mouse_controller.py','factory/core/tui/mouse_frame.py']
        result={'copied':copied,'expanded':controller.is_cell_expanded('fixture-tool'),'offset':viewer.offset,'selection_cleared':viewer.selection is None,'viewer_active':controller._viewer_active,'capture_enabled':bool(controller._capture_output and controller._capture_output.capture_enabled),'size':list(viewer._terminal_size()),'source_hashes':{name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in names}}
        (artifact/'report.json').write_text(json.dumps(result),encoding='utf-8')
    '''),encoding='utf-8')
    env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','SYSTEMROOT','WINDIR'}}
    env.update(HOME=str(tmp_path),USERPROFILE=str(tmp_path),FACTORY_DATA_DIR=str(tmp_path/'data'),IGOREK_FACTORY_HOME=str(tmp_path/'data'),PYTHONPATH=str(repo),TERM='xterm-256color',IGOREK_TUI_MOUSE='1',PYTHONUNBUFFERED='1')
    master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',32,100,0,0));initial=termios.tcgetattr(slave)
    process=subprocess.Popen([sys.executable,'-m','coverage','run','--branch','--source=factory,desktop','--data-file='+str(data),str(peer),str(artifact),str(repo)],stdin=slave,stdout=slave,stderr=slave,cwd=repo,env=env)
    os.close(slave);output=bytearray();replies=0
    def pump(seconds=.1):
        nonlocal replies
        import time
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if select.select([master],[],[],.03)[0]:
                try:raw=os.read(master,65536)
                except OSError:return
                output.extend(raw)
                for _ in range(raw.count(b'\x1b[6n')):os.write(master,b'\x1b[1;1R');replies+=1
    def wait(predicate,timeout=8):
        return wait_for_terminal(predicate,process=process,pump=pump,timeout=timeout,diagnostic=lambda:output.decode(errors='replace')[-1800:])
    try:
        wait(lambda:not(termios.tcgetattr(master)[3]&termios.ICANON));pump(.2)
        os.write(master,b'\x1b[<0;1;1M\x1b[<32;5;1M\x1b[<0;5;1m\x1b[<0;1;1M\x1b[<0;1;1m\x1b[<65;2;2M\x1b[<64;2;2Mq')
        wait(lambda:report.exists());wait(lambda:process.poll() is not None,timeout=3)
        assert process.returncode==0
        result=json.loads(report.read_text());assert result['source_hashes']==frozen
        assert result['copied']==['tool'] and result['expanded'] and result['offset']==0
        assert result['selection_cleared'] and not result['viewer_active'] and not result['capture_enabled']
        assert result['size']==[100,32]
        restored=termios.tcgetattr(master)
        assert restored[3]&(termios.ICANON|termios.ECHO)==initial[3]&(termios.ICANON|termios.ECHO)
        assert b'tool header' in output
        child=coverage.CoverageData(basename=str(data));child.read()
        assert child.has_arcs() and child.lines(str(repo/names[0]))
        parent=coverage.Coverage.current()
        if parent is not None:
            assert parent.config.branch
            parent.get_data().update(child)
        (artifact/'parent-acceptance.json').write_text(json.dumps({'native_pty':True,'window':[100,32],'cursor_replies':replies,'source_hashes_verified':True,'terminal_restored':True,'clipboard_backend':'in-memory fixture'}))
    finally:
        if process.poll() is None:process.kill();process.wait(3)
        os.close(master)
        (artifact/'terminal.log').write_bytes(output)
