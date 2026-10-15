"""Mentioning a saved fact never grants permission to delete it."""
import json
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from factory.core.agent_foundation import get_foundation_paths
from factory.core import cc_backend_tools as tools


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    path = get_foundation_paths(str(tmp_path)).memory_index
    path.write_text('# Memory\n\n## Saved facts\n- synthetic owner fact\n\n## Other\n')
    return path


def test_mention_and_model_supplied_approval_do_not_delete(memory):
    result = json.loads(tools.execute_isolated_memory_forget(
        {'target': 'synthetic owner fact', 'approved': True}, user_message='Explain synthetic owner fact'))
    assert not result['changed']
    assert result['reason'] == 'owner_confirmation_required'
    assert 'synthetic owner fact' in memory.read_text()


@pytest.mark.parametrize('selected,deleted', [(['Сохранить факт'], False), ([], False),
    (['Удалить факт', 'Сохранить факт'], False), (['Удалить факт'], True)])
def test_owner_choice_binds_the_exact_fact_and_preserves_declined_data(memory, selected, deleted):
    seen = []

    def owner(payload):
        seen.append(payload)
        return {'answers': {'memory_forget_decision': {'selected_options': selected}}}

    result = json.loads(tools._handle_memory_forget({'target': 'synthetic owner fact'}, user_input_provider=owner))
    assert 'synthetic owner fact' in seen[0]['questions'][0]['question']
    assert result['changed'] is deleted
    assert ('- synthetic owner fact' in memory.read_text()) is not deleted


def test_guest_forget_waits_for_its_host_owned_form(memory):
    from factory.core.user_input_store import list_pending_user_input_requests, resolve_user_input_request
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tools.execute_isolated_memory_forget,
            {'target': 'synthetic owner fact', 'approved': True, 'request_id': 'guest-forged'},
            user_message='Explain the fact', task_id='host-owned-task', timeout_s=3)
        deadline = time.monotonic() + 2
        pending = []
        while not pending and time.monotonic() < deadline:
            pending = list_pending_user_input_requests(task_id='host-owned-task')
            time.sleep(0.01)
        assert len(pending) == 1
        assert pending[0]['request_id'] != 'guest-forged'
        assert not future.done()
        assert 'synthetic owner fact' in memory.read_text()
        resolve_user_input_request(pending[0]['request_id'], response={
            'answers': {'memory_forget_decision': {'selected_options': ['Удалить факт']}}})
        assert json.loads(future.result(timeout=2))['changed']
    assert '- synthetic owner fact' not in memory.read_text()
