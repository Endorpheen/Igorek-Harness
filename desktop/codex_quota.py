"""Read confirmed account quotas over HTTPS without a CLI or auth mutation."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time

from factory.core.artifact_files import open_regular_file
from factory.core.chatgpt_usage import UsageError, fetch_account_usage


class CodexQuotaConnection:
    def __init__(self, root, lifecycle, consumer_check=lambda: True, *, local_auth_path=None, fetcher=None):
        self.lifecycle = lifecycle
        self.consumer_check = consumer_check
        self.fetcher = fetcher or fetch_account_usage
        home = os.environ.get('FACTORY_EXTERNAL_CODEX_HOME', '')
        self.local_auth_path = (Path(local_auth_path) if local_auth_path else
            Path(home) / 'auth.json' if home and Path(home).is_absolute() else None)
        self.pending = {}
        self.mutex = threading.RLock()

    def record(self, profile):
        self.lifecycle._require()
        if not self.consumer_check():
            raise UsageError('quota_locked')
        item = next((entry for entry in self.lifecycle.list_provider_profiles()
                     if entry['config']['name'] == profile), None)
        if item is None:
            raise UsageError('profile_unknown')
        _, record, secret = self.lifecycle._chatgpt_record(item['credential_id'], item['config'])
        return item, record, secret, json.loads(secret.secret)

    @staticmethod
    def binding(item, saved):
        return {'credential_id': item['credential_id'], 'config': json.loads(json.dumps(item['config'])),
                'issuer': saved.get('issuer'), 'client_id': saved.get('client_id'), 'subject': saved.get('subject')}

    @staticmethod
    def identity(auth):
        try:
            tokens = auth['tokens']
            claims = json.loads(base64.urlsafe_b64decode(tokens['id_token'].split('.')[1] + '==='))
            email = claims.get('email') or (claims.get('https://api.openai.com/profile') or {}).get('email')
            subject, account_id, access_token = claims['sub'], tokens['account_id'], tokens['access_token']
            if (not isinstance(subject, str) or not subject or len(subject) > 256
                    or not isinstance(email, str) or not email or len(email) > 256
                    or not isinstance(account_id, str) or not account_id or len(account_id) > 256
                    or any(not 33 <= ord(char) <= 126 for char in account_id)
                    or not isinstance(access_token, str) or not access_token or len(access_token) > 32768
                    or any(not 33 <= ord(char) <= 126 for char in access_token)):
                raise ValueError()
            return {'subject': subject, 'email': email.casefold(), 'account_id': account_id}
        except (ValueError, KeyError, IndexError, TypeError):
            raise UsageError('quota_identity_invalid') from None

    def local_snapshot(self):
        if self.local_auth_path is None:
            return None
        try:
            with open_regular_file(self.local_auth_path.absolute()) as source:
                raw = source.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError()
            value = json.loads(raw)
            tokens = value['tokens']
            # Only access credentials are read; refresh tokens never enter the adapter.
            auth = {'tokens': {key: tokens[key] for key in ('id_token', 'access_token', 'account_id')}}
            self.identity(auth)
            return auth
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            raise UsageError('quota_local_login_required') from None

    def link(self, item, saved):
        secret = self.lifecycle._require().get_secret('desktop-codex-quota:' + item['credential_id'])
        if secret is None:
            return None
        try:
            link = json.loads(secret.secret)
            if not isinstance(link, dict) or link.get('source') not in {'local_codex', 'oauth', 'profile'}:
                raise ValueError()
            identity = link['identity']
            if not all(isinstance(identity[key], str) and identity[key]
                       for key in ('subject', 'email', 'account_id')):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise UsageError('quota_identity_invalid') from None
        if link.get('binding') != self.binding(item, saved):
            raise UsageError('quota_profile_changed')
        return link

    def _access(self, link, saved):
        if link['source'] == 'profile':
            token = saved.get('access_token')
            if not isinstance(token, str) or not token:
                raise UsageError('quota_login_required')
            return token
        auth = self.local_snapshot() if link['source'] == 'local_codex' else link.get('auth')
        if not auth:
            raise UsageError('quota_local_login_required')
        if self.identity(auth) != link['identity']:
            raise UsageError('quota_account_mismatch')
        return auth['tokens']['access_token']

    def cache_key(self, profile):
        item, _, _, saved = self.record(profile)
        link = self.link(item, saved)
        if not link:
            raise UsageError('quota_login_required')
        self._access(link, saved)
        return hashlib.sha256(json.dumps(link['identity'], sort_keys=True).encode()).hexdigest()

    def _pending_reply(self, profile, pending):
        item, _, _, saved = self.record(profile)
        if pending['binding'] != self.binding(item, saved):
            raise UsageError('quota_profile_changed')
        if time.monotonic() > pending['expires']:
            raise UsageError('quota_cancelled')
        return {'state': 'confirm_account', 'profile': profile, 'email': pending['identity']['email'],
                'link_id': pending['link_id'], 'source': pending['source']}

    def begin(self, profile):
        with self.mutex:
            item, _, _, saved = self.record(profile)
            current = self.pending.get(item['credential_id'])
            if current and (time.monotonic() > current['expires']
                            or current['binding'] != self.binding(item, saved)):
                del self.pending[item['credential_id']]
                current = None
            if current:
                return self._pending_reply(profile, current)
            local = self.local_snapshot()
            if local:
                identity = self.identity(local)
                if identity['email'] != str(saved.get('email', '')).casefold():
                    raise UsageError('quota_account_mismatch')
                source = 'local_codex'
            else:
                token = saved.get('access_token')
                email, subject = saved.get('email'), saved.get('subject')
                if not all(isinstance(value, str) and value for value in (token, email, subject)):
                    raise UsageError('quota_local_login_required')
                # An existing profile token may be accepted by the quota service.
                # A refusal never starts a login, refresh or another credential source.
                response = self.fetcher(token, expected_email=email, include_identity=True)
                identity = {**response['_account_identity'], 'subject': subject}
                source = 'profile'
            pending = {'identity': identity, 'source': source, 'binding': self.binding(item, saved),
                       'link_id': secrets.token_hex(16), 'expires': time.monotonic() + 600}
            self.pending[item['credential_id']] = pending
            return self._pending_reply(profile, pending)

    def confirm(self, profile, link_id):
        with self.mutex, self.lifecycle._mutex:
            item, _, _, saved = self.record(profile)
            pending = self.pending.get(item['credential_id'])
            if not pending or pending['link_id'] != link_id:
                raise UsageError('quota_login_failed')
            self._pending_reply(profile, pending)
            if pending['source'] == 'local_codex':
                local = self.local_snapshot()
                if not local or self.identity(local) != pending['identity']:
                    raise UsageError('quota_account_mismatch')
            value = {'binding': pending['binding'], 'identity': pending['identity'], 'source': pending['source']}
            # Explicit owner confirmation stores identity metadata, never tokens.
            self.lifecycle._require().add_credential('desktop-codex-quota:' + item['credential_id'],
                json.dumps(value), 'oauth', alias='counter', imported_from='desktop-codex-quota', replace_existing=True)
            del self.pending[item['credential_id']]
            return {'state': 'connected'}

    def status(self, profile):
        with self.mutex:
            item, _, _, saved = self.record(profile)
            pending = self.pending.get(item['credential_id'])
            if pending:
                return self._pending_reply(profile, pending)
            link = self.link(item, saved)
            if link:
                self._access(link, saved)
            return {'state': 'connected' if link else 'disconnected'}

    def usage(self, profile):
        with self.mutex:
            item, _, _, saved = self.record(profile)
            link = self.link(item, saved)
            if not link:
                raise UsageError('quota_confirmation_required' if item['credential_id'] in self.pending else 'quota_login_required')
            token = self._access(link, saved)
            key = self.cache_key(profile)
            identity = link['identity']
            result = self.fetcher(token, account_id=identity['account_id'], expected_email=identity['email'])
            # Neither a lock/profile switch nor a changed native account can publish a stale reply.
            if self.cache_key(profile) != key:
                raise UsageError('quota_account_mismatch')
            result.pop('_account_identity', None)
            result.update(account_email=identity['email'], account_key=key,
                          source='codex_local' if link['source'] == 'local_codex' else 'codex_account')
            return result

    def cancel(self, profile):
        with self.mutex:
            item, _, _, _ = self.record(profile)
            self.pending.pop(item['credential_id'], None)

    def close(self):
        with self.mutex:
            self.pending.clear()
