import os
from pathlib import Path
import shutil
import subprocess

import pytest

DESKTOP = Path(__file__).resolve().parents[1] / 'desktop'


@pytest.mark.parametrize('close_during_progress', [False, True])
def test_native_gtk_switches_screens_and_finishes_after_close(close_during_progress):
    if not shutil.which('xvfb-run'):
        pytest.skip('Native GTK verification requires Xvfb')
    probe = subprocess.run(['/usr/bin/python3', '-c', "import gi; gi.require_version('Gtk', '4.0')"], capture_output=True)
    if probe.returncode:
        pytest.skip('Native GTK verification requires system PyGObject and GTK 4')
    script = r'''
import importlib.util
import time
import sys
from pathlib import Path
from gi.repository import GLib
path=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('installer_gtk',path)
ui=importlib.util.module_from_spec(spec);spec.loader.exec_module(ui)
holder={};steps=[];closing=sys.argv[2]=='True'
def walk(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from walk(child);child=child.get_next_sibling()
def tick():
    d=holder.get('dialogs')
    if not d: return True
    if closing and holder.get('progress'):
        d.close(d.window);return False
    if not d.pending: return True
    widgets=list(walk(d.window))
    buttons=[w for w in widgets if isinstance(w,ui.Gtk.Button)]
    for button in buttons:
        if button.get_label() in ('Продолжить','Готово'):
            button.emit('clicked');break
    return True
GLib.timeout_add(100,tick)
def controller(d):
    holder['dialogs']=d
    assert d.choose('Приветствие',[('install','Установить приложение')])=='install'
    steps.append('choose')
    assert d.confirm('Подтверждение')
    steps.append('confirm')
    with d.progress('Копирование'):
        holder['progress']=True
        time.sleep(.35)
    d.info('Завершено')
    steps.append('info')
    d.finished=True
    return 0
assert ui.start(controller,object)==0
assert steps==['choose','confirm','info'],steps
print('PASS')
'''
    result = subprocess.run(['xvfb-run', '-a', '/usr/bin/python3', '-u', '-c', script,
                             str(DESKTOP / 'packaging' / 'installer-gtk.py'), str(close_during_progress)],
                            env={**os.environ, 'GSK_RENDERER': 'cairo'}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS' in result.stdout
