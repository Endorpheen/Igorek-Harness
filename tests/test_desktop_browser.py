import json
import threading

import pytest

from factory.core.desktop_browser import DesktopBrowserPipe
from factory.core.tool_registry import registry


def test_pipe_correlates_only_pending_replies_and_stays_off_without_desktop():
    emitted = []
    client = DesktopBrowserPipe(emitted.append, lambda profile: profile in (None, 'desktop'), timeout=1)
    result = []
    thread = threading.Thread(target=lambda: result.append(json.loads(client.call({'action':'read'}, profile='desktop'))))
    thread.start()
    for _ in range(100):
        if emitted:
            break
        threading.Event().wait(.01)
    assert not client.receive({'type':'browser_reply','id':'foreign','result':{'secret':'canary'}})
    assert client.receive({'type':'browser_reply','id':emitted[0]['id'],'result':{'posts':[{'id':'123'}]}})
    thread.join(2)
    assert result == [{'posts':[{'id':'123'}]}]
    assert 'desktop_browser_access_denied' in client.call({'action':'read'}, profile='other')
    assert 'invalid_request' in client.call({'action':'eval','text':'document.cookie'}, profile='desktop')
    client.close()
    assert not client.available()


def test_lock_during_tool_call_discards_response_and_close_unblocks_waiter():
    allowed = [True]
    emitted = []
    def emit(frame):
        emitted.append(frame)
        allowed[0] = False
        client.receive({'type':'browser_reply','id':frame['id'],'result':{'posts':['private']}})
    client = DesktopBrowserPipe(emit, lambda _: allowed[0], timeout=.01)
    assert json.loads(client.call({'action':'read'})) == {'error':'desktop_browser_access_denied'}
    allowed[0] = True
    client.emit = lambda _: client.close()
    assert 'access_denied' in client.call({'action':'read'})


def test_registration_uses_existing_web_toolset_and_timeout_does_not_retry():
    emitted = []
    client = DesktopBrowserPipe(emitted.append, lambda _: True, timeout=.01)
    try:
        client.install()
        assert registry.get_schema('desktop_browser')['name'] == 'desktop_browser'
        assert 'desktop_browser_timeout' in registry.dispatch('desktop_browser', {'action':'collect'})
        assert len(emitted) == 1
        assert client.pending == {}
    finally:
        client.close()
        registry.deregister('desktop_browser')


@pytest.mark.parametrize('fields',[
    {'action':'click','target':'r-'+'a'*32+'-0'},
    {'action':'fill','target':'r-'+'a'*32+'-0','text':'Python official website'},
    {'action':'press','target':'r-'+'a'*32+'-0','key':'Enter'},
])
def test_search_interactions_cross_the_same_private_pipe(fields):
    def emit(frame):
        client.receive({'type':'browser_reply','id':frame['id'],'result':{'performed':True}})
    client=DesktopBrowserPipe(emit,lambda _:True)
    try:
        assert json.loads(client.call(fields))=={'performed':True}
        assert json.loads(client.call(fields|{'target':'input[type=password]'}))=={'error':'invalid_request'}
    finally:
        client.close()


def test_browser_prompt_uses_native_tool_and_keeps_untrusted_data_boundary():
    from factory.core.prompt_assembly import desktop_browser_read_request, _web_tools_guidance, _mcp_tools_guidance
    assert 'desktop_browser' in desktop_browser_read_request()
    assert 'данные' in desktop_browser_read_request()
    assert 'desktop_browser' in _web_tools_guidance()
    assert 'платный API X' in _web_tools_guidance()
    assert 'desktop_browser' in _mcp_tools_guidance(['xsearch_query'])


@pytest.mark.parametrize('api_format', ['openai', 'anthropic', 'openai-responses'])
def test_standard_executor_calls_native_browser_in_both_transports(tmp_path, monkeypatch, api_format):
    from factory.core import cc_backend_executor as executor
    from factory.core.prompt_assembly import _web_tools_guidance
    calls = []
    def emit(frame):
        pipe.receive({'type':'browser_reply','id':frame['id'],'result':{
            'auth':'ready','posts':[{'id':'123','url':'https://x.com/ai/status/123','text':'Открытые веса модели'}]}})
    pipe = DesktopBrowserPipe(emit, lambda _: True)
    pipe.install()
    def model(url, payload, headers, *args, **kwargs):
        calls.append(payload)
        if api_format == 'openai':
            answer = {'role':'assistant','content':None,'tool_calls':[{'id':'browser','type':'function','function':{
                'name':'desktop_browser','arguments':'{"action":"read"}'}}]} if len(calls) == 1 else {
                    'role':'assistant','content':'Открытые веса: https://x.com/ai/status/123'}
            return {'choices':[{'message':answer,'finish_reason':'tool_calls' if len(calls) == 1 else 'stop'}],
                    'usage':{'prompt_tokens':1,'completion_tokens':1}}, 1
        content = [{'type':'tool_use','id':'browser','name':'desktop_browser','input':{'action':'read'}}] if len(calls) == 1 else [
            {'type':'text','text':'Открытые веса: https://x.com/ai/status/123'}]
        return {'content':content,'stop_reason':'tool_use' if len(calls) == 1 else 'end_turn',
                'usage':{'input_tokens':1,'output_tokens':1}}, 1
    monkeypatch.setattr(executor, '_http_post_streaming_openai', model)
    monkeypatch.setattr(executor, '_http_post', model)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **_: (32768, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    monkeypatch.setattr(executor, 'task_should_stop', lambda _: False)
    try:
        result = executor.execute_cc_backend_task(base_url='http://127.0.0.1:1/v1', model='fixture',
            system_prompt=_web_tools_guidance(), user_message='Прочитай мою AI-ленту.', workdir=str(tmp_path),
            artifact_workdir=str(tmp_path), api_format=api_format, enabled_tools=[registry.get_schema('desktop_browser')],
            checkpoint_config={'enabled':False})
        assert result['status'] == 'ok'
        assert len(calls) == 2 and 'https://x.com/ai/status/123' in json.dumps(calls[1])
    finally:
        pipe.close()
        registry.deregister('desktop_browser')


def test_native_read_button_creates_an_ordinary_session_with_the_existing_worker(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'desktop'))
    from vault_owner import OwnerVault
    owner = OwnerVault(tmp_path)
    owner.service.create('fixture-password')
    created, sent = [], []
    owner.agent = SimpleNamespace(available_profiles=lambda:['demo'],profile='demo',execution=SimpleNamespace(mode='off'),
                                  status=lambda:{'running':True,'profile':'demo'},close=lambda:None)
    owner.browser_service = SimpleNamespace(workdir=str(tmp_path),
        create_session=lambda **fields: created.append(fields) or {'session_id':'chat-fixture'},
        update_session=lambda *_args, **_fields:None,
        send_message=lambda sid, text, **fields: sent.append((sid,text,fields)) or {'task_id':'task-fixture'})
    request = {'id':'x','action':'browser_read_session'}
    try:
        result = owner.dispatch(request)
        assert result['result']['session_id'] == 'chat-fixture'
        assert created[0]['profile'] == 'demo' and created[0]['cwd'] == str(tmp_path)
        assert 'desktop_browser' in sent[0][1]
        owner.interface_locked = True
        assert owner.dispatch(request)['error'] == 'interface_locked'
        assert len(sent) == 1
        owner.interface_locked = False
        owner.agent.execution.mode = 'kvm'
        assert owner.dispatch(request)['error'] == 'desktop_execution_required'
        owner.agent.execution.mode = 'off'
        owner.lifecycle.lock()
        assert owner.dispatch(request)['error'] == 'vault_locked'
        assert len(sent) == 1
    finally:
        owner.close()
