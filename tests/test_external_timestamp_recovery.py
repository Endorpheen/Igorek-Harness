"""Malformed imported timestamps cannot abort unrelated external histories."""
import json
import sqlite3

import pytest

import test_external_sessions as fixtures
from factory.core.external_sessions import _epoch_seconds


@pytest.fixture
def source():
    case = fixtures.ExternalSessionCatalogTest();case.setUp()
    try:yield case
    finally:case.tearDown()


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_invalid_claude_index_timestamps_do_not_abort_catalog(source, value):
    path = next(source.claude.rglob('sessions-index.json'));data=json.loads(path.read_text())
    data['entries'][0].update(created=value,modified=value);path.write_text(json.dumps(data))
    result=source.catalog.list_sessions(refresh=True)
    assert result['counts']=={'claude':1,'codex':1,'opencode':1}
    row=next(x for x in result['sessions'] if x['source']=='claude')
    assert row['created_at']==row['updated_at']==0
    assert source.catalog.get_session('claude','claude-1')['messages']


@pytest.mark.parametrize('provider', ['codex','opencode'])
@pytest.mark.parametrize('value', [float('inf'),float('-inf')])
def test_nonfinite_sqlite_dates_keep_other_sources_available(source, provider, value):
    path=source.codex/'state_5.sqlite' if provider=='codex' else source.opencode
    with sqlite3.connect(path) as db:
        if provider=='codex':db.execute('UPDATE threads SET created_at_ms=?,updated_at_ms=?',(value,value))
        else:db.execute('UPDATE session SET time_created=?,time_updated=?',(value,value))
    result=source.catalog.list_sessions(refresh=True)
    assert result['total']==3
    row=next(x for x in result['sessions'] if x['source']==provider)
    assert row['created_at']==row['updated_at']==0


@pytest.mark.parametrize('value', [10**400,-10**400])
def test_unrepresentable_numeric_date_is_unknown_instead_of_an_exception(value):
    assert _epoch_seconds(value)==0


@pytest.mark.parametrize('value,expected', [
    (1700000000.9,1700000000),(1700000000000.9,1700000000),
    ('2023-11-15T01:13:20+03:00',1700000000),
    (-100,0),(0,0),(None,0),({},0),([],0),('bad date',0),('',0),
])
def test_existing_timestamp_shapes_and_clamping_remain_compatible(value, expected):
    assert _epoch_seconds(value)==expected
