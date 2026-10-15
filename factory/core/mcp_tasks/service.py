import hmac
import json
import os
import threading
from pathlib import Path

from .access import McpAccess
from .gmail import GmailConnection
from .private import PrivateMcp
from ..runtime import get_data_root, get_profile_config, list_profile_names
from ..vault.crypto import derive_key, resolve_passphrase
from ..vault.lifecycle import is_loopback_provider
from ..vault.lifecycle import VaultAuthenticationError


class HostVault:
    def __init__(self, root=None):
        from ..vault.config import get_settings
        from ..vault.vault import Vault
        settings = get_settings()
        self.root = Path(root) if root else settings.db_path.parent
        if not (self.root / 'vault.db').is_file():
            raise PermissionError()
        self.profiles_root = self.root.parent / 'profiles'
        if root:
            from ..vault.crypto import _read_passphrase_file
            password = _read_passphrase_file(self.root / 'passphrase.txt')
            if not password:
                raise PermissionError()
        else:
            password = resolve_passphrase()
        self.vault = Vault(self.root / 'vault.db', self.root / 'master_key_salt.bin', password)
        self.policy_path = self.root / 'policy.yaml' if root else settings.effective_policy_path
        self.salt = (self.root / 'master_key_salt.bin').read_bytes()
        self.state = 'unlocked'

    def authenticate(self, password):
        if not hmac.compare_digest(derive_key(password, self.salt), self.vault.key):
            raise PermissionError()

    def list_credentials(self):
        return self.vault.list_credentials()

    def get_secret(self, name):
        return self.vault.get_secret(name)

    def add_credential(self, *args, **kwargs):
        return self.vault.add_credential(*args, **kwargs)

    def list_provider_profiles(self):
        profiles = []
        for name in list_profile_names(self.profiles_root):
            try:
                value = get_profile_config(name, profiles_dir=self.profiles_root)
                if value.get('backend') != 'cc-backend' or value.get('api_format') != 'openai':
                    continue
                config = {key:value.get(key,'') for key in ('model','base_url','api_format','backend','provider')}
                config['name'] = name
                if not config['model'] or not config['base_url']:
                    continue
                config['auth_binding'] = hmac.new(self.vault.key, json.dumps(value.get('env',{}),sort_keys=True).encode(), 'sha256').hexdigest()
                profiles.append({'credential_id':'runtime:'+name,'config':config,'status':'configured'})
            except (OSError, ValueError):
                continue
        return profiles

    def provider_runtime_headers(self, credential_id, config, api_format):
        profile = next((p for p in self.list_provider_profiles() if p['credential_id']==credential_id and p['config']==config), None)
        if profile is None or api_format != 'openai':
            raise PermissionError()
        from ..worker import _resolve_cc_backend_auth_headers
        original = get_profile_config(config['name'], profiles_dir=self.profiles_root)
        environment = {**os.environ, **original.get('env',{})}
        provider = str(original.get('provider') or '').strip().lower()
        declared = original.get('vault_services', [])
        if isinstance(declared, str):
            declared = [value.strip() for value in declared.split(',')]
        vault_config = original.get('vault')
        if not isinstance(vault_config, dict):
            vault_config = {}
        if vault_config:
            extra = vault_config.get('services', [])
            if isinstance(extra, str):
                extra = [value.strip() for value in extra.split(',')]
            declared = list(declared) + list(extra)
        if provider in {'openrouter','opencode','opencode-go','minimax','glm'} and provider in declared:
            from ..vault.audit import AuditLogger
            from ..vault.broker import Broker
            from ..vault.policy import PolicyEngine
            from ..vault.verifier import Verifier
            broker = Broker(self.vault, PolicyEngine.from_yaml(self.policy_path), Verifier(), AuditLogger(self.root / 'vault.db'))
            agent = str((vault_config or {}).get('agent') or original.get('vault_agent') or 'cc-backend')
            decision = broker.get_ephemeral_env(service=provider, agent_id=agent, ttl=300)
            if not decision.allowed:
                raise PermissionError()
            environment.update(decision.env)
        headers = _resolve_cc_backend_auth_headers(original, api_format=api_format, environment=environment)
        if not headers and not is_loopback_provider(config):
            raise PermissionError()
        return headers or {}


class McpTasks:
    def __init__(self, *, root=None, lifecycle=None, consumer_valid=None, owner_device_check=None):
        self.root = Path(root) if root else get_data_root()
        self.lifecycle = lifecycle
        self.consumer_valid = consumer_valid or (lambda identity: True)
        self.owner_device_check = owner_device_check or (lambda identity: False)
        self.access = self.gmail = self.runner = None
        self.owner = None
        self.task_id = None
        self.mutex = threading.RLock()

    def _ready(self):
        if self.lifecycle is None:
            self.lifecycle = HostVault()
        if self.runner is None:
            self.access = McpAccess(self.root,self.lifecycle,policy_name='private-access.json')
            self.gmail = GmailConnection(self.lifecycle)
            self.runner = PrivateMcp(self.root,self.lifecycle,self.gmail,access=self.access)

    def dispatch(self, identity, action, fields=None):
        fields = fields or {}
        shapes = {'list':set(), 'catalog':set(), 'configure':{'server','credential_id','access','password'}, 'configure_device':{'server','credential_id','access','confirmation'}, 'start':{'server','credential_id','message'},'status':set(),'clear':set(),'approve':{'request_id','decision'}}
        if action not in shapes or not isinstance(fields,dict) or set(fields)!=shapes[action] or any(not isinstance(value,str) or not value.strip() or len(value)>1024 for value in fields.values()):
            return {'error':'invalid_request'}
        with self.mutex:
            try:
                if not self.consumer_valid(identity):
                    raise PermissionError()
                self._ready()
                if self.lifecycle.state!='unlocked':
                    return {'error':'vault_locked'}
                if action=='configure':
                    self.access.save(fields['server'],fields['credential_id'],fields['access'],fields['password'])
                    return {'saved':True}
                if action=='configure_device':
                    if fields['confirmation'] != 'owner_confirmed' or not self.owner_device_check(identity) or not self.consumer_valid(identity):
                        raise PermissionError()
                    self.access._save_owner_grant(fields['server'],fields['credential_id'],fields['access'])
                    return {'saved':True}
                if action in {'catalog','list'}:
                    connected = self.gmail.status()['connected']
                    profiles = self.lifecycle.list_provider_profiles()
                    servers = self.access.registry()
                    if action=='catalog':
                        return {'servers':[{'id':name,'disabled':value.get('disabled') is True} for name,value in servers.items()],
                            'profiles':[{'credential_id':p['credential_id'],'name':p['config']['name'],'local':is_loopback_provider(p['config'])} for p in profiles],
                            'permissions':{server:{cid:grant.get('access','deny') for cid,grant in grants.items() if isinstance(grant,dict)} for server,grants in self.access.grants().items() if isinstance(grants,dict)},'gmail_connected':connected}
                    entries=[]
                    for server in servers:
                        if server=='gmail' and not connected:
                            continue
                        for p in profiles:
                            if p['status'] in {'verified','configured'} and p['config'].get('api_format')=='openai' and self.access.authorize(server,p['config'],private=True):
                                entries.append({'server':server,'credential_id':p['credential_id'],'profile':p['config']['name'], 'local':is_loopback_provider(p['config']), 'access':self.access.grants()[server][p['credential_id']]['access'],'readonly':server in {'gmail','openai_docs'}})
                    return {'entries':entries}
                if self.owner and self.owner!=identity:
                    if self.consumer_valid(self.owner):
                        return {'error':'task_other_window'}
                    self.runner.clear()
                    self.owner=self.task_id=None
                if action=='start':
                    self.runner.start(fields['credential_id'],fields['server'],fields['message'],consumer_check=lambda:self.consumer_valid(identity),continuation=True)
                    self.owner=identity
                    self.task_id=self.runner.task_id
                elif action=='approve':
                    if self.owner!=identity:raise PermissionError()
                    self.runner.approve(fields['request_id'],fields['decision'])
                elif action=='clear':
                    if self.owner==identity:
                        self.runner.clear()
                        self.owner=self.task_id=None
                    return {'job':{'running':False,'result':'','error':''}}
                if self.owner!=identity:
                    return {'job':{'running':False,'result':'','error':''}}
                result=self.runner.status()
                if result['task_id']!=self.task_id:
                    return {'error':'task_replaced'}
                if not self.consumer_valid(identity):
                    raise PermissionError()
                return {'job':result}
            except (PermissionError, VaultAuthenticationError):
                return {'error':'mcp_access_denied'}
            except Exception:
                return {'error':'mcp_unavailable'}
