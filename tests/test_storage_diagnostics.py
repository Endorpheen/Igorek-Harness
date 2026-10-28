"""Storage diagnostics use native evidence without filling the host filesystem."""
import errno
import json
import sqlite3
import ssl

import pytest

from factory.core.diagnostics import classify_error


def sqlite_full_error():
    with sqlite3.connect(':memory:') as db:
        db.execute('PRAGMA page_size=512')
        db.execute('PRAGMA max_page_count=2')
        db.execute('CREATE TABLE items (value BLOB)')
        try:
            db.execute('INSERT INTO items VALUES (zeroblob(4096))')
        except sqlite3.OperationalError as error:
            assert error.sqlite_errorcode == sqlite3.SQLITE_FULL
            return error
    raise AssertionError('SQLite did not reach the configured page limit')


def test_real_sqlite_page_limit_is_storage_full():
    assert classify_error(sqlite_full_error(), status=500) == 'STORAGE.FULL'


def test_full_storage_evidence_is_retained_without_private_exception_text():
    from factory.core.diagnostics import exception_details, observation
    error = sqlite_full_error()
    error.args = ('EXCLUDED private database path or SQL',)
    details = exception_details(error)
    assert details == {'native_code': 'OperationalError', 'reason_code': 'SQLITE_FULL'}
    event = observation(classify_error(error), component='bridge', operation='read',
                        endpoint_role='client_to_bridge', **details)
    assert event['reason_code'] == 'SQLITE_FULL'
    assert 'EXCLUDED' not in json.dumps(event)


@pytest.mark.parametrize('number,code,reason', [
    (errno.ENOSPC, 'STORAGE.FULL', 'ENOSPC'),
    (errno.EDQUOT, 'STORAGE.QUOTA_EXCEEDED', 'EDQUOT'),
    (errno.EROFS, 'STORAGE.READ_ONLY', 'EROFS'),
    (errno.EIO, 'STORAGE.IO_ERROR', 'EIO'),
])
def test_os_storage_errors_have_distinct_causes(number, code, reason):
    from factory.core.diagnostics import exception_details
    error = OSError(number, 'EXCLUDED')
    assert classify_error(error) == code
    assert exception_details(error)['reason_code'] == reason


def test_sqlite_lock_is_not_full_and_releases_normally(tmp_path):
    path = tmp_path/'db.sqlite'
    with sqlite3.connect(path) as first, sqlite3.connect(path, timeout=0) as second:
        first.execute('CREATE TABLE items(value)')
        first.execute('BEGIN EXCLUSIVE')
        try:
            with pytest.raises(sqlite3.OperationalError) as failed:
                second.execute('INSERT INTO items VALUES (1)')
            assert classify_error(failed.value) == 'DATABASE.BUSY'
        finally:
            first.rollback()
        second.execute('INSERT INTO items VALUES (2)')
        assert second.execute('SELECT value FROM items').fetchall() == [(2,)]


def test_sqlite_read_only_is_not_storage_full(tmp_path):
    path = tmp_path/'db.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE items(value)')
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        with pytest.raises(sqlite3.OperationalError) as failed:
            db.execute('INSERT INTO items VALUES (1)')
    assert classify_error(failed.value) == 'STORAGE.READ_ONLY'


def test_invalid_database_is_not_free_space_advice(tmp_path):
    path = tmp_path/'db.sqlite'
    path.write_bytes(b'EXCLUDED not a sqlite database' * 100)
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.DatabaseError) as failed:
            db.execute('SELECT * FROM sqlite_master').fetchall()
    assert classify_error(failed.value) == 'DATABASE.INVALID'


def test_sqlite_extended_code_retains_the_specific_native_reason():
    from factory.core.diagnostics import exception_details
    error = sqlite3.OperationalError('EXCLUDED')
    error.sqlite_errorcode = sqlite3.SQLITE_IOERR_WRITE
    error.sqlite_errorname = 'SQLITE_IOERR_WRITE'
    assert classify_error(error) == 'STORAGE.IO_ERROR'
    assert exception_details(error)['reason_code'] == 'SQLITE_IOERR_WRITE'


def test_wrapped_exception_keeps_the_storage_cause_and_handles_cycles():
    from factory.core.diagnostics import exception_details
    inner = sqlite_full_error()
    wrapper = RuntimeError('EXCLUDED')
    wrapper.__cause__ = inner
    inner.__cause__ = wrapper
    assert classify_error(wrapper) == 'STORAGE.FULL'
    assert exception_details(wrapper)['reason_code'] == 'SQLITE_FULL'


def test_explicit_inner_storage_cause_beats_an_unspecific_outer_errno():
    from factory.core.diagnostics import exception_details
    wrapper = OSError(errno.EINVAL, 'EXCLUDED')
    wrapper.__cause__ = sqlite_full_error()
    assert classify_error(wrapper) == 'STORAGE.FULL'
    assert exception_details(wrapper)['reason_code'] == 'SQLITE_FULL'


def test_unknown_operational_error_does_not_guess_disk_exhaustion():
    from factory.core.diagnostics import exception_details
    error = sqlite3.OperationalError('database or disk is full EXCLUDED')
    assert classify_error(error) == 'INTERNAL.UNEXPECTED'
    assert exception_details(error) == {'native_code': 'OperationalError'}


def test_sql_error_stays_internal_with_a_safe_native_code():
    from factory.core.diagnostics import exception_details
    with sqlite3.connect(':memory:') as db:
        with pytest.raises(sqlite3.OperationalError) as failed:
            db.execute('SELECT EXCLUDED FROM missing_private_table')
    assert classify_error(failed.value) == 'INTERNAL.UNEXPECTED'
    assert exception_details(failed.value)['reason_code'] == 'SQLITE_ERROR'
    assert 'EXCLUDED' not in json.dumps(exception_details(failed.value))


def test_tls_library_error_number_is_not_a_filesystem_errno():
    from factory.core.diagnostics import exception_details
    error = ssl.SSLError(errno.EIO, 'EXCLUDED')
    assert classify_error(error) == 'TLS.PROTOCOL_FAILURE'
    assert exception_details(error) == {'native_code': 'SSLError'}
