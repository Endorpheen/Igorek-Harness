"""Independent boundary acceptance for the three scoped helper components."""
import importlib.util
import io
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from factory.core.tui import custom_terminal as terminal_module
from factory.core.tui.bottom_pane.list_selection_view import ListSelectionView, SelectionItem
from factory.core.tui.bottom_pane.multi_select_picker import MultiSelectPicker, MultiSelectItem


@pytest.mark.parametrize('frames', [
    [['one', 'two', 'three'], ['next'], []],
    [['a', 'b', 'c', 'invisible'], ['a', 'b'], ['x', 'b', 'z']],
    [[], ['one'], ['one'], [], []],
])
def test_owned_terminal_frame_sequence_has_no_ghost_rows(monkeypatch, frames):
    output = io.StringIO()
    monkeypatch.setattr(terminal_module, 'sys', SimpleNamespace(stdout=output))
    monkeypatch.setattr(terminal_module, 'shutil', SimpleNamespace(get_terminal_size=lambda fallback: SimpleNamespace(lines=20, columns=80)))
    terminal = terminal_module.CustomTerminal()
    terminal.set_viewport_area(terminal_module.ViewportArea(x=4, y=7, width=30, height=3))
    screen = {row: 'old screen' for row in range(7, 10)}
    for frame in frames:
        output.seek(0)
        output.truncate(0)
        terminal.draw(lambda area, buffer: [buffer.append(line) for line in frame])
        rendered = output.getvalue()
        for row, column, content in re.findall(r'\x1b\[(\d+);(\d+)H\x1b\[2K([^\x1b]*)', rendered):
            assert 7 <= int(row) < 10 and int(column) == 4
            screen[int(row)] = content
        assert [screen[row] for row in range(7, 10)] == (frame[:3] + [''] * 3)[:3]


@pytest.mark.parametrize('kind', ['single', 'multi'])
def test_new_items_reset_navigation_without_mutating_selection_flags(kind):
    if kind == 'single':
        view = ListSelectionView('Pick')
        make = lambda n, checked: SelectionItem(n, selected=checked)
    else:
        view = MultiSelectPicker('Pick')
        make = lambda n, checked: MultiSelectItem(n, n, selected=checked)
    view.set_items([make(str(i), False) for i in range(20)])
    view.scroll.selected, view.scroll.offset = 19, 12
    new_items = [make('first', True), make('second', False)]
    view.set_items(new_items)
    assert view.scroll.selected == view.scroll.offset == 0
    assert view.scroll.total == 2
    assert [x.selected for x in new_items] == [True, False]
    before = [(vars(x).copy()) for x in new_items]
    assert any('first' in line and '►' in line for line in view.render_lines())
    assert [vars(x) for x in new_items] == before
    view.set_items([])
    assert view.scroll.selected == view.scroll.offset == view.scroll.total == 0
    if kind == 'single':
        assert view.selected_item() is None
    else:
        view.toggle_current()
        assert view.selected_keys() == []


@pytest.fixture
def client_module(monkeypatch):
    path = Path(__file__).resolve()
    repository = Path.cwd()
    assert (repository / 'desktop/opencode-host-services-client.py').is_file(), path
    spec = importlib.util.spec_from_file_location('fixture_host_services_client_acceptance', repository / 'desktop/opencode-host-services-client.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'Path', lambda _: SimpleNamespace(read_text=lambda: 'owned-fixture-token'))
    return module


def exercise(module, monkeypatch, capsys, lines, replies):
    requests, closes, read_limits = [], [], []
    queue = iter(replies)
    class FakeConnection:
        def __init__(self, host, timeout):
            assert (host, timeout) == ('localhost', 100)
            self.reply = next(queue)
        def request(self, method, route, body, headers):
            requests.append((method, route, body, headers))
        def getresponse(self):
            status, raw = self.reply
            def read(limit):
                read_limits.append(limit)
                return raw
            return SimpleNamespace(status=status, read=read)
        def close(self):
            closes.append(self)
    monkeypatch.setattr(module, 'Connection', FakeConnection)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(stdin=io.StringIO(''.join(lines))))
    module.main()
    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return printed, requests, closes, read_limits


@pytest.mark.parametrize('size', [65536, 65537])
def test_request_character_limit_includes_newline(client_module, monkeypatch, capsys, size):
    base = json.dumps({'jsonrpc': '2.0', 'id': 0, 'method': 'tools/list'})
    line = base + ' ' * (size - len(base) - 1) + '\n'
    result, requests, closed, limits = exercise(client_module, monkeypatch, capsys, [line], [(200, b'{"id":0,"result":{}}')])
    assert len(line) == size
    if size == 65536:
        assert result == [{'id': 0, 'result': {}}]
    else:
        assert result == []
    assert len(requests) == len(closed) == (1 if size == 65536 else 0)
    assert limits == ([2 * 1024 * 1024 + 1] if size == 65536 else [])


@pytest.mark.parametrize('size', [2 * 1024 * 1024, 2 * 1024 * 1024 + 1])
def test_response_exact_byte_boundary(client_module, monkeypatch, capsys, size):
    base = b'{"id":7,"result":{}}'
    raw = base + b' ' * (size - len(base))
    lines = ['{"jsonrpc":"2.0","id":7,"method":"tools/list"}\n']
    result, requests, closed, limits = exercise(client_module, monkeypatch, capsys, lines, [(200, raw)])
    assert len(result) == len(requests) == len(closed) == 1
    assert result[0]['id'] == 7 and limits == [2 * 1024 * 1024 + 1]
    if size == 2 * 1024 * 1024:
        assert result == [{'id': 7, 'result': {}}]
    else:
        assert result[0]['error']['code'] == -32000
        assert 'owned-fixture-token' not in json.dumps(result)


def test_notification_followed_by_id_zero_preserves_request_order(client_module, monkeypatch, capsys):
    lines = ['{"jsonrpc":"2.0","method":"initialized"}\n', '{"jsonrpc":"2.0","id":0,"method":"tools/list"}\n']
    result, requests, closed, limits = exercise(client_module, monkeypatch, capsys, lines, [(202, b''), (200, b'{"id":0,"result":[]}')])
    assert result == [{'id': 0, 'result': []}]
    assert len(requests) == len(closed) == len(limits) == 2
    assert [json.loads(req[2])['method'] for req in requests] == ['initialized', 'tools/list']
    assert all(req[3]['Authorization'] == 'Bearer owned-fixture-token' for req in requests)


def test_selected_key_order_preserves_checked_input_order():
    view = MultiSelectPicker('Owned selection')
    view.set_items([MultiSelectItem('z', 'Z', True), MultiSelectItem('a', 'A', True), MultiSelectItem('m', 'M', False)])
    assert view.selected_keys() == ['z', 'a']
    view.toggle_current()
    assert view.selected_keys() == ['a']
    view.toggle_current()
    before = [(item.key, item.selected) for item in view.items]
    view.render_lines()
    assert view.selected_keys() == ['z', 'a']
    assert [(item.key, item.selected) for item in view.items] == before


def test_single_codepoint_input_operations_are_safe_at_both_edges():
    from factory.core.tui.bottom_pane.textarea import TextArea
    view = TextArea()
    view.delete_before()
    view.move_left()
    view.move_right()
    assert (view.text, view.cursor) == ('', 0)
    view.insert('🐙')
    view.move_right()
    assert (view.text, view.cursor) == ('🐙', 1)
    view.move_left()
    view.delete_before()
    assert (view.text, view.cursor) == ('🐙', 0)
    view.move_right()
    view.delete_before()
    assert (view.text, view.cursor) == ('', 0)
