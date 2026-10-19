import io
import json
import ssl
import urllib.error
from unittest.mock import patch
from types import SimpleNamespace

from factory.core.cc_backend_executor import _http_post, _http_post_curl, _is_transient_network_error
from factory.core.cc_backend_trace import append_cc_backend_trace, trace_health
from factory.core.mobile_bridge.events import normalize_trace_event
from factory.core.diagnostics import model_observation


def test_legacy_http_failure_keeps_status_delay_correlation_and_redacts_body():
    error=urllib.error.HTTPError('https://example.invalid',503,'fixture',{'retry-after':'3','x-request-id':'req_fixture12345678'},io.BytesIO(b'{"error":"api_key=fixture-private"}'))
    with patch('urllib.request.urlopen',side_effect=error):
        body,_=_http_post('https://example.invalid/v1/messages',{}, {},1)
    assert body['error_code']=='HTTP.SERVICE_UNAVAILABLE'
    assert body['diagnostic']['retry_after_s']==3
    assert body['diagnostic']['provider_request_id']=='req_fixture12345678'
    assert 'fixture-private' not in json.dumps(body)
    assert _is_transient_network_error(body['error'],error_code=body['error_code'])


def test_certificate_failure_is_never_retried():
    with patch('urllib.request.urlopen',side_effect=ssl.SSLCertVerificationError(1,'fixture')):
        body,_=_http_post('https://example.invalid/v1/messages',{}, {},1)
    assert body['error_code']=='TLS.CERTIFICATE_REJECTED'
    assert not _is_transient_network_error(body['error'],error_code=body['error_code'])


def test_curl_certificate_failure_does_not_expose_command_or_credentials():
    with patch('subprocess.run',return_value=SimpleNamespace(stdout='\n000',stderr='fixture-private',returncode=60)):
        body,_=_http_post_curl('https://example.invalid',{}, {'Authorization':'Bearer fixture-private'},1)
    assert body['error_code']=='TLS.CERTIFICATE_REJECTED'
    assert 'fixture-private' not in json.dumps(body)


def test_mobile_finish_preserves_safe_metadata():
    raw=model_observation({'error':'fixture','error_code':'responses_timeout'},profile='fixture',model='org/model',task_id='task_fixture')
    event=normalize_trace_event({'kind':'task.finish','status':'fail','diagnostic':raw,'error_diagnostics':{'error_code':'responses_timeout','authorization':'fixture-private'}})
    assert event['payload']['diagnostic']['model']=='org/model'
    assert event['payload']['diagnostic']['task_id']=='task_fixture'
    assert 'fixture-private' not in json.dumps(event)


def test_trace_error_redaction_and_writer_failure_do_not_replace_task_error(tmp_path):
    append_cc_backend_trace(str(tmp_path),{'kind':'network.retry','error':'token=fixture-private'})
    assert 'fixture-private' not in (tmp_path/'cc_backend_trace.jsonl').read_text()
    before=trace_health()['write_failures']
    with patch('pathlib.Path.open',side_effect=OSError('fixture disk full')):
        append_cc_backend_trace(str(tmp_path),{'kind':'task.finish','status':'fail','reason':'http_error'})
    assert trace_health()['write_failures']==before+1


def test_responses_statuses_use_structural_retry_rules():
    assert _is_transient_network_error('fixture',error_code='responses_failed',error_diagnostics={'http_status':503})
    assert not _is_transient_network_error('fixture',error_code='responses_failed',error_diagnostics={'http_status':429,'provider_error_code':'insufficient_quota'})


def test_trace_formatter_failure_is_counted_and_cannot_replace_execution_error(tmp_path):
    class BadValue:
        def __str__(self):
            raise ValueError('fixture formatting failure')

    before = trace_health()['dropped']
    append_cc_backend_trace(str(tmp_path), {'kind': 'task.finish', 'output': BadValue()})
    assert trace_health()['dropped'] == before + 1
