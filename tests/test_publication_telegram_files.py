"""Telegram attachments use the authorized regular file, never a later link."""
import io
import json

import pytest

from factory.core import telegram_bridge as module


def test_telegram_attachment_parent_swap_cannot_read_outside_file(tmp_path, monkeypatch):
    directory = tmp_path / 'work'
    directory.mkdir()
    target = directory / 'report.txt'
    target.write_text('allowed fixture')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'report.txt').write_text('private fixture')
    original = module.open_regular_file

    def swap(path):
        directory.rename(tmp_path / 'previous')
        directory.symlink_to(outside, target_is_directory=True)
        return original(path)

    monkeypatch.setattr(module, 'open_regular_file', swap)
    monkeypatch.setattr(module.urllib_request, 'urlopen', lambda *args, **kwargs: pytest.fail('Unsafe file reached transport'))
    with pytest.raises((OSError, ValueError)):
        module.TelegramBotApi('synthetic-fixture').send_document('42', target)


def test_regular_telegram_attachment_preserves_russian_caption_and_file_bytes(tmp_path, monkeypatch):
    target = tmp_path / 'report.txt'
    target.write_bytes(b'allowed fixture')
    sent = []

    def transport(request, **kwargs):
        sent.append(request.data)
        return io.BytesIO(json.dumps({'ok': True}).encode())

    monkeypatch.setattr(module.urllib_request, 'urlopen', transport)
    module.TelegramBotApi('synthetic-fixture').send_document('42', target, caption='Готовый отчёт')
    assert 'Готовый отчёт'.encode() in sent[0]
    assert b'allowed fixture' in sent[0]


def test_oversized_telegram_attachment_is_refused_before_transport(tmp_path, monkeypatch):
    target = tmp_path / 'large.txt'
    with target.open('wb') as stream:
        stream.truncate(51 * 1024 * 1024)
    monkeypatch.setattr(module.urllib_request, 'urlopen', lambda *args, **kwargs: pytest.fail('Oversized file reached transport'))
    with pytest.raises(RuntimeError, match='limit'):
        module.TelegramBotApi('synthetic-fixture').send_document('42', target)
