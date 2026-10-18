"""Portable file, process-lease and TLS contracts, including native Windows checks."""
import datetime
import ipaddress
import json
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from factory.core import cc_backend_executor as executor
from factory.core import openai_responses as transport
from test_openai_responses import completed, function, message
from factory.core.cc_backend_trace import model_failure_diagnostics
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from factory.core.artifact_files import atomic_write_file, open_regular_file, open_workspace_artifact
from factory.core.platform_support import exclusive_lock


class PlatformFiles(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_unicode_file_replace_and_bounded_workspace_read(self):
        path = self.root / 'nested' / 'Пример.txt'
        atomic_write_file(path, 'first')
        atomic_write_file(path, 'Привет, Игорь!')
        with open_workspace_artifact(self.root, 'nested/Пример.txt') as stream:
            self.assertEqual(stream.read().decode('utf-8'), 'Привет, Игорь!')
        self.assertFalse(list(path.parent.glob('.igorek-write-*')))
        for relative in ('../private', 'nested/../private', 'nested\\private', '/private'):
            with self.assertRaises(ValueError):
                open_workspace_artifact(self.root, relative)

    def test_lock_excludes_a_second_process_and_releases_on_close(self):
        path = self.root / 'lease'
        descriptor = exclusive_lock(path)
        code = ('from pathlib import Path; from factory.core.platform_support import exclusive_lock; '
                'import os,sys; fd=exclusive_lock(Path(sys.argv[1])); os.close(fd)')
        try:
            process = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True)
            self.assertNotEqual(process.returncode, 0)
        finally:
            os.close(descriptor)
        process = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors='replace'))

    def test_bridge_output_read_preserves_portable_identity_checks(self):
        from factory.core.mobile_bridge.files import describe_file, open_file_safely
        path = self.root / 'Пример.md'
        atomic_write_file(path, 'first output')
        description = describe_file(path)
        with open_file_safely(path, description) as stream:
            self.assertEqual(stream.read(), b'first output')
        atomic_write_file(path, 'changed output with a different identity')
        with self.assertRaises(OSError):
            open_file_safely(path, description)

    def test_directory_link_cannot_redirect_file_operations(self):
        from factory.core.mobile_bridge.files import describe_file, open_file_safely
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'secret').write_bytes(b'private')
        work = self.root / 'work'; work.mkdir()
        link = work / 'redirect'
        if os.name == 'nt':
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
        for operation in (lambda: open_regular_file(link / 'secret'),
                          lambda: atomic_write_file(link / 'secret', 'overwritten'),
                          lambda: open_file_safely(link / 'secret', describe_file(link / 'secret')),
                          lambda: open_workspace_artifact(work, 'redirect/secret')):
            with self.assertRaises((OSError, ValueError)):
                operation()
        self.assertEqual((outside / 'secret').read_bytes(), b'private')

    @unittest.skipUnless(os.name == 'nt', 'Windows handle pinning contract')
    def test_windows_pinned_parent_cannot_be_renamed(self):
        from factory.core.platform_support.windows_files import pinned_parent
        directory = self.root / 'parent'; directory.mkdir()
        with pinned_parent(directory / 'file'):
            with self.assertRaises(OSError):
                directory.rename(self.root / 'moved')
        directory.rename(self.root / 'moved')

    @unittest.skipUnless(os.name == 'nt', 'Windows DACL preservation contract')
    def test_windows_replace_preserves_private_dacl(self):
        from factory.core.platform_support.windows_security import protect_directory, read_dacl
        protect_directory(self.root)
        path = self.root / 'private'
        atomic_write_file(path, 'old')
        before = read_dacl(path)
        atomic_write_file(path, 'new')
        self.assertEqual(read_dacl(path), before)

    @unittest.skipUnless(os.name == 'nt', 'Windows Git and checkpoint process contract')
    def test_checkpointed_write_uses_windows_environment_without_current_directory_execution(self):
        code = '''from pathlib import Path
from factory.core.checkpoint_manager import CheckpointManager, _git_executable
from factory.core.cc_backend_tools import _tool_write
from factory.core.readonly_git import git_environment
from factory.core.session_changes import TaskChangeTracker
import os, sys
import faulthandler
faulthandler.dump_traceback_later(8, repeat=True)
root=Path(sys.argv[1]); work=root/'private'/'demos'/'project';work.mkdir(parents=True)
(work/'git.exe').write_bytes(b'not a trusted executable')
os.chdir(work)
git=_git_executable(); assert git and Path(git).parent != work
env=git_environment();assert env['SystemRoot'] and env['NoDefaultCurrentDirectoryInExePath']=='1'
assert 'OPENAI_API_KEY' not in env
manager=CheckpointManager(data_root=str(root/'private'))
tracker=TaskChangeTracker(task_id='fixture-write',workdir=str(work),checkpoint_manager=manager,data_root=str(root/'private'))
result=_tool_write({'file_path':'note.txt','content':'fixture'},str(work),'workspace',manager,tracker)
assert (work/'note.txt').read_text()=='fixture'
assert manager.list_checkpoints(str(work))
'''
        env = dict(os.environ)
        home = self.root / 'home'; home.mkdir()
        env.update(HOME=str(home), USERPROFILE=str(home), TEMP=str(self.root), TMP=str(self.root))
        with tempfile.TemporaryFile() as output:
            with subprocess.Popen([sys.executable, '-c', code, str(self.root)], env=env,
                                  stdin=subprocess.PIPE, stdout=output, stderr=output) as process:
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
                    output.seek(0)
                    self.fail('Checkpointed Write stalled:\n' + output.read().decode(errors='replace'))
                output.seek(0)
                self.assertEqual(process.returncode, 0, output.read().decode(errors='replace'))

    def test_windows_command_guard_cannot_be_disabled(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {'FACTORY_DATA_DIR': str(self.root), 'IGOREK_FACTORY_HOME': str(self.root)}):
            from factory.core.cc_backend_tools import _tool_bash
        with patch('sys.platform', 'win32'), patch('subprocess.run') as run:
            result = _tool_bash({'command': 'echo must-not-run'}, str(self.root), guard_mode='off')
            self.assertIn('no command was executed', result)
            run.assert_not_called()


@pytest.fixture
def tls_server(tmp_path_factory):
    root = tmp_path_factory.mktemp('responses-tls')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'fixture.invalid')])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .sign(key, hashes.SHA256()))
    cert = root / 'fixture.pem'
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private = root / 'fixture.key'
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, private)
    calls, handlers = [], []
    state = {'connections': 0, 'drop': set()}

    def exact(stream, size):
        value = b''
        while len(value) < size:
            part = stream.recv(size - len(value))
            if not part:
                raise OSError('Incomplete fixture ClientHello')
            value += part
        return value

    class Server(ThreadingHTTPServer):
        def get_request(self):
            stream, address = self.socket.accept()
            stream.settimeout(3)
            state['connections'] += 1
            try:
                if state['connections'] in state['drop']:
                    header = exact(stream, 5)
                    exact(stream, int.from_bytes(header[3:5], 'big'))
                    stream.shutdown(socket.SHUT_WR)
                    raise OSError('Fixture closed TLS before ServerHello')
                return context.wrap_socket(stream, server_side=True), address
            except BaseException:
                stream.close()
                raise

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for event in handlers[0](body, len(calls)):
                self.wfile.write(b'data: ' + json.dumps(event).encode() + b'\n\n')
                self.wfile.flush()

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'https://127.0.0.1:{server.server_port}/v1', calls, handlers, state, cert
    server.shutdown()
    server.server_close()
    thread.join(3)


@pytest.mark.parametrize('failures', [1, 3])
def test_real_tls_eof_retries_after_tool_without_reexecuting_it(tls_server, tmp_path, monkeypatch, failures):
    from factory.core.cc_backend_tools import get_tool_schemas
    base, calls, handlers, state, cert = tls_server
    state['drop'] = set(range(2, failures + 2))
    trusted = ssl.create_default_context(cafile=str(cert))
    assert trusted.check_hostname and trusted.verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setattr(ssl, '_create_default_https_context', lambda *args, **kwargs: trusted)
    path = tmp_path / 'input.txt'
    path.write_text('tls-evidence-7351', encoding='utf-8')

    def reply(payload, index):
        if index == 1:
            return [completed([function(args={'file_path': str(path)})])]
        outputs = [item for item in payload['input'] if item.get('type') == 'function_call_output']
        assert len(outputs) == 1 and outputs[0]['call_id'] == 'call-fixture'
        assert 'tls-evidence-7351' in outputs[0]['output']
        return [completed([message('tls-evidence-7351')])]

    handlers.append(reply)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **_: (128000, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    tool = next(item for item in get_tool_schemas() if item['name'] == 'Read')
    result = executor.execute_cc_backend_task(base_url=base, model='fixture', system_prompt='Fixture',
        user_message='Read input.txt', workdir=str(tmp_path), api_format='openai-responses',
        enabled_tools=[tool], checkpoint_config={'enabled': False}, timeout_s=3)
    events = [json.loads(line) for line in (tmp_path / 'cc_backend_trace.jsonl').read_text(encoding='utf-8').splitlines()]
    retries = [item for item in events if item['kind'] == 'network.retry']
    assert len(retries) == min(failures, 2)
    assert all(item['round'] == 2 and item['error_diagnostics']['error_code'] == 'responses_tls'
               and item['error_diagnostics']['tls_error_type'] == 'SSLEOFError' for item in retries)
    assert state['connections'] == 1 + min(failures + 1, 3)
    assert result['tool_calls_count'] == 1
    assert len([item for item in events if item['kind'] == 'tool.result']) == 1
    assert result['status'] == ('ok' if failures == 1 else 'fail')
    if failures == 1:
        assert result['output_text'] == 'tls-evidence-7351'
    else:
        assert events[-1]['error_diagnostics']['tls_error_type'] == 'SSLEOFError'


def test_real_untrusted_certificate_is_not_retried_or_bypassed(tls_server, tmp_path, monkeypatch):
    base, calls, _, state, _ = tls_server
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **_: (128000, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    result = executor.execute_cc_backend_task(base_url=base, model='fixture', system_prompt='Fixture',
        user_message='Hello', workdir=str(tmp_path), api_format='openai-responses',
        enabled_tools=[], checkpoint_config={'enabled': False}, timeout_s=3)
    events = [json.loads(line) for line in (tmp_path / 'cc_backend_trace.jsonl').read_text(encoding='utf-8').splitlines()]
    assert result['status'] == 'fail' and state['connections'] == 1 and not calls
    assert not [item for item in events if item['kind'] == 'network.retry']
    details = events[-1]['error_diagnostics']
    assert details['tls_error_type'] == 'SSLCertVerificationError'
    assert details['tls_reason'] == 'CERTIFICATE_VERIFY_FAILED'
    assert type(details['tls_verify_code']) is int and details['tls_verify_code'] > 0


@pytest.mark.parametrize('kind,reason,retry', [
    ('SSLZeroReturnError', 'OTHER', True),
    ('SSLError', 'UNEXPECTED_EOF_WHILE_READING', True),
    ('SSLError', 'TLSV1_ALERT_INTERNAL_ERROR', True),
    ('SSLError', 'WRONG_VERSION_NUMBER', False),
    ('SSLError', 'OTHER', False),
    ('SSLCertVerificationError', 'UNEXPECTED_EOF_WHILE_READING', False),
    ('SSLEOFError', 'CERTIFICATE_VERIFY_FAILED', False),
])
def test_tls_retry_uses_structured_subtype_and_preserves_certificate_denial(kind, reason, retry):
    assert executor._is_transient_network_error('connection timeout', error_code='responses_tls',
        error_diagnostics={'tls_error_type': kind, 'tls_reason': reason}) is retry


def test_tls_diagnostics_preserve_static_reason_and_verification_code_without_private_messages():
    value=model_failure_diagnostics({'error_code':'responses_tls','transport_phase':'connect',
        'tls_error_type':'SSLCertVerificationError','tls_reason':'CERTIFICATE_VERIFY_FAILED',
        'tls_errno':1,'tls_verify_code':18,'verify_message':'private certificate subject',
        'exception':'Bearer private-token','certificate':'private-pem'})
    assert value['tls_error_type']=='SSLCertVerificationError' and value['tls_verify_code']==18
    assert value['tls_reason']=='CERTIFICATE_VERIFY_FAILED' and value['tls_errno']==1
    assert 'private' not in json.dumps(value)
    assert model_failure_diagnostics({'tls_error_type':'private','tls_reason':'private',
        'tls_errno':True,'tls_verify_code':-1})=={}

if __name__ == '__main__':
    unittest.main()
