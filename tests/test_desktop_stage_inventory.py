"""Shared staging copies only reviewed dummy sources, never an installed runtime."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_desktop_cross_platform_package import load


@pytest.fixture
def stage_fixture(tmp_path,monkeypatch):
    module=load('stage-app')
    repo=tmp_path/'repo'
    desktop=repo/'desktop'
    (desktop/'packaging').mkdir(parents=True)
    monkeypatch.setattr(module,'__file__',str(desktop/'packaging/stage-app.py'))
    (repo/'pyproject.toml').write_text('[project]\nversion="1.2.3"\n')
    for name in set(module.APP_FILES)|set(module.BACKEND_FILES):
        (desktop/name).write_bytes(('fixture '+name).encode())
    for name in module.LEGAL_FILES:
        target=repo/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('fixture legal '+name)
    assets=desktop/'assets';assets.mkdir()
    for name in ['release-notes.json','evil-octopus.png','evil-octopus-wink.png','igorek.png','github.svg','OPENAI-ICON-LICENSE.txt','GITHUB-ICON-LICENSE.txt']:
        (assets/name).write_text('fixture '+name)
    source=repo/'factory/fixture.py';source.parent.mkdir();source.write_text('fixture = True\n')
    runtime=tmp_path/'runtime';runtime.mkdir()
    for name in module.BACKEND_FILES:
        (runtime/name).write_bytes((desktop/name).read_bytes())
    python=runtime/'python/bin/python3.14';python.parent.mkdir(parents=True);python.write_text('dummy executable never launched')
    manifest={'schema_version':1,'factory_version':'1.2.3','platform':'linux-x64','python':'python/bin/python3.14','entry':'backend.py','backend_sources':{name:hashlib.sha256((desktop/name).read_bytes()).hexdigest() for name in module.BACKEND_FILES},'source_commit':'a'*40,'source_modified':False}
    provenance={'sources':{'factory/fixture.py':hashlib.sha256(source.read_bytes()).hexdigest()}}
    def save():
        (runtime/'manifest.json').write_text(json.dumps(manifest));(runtime/'source-provenance.json').write_text(json.dumps(provenance))
    save()
    return SimpleNamespace(module=module,repo=repo,desktop=desktop,runtime=runtime,output=tmp_path/'output',manifest=manifest,provenance=provenance,save=save)


@pytest.mark.parametrize('target',['linux-x64','windows-x64'])
def test_staging_manifest_matches_every_copied_file_and_ignores_bytecode(stage_fixture,target):
    f=stage_fixture
    if target=='windows-x64':
        f.manifest.update(platform=target,python='python/python.exe')
        (f.runtime/'python/python.exe').write_text('dummy executable never launched')
        f.save()
    cache=f.runtime/'__pycache__';cache.mkdir();(cache/'fixture.pyc').write_text('do not copy')
    (f.runtime/'loose.pyc').write_text('do not copy')
    f.module.stage(f.runtime,f.output,target)
    assert not (f.output/'runtime/__pycache__').exists() and not (f.output/'runtime/loose.pyc').exists()
    package=json.loads((f.output/'package.json').read_text())
    assert package['version']=='1.2.3' and package['main']=='main.cjs' and package['dependencies']=={}
    inventory=json.loads((f.output/'source-manifest.json').read_text())
    assert inventory['platform']==target and inventory['source_commit']=='a'*40 and inventory['source_modified'] is False
    actual={p.relative_to(f.output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in f.output.rglob('*') if p.is_file() and p.name!='source-manifest.json'}
    assert inventory['files']==actual
    assert set(f.module.APP_FILES)<=set(inventory['files'])
    for name in f.module.LEGAL_FILES:
        assert (f.output/'licenses'/Path(name).name).read_text()=='fixture legal '+name


@pytest.mark.parametrize('field,value,reason',[('factory_version','9.9.9','version'),('schema_version',2,'version'),('python','python/bin/python3.13','Linux'),('entry','../outside.py','entry path'),('entry','/outside.py','entry path'),('entry','C:/outside.py','entry path'),('entry','directory\\entry.py','entry path'),('entry','missing.py','missing')])
def test_invalid_runtime_manifest_is_rejected_before_copy(stage_fixture,field,value,reason):
    f=stage_fixture;f.manifest[field]=value;f.save()
    with pytest.raises(ValueError,match=reason):f.module.stage(f.runtime,f.output,'linux-x64')
    assert not f.output.exists()


def test_existing_staging_directory_is_preserved(stage_fixture):
    f=stage_fixture;f.output.mkdir();marker=f.output/'owner';marker.write_text('keep')
    with pytest.raises(FileExistsError):f.module.stage(f.runtime,f.output,'linux-x64')
    assert marker.read_text()=='keep' and list(f.output.iterdir())==[marker]


@pytest.mark.parametrize('side',['inventory','reviewed','runtime','provenance','missing-source'])
def test_stale_or_incomplete_source_inventory_blocks_packaging(stage_fixture,side):
    f=stage_fixture
    if side=='inventory':f.manifest['backend_sources'].pop(next(iter(f.manifest['backend_sources'])))
    elif side=='reviewed':(f.desktop/f.module.BACKEND_FILES[0]).write_text('changed after review')
    elif side=='runtime':(f.runtime/f.module.BACKEND_FILES[0]).write_text('stale bundled bytes')
    elif side=='provenance':(f.repo/'factory/fixture.py').write_text('changed after runtime build')
    else:(f.repo/'factory/fixture.py').unlink()
    f.save()
    with pytest.raises(ValueError):f.module.stage(f.runtime,f.output,'linux-x64')
    assert not f.output.exists()


@pytest.mark.parametrize('private_name',['private.db','private.sqlite','private.sqlite3','trace.jsonl','.env','auth.json','passphrase.txt'])
def test_private_runtime_state_is_never_copied_into_staged_application(stage_fixture,private_name):
    f=stage_fixture;(f.runtime/private_name).write_text('synthetic private fixture')
    with pytest.raises(ValueError,match='private data'):f.module.stage(f.runtime,f.output,'linux-x64')
    assert not f.output.exists()


@pytest.mark.parametrize('legacy',['manifest','file'])
def test_legacy_quota_executable_is_not_bundled(stage_fixture,legacy):
    f=stage_fixture
    if legacy=='manifest':f.manifest['codex_quota']='legacy';f.save()
    else:(f.runtime/'codex-quota').write_text('legacy dummy')
    with pytest.raises(ValueError,match='Legacy'):f.module.stage(f.runtime,f.output,'linux-x64')
    assert not f.output.exists()


@pytest.mark.skipif(os.name!='posix',reason='POSIX temporary symlink staging contract')
@pytest.mark.parametrize('kind',['external','broken','source','asset','provenance'])
def test_symlinks_cannot_substitute_reviewed_sources_or_escape_runtime(stage_fixture,tmp_path,kind):
    f=stage_fixture
    outside=tmp_path/'outside';outside.write_text('fixture outside')
    if kind in {'external','broken'}:
        (f.runtime/'link').symlink_to(outside if kind=='external' else tmp_path/'missing')
    elif kind=='source':
        target=f.desktop/f.module.APP_FILES[0];target.unlink();target.symlink_to(outside)
    elif kind=='asset':
        target=f.desktop/'assets/igorek.png';target.unlink();target.symlink_to(outside)
    else:
        target=f.repo/'factory/fixture.py';target.unlink();target.symlink_to(outside)
    with pytest.raises(ValueError):f.module.stage(f.runtime,f.output,'linux-x64')
    assert not any(p.is_file() and p.read_bytes()==b'fixture outside' for p in f.output.rglob('*'))
