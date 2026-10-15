import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from factory.core.desktop_browser import DesktopBrowserPipe
from factory.core.desktop_browser_preview import DesktopBrowserPreview


def frame():
    image=b'\xff\xd8\xfffixture-jpeg'
    return {'ready':True,'frame_id':hashlib.sha256(image).hexdigest(),'image_base64':base64.b64encode(image).decode(),
        'width':640,'height':480,'captured_at':123456789,'url':'https://example.com/article?secret=canary#token',
        'title':'Example','loading':False,'private':'do-not-forward'}


def test_preview_pipe_keeps_frame_out_of_agent_tools_and_image_store():
    def emit(request):
        assert request['fields']=={'action':'preview'}
        pipe.receive({'type':'browser_reply','id':request['id'],'result':frame()})
    pipe=DesktopBrowserPipe(emit,lambda _:True,timeout=.1)
    try:
        assert json.loads(pipe.call({'action':'preview'}))['error']=='invalid_request'
        result=DesktopBrowserPreview(pipe).read()
        assert result['ready'] and result['image_base64']==frame()['image_base64']
        assert result['url']=='https://example.com/article' and 'private' not in result
    finally:pipe.close()


def test_preview_cache_deduplicates_requests_and_clears_on_lock():
    calls=[];allowed=[True];clock=[1.0]
    pipe=SimpleNamespace(available=lambda:allowed[0],request=lambda fields,timeout:calls.append(fields) or json.dumps(frame()))
    preview=DesktopBrowserPreview(pipe,clock=lambda:clock[0])
    first=preview.read();second=preview.read(first['frame_id'])
    assert len(calls)==1 and second['unchanged'] is True and 'image_base64' not in second
    clock[0]=3.1;assert preview.read()['ready'] and len(calls)==2
    allowed[0]=False;assert not preview.read()['ready'] and preview.cached is None


def test_lock_during_capture_discards_image_and_busy_does_not_create_parallel_capture():
    allowed=[True]
    def request(fields,timeout):allowed[0]=False;return json.dumps(frame())
    pipe=SimpleNamespace(available=lambda:allowed[0],request=request)
    preview=DesktopBrowserPreview(pipe)
    assert preview.read()['reason']=='desktop_unavailable' and preview.cached is None
    allowed[0]=True;preview.lock.acquire()
    try:assert preview.read()['reason']=='busy'
    finally:preview.lock.release()


@pytest.mark.parametrize('change',[{'image_base64':'bad'}, {'width':961}, {'height':True}, {'captured_at':0},
    {'frame_id':'f'*64}, {'image_base64':'a'*426669}, {'ready':True,'reason':'manual_control','image_base64':''}])
def test_invalid_frames_are_never_returned(change):
    value=DesktopBrowserPreview.normalize({**frame(),**change})
    assert not value['ready'] and 'image_base64' not in value


def test_manual_preview_has_no_image_or_private_page_metadata():
    value=DesktopBrowserPreview.normalize({'reason':'manual_control','image_base64':frame()['image_base64'],'url':'https://example.com/private'})
    assert value=={'available':True,'ready':False,'reason':'manual_control'}


def test_tab_metadata_is_bounded_and_locked_state_has_no_tab_data():
    raw={**frame(),'tabs':[{'id':'one','title':'Page','url':'https://example.com/a?secret=canary#token'}, {'id':'../bad','title':'Invalid'}, {'id':'one','title':'Duplicate'}], 'active_tab_id':'one'}
    value=DesktopBrowserPreview.normalize(raw)
    assert value['tabs']==[{'id':'one','title':'Page','url':'https://example.com/a'}]
    assert value['active_tab_id']=='one'
    assert DesktopBrowserPreview.normalize({'reason':'desktop_locked','tabs':raw['tabs'],'active_tab_id':'one'})=={'available':True,'ready':False,'reason':'desktop_locked'}
    assert len(DesktopBrowserPreview.tabs({'tabs':[{'id':f'tab-{i}','title':'x'*200,'url':'https://example.com/'+'x'*2000} for i in range(30)]})['tabs'])==24
    assert 'canary' not in json.dumps(value)


def test_select_tab_sends_only_id_and_invalidates_cached_frame():
    calls=[]
    def request(fields,timeout):
        calls.append(fields)
        return json.dumps({'selected':True,'tab_id':'two','private':'ignored'}) if fields['action']=='select_preview_tab' else json.dumps(frame())
    pipe=SimpleNamespace(available=lambda:True,request=request)
    preview=DesktopBrowserPreview(pipe)
    assert preview.read()['ready']
    assert preview.select('two')=={'selected':True,'tab_id':'two'}
    assert preview.cached is None
    assert preview.read()['ready']
    assert calls==[{'action':'preview'},{'action':'select_preview_tab','tab_id':'two'},{'action':'preview'}]
    with pytest.raises(ValueError):preview.select('../bad')


def test_select_tab_refuses_locked_busy_and_disconnected_reply():
    allowed=[True]
    def request(fields,timeout):allowed[0]=False;return json.dumps({'selected':True,'tab_id':'one'})
    preview=DesktopBrowserPreview(SimpleNamespace(available=lambda:allowed[0],request=request))
    preview.lock.acquire()
    try:assert preview.select('one')['reason']=='busy'
    finally:preview.lock.release()
    assert preview.select('one')['reason']=='desktop_unavailable'
    assert preview.select('one')['reason']=='desktop_unavailable'
