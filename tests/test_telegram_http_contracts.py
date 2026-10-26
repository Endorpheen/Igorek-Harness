"""Telegram API contracts use only owned loopback servers and fake tokens."""
import io
import json
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs

import pytest

from factory.core import telegram_bridge as t


@pytest.fixture
def wire():
    state=SimpleNamespace(body={'ok':True,'result':{}},raw=None,status=200,records=[])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=self.rfile.read(int(self.headers.get('Content-Length') or 0));state.records.append((self.path,dict(self.headers),data))
            payload=state.raw if state.raw is not None else json.dumps(state.body).encode()
            self.send_response(state.status);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def do_GET(self):
            payload=state.raw or b'fixture';self.send_response(state.status);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01));thread.start()
    bot=t.TelegramBotApi('fixture-token',timeout_s=3);bot.base_url=f'http://127.0.0.1:{server.server_port}';bot.file_base_url=bot.base_url+'/files'
    try:yield bot,state
    finally:server.shutdown();server.server_close();thread.join(3);assert not thread.is_alive()


@pytest.mark.parametrize('result,expected',[([{'update_id':1}],[{'update_id':1}]),({},[]),(None,[])])
def test_long_poll_uses_offset_zero_and_clamps_timeout(wire,result,expected):
    bot,state=wire;state.body['result']=result
    assert bot.get_updates(offset=0,timeout=0)==expected
    path,headers,raw=state.records[-1];payload=parse_qs(raw.decode())
    assert path=='/getUpdates' and payload=={'offset':['0'],'timeout':['1']}


def test_json_payload_serializes_structured_fields_and_omits_none(wire):
    bot,state=wire
    bot._request_json('fixture',{'text':'Привет & 🐙','markup':{'value':1},'empty':None})
    values=parse_qs(state.records[-1][2].decode())
    assert values['text']==['Привет & 🐙'] and json.loads(values['markup'][0])=={'value':1}
    assert 'empty' not in values


@pytest.mark.parametrize('raw',[b'[]',b'null',b'{"ok":false}',b''])
def test_non_success_response_is_never_presented_as_delivery(wire,raw):
    bot,state=wire;state.raw=raw
    with pytest.raises(RuntimeError,match='Telegram API'):bot._request_json('fixture',{})


def test_http_failure_has_operation_and_status(wire):
    bot,state=wire;state.status=502
    with pytest.raises(RuntimeError,match='fixture failed: HTTP 502'):bot._request_json('fixture',{})


def test_transport_failure_has_explicit_unavailable_error(monkeypatch):
    bot=t.TelegramBotApi('fixture-token')
    monkeypatch.setattr(t.urllib_request,'urlopen',Mock(side_effect=urllib.error.URLError('fixture offline')))
    with pytest.raises(t.TelegramApiUnavailableError,match='unavailable'):bot._request_json('fixture',{})


def test_plain_html_fallback_retains_reply_identity_and_approval_buttons(monkeypatch):
    bot=t.TelegramBotApi('fixture-token');markup={'inline_keyboard':[[{'text':'Fixture approve','callback_data':'owned-request'}]]}
    request=Mock(side_effect=[RuntimeError('fixture HTML rejected'),{'ok':True,'result':{'message_id':2}}]);monkeypatch.setattr(bot,'_request_json',request)
    result=bot._send_html_message_chunk('fixture-chat','<b>One &amp; two</b>',reply_to_message_id=7,reply_markup=markup)
    assert result['ok'] is True and request.call_count==2
    fallback=request.call_args.args[1]
    assert fallback['text']=='One & two' and fallback['reply_to_message_id']==7
    assert fallback['reply_markup']==markup and 'parse_mode' not in fallback


@pytest.mark.parametrize('caption',[None,'Fixture caption 🐙'])
def test_document_multipart_preserves_owned_bytes_and_optional_caption(wire,tmp_path,caption):
    bot,state=wire;path=tmp_path/'fixture.bin';path.write_bytes(b'\x00\xfffixture')
    bot.send_document('fixture-chat',path,caption=caption)
    target,headers,raw=state.records[-1]
    assert target=='/sendDocument' and b'\x00\xfffixture' in raw
    assert 'multipart/form-data' in headers['Content-Type']
    assert (b'name="caption"' in raw) is bool(caption)
    if caption:assert caption.encode() in raw


def test_oversized_attachment_is_rejected_before_network(monkeypatch,tmp_path):
    class Oversized(io.BytesIO):
        def read(self,size=-1):
            assert size==50*1024*1024+1
            return b'x'*size
    monkeypatch.setattr(t,'open_regular_file',lambda _:Oversized())
    network=Mock(side_effect=AssertionError('no upload expected'));monkeypatch.setattr(t.urllib_request,'urlopen',network)
    with pytest.raises(RuntimeError,match='50 MiB'):t.TelegramBotApi('fixture-token').send_document('fixture-chat',tmp_path/'fixture')
    network.assert_not_called()


@pytest.mark.parametrize('status,raw',[(500,b'{}'),(200,b'[]'),(200,b'{"ok":false}')])
def test_document_failure_never_claims_success(wire,tmp_path,status,raw):
    bot,state=wire;state.status=status;state.raw=raw;path=tmp_path/'fixture.txt';path.write_text('fixture')
    with pytest.raises(RuntimeError,match='sendDocument failed'):bot.send_document('fixture-chat',path)


@pytest.mark.parametrize('limit,success',[(3,False),(8,True)])
def test_download_budget_is_enforced_without_contacting_telegram(wire,limit,success):
    bot,state=wire;state.raw=b'12345678'
    if success:assert bot.download_file_bytes('fixture',max_bytes=limit)==b'12345678'
    else:
        with pytest.raises(RuntimeError,match='too large'):bot.download_file_bytes('fixture',max_bytes=limit)


def test_cli_maps_explicit_options_without_starting_polling(monkeypatch,tmp_path):
    run=Mock();monkeypatch.setattr(t,'run_telegram_bridge',run)
    monkeypatch.setattr(sys,'argv',['igorek-telegram','--token','fixture-token','--profile','fixture','--runtime-api-base-url','http://127.0.0.1:1','--runtime-api-key','fixture-key','--allowed-user-ids','1, 2','--allowed-chat-ids','3,4','--workdir',str(tmp_path),'--poll-timeout','5','--auto-approve','--auto-approve-scope','turn'])
    assert t.main()==0
    run.assert_called_once_with(token='fixture-token',profile='fixture',runtime_api_base_url='http://127.0.0.1:1',runtime_api_key='fixture-key',allowed_user_ids={'1','2'},allowed_chat_ids={'3','4'},workdir=str(tmp_path),poll_timeout_s=5,auto_approve=True,auto_approve_scope='turn')


def test_runner_rejects_missing_token_before_constructing_bridge(monkeypatch):
    factory=Mock(side_effect=AssertionError('no bridge expected'));monkeypatch.setattr(t,'TelegramBridge',factory)
    with pytest.raises(ValueError,match='token is required'):t.run_telegram_bridge(token='',profile='fixture',runtime_api_base_url='http://127.0.0.1:1',runtime_api_key='',allowed_user_ids=set())
    factory.assert_not_called()
