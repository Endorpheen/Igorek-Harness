"""Explicit encrypted value grants for private stdio MCP consumers."""
import hashlib
import hmac
import json
import re
import sqlite3
from uuid import uuid4

from .crypto import encrypt_secret


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class McpValueAccess:
    def __init__(self, root, lifecycle, access=None):
        from factory.core.mcp_tasks.access import McpAccess
        self.lifecycle = lifecycle
        self.access = access or McpAccess(root, lifecycle)

    def _servers(self):
        return {name: entry for name, entry in self.access.registry().items()
                if entry.get('type', 'stdio') == 'stdio' and isinstance(entry.get('command'), str)
                and entry['command'].strip() and entry.get('disabled') is not True}

    def _binding(self, server, profile_id):
        entry = self._servers().get(server)
        profile = next((p for p in self.lifecycle.list_provider_profiles() if p['credential_id'] == profile_id), None)
        if entry is None or profile is None:
            raise ValueError()
        return {'server': server, 'profile_id': profile_id,
                'server_digest': _digest(entry), 'profile_digest': _digest(profile['config'])}

    def _read(self, credential_id):
        record = self.lifecycle._value_record(credential_id)
        secret = self.lifecycle._require().get_secret(record.id)
        grants = secret.metadata.get('mcp_consumers', [])
        if not isinstance(grants, list):
            raise ValueError()
        return record, secret, grants

    def _write(self, record, secret, grants):
        secret.metadata['mcp_consumers'] = grants
        encrypted = encrypt_secret(secret.model_dump_json(), self.lifecycle._require().key)
        with sqlite3.connect(self.lifecycle.db_path) as conn:
            conn.execute('UPDATE credentials SET encrypted_payload=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                         (encrypted, record.id))

    def status(self, credential_id):
        with self.lifecycle._mutex:
            record, _, grants = self._read(credential_id)
            profiles = self.lifecycle.list_provider_profiles()
            result = []
            for grant in grants:
                try:
                    active = all(grant.get(key) == value for key, value in self._binding(grant['server'], grant['profile_id']).items())
                except (ValueError, KeyError):
                    active = False
                result.append({key: grant[key] for key in ('id', 'server', 'profile_id', 'env_name')} | {'active': active})
            return {'credential_id': record.id, 'name': record.service.removeprefix('desktop-value:'),
                    'servers': list(self._servers()),
                    'profiles': [{'credential_id': p['credential_id'], 'name': p['config']['name']} for p in profiles],
                    'grants': result}

    def grant(self, credential_id, server, profile_id, env_name, password):
        with self.lifecycle._mutex:
            self.lifecycle.authenticate(password)
            if not isinstance(env_name, str) or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', env_name) is None:
                raise ValueError()
            binding = self._binding(server, profile_id)
            record, secret, grants = self._read(credential_id)
            if '\0' in secret.secret:
                raise ValueError()
            for item in self.lifecycle.list_values():
                if item['credential_id'] == credential_id:
                    continue
                _, _, others = self._read(item['credential_id'])
                if any(g['server'] == server and g['profile_id'] == profile_id and g['env_name'] == env_name for g in others):
                    raise ValueError()
            grants = [g for g in grants if (g['server'], g['profile_id'], g['env_name']) != (server, profile_id, env_name)]
            grants.append({**binding, 'env_name': env_name, 'id': str(uuid4())})
            self._write(record, secret, grants)
            return self.status(credential_id)

    def revoke(self, credential_id, grant_id, password):
        with self.lifecycle._mutex:
            self.lifecycle.authenticate(password)
            record, secret, grants = self._read(credential_id)
            if not any(g['id'] == grant_id for g in grants):
                raise ValueError()
            self._write(record, secret, [g for g in grants if g['id'] != grant_id])
            return self.status(credential_id)

    def snapshot(self, server, profile_id, config):
        with self.lifecycle._mutex:
            if not self.access.authorize(server, config, private=True):
                raise PermissionError('value_access_denied')
            binding = self._binding(server, profile_id)
            if binding['profile_digest'] != _digest(config):
                raise PermissionError('value_access_denied')
            env, identities = {}, []
            for item in self.lifecycle.list_values():
                record, secret, grants = self._read(item['credential_id'])
                for grant in grants:
                    if grant.get('server') != server or grant.get('profile_id') != profile_id:
                        continue
                    if not all(grant.get(key) == value for key, value in binding.items()):
                        raise PermissionError('value_access_changed')
                    name = grant['env_name']
                    if name in env or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name) is None or '\0' in secret.secret:
                        raise PermissionError('value_access_denied')
                    env[name] = secret.secret
                    identities.append((record.id, grant['id']))
            payload = json.dumps({'binding': binding, 'env': env, 'grants': identities}, sort_keys=True).encode()
            signature = hmac.new(self.lifecycle._require().key, payload, 'sha256').hexdigest()
            return env, signature

    def issue(self, server, profile_id, config):
        from .audit import AuditLogger
        from .models import AccessLogRecord, Decision
        env, signature = self.snapshot(server, profile_id, config)
        if env:
            AuditLogger(self.lifecycle.home / 'consumer-audit.db').record(AccessLogRecord(
                agent_id='mcp:' + server + ':' + profile_id, service='desktop-values',
                action='consumer.env.issue', decision=Decision.allow,
                reason='explicit grant; env_names=' + ','.join(sorted(env))))
        return env, signature
