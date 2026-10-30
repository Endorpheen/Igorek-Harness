"""Normal terminal answers must not require a synthetic completion tool."""
import pytest

from factory.core import cc_backend_executor as e
from test_executor_tool_loop_edges import loop
from test_executor_workflow_guards import response


@pytest.fixture(autouse=True)
def confirmed_local_backend(monkeypatch):
    def probe(policy):
        policy.backend = 'llama_cpp'
    monkeypatch.setattr(e.LlamaImageInput, '_probe', probe)


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
@pytest.mark.parametrize('base', ['http://127.0.0.1:1', 'https://cloud.example.test/v1'])
@pytest.mark.parametrize('user,answer', [
    ('Привет!', 'Привет! 👋 Как я могу тебе помочь сегодня?'),
    ('Как дела?', 'Всё хорошо, спасибо!'),
    ('Ты молодец!', 'Спасибо! Рад помочь.'),
    ('Объясни, что такое контекст.', 'Контекст — доступная модели история разговора.'),
    ('Покажи пример команды, ничего не запускай.', 'Пример: `node --version`.'),
    ('Спасибо.', 'Пожалуйста!'),
])
def test_ordinary_answer_finishes_once_without_a_control_tool(loop, monkeypatch, api, base, user, answer):
    monkeypatch.setattr(e, "_resolve_backend_context_window", lambda **kw: (262144, "fixture"))
    run = loop(api, [response(api, answer)] * 3, names=['Read'], base_url=base,
               user_message=user, max_output_tokens=65536, metadata={"model_context_window":262144})
    assert run.result['status'] == 'ok' and run.result['output_text'] == answer
    assert len(run.requests) == 1 and run.calls == []
    payload = run.requests[0]
    assert payload['max_tokens'] == 65536 and 'tool_choice' not in payload
    schemas = payload['tools']
    assert all(item.get('name', item.get('function', {}).get('name')) != 'finish_task' for item in schemas)
    system = payload['messages'][0]['content'] if api == 'openai' else payload['system']
    assert 'finish_task' not in str(system) and 'Local task protocol' not in str(system)
    assert not any(row.get('reason') == 'missing_finish_signal' or row['kind'] == 'model.completion_review' for row in run.trace)


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
def test_real_work_tools_and_their_normal_final_answer_still_work(loop, api):
    tool = {'id': 'read-current', 'name': 'Read', 'input': {'file_path': 'input.txt'}}
    run = loop(api, [response(api, tool=tool), response(api, 'Файл прочитан.')], names=['Read'])
    assert run.result['status'] == 'ok' and run.result['output_text'] == 'Файл прочитан.'
    assert len(run.requests) == 2 and len(run.calls) == 1
    assert run.result['tool_calls_count'] == 1


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
def test_clarification_or_honest_blocker_does_not_require_a_fake_tool(loop, api):
    answer = 'Нужен адрес страницы, чтобы продолжить.'
    run = loop(api, [response(api, answer)], names=['desktop_browser'])
    assert run.result['status'] == 'ok' and run.result['output_text'] == answer
    assert len(run.requests) == 1 and run.calls == []
