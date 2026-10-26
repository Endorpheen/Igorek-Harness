"""Owned HTTP clients verify transfer errors and revocation without real devices."""
from email.message import Message
import io
import json
import urllib.error
from unittest.mock import Mock

import pytest

from test_web_ui_proxy_routes import servers, proxy  # noqa: F401
from factory.web_ui import server as web


class Reply(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value


def http_error(url, code, reason, headers, body):
    message = Message()
    for key, value in headers.items():
        message[key] = value
    return urllib.error.HTTPError(url, code, reason, message, body)


@pytest.mark.parametrize('route', ['/api/sessions/fixture/files/test.bin', '/api/sessions/fixture/events?after=2&stream=1'])
@pytest.mark.parametrize('error', [urllib.error.URLError('fixture offline'), TimeoutError('fixture timeout'), OSError('fixture socket')])
def test_unavailable_transfer_returns_actionable_gateway_error(proxy, monkeypatch, route, error):
    upstream = Mock(side_effect=error)
    monkeypatch.setattr(web.urllib.request, 'urlopen', upstream)
    status, headers, body = proxy.request('GET', route)
    assert status == 502, body
    assert 'Не удалось' in json.loads(body)['error']
    assert b'fixture-token' not in body
    assert upstream.call_count == 1


@pytest.mark.parametrize('code', [401, 404, 416, 503])
def test_download_preserves_http_failure_and_range_contract(proxy, monkeypatch, code):
    exc = http_error('http://fixture', code, 'failure', {'Content-Range': 'bytes */123'}, io.BytesIO(b'{"error":"fixture unavailable"}'))
    monkeypatch.setattr(web.urllib.request, 'urlopen', Mock(side_effect=exc))
    status, headers, body = proxy.request('GET', '/api/sessions/fixture/files/test.bin')
    assert status == code
    if code == 416:
        assert headers['Content-Range'] == 'bytes */123' and body == b''
    elif code == 401:
        assert 'Max-Age=0' in headers['Set-Cookie']
        assert 'отозван' in json.loads(body)['error']
    else:
        assert json.loads(body)['error'] == 'fixture unavailable'


@pytest.mark.parametrize('length', [True, False])
def test_binary_transfer_keeps_bytes_range_and_close_semantics(proxy, monkeypatch, length):
    raw = b'\x00\xffbinary\x80'
    headers = {'Content-Type': 'application/octet-stream', 'Content-Range': 'bytes 0-8/100', 'Accept-Ranges': 'bytes', 'Content-Disposition': 'attachment; filename="test.bin"'}
    if length:
        headers['Content-Length'] = str(len(raw))
    upstream = Mock(return_value=Reply(raw, 206, headers))
    monkeypatch.setattr(web.urllib.request, 'urlopen', upstream)
    status, received, data = proxy.request('GET', '/api/sessions/fixture/files/test.bin', headers={'Range': 'bytes=0-8'})
    assert (status, data) == (206, raw)
    assert received['Content-Range'] == headers['Content-Range']
    assert received['Content-Disposition'] == headers['Content-Disposition']
    assert upstream.call_args.args[0].get_header('Range') == 'bytes=0-8'
    assert upstream.call_args.args[0].get_header('Authorization') == 'Bearer fixture-token'
    if not length:
        assert received['Connection'] == 'close'


def test_sse_preserves_frames_and_authentication(proxy, monkeypatch):
    raw = b'id: 3\nevent: usage\ndata: {"value":2}\n\n:keepalive\r\n\r\n'
    upstream = Mock(return_value=Reply(raw))
    monkeypatch.setattr(web.urllib.request, 'urlopen', upstream)
    status, headers, data = proxy.request('GET', '/api/sessions/fixture/events?after=2&stream=1')
    assert status == 200 and data == raw
    assert headers['X-Accel-Buffering'] == 'no'
    assert headers['Content-Type'].startswith('text/event-stream')
    request = upstream.call_args.args[0]
    assert 'session_id=fixture' in request.full_url and 'after=2' in request.full_url
    assert request.get_header('Accept') == 'text/event-stream'


@pytest.mark.parametrize('sequence,code,expected', [(-8, 200, 0), ('4', 200, 4), (None, 502, None), ('not-a-number', 502, None)])
def test_cursor_normalizes_valid_values_and_rejects_malformed(proxy, monkeypatch, sequence, code, expected):
    reply = Reply(json.dumps({'seq': sequence}).encode(), headers={'Content-Type': 'application/json'})
    monkeypatch.setattr(web.urllib.request, 'urlopen', Mock(return_value=reply))
    status, _, data = proxy.request('GET', '/api/sessions/fixture/cursor')
    assert status == code, data
    if expected is not None:
        assert json.loads(data) == {'seq': expected}
    else:
        assert 'курсор' in json.loads(data)['error']


@pytest.mark.parametrize('status', [401, 403, 503])
def test_cursor_propagates_upstream_access_failure(proxy, monkeypatch, status):
    exc = http_error('http://fixture', status, 'denied', {}, io.BytesIO(b'{"error":"fixture denied"}'))
    monkeypatch.setattr(web.urllib.request, 'urlopen', Mock(side_effect=exc))
    actual, headers, data = proxy.request('GET', '/api/sessions/fixture/cursor')
    assert actual == status
    if status == 401:
        assert 'Max-Age=0' in headers['Set-Cookie']
    assert b'fixture-token' not in data


@pytest.mark.parametrize('phase', ['list', 'missing-id', 'delete', 'ok'])
def test_logout_clears_cookie_only_after_confirmed_device_revocation(proxy, monkeypatch, phase):
    calls = []
    def respond(request, **kwargs):
        calls.append((request.method, request.full_url))
        if len(calls) == 1:
            if phase == 'list':
                raise http_error(request.full_url, 503, 'denied', {}, io.BytesIO(b'{"error":"fixture list error"}'))
            return Reply(json.dumps({} if phase == 'missing-id' else {'current_device_id': 'fixture/device'}).encode())
        if phase == 'delete':
            raise http_error(request.full_url, 409, 'denied', {}, io.BytesIO(b'{"error":"fixture delete error"}'))
        return Reply(b'{}')
    monkeypatch.setattr(web.urllib.request, 'urlopen', respond)
    status, headers, data = proxy.request('POST', '/api/logout', body={})
    assert status == {'list': 503, 'missing-id': 502, 'delete': 409, 'ok': 200}[phase], data
    assert ('Set-Cookie' in headers) == (phase == 'ok')
    if len(calls) == 2:
        assert calls[1][0] == 'DELETE' and calls[1][1].endswith('/devices/fixture%2Fdevice')


def test_logout_without_cookie_does_not_call_bridge(proxy, monkeypatch):
    upstream = Mock(side_effect=AssertionError('unauthorized upstream call'))
    monkeypatch.setattr(web.urllib.request, 'urlopen', upstream)
    status, headers, data = proxy.request('POST', '/api/logout', body={}, paired=False)
    assert status == 200 and json.loads(data)['logged_out'] is True
    assert 'Max-Age=0' in headers['Set-Cookie']
    upstream.assert_not_called()


@pytest.mark.parametrize('raw,headers,status', [(b'[]', {'Content-Type': 'application/json'}, 400), (b'\xff', {'Content-Type': 'application/json'}, 400), (b'{', {'Content-Type': 'application/json'}, 400), (b'{}', {'Content-Type': 'text/plain'}, 415), (b'', {'Content-Type': 'application/json'}, 400)])
def test_invalid_json_body_never_reaches_upstream(proxy, monkeypatch, raw, headers, status):
    upstream = Mock(side_effect=AssertionError('invalid body reached bridge'))
    monkeypatch.setattr(web.urllib.request, 'urlopen', upstream)
    actual, _, data = proxy.request('POST', '/api/mcp', raw=raw, headers=headers)
    assert actual == status, data
    upstream.assert_not_called()
