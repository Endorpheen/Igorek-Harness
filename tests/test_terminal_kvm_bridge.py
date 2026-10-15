import json
import base64
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from factory.core.isolation.channel import read_frame, write_frame
from factory.core.isolation.guest_terminal import GuestTerminalService, TerminalTuning
from factory.core.isolation.manager import IsolationManager, SessionSpec
from factory.core.isolation.terminal_manager import TerminalError, TerminalSpec
from factory.core.isolation.terminal_vsock import RoutedTerminalBackend, VsockTerminalBackend


class TerminalKvmBridgeTest(unittest.TestCase):
    def test_final_output_and_exit_survive_a_peer_closed_before_ack(self):
        host, guest = socket.socketpair()
        backend = VsockTerminalBackend()
        backend.sock = host
        with guest:
            write_frame(guest, {"kind": "output", "seq": 1,
                                "payload": {"data": base64.b64encode(b"last output").decode()}})
            write_frame(guest, {"kind": "exit", "seq": 2, "payload": {"code": 0}})
        try:
            self.assertEqual(backend.read(1), b"last output")
            self.assertIsNone(backend.read(1))
            self.assertEqual(backend.poll_exit(), 0)
        finally:
            backend.close()

    def test_unknown_mode_never_starts_host_shell(self):
        with self.assertRaises(TerminalError):
            RoutedTerminalBackend({}).open(TerminalSpec("session", 24, 80, "/tmp"))

    def test_vm_lease_does_not_change_task_in_flight_and_rejects_replacement(self):
        manager = object.__new__(IsolationManager)
        manager._vm_lock = threading.Lock()
        manager._terminal_leases = {}
        vm = Mock(session_id="session", peer_cid=42)
        vm.domain.is_active.return_value = True
        manager._vms = {"session": vm}
        manager.ensure_vm = Mock(return_value=vm)
        route = manager.terminal_acquire(SessionSpec("session", "/tmp"))
        self.assertTrue(manager.terminal_held("session"))
        self.assertTrue(manager.terminal_renew(route["lease"]))
        manager._vms["session"] = Mock()
        self.assertFalse(manager.terminal_renew(route["lease"]))
        self.assertFalse(manager.terminal_held("session"))

    def test_guest_protocol_with_real_hardened_pty(self):
        host, guest = socket.socketpair()
        with tempfile.TemporaryDirectory() as cwd:
            Path(cwd, "session.jsonl").write_text(json.dumps({"profile": "test"}) + "\n")
            def serve_guest():
                with guest:
                    write_frame(guest, {"kind": "hello", "version": 1})
                    self.assertTrue(read_frame(guest, timeout_s=5)["accepted"])
                    GuestTerminalService(guest, tuning=TerminalTuning(workdir=cwd)).serve()

            thread = threading.Thread(target=serve_guest, daemon=True)
            thread.start()
            backend = VsockTerminalBackend()
            class ConnectedSocket:
                def __getattr__(self, key):
                    return getattr(host, key)
                def connect(self, address):
                    self.address = address

            route = {"cid": 42, "lease": "lease", "ok": True}
            with patch("factory.core.isolation.terminal_vsock.get_chat_sessions_dir", return_value=cwd), \
                 patch("factory.core.isolation.terminal_vsock.owner_request", return_value=route), \
                 patch("factory.core.isolation.terminal_vsock.socket.socket", return_value=ConnectedSocket()):
                try:
                    backend.open(TerminalSpec("session", 24, 80, cwd))
                    backend.write(b"printf 'KVM_%s\\n' OK; /usr/bin/grep --color=never '^NoNewPrivs:' /proc/$$/status; pwd; exit\n")
                    output = bytearray()
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        output.extend(backend.read(0.1) or b"")
                        if backend.poll_exit() is not None:
                            break
                    self.assertIn(b"KVM_OK", output)
                    self.assertIn(b"NoNewPrivs:\t1", output)
                    self.assertIn(cwd.encode(), output)
                    self.assertEqual(backend.poll_exit(), 0)
                finally:
                    backend.close()
            thread.join(2)
            self.assertFalse(thread.is_alive())
