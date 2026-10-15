"""Browser capture paths obey the same write grant as file tools."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory.core import cc_backend_tools as tools


@pytest.mark.parametrize('action', ['screenshot', 'pdf'])
def test_outside_browser_capture_is_denied_before_start(tmp_path, monkeypatch, action):
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.delenv('IGOREK_ISOLATION_GUEST', raising=False)
    monkeypatch.setattr(tools, '_agent_browser_path', lambda: '/usr/bin/agent-browser')
    monkeypatch.setattr(tools.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Outside capture reached browser'))
    result = tools._handle_browser({'action': action, 'path': str(tmp_path / 'outside')},
                                   workdir=str(work), filesystem_mode='workspace')
    assert 'error' in json.loads(result)


@pytest.mark.parametrize('action', ['screenshot', 'pdf'])
def test_capture_staging_prevents_a_late_directory_link_escape(tmp_path, monkeypatch, action):
    work = tmp_path / 'work'
    output = work / 'output'
    output.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    private = outside / 'capture.bin'
    private.write_bytes(b'unchanged fixture')
    monkeypatch.delenv('IGOREK_ISOLATION_GUEST', raising=False)
    monkeypatch.setattr(tools, '_agent_browser_path', lambda: '/usr/bin/agent-browser')

    def run(argv, **kwargs):
        staged = Path(argv[argv.index(action) + 1])
        assert not staged.is_relative_to(work)
        staged.write_bytes(b'capture fixture')
        output.rename(work / 'previous')
        output.symlink_to(outside, target_is_directory=True)
        return SimpleNamespace(returncode=0, stdout=str(staged), stderr='')

    monkeypatch.setattr(tools.subprocess, 'run', run)
    result = tools._handle_browser({'action': action, 'path': 'output/capture.bin'},
                                   workdir=str(work), filesystem_mode='workspace')
    assert 'error' in json.loads(result)
    assert private.read_bytes() == b'unchanged fixture'
