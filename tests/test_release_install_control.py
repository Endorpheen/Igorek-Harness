"""Update-helper orchestration over dummy packages; installers and restarts are fake."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
from test_release_install import update


@pytest.fixture
def request_job(tmp_path):
    directory=tmp_path/'jobs/fixture';directory.mkdir(parents=True)
    data=tmp_path/'data';data.mkdir()
    target=tmp_path/('Install' if sys.platform=='win32' else 'Igorek.AppImage')
    if sys.platform=='win32':
        target.mkdir();(target/'igorek.exe').write_bytes(b'old')
    else:target.write_bytes(b'old')
    suffix='win-x64.exe' if sys.platform=='win32' else 'linux-x86_64.AppImage'
    package=directory/('igorek-1.2.3-'+suffix);package.write_bytes(b'payload')
    value={'schema':1,'kind':'windows' if sys.platform=='win32' else 'appimage','file':str(package),'size':7,'sha256':hashlib.sha256(b'payload').hexdigest(),'version':'1.2.3','target':str(target),'parent_pid':os.getpid()+1000000,'restart':False,'data_dir':str(data)}
    request=directory/'request.json';request.write_text(json.dumps(value))
    return request,value


def test_valid_owned_request_keeps_exact_installation_metadata(request_job):
    path,value=request_job
    assert update.read_request(path)==value


@pytest.mark.parametrize('field,value',[
    ('schema',True),('schema',2),('kind','unknown'),('size',0),('size',-1),('size',2*1024**3+1),
    ('sha256','A'*64),('sha256','short'),('version','1.2'),('version','../1.2.3'),
    ('restart',1),('parent_pid',True),('parent_pid',1),
])
def test_invalid_request_fields_are_rejected_before_package_execution(request_job,field,value):
    path,body=request_job;body[field]=value;path.write_text(json.dumps(body))
    with pytest.raises((ValueError,TypeError)):update.read_request(path)


def test_boolean_byte_count_is_not_an_integer_package_size(request_job):
    path,body=request_job;package=Path(body['file']);package.write_bytes(b'x')
    body['size']=True;body['sha256']=hashlib.sha256(b'x').hexdigest();path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='invalid_update_request'):update.read_request(path)


@pytest.mark.parametrize('field',['file','target','data_dir'])
def test_paths_cannot_escape_owned_job_or_use_relative_locations(request_job,field):
    path,body=request_job;body[field]='relative/fixture';path.write_text(json.dumps(body))
    with pytest.raises(ValueError):update.read_request(path)


@pytest.mark.parametrize('state',['success','parent-live','install-failed','bad-request'])
@pytest.mark.parametrize('restart',[False,True])
def test_controller_writes_truthful_result_and_restarts_only_after_parent_exit(request_job,monkeypatch,state,restart):
    path,body=request_job;body['restart']=restart
    if state=='bad-request':body['schema']=0
    path.write_text(json.dumps(body))
    wait=Mock(side_effect=TimeoutError('fixture parent live') if state=='parent-live' else None)
    apply=Mock(side_effect=ValueError('fixture install failed') if state=='install-failed' else None,return_value={'state':'installed','version':'1.2.3'})
    spawn=Mock();close=Mock()
    monkeypatch.setattr(update,'wait_for_exit',wait);monkeypatch.setattr(update,'apply',apply)
    monkeypatch.setattr(update,'close_inherited_installation_lock',close);monkeypatch.setattr(update.subprocess,'Popen',spawn)
    code=update.main(path)
    result=json.loads(path.with_suffix('.result.json').read_text())
    assert code==(0 if state=='success' else 1) and result['state']==('installed' if state=='success' else 'failed')
    exited=state in {'success','install-failed'}
    assert (path.parent.parent/'last-install-result.json').exists()==exited
    assert spawn.call_count==int(exited and restart)
    assert apply.call_count==int(exited)
    assert wait.call_count==int(state!='bad-request')
    if spawn.called:
        assert spawn.call_args.args[0][-2:]==['--data-dir',body['data_dir']]
        assert spawn.call_args.kwargs['close_fds'] is True


@pytest.mark.skipif(sys.platform!='linux',reason='Linux snapshot extraction and installation-lock protocol')
@pytest.mark.parametrize('version_ok',[False,True])
def test_snapshot_extract_verifies_version_before_invoking_installer(tmp_path,monkeypatch,version_ok):
    target=tmp_path/'install';target.mkdir()
    (target/'installation.json').write_text(json.dumps({'schema_version':1,'kind':'igorek-linux-user-installation'}))
    (target/'run.lock').write_bytes(b'')
    package=tmp_path/'fixture.deb';package.write_bytes(b'not an executable package')
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if command[0]=='/usr/bin/dpkg-deb':
            root=Path(command[-1])/'opt/igorek-desktop-installer';root.mkdir(parents=True)
            (root/'verify-package.py').write_text("def verify(root):\n    return {'factory_version':"+repr('1.2.3' if version_ok else '0.0.0')+"}\n")
            (root/'install-linux.py').write_text('# synthetic installer; never executed\n')
        else:assert command[:3]==[sys.executable,'-I','-B']
        return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(update.subprocess,'run',run)
    if version_ok:
        assert update.install_snapshot(package,target,'1.2.3')==target/'igorek'
        assert len(calls)==2 and calls[-1][-2:]==['--prefix',str(target)]
    else:
        with pytest.raises(ValueError,match='version_mismatch'):update.install_snapshot(package,target,'1.2.3')
        assert len(calls)==1
    assert not list(tmp_path.glob('extracted-*'))
