"""APK export boundaries, immutable snapshots and idempotent delivery."""
import hashlib
import http.client
import json
import os
import struct
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.core.isolation.apk_delivery import ApkDelivery, apk_metadata, snapshot_apk, validate_endpoint, guest_delivery_environment


def manifest(package='ru.igorek.remote',version='0.1.51',code=52):
    strings=['manifest','package','versionName','versionCode',package,version,'http://schemas.android.com/apk/res/android']
    data=b'';offsets=[]
    for value in strings:
        offsets.append(len(data));encoded=value.encode();data+=bytes([len(value),len(encoded)])+encoded+b'\0'
    start=28+4*len(strings);pool=struct.pack('<HHIIIIII',1,28,start+len(data),len(strings),0,256,start,0)+struct.pack('<'+'I'*len(strings),*offsets)+data
    attrs=b''
    for name,kind,value in ((1,3,4),(2,3,5),(3,16,code)):
        attrs+=struct.pack('<IIIHBBI',0xffffffff if name==1 else 6,name,0xffffffff,8,0,kind,value)
    node=struct.pack('<HHIII',0x102,16,36+len(attrs),1,0xffffffff)+struct.pack('<IIHHHHHH',0xffffffff,0,20,20,3,0,0,0)+attrs
    return struct.pack('<HHI',3,8,8+len(pool)+len(node))+pool+node


@pytest.fixture
def apk(tmp_path):
    relative='android/app/build/outputs/apk/debug/test.apk';path=tmp_path/relative;path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path,'w') as archive: archive.writestr('AndroidManifest.xml',manifest())
    return tmp_path,relative,path,hashlib.sha256(path.read_bytes()).hexdigest()


def test_metadata_snapshot_and_success_receipt_cannot_send_twice(apk,tmp_path):
    root,relative,path,digest=apk;calls=[]
    def sender(file,metadata):
        assert file!=path and file.read_bytes()==path.read_bytes()
        assert metadata=={'package':'ru.igorek.remote','versionName':'0.1.51','versionCode':52}
        calls.append(metadata);return {'ok':True,'curl_exit':0,'message_id':123,'version_name':'0.1.51','version_code':52}
    service=ApkDelivery(root,tmp_path/'receipts',sender=sender)
    try:
        first=service.deliver(relative,digest);second=service.deliver(relative,digest)
        assert first['message_id']==123 and second['already_sent'] is True and len(calls)==1
        with patch('factory.core.isolation.apk_delivery.apk_metadata',return_value=sender.__name__):
            with pytest.raises(ValueError): service.deliver(relative,'0'*64)
    finally: service.close()
    service=ApkDelivery(root,tmp_path/'receipts',sender=sender)
    try: assert service.deliver(relative,digest)['already_sent'] is True and len(calls)==1
    finally: service.close()


@pytest.mark.parametrize('relative',['/etc/passwd','../secret.apk','android/app/build/outputs/apk/../../secret.apk','other.apk','android/app/build/outputs/apk//debug/test.apk'])
def test_paths_outside_build_output_are_denied(apk,relative):
    root,_,_,digest=apk
    with pytest.raises((ValueError,OSError)):
        with snapshot_apk(root,relative,digest): pass


def test_symlinks_hardlinks_and_modified_files_are_not_exported(apk,tmp_path):
    root,relative,path,digest=apk
    copy=tmp_path/'outside.apk';path.rename(copy);path.symlink_to(copy)
    with pytest.raises(OSError):
        with snapshot_apk(root,relative,digest): pass
    path.unlink();os.link(copy,path)
    with pytest.raises(ValueError):
        with snapshot_apk(root,relative,digest): pass
    path.unlink();path.write_bytes(copy.read_bytes()+b'changed')
    with pytest.raises(ValueError):
        with snapshot_apk(root,relative,digest): pass


def test_other_application_and_invalid_archive_are_denied(apk):
    root,relative,path,_=apk
    with zipfile.ZipFile(path,'w') as archive: archive.writestr('AndroidManifest.xml',manifest(package='other.application'))
    with pytest.raises(ValueError): apk_metadata(path)
    path.write_bytes(b'not an APK')
    with pytest.raises(zipfile.BadZipFile): apk_metadata(path)


def test_http_api_does_not_accept_recipient_or_commands(apk,tmp_path):
    root,relative,path,digest=apk;sender_calls=[]
    service=ApkDelivery(root,tmp_path/'receipts',sender=lambda *args:sender_calls.append(args))
    try:
        for payload in ({'relative_path':relative,'sha256':digest,'chat_id':1},{'command':'cat /etc/passwd'},{'relative_path':relative,'sha256':0}):
            connection=http.client.HTTPConnection('127.0.0.1',service.port)
            connection.request('POST','/'+service.nonce+'/send-apk',json.dumps(payload),{'Content-Type':'application/json'})
            response=connection.getresponse();assert response.status==400;response.read();connection.close()
        assert not sender_calls
    finally: service.close()


def test_guest_endpoint_and_environment_fail_closed():
    endpoint='http://10.0.2.2:12345/'+'a'*64+'/send-apk'
    assert validate_endpoint(endpoint)==endpoint
    for value in ('http://192.168.50.10:12345/send-apk',endpoint+'?chat_id=1',endpoint+'/extra'):
        with pytest.raises(ValueError): validate_endpoint(value)
    with patch.dict(os.environ,{},clear=True):
        with guest_delivery_environment(endpoint): assert os.environ['IGOREK_APK_DELIVERY_URL']==endpoint
        assert 'IGOREK_APK_DELIVERY_URL' not in os.environ


def test_failed_send_does_not_publish_success_receipt(apk,tmp_path):
    root,relative,path,digest=apk
    service=ApkDelivery(root,tmp_path/'receipts',sender=lambda *_:{'ok':False,'curl_exit':1})
    try:
        with pytest.raises(ValueError): service.deliver(relative,digest)
        assert not (tmp_path/'receipts'/(digest+'.json')).exists()
    finally: service.close()


def test_untrusted_filename_cannot_become_curl_form_options(apk):
    root,relative,path,_=apk
    hostile=path.with_name('test;headers=@secret.apk');path.rename(hostile)
    relative=hostile.relative_to(root).as_posix();digest=hashlib.sha256(hostile.read_bytes()).hexdigest()
    with snapshot_apk(root,relative,digest) as (snapshot,metadata):
        assert snapshot.name=='app-debug.apk' and ';' not in str(snapshot)


def test_payload_delivery_endpoint_is_validated_and_optional():
    from factory.core.isolation.history import HostTaskInput,build_wire_task_payload,rehydrate_payload,PayloadError
    from factory.core.isolation.guest_task import GuestTask
    fields=dict(task_id='t',profile='p',model='m',base_url='http://localhost/v1',api_format='openai',system_prompt='',user_message='',skills_text='',foundation_context='',filesystem_mode='workspace',guard_mode='on',max_tokens=1024,timeout_s=60,workdir='/workspace',artifact_workdir='/workspace/artifacts')
    endpoint='http://10.0.2.2:12345/'+'b'*64+'/send-apk'
    wire=build_wire_task_payload(HostTaskInput(**fields,apk_delivery_url=endpoint))
    assert GuestTask.from_wire(wire,guest_roots=('/workspace',)).apk_delivery_url==endpoint
    assert rehydrate_payload(build_wire_task_payload(HostTaskInput(**fields))).apk_delivery_url==''
    wire['apk_delivery_url']='http://192.168.50.10:8034/other'
    with pytest.raises(PayloadError): rehydrate_payload(wire)


def test_telegram_delivery_keeps_token_off_argv_and_checks_sender_result(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from factory.core.isolation.apk_delivery import telegram_send
    service=SimpleNamespace(broker=SimpleNamespace(get_ephemeral_env=lambda **kw:SimpleNamespace(allowed=True,env={'FACTORY_TELEGRAM_BOT_TOKEN':'TEST_TOKEN'})))
    monkeypatch.setenv('FACTORY_TELEGRAM_ALLOWED_USER_IDS','12345678')
    monkeypatch.delenv('FACTORY_TELEGRAM_ALLOWED_CHAT_IDS',raising=False)
    monkeypatch.delenv('IGOREK_APK_DELIVERY_CHAT_ID',raising=False)
    replies=[SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'result':{'message_id':1}})),SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'result':{'message_id':2}}))]
    with patch('factory.core.vault.runtime.build_services',return_value=service),patch('factory.core.isolation.apk_delivery.subprocess.run',side_effect=replies) as run:
        result=telegram_send(tmp_path/'app-debug.apk',{'versionName':'0.1.51','versionCode':52})
    assert result['message_id']==2 and result['ok'] is True
    assert all('TEST_TOKEN' not in ' '.join(call.args[0]) for call in run.call_args_list)
    assert 'document=@' + str(tmp_path/'app-debug.apk') in run.call_args_list[1].args[0]
    assert '12345678' in run.call_args_list[0].args[0][-1]


@pytest.mark.parametrize('allowed,explicit,error',[
    ('','','apk_delivery_recipient_required'),
    ('12345678,23456789','','apk_delivery_recipient_required'),
    ('12345678','23456789','apk_delivery_recipient_not_allowed'),
])
def test_telegram_delivery_requires_an_unambiguous_allowed_recipient(monkeypatch,allowed,explicit,error):
    from factory.core.isolation.apk_delivery import telegram_recipient
    monkeypatch.setenv('FACTORY_TELEGRAM_ALLOWED_USER_IDS',allowed)
    monkeypatch.delenv('FACTORY_TELEGRAM_ALLOWED_CHAT_IDS',raising=False)
    monkeypatch.setenv('IGOREK_APK_DELIVERY_CHAT_ID',explicit)
    with pytest.raises(ValueError,match=error):telegram_recipient()


def test_telegram_delivery_can_select_one_configured_recipient(monkeypatch):
    from factory.core.isolation.apk_delivery import telegram_recipient
    monkeypatch.setenv('FACTORY_TELEGRAM_ALLOWED_USER_IDS','12345678,23456789')
    monkeypatch.setenv('FACTORY_TELEGRAM_ALLOWED_CHAT_IDS','')
    monkeypatch.setenv('IGOREK_APK_DELIVERY_CHAT_ID','23456789')
    assert telegram_recipient()==23456789
