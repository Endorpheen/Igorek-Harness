"""Persist owner-selected desktop execution and build its standard KVM manager."""
import json
import os
from pathlib import Path
import tempfile

from factory.core.isolation.errors import IsolationError


class DesktopExecution:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'desktop-execution.json'
        if self.path.is_symlink():
            raise ValueError('Invalid execution settings path')
        self.mode = 'off'
        if self.path.exists():
            value = json.loads(self.path.read_text())
            if (not isinstance(value, dict) or set(value) != {'schema_version', 'mode'}
                    or type(value['schema_version']) is not int or value['schema_version'] != 1
                    or value['mode'] not in ('off', 'kvm')):
                raise ValueError('Invalid execution settings')
            self.mode = value['mode']

    def status(self):
        result = {'mode': self.mode}
        import sys
        if sys.platform == 'win32':
            result['capabilities'] = {'platform': 'win32', 'channel': 'preview',
                'command_sandbox': False, 'kvm': False, 'host_service_proxy': False}
        return result

    def select(self, mode):
        if mode not in {'off', 'kvm'}:
            raise ValueError('Invalid execution mode')
        if mode == 'kvm':
            from factory.core.platform_support import require_linux
            require_linux('kvm_execution')
        if self.path.is_symlink():
            raise ValueError('Invalid execution settings path')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.execution-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'schema_version': 1, 'mode': mode}, stream)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        self.mode = mode
        return self.status()

    def create_manager(self, lifecycle, credential_id, profile):
        if self.mode == 'off':
            return None
        from factory.core.platform_support import require_linux
        require_linux('kvm_execution')
        from factory.core.isolation.manager import IsolationManager, ManagerSeams
        from factory.core.isolation.config import IsolationConfig
        from factory.core.isolation.base_image import manifest_path_for, load_manifest, ManifestError
        from factory.core.worker import _get_host_op_runtime
        from vault_egress import DesktopVaultEgress
        image = self.root / 'isolation/images/base.qcow2'
        manifest = manifest_path_for(image)
        if any(path.resolve() != path.absolute() for path in (image, manifest)):
            raise IsolationError('isolation_unavailable:desktop_image_path')
        try:
            port = load_manifest(manifest).vsock_port
        except (OSError, ManifestError) as error:
            raise IsolationError('isolation_unavailable:desktop_image_manifest') from error
        if port != 40519:
            raise IsolationError('isolation_unavailable:desktop_guest_channel_mismatch')
        env = {'IGOREK_ISOLATION_MODE': 'kvm', 'IGOREK_ISOLATION_BASE_IMAGE': str(image),
               'IGOREK_ISOLATION_OVERLAY_DIR': str(self.root / 'isolation/overlays'),
               'IGOREK_ISOLATION_LIBVIRT_URI': 'qemu:///system',
               'IGOREK_ISOLATION_VSOCK_PORT': str(port)}
        from factory.core.isolation.limits import qcow2_virtual_size, GIB_BYTES
        from factory.core.isolation.config import DEFAULT_SESSION_DISK_GB
        size = qcow2_virtual_size(image)
        if size is None or size <= 0:
            raise IsolationError('isolation_unavailable:desktop_image_disk_size')
        env['IGOREK_ISOLATION_SESSION_DISK_GB'] = str(max(
            DEFAULT_SESSION_DISK_GB, (size + GIB_BYTES - 1) // GIB_BYTES))
        config = IsolationConfig.from_env(env, data_root=self.root)
        if not config.enabled:
            raise IsolationError('isolation_unavailable:' + str(config.reason or 'unknown'))
        from qemu_access import prepare_access, verify_socket_policy
        verify_socket_policy(self.root)
        prepare_access(self.root)
        proxy = DesktopVaultEgress(lifecycle, credential_id, profile)
        from factory.core.vault.agent_values import AgentValueWriter
        try:
            manager = IsolationManager.from_env(env, seams=ManagerSeams(
                provider_secrets=proxy, host_ops=_get_host_op_runtime(),
                vault_value_writer=AgentValueWriter(lifecycle, credential_id, profile)))
            try:
                manager.channel_server.reserve_listener()
            except Exception as error:
                manager.close()
                raise IsolationError('isolation_unavailable:desktop_channel_unavailable') from error
            return manager
        except Exception:
            proxy.close()
            raise
