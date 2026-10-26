"""ACP entrypoint keeps stdout clean and reports lifecycle failures honestly."""

import asyncio
import io
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from factory.core.acp_bridge import entry


def test_logging_goes_to_stderr_without_polluting_protocol_stdout(monkeypatch):
    error = io.StringIO()
    output = io.StringIO()
    root = logging.getLogger()
    old_level = root.level
    other = [logging.getLogger(name) for name in ['httpx', 'httpcore']]
    old_other = [item.level for item in other]
    monkeypatch.setattr(root, 'handlers', [])
    monkeypatch.setattr(entry, 'sys', SimpleNamespace(stderr=error, stdout=output))
    try:
        entry._setup_logging()
        logging.getLogger('owned-fixture').info('owned ACP message')
        assert 'owned ACP message' in error.getvalue() and output.getvalue() == ''
        assert all(item.level == logging.WARNING for item in other)
    finally:
        root.setLevel(old_level)
        for item, level in zip(other, old_other):
            item.setLevel(level)
        for handler in root.handlers:
            handler.close()


def test_run_passes_exact_agent_to_protocol_boundary(monkeypatch):
    agent = object()
    serve = AsyncMock()
    create = Mock(return_value=agent)
    monkeypatch.setattr(entry, 'FactoryACPAgent', create)
    monkeypatch.setattr(entry, 'acp', SimpleNamespace(run_agent=serve))
    asyncio.run(entry._run())
    create.assert_called_once_with()
    serve.assert_awaited_once_with(agent, use_unstable_protocol=True)


@pytest.mark.parametrize('failure,exit_code', [(KeyboardInterrupt(), None), (RuntimeError('owned fixture failure'), 1)])
def test_run_interrupt_or_error_preserves_exit_contract(monkeypatch, caplog, failure, exit_code):
    monkeypatch.setattr(entry, 'FactoryACPAgent', lambda: object())
    monkeypatch.setattr(entry, 'acp', SimpleNamespace(run_agent=AsyncMock(side_effect=failure)))
    caplog.set_level(logging.INFO)
    if exit_code is None:
        asyncio.run(entry._run())
        assert 'Shutting down' in caplog.text
    else:
        with pytest.raises(SystemExit) as caught:
            asyncio.run(entry._run())
        assert caught.value.code == exit_code and 'ACP agent crashed' in caplog.text


def test_main_configures_then_runs_once_and_does_not_duplicate_search_path(monkeypatch):
    configured = Mock()
    run = AsyncMock()
    path = []
    monkeypatch.setattr(entry, '_setup_logging', configured)
    monkeypatch.setattr(entry, '_run', run)
    monkeypatch.setattr(entry, 'sys', SimpleNamespace(path=path, stderr=io.StringIO(), exit=sys.exit))
    entry.main()
    assert len(path) == 1 and path[0]
    entry.main()
    assert len(path) == 1 and configured.call_count == run.await_count == 2
