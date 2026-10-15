"""Checkpoint commands detach owner IPC and remain bounded with descendants."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from factory.core.checkpoint_manager import _git_to_files


def test_file_capture_preserves_unicode_and_closes_command_stdin(tmp_path):
    result = _git_to_files([sys.executable, '-c',
        'import sys;assert sys.stdin.buffer.read()==b"";print("Привет");print("fixture",file=sys.stderr)'],
        cwd=str(tmp_path), env=dict(os.environ, PYTHONUTF8='1'), timeout=5)
    assert result.returncode == 0
    assert result.stdout.strip() == 'Привет'
    assert result.stderr.strip() == 'fixture'


def test_timeout_does_not_wait_for_descendant_output_handles(tmp_path):
    marker = tmp_path / 'descendant.pid'
    code = ('import pathlib,subprocess,sys,time;'
            'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(15)"],close_fds=False);'
            'pathlib.Path(sys.argv[1]).write_text(str(child.pid));time.sleep(15)')
    start = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            _git_to_files([sys.executable, '-c', code, str(marker)],
                          cwd=str(tmp_path), env=dict(os.environ), timeout=1)
        assert time.monotonic() - start < 5
        assert marker.is_file(), 'The fixture must have started a descendant'
    finally:
        if marker.is_file():
            try:
                os.kill(int(Path(marker).read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
