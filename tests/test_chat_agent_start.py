import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from vault_owner import OwnerVault
from agent_runtime import DesktopAgentRuntime
from factory.core.schema import initialize_database


class Worker:
    instances=[]
    def __init__(self,profile,**kwargs):self.profile=profile;self.instances.append(self)
    def run(self,*,stop_event,execution_lock):stop_event.wait()


def test_start_profile_from_chat_reuses_worker_and_refuses_unverified_or_locked(tmp_path,monkeypatch):
    monkeypatch.setenv('FACTORY_DATA_DIR',str(tmp_path));monkeypatch.setenv('IGOREK_FACTORY_HOME',str(tmp_path))
    initialize_database(str(tmp_path/'database/factory.db'))
    owner=OwnerVault(tmp_path);owner.service.create('fixture-password')
    owner.agent=DesktopAgentRuntime(tmp_path,owner.lifecycle,SimpleNamespace(profile=''),worker_factory=Worker,execution=owner.execution)
    p=owner.providers.save('fixture-model','fixture','https://example.test/v1','openai','fixture-key')
    call=lambda op,profile='fixture-model':owner.dispatch({'id':'x','action':'session_agent','operation':op,'profile':profile})
    try:
        assert call('start')['error']=='profile_not_verified'
        owner.lifecycle.check_provider_profile(p['credential_id'],lambda *_:{'status':'verified'})
        assert call('catalog','')['result']['profiles']==['fixture-model']
        assert call('status')['result']['agent']['running'] is False
        assert call('start')['result']['agent']=={'running':True,'profile':'fixture-model'}
        worker=Worker.instances[-1];assert call('start')['result']['agent']['running'] and Worker.instances[-1] is worker
        owner.interface_locked=True
        assert call('status')['result']['agent']['running']
        assert call('catalog','')['result']['profiles']==['fixture-model']
        assert call('start')['error']=='interface_locked'
        owner.interface_locked=False
        assert 'fixture-key' not in json.dumps(call('status'))
        owner.agent.execution_lock.acquire()
        try:assert call('start','other')['error']=='profile_not_verified'
        finally:owner.agent.execution_lock.release()
        owner.dispatch({'id':'x','action':'lock'})
        assert call('start')['error']=='vault_locked'
    finally:owner.close()
