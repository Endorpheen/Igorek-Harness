import json
import os
import re
import tempfile
from pathlib import Path


class McpAccess:
    def __init__(self, root, lifecycle, policy_name='access.json'):
        self.root = Path(root)
        self.lifecycle = lifecycle
        if policy_name not in {'access.json','private-access.json'}:
            raise ValueError()
        self.path = self.root / 'mcp' / policy_name

    def registry(self):
        path = self.root / 'mcp' / 'servers.json'
        if not path.exists():
            data = {}
        else:
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                raise ValueError()
            data = json.loads(path.read_text())['mcpServers']
        if not isinstance(data, dict):
            raise ValueError()
        result = {name: entry for name, entry in data.items() if re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name) and isinstance(entry, dict)}
        result['gmail'] = {'type':'builtin','adapter':'gmail'}
        from .plugins import PluginSettings
        result.update(PluginSettings(self.root).registry())
        return result

    def grants(self):
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or self.path.stat().st_size > 1024 * 1024:
            raise ValueError()
        value = json.loads(self.path.read_text())
        if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('grants'), dict):
            raise ValueError()
        return value['grants']

    def status(self):
        return {'servers': [{'id': name, 'disabled': entry.get('disabled') is True} for name, entry in self.registry().items()],
                'profiles': self.lifecycle.list_provider_profiles(), 'grants': self.grants(),
                'private_processing_ready': True}

    def save(self, server, credential_id, access, password):
        self.lifecycle.authenticate(password)
        return self._save_owner_grant(server, credential_id, access)

    def _save_owner_grant(self, server, credential_id, access):
        if access not in {'deny', 'allow', 'local_only'} or server not in self.registry():
            raise ValueError()
        profile = next((p for p in self.lifecycle.list_provider_profiles() if p['credential_id'] == credential_id), None)
        if profile is None:
            raise ValueError()
        grants = self.grants()
        grants.setdefault(server, {})[credential_id] = {'access': access, 'config': profile['config']}
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.access-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'version': 1, 'grants': grants}, stream)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        return self.status()

    def authorize(self, server, config, *, private=False):
        if self.lifecycle.state != 'unlocked':
            return False
        registry = self.registry()
        if server not in registry or registry[server].get('disabled') is True:
            return False
        profile = next((p for p in self.lifecycle.list_provider_profiles() if p['config'] == config), None)
        if profile is None:
            return False
        grant = self.grants().get(server, {}).get(profile['credential_id'], {})
        if grant.get('config') != config:
            return False
        if grant.get('access') == 'local_only':
            from factory.core.vault.lifecycle import is_loopback_provider
            return private and is_loopback_provider(config) and config.get('api_format') == 'openai'
        return grant.get('access') == 'allow'
