"""Restricted management protocol available only on the parent's private pipe."""
from factory.core.chatgpt_usage import UsageError
from mobile_access import MobileAccessError
from pathlib import Path
import base64
import tempfile
import json
import os
import hashlib
import hmac
import secrets

from factory.core.vault.lifecycle import (
    VaultLifecycle, VaultAuthenticationError, VaultBusyError,
    VaultLockedError, VaultMigrationRequired, VaultRecoveryError, MAX_RECOVERY_BYTES,
)
from factory.core.vault.management import VaultManagement, VaultPrincipal
from provider_setup import ProviderSetup
from execution_mode import DesktopExecution
from factory.core.isolation.errors import IsolationError
from image_setup import ImagePreparation, ImageSetupError
from factory.core.isolation.base_image import resolve_tool_version
from agent_runtime import AgentBusyError, ProfileNotVerifiedError, ProfileInUseError
from factory.core.vault.vault import DuplicateCredentialError
from factory.core.vault.env_import import EnvImportError


class OwnerVault:
    def __init__(self, root: Path):
        self.root = root
        self.lifecycle = VaultLifecycle(root / "vault" / "credentials")
        self.service = VaultManagement(self.lifecycle, principal=VaultPrincipal.OWNER)
        self.agent = None
        self.mobile_access=None
        self.image = ImagePreparation(root)
        self.execution = DesktopExecution(root)
        self.providers = ProviderSetup(self.service)
        from chatgpt_connection import ChatGPTConnection
        self.chatgpt = ChatGPTConnection(root, self.lifecycle)
        from codex_quota import CodexQuotaConnection
        self.codex_quota=CodexQuotaConnection(root,self.lifecycle,lambda:not self.interface_locked)
        self.lifecycle.codex_quota=self.codex_quota
        from private_mcp import PrivateMcp
        from gmail_connection import GmailConnection
        self.gmail = GmailConnection(self.lifecycle)
        self.private_mcp = PrivateMcp(root, self.lifecycle, self.gmail)
        from factory.core.mcp_tasks.plugins import Plugins
        self.plugins = Plugins(root, self.lifecycle, self.private_mcp.calendar)
        self.interface_locked = False
        self.lock_enabled = False
        try:
            self.lock_enabled = json.loads((root / "interface-security.json").read_text())["lock_enabled"] is True
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.pin_digest = None
        self.pin_salt = None
        self.pin_failures = 0
        self.host_access = None

    def _start_agent_profile(self, credential_id):
        if self.image.status()["running"]:
            raise ImageSetupError("image_busy")
        from factory.core.vault.lifecycle import is_loopback_provider
        profile = next((p for p in self.providers.list_profiles() if p["credential_id"] == credential_id), None)
        if profile and is_loopback_provider(profile["config"]) and self.execution.mode != "off":
            return 'local_requires_host_mode'
        self.agent.start(credential_id)
        return None

    def mobile_profile_control(self, operation, name):
        if operation not in {'catalog', 'activate'} or not isinstance(name, str) or len(name) > 40:
            return {'error': 'invalid_request'}
        if self.service.status()['state'] != 'unlocked':
            return {'error': 'vault_locked'}
        from factory.core.vault.lifecycle import is_loopback_provider
        from provider_probe import is_openai_realtime
        profiles = [p for p in self.providers.list_profiles() if p['status'] == 'verified'
                    and p['config'].get('backend') == 'cc-backend' and not is_openai_realtime(p['config'])]
        if not self.agent:
            return {'error': 'agent_unavailable'}
        if operation == 'catalog':
            return {'profiles': [{'name': p['config']['name'], 'model': p['config']['model'],
                                   'local': is_loopback_provider(p['config'])} for p in profiles],
                    'active_profile': self.agent.status().get('profile', ''), 'can_switch': True}
        profile = next((p for p in profiles if p['config']['name'] == name), None)
        if profile is None:
            return {'error': 'profile_not_verified'}
        with self.agent.control_lock:
            active = self.agent.status().get('profile', '')
            if active and active != name and self.browser_service.has_pending_profile_tasks(active):
                return {'error': 'profile_busy'}
            try:
                error = self._start_agent_profile(profile['credential_id'])
                return {'error': error} if error else {'agent': self.agent.status()}
            except AgentBusyError:
                return {'error': 'profile_busy'}
            except (ProfileNotVerifiedError, VaultLockedError):
                return {'error': 'profile_not_verified'}
            except ImageSetupError:
                return {'error': 'image_busy'}
            except Exception:
                return {'error': 'profile_start_failed'}

    def dispatch(self, request):
        identity = request.get("id") if isinstance(request, dict) else None
        reply = {"type": "owner_reply", "id": identity}
        try:
            if not isinstance(identity, str) or not 1 <= len(identity) <= 64:
                raise ValueError()
            action = request.get("action")
            expected = {"status": set(), "configure_interface_lock": {"password", "enabled"}, "lock_interface": set(), "unlock_interface": {"password"}, "set_interface_pin": {"password", "pin"}, "remove_interface_pin": {"password"}, "unlock_interface_pin": {"pin"}, "gmail_status": set(), "gmail_import_host": {"password"}, "gmail_configure": {"source", "password"}, "gmail_begin": {"password"}, "gmail_disconnect": {"password"}, "private_mcp_start": {"server", "credential_id", "message"}, "private_mcp_status": set(), "private_mcp_clear": set(), "host_mcp_access": set(), "set_host_mcp_access": {"server", "credential_id", "access", "password"}, "mcp_access": set(), "set_mcp_access": {"server", "credential_id", "access", "password"}, "read_legacy_profile": {"source"}, "readiness": set(), "image_status": set(), "execution_mode": set(), "select_execution_mode": {"mode"}, "prepare_image": {"source"}, "recover_image": {"source"}, "change_password": {"current", "new_password"}, "lock": set(), "create": {"password"}, "unlock": {"password"},
                        "generate_phrase": set(), "export_recovery": {"password", "recovery_phrase"},
                        "verify_recovery": {"backup", "recovery_phrase"},
                        "restore_recovery": {"backup", "recovery_phrase", "new_password"},
                        "delete_profile": {"credential_id", "password"}, "rename_profile": {"credential_id", "password", "new_name"}, "list_profiles": set(), "reveal_profile_key": {"credential_id", "password"}, "save_profile": {"name", "model", "base_url", "api_format", "api_key"},
                        "update_profile": {"credential_id", "name", "model", "base_url", "api_format", "api_key"},
                        "model_catalog": {"preset", "api_key", "credential_id"}, "check_profile": {"credential_id"}, "start_agent": {"credential_id"}, "stop_agent": set()}
            expected.update({"list_values": set(), "save_value": {"credential_id", "name", "kind", "value", "password"},
                             "reveal_value": {"credential_id", "password"}, "delete_value": {"credential_id", "password"},
                             "value_access": {"credential_id"},
                             "grant_value_access": {"credential_id", "server", "profile_id", "env_name", "password"},
                             "revoke_value_access": {"credential_id", "grant_id", "password"}})
            expected.update({'prepare_env_import': {'source'}, 'commit_env_import': {'draft_id', 'selection', 'password'}, 'cancel_env_import': {'draft_id'}})
            expected.update({'mobile_configure':{'port','public_url'},'mobile_start':set(),'mobile_status':set(),'mobile_stop':set(),'mobile_revoke':{'device_id'},'quota_begin': {'profile'}, 'quota_status': {'profile'}, 'quota_cancel': {'profile'}, 'quota_confirm': {'profile','link_id'}, 'chatgpt_begin': {'name', 'credential_id'}, 'chatgpt_status': set(),
                             'chatgpt_cancel': set(), 'chatgpt_models': {'credential_id'},
                             'chatgpt_disconnect': {'credential_id', 'password'}})
            expected.update({'plugins_status': set(), 'plugin_action': {'plugin','operation'}, 'calendar_connect': {'profile'}, 'calendar_disable': set(), 'session_agent': {'operation','profile'},
                             'browser_read_session': set(), 'host_service': {'operation','service'},
                             'opencode_services': {'operation', 'config'},
                             'opencode_profiles': {'operation', 'config'},
                             'calendar_configure': {'source','password'}, 'calendar_begin': {'password'},
                             'calendar_disconnect': {'password'}, 'calendar_list': set(),
                             'calendar_select': {'calendar_id','password'}, 'private_mcp_approve': {'request_id','decision'}})
            if not isinstance(action, str) or action not in expected or set(request) != {"id", "action"} | expected[action]:
                raise ValueError()
            for field in expected[action] - {"backup"}:
                value = request[field]
                if action in {'opencode_services', 'opencode_profiles'} and field == 'config':
                    if not isinstance(value, str) or not 1 <= len(value) <= 65536:
                        raise ValueError()
                    continue
                if action == 'commit_env_import' and field == 'selection':
                    if not isinstance(value, str) or not 1 <= len(value) <= 32768:
                        raise ValueError()
                    continue
                allow_empty = (action=='host_service' and field=='service' and request['operation']=='list') or (action=='mobile_configure' and field=='public_url') or (action == 'chatgpt_begin' and field == 'credential_id') or (action=='session_agent' and field=='profile' and request['operation']=='catalog') or (action in {"save_profile", "update_profile"} and field == "api_key") or (action == "model_catalog" and field in {"api_key", "credential_id"}) or (action == "save_value" and field == "credential_id")
                if action == "save_value" and field == "value":
                    if not isinstance(value, str) or len(value) > 1024:
                        raise ValueError()
                    continue
                if not isinstance(value, str) or (not value.strip() and not (allow_empty and value == "")) or len(value) > 1024:
                    raise ValueError()
            if self.interface_locked and action=='session_agent' and request['operation'] in {'status','catalog'}:
                if self.service.status()['state']!='unlocked':raise VaultLockedError()
                current=self.agent.status() if self.agent else {'running':False,'profile':''}
                if request['operation']=='status' and (not current['running'] or current['profile']!=request['profile']):
                    reply['error']='interface_locked'
                else:reply['result']={'agent':current,'profiles':[current['profile']] if current['running'] else []}
                return reply
            if action in {'opencode_services', 'opencode_profiles'}:
                server = getattr(self, action, None)
                if server is None or request['operation'] not in {'grant', 'revoke'}:
                    raise ValueError()
                if request['operation'] == 'grant' and self.interface_locked:
                    reply['error'] = 'interface_locked'
                    return reply
                reply['result'] = server.configure(request['operation'], json.loads(request['config']))
                return reply
            if self.interface_locked and action not in {"status", "unlock_interface", "unlock_interface_pin", "lock"}:
                reply["error"] = "interface_locked"
                return reply
            if action == 'host_service':
                if request['operation'] not in {'list', 'start', 'stop', 'status'}:
                    raise ValueError()
                manager = getattr(self, 'host_services', None)
                if manager is None:
                    raise ValueError()
                fields = {'action': request['operation']}
                if request['operation'] != 'list':
                    fields['service'] = request['service']
                reply['result'] = manager.execute(fields)
                return reply
            if action == "configure_interface_lock":
                self.service.authenticate(request["password"])
                if request["enabled"] not in {"on", "off"}:
                    raise ValueError()
                enabled = request["enabled"] == "on"
                self.root.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(dir=self.root, prefix=".interface-security-")
                try:
                    with os.fdopen(fd, "w") as stream:
                        json.dump({"lock_enabled": enabled}, stream)
                    os.replace(name, self.root / "interface-security.json")
                finally:
                    Path(name).unlink(missing_ok=True)
                self.lock_enabled = enabled
                if not enabled:
                    self.pin_salt = self.pin_digest = None
                    self.pin_failures = 0
            elif action == "set_interface_pin":
                if not self.lock_enabled:
                    reply["error"] = "interface_lock_disabled"
                    return reply
                self.service.authenticate(request["password"])
                pin = request["pin"]
                if len(pin) != 6 or not pin.isascii() or not pin.isdigit():
                    raise ValueError()
                salt = secrets.token_bytes(16)
                digest = hashlib.scrypt(pin.encode(), salt=salt, n=16384, r=8, p=1)
                self.pin_salt, self.pin_digest = salt, digest
                self.pin_failures = 0
            elif action == "remove_interface_pin":
                self.service.authenticate(request["password"])
                self.pin_salt = self.pin_digest = None
                self.pin_failures = 0
            elif action == "unlock_interface_pin":
                if not self.interface_locked or self.pin_digest is None or self.pin_failures >= 5:
                    reply["error"] = "pin_password_required"
                    return reply
                pin = request["pin"]
                self.pin_failures += 1
                valid = len(pin) == 6 and pin.isascii() and pin.isdigit()
                digest = hashlib.scrypt(pin.encode(), salt=self.pin_salt, n=16384, r=8, p=1) if valid else b""
                if not hmac.compare_digest(digest, self.pin_digest):
                    reply["error"] = "pin_password_required" if self.pin_failures >= 5 else "pin_invalid"
                    return reply
                self.pin_failures = 0
                self.interface_locked = False
            elif action == "lock_interface":
                if not self.lock_enabled:
                    reply["error"] = "interface_lock_disabled"
                    return reply
                if self.service.status()["state"] != "unlocked":
                    raise VaultLockedError()
                self.interface_locked = True
                self.chatgpt.cancel(); self.codex_quota.close()
                self.service.cancel_env_import()
            elif action == "unlock_interface":
                self.service.authenticate(request["password"])
                self.pin_failures = 0
                self.interface_locked = False
            if action in {"create", "unlock"}:
                password = request["password"]
                getattr(self.service, action)(password)
                if self.mobile_access:
                    try:self.mobile_access.restore()
                    except Exception:reply['mobile_error']='mobile_restore_failed'
            elif action == "lock":
                if self.agent:
                    self.agent.stop()
                self.gmail.cancel.set()
                self.chatgpt.cancel(); self.codex_quota.close()
                self.private_mcp.calendar.cancel.set()
                self.private_mcp.clear()
                if self.mobile_access:self.mobile_access.close()
                self.service.lock()
                self.pin_salt = self.pin_digest = None
                self.pin_failures = 0
                self.interface_locked = False
            if action in {"host_mcp_access", "set_host_mcp_access"}:
                if self.service.status()["state"] != "unlocked":
                    raise VaultLockedError()
                from factory.core.mcp_tasks.service import HostVault
                from mcp_access import McpAccess
                if self.host_access is None:
                    host_root = Path(os.environ['FACTORY_GMAIL_HOST_VAULT'])
                    self.host_access = McpAccess(host_root.parent, HostVault(host_root), policy_name='private-access.json')
                if action == 'set_host_mcp_access':
                    self.service.authenticate(request['password'])
                    host_password = (Path(os.environ['FACTORY_GMAIL_HOST_VAULT']) / 'passphrase.txt').read_text().strip()
                    self.host_access.save(request['server'],request['credential_id'],request['access'],host_password)
                reply['result'] = {'mcp_access':self.host_access.status()}
            elif action in {"gmail_status", "gmail_import_host", "gmail_configure", "gmail_begin", "gmail_disconnect"}:
                if self.service.status()["state"] != "unlocked":
                    raise VaultLockedError()
                if action == "gmail_import_host":
                    self.gmail.import_host(request["password"])
                elif action == "gmail_configure":
                    self.gmail.configure(request["source"], request["password"])
                elif action == "gmail_begin":
                    url = self.gmail.begin(request["password"])
                    reply["result"] = {"authorization_url": url}
                    return reply
                elif action == "gmail_disconnect":
                    self.private_mcp.clear()
                    self.gmail.disconnect(request["password"])
                reply["result"] = {"gmail": self.gmail.status()}
            elif action == 'plugins_status':
                reply['result'] = self.plugins.status()
            elif action == 'plugin_action':
                reply['result'] = self.plugins.action(request['plugin'],request['operation'])
            elif action == 'calendar_connect':
                reply['result'] = self.plugins.connect_calendar(request['profile'],lambda:not self.interface_locked)
            elif action == 'calendar_disable':
                if self.service.status()['state']!='unlocked':raise VaultLockedError()
                if self.private_mcp.binding and self.private_mcp.binding[0]=='google_calendar':self.private_mcp.clear()
                self.plugins.settings.update('google_calendar',enabled=False)
                self.private_mcp.calendar.detach()
                reply['result']=self.plugins.status()
            elif action in {'calendar_configure','calendar_begin','calendar_disconnect','calendar_list','calendar_select'}:
                if self.service.status()['state'] != 'unlocked':raise VaultLockedError()
                calendar=self.private_mcp.calendar
                if action=='calendar_configure':calendar.configure(request['source'],request['password'])
                elif action=='calendar_begin':
                    reply['result']={'authorization_url':calendar.begin(request['password'])};return reply
                elif action=='calendar_disconnect':
                    self.service.authenticate(request['password'])
                    self.private_mcp.clear()
                    self.plugins.settings.update('google_calendar',enabled=False)
                    calendar.disconnect(request['password'])
                elif action=='calendar_list':
                    reply['result']={'calendars':calendar.calendars()};return reply
                elif action=='calendar_select':
                    self.private_mcp.clear()
                    calendar.select(request['calendar_id'],request['password'])
                reply['result']=self.plugins.status()
            elif action in {"private_mcp_start", "private_mcp_status", "private_mcp_clear", "private_mcp_approve"}:
                if self.service.status()["state"] != "unlocked":
                    raise VaultLockedError()
                if action == "private_mcp_start":
                    self.private_mcp.start(request["credential_id"], request["server"], request["message"], consumer_check=lambda:not self.interface_locked)
                elif action == "private_mcp_clear":
                    self.private_mcp.clear()
                elif action == 'private_mcp_approve':
                    self.private_mcp.approve(request['request_id'],request['decision'])
                reply["result"] = {"private_mcp": self.private_mcp.status()}
            elif action in {"mcp_access", "set_mcp_access"}:
                from mcp_access import McpAccess
                if self.service.status()["state"] != "unlocked":
                    raise VaultLockedError()
                access = McpAccess(self.root, self.lifecycle)
                value = access.status() if action == "mcp_access" else access.save(request["server"], request["credential_id"], request["access"], request["password"])
                reply["result"] = {"mcp_access": value}
            elif action == "read_legacy_profile":
                if self.service.status()["state"] != "unlocked":
                    raise VaultLockedError()
                from provider_setup import read_legacy_profile
                reply["result"] = {"legacy_profile": read_legacy_profile(request["source"])}
            elif action == "execution_mode":
                reply["result"] = {"execution": self.execution.status()}
            elif action == "select_execution_mode":
                if self.service.status()["state"] != "unlocked":
                    raise VaultLockedError()
                if self.agent and self.agent.status()["running"]:
                    raise ProfileInUseError()
                if self.image.status()["running"]:
                    raise ImageSetupError("image_busy")
                reply["result"] = {"execution": self.execution.select(request["mode"])}
                if self.agent is not None:
                    self.agent.sync_workdir()
            elif action == "image_status":
                reply["result"] = {"image": self.image.status()}
            elif action in {"prepare_image", "recover_image"}:
                if self.service.status()["state"] != "unlocked":
                    raise VaultLockedError()
                if self.agent and self.agent.status()["running"]:
                    raise ProfileInUseError()
                if not Path(request["source"]).is_absolute():
                    raise ValueError()
                if action == "recover_image":
                    result = self.image.start(request["source"], resolve_tool_version(), recover=True)
                else:
                    result = self.image.start(request["source"], resolve_tool_version())
                reply["result"] = {"image": result}
            elif action == "readiness":
                from isolation_readiness import desktop_readiness
                reply["result"] = {"readiness": desktop_readiness(self.root, execution_mode=self.execution.mode)}
            elif action == "change_password":
                if self.agent and self.agent.status()["running"]:
                    raise ProfileInUseError()
                self.service.change_password(request["current"], request["new_password"])
                self.pin_salt = self.pin_digest = None
                self.pin_failures = 0
                reply["result"] = self.service.status()
            elif action=='session_agent':
                if self.service.status()['state']!='unlocked':raise VaultLockedError()
                operation=request['operation']
                if operation not in {'catalog','status','start'}:raise ValueError()
                profiles=[p for p in self.providers.list_profiles() if p['status']=='verified' and p['config'].get('backend')=='cc-backend' and p['config']['name']!='openai-realtime']
                profile=next((p for p in profiles if p['config']['name']==request['profile']),None)
                if operation!='catalog' and profile is None:raise ProfileNotVerifiedError()
                if operation=='start':
                    started=self.dispatch({'id':identity,'action':'start_agent','credential_id':profile['credential_id']})
                    if started.get('error'):return started
                reply['result']={'profiles':[p['config']['name'] for p in profiles], 'agent':self.agent.status() if self.agent else {'running':False,'profile':''}}
            elif action == 'browser_read_session':
                if self.service.status()['state'] != 'unlocked':
                    raise VaultLockedError()
                if not self.agent or not self.agent.available_profiles():
                    reply['error'] = 'agent_not_running'
                    return reply
                if self.agent.execution.mode != 'off':
                    reply['error'] = 'desktop_execution_required'
                    return reply
                browser_service = getattr(self, 'browser_service', None)
                if browser_service is None:
                    raise ValueError()
                from factory.core.prompt_assembly import desktop_browser_read_request
                session = browser_service.create_session(profile=self.agent.profile, cwd=browser_service.workdir,
                    idempotency_key='desktop-browser:' + self.agent.profile)
                session_id = session['session_id']
                browser_service.update_session(session_id, title='Браузер')
                task = browser_service.send_message(session_id, desktop_browser_read_request(), idempotency_key=secrets.token_hex(16))
                reply['result'] = {'session_id': session_id, 'task_id': task['task_id']}
            elif action in {"start_agent", "stop_agent"}:
                if not self.agent:
                    raise ValueError()
                if action == "start_agent":
                    error = self._start_agent_profile(request["credential_id"])
                    if error:
                        reply["error"] = error
                        return reply
                else:
                    self.agent.stop()
                reply["result"] = {**self.service.status(), "profiles": self.providers.list_profiles()}
            elif action == 'prepare_env_import':
                reply['result'] = {'env_import': self.service.prepare_env_import(request['source'])}
            elif action == 'commit_env_import':
                reply['result'] = self.service.commit_env_import(request['draft_id'], request['selection'], request['password'])
            elif action == 'cancel_env_import':
                self.service.cancel_env_import(request['draft_id'])
                reply['result'] = {'canceled': True}
            elif action == "value_access":
                reply["result"] = {"value_access": self.service.value_access(self.root, request["credential_id"])}
            elif action == "grant_value_access":
                reply["result"] = {"value_access": self.service.grant_value_access(self.root, **{key: request[key] for key in expected[action]})}
            elif action == "revoke_value_access":
                reply["result"] = {"value_access": self.service.revoke_value_access(self.root, **{key: request[key] for key in expected[action]})}
            elif action == "list_values":
                reply["result"] = {"values": self.service.list_values()}
            elif action == "save_value":
                saved = self.service.save_value(**{key: request[key] for key in expected[action]})
                reply["result"] = {"saved_value": saved, "values": self.service.list_values()}
            elif action == "delete_value":
                self.service.delete_value(request["credential_id"], request["password"])
                reply["result"] = {"values": self.service.list_values()}
            elif action == "reveal_value":
                reply["result"] = {"secret": self.service.reveal_value(request["credential_id"], request["password"]), "expires_in_seconds": 15}
            elif action in {'mobile_configure','mobile_start','mobile_status','mobile_stop','mobile_revoke'}:
                self.lifecycle._require()
                if self.mobile_access is None:raise ValueError()
                if action=='mobile_configure':reply['result']=self.mobile_access.configure(request['port'],request['public_url'])
                else:reply['result']=self.mobile_access.revoke(request['device_id']) if action=='mobile_revoke' else getattr(self.mobile_access,{'mobile_start':'start','mobile_status':'status','mobile_stop':'disable'}[action])()
            elif action in {'quota_begin','quota_status','quota_cancel','quota_confirm'}:
                self.lifecycle._require()
                profile=request['profile']
                if action=='quota_begin':reply['result']=self.codex_quota.begin(profile)
                elif action=='quota_status':reply['result']=self.codex_quota.status(profile)
                elif action=='quota_confirm':reply['result']=self.codex_quota.confirm(profile,request['link_id'])
                else:self.codex_quota.cancel(profile);reply['result']={'state':'disconnected'}
            elif action in {'chatgpt_begin', 'chatgpt_status', 'chatgpt_cancel', 'chatgpt_models', 'chatgpt_disconnect'}:
                self.lifecycle._require()
                if action == 'chatgpt_begin':
                    profile = next((p for p in self.providers.list_profiles() if p['credential_id'] == request['credential_id']), None)
                    if profile and self.agent and profile['config']['name'] in self.agent.available_profiles():
                        raise ProfileInUseError()
                    reply['result'] = {'authorization_url': self.chatgpt.begin(request['name'], request['credential_id'],
                        consumer_check=lambda: not self.interface_locked)}
                elif action == 'chatgpt_status':
                    reply['result'] = {'chatgpt': self.chatgpt.status(), 'profiles': self.providers.list_profiles()}
                elif action == 'chatgpt_cancel':
                    self.chatgpt.cancel(); self.codex_quota.close()
                    reply['result'] = {'chatgpt': self.chatgpt.status()}
                elif action == 'chatgpt_models':
                    reply['result'] = self.chatgpt.catalog(request['credential_id'])
                else:
                    self.service.authenticate(request['password'])
                    profile = next((p for p in self.providers.list_profiles() if p['credential_id'] == request['credential_id']), None)
                    if profile and self.agent and profile['config']['name'] in self.agent.available_profiles():
                        raise ProfileInUseError()
                    self.chatgpt.cancel(); self.codex_quota.close()
                    reply['result'] = {**self.lifecycle.disconnect_chatgpt(request['credential_id']), 'profiles': self.providers.list_profiles()}
            elif action == "list_profiles":
                reply["result"] = {"profiles": self.providers.list_profiles()}
            elif action == "reveal_profile_key":
                if not any(profile["credential_id"] == request["credential_id"]
                           for profile in self.providers.list_profiles()):
                    raise ValueError()
                secret = self.service.reveal_secret(request["credential_id"], request["password"])
                if secret is None:
                    raise ValueError()
                if secret.metadata.get('desktop_profile', {}).get('api_format') == 'openai-responses':
                    raise ValueError('OAuth credentials cannot be revealed')
                reply["result"] = {"secret": secret.secret, "expires_in_seconds": 15}
            elif action == "model_catalog":
                from provider_setup import model_catalog
                try:
                    reply["result"] = model_catalog(request["preset"], request["api_key"], request["credential_id"], self.lifecycle)
                except VaultLockedError:
                    raise
                except Exception:
                    reply["error"] = "catalog_failed"
            elif action == "check_profile":
                active = self.agent.available_profiles() if self.agent else []
                if active and any(profile["credential_id"] == request["credential_id"]
                                  and profile["config"]["name"] in active
                                  for profile in self.providers.list_profiles()):
                    raise AgentBusyError()
                check = self.providers.check(request["credential_id"])
                reply["result"] = {"check": check, "profiles": self.providers.list_profiles()}
            elif action in {"delete_profile", "rename_profile"}:
                profile = next((p for p in self.providers.list_profiles() if p["credential_id"] == request["credential_id"]), None)
                if profile is None or profile["config"]["name"] == "openai-realtime" or request.get("new_name") == "openai-realtime":
                    raise ValueError()
                if action == 'delete_profile' and profile['config']['api_format'] == 'openai-responses':
                    self.service.authenticate(request['password'])
                    if self.agent:
                        self.agent.prepare_profile_update(profile['config'], profile['config'])
                    self.chatgpt.cancel(); self.codex_quota.close()
                    reply['chatgpt_revoked'] = self.lifecycle.disconnect_chatgpt(request['credential_id'])['revoked']
                result = self.service.manage_provider_profile(request["credential_id"], request["password"],
                    new_name=request.get("new_name"), before_update=self.agent.prepare_profile_update if self.agent else None)
                reply["result"] = {"managed_profile": result, "profiles": self.providers.list_profiles()}
            elif action == "update_profile":
                profile = self.providers.update(**{key: request[key] for key in expected[action]},
                    before_update=self.agent.prepare_profile_update if self.agent else None)
                reply["result"] = {"updated_profile": profile, "profiles": self.providers.list_profiles()}
            elif action == "save_profile":
                profile = self.providers.save(**{key: request[key] for key in expected[action]})
                reply["result"] = {"saved_profile": profile, "profiles": self.providers.list_profiles()}
            elif action == "generate_phrase":
                reply["result"] = {"phrase": self.service.generate_recovery_phrase()}
            elif action == "export_recovery":
                backup = self.service.export_recovery(request["password"], request["recovery_phrase"])
                reply["result"] = {"backup": base64.b64encode(backup).decode("ascii")}
            elif action in {"verify_recovery", "restore_recovery"}:
                encoded = request["backup"]
                if not isinstance(encoded, str) or len(encoded) > ((MAX_RECOVERY_BYTES + 2) // 3) * 4:
                    raise ValueError()
                backup = base64.b64decode(encoded, validate=True)
                if action == "restore_recovery":
                    self.service.restore_recovery(backup, request["recovery_phrase"], request["new_password"])
                    reply["result"] = {**self.service.status(), "restored": True,
                                       "credential_count": len(self.service.list_credentials())}
                    return reply
                # Restore using an unrelated password and remove all temporary files.
                with tempfile.TemporaryDirectory(prefix="igorek-recovery-check-") as folder:
                    restored = VaultLifecycle.restore_recovery(Path(folder) / "vault", backup,
                        request["recovery_phrase"], VaultLifecycle.generate_recovery_phrase())
                    try:
                        records = restored.list_credentials()
                        for record in records:
                            restored.get_secret(record.id)
                        reply["result"] = {"verified": True, "credential_count": len(records)}
                    finally:
                        restored.lock()
            else:
                reply["result"] = self.service.status()
            if "result" in reply and self.agent:
                reply["result"]["agent"] = self.agent.status()
            if "result" in reply and "state" in reply["result"]:
                from onboarding import onboarding_progress
                reply["result"]["onboarding"] = onboarding_progress(
                    self.root, unlocked=reply["result"]["state"] == "unlocked" and not self.interface_locked
                )
                reply["result"]["interface_lock_enabled"] = self.lock_enabled
                reply["result"]["interface_locked"] = self.interface_locked
                reply["result"]["interface_pin_enabled"] = self.pin_digest is not None
                reply["result"]["interface_pin_available"] = self.pin_digest is not None and self.pin_failures < 5
        except IsolationError as error:
            reply["error"] = (error.code if error.code in {
                'desktop_socket_policy_missing', 'desktop_socket_policy_unavailable'
            } else "isolation_unavailable")
        except ImageSetupError:
            reply["error"] = "image_busy"
        except ProfileInUseError:
            reply["error"] = "stop_agent_first"
        except AgentBusyError:
            reply["error"] = "agent_busy"
        except ProfileNotVerifiedError:
            reply["error"] = "profile_not_verified"
        except VaultAuthenticationError:
            reply["error"] = "authentication_failed"
        except VaultBusyError:
            reply["error"] = "vault_busy"
        except VaultMigrationRequired:
            reply["error"] = "migration_required"
        except VaultLockedError:
            reply["error"] = "vault_locked"
        except VaultRecoveryError:
            reply["error"] = "recovery_failed"
        except EnvImportError as error:
            reply['error'] = error.code
            if error.line:
                reply['error_line'] = error.line
        except (UsageError,MobileAccessError) as error:
            reply['error']=error.code
        except DuplicateCredentialError:
            reply["error"] = "profile_exists"
        except FileExistsError:
            reply["error"] = "vault_exists"
        except ValueError as error:
            from factory.core.chatgpt_auth import ChatGPTAuthError
            if isinstance(error, ChatGPTAuthError):
                reply['error'] = error.code
                return reply
            from factory.core.mcp_tasks.calendar import CalendarConnectError
            if isinstance(error,CalendarConnectError):
                reply['error']=error.code
                return reply
            reply["error"] = "invalid_request"
        except Exception:
            # Never forward exception text: it may contain paths or secret inputs.
            reply["error"] = "operation_failed"
        return reply

    def close(self):
        self.chatgpt.cancel(); self.codex_quota.close()
        self.gmail.cancel.set()
        self.private_mcp.calendar.cancel.set()
        self.private_mcp.clear()
        if self.agent:
            self.agent.close()
        self.service.lock()
