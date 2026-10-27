import multiprocessing
import os
from pathlib import Path

import pytest

from factory.core import artifact_files


class InterruptedInput:
    def __init__(self):
        self.calls = 0

    def read(self, size):
        self.calls += 1
        if self.calls == 1:
            return b'partial replacement'
        raise OSError('input interrupted')


def _blocked_write(path, ready):
    class BlockingInput:
        def read(self, size):
            ready.set()
            while True:
                import time
                time.sleep(1)

    artifact_files.atomic_write_file(path, BlockingInput())


def test_interrupted_input_preserves_existing_file_and_allows_retry(tmp_path):
    path = tmp_path / 'document'
    path.write_bytes(b'original')
    path.chmod(0o600)
    with pytest.raises(OSError, match='input interrupted'):
        artifact_files.atomic_write_file(path, InterruptedInput())
    assert path.read_bytes() == b'original'
    assert not list(tmp_path.glob('.igorek-write-*'))
    artifact_files.atomic_write_file(path, b'recovered')
    assert path.read_bytes() == b'recovered'
    if os.name != 'nt':
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process termination and file permissions')
def test_process_death_before_replace_preserves_file_and_allows_retry(tmp_path):
    path = tmp_path / 'document'
    path.write_bytes(b'original')
    context = multiprocessing.get_context('spawn')
    ready = context.Event()
    process = context.Process(target=_blocked_write, args=(path, ready))
    try:
        process.start()
        assert ready.wait(10)
        process.terminate()
        process.join(10)
        assert not process.is_alive()
        assert process.exitcode is not None and process.exitcode != 0
        assert path.read_bytes() == b'original'
        artifact_files.atomic_write_file(path, b'recovered')
        assert path.read_bytes() == b'recovered'
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX descriptor-relative replacement')
def test_raced_output_symlink_does_not_modify_external_file(tmp_path, monkeypatch):
    path = tmp_path / 'document'
    outside = tmp_path / 'outside'
    path.write_bytes(b'original')
    outside.write_bytes(b'private')
    replace = os.replace

    def race(source, target, **kwargs):
        path.unlink()
        path.symlink_to(outside)
        replace(source, target, **kwargs)

    monkeypatch.setattr(artifact_files.os, 'replace', race)
    artifact_files.atomic_write_file(path, b'new')
    assert outside.read_bytes() == b'private'
    assert not path.is_symlink()
    assert path.read_bytes() == b'new'
