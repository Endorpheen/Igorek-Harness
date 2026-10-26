"""Real prompt-toolkit keyboard input and owned-stream EOF behavior."""
import io
import os
from pathlib import Path
import subprocess
import sys

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from factory.core import cli_chat_user_input as ui


def text_question(name='answer', *, secret=False):
    return {'id': name, 'header': 'Input', 'question': 'Provide fixture text', 'is_secret': secret}


def select_question(name='answer', *, multiple=False):
    return {'id':name,'header':'Choice','question':'Choose fixture items',
            'options':[{'label':'First'},{'label':'Second'},{'label':'Third'}], 'is_multiselect':multiple}


@pytest.fixture
def ask(monkeypatch):
    monkeypatch.setattr(ui, '_drain_stdin', lambda:None)
    monkeypatch.setattr(ui.shutil, 'get_terminal_size', lambda fallback:os.terminal_size((96,28)))
    def run(questions, keys, *, close_input=False):
        request = ui.user_input_request_from_payload({'item_id':'fixture-form','questions':questions})
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            if keys:
                pipe.send_text(keys)
            if close_input:
                pipe.close()
            return ui._ask_user_input(Console(file=io.StringIO(),force_terminal=False),request)
    return run


@pytest.mark.parametrize('typed,expected', [
    ('jack','jack'), ('jkjk','jkjk'), ('a b','a b'),
    ('ab\x7fc','ac'), ('a\x08b','b'),
    ('ёжик','ёжик'), ('ready🐙','ready🐙'),
    ('first\x7f\x7f','fir'), ('\x7fhi','hi'),
])
@pytest.mark.parametrize('secret',[False,True],ids=['text','secret'])
def test_real_keyboard_preserves_typed_text_and_backspace(ask,typed,expected,secret):
    result=ask([text_question(secret=secret)],typed+'\x13')
    assert result is not None
    assert result.answers['answer'].text_value==expected


@pytest.mark.parametrize('keys,expected', [
    ('\x1b[B\r\x13',['Second']),
    ('j\r\x13',['Second']),
    ('jjk\r\x13',['Second']),
    ('\x1b[B \x13',['Second']),
])
def test_select_navigation_retains_arrows_vim_keys_and_space(ask,keys,expected):
    result=ask([select_question()],keys)
    assert result is not None and result.answers['answer'].selected_options==expected


def test_multiselect_can_toggle_multiple_choices_and_submit(ask):
    result=ask([select_question(multiple=True)],' \x1b[B \x1b[B\r\x13')
    assert result is not None
    assert result.answers['answer'].selected_options==['First','Second','Third']


def test_tab_and_shift_tab_keep_answers_in_their_own_fields(ask):
    result=ask([text_question('one'),text_question('two')],'abc\tdef\x1b[Zg\x13')
    assert result is not None
    assert result.answers['one'].text_value=='abcg'
    assert result.answers['two'].text_value=='def'


def test_enter_submits_a_text_answer(ask):
    result=ask([text_question()],'fixture\r')
    assert result is not None and result.answers['answer'].text_value=='fixture'


@pytest.mark.parametrize('keys',['\x03','\x1b'])
def test_real_keyboard_cancellation_does_not_return_partial_answer(ask,keys):
    assert ask([text_question()],'partial'+keys) is None


def test_closed_prompt_input_cancels_instead_of_returning_partial_answer(ask):
    assert ask([text_question()],'',close_input=True) is None


def test_empty_request_can_be_submitted_without_fabricated_answers(ask):
    result=ask([],'\x13')
    assert result is not None and result.answers=={}


def test_draining_eof_stops_after_one_read(monkeypatch):
    import select
    reads=[]
    class EndOfInput:
        def read(self, count):
            reads.append(count)
            if len(reads)>3:
                raise OSError('stop original EOF busy-loop fixture')
            return ''
    source=EndOfInput()
    monkeypatch.setattr(sys,'stdin',source)
    monkeypatch.setattr(select,'select',lambda *args:([source],[],[]))
    ui._drain_stdin()
    assert reads==[1]


def test_real_owned_eof_pipe_returns_without_busy_loop(tmp_path):
    repo=Path(__file__).resolve().parents[1]
    env={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL','SYSTEMROOT','WINDIR'}}
    env.update(HOME=str(tmp_path),USERPROFILE=str(tmp_path),PYTHONPATH=str(repo),IGOREK_FACTORY_HOME=str(tmp_path),FACTORY_DATA_DIR=str(tmp_path))
    result=subprocess.run([sys.executable,'-c',
        'from factory.core.cli_chat_user_input import _drain_stdin; _drain_stdin(); print("OWNED_EOF_RETURNED")'],
        stdin=subprocess.DEVNULL,capture_output=True,text=True,env=env,cwd=tmp_path,timeout=3)
    assert result.returncode==0 and result.stdout.strip()=='OWNED_EOF_RETURNED'


@pytest.mark.parametrize('error',[OSError('unselectable input'),ValueError('closed input'),TypeError('no file descriptor')])
def test_unselectable_input_does_not_prevent_prompt_startup(monkeypatch,error):
    import select
    def unavailable(*args):
        raise error
    monkeypatch.setattr(select,'select',unavailable)
    assert ui._drain_stdin() is None


@pytest.mark.parametrize('secret',[False,True])
def test_plain_form_fallback_preserves_text_and_masked_rendering(monkeypatch,secret):
    monkeypatch.setattr(ui,'Application',None)
    monkeypatch.setattr(ui,'_drain_stdin',lambda:None)
    keys=iter(['j','a','c','k',' ','z','backspace','enter'])
    monkeypatch.setattr(ui,'_read_key_plain',lambda:next(keys))
    output=io.StringIO()
    result=ui._ask_user_input(Console(file=output,force_terminal=False),ui.user_input_request_from_payload({'questions':[text_question(secret=secret)]}))
    assert result is not None and result.answers['answer'].text_value=='jack '
    if secret:
        assert 'jack' not in output.getvalue() and '•' in output.getvalue()


@pytest.mark.parametrize('error',[EOFError(),KeyboardInterrupt()])
def test_plain_fallback_cancels_when_its_input_ends(monkeypatch,error):
    monkeypatch.setattr(ui,'Application',None)
    monkeypatch.setattr(ui,'_drain_stdin',lambda:None)
    def stop():
        raise error
    monkeypatch.setattr(ui,'_read_key_plain',stop)
    request=ui.user_input_request_from_payload({'questions':[text_question()]})
    assert ui._ask_user_input(Console(file=io.StringIO()),request) is None


def test_plain_fallback_keeps_selection_navigation(monkeypatch):
    monkeypatch.setattr(ui,'Application',None)
    monkeypatch.setattr(ui,'_drain_stdin',lambda:None)
    keys=iter(['down','enter','c-s'])
    monkeypatch.setattr(ui,'_read_key_plain',lambda:next(keys))
    request=ui.user_input_request_from_payload({'questions':[select_question()]})
    result=ui._ask_user_input(Console(file=io.StringIO()),request)
    assert result is not None and result.answers['answer'].selected_options==['Second']
