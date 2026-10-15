import json

from factory.core.cc_backend_trace import model_failure_diagnostics, browser_tool_diagnostics


def test_model_failure_metadata_keeps_static_codes_and_rejects_secret_fields():
    body={'http_status':200,'error_code':'responses_failed','provider_error_code':'server_error',
        'request_id':'req_fixture12345678','terminal_event':'response.failed','transport_phase':'response_terminal',
        'response_content_type':'text/event-stream','authorization':'Bearer private-secret','message':'private-body'}
    value=model_failure_diagnostics(body)
    assert value['provider_error_code']=='server_error' and value['request_id']=='req_fixture12345678'
    assert 'private' not in json.dumps(value)
    assert model_failure_diagnostics({'error_code':'private-secret','provider_error_code':{},'http_status':True,'request_id':'Bearer private'})=={}
    assert model_failure_diagnostics([])=={}


def test_browser_failure_and_recovered_navigation_metadata_are_safe():
    ref='r-'+'a'*32+'-17'
    body={'error':'navigation_failed','auth':'not_checked','url':'https://example.com/private?token=secret',
        'navigation':{'phase':'failed','error_code':'ERR_ABORTED','committed':True,'document_ready':False,'recovered':False,'headers':{'cookie':'secret'}}}
    value=browser_tool_diagnostics({'action':'open','target':ref},json.dumps(body))
    assert value['error']=='navigation_failed' and value['target_ref']==ref and value['origin']=='https://example.com'
    assert value['navigation']['error_code']=='ERR_ABORTED' and 'secret' not in json.dumps(value)
    body['navigation']={'phase':'complete','warning_code':'ERR_ABORTED','recovered':True}
    assert browser_tool_diagnostics({'action':'open'},json.dumps(body))['navigation']['warning_code']=='ERR_ABORTED'
    assert browser_tool_diagnostics({'action':{}},json.dumps({'error':{},'navigation':{'phase':{},'error_code':[]}}))=={'navigation':{}}
    assert browser_tool_diagnostics({'action':'read'},'invalid JSON')=={'action':'read'}
