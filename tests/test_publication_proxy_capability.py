"""The local egress proxy lends provider authority only to its scoped bearer."""
import http.client
import io
from types import SimpleNamespace
from unittest.mock import Mock

from factory.core.isolation import egress_auth as module


class Response(io.BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json'}

    def __init__(self):
        super().__init__(b'{"ok":true}')


def call(proxy, path):
    connection = http.client.HTTPConnection('127.0.0.1', proxy.port, timeout=2)
    connection.request('GET', path)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def test_missing_wrong_and_retired_capabilities_never_borrow_provider_credentials(monkeypatch):
    token = Mock(return_value='synthetic-host-credential')
    outbound = Mock(side_effect=lambda *args, **kwargs: Response())
    logs = []
    monkeypatch.setattr(module, '_OPENER', SimpleNamespace(open=outbound))
    proxy = module.EgressAuthProxy(module.ProxyConfig(upstream_base='https://provider.example',
                                                     token_provider=token, log_fn=logs.append))
    proxy.start()
    try:
        assert call(proxy, '/v1/models')[0] == 401
        assert call(proxy, '/wrong/v1/models')[0] == 401
        token.assert_not_called()
        old = proxy.capability
        assert call(proxy, '/' + old + '/v1/models')[0] == 200
        assert outbound.call_args.args[0].full_url == 'https://provider.example/v1/models'
        assert not any(old in entry for entry in logs)
        proxy.stop()
        proxy.start()
        token.reset_mock()
        assert proxy.capability != old
        assert call(proxy, '/' + old + '/v1/models')[0] == 401
        token.assert_not_called()
    finally:
        proxy.stop()
