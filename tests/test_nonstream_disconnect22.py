"""Provider disconnects must produce a safe transport error."""

import http.client
import json
import urllib.request

from factory.core.cc_backend_executor import _http_post


def test_nonstream_disconnect_does_not_leak_secrets_or_look_successful(monkeypatch):
    def disconnect(*args, **kwargs):
        raise http.client.RemoteDisconnected('token=fixture-private')

    monkeypatch.setattr(urllib.request, 'urlopen', disconnect)
    result, _ = _http_post('https://example.invalid/v1/messages', {}, {}, 1)
    assert 'transport:remote_disconnected' in result['error']
    assert result.get('error_code', '').startswith('NET.')
    assert isinstance(result.get('diagnostic'), dict)
    assert 'choices' not in result
    assert 'fixture-private' not in json.dumps(result)
