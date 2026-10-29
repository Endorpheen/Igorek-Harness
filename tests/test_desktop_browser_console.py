import json

import pytest

from factory.core.desktop_browser import DesktopBrowserPipe, SCHEMA


@pytest.mark.parametrize('fields', [
    {'action': 'console', 'expression': 'document.title', 'timeout_ms': 5000},
    {'action': 'console_messages', 'after': 42, 'limit': 200, 'level': 'error'},
    {'action': 'console_clear'},
])
def test_console_actions_round_trip_through_the_private_browser_pipe(fields):
    emitted = []
    def emit(frame):
        emitted.append(frame)
        pipe.receive({'type': 'browser_reply', 'id': frame['id'], 'result': {'value': 'fixture'}})
    pipe = DesktopBrowserPipe(emit, lambda profile: profile == 'fixture')
    try:
        assert json.loads(pipe.call(fields, profile='fixture')) == {'value': 'fixture'}
        assert emitted[0]['fields'] == fields
        assert json.loads(pipe.call(fields, profile='foreign'))['error'] == 'desktop_browser_access_denied'
        assert len(emitted) == 1
    finally:
        pipe.close()


@pytest.mark.parametrize('fields', [
    {'action': 'console', 'expression': ' '},
    {'action': 'console', 'expression': 'x' * 16001},
    {'action': 'console', 'expression': '1', 'timeout_ms': 5001},
    {'action': 'console', 'expression': '1', 'timeout_ms': True},
    {'action': 'console', 'expression': '1', 'url': 'https://example.com'},
    {'action': 'console_messages', 'after': True},
    {'action': 'console_messages', 'after': 9007199254740992},
    {'action': 'console_messages', 'limit': 201},
    {'action': 'console_messages', 'level': 'verbose'},
    {'action': 'console_clear', 'expression': '1'},
])
def test_invalid_console_arguments_never_reach_desktop(fields):
    emitted = []
    pipe = DesktopBrowserPipe(emitted.append, lambda _: True)
    try:
        assert json.loads(pipe.call(fields))['error'] == 'invalid_request'
        assert emitted == []
    finally:
        pipe.close()


def test_console_actions_are_available_in_the_native_tool_schema():
    assert {'console', 'console_messages', 'console_clear'} <= set(SCHEMA['input_schema']['properties']['action']['enum'])
