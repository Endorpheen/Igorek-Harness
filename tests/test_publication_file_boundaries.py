"""Filesystem authorization remains valid through open and persistence."""
from pathlib import Path

import pytest

from factory.core import cc_backend_tools as tools
from factory.core.tool_result_store import maybe_persist_tool_result


@pytest.mark.parametrize('operation', ['read', 'write', 'edit'])
@pytest.mark.parametrize('component', ['file', 'directory'])
def test_path_swap_after_validation_cannot_access_outside_workspace(tmp_path, monkeypatch, operation, component):
    work = tmp_path / 'work'
    directory = work / 'sub'
    directory.mkdir(parents=True)
    target = directory / 'note.txt'
    target.write_text('old text')
    outside = tmp_path / 'outside'
    outside.mkdir()
    private = outside / 'note.txt'
    private.write_text('outside fixture')
    validator = '_validate_read_path' if operation == 'read' else '_validate_write_path'
    original = getattr(tools, validator)
    def swap(*args, **kwargs):
        path = original(*args, **kwargs)
        if component == 'file':
            target.unlink()
            target.symlink_to(private)
        else:
            directory.rename(work / 'previous')
            directory.symlink_to(outside, target_is_directory=True)
        return path
    monkeypatch.setattr(tools, validator, swap)
    try:
        if operation == 'read':
            result = tools._tool_read({'file_path': str(target)}, str(work), 'workspace')
            assert 'outside fixture' not in result
        elif operation == 'write':
            tools._tool_write({'file_path': str(target), 'content': 'new text'}, str(work), 'workspace', None)
        else:
            tools._tool_edit({'file_path': str(target), 'old_string': 'outside', 'new_string': 'changed'},
                             str(work), 'workspace', None)
    except (OSError, ValueError):
        pass
    assert private.read_text() == 'outside fixture'


def test_persisted_result_does_not_follow_a_workspace_link(tmp_path):
    work = tmp_path / 'work'
    (work / '.tool_results').mkdir(parents=True)
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside fixture')
    (work / '.tool_results/call-fixture.txt').symlink_to(outside)
    try:
        maybe_persist_tool_result(content='long result ' * 2000, tool_name='Read',
                                  tool_use_id='call-fixture', workdir=str(work))
    except (OSError, ValueError):
        pass
    assert outside.read_text() == 'outside fixture'


def test_internal_links_and_regular_edits_remain_usable(tmp_path):
    target = tmp_path / 'note.txt'
    target.write_text('old text')
    target.chmod(0o755)
    link = tmp_path / 'link.txt'
    link.symlink_to(target)
    assert tools._tool_read({'file_path': str(link)}, str(tmp_path), 'workspace') == 'old text'
    tools._tool_edit({'file_path': str(link), 'old_string': 'old', 'new_string': 'new'},
                    str(tmp_path), 'workspace', None)
    assert target.read_text() == 'new text'
    assert target.stat().st_mode & 0o777 == 0o755


def test_edit_preserves_read_tool_newline_semantics(tmp_path):
    target = tmp_path / 'windows.txt'
    target.write_bytes(b'first\r\nsecond\r\n')
    assert tools._tool_read({'file_path': str(target)}, str(tmp_path), 'workspace') == 'first\nsecond\n'
    tools._tool_edit({'file_path': str(target), 'old_string': 'first\nsecond', 'new_string': 'new'},
                     str(tmp_path), 'workspace', None)
    assert target.read_text() == 'new\n'


def test_read_range_keeps_unicode_line_boundaries(tmp_path):
    target = tmp_path / 'unicode.txt'
    target.write_text('first\u2028second\nthird')
    assert tools._tool_read({'file_path': str(target), 'offset': 1, 'limit': 1},
                           str(tmp_path), 'workspace') == 'second\n'


def test_oversized_change_preview_is_unavailable_not_empty(tmp_path):
    from factory.core.session_changes import _read_text
    target = tmp_path / 'large.txt'
    with target.open('wb') as stream:
        stream.truncate(9 * 1024 * 1024)
    with pytest.raises(ValueError, match='limit'):
        _read_text(target)


@pytest.mark.timeout(1)
def test_read_rejects_a_workspace_fifo_without_waiting(tmp_path):
    import os
    fifo = tmp_path / 'input.pipe'
    os.mkfifo(fifo)
    with pytest.raises((OSError, ValueError)):
        tools._tool_read({'file_path': str(fifo), 'limit': 1}, str(tmp_path), 'workspace')
