"""Worker skill changes reach the actual task execution boundary."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import worker as w
from test_worker_execution_contracts import worker_factory as worker_factory, task


@pytest.mark.parametrize('needs_reload,loaded', [(True, True), (False, True), (True, False)])
def test_worker_refreshes_and_passes_skill_context(worker_factory, monkeypatch, needs_reload, loaded):
    worker = worker_factory()
    manager = SimpleNamespace(
        needs_reload=Mock(return_value=needs_reload),
        load_settings=Mock(), load_skills=Mock(), skills=['fixture'], is_loaded=loaded,
        build_skill_runtime_context=Mock(return_value=('fixture skill instructions', {'FIXTURE_SKILL': 'enabled'})),
    )
    monkeypatch.setattr(w, 'SKILLS_AVAILABLE', True)
    monkeypatch.setattr(w, 'skills_manager', manager)
    backend = Mock(return_value={'status': 'ok', 'output': {'text': 'fixture answer'}})
    monkeypatch.setattr(worker, '_execute_cc_backend', backend)
    result = worker.execute_task(task(input_text='fixture user request'))

    assert result['status'] == 'ok'
    assert manager.load_settings.call_count == int(needs_reload)
    assert manager.load_skills.call_count == int(needs_reload)
    assert manager.build_skill_runtime_context.call_count == int(loaded)
    assert backend.call_args.args[3] == 'fixture user request'
    assert backend.call_args.args[4] == ('fixture skill instructions' if loaded else '')
    if loaded:
        assert manager.build_skill_runtime_context.call_args.kwargs['backend'] == 'cc-backend'
        assert manager.build_skill_runtime_context.call_args.kwargs['workdir'] == backend.call_args.args[2]
