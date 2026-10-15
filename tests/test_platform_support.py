"""Portable file and process-lease contracts, including native Windows checks."""
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

    def test_directory_link_cannot_redirect_file_operations(self):
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


if __name__ == '__main__':
    unittest.main()
