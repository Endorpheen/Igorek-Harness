import json
import pytest
from factory.core.desktop_browser import DesktopBrowserPipe

TARGET='r-'+'a'*32+'-0'


@pytest.mark.parametrize('fields', [
    {'action':'click','target':TARGET}, {'action':'hover','target':TARGET},
    {'action':'fill','target':TARGET,'text':'Обычное поле'}, {'action':'press','target':TARGET,'key':'Escape'},
    {'action':'press','target':TARGET,'key':'Space'}, {'action':'select','target':TARGET,'value':'ru'},
    {'action':'check','target':TARGET,'checked':True}, {'action':'scroll_element','target':TARGET,'direction':'down'},
    {'action':'elements','query':'Repost'}, {'action':'click','target':TARGET,'offset_x':.8,'offset_y':.2,'button':'right'},
])
def test_general_contract_reaches_the_same_profile_bound_browser(fields):
    sent=[]
    def emit(frame):
        sent.append(frame)
        pipe.receive({'type':'browser_reply','id':frame['id'],'result':{'performed':True}})
    pipe=DesktopBrowserPipe(emit,lambda profile:profile in {None,'codexpro'})
    assert json.loads(pipe.call(fields,profile='codexpro'))['performed']
    assert sent[0]['fields']==fields
    assert json.loads(pipe.call(fields,profile='other'))['error']=='desktop_browser_access_denied'
    assert len(sent)==1
    pipe.close()


@pytest.mark.parametrize('fields', [None, {'action':[]}, {'action':'click','target':'#like'},
    {'action':'click','target':TARGET,'eval':'document.cookie'},
    {'action':'check','target':TARGET,'checked':'true'},
    {'action':'press','target':TARGET,'key':'Control+L'},
    {'action':'fill','target':TARGET,'text':'x'*16001},
    {'action':'click','target':TARGET,'offset_x':True},
    {'action':'click','target':TARGET,'offset_y':float('nan')},
    {'action':'click','target':TARGET,'button':{}},
    {'action':'scroll_element','target':TARGET,'direction':{}},
])
def test_general_contract_rejects_privileged_or_malformed_inputs(fields):
    pipe=DesktopBrowserPipe(lambda _:pytest.fail('invalid request was sent'),lambda _:True)
    assert json.loads(pipe.call(fields))['error']=='invalid_request'
    pipe.close()


@pytest.mark.parametrize('fields',[
    {'action':'press','key':'Escape'}, {'action':'close_menu'}, {'action':'screenshot'},
    {'action':'wait','condition':'menu','timeout_ms':1500},
    {'action':'read_post','url':'https://x.com/a/status/123','offset':6000,'limit':6000},
    {'action':'comments','url':'https://x.com/a/status/123','limit':5},
    {'action':'elements','url':'https://x.com/a/status/123','query':'like'},
])
def test_extended_reading_contract_preserves_profile_binding(fields):
    def emit(frame):pipe.receive({'type':'browser_reply','id':frame['id'],'result':{'ok':True}})
    pipe=DesktopBrowserPipe(emit,lambda p:p=='codexpro')
    assert json.loads(pipe.call(fields,profile='codexpro'))['ok']
    assert json.loads(pipe.call(fields,profile='other'))['error']=='desktop_browser_access_denied'
    pipe.close()


@pytest.mark.parametrize('fields',[
    {'action':'press','key':'Enter'}, {'action':'wait','condition':{}},
    {'action':'wait','timeout_ms':15001}, {'action':'read_post','url':'https://x.com/a/status/1','offset':-1},
    {'action':'comments','url':'https://x.com/a/status/1','cursor':'file:///secrets'},
    {'action':'screenshot','path':'/etc/passwd'},
])
def test_extended_reading_contract_rejects_unbounded_or_privileged_fields(fields):
    pipe=DesktopBrowserPipe(lambda _:pytest.fail('invalid request sent'),lambda _:True)
    assert json.loads(pipe.call(fields))['error']=='invalid_request'
    pipe.close()


def test_screenshot_image_is_attached_once_only_from_the_private_browser_reply():
    import base64
    from uuid import uuid4
    from factory.core.desktop_browser import take_browser_image
    identity=str(uuid4());encoded=base64.b64encode(b'\xff\xd8\xfffixture').decode()
    def emit(frame):pipe.receive({'type':'browser_reply','id':frame['id'],'result':{'screenshot':{'id':identity},'image_base64':encoded}})
    pipe=DesktopBrowserPipe(emit,lambda _:True)
    result=pipe.call({'action':'screenshot'})
    assert 'image_base64' not in result and encoded not in result
    image=take_browser_image(result)
    assert image['source']['media_type']=='image/jpeg' and image['source']['data']==encoded
    assert take_browser_image(result) is None
    pipe.close()


def test_browser_image_reaches_both_responses_and_chat_vision_without_changing_plain_results():
    from factory.core.openai_responses import build_request
    from factory.core.cc_backend_executor import _anthropic_messages_to_openai
    image={'type':'image','source':{'type':'base64','media_type':'image/jpeg','data':'fixture'}}
    messages=[{'role':'user','content':[{'type':'tool_result','tool_use_id':'call-fixture','content':'screenshot metadata'},image]}]
    responses=build_request({'model':'fixture','messages':messages})
    assert any(p.get('image_url')=='data:image/jpeg;base64,fixture' for item in responses['input'] for p in item.get('content',[]) if isinstance(p,dict))
    chat=_anthropic_messages_to_openai(messages)
    assert chat[0]=={'role':'tool','tool_call_id':'call-fixture','content':'screenshot metadata'}
    assert chat[1]['content'][0]['image_url']['url']=='data:image/jpeg;base64,fixture'
    plain=_anthropic_messages_to_openai([{'role':'user','content':messages[0]['content'][:1]}])
    assert plain==[chat[0]]
