"""Terminal tickets validate bridge context before granting any capability."""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_web_ui_proxy_routes import servers,proxy  # noqa: F401
from test_web_transfer_failures import Reply
from factory.web_ui import server as web
from factory.core.isolation.terminal_manager import TerminalError


@pytest.mark.parametrize('payload',[[],None,True,4,'fixture',{'isolation':[]},{'isolation':{}},{'isolation':'kvm','meta':['bad']},{'isolation':'kvm','meta':'bad'},{'isolation':'kvm','meta':4}],ids=['array','null','bool','number','string','list-mode','object-mode','list-meta','string-meta','number-meta'])
def test_malformed_bridge_context_is_typed_refusal_not_connection_drop(proxy,monkeypatch,payload):
    monkeypatch.setattr(web.urllib.request,'urlopen',Mock(return_value=Reply(json.dumps(payload).encode())))
    backend=Mock(side_effect=AssertionError('terminal backend must not open'));monkeypatch.setattr(proxy.app.terminals,'_backend_factory',backend)
    status,_,data=proxy.request('POST','/api/sessions/fixture/terminal',body={})
    assert status==400 and 'Не удалось выдать билет' in json.loads(data)['error']
    backend.assert_not_called()


@pytest.mark.parametrize('mode',['none','kvm'])
def test_valid_ticket_is_session_device_bound_and_single_use_without_opening_shell(proxy,monkeypatch,tmp_path,mode):
    monkeypatch.setenv('FACTORY_WEB_HOST_TERMINAL','1')
    monkeypatch.setattr(web.urllib.request,'urlopen',Mock(return_value=Reply(json.dumps({'isolation':mode,'meta':{'cwd':str(tmp_path)}}).encode())))
    backend=Mock(side_effect=AssertionError('ticket does not open a shell'));monkeypatch.setattr(proxy.app.terminals,'_backend_factory',backend)
    status,_,data=proxy.request('POST','/api/sessions/fixture/terminal',body={})
    result=json.loads(data);assert status==200 and result['execution_mode']==mode and result['ttl_s']>0
    record=proxy.app.terminals.consume_ticket(result['ticket'],'fixture')
    assert record.session_id=='fixture' and record.device_identity==hashlib.sha256(b'fixture-token').hexdigest()
    with pytest.raises(TerminalError):proxy.app.terminals.consume_ticket(result['ticket'],'fixture')
    backend.assert_not_called()


@pytest.mark.parametrize('payload,reason',[({'isolation':'kvm-unavailable'},'kvm_terminal_unavailable'),({'isolation':'unknown'},'session_mode_unknown'),({'isolation':'none'},'host_terminal_disabled'),({'isolation':'kvm','meta':{'cwd':'relative'}},'workdir_missing')])
def test_disallowed_execution_context_never_mints_ticket(proxy,monkeypatch,payload,reason):
    monkeypatch.delenv('FACTORY_WEB_HOST_TERMINAL',raising=False)
    monkeypatch.setattr(web.urllib.request,'urlopen',Mock(return_value=Reply(json.dumps(payload).encode())))
    issue=Mock(side_effect=AssertionError('no ticket expected'));monkeypatch.setattr(proxy.app.terminals,'issue_ticket',issue)
    status,_,data=proxy.request('POST','/api/sessions/fixture/terminal',body={})
    assert status==400 and reason in json.loads(data)['error'];issue.assert_not_called()


def test_unpaired_ticket_request_never_contacts_bridge(proxy,monkeypatch):
    upstream=Mock(side_effect=AssertionError('no upstream expected'));monkeypatch.setattr(web.urllib.request,'urlopen',upstream)
    status,_,data=proxy.request('POST','/api/sessions/fixture/terminal',body={},paired=False)
    assert status==401;upstream.assert_not_called()


@pytest.mark.parametrize('args',[{'port':0},{'port':65536},{'port':'bad'},{'port':12345,'tls_cert':'fixture-cert','tls_key':''},{'port':12345,'bridge_url':'ftp://fixture'}])
def test_server_validation_failure_does_not_bind_port(monkeypatch,tmp_path,args):
    monkeypatch.setenv('FACTORY_DATA_DIR',str(tmp_path));factory=Mock(side_effect=AssertionError('no server expected'));monkeypatch.setattr(web,'_Server',factory)
    assert web.run_server(SimpleNamespace(**args))==2
    factory.assert_not_called()


@pytest.mark.parametrize('failure',['bind','tls','stop'])
def test_server_startup_failure_closes_owned_server_and_never_runs_user_service(monkeypatch,tmp_path,failure):
    server=Mock();server.serve_forever.side_effect=KeyboardInterrupt
    factory=Mock(side_effect=OSError('fixture bind') if failure=='bind' else None,return_value=server)
    monkeypatch.setattr(web,'_Server',factory)
    context=Mock()
    if failure=='tls':context.load_cert_chain.side_effect=OSError('fixture certificate')
    monkeypatch.setattr(web.ssl,'SSLContext',Mock(return_value=context))
    args=SimpleNamespace(port=12345,bridge_url='http://127.0.0.1:1',tls_cert=str(tmp_path/'cert'),tls_key=str(tmp_path/'key'))
    assert web.run_server(args)==(0 if failure=='stop' else 2)
    if failure!='bind':server.server_close.assert_called_once()
    if failure!='stop':server.serve_forever.assert_not_called()
    else:
        assert context.minimum_version==web.ssl.TLSVersion.TLSv1_2
        server.enable_tls.assert_called_once_with(context)
