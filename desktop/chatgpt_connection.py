"""Owner-only browser sign-in; the loopback callback never enters the renderer."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from factory.core import chatgpt_auth as auth
from provider_setup import profile_config


class ChatGPTConnection:
    def __init__(self, root, lifecycle):
        self.root, self.lifecycle = root, lifecycle
        self._mutex = threading.RLock()
        self._pending = None
        self._result = {'state': 'idle'}

    def _host_id(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / 'chatgpt-host-id'
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if path.is_symlink():
                raise ValueError('Invalid host identity')
        else:
            with os.fdopen(descriptor, 'w') as stream:
                stream.write('urn:uuid:' + str(uuid.uuid4()))
                stream.flush()
                os.fsync(stream.fileno())
        value = path.read_text().strip()
        if not value.startswith('urn:uuid:') or str(uuid.UUID(value[9:])) != value[9:]:
            raise ValueError('Invalid host identity')
        return value

    def begin(self, name, credential_id='', *, consumer_check=lambda: True):
        config = profile_config(name, 'select-model', auth.RESOURCE, 'openai-responses')
        with self._mutex, self.lifecycle._mutex:
            if self._pending:
                raise auth.ChatGPTAuthError('chatgpt_signin_busy')
            vault = self.lifecycle._require()
            previous, record = None, None
            if credential_id:
                _, record, secret = self.lifecycle._chatgpt_record(credential_id)
                if secret.metadata['desktop_profile']['name'] != name:
                    raise ValueError('Profile binding changed')
                previous = json.loads(secret.secret)
                config = secret.metadata['desktop_profile']
            elif any(p['config']['name'] == name for p in self.lifecycle.list_provider_profiles()):
                raise auth.ChatGPTAuthError('profile_exists')
            pending = {'state': secrets.token_urlsafe(32), 'nonce': secrets.token_urlsafe(32),
                'verifier': secrets.token_urlsafe(64), 'expires': time.monotonic() + 600,
                'cancel': threading.Event(), 'vault': vault, 'previous': previous, 'record': record,
                'credential_id': credential_id, 'config': config, 'consumer_check': consumer_check,
                'host_id': self._host_id()}
            connection = self
            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_GET(self):
                    status = 400
                    expected_host = '127.0.0.1:' + str(self.server.server_port)
                    target = urlsplit(self.path)
                    if (len(self.path) <= 16384 and self.headers.get('Host') == expected_host
                            and target.path == '/auth/callback' and not target.scheme and not target.netloc):
                        try:
                            fields = parse_qs(target.query, keep_blank_values=True, max_num_fields=12)
                            if any(len(values) != 1 for values in fields.values()):
                                raise ValueError()
                            values = {key: value[0] for key, value in fields.items()}
                            if hmac.compare_digest(values.get('state', ''), pending['state']):
                                connection._complete(pending, values)
                                status = 200
                        except Exception:
                            status = 400
                    body = ('Вход обработан. Вернитесь в Игорька: результат показан в настройках.'
                            if status == 200 else 'Не удалось подтвердить запрос входа. Вернитесь в Игорька.').encode()
                    self.send_response(status)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Referrer-Policy', 'no-referrer')
                    self.send_header('Content-Security-Policy', "default-src 'none'")
                    self.end_headers()
                    self.wfile.write(body)
            server = HTTPServer(('127.0.0.1', 0), Callback)
            server.timeout = 0.25
            pending['redirect_uri'] = f'http://127.0.0.1:{server.server_port}/auth/callback'
            self._pending = pending
            self._result = {'state': 'awaiting', 'name': name}
            def listen():
                try:
                    while not pending['cancel'].is_set() and time.monotonic() < pending['expires']:
                        server.handle_request()
                finally:
                    server.server_close()
                    with self._mutex:
                        if self._pending is pending:
                            self._pending = None
                            self._result = {'state': 'failed', 'error': 'chatgpt_signin_expired'}
            threading.Thread(target=listen, daemon=True, name='chatgpt-sign-in').start()
            params = {'client_id': previous['client_id'] if previous else 'dynamic_agent_client',
                'ext_agent_host_id': pending['host_id'], 'redirect_uri': pending['redirect_uri'],
                'response_type': 'code', 'scope': auth.SCOPES, 'resource': auth.RESOURCE,
                'state': pending['state'], 'nonce': pending['nonce'], 'code_challenge_method': 'S256',
                'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(pending['verifier'].encode()).digest()).decode().rstrip('=')}
            if not previous:
                params['agent_name_hint'] = 'Igorek'
            # Reauthorization deliberately omits optional token-bearing URL hints.
            return auth.AUTHORIZE + '?' + urlencode(params)

    def _complete(self, pending, fields):
        with self._mutex:
            if self._pending is not pending or pending['cancel'].is_set():
                raise ValueError()
            pending['cancel'].set()  # Consume this attempt before exchanging its code.
        result = {'state': 'failed', 'error': 'chatgpt_auth_failed'}
        try:
            if time.monotonic() >= pending['expires'] or not pending['consumer_check']():
                raise auth.ChatGPTAuthError('chatgpt_signin_expired')
            if fields.get('error'):
                raise auth.ChatGPTAuthError('chatgpt_access_denied')
            previous = pending['previous']
            client_id = fields.get('client_id') or (previous or {}).get('client_id')
            if (not isinstance(client_id, str) or not client_id or client_id == 'dynamic_agent_client'
                    or previous and client_id != previous['client_id'] or not fields.get('code')):
                raise auth.ChatGPTAuthError('chatgpt_registration_incomplete')
            response = auth.request(auth.TOKEN, form={'grant_type': 'authorization_code', 'client_id': client_id,
                'code': fields['code'], 'code_verifier': pending['verifier'],
                'redirect_uri': pending['redirect_uri'], 'resource': auth.RESOURCE})
            identity = auth.validate_identity(response.get('id_token', ''), client_id, pending['nonce'])
            if previous and identity['sub'] != previous['subject']:
                raise auth.ChatGPTAuthError('chatgpt_identity_invalid')
            saved = auth.token_set(response, {'issuer': auth.ISSUER, 'subject': identity['sub'],
                'email': str(identity.get('email') or '')[:256], 'client_id': client_id,
                'ext_agent_host_id': pending['host_id']})
            config = dict(pending['config'])
            catalog = None
            try:
                catalog = auth.models(saved['access_token'])
            except auth.ChatGPTAuthError:
                pass
            if not previous and catalog:
                config['model'] = catalog['models'][0]['id']
            with self._mutex, self.lifecycle._mutex:
                if (self._pending is not pending or not pending['consumer_check']()
                        or self.lifecycle._require() is not pending['vault']):
                    raise auth.ChatGPTAuthError('chatgpt_signin_expired')
                if previous:
                    vault, record, secret = self.lifecycle._chatgpt_record(pending['credential_id'], config)
                    if record.encrypted_payload != pending['record'].encrypted_payload:
                        raise auth.ChatGPTAuthError('chatgpt_profile_changed')
                    secret.secret = json.dumps(saved)
                    secret.metadata.pop('connection_check', None)
                    self.lifecycle._store_provider_secret(vault, record, secret)
                else:
                    record = self.lifecycle.add_credential('desktop-profile:' + config['name'], json.dumps(saved),
                        'oauth', imported_from='desktop-provider-setup', secret_metadata={'desktop_profile': config})
                result = {'state': 'connected', 'credential_id': record.id, 'name': config['name']}
                if not catalog:
                    result['warning'] = 'chatgpt_catalog_failed'
        except auth.ChatGPTAuthError as error:
            result['error'] = error.code
        except Exception:
            pass
        finally:
            with self._mutex:
                if self._pending is pending:
                    self._pending = None
                    self._result = result

    def status(self):
        with self._mutex:
            self.lifecycle._require()
            return dict(self._result)

    def cancel(self):
        with self._mutex:
            if self._pending:
                self._pending['cancel'].set()
                self._pending = None
            self._result = {'state': 'idle'}

    def catalog(self, credential_id):
        return auth.models(self.lifecycle.chatgpt_access_token(credential_id))
