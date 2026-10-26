"""Actual prompt-toolkit input and frame mappings, with synthetic clipboard/tasks."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from factory.core import cli_chat_active as a, cli_chat_idle as idle
from factory.core import cli_chat_rich_shared as shared
from factory.core.cli_chat_user_input import user_input_request_from_payload
from factory.core.tui.bottom_pane.request_user_input_form import RequestUserInputForm
from factory.core.tui.mouse_controller import MouseController


@pytest.fixture
def app_runner(monkeypatch,tmp_path):
    monkeypatch.setenv('HOME',str(tmp_path))
    monkeypatch.setattr(idle,'_make_idle_completer',lambda:None)
    monkeypatch.setattr(a,'create_output',lambda:DummyOutput())
    cancel = Mock()
    monkeypatch.setattr(a,'cancel_task',cancel)
    controller=MouseController(user_enabled=True,clipboard_service=SimpleNamespace(copy=Mock()))
    def run(keys,*,state=None,**kwargs):
        state={} if state is None else state
        frames=[]
        with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
            app,buffer,lines=a._create_chat_application(running_state=state,mouse_controller=controller,**kwargs)
            app.ttimeoutlen=.01
            app.timeoutlen=.01
            app.after_render += lambda _:frames.append(controller.current_frame())
            pipe.send_text(keys)
            result=app.run()
        controller.clear_app()
        return SimpleNamespace(result=result,buffer=buffer,state=state,frames=frames,cancel=cancel,controller=controller)
    return run


@pytest.mark.parametrize('keys,expected',[('  hello  \r','hello'),('line1\nline2\r','line1\nline2'),('why?\r','why?'),('\x14','__transcript__')])
def test_submit_preserves_user_text_and_clears_buffer(app_runner,keys,expected):
    result=app_runner(keys)
    assert result.result==expected
    assert result.state['submitted_text']==expected and result.buffer.text==''


@pytest.mark.parametrize('keys,visible',[('?\x03',True),('??\x03',False),('?\x1b\x03',False)])
def test_shortcut_overlay_does_not_become_submitted_text(app_runner,keys,visible):
    result=app_runner(keys)
    assert result.state.get('show_shortcuts',False) is visible
    assert 'submitted_text' not in result.state


def test_scroll_keys_route_to_transcript_without_changing_composer(app_runner):
    scroll=Mock();edge=Mock()
    result=app_runner('\x1b[5~\x1b[6~\x1b[1;5H\x1b[1;5F\x03',fullscreen=True,on_scroll=scroll,on_scroll_edge=edge,transcript_provider=lambda:['one'])
    assert [call.args for call in scroll.call_args_list]==[(5,),(-5,)]
    assert [call.args for call in edge.call_args_list]==[('top',),('bottom',)]
    assert result.buffer.text==''


def test_reasoning_toggle_clears_only_per_cell_overrides(app_runner):
    state={'reasoning_state':{'visible':False,'lines':['fixture thought'],'cell_overrides':{'fixture':True}}}
    result=app_runner('\x0f\x03',state=state)
    assert result.state['reasoning_state']=={'visible':True,'lines':['fixture thought'],'cell_overrides':{}}


@pytest.mark.parametrize('with_id',[True,False])
def test_persistent_interrupt_records_cancellation_before_exit(app_runner,with_id):
    state={'task_active':True,'exit_on_interrupt':True}
    if with_id:state['current_task_id']='owned-fixture-task'
    result=app_runner('\x03',state=state,persistent=True)
    assert result.result=='__INTERRUPT__' and state['interrupted'] and state['exit_requested']
    if with_id:
        result.cancel.assert_called_once_with('owned-fixture-task')
    else:
        assert state['interrupt_pending'] is True
        result.cancel.assert_not_called()


def test_persistent_submit_dispatches_once_and_keeps_input_loop_alive(app_runner):
    received=[]
    result=app_runner('one\rsecond\r\x03',persistent=True,on_submit=received.append)
    assert received==['one','second'] and result.buffer.text==''


@pytest.mark.parametrize('keys',['\x1b','\x03'])
def test_form_cancel_is_separate_from_task_cancellation(app_runner,keys):
    request=user_input_request_from_payload({'questions':[{'id':'name','header':'Name','question':'Fixture?'}]})
    form=RequestUserInputForm(request)
    state={'user_input_form':{'request_id':'owned-request','form':form}}
    result=app_runner(keys,state=state)
    assert result.result is None and form.cancelled
    assert state['user_input_form_result']=={'request_id':'owned-request','response':None,'cancelled':True}
    result.cancel.assert_not_called()


@pytest.mark.parametrize('submit_key',['\r','\x13'])
def test_form_submission_serializes_answer_for_exact_request(app_runner,submit_key):
    request=user_input_request_from_payload({'questions':[{'id':'name','header':'Name','question':'Fixture?'}]})
    form=RequestUserInputForm(request)
    form.answers['name'].text_value='fixture answer'
    state={'user_input_form':{'request_id':'owned-request','form':form}}
    result=app_runner(submit_key,state=state)
    value=state['user_input_form_result']
    assert value['request_id']=='owned-request' and value['cancelled'] is False
    assert value['response']['answers']['name']['text_value']=='fixture answer'
    assert result.result.answers['name'].text_value=='fixture answer'


def test_fullscreen_mouse_regions_match_transcript_spans_and_scroll_window(app_runner):
    result=app_runner('\x03',fullscreen=True,transcript_provider=lambda:['one','two','three'],transcript_regions_provider=lambda:[{'start':1,'end':2,'kind':'reasoning','cell_id':'reasoning-fixture','expandable':True}],transcript_full_provider=lambda:['old','one','two','three'],transcript_window_start_provider=lambda:1)
    frame=next(frame for frame in result.frames if frame.rows)
    region=frame.regions['span:1:reasoning']
    assert (region.top,region.bottom,region.cell_id,region.expandable)==(1,2,'reasoning-fixture',True)
    assert frame.window_start==1 and frame.full_lines==['old','one','two','three']
    assert frame.composer_top>region.bottom


def test_plain_clipboard_paste_does_not_invoke_image_capture(app_runner,monkeypatch):
    monkeypatch.setattr(shared,'_clipboard_image_path',lambda:None)
    monkeypatch.setattr(shared,'_clipboard_text',lambda:'pasted fixture')
    result=app_runner('\x16\r')
    assert result.result=='pasted fixture'


def test_image_clipboard_paste_adds_attachment_path_without_reading_file(app_runner,monkeypatch):
    monkeypatch.setattr(shared,'_clipboard_image_path',lambda:'/tmp/fixture-image.png')
    monkeypatch.setattr(shared,'_clipboard_text',Mock(side_effect=AssertionError('unexpected text lookup')))
    result=app_runner('draw\x16\r')
    assert result.result=='draw 📎 /tmp/fixture-image.png'
