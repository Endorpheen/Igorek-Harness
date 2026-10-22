import json

from factory.core.diagnostics import model_observation, sanitize_observation
from factory.core.mobile_bridge.events import normalize_trace_event


def test_explicit_provider_reason_reaches_the_mobile_event_without_changing_status_policy():
    body = {'error': 'HTTP 500: '+json.dumps({'error': {'code': 'server_error', 'message': 'Fixture request content is unsupported'}}), 'http_status': 500}
    error = model_observation(body, task_id='fixture-task', session_id='fixture-session')
    assert error['code'] == 'HTTP.SERVICE_UNAVAILABLE'
    assert error['reason_code'] == 'server_error'
    assert error['reason_detail'] == 'Fixture request content is unsupported'
    event = normalize_trace_event({'kind': 'task.finish', 'ts': 1792629242, 'status': 'fail', 'diagnostic': error}, task_id='fixture-task')
    assert event['payload']['diagnostic']['reason_detail'] == error['reason_detail']


def test_native_transport_codes_and_unconfirmed_delivery_are_not_reclassified_from_text():
    error = model_observation({'error': 'arbitrary fixture error', 'diagnostic': {'code': 'TLS.CERTIFICATE_REJECTED', 'phase': 'connect'}})
    assert error['code'] == 'TLS.CERTIFICATE_REJECTED'
    assert error['reason_detail'] == 'arbitrary fixture error'
    assert sanitize_observation({**error, 'outcome': 'unknown'})['outcome'] == 'unknown'


def test_provider_error_detail_is_bounded_and_does_not_expose_credentials():
    error = model_observation({'error': {'message': 'api_key=EXCLUDED Bearer EXCLUDED sk-EXCLUDED https://user:EXCLUDED@fixture.test/p?secret=EXCLUDED '+('x' * 2000)}, 'http_status': 400})
    assert len(error['reason_detail']) <= 512
    assert 'EXCLUDED' not in json.dumps(error)
    assert sanitize_observation(error)['reason_detail'] == error['reason_detail']


def test_malformed_or_missing_provider_detail_does_not_manufacture_a_cause():
    for value in [None, [], {'code': 'fixture_code'}, 'HTTP 500: {broken']:
        error = model_observation({'error': value, 'http_status': 500})
        assert error['code'] == 'HTTP.SERVICE_UNAVAILABLE'
        if not isinstance(value, str):
            assert not error.get('reason_detail')


def test_error_payload_without_explicit_message_does_not_leak_echoed_input():
    body = {'error': 'HTTP 500: '+json.dumps({'error': {'code': 'server_error', 'request': {'input': 'EXCLUDED'}}}), 'http_status': 500}
    error = model_observation(body)
    assert error['reason_code'] == 'server_error'
    assert 'EXCLUDED' not in json.dumps(error)
    assert not error.get('reason_detail')


def test_unrecognized_provider_code_is_not_copied_as_a_private_identifier():
    error = model_observation({'error': 'Fixture failure', 'error_code': 'EXCLUDED', 'http_status': 503})
    assert 'EXCLUDED' not in json.dumps(error)
    assert error['reason_detail'] == 'Fixture failure'
