import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from vault_owner import OwnerVault
from factory.core.vault.env_import import EnvImport, EnvImportError, parse_env
from factory.core.vault.lifecycle import VaultAuthenticationError, VaultLifecycle
from factory.core.vault.management import VaultManagement, VaultPrincipal, VaultManagementDenied


@pytest.fixture
def setup(tmp_path):
    owner = OwnerVault(tmp_path / 'runtime')
    owner.service.create('fixture-password')
    source = tmp_path / '.env'
    source.write_text('API_TOKEN=fixture-private-token\nSERVICE_URL="http://localhost:9000/#fragment"\nEMPTY=\n')
    yield owner, source
    owner.close()


def test_parser_handles_quotes_export_comments_and_literal_expressions(tmp_path):
    data = b'\xef\xbb\xbf# comment\r\nexport API_TOKEN=" exact value " # note\r\nURL=https://example.test/#part\r\nEMPTY=\r\nLITERAL=\'${HOME} $(touch marker)\'\r\nESCAPED="one\\ntwo\\t\\\"three"\r\n'
    parsed = parse_env(data)
    assert parsed == {'API_TOKEN': ' exact value ', 'URL': 'https://example.test/#part', 'EMPTY': '',
                      'LITERAL': '${HOME} $(touch marker)', 'ESCAPED': 'one\ntwo\t"three'}
    assert not (tmp_path / 'marker').exists()


@pytest.mark.parametrize('data', [b'NO_EQUALS', b'1BAD=value', b'BAD-NAME=value', b'X="unclosed',
    b'X="value" tail', b'X=a\nX=b', b'X=\xff', b'X="a\nb"', b'X=\0',
    ('X=' + 'x' * 1025).encode(), '\n'.join('K' + str(i) + '=v' for i in range(129)).encode()])
def test_parser_rejects_unsupported_or_ambiguous_input_without_values(data):
    with pytest.raises(EnvImportError) as error:
        parse_env(data)
    assert 'value' not in str(error.value)


def test_preview_is_metadata_only_and_import_is_selected_encrypted_and_recoverable(setup, tmp_path):
    owner, source = setup
    controller = EnvImport(owner.lifecycle)
    preview = controller.prepare(str(source))
    assert preview['filename'] == '.env'
    assert set(preview['entries'][0]) == {'name', 'empty', 'exists'}
    assert 'fixture-private-token' not in json.dumps(preview)
    with pytest.raises(VaultAuthenticationError):
        controller.commit(preview['draft_id'], '{"API_TOKEN":"secret"}', 'wrong')
    result = controller.commit(preview['draft_id'], '{"API_TOKEN":"secret","EMPTY":"variable"}', 'fixture-password')
    assert result['imported'] == 2
    assert {entry['name'] for entry in result['values']} == {'API_TOKEN', 'EMPTY'}
    assert all(entry['origin'] == {'kind': 'import', 'filename': '.env'} for entry in result['values'])
    assert b'fixture-private-token' not in owner.lifecycle.db_path.read_bytes()
    assert 'fixture-private-token' not in json.dumps(result)
    assert source.read_text().startswith('API_TOKEN=fixture-private-token')
    assert controller.draft is None
    restored = VaultLifecycle.restore_recovery(tmp_path / 'restored', owner.lifecycle.export_recovery('fixture-recovery'), 'fixture-recovery', 'new-password')
    try:
        assert restored.list_values() == owner.lifecycle.list_values()
    finally:
        restored.lock()


def test_conflict_rolls_back_the_entire_import_and_preserves_old_values(setup):
    owner, source = setup
    controller = EnvImport(owner.lifecycle)
    preview = controller.prepare(str(source))
    existing = owner.service.save_value('', 'SERVICE_URL', 'variable', 'existing-private', 'fixture-password')
    with pytest.raises(EnvImportError, match='env_import_conflict'):
        controller.commit(preview['draft_id'], '{"API_TOKEN":"secret","SERVICE_URL":"variable"}', 'fixture-password')
    assert owner.lifecycle.list_values() == [existing]
    assert owner.lifecycle.reveal_value(existing['credential_id'], 'fixture-password') == 'existing-private'
    refreshed = controller.prepare(str(source))
    assert next(entry for entry in refreshed['entries'] if entry['name'] == 'SERVICE_URL')['exists']


def test_sql_failure_rolls_back_inserted_rows(setup):
    owner, source = setup
    controller = EnvImport(owner.lifecycle)
    preview = controller.prepare(str(source))
    with sqlite3.connect(owner.lifecycle.db_path) as conn:
        conn.execute("CREATE TRIGGER reject_import BEFORE INSERT ON credentials WHEN NEW.service='desktop-value:SERVICE_URL' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        controller.commit(preview['draft_id'], '{"API_TOKEN":"secret","SERVICE_URL":"variable"}', 'fixture-password')
    assert owner.lifecycle.list_values() == []


@pytest.mark.parametrize('change', ['contents', 'replace', 'delete', 'symlink'])
def test_changed_source_is_not_imported(setup, change):
    owner, source = setup
    controller = EnvImport(owner.lifecycle)
    preview = controller.prepare(str(source))
    if change == 'contents': source.write_text('API_TOKEN=changed-private')
    elif change == 'replace':
        content = source.read_bytes(); source.unlink(); source.write_bytes(content)
    elif change == 'delete': source.unlink()
    else:
        source.unlink(); source.symlink_to('/etc/passwd')
    with pytest.raises(EnvImportError, match='env_import_changed'):
        controller.commit(preview['draft_id'], '{"API_TOKEN":"secret"}', 'fixture-password')
    assert owner.lifecycle.list_values() == [] and controller.draft is None


@pytest.mark.parametrize('selection', ['{}', '[]', 'null', '{"MISSING":"secret"}', '{"API_TOKEN":"wrong"}',
    '{"EMPTY":"secret"}', '{"API_TOKEN":{}}', '{"API_TOKEN":"secret","API_TOKEN":"variable"}'])
def test_invalid_selections_cannot_write(setup, selection):
    owner, source = setup
    controller = EnvImport(owner.lifecycle)
    preview = controller.prepare(str(source))
    with pytest.raises(EnvImportError):
        controller.commit(preview['draft_id'], selection, 'fixture-password')
    assert owner.lifecycle.list_values() == []


def test_draft_expiry_cancel_new_preview_and_vault_lock_are_binding(setup):
    owner, source = setup
    now = [100]
    controller = EnvImport(owner.lifecycle, clock=lambda: now[0])
    draft = controller.prepare(str(source))
    now[0] = 401
    with pytest.raises(EnvImportError, match='env_import_expired'):
        controller.commit(draft['draft_id'], '{"API_TOKEN":"secret"}', 'fixture-password')
    first = owner.service.prepare_env_import(str(source)); second = owner.service.prepare_env_import(str(source))
    owner.service.cancel_env_import(first['draft_id'])
    assert owner.service._env_import.draft['id'] == second['draft_id']
    owner.service.lock(); owner.service.unlock('fixture-password')
    with pytest.raises(EnvImportError, match='env_import_expired'):
        owner.service.commit_env_import(second['draft_id'], '{"API_TOKEN":"secret"}', 'fixture-password')


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'directory', 'oversized'])
def test_source_file_boundary(setup, tmp_path, kind):
    owner, source = setup
    original = tmp_path / 'original'; original.write_text('TOKEN=private')
    source.unlink()
    if kind == 'symlink': source.symlink_to(original)
    elif kind == 'hardlink': os.link(original, source)
    elif kind == 'fifo': os.mkfifo(source)
    elif kind == 'directory': source.mkdir()
    else: source.write_bytes(b'x' * (256 * 1024 + 1))
    with pytest.raises(EnvImportError):
        owner.service.prepare_env_import(str(source))
    assert owner.service._env_import.draft is None


@pytest.mark.parametrize('principal', [VaultPrincipal.DEVICE, VaultPrincipal.TOOL, 'owner'])
def test_import_management_is_owner_only(setup, principal):
    owner, source = setup
    service = VaultManagement(owner.lifecycle, principal=principal)
    for operation in [lambda: service.prepare_env_import(str(source)),
                      lambda: service.commit_env_import('a' * 64, '{}', 'fixture-password'), service.cancel_env_import]:
        with pytest.raises(VaultManagementDenied): operation()


def test_owner_protocol_strict_fields_safe_errors_and_interface_lock(setup):
    owner, source = setup
    def call(action, **fields): return owner.dispatch({'id': 'import', 'action': action, **fields})
    assert call('prepare_env_import', source=str(source), role='owner')['error'] == 'invalid_request'
    draft = call('prepare_env_import', source=str(source))['result']['env_import']
    assert 'fixture-private-token' not in json.dumps(draft)
    owner.lock_enabled = True
    assert call('lock_interface')['result']['interface_locked']
    assert call('commit_env_import', draft_id=draft['draft_id'], selection='{"API_TOKEN":"secret"}', password='fixture-password')['error'] == 'interface_locked'
    call('unlock_interface', password='fixture-password')
    assert call('commit_env_import', draft_id=draft['draft_id'], selection='{"API_TOKEN":"secret"}', password='fixture-password')['error'] == 'env_import_expired'
    source.write_text('API_TOKEN="private-unclosed')
    reply = call('prepare_env_import', source=str(source))
    assert reply['error'] == 'env_import_invalid' and reply['error_line'] == 1
    assert 'private-unclosed' not in json.dumps(reply)


def test_stale_commit_keeps_current_draft_and_password_change_clears_it(setup):
    owner, source = setup
    first = owner.service.prepare_env_import(str(source)); current = owner.service.prepare_env_import(str(source))
    with pytest.raises(EnvImportError, match='env_import_expired'):
        owner.service.commit_env_import(first['draft_id'], '{"API_TOKEN":"secret"}', 'fixture-password')
    assert owner.service._env_import.draft['id'] == current['draft_id']
    owner.service.change_password('fixture-password', 'new-password')
    assert owner.service._env_import.draft is None
    with pytest.raises(EnvImportError, match='env_import_expired'):
        owner.service.commit_env_import(current['draft_id'], '{"API_TOKEN":"secret"}', 'new-password')


def test_import_binds_to_restored_management_lifecycle(setup, tmp_path):
    owner, source = setup
    backup = owner.lifecycle.export_recovery('fixture-recovery')
    service = VaultManagement(VaultLifecycle(tmp_path / 'restored-vault'), principal=VaultPrincipal.OWNER)
    service.restore_recovery(backup, 'fixture-recovery', 'restored-password')
    try:
        draft = service.prepare_env_import(str(source))
        result = service.commit_env_import(draft['draft_id'], '{"API_TOKEN":"secret"}', 'restored-password')
        assert result['imported'] == 1 and owner.service.list_values() == []
    finally:
        service.lock()
