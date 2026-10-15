import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('opencode_sandbox', Path(__file__).parents[1] / 'desktop/opencode-sandbox.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def xml(devices='', kind='kvm'):
    return f'<domain type="{kind}"><devices>{devices}<vsock><cid address="27"/></vsock></devices></domain>'


def test_vsock_target_comes_from_libvirt_and_only_approved_shares_are_allowed(tmp_path):
    assert module.validate_vm(xml(), [str(tmp_path)]) == 27
    share = f'<filesystem type="mount"><source dir="{tmp_path}"/></filesystem>'
    assert module.validate_vm(xml(share), [str(tmp_path)]) == 27


@pytest.mark.parametrize('devices', [
    '<hostdev/>', '<shmem/>',
    '<filesystem type="mount"><source dir="/home"/></filesystem>',
    '<filesystem type="mount"><source socket="/run/virtiofs.sock"/></filesystem>',
    '<interface type="bridge"/>', '<interface type="direct"/>',
    '<channel type="unix"><source path="/run/private.sock"/></channel>',
    '<serial type="tcp"/>', '<disk type="block"/>',
    '<interface type="user"><portForward/></interface>',
    '<q:commandline xmlns:q="http://libvirt.org/schemas/domain/qemu/1.0"/>',
])
def test_kvm_connector_rejects_unapproved_host_access(devices):
    with pytest.raises(ValueError):
        module.validate_vm(xml(devices), ['/tmp/project'])


def test_kvm_connector_does_not_accept_emulation_or_a_missing_vsock():
    with pytest.raises(ValueError):
        module.validate_vm(xml(kind='qemu'), [])
    with pytest.raises(ValueError):
        module.validate_vm('<domain type="kvm"><devices/></domain>', [])
