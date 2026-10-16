"""Expose owner-approved provider profiles without exporting provider secrets."""
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import stat
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from factory.core.vault.lifecycle import is_loopback_provider
from provider_probe import is_openai_realtime


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class OpenCodeProfiles(socketserver.ThreadingMixIn, socketserver.TCPServer if os.name == 'nt' else socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, root, lifecycle, allowed):
        from factory.core.platform_support import require_linux
        require_linux('opencode_profile_proxy')
        self.lifecycle, self.allowed = lifecycle, allowed
        self.grant = None
        self.mutex = threading.RLock()
        self.slots = threading.BoundedSemaphore(8)
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('opencode_profiles_private_directory_required')
        self.path = root / 'api.sock'
        if self.path.exists() or self.path.is_symlink():
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('opencode_profiles_socket_exists')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                try:
                    probe.connect(str(self.path))
                except ConnectionRefusedError:
                    self.path.unlink()
                else:
                    raise ValueError('opencode_profiles_already_running')
        super().__init__(str(self.path), Handler)
        os.chmod(self.path, 0o600)
        info = self.path.stat()
        self.identity = (info.st_dev, info.st_ino)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def configure(self, operation, value):
        if not isinstance(value, dict):
            raise ValueError('invalid_opencode_profiles')
        with self.mutex:
            if operation == 'revoke' and set(value) == {'token'} and isinstance(value['token'], str):
                if self.grant and hmac.compare_digest(self.grant['token'], value['token']):
                    self.grant = None
                return {'revoked': True}
            if operation != 'grant' or value or not self.allowed():
                raise ValueError('opencode_profile_access_denied')
            profiles = {p['credential_id']: p['config'] for p in self.lifecycle.list_provider_profiles()
                        if p['status'] == 'verified' and not is_openai_realtime(p['config'])
                        and p['config'].get('api_format') in {'openai', 'anthropic', 'openai-responses'}}
            self.grant = {'token': secrets.token_hex(32), 'profiles': json.loads(json.dumps(profiles))}
            contexts, catalog = {}, []
            for identity, config in profiles.items():
                base = config['base_url']
                if base not in contexts:
                    contexts[base] = self.context(config)
                catalog.append({'id': identity, 'name': config['name'], 'model': config['model'],
                                'api_format': config['api_format'], 'context': contexts[base]})
            return {'socket': str(self.path), 'token': self.grant['token'], 'profiles': catalog}

    def context(self, config):
        if not is_loopback_provider(config):
            return None
        url = urlsplit(config['base_url'])
        origin = f'{url.scheme}://{url.netloc}'
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
            with opener.open(origin + '/props', timeout=2) as response:
                data = json.loads(response.read(1024 * 1024))
            value = data.get('default_generation_settings', {}).get('n_ctx')
            return value if isinstance(value, int) and 1024 <= value <= 10_000_000 else None
        except (OSError, ValueError):
            return None

    def binding(self, token, identity):
        with self.mutex:
            if not self.grant or not isinstance(token, str) or not hmac.compare_digest(token, self.grant['token']) or not self.allowed():
                raise ValueError('opencode_profile_access_denied')
            config = self.grant['profiles'].get(identity)
            if config is None:
                raise ValueError('opencode_profile_not_granted')
            current = next((p for p in self.lifecycle.list_provider_profiles() if p['credential_id'] == identity), None)
            if not current or current['status'] != 'verified' or current['config'] != config:
                raise ValueError('opencode_profile_binding_changed')
            return dict(config)

    def server_close(self):
        with self.mutex:
            self.grant = None
        super().server_close()
        try:
            info = self.path.lstat()
            if (info.st_dev, info.st_ino) == self.identity:
                self.path.unlink()
        except FileNotFoundError:
            pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        self.request.settimeout(30)
        super().setup()

    def log_message(self, *_):
        pass

    def fail(self, status, reason):
        raw = json.dumps({'error': {'message': reason, 'type': 'igorek_profile_error'}}).encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        try:
            parts = self.path.split('/')
            if len(parts) < 5 or parts[1] != 'profiles' or parts[3] != 'v1':
                raise ValueError('opencode_profile_path_denied')
            identity, endpoint = parts[2], '/'.join(parts[4:])
            # OpenCode's saved credentials can replace Authorization, not the managed grant.
            token = self.headers.get('X-Igorek-Profile-Grant')
            if token is None:
                token = self.headers.get('Authorization', '').removeprefix('Bearer ') or self.headers.get('x-api-key', '')
            config = self.server.binding(token, identity)
            expected = {'openai': 'chat/completions', 'anthropic': 'messages', 'openai-responses': 'responses'}[config['api_format']]
            if endpoint != expected or self.headers.get('Transfer-Encoding'):
                raise ValueError('opencode_profile_path_denied')
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= length <= 32 * 1024 * 1024:
                self.fail(413, 'opencode_profile_request_too_large')
                return
            raw = self.rfile.read(length)
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get('model') != config['model']:
                raise ValueError('opencode_profile_model_denied')
            headers = self.server.lifecycle.provider_runtime_headers(identity, config, config['api_format'])
            auth = dict(headers)
            observer = getattr(headers, 'response_observer', None)
            auth.update({'Content-Type': 'application/json', 'Accept': self.headers.get('Accept', 'application/json')})
            if config['api_format'] == 'anthropic':
                auth['anthropic-version'] = '2023-06-01'
                beta = self.headers.get('anthropic-beta')
                if beta:
                    auth['anthropic-beta'] = beta
            opener = urllib.request.build_opener(NoRedirect, *([urllib.request.ProxyHandler({})] if is_loopback_provider(config) else []))
            base = config['base_url'].rstrip('/')
            if config['api_format'] == 'anthropic' and not base.endswith('/v1'):
                base += '/v1'
            if urlsplit(base).hostname == 'opencode.ai' and urlsplit(base).path == '/zen/go/v1':
                auth['x-opencode-session'] = 'igorek-opencode-' + token[:24]
            req = urllib.request.Request(base + '/' + endpoint, data=raw, headers=auth, method='POST')
        except (ValueError, KeyError, OSError):
            self.fail(403, 'opencode_profile_access_denied')
            return
        try:
            try:
                response = opener.open(req, timeout=2400)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                if observer:
                    observer(headers=response.headers)
                self.send_response(response.status)
                self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.close_connection = True
                self.end_headers()
                read = getattr(response, 'read1', response.read)
                while True:
                    if not self.server.allowed():
                        break
                    chunk = read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (OSError, ValueError, urllib.error.URLError):
            if not self.close_connection:
                self.fail(502, 'opencode_profile_upstream_unavailable')
