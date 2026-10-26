"""File search replaces query results and drops stale session/query reports."""

import threading
from unittest.mock import Mock

import pytest

from factory.core.tui import file_search as fs


def make_tree(root):
    (root / 'nested').mkdir(parents=True)
    (root / 'Alpha.py').write_text('alpha')
    (root / 'beta.txt').write_text('beta')
    (root / 'nested/other.py').write_text('other')
    return root


def test_walk_search_replaces_old_results_and_deduplicates_roots(tmp_path):
    root = make_tree(tmp_path / 'root')
    search = fs.OsWalkFileSearchSession([root, root, tmp_path / 'absent'], fs.FileSearchOptions())
    search.update_query('ALPHA')
    assert search.snapshot().matches == ['Alpha.py']
    search.update_query('nested')
    snapshot = search.snapshot()
    assert snapshot.query == 'nested' and snapshot.matches == ['nested/other.py']
    snapshot.matches.append('external mutation')
    assert search.snapshot().matches == ['nested/other.py']
    search.update_query('no matches')
    assert search.snapshot().matches == []
    search.update_query('')
    assert search.snapshot() == fs.FileSearchSnapshot('', [])


def test_manager_reuses_session_until_query_cleared_or_root_changed(tmp_path, monkeypatch):
    sessions = []

    def create(roots, options, reporter):
        value = Mock()
        sessions.append((roots, reporter, value))
        return value

    monkeypatch.setattr(fs, 'create_session', create)
    output = []
    manager = fs.FileSearchManager(tmp_path, output.append)
    manager.on_user_query('one')
    manager.on_user_query('one')
    manager.on_user_query('two')
    assert len(sessions) == 1
    assert [c.args[0] for c in sessions[0][2].update_query.call_args_list] == ['one', 'two']
    manager.on_user_query('')
    manager.on_user_query('three')
    assert len(sessions) == 2
    manager.update_search_dir(tmp_path / 'new')
    manager.on_user_query('four')
    assert len(sessions) == 3 and sessions[2][0] == [tmp_path / 'new']
    sessions[0][1].on_update(fs.FileSearchSnapshot('two', ['stale']))
    sessions[1][1].on_update(fs.FileSearchSnapshot('three', ['stale']))
    assert output == []
    sessions[2][1].on_update(fs.FileSearchSnapshot('four', ['current']))
    assert output[0].query == 'four' and output[0].matches == ['current']


@pytest.mark.parametrize('snapshot_query', ['', 'previous'])
def test_reporter_discards_empty_or_previous_query_inside_same_session(snapshot_query):
    state = fs._SearchState(latest_query='current', session_token=5)
    messages = []
    reporter = fs.TuiSessionReporter(threading.RLock(), state, messages.append, 5)
    reporter.on_update(fs.FileSearchSnapshot(snapshot_query, ['wrong-file']))
    reporter.on_complete()
    assert messages == []


def test_reporter_publishes_a_copy_and_suppresses_after_clear():
    state = fs._SearchState(latest_query='current', session_token=5)
    messages = []
    reporter = fs.TuiSessionReporter(threading.RLock(), state, messages.append, 5)
    source = fs.FileSearchSnapshot('current', ['a'])
    reporter.on_update(source)
    source.matches.append('b')
    assert messages[0].matches == ['a']
    state.latest_query = ''
    reporter.on_update(source)
    assert len(messages) == 1


def test_failed_session_start_is_nonfatal_and_next_query_can_retry(tmp_path, monkeypatch):
    create = Mock(side_effect=OSError('fixture backend unavailable'))
    monkeypatch.setattr(fs, 'create_session', create)
    manager = fs.FileSearchManager(tmp_path, lambda event: None)
    with pytest.warns(UserWarning, match='failed to start'):
        manager.on_user_query('one')
    good = Mock()
    create.side_effect = None
    create.return_value = good
    manager.on_user_query('two')
    good.update_query.assert_called_once_with('two')


def test_default_factory_reads_only_given_owned_roots(tmp_path):
    root = make_tree(tmp_path / 'owned')
    search = fs.create_session([root], fs.FileSearchOptions(), Mock())
    search.update_query('.py')
    assert search.snapshot().matches == ['Alpha.py', 'nested/other.py']
