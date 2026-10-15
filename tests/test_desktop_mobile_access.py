import json
import ssl
import sys
import urllib.request
import urllib.error
from pathlib import Path
import pytest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))


def test_network_address_lookup_resolves_system_binary_outside_runtime_path(monkeypatch):
    import mobile_access
    from types import SimpleNamespace
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    interfaces = [{'flags': ['UP'], 'addr_info': [{'family': 'inet', 'local': '192.168.0.155'}, {},
        {'family': 'inet6', 'local': '::1'}]}]
    with patch.object(mobile_access.shutil, 'which', return_value='/usr/sbin/ip') as lookup, \
         patch.object(mobile_access.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(interfaces))) as run:
        assert mobile_access.network_addresses() == ['192.168.0.155']
        lookup.assert_called_once_with('ip', path='/usr/sbin:/sbin:/usr/bin:/bin')
        assert run.call_args.args[0][0] == '/usr/sbin/ip'
from mobile_access import DesktopMobileAccess


def test_desktop_migrates_random_port_and_persists_explicit_public_settings(tmp_path):
    from types import SimpleNamespace
    bridge=SimpleNamespace(store=SimpleNamespace(list_devices=lambda **_:[]))
    (tmp_path/'mobile-access.json').write_text(json.dumps({'port':42975}))
    access=DesktopMobileAccess(bridge,tmp_path/'cert',tmp_path/'key',tmp_path)
    assert access.port==47823
    configured=access.configure('49123','198.51.100.42:47823/')
    assert configured['public_url']=='https://198.51.100.42:47823'
    restored=DesktopMobileAccess(bridge,tmp_path/'cert',tmp_path/'key',tmp_path)
    assert restored.port==49123 and restored.public_url==configured['public_url']
    assert (tmp_path/'mobile-access.json').stat().st_mode & 0o777==0o600
    restored.configure('47823','')
    assert DesktopMobileAccess(bridge,tmp_path/'cert',tmp_path/'key',tmp_path).public_url==''


@pytest.mark.parametrize('port,url',[
    ('0',''),('80',''),('65536',''),('1.5',''),('１２３４',''),
    ('47823','http://example.com'),('47823','https://user:pass@example.com'),
    ('47823','https://example.com/path'),('47823','https://example.com?token=private'),
    ('47823','https://example.com#private'),('47823','https://example.com:0'),
    ('47823','https://example.com?'),('47823','https://example.com#'),('47823','https://example.com\\bad'),
    ('47823','https://example.com:65536'),('47823','https://example.com\n'),
])
def test_invalid_network_settings_do_not_replace_saved_configuration(tmp_path,port,url):
    from types import SimpleNamespace
    from mobile_access import MobileAccessError
    bridge=SimpleNamespace(store=SimpleNamespace(list_devices=lambda **_:[]))
    access=DesktopMobileAccess(bridge,tmp_path/'cert',tmp_path/'key',tmp_path)
    access.configure('47823','https://198.51.100.42:47823')
    before=access.settings.read_bytes()
    with pytest.raises(MobileAccessError):access.configure(port,url)
    assert access.settings.read_bytes()==before and access.port==47823


def test_mobile_desktop_requires_pairing_and_returns_its_active_codex_profile(tmp_path,monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME',str(tmp_path))
    monkeypatch.setenv('FACTORY_DATA_DIR',str(tmp_path))
    from factory.core.mobile_bridge.server import MobileBridge,BridgeConfig
    from factory.web_ui.server import _certificate_paths
    bridge=MobileBridge(config=BridgeConfig(host='127.0.0.1',port=0,workdir=str(tmp_path)))
    bridge.service.profile='codexpro';bridge.service.available_profiles=lambda:['codexpro']
    cert,key,_=_certificate_paths();access=DesktopMobileAccess(bridge,cert,key,tmp_path);access.port=0
    assert access.status()=={'enabled':False,'devices':[],'port':0,'public_url':'','auto_resume':False,'restore_error':''}
    context=ssl._create_unverified_context()
    try:
        started=access.start();port=started['port'];url=f'https://127.0.0.1:{port}/mobile/v1'
        access.configure(str(port),'https://198.51.100.42:47823')
        with patch('mobile_access.network_addresses',return_value=[]):
            public=access.start()
        assert public['qr']['url']=='https://198.51.100.42:47823'
        assert public['port']==port
        started=public
        assert started['profiles']==['codexpro'] and len(started['fingerprint'])==64
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(url+'/session-options',context=context)
        assert denied.value.code==401
        request=urllib.request.Request(url+'/pair/complete',data=json.dumps({'code':started['pairing']['code'],'name':'fixture-phone','platform':'android'}).encode(),headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(request,context=context) as response:paired=json.load(response)
        request=urllib.request.Request(url+'/session-options',headers={'Authorization':'Bearer '+paired['token']})
        with urllib.request.urlopen(request,context=context) as response:options=json.load(response)
        assert options['profiles']==['codexpro'] and options['default_profile']=='codexpro'
        assert 'access_token' not in str(options) and 'refresh_token' not in str(options)
        bridge.service.profile_usage=lambda profile,force=False:{'provider':'codex','profile':profile,'usage':{'weekly':{'percent':37}}}
        request=urllib.request.Request(url+'/provider-usage?profile=codexpro',headers={'Authorization':'Bearer '+paired['token']})
        with urllib.request.urlopen(request,context=context) as response:quota=json.load(response)
        assert quota['provider']=='codex' and quota['usage']['weekly']['percent']==37
        request=urllib.request.Request(url+'/provider-usage?profile=other',headers={'Authorization':'Bearer '+paired['token']})
        with pytest.raises(urllib.error.HTTPError) as refused:urllib.request.urlopen(request,context=context)
        assert refused.value.code==404
        from types import SimpleNamespace
        voice_closed=[]
        bridge.voice=SimpleNamespace(start_session=lambda sid,tier:{'call_id':'phone-call'},
            hangup=lambda call:voice_closed.append(call) or {'ok':True},relay_sdp=lambda *a:pytest.fail('foreign voice call accessed'))
        bridge.service.get_session=lambda sid:{}
        voice_request=urllib.request.Request(url+'/voice/session',data=json.dumps({'session_id':'fixture'}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+paired['token']},method='POST')
        with urllib.request.urlopen(voice_request,context=context) as response:assert json.load(response)['call_id']=='phone-call'
        bridge.voice_devices['other-call']='another-device'
        forbidden=urllib.request.Request(url+'/voice/sdp',data=json.dumps({'call_id':'other-call','sdp':'v=0'}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+paired['token']},method='POST')
        with pytest.raises(urllib.error.HTTPError) as foreign:urllib.request.urlopen(forbidden,context=context)
        assert foreign.value.code==403
        stream_request=urllib.request.Request(url+'/events?after='+str(bridge.store.max_seq()),headers={'Authorization':'Bearer '+paired['token']})
        stream=urllib.request.urlopen(stream_request,context=context,timeout=4)
        assert b'stream.ready' in stream.readline();stream.readline()
        access.revoke(paired['device_id'])
        assert stream.readline()==b'';stream.close()
        assert voice_closed==['phone-call'] and 'other-call' in bridge.voice_devices
        with pytest.raises(urllib.error.HTTPError) as revoked:urllib.request.urlopen(request,context=context)
        assert revoked.value.code==401
        assert access.status()['devices'][0]['revoked_at'] is not None
        access.close();access.start();assert access.status()['port']==port
    finally:access.close();bridge.stop()


def test_mobile_listener_restores_without_new_pairing_and_explicit_disable_is_persistent(tmp_path,monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME',str(tmp_path));monkeypatch.setenv('FACTORY_DATA_DIR',str(tmp_path))
    from factory.core.mobile_bridge.server import MobileBridge,BridgeConfig
    from factory.web_ui.server import _certificate_paths
    bridge=MobileBridge(config=BridgeConfig(host='127.0.0.1',port=0,workdir=str(tmp_path)))
    cert,key,_=_certificate_paths();access=DesktopMobileAccess(bridge,cert,key,tmp_path);access.port=0
    context=ssl._create_unverified_context();restored=None;disabled=None
    try:
        started=access.start();port=started['port']
        request=urllib.request.Request(f'https://127.0.0.1:{port}/mobile/v1/pair/complete',
            data=json.dumps({'code':started['pairing']['code'],'name':'fixture-phone','platform':'android'}).encode(),
            headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(request,context=context) as response:paired=json.load(response)
        access.close()
        restored=DesktopMobileAccess(bridge,cert,key,tmp_path)
        with patch.object(bridge.store,'create_pairing_code',side_effect=AssertionError('Restore must not issue QR')):
            assert restored.restore()['enabled'] is True
        request=urllib.request.Request(f'https://127.0.0.1:{port}/mobile/v1/session-options',headers={'Authorization':'Bearer '+paired['token']})
        with urllib.request.urlopen(request,context=context) as response:assert response.status==200
        restored.disable()
        disabled=DesktopMobileAccess(bridge,cert,key,tmp_path)
        assert disabled.restore()['enabled'] is False and not disabled.resume_enabled
    finally:
        if disabled:disabled.close()
        if restored:restored.close()
        access.close();bridge.stop()


def test_restore_failure_is_reported_without_changing_remembered_permission(tmp_path):
    from types import SimpleNamespace
    from mobile_access import MobileAccessError
    bridge=SimpleNamespace(store=SimpleNamespace(list_devices=lambda **_:[]))
    access=DesktopMobileAccess(bridge,tmp_path/'cert',tmp_path/'key',tmp_path);access.resume_enabled=True;access.save()
    with patch.object(access,'listen',side_effect=MobileAccessError('mobile_port_unavailable')):
        state=access.restore()
    assert state['enabled'] is False and state['restore_error']=='mobile_port_unavailable'
    assert DesktopMobileAccess(bridge,tmp_path/'cert',tmp_path/'key',tmp_path).resume_enabled
