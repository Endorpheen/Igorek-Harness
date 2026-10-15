"""Pairing capabilities are single-use and remain bound to their issuer."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading

import pytest

from factory.core.mobile_bridge import store as module


@pytest.fixture
def store(tmp_path):
    return module.MobileStore(str(tmp_path / 'factory.db'))


def pair(store, name='Owner'):
    code = store.create_pairing_code()['code']
    return store.complete_pairing(code=code, name=name)


def test_concurrent_exchange_consumes_code_exactly_once(store, monkeypatch):
    code = store.create_pairing_code()['code']
    original = module.open_db
    barrier = threading.Barrier(2)

    class ScheduledConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, *args):
            result = self.connection.execute(query, *args)
            if query.startswith('SELECT expires_at') and not self.connection.in_transaction:
                result = list(result)
                barrier.wait(timeout=5)

                class Result:
                    def fetchone(self):
                        return result[0] if result else None

                return Result()
            return result

    @contextmanager
    def scheduled(*args, **kwargs):
        with original(*args, **kwargs) as connection:
            yield ScheduledConnection(connection)

    monkeypatch.setattr(module, 'open_db', scheduled)

    def exchange(name):
        try:
            return store.complete_pairing(code=code, name=name)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(exchange, ['First', 'Second']))
    assert sum(result is not None for result in results) == 1
    assert len(store.list_devices()) == 1


@pytest.mark.parametrize('removal', ['revoke_device', 'delete_device'])
def test_removed_issuer_cannot_mint_or_redeem_codes(store, removal):
    owner = pair(store)
    creator = 'device:' + owner['device_id']
    code = store.create_pairing_code(created_by=creator)['code']
    getattr(store, removal)(owner['device_id'])
    with pytest.raises(ValueError):
        store.create_pairing_code(created_by=creator)
    with pytest.raises(ValueError):
        store.complete_pairing(code=code, name='Unwanted')
    assert len(store.list_devices()) == 0


def test_issuer_revocation_does_not_remove_independently_paired_devices(store):
    owner = pair(store)
    created = store.create_pairing_code(created_by='device:' + owner['device_id'])
    child = store.complete_pairing(code=created['code'], name='Second')
    store.revoke_device(owner['device_id'])
    assert store.authenticate(child['token']) is not None
    assert store.complete_pairing(code=store.create_pairing_code()['code'], name='CLI')['token']


def test_unbound_legacy_http_codes_require_fresh_owner_pairing(store):
    from factory.core.schema import open_db
    code = store.create_pairing_code()['code']
    with open_db(store.db_path) as connection:
        connection.execute('UPDATE mobile_pairing_codes SET created_by=?', ('http',))
    with pytest.raises(ValueError):
        store.complete_pairing(code=code, name='Unbound')
