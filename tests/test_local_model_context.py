import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from factory.core.local_model_context import is_loopback_model_url, local_model_context_window


@pytest.mark.parametrize('url', ['http://127.0.0.1:8081/v1', 'http://localhost:8081/v1', 'http://[::1]:8081/v1'])
def test_only_loopback_inference_addresses_are_eligible(url):
    assert is_loopback_model_url(url)


@pytest.mark.parametrize('url', ['https://example.org/v1', 'http://192.168.0.171/v1', 'file:///tmp/profile',
                                 'http://user:secret@localhost/v1', 'http://localhost:99999/v1',
                                 'http://localhost/v1?token=private', 'http://localhost/v1#fragment'])
def test_remote_or_credential_bearing_urls_are_not_contacted(url):
    with patch('factory.core.local_model_context.http.client.HTTPConnection') as connection:
        assert local_model_context_window(url) is None
        connection.assert_not_called()


def test_live_props_tracks_configured_context_and_never_generates_or_follows_redirects():
    requests = []
    payload = {'default_generation_settings': {'n_ctx': 64000}, 'total_slots': 2, 'n_ctx': 128000}
    response_status = [200]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(response_status[0])
            if response_status[0] == 302:
                self.send_header('Location', 'https://example.org/private')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}/v1'
    try:
        assert local_model_context_window(base) == 64000
        payload['default_generation_settings']['n_ctx'] = 128000
        assert local_model_context_window(base) == 128000
        payload['default_generation_settings']['n_ctx'] = True
        assert local_model_context_window(base) is None
        payload.clear()
        payload['n_ctx_train'] = 262144
        assert local_model_context_window(base) is None
        response_status[0] = 302
        assert local_model_context_window(base) is None
        assert requests == ['/props'] * 5
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_metadata_timeout_is_unknown_without_a_catalogue_guess():
    with patch('factory.core.local_model_context.http.client.HTTPConnection', side_effect=TimeoutError):
        assert local_model_context_window('http://127.0.0.1:8081/v1') is None


def test_profile_context_uses_live_local_limit_and_preserves_cloud_resolution(tmp_path):
    from factory.web_ui.server import _profile_context_window
    profile = tmp_path / 'local.json'
    profile.write_text(json.dumps({'base_url': 'http://127.0.0.1:8081/v1'}))
    with patch('factory.web_ui.server.get_profile_path', return_value=profile), \
         patch('factory.core.local_model_context.local_model_context_window', return_value=64000) as live, \
         patch('factory.web_ui.server._model_context_window', return_value=262144) as catalogue:
        assert _profile_context_window('local', 'qwen-local') == 64000
        live.assert_called_once_with('http://127.0.0.1:8081/v1')
        catalogue.assert_not_called()
        live.return_value = None
        assert _profile_context_window('local', 'qwen-local') is None
        catalogue.assert_not_called()
        profile.write_text(json.dumps({'base_url': 'https://api.example.org/v1'}))
        assert _profile_context_window('cloud', 'cloud-model') == 262144
        catalogue.assert_called_once_with('cloud-model')


def test_worker_prefers_live_local_context_before_the_models_catalogue():
    from factory.core.cc_backend_executor import _resolve_backend_context_window
    with patch('factory.core.local_model_context.local_model_context_window', return_value=64000), \
         patch('factory.core.cc_backend_executor._context_window_from_models_cache', return_value=262144) as catalogue:
        assert _resolve_backend_context_window(base_url='http://127.0.0.1:8081/v1', model='qwen-local', fallback=32000) == (64000, '/props')
        catalogue.assert_not_called()
