"""Persistent plugin switches and owner-side connection checks."""
import json
import os
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
from .remote import RemoteMcpClient
from .calendar import CalendarConnection
from ..vault.lifecycle import VaultLockedError

PLUGIN_IDS = ('openai_docs','google_calendar')


class PluginSettings:
    def __init__(self,root):self.path=Path(root)/'mcp/plugins.json';self.lock=threading.RLock()

    def read(self):
        if not self.path.exists():return {}
        if self.path.is_symlink() or self.path.stat().st_size>65536:raise ValueError('Invalid plugin settings')
        value=json.loads(self.path.read_text())
        if not isinstance(value,dict) or value.get('version')!=1 or not isinstance(value.get('plugins'),dict):raise ValueError('Invalid plugin settings')
        return {key:entry for key,entry in value['plugins'].items() if key in PLUGIN_IDS and isinstance(entry,dict)}

    def enabled(self,identity):return self.read().get(identity,{}).get('enabled') is True

    def update(self,identity,**fields):
        if identity not in PLUGIN_IDS:raise ValueError('Unknown plugin')
        with self.lock:
            values=self.read();values[identity]={**values.get(identity,{}),**fields}
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd,name=tempfile.mkstemp(dir=self.path.parent,prefix='.plugins-')
            try:
                with os.fdopen(fd,'w') as stream:json.dump({'version':1,'plugins':values},stream)
                os.replace(name,self.path)
            finally:Path(name).unlink(missing_ok=True)

    def registry(self):
        adapters={'openai_docs':'openai_docs','google_calendar':'google_calendar'}
        return {identity:{'type':'builtin','adapter':adapters[identity],'disabled':entry.get('enabled') is not True} for identity,entry in self.read().items()}


class Plugins:
    def __init__(self,root,lifecycle,calendar=None):
        self.root=Path(root);self.settings=PluginSettings(root);self.lifecycle=lifecycle;self.calendar=calendar or CalendarConnection(lifecycle)

    def status(self):
        values=self.settings.read();unlocked=self.lifecycle.state=='unlocked'
        calendar=self.calendar.status() if unlocked else {'state':'vault_locked'}
        from .access import McpAccess
        access=McpAccess(self.root,self.lifecycle)
        profiles=self.lifecycle.list_provider_profiles() if unlocked else []
        grants=access.grants() if unlocked else {}
        return {'plugins':{identity:{'enabled':values.get(identity,{}).get('enabled') is True,'check':values.get(identity,{}).get('check'),
            'model_access':[{'profile':p['config']['name'],'access':grants[identity][p['credential_id']]['access']} for p in profiles
                            if p['status']=='verified' and p['config'].get('api_format') in {'openai','anthropic'} and access.authorize(identity,p['config'],private=True)],
            **(calendar if identity=='google_calendar' else {})} for identity in PLUGIN_IDS},'vault_unlocked':unlocked,
            'profiles':[{'name':p['config']['name']} for p in profiles if p['status']=='verified' and p['config'].get('api_format') in {'openai','anthropic'}]}

    def connect_calendar(self,profile,consumer_valid=lambda:True):
        from .access import McpAccess
        from .calendar import CalendarConnectError, connection_error
        if self.lifecycle.state!='unlocked':raise VaultLockedError()
        binding=next((p for p in self.lifecycle.list_provider_profiles() if p['config']['name']==profile and p['status']=='verified' and p['config'].get('api_format') in {'openai','anthropic'}),None)
        if binding is None:raise CalendarConnectError('model_profile_required')
        if self.calendar.thread and self.calendar.thread.is_alive():raise CalendarConnectError('operation_busy')
        self.calendar.connect_profile=binding['config']['name']
        self.calendar.cancel.clear()
        binding=deepcopy(binding);access=McpAccess(self.root,self.lifecycle)
        def allowed():
            return not self.calendar.cancel.is_set() and self.lifecycle.state=='unlocked' and consumer_valid() and any(p['credential_id']==binding['credential_id'] and p['config']==binding['config'] and p['status']=='verified' for p in self.lifecycle.list_provider_profiles())
        def connected():
            if not allowed():raise CalendarConnectError('mcp_access_denied')
            check={**self.calendar.check(),'checked_at':int(time.time())}
            if not allowed():raise CalendarConnectError('mcp_access_denied')
            grant=access.grants().get('google_calendar',{}).get(binding['credential_id'],{})
            mode='local_only' if grant.get('config')==binding['config'] and grant.get('access')=='local_only' else 'allow'
            access._save_owner_grant('google_calendar',binding['credential_id'],mode)
            self.settings.update('google_calendar',enabled=True,check=check)
        self.settings.update('google_calendar',enabled=False)
        if self.calendar.status()['connected']:
            try:
                connected();self.calendar.pending=None;return self.status()
            except Exception as error:
                code=connection_error(error);self.calendar.pending={'error':code,'activation_url':getattr(error,'activation_url','')}
                raise CalendarConnectError(code) from None
        return {'authorization_url':self.calendar.automatic_begin(connected,allowed)}

    def action(self,identity,operation):
        if self.lifecycle.state!='unlocked':raise VaultLockedError()
        if identity not in PLUGIN_IDS or operation not in {'connect','check','disconnect'}:raise ValueError('Invalid plugin operation')
        if operation=='disconnect':
            self.settings.update(identity,enabled=False)
            return self.status()
        try:
            if identity=='openai_docs':
                client=RemoteMcpClient(lambda:self.lifecycle.state=='unlocked')
                try:
                    client.start();raw=client.call_tool('search_openai_docs',{'query':'Responses API streaming','limit':2})
                    result=json.loads(raw);hits=result.get('hits',[]);links=[]
                    for hit in hits:
                        url=hit.get('url') if isinstance(hit,dict) else None
                        if isinstance(url,str) and urlsplit(url).scheme=='https' and urlsplit(url).hostname in {'developers.openai.com','platform.openai.com','learn.chatgpt.com'}:
                            hierarchy=hit.get('hierarchy') or {};links.append({'url':url,'title':str(hierarchy.get('lvl1') or url)[:300]})
                    if not links:raise ValueError('No documentation results')
                    check={'ok':True,'checked_at':int(time.time()),'query':'Responses API streaming','links':links[:2]}
                finally:client.stop()
            else:
                check={**self.calendar.check(),'checked_at':int(time.time())}
            if self.lifecycle.state!='unlocked':raise VaultLockedError()
            self.settings.update(identity,check=check,**({'enabled':True} if operation=='connect' else {}))
        except VaultLockedError:raise
        except Exception:
            self.settings.update(identity,check={'ok':False,'checked_at':int(time.time()),'code':'check_failed'})
        return self.status()
