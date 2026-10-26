"""The guest launcher rejects unsafe inputs before it can start a process."""
import importlib.util
import stat
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / 'desktop' / 'opencode-kvm-guest.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('opencode_kvm_guest', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class KvmGuestChecks14Tests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.module.os = SimpleNamespace(getuid=lambda: 1000, environ={})
        self.project = mock.Mock()
        self.project.is_dir.return_value = True
        self.binary = mock.Mock()
        self.binary.is_absolute.return_value = True
        self.password = mock.Mock()
        self.password.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000)
        self.password.read_text.return_value = 'fixture-' + 'p' * 30

    def _run_main(self, *, binary=None, expected):
        args = SimpleNamespace(password_file=self.password,
                               binary=binary if binary is not None else self.binary,
                               project=self.project)
        with mock.patch.object(self.module.argparse, 'ArgumentParser') as parser, \
             mock.patch.object(self.module.subprocess, 'Popen') as popen, \
             mock.patch.object(self.module, 'spec_from_file_location', side_effect=AssertionError('Invalid inputs must not reach bridge')):
            parser.return_value.parse_args.return_value = args
            with self.assertRaisesRegex(ValueError, expected):
                self.module.main()
        popen.assert_not_called()

    def test_symlink_password_rejected_before_popen(self):
        file_info = self.password.lstat.return_value
        file_info.st_mode = stat.S_IFLNK | 0o600
        self._run_main(expected='Private guest password file required')
        self.password.read_text.assert_not_called()

    def test_0644_password_rejected_before_popen(self):
        file_info = self.password.lstat.return_value
        file_info.st_mode = stat.S_IFREG | 0o644
        self._run_main(expected='Private guest password file required')
        self.password.read_text.assert_not_called()

    def test_short_password_rejected_before_popen(self):
        self.password.read_text.return_value = 'short'
        self._run_main(expected='Invalid guest service configuration')
        self.password.read_text.assert_called_once_with()

    def test_relative_binary_rejected_before_popen(self):
        self._run_main(binary=Path('relative/opencode'), expected='Invalid guest service configuration')
        self.password.read_text.assert_called_once_with()

    def test_other_users_password_rejected_before_read(self):
        self.password.lstat.return_value.st_uid = 1001
        self._run_main(expected='Private guest password file required')
        self.password.read_text.assert_not_called()

    def test_missing_project_directory_rejected_before_popen(self):
        self.project.is_dir.return_value = False
        self._run_main(expected='Invalid guest service configuration')
        self.password.read_text.assert_called_once_with()
        self.project.is_dir.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
