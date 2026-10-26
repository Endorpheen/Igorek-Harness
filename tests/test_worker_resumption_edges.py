"""Worker stop and resumed host-operation boundaries without executing tasks."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import worker as w
from factory.core import cli_tasks, cc_backend_executor, tool_runtime_context
from factory.core.isolation import host_op_resume, host_op_runtime


@pytest.mark.parametrize('errors,expected', [(None, '-'), ([], '-'), ([None, 'bad', {}], '-'), ([{'name':' a ','error':' broken '}], 'a:broken'), ([{'name':'a'},{'error':'b'}], 'a, b')])
def test_mcp_error_summary_retains_names_and_reasons(errors, expected):
    assert w._format_mcp_errors(errors) == expected


@pytest.mark.parametrize('interrupt,task_id,stop,status,expected_source', [
    (True, '', False, None, 'runtime_interrupt'),
    (False, '', False, None, ''),
    (False, 'owned-task', False, 'running', ''),
    (False, 'owned-task', True, None, 'task_row_missing'),
    (False, 'owned-task', True, 'cancelling', 'task_status_cancelling'),
    (False, 'owned-task', True, 'cancelled', 'task_stop_predicate_changed'),
    (False, 'owned-task', True, 'read-error', 'task_stop_predicate_db_error_or_row_missing'),
])
def test_isolation_stop_reason_tracks_real_predicate_boundary(monkeypatch, capsys, interrupt, task_id, stop, status, expected_source):
    monkeypatch.setattr(tool_runtime_context, 'is_interrupt_requested', lambda: interrupt)
    predicate = Mock(return_value=stop)
    monkeypatch.setattr(cc_backend_executor, 'task_should_stop', predicate)
    reader = Mock(side_effect=OSError('fixture DB') if status == 'read-error' else None, return_value=None if status is None else {'status':status})
    monkeypatch.setattr(cli_tasks, 'get_task', reader)
    assert w._isolation_cancel_check(task_id)() is bool(expected_source)
    output = capsys.readouterr().out
    if expected_source:
        assert 'source='+expected_source in output
    else:
        assert output == ''
    if interrupt or not task_id:
        predicate.assert_not_called()
    if not stop or interrupt:
        reader.assert_not_called()


@pytest.mark.parametrize('key', ['host_op_resolutions', 'host_op_session_grants'])
def test_resumed_operations_replace_metadata_only_after_success(monkeypatch, key):
    metadata = {key:['fixture'], 'stale':'discard'}
    result = SimpleNamespace(refused=False, metadata={'fresh':True}, executed=['fixture-op'])
    consumer = Mock(return_value=result)
    monkeypatch.setattr(host_op_resume, 'consume_pending_host_op_resolutions', consumer)
    runtime = object()
    monkeypatch.setattr(w, '_get_host_op_runtime', lambda: runtime)
    assert w._consume_host_op_resolutions('owned-task', metadata) == ''
    assert metadata == {'fresh':True}
    consumer.assert_called_once_with(task_id='owned-task', runtime=runtime)


@pytest.mark.parametrize('error', [OSError('fixture disk'), sqlite3.OperationalError('fixture database')])
def test_resolution_io_error_blocks_execution_and_preserves_metadata(monkeypatch, error):
    metadata = {'host_op_resolutions':['fixture']}
    monkeypatch.setattr(w, '_get_host_op_runtime', lambda: None)
    monkeypatch.setattr(host_op_resume, 'consume_pending_host_op_resolutions', Mock(side_effect=error))
    assert w._consume_host_op_resolutions('owned-task', metadata) == 'host_op_resolution_failed'
    assert metadata == {'host_op_resolutions':['fixture']}


def test_refused_resolution_remains_explicit(monkeypatch):
    result = SimpleNamespace(refused=True, refused_reason='fixture_expired', metadata={'wrong':1})
    monkeypatch.setattr(w, '_get_host_op_runtime', lambda: None)
    monkeypatch.setattr(host_op_resume, 'consume_pending_host_op_resolutions', lambda **k: result)
    metadata = {'host_op_resolutions':['fixture']}
    assert w._consume_host_op_resolutions('owned-task', metadata) == 'fixture_expired'
    assert metadata == {'host_op_resolutions':['fixture']}


def test_no_resolutions_never_constructs_host_runtime(monkeypatch):
    builder = Mock(side_effect=AssertionError('unexpected runtime'))
    monkeypatch.setattr(w, '_get_host_op_runtime', builder)
    assert w._consume_host_op_resolutions('owned-task', {'other':1}) == ''
    builder.assert_not_called()


@pytest.mark.parametrize('available', [True, False])
def test_host_runtime_resolution_is_cached_including_unavailable(monkeypatch, available):
    runtime = object() if available else None
    builder = Mock(return_value=runtime)
    monkeypatch.setattr(host_op_runtime, 'build_host_op_runtime', builder)
    monkeypatch.setattr(w, '_HOST_OP_RUNTIME', None)
    monkeypatch.setattr(w, '_HOST_OP_RUNTIME_RESOLVED', False)
    assert w._get_host_op_runtime() is runtime
    assert w._get_host_op_runtime() is runtime
    builder.assert_called_once_with()
