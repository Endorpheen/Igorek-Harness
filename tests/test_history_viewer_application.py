"""Actual pager keyboard/SGR input with an in-memory clipboard, never the desktop clipboard."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from factory.core.tui.mouse_controller import MouseController
from factory.core.tui.mouse_viewer import MouseHistoryViewer, ViewerItem


@pytest.fixture
def pager(monkeypatch):
    copy=Mock(return_value=SimpleNamespace(message='Fixture copied'))
    controller=MouseController(user_enabled=True,clipboard_service=SimpleNamespace(copy=copy))
    focus=Mock(wraps=controller.set_viewer_active);monkeypatch.setattr(controller,'set_viewer_active',focus)
    def run(keys,*,items=None,close=False):
        value=MouseHistoryViewer([f'line {i}' for i in range(60)],items=items,controller=controller)
        monkeypatch.setattr(value,'_terminal_size',lambda:(80,12))
        build=value.build_application
        def factory():
            app=build();app.ttimeoutlen=.01;app.timeoutlen=.01;return app
        monkeypatch.setattr(value,'build_application',factory)
        with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
            if keys:pipe.send_text(keys)
            if close:pipe.close()
            if close:
                with pytest.raises(EOFError):value.run()
            else:value.run()
        assert [call.args[0] for call in focus.call_args_list]==[True,False]
        return value
    return run,controller,copy


@pytest.mark.parametrize('keys,offset',[
    ('jq',1),('jjkq',1),('\x1b[Bq',1),('\x1b[B\x1b[Aq',0),
    ('\x1b[6~q',10),('\x1b[6~\x1b[5~q',0),(' q',10),
    ('\x1b[Fq',49),('\x1b[F\x1b[Hq',0),('\x1b',0),
])
def test_real_keyboard_scrolls_pages_edges_and_restores_controller_focus(pager,keys,offset):
    run,_,copy=pager;value=run(keys)
    assert value.offset==offset and not copy.called


def test_eof_still_releases_viewer_focus(pager):
    run,_,_=pager;run('',close=True)


def test_real_sgr_drag_copies_only_selected_text_to_the_fake_clipboard(pager):
    run,_,copy=pager
    value=run('\x1b[<0;1;1M\x1b[<32;5;1M\x1b[<0;5;1mq')
    copy.assert_called_once_with('line')
    assert value.selection is None and value._notification=='Fixture copied'


def test_real_sgr_click_expands_only_the_selected_snapshot_item(pager):
    run,controller,copy=pager
    first=ViewerItem('one',['header one'],['header one','expanded A'],True)
    second=ViewerItem('two',['header two'],['header two','expanded B'],True)
    original=(list(first.lines_collapsed),list(first.lines_expanded))
    value=run('\x1b[<0;1;1M\x1b[<0;1;1mq',items=[first,second])
    assert controller.is_cell_expanded('one') and not controller.is_cell_expanded('two')
    assert value._build_content()[0]==['header one','expanded A','header two']
    assert original==(first.lines_collapsed,first.lines_expanded) and not copy.called


def test_wheel_and_unrelated_modified_click_preserve_selection_boundaries(pager):
    run,_,copy=pager
    value=run('\x1b[<65;2;2M\x1b[<64;2;2M\x1b[<4;1;1M\x1b[<4;1;1mq')
    assert value.offset==0 and value.selection is None and not copy.called
