"""Mobile CLI operations use an owned database and a substituted listener boundary."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.mobile_bridge import cli
from factory.core.mobile_bridge.store import MobileStore
from factory.core.schema import open_db


@pytest.fixture
def store(tmp_path, monkeypatch):
    owned = MobileStore(db_path=str(tmp_path / 'owned.db'))
    monkeypatch.setattr(cli, 'MobileStore', lambda **kwargs: owned)
    return owned


def args(command, **fields):
    return SimpleNamespace(mobile_command=command, **fields)


def test_pair_prints_a_new_code_without_starting_listener(store, capsys):
    assert cli.run_mobile_command(args('pair', ttl=60)) == 0
    text = capsys.readouterr().out
    assert 'Код сопряжения' in text and 'одноразовый' in text
    with open_db(store.db_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM mobile_pairing_codes').fetchone()[0] == 1


def test_devices_and_revoke_preserve_other_paired_device(store, capsys):
    assert cli.run_mobile_command(args('devices')) == 0
    assert 'нет' in capsys.readouterr().out
    pairing = store.create_pairing_code()
    first = store.complete_pairing(code=pairing['code'], name='Owned phone', platform='android')
    pairing = store.create_pairing_code()
    second = store.complete_pairing(code=pairing['code'], name='Other owned phone', platform='android')
    assert cli.run_mobile_command(args('devices')) == 0
    assert 'Owned phone' in capsys.readouterr().out
    assert cli.run_mobile_command(args('revoke', device_id=first['device_id'])) == 0
    assert store.authenticate(first['token']) is None
    assert store.authenticate(second['token']) is not None
    assert cli.run_mobile_command(args('revoke', device_id='absent')) == 1
    assert cli.run_mobile_command(args('unknown')) == 2


def test_serve_passes_explicit_settings_to_listener_boundary(store, tmp_path, monkeypatch):
    created = []
    def bridge(*, config):
        created.append(config)
        return SimpleNamespace(config=config)
    listen = Mock(return_value=17)
    monkeypatch.setattr(cli, 'MobileBridge', bridge)
    monkeypatch.setattr(cli, 'serve_forever', listen)
    assert cli.run_mobile_command(args('serve', host='127.0.0.1', port=19876, tls_cert='owned-cert', tls_key='owned-key', profile='fixture', workdir=str(tmp_path), poll_interval=.2)) == 17
    config = created[0]
    assert (config.host, config.port, config.profile, config.workdir, config.poll_interval_s) == ('127.0.0.1', 19876, 'fixture', str(tmp_path), .2)
    listen.assert_called_once()


@pytest.mark.parametrize('value', [None, '', 0, -1, True, [], {}, float('nan'), float('inf'), 10**400])
def test_invalid_timestamp_does_not_break_device_listing(value):
    assert cli._format_timestamp(value) == '-'


def test_valid_timestamp_is_displayed_in_the_process_timezone():
    assert cli._format_timestamp(1) == '1970-01-01 00:00'


@pytest.mark.parametrize('existing_key', [None, 'FACTORY_DATA_DIR', 'IGOREK_FACTORY_HOME'])
def test_data_dir_argument_preserves_explicit_environment_precedence(monkeypatch, existing_key):
    for key in ['FACTORY_DATA_DIR', 'IGOREK_FACTORY_HOME']:
        monkeypatch.delenv(key, raising=False)
    if existing_key:
        monkeypatch.setenv(existing_key, 'already-configured')
    cli._apply_data_dir('requested-fixture-dir')
    if existing_key:
        assert __import__('os').environ[existing_key] == 'already-configured'
    else:
        assert __import__('os').environ['FACTORY_DATA_DIR'] == 'requested-fixture-dir'
