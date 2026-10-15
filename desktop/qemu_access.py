"""Grant system QEMU access only to the owned desktop VM storage."""
import os
from pathlib import Path
import re
import subprocess
import uuid
import xml.etree.ElementTree as ET

from factory.core.isolation.errors import IsolationError


def verify_socket_policy(root):
    import libvirt
    connection = libvirt.open('qemu:///system')
    try:
        models = ET.fromstring(connection.getCapabilities()).findall('./host/secmodel/model')
    finally:
        connection.close()
    if not any(model.text == 'apparmor' for model in models):
        return
    from factory.core.isolation.domain import DomainSpec, build_domain_xml, _session_slug
    root = Path(root).resolve()
    session = 'desktop-policy-' + uuid.uuid4().hex
    socket = root / 'isolation/overlays' / (_session_slug(session) + '.sock')
    spec = DomainSpec(session, root / 'isolation/images/base.qcow2',
                      root / 'workspace', socket.parent, virtiofs_socket=socket)
    xml = ET.fromstring(build_domain_xml(spec))
    identity = str(uuid.uuid4())
    ET.SubElement(xml, 'uuid').text = identity
    helper = next((path for path in (Path('/usr/lib/libvirt/virt-aa-helper'),
                  Path('/usr/libexec/virt-aa-helper')) if path.is_file()), None)
    if helper is None:
        raise IsolationError('desktop_socket_policy_unavailable')
    try:
        result = subprocess.run([str(helper), '-d', '-c', '-u', 'libvirt-' + identity],
                                input=ET.tostring(xml, encoding='unicode'), text=True,
                                capture_output=True, timeout=5, env={'PATH': '/usr/bin:/bin'})
    except (OSError, subprocess.SubprocessError) as error:
        raise IsolationError('desktop_socket_policy_unavailable') from error
    if result.returncode != 0:
        raise IsolationError('desktop_socket_policy_unavailable')
    rules = result.stdout + '\n' + result.stderr
    grants = re.findall(r'^\s*"' + re.escape(str(socket)) + r'"\s+([a-z]+),\s*$', rules, re.M)
    if not any('r' in permission and 'w' in permission for permission in grants):
        raise IsolationError('desktop_socket_policy_missing')


def qemu_uid():
    import libvirt
    connection = libvirt.open('qemu:///system')
    try:
        models = ET.fromstring(connection.getCapabilities()).findall('./host/secmodel')
        labels = [label.text for model in models if model.findtext('model') == 'dac'
                  for label in model.findall('baselabel') if label.get('type') == 'kvm']
    finally:
        connection.close()
    if len(labels) != 1 or not re.fullmatch(r'\+[0-9]+:\+[0-9]+', labels[0] or ''):
        raise IsolationError('isolation_unavailable:desktop_qemu_identity')
    uid = int(labels[0].split(':')[0][1:])
    if uid == 0 or uid == os.getuid() or uid >= 4294967295:
        raise IsolationError('isolation_unavailable:desktop_qemu_identity')
    return uid


def prepare_access(root):
    root = Path(root).absolute()
    image = root / 'isolation/images/base.qcow2'
    overlays = root / 'isolation/overlays'
    paths = [root, root / 'isolation', image.parent, image]
    if any(path.resolve() != path or not path.exists() or path.stat().st_uid != os.getuid() for path in paths):
        raise IsolationError('isolation_unavailable:desktop_storage_path')
    if overlays.is_symlink():
        raise IsolationError('isolation_unavailable:desktop_storage_path')
    overlays.mkdir(mode=0o700, exist_ok=True)
    if overlays.stat().st_uid != os.getuid():
        raise IsolationError('isolation_unavailable:desktop_storage_path')
    uid = qemu_uid()
    def grant(path, permission, *, default=False):
        subprocess.run(['/usr/bin/setfacl', '-m', ('d:' if default else '') + f'u:{uid}:{permission}', str(path)],
                       check=True, capture_output=True, timeout=5)
    try:
        for parent in reversed(root.parents):
            if not parent.stat().st_mode & 0o001:
                if parent.stat().st_uid != os.getuid():
                    raise IsolationError('isolation_unavailable:desktop_storage_parent')
                grant(parent, '--x')
        for path in (root, root / 'isolation', image.parent):
            grant(path, '--x')
        grant(image, 'r--')
        grant(overlays, 'rwx')
        grant(overlays, 'rwx', default=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise IsolationError('isolation_unavailable:desktop_storage_acl') from error
    return uid
