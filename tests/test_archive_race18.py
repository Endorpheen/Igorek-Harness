"""A message written during archiving must remain available."""

import multiprocessing
from pathlib import Path
from unittest.mock import patch

from factory.core import cli_chat


def _append_in_process(path, started, finished):
    started.set()
    cli_chat._append_entry(Path(path), 'assistant', 'concurrent message')
    finished.set()


def test_archive_does_not_erase_a_concurrent_message(tmp_path):
    path = tmp_path / 'chat.jsonl'
    cli_chat._write_session_meta(path, session_id='chat')
    cli_chat._append_entry(path, 'user', 'before')
    context = multiprocessing.get_context('spawn')
    started, finished = context.Event(), context.Event()
    process = context.Process(target=_append_in_process, args=(str(path), started, finished))
    replace = Path.replace

    def after_archive_rename(source, destination):
        result = replace(source, destination)
        process.start()
        assert started.wait(10)
        assert not finished.wait(0.2)
        return result

    try:
        with patch.object(Path, 'replace', after_archive_rename):
            archive = cli_chat.archive_session_thread(path)
        process.join(10)
        assert process.exitcode == 0
        assert finished.is_set()
        assert [entry['text'] for entry in cli_chat._load_entries(archive)] == ['before']
        assert [entry['text'] for entry in cli_chat._load_entries(path)] == ['concurrent message']
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
