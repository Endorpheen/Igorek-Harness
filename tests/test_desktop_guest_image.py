import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
spec = importlib.util.spec_from_file_location('desktop_guest_image', Path(__file__).resolve().parents[1] / 'desktop/packaging/build-guest-image.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
from factory.core.isolation.base_image import ImageBuildInfo, build_manifest, write_manifest

UNIT = '''[Unit]
Requires=workspace.mount igorek-egress-guard.service
[Service]
User=agent
Group=agent
Environment=IGOREK_ISOLATION_VSOCK_PORT=5005
ExecStartPre=/usr/local/bin/igorek-guest-bwrap-probe
ExecStart=/home/agent/.local/bin/igorek-factory guest-runner --vsock-cid 2 --vsock-port 5005
'''


def test_override_changes_only_channel_and_keeps_runner_identity():
    override = module.desktop_override(UNIT, 'agent')
    assert 'vsock-port 40519' in override
    assert 'Environment=IGOREK_ISOLATION_VSOCK_PORT=40519' in override
    assert 'User=' not in override and 'ExecStartPre=' not in override
    assert 'ExecStart=\nExecStart=/home/agent/' in override


@pytest.mark.parametrize('unit,user', [(UNIT.replace('User=agent', 'User=root'),'agent'),
    (UNIT.replace('--vsock-port 5005', '--vsock-port 6000'),'agent'),
    (UNIT.replace('ExecStartPre=', 'Different='),'agent'), (UNIT, '../agent')])
def test_unknown_guest_contract_is_refused(unit, user):
    with pytest.raises(ValueError): module.desktop_override(unit, user)


@pytest.mark.parametrize('failure', [None, 'existing_override', 'write_failed', 'readback'])
def test_build_is_separate_verified_and_never_publishes_failure(tmp_path, monkeypatch, failure):
    source = tmp_path / 'source.qcow2'
    subprocess.run(['/usr/bin/qemu-img','create','-f','qcow2',str(source),'1M'],check=True,capture_output=True)
    write_manifest(build_manifest(source, ImageBuildInfo(tool_version='0.1.819', guest_user='agent')), source)
    before = source.read_bytes()
    run = subprocess.run
    def guestfish(args, **kwargs):
        if args[0] != '/usr/bin/guestfish': return run(args, **kwargs)
        if '--ro' in args:
            return SimpleNamespace(stdout=UNIT+('true\n' if failure=='existing_override' else 'false\n'))
        if failure=='write_failed': raise subprocess.CalledProcessError(1,args)
        return SimpleNamespace(stdout='wrong' if failure=='readback' else args[args.index('write')+2]+'\n')
    monkeypatch.setattr(module.subprocess, 'run', guestfish)
    output = tmp_path / 'desktop-image'
    if failure:
        with pytest.raises((ValueError,subprocess.CalledProcessError)):
            module.build_guest(source, output, tool_version='0.1.819', progress=lambda _:None)
        assert not output.exists()
    else:
        result = module.build_guest(source, output, tool_version='0.1.819', progress=lambda _:None)
        assert result['vsock_port']==40519
        target = output/'base.qcow2'
        assert target.stat().st_ino!=source.stat().st_ino
        assert target.stat().st_mode & 0o777==0o400
        record = json.loads(Path(str(target)+'.manifest.json').read_text())
        assert record['desktop_channel']['vsock_port']==40519
        with pytest.raises(ValueError,match='already exists'):
            module.build_guest(source, output, tool_version='0.1.819', progress=lambda _:None)
    assert source.read_bytes()==before
    assert not list(tmp_path.glob('.desktop-guest-*'))
