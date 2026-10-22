import importlib
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

search = importlib.import_module('factory.core.session_search')


@pytest.fixture
def history(tmp_path, monkeypatch):
    directory = tmp_path / 'sessions'
    directory.mkdir()
    monkeypatch.setattr(search, 'get_chat_sessions_dir', lambda: str(directory))
    store = SimpleNamespace(
        embedder=SimpleNamespace(available=False),
        search_lexical=Mock(return_value=[]),
        search_semantic=Mock(return_value=[]),
    )
    monkeypatch.setattr(search, 'SessionRecallStore', lambda: store)

    def write(name, entries, modified=100):
        path = directory / (name + '.jsonl')
        path.write_text('\n'.join(json.dumps(e) if not isinstance(e, str) else e for e in entries))
        os.utime(path, (modified, modified))
        return path

    return write, store


def result(**kwargs):
    return json.loads(search.session_search(**kwargs))


def test_recent_history_ignores_current_session_and_non_messages(history):
    write, _ = history
    write('current', [{'role': 'user', 'text': 'Current task'}], modified=300)
    write('broken', ['{broken', '', ['not a message'], {'role': 'tool', 'text': 'Tool-only'}], modified=200)
    write('previous', [{'role': 'meta'}, {'role': 'user', 'text': 'Question', 'ts': 1},
                       {'role': 'assistant', 'text': '  Useful\n answer  ', 'ts': 2}])
    payload = result(current_session='current')
    assert payload['success'] is True
    assert payload['mode'] == 'recent'
    assert payload['count'] == 1
    assert payload['results'][0]['session'] == 'previous'
    assert payload['results'][0]['message_count'] == 2
    assert payload['results'][0]['preview'] == 'Useful answer'


@pytest.mark.parametrize(('limit', 'expected'), [(99, 5), (1, 1), (-4, 1), (0, 3)])
def test_recent_results_are_newest_first_and_bounded(history, limit, expected):
    write, _ = history
    for i in range(7):
        write(f'chat-{i}', [{'role': 'user', 'text': f'Message {i}'}], modified=100 + i)
    payload = result(limit=limit)
    assert payload['count'] == expected
    assert [item['session'] for item in payload['results']] == [f'chat-{i}' for i in range(6, 6 - expected, -1)]


def test_recent_preview_is_bounded_and_skips_empty_last_message(history):
    write, _ = history
    write('long', [{'role': 'user', 'text': 'x' * 700}, {'role': 'assistant', 'text': ' \n '}])
    preview = result()['results'][0]['preview']
    assert preview == 'x' * 500 + '…'


def test_lexical_search_filters_roles_and_excludes_current_chat(history):
    write, store = history
    write('current', [{'role': 'user', 'text': 'needle'}], modified=300)
    write('assistant-only', [{'role': 'assistant', 'text': 'needle'}], modified=200)
    write('user-match', [{'role': 'user', 'text': 'Useful NEEDLE text'},
                         {'role': 'assistant', 'text': 'Unrelated answer'}])
    payload = result(query=' needle ', current_session='current', role_filter='user')
    assert payload['count'] == 1
    assert payload['results'][0]['session'] == 'user-match'
    assert payload['results'][0]['excerpt'] == 'user: Useful NEEDLE text'
    assert payload['results'][0]['matched_terms'] == ['needle']
    assert payload['results'][0]['source'] == 'transcript'
    store.search_lexical.assert_called_once_with('needle', current_session='current', limit=3)
    store.search_semantic.assert_not_called()


@pytest.mark.parametrize('query', ['alpha OR beta OR ALPHA', '"alpha" "beta" "ALPHA"'])
def test_multiple_search_terms_are_deduplicated_and_ranked_by_matches(history, query):
    write, _ = history
    write('newer-single', [{'role': 'user', 'text': 'alpha only'}], modified=200)
    write('older-both', [{'role': 'user', 'text': 'alpha and beta'}], modified=100)
    payload = result(query=query, limit=2)
    assert [item['session'] for item in payload['results']] == ['older-both', 'newer-single']
    assert payload['results'][0]['matched_terms'] == ['alpha', 'beta']
    assert all('_rank' not in item and 'score' not in item for item in payload['results'])


def test_search_no_match_does_not_return_unrelated_history(history):
    write, _ = history
    write('unrelated', [{'role': 'user', 'text': 'ordinary content'}])
    assert result(query='absent')['results'] == []


def test_unknown_mode_falls_back_to_lexical_even_when_semantic_is_available(history):
    write, store = history
    write('match', [{'role': 'user', 'text': 'needle'}])
    store.embedder.available = True
    payload = result(query='needle', mode='unsupported')
    assert payload['requested_mode'] == payload['effective_mode'] == 'lexical'
    store.search_semantic.assert_not_called()


@pytest.mark.parametrize('mode', ['semantic', 'hybrid'])
def test_semantic_unavailable_falls_back_to_lexical_without_model_call(history, mode):
    write, store = history
    write('match', [{'role': 'user', 'text': 'needle'}])
    payload = result(query='needle', mode=mode)
    assert payload['requested_mode'] == mode
    assert payload['effective_mode'] == 'lexical'
    assert payload['semantic_available'] is False
    assert payload['results'][0]['session'] == 'match'
    store.search_semantic.assert_not_called()


@pytest.mark.parametrize('mode', ['semantic', 'hybrid'])
def test_available_semantic_results_are_merged_with_recall_without_internal_fields(history, mode):
    _, store = history
    store.embedder.available = True
    store.search_lexical.return_value = [{'entry_id': 'same', 'session': 'remembered', 'excerpt': 'A useful outcome'}]
    store.search_semantic.return_value = [{'entry_id': 'same', 'session': 'remembered', 'excerpt': 'A useful outcome',
                                          'semantic_score': 0.8, '_provenance': 'local'}]
    payload = result(query='previous decision', mode=mode)
    assert payload['effective_mode'] == mode
    assert payload['count'] == 1
    assert payload['results'][0]['semantic_score'] == 0.8
    assert '_rank' not in payload['results'][0]
    assert '_provenance' not in payload['results'][0]
    assert store.search_semantic.return_value[0]['_provenance'] == 'local'
    store.search_semantic.assert_called_once_with('previous decision', current_session=None, limit=3)


def test_semantic_model_with_no_matches_keeps_lexical_results(history):
    write, store = history
    store.embedder.available = True
    write('match', [{'role': 'user', 'text': 'needle'}])
    payload = result(query='needle', mode='semantic')
    assert payload['semantic_available'] is True
    assert payload['effective_mode'] == 'lexical'
    assert payload['results'][0]['session'] == 'match'


def test_long_excerpt_shows_match_context_without_exceeding_bound(history):
    write, _ = history
    write('long', [{'role': 'user', 'text': 'x' * 800 + ' target ' + 'y' * 1500}])
    excerpt = result(query='target')['results'][0]['excerpt']
    assert 'target' in excerpt
    assert excerpt.startswith('… ') and excerpt.endswith(' …')
    assert len(excerpt) <= 1200
