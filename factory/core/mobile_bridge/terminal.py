"""Authenticated mobile transport for the existing KVM terminal endpoint."""
from pathlib import Path

from ..isolation.terminal_manager import TerminalError, TerminalManager
from ..isolation.terminal_vsock import RoutedTerminalBackend


class MobileTerminals:
    def __init__(self, service):
        self.service = service
        self.modes = {}
        self.manager = TerminalManager(backend_factory=lambda: RoutedTerminalBackend(self.modes))

    def workdir(self, session_id):
        session = self.service.get_session(session_id)
        if session_id.startswith('agent-'):
            raise TerminalError('child_terminal_unavailable')
        if session.get('isolation') != 'kvm':
            raise TerminalError('kvm_terminal_unavailable')
        cwd = (session.get('meta') or {}).get('cwd')
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise TerminalError('workdir_missing')
        self.modes[session_id] = 'kvm'
        return cwd

    def ticket(self, session_id, device_id):
        self.workdir(session_id)
        return str(self.manager.issue_ticket(session_id, device_identity=device_id))
