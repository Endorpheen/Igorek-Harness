"""Cross-process session writes must not lose messages during metadata changes."""

import multiprocessing
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.core import cli_chat


def _append_in_process(path, started, finished):
    started.set()
    cli_chat._append_entry(Path(path), 'assistant', 'concurrent message')
    finished.set()


def _update_in_process(path, started, finished):
    started.set()
    cli_chat.update_session_meta(Path(path), {'pinned': True})
    finished.set()


def test_metadata_replacement_serializes_concurrent_message_from_another_process(tmp_path):
    path = tmp_path / 'chat.jsonl'
    cli_chat._write_session_meta(path, session_id='chat')
    cli_chat._append_entry(path, 'user', 'before')
    context = multiprocessing.get_context('spawn')
    started, finished = context.Event(), context.Event()
    process = context.Process(target=_append_in_process, args=(str(path), started, finished))
    replace = os.replace

    def concurrent_replace(source, target):
        process.start()
        assert started.wait(10)
        assert not finished.wait(0.2)
        replace(source, target)

    try:
        with patch.object(cli_chat.os, 'replace', side_effect=concurrent_replace):
            cli_chat.update_session_meta(path, {'title': 'renamed'})
        process.join(10)
        assert process.exitcode == 0
        assert finished.is_set()
        assert [entry['text'] for entry in cli_chat._load_entries(path)] == [
            'before', 'concurrent message'
        ]
        assert cli_chat._read_session_meta(path)['title'] == 'renamed'
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)


def test_failed_replace_preserves_original_history_and_allows_later_append(tmp_path):
    path = tmp_path / 'chat.jsonl'
    cli_chat._write_session_meta(path, session_id='chat')
    cli_chat._append_entry(path, 'user', 'before')
    original = path.read_bytes()
    with patch.object(cli_chat.os, 'replace', side_effect=OSError('fixture write failure')):
        with pytest.raises(OSError, match='fixture write failure'):
            cli_chat.update_session_meta(path, {'title': 'renamed'})
    assert path.read_bytes() == original
    cli_chat._append_entry(path, 'assistant', 'after failure')
    assert [entry['text'] for entry in cli_chat._load_entries(path)] == [
        'before', 'after failure'
    ]


def test_concurrent_metadata_updates_keep_both_changes(tmp_path):
    path = tmp_path / 'chat.jsonl'
    cli_chat._write_session_meta(path, session_id='chat')
    context = multiprocessing.get_context('spawn')
    started, finished = context.Event(), context.Event()
    process = context.Process(target=_update_in_process, args=(str(path), started, finished))
    replace = os.replace

    def concurrent_replace(source, target):
        process.start()
        assert started.wait(10)
        assert not finished.wait(0.2)
        replace(source, target)

    try:
        with patch.object(cli_chat.os, 'replace', side_effect=concurrent_replace):
            cli_chat.update_session_meta(path, {'title': 'renamed'})
        process.join(10)
        assert process.exitcode == 0
        assert finished.is_set()
        meta = cli_chat._read_session_meta(path)
        assert meta['title'] == 'renamed'
        assert meta['pinned'] is True
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
