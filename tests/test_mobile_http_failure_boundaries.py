"""Owned mobile HTTP/TLS requests verify failure detail and capability boundaries."""
import http.client
import json
import ssl
import threading
from unittest.mock import Mock

import pytest

from test_mobile_delivery_file_boundaries import mobile  # noqa: F401
from factory.core.mobile_bridge import server as s
from factory.web_ui.server import _certificate_paths
from factory.core.opencode_quota import OpenCodeUsageError


@pytest.fixture
def mobile_http(mobile):
    f=mobile;resources=[]
    def start(tls=False):
        cert,key,_=_certificate_paths() if tls else ('','','')
        bridge=s.MobileBridge(config=s.BridgeConfig(profile='fixture',tls_cert=str(cert),tls_key=str(key)),store=f.store,service=f.service)
        server=s.build_server(bridge,host='127.0.0.1',port=0)
        thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01));thread.start();resources.append((server,thread))
        context=ssl.create_default_context(cafile=str(cert)) if tls else None
        def request(method,path,body=None,raw=None,headers=None,paired=True):
            connection=http.client.HTTPSConnection('127.0.0.1',server.server_port,timeout=4,context=context) if tls else http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
            request_headers={'Content-Type':'application/json'}
            if paired and token:request_headers['Authorization']='Bearer '+token
            request_headers.update(headers or {})
            if body is not None:raw=json.dumps(body).encode()
            try:
                connection.request(method,path,body=raw,headers=request_headers)
                response=connection.getresponse();data=response.read()
                return response.status,json.loads(data) if data else {},dict(response.headers)
            finally:connection.close()
        token=''
        code=f.store.create_pairing_code()['code']
        status,result,_=request('POST','/mobile/v1/pair/complete',body={'code':code,'name':'owned-fixture','platform':'test'},paired=False)
        assert status==201;token=result['token']
        return request,bridge
    yield start,f
    for server,thread in resources:server.shutdown();server.server_close();thread.join(3);assert not thread.is_alive()


@pytest.mark.parametrize('path',['/mobile/v1/operations/short','/mobile/v1/operations/valid-reference?extra=1','/mobile/v1/provider-usage','/mobile/v1/provider-usage?profile='+('x'*129)])
def test_invalid_operation_or_profile_reference_has_explicit_client_failure(mobile_http,path):
    start,f=mobile_http;request,_=start();status,result,_=request('GET',path)
    assert status==400 and result['error']
    assert f.tasks()==[]


def test_non_api_route_is_not_exposed_as_valid_bridge_request(mobile_http):
    start,_=mobile_http;request,_=start();status,result,_=request('GET','/unrelated')
    assert status==404 and result['error']=='not found'


@pytest.mark.parametrize('method,path,status',[('GET','/mobile/v1/schedules',200),('POST','/mobile/v1/schedules',409),('DELETE','/mobile/v1/schedules/fixture',409)])
def test_unattached_scheduler_has_truthful_availability_without_starting_one(mobile_http,method,path,status):
    start,f=mobile_http;request,_=start();actual,result,_=request(method,path,body={} if method=='POST' else None)
    assert actual==status
    if status==200:assert result=={'available':False,'running':False,'schedules':[]}
    assert f.tasks()==[]


@pytest.mark.parametrize('error,status',[(s.MobileInvalid('fixture invalid'),400),(s.MobileNotFound('fixture missing'),404),(RuntimeError('fixture-private-internal'),500)])
def test_scheduler_error_retains_expected_status_and_hides_internal_details(mobile_http,monkeypatch,error,status):
    start,f=mobile_http;controller=Mock();controller.list.side_effect=error;monkeypatch.setattr(f.service,'schedules',controller,raising=False)
    request,_=start();actual,result,_=request('GET','/mobile/v1/schedules')
    assert actual==status
    if status==500:assert 'fixture-private-internal' not in json.dumps(result)


@pytest.mark.parametrize('raw,headers',[ (b'[]',{}),(b'{',{}),(b'\xff',{}),(b'{}',{'Content-Length':str(s.MAX_BODY_BYTES+1)}) ])
def test_invalid_json_body_cannot_reach_profile_controller(mobile_http,monkeypatch,raw,headers):
    start,f=mobile_http;controller=Mock();monkeypatch.setattr(f.service,'browser_preview',controller,raising=False);request,_=start()
    status,result,_=request('POST','/mobile/v1/browser/select',raw=raw,headers=headers)
    assert status==400 and result['error']
    controller.select.assert_not_called()


def test_browser_selection_without_desktop_provider_is_clear_unavailable(mobile_http):
    start,_=mobile_http;request,_=start();status,result,_=request('POST','/mobile/v1/browser/select',body={'tab_id':'fixture-tab'})
    assert status==404 and result['error']=='browser tab selection unavailable'


@pytest.mark.parametrize('path,method,body,callback,expected_args,expected_kwargs',[
    ('/mobile/v1/sessions/owned-session/agents','GET',None,'agent_sessions',('owned-session',),{}),
    ('/mobile/v1/sessions/owned-session/subagents?limit=4','GET',None,'list_subagents',('owned-session',),{'limit':4}),
    ('/mobile/v1/sessions/owned-session/subagents/child-one/cancel','POST',{},'cancel_subagent',('owned-session','child-one'),{}),
    ('/mobile/v1/sessions/owned-session/messages/message-one/send-now','POST',{'task_id':'owned-task'},'send_message_now',('owned-session','owned-task','message-one'),{}),
    ('/mobile/v1/sessions/owned-session/messages/message-one/cancel','POST',{'task_id':'owned-task'},'cancel_message',('owned-session','owned-task','message-one'),{}),
])
def test_session_control_route_preserves_exact_session_task_and_message_ids(mobile_http,monkeypatch,path,method,body,callback,expected_args,expected_kwargs):
    start,f=mobile_http;operation=Mock(return_value={'fixture':True});monkeypatch.setattr(f.service,callback,operation);request,_=start()
    status,result,_=request(method,path,body=body)
    assert status==200 and result=={'fixture':True}
    operation.assert_called_once_with(*expected_args,**expected_kwargs)
    assert f.tasks()==[]


def test_cursor_is_derived_from_shared_owned_event_journal(mobile_http):
    start,f=mobile_http
    row=f.store.append_event(kind='fixture.event',session_id=f.session,payload={'fixture':True})
    request,_=start();status,result,_=request('GET','/mobile/v1/sessions/'+f.session+'/cursor')
    assert status==200 and result['seq']==f.store.max_seq(session_id=f.session)>0


@pytest.mark.parametrize('invalid_limit',['not-a-number','[]'])
def test_subagent_limit_parse_failure_is_a_client_error(mobile_http,invalid_limit):
    start,_=mobile_http;request,_=start();status,result,_=request('GET','/mobile/v1/sessions/owned-session/subagents?limit='+invalid_limit)
    assert status==400 and result['error']


def test_provider_usage_fallback_is_typed_without_opening_real_account(mobile_http,monkeypatch):
    start,f=mobile_http;quota=Mock(side_effect=OpenCodeUsageError('fixture_denied',503,'Fixture usage unavailable'))
    monkeypatch.setattr(f.service,'opencode_usage',quota);request,_=start();status,result,_=request('GET','/mobile/v1/provider-usage?profile=fixture&refresh=true')
    assert status==503 and result['code']=='fixture_denied' and result['error']=='Fixture usage unavailable'
    quota.assert_called_once_with(force=True)


@pytest.mark.parametrize('tls,rejected,status',[(False,False,403),(True,False,200),(True,True,409)])
def test_password_decision_requires_real_tls_and_never_echoes_password(mobile_http,monkeypatch,tls,rejected,status):
    start,_=mobile_http
    decision=Mock(side_effect=s.SudoRequestError('fixture expired') if rejected else None,return_value={'accepted':True})
    monkeypatch.setattr(s,'deliver_decision',decision)
    request,_=start(tls=tls)
    actual,result,_=request('POST','/mobile/v1/sudo-requests/owned-request',body={'decision':'allow','password':'fixture-password'})
    assert actual==status and 'fixture-password' not in json.dumps(result)
    if tls:decision.assert_called_once_with('owned-request','allow','fixture-password')
    else:decision.assert_not_called()
