"""External history compatibility using isolated JSONL files and real SQLite."""
import hashlib
import json
import sqlite3

import pytest

import test_external_sessions as fixtures


@pytest.fixture
def source():
    case = fixtures.ExternalSessionCatalogTest()
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def rollout(source, payloads):
    path = next((source.codex / 'sessions').rglob('rollout-*.jsonl'))
    rows = [{'type': 'session_meta', 'payload': {'id': 'codex-1', 'cwd': '/fixture', 'timestamp': '2026-01-02T03:04:05Z'}}]
    rows += [{'type': 'response_item', 'timestamp': '2026-01-02T03:04:06Z', 'payload': p} for p in payloads]
    write_rows(path, rows)
    (source.codex / 'thread_history_1.sqlite').unlink(missing_ok=True)
    return path


@pytest.mark.parametrize('kind', ['function_call', 'custom_tool_call'])
@pytest.mark.parametrize('arguments,expected', [('{"path":"fixture"}', {'path': 'fixture'}), ('invalid-json', {'raw': 'invalid-json'}), ({'path': 'fixture'}, {'path': 'fixture'})])
def test_codex_rollout_tool_call_retains_ids_and_structured_arguments(source, kind, arguments, expected):
    rollout(source, [{'type': kind, 'name': 'Read', 'call_id': 'call-one', 'arguments': arguments}])
    messages = source.catalog.get_session('codex', 'codex-1')['messages']
    assert len(messages) == 1
    assert messages[0]['kind'] == 'tool_call' and messages[0]['tool_use_id'] == 'call-one'
    assert messages[0]['tool_name'] == 'Read' and messages[0]['tool_input'] == expected


@pytest.mark.parametrize('kind', ['function_call_output', 'custom_tool_call_output'])
def test_codex_rollout_pairs_tool_results_by_call_id(source, kind):
    rollout(source, [{'type': 'function_call', 'name': 'Read', 'call_id': 'c', 'arguments': '{}'}, {'type': kind, 'call_id': 'c', 'output': 'fixture output'}])
    messages = source.catalog.get_session('codex', 'codex-1')['messages']
    assert [x['kind'] for x in messages] == ['tool_call', 'tool_result']
    assert [x['tool_use_id'] for x in messages] == ['c', 'c']
    assert messages[1]['tool_output'] == 'fixture output'


@pytest.mark.parametrize('role,part', [('user', 'input_text'), ('assistant', 'output_text')])
def test_codex_rollout_text_blocks_keep_order_and_skip_nontext_parts(source, role, part):
    rollout(source, [{'type': 'message', 'role': role, 'content': [{'type': part, 'text': 'first'}, {'type': 'image'}, 7, {'type': part, 'text': 'second'}]}, {'type': 'unknown'}])
    messages = source.catalog.get_session('codex', 'codex-1')['messages']
    assert [(x['role'], x['text']) for x in messages] == [(role, 'first\n\nsecond')]


@pytest.mark.parametrize('content', ['not a list', [], None, [4, {'type': 'unknown'}]])
def test_codex_rollout_empty_or_invalid_message_content_is_not_invented(source, content):
    rollout(source, [{'type': 'message', 'role': 'assistant', 'content': content}])
    assert source.catalog.get_session('codex', 'codex-1')['messages'] == []


def test_corrupt_history_database_falls_back_to_readable_rollout(source):
    path = rollout(source, [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'retained'}]}])
    (source.codex / 'thread_history_1.sqlite').write_bytes(b'not sqlite')
    assert source.catalog.get_session('codex', 'codex-1')['messages'][0]['text'] == 'retained'
    assert path.is_file()


@pytest.mark.parametrize('outside', [False, True])
def test_external_codex_rollout_locator_cannot_escape_selected_root(source, outside):
    path = rollout(source, [])
    if outside:
        path = source.root / 'outside.jsonl'
        write_rows(path, [{'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'outside fixture'}}])
    else:
        path = source.codex / 'sessions' / 'wrong.txt'
        path.write_text('outside fixture')
    with sqlite3.connect(source.codex / 'state_5.sqlite') as db:
        db.execute('UPDATE threads SET rollout_path=?', (str(path),))
    assert source.catalog.get_session('codex', 'codex-1')['messages'] == []


def test_symlinked_codex_rollout_does_not_bypass_root_boundary(source):
    path = rollout(source, [])
    outside = source.root / 'outside.jsonl'
    outside.write_text('{"type":"response_item","payload":{"type":"function_call_output","output":"outside"}}\n')
    path.unlink()
    path.symlink_to(outside)
    assert source.catalog.get_session('codex', 'codex-1')['messages'] == []


@pytest.mark.parametrize('broken', ['not sqlite', 'wrong schema'])
def test_corrupt_state_database_uses_rollout_metadata(source, broken):
    rollout(source, [])
    state = source.codex / 'state_5.sqlite'
    if broken == 'not sqlite': state.write_bytes(b'invalid database')
    else:
        with sqlite3.connect(state) as db: db.execute('DROP TABLE threads')
    listing = source.catalog.list_sessions(source='codex', refresh=True)
    assert listing['total'] == 1 and listing['sessions'][0]['session_id'] == 'codex-1'
    assert listing['sessions'][0]['cwd'] == '/fixture'


@pytest.mark.parametrize('header', [{}, {'type': 'other'}, {'type': 'session_meta', 'payload': []}, {'type': 'session_meta', 'payload': {}}])
def test_rollouts_without_valid_metadata_are_not_listed(source, header):
    (source.codex / 'state_5.sqlite').unlink()
    path = next((source.codex / 'sessions').rglob('*.jsonl'))
    write_rows(path, [header])
    assert source.catalog.list_sessions(source='codex', refresh=True)['sessions'] == []


def test_jsonl_reader_ignores_bad_lines_without_losing_following_messages(source):
    path = rollout(source, [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'retained'}]}])
    lines = path.read_text().splitlines()
    path.write_text(lines[0] + '\n\n{bad\n[]\n9\n' + lines[1] + '\n')
    assert source.catalog.get_session('codex', 'codex-1')['messages'][0]['text'] == 'retained'


@pytest.mark.parametrize('payload', ['{bad', '[]', '{"entries":{}}', '{"entries":[7,{}, {"isSidechain":true}]}'])
def test_claude_index_failure_preserves_jsonl_discovery(source, payload):
    index = next(source.claude.rglob('sessions-index.json'))
    index.write_text(payload)
    listing = source.catalog.list_sessions(source='claude', refresh=True)
    assert listing['total'] == 1 and listing['sessions'][0]['title'] == 'Claude question'
    assert listing['sessions'][0]['message_count'] == 2


@pytest.mark.parametrize('count', ['not-a-number', {}, {'bad': 2}, [2], float('nan'), float('inf')])
def test_bad_claude_index_count_does_not_break_other_sessions(source, count):
    index = next(source.claude.rglob('sessions-index.json'))
    payload = json.loads(index.read_text());payload['entries'][0]['messageCount'] = count
    index.write_text(json.dumps(payload))
    listing = source.catalog.list_sessions(refresh=True)
    assert listing['counts'] == {'claude': 1, 'codex': 1, 'opencode': 1}
    assert next(x for x in listing['sessions'] if x['source'] == 'claude')['message_count'] == 0


@pytest.mark.parametrize('content,expected', [(' user text ', 'user text'), ([{'type': 'text', 'text': 'one'}, {'type': 'image'}, None, {'type': 'text', 'text': 'two'}], 'one\n\ntwo'), (None, ''), (5, '')])
def test_claude_fallback_title_uses_only_actual_user_text(source, content, expected):
    next(source.claude.rglob('sessions-index.json')).unlink()
    path = next(source.claude.rglob('*.jsonl'))
    write_rows(path, [{'type': 'user', 'sessionId': 'claude-1', 'message': {'content': content}}])
    title = source.catalog.list_sessions(source='claude', refresh=True)['sessions'][0]['title']
    assert title == (expected or 'Claude claude-1')


def test_claude_sidechain_rows_do_not_enter_main_history(source):
    path = next(source.claude.rglob('*.jsonl'))
    with path.open('a') as f: f.write(json.dumps({'type': 'user', 'isSidechain': True, 'message': {'content': 'sidechain fixture'}}) + '\n')
    messages = source.catalog.get_session('claude', 'claude-1')['messages']
    assert all('sidechain fixture' not in x['text'] for x in messages)


def test_claude_reasoning_and_failed_tool_results_keep_separate_kinds(source):
    path = next(source.claude.rglob('*.jsonl'))
    with path.open('a') as f:
        f.write(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'thinking', 'thinking': 'fixture reasoning'}, {'type': 'tool_result', 'tool_use_id': 'call-one', 'content': 'failed fixture', 'is_error': True}, 4, {'type': 'unknown'}]}}) + '\n')
    messages = source.catalog.get_session('claude', 'claude-1')['messages']
    assert messages[-2]['kind'] == 'reasoning'
    assert messages[-1]['kind'] == 'tool_result' and messages[-1]['tool_status'] == 'error'
    assert messages[-1]['tool_use_id'] == 'call-one'


@pytest.mark.parametrize('part,role,kind,text', [
    ({'type': 'reasoning', 'text': 'fixture reasoning'}, 'assistant', 'reasoning', 'fixture reasoning'),
    ({'type': 'tool', 'tool': 'Read', 'callID': 'call-one', 'state': {'status': 'error', 'error': 'fixture failure', 'input': {'path': 'fixture'}}}, 'assistant', 'tool_result', None),
    ({'type': 'file', 'filename': 'fixture.png'}, 'user', 'attachment', 'fixture.png'),
    ({'type': 'file', 'url': 'https://example.invalid/file'}, 'system', 'attachment', 'https://example.invalid/file'),
    ({'type': 'unknown'}, 'assistant', None, None),
    ({'type': 'text', 'text': 'ignored'}, 'system', None, None),
])
def test_opencode_message_parts_preserve_native_semantics(source, part, role, kind, text):
    with sqlite3.connect(source.opencode) as db:
        db.execute('UPDATE message SET data=?', (json.dumps({'role': role}),))
        db.execute('UPDATE part SET data=?', (json.dumps(part),))
    messages = source.catalog.get_session('opencode', 'open-1')['messages']
    if kind is None: assert messages == [];return
    assert messages[0]['kind'] == kind
    if text: assert messages[0]['text'] == text
    if part['type'] == 'tool':
        assert messages[0]['tool_use_id'] == 'call-one' and messages[0]['tool_status'] == 'error'
        assert messages[0]['tool_output'] == 'fixture failure'


def test_external_history_reads_never_modify_provider_databases(source):
    paths = [source.opencode, source.codex / 'state_5.sqlite', source.codex / 'thread_history_1.sqlite']
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    for name, sid in [('claude', 'claude-1'), ('codex', 'codex-1'), ('opencode', 'open-1')]:
        source.catalog.get_session(name, sid)
        source.catalog.import_messages(name, sid)
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


@pytest.mark.parametrize('item,expected_kind', [
    ({'type': 'mcpToolCall', 'id': 'tool-one', 'server': 'fixture', 'tool': 'lookup', 'result': {'answer': 'fixture result'}}, 'tool_result'),
    ({'type': 'mcpToolCall', 'id': 'tool-one', 'server': 'fixture', 'tool': 'lookup', 'error': 'fixture error'}, 'tool_result'),
    ({'type': 'webSearch', 'id': 'tool-one', 'query': 'fixture query'}, 'web_search'),
    ({'type': 'commandExecution', 'id': 'tool-one', 'command': 'fixture command', 'aggregatedOutput': 'fixture output', 'exitCode': 2}, 'tool_result'),
])
def test_projected_codex_tools_keep_search_and_failure_metadata(source, item, expected_kind):
    with sqlite3.connect(source.codex / 'thread_history_1.sqlite') as db:
        db.execute('DELETE FROM thread_items')
        db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?)', ('codex-1', 0, 1700000000000, json.dumps(item), item['type']))
    messages = source.catalog.get_session('codex', 'codex-1')['messages']
    assert len(messages) == 1 and messages[0]['kind'] == expected_kind
    assert messages[0]['tool_use_id'] == 'tool-one'
    assert messages[0]['tool_status'] == ('error' if item.get('error') or item.get('exitCode') else 'done')
    if item['type'] == 'webSearch': assert messages[0]['tool_input'] == {'query': 'fixture query'}
    if item['type'] == 'commandExecution': assert messages[0]['tool_input'] == {'command': 'fixture command'}
