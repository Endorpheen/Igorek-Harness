"""Fast summary input preserves full evidence in an addressable archive."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.core.prompt_assembly import SESSION_SUMMARY_HEADINGS
from factory.core.session_compaction import archive_summary_history, compact_summary_messages, summarize_history

SUMMARY = '\n'.join(h + '\n- confirmed' for h in SESSION_SUMMARY_HEADINGS)


def imported_tool(index):
    output = 'HEAD ' + 'x' * 30000 + f' MIDDLE-{index} ' + 'x' * 30000 + ' TAIL'
    record = {'role': 'tool', 'kind': 'tool_result', 'tool_use_id': str(index), 'tool_name': 'Read',
              'tool_input': {'file_path': '/workspace/source.py', 'offset': 1, 'limit': 200},
              'tool_output': output, 'tool_status': 'completed', 'text': output}
    return {'role': 'assistant', 'content': '[Historical tool evidence]\n' + json.dumps(record)}


def test_large_import_uses_few_summary_requests_and_keeps_complete_archive(tmp_path):
    messages = [{'role': 'user', 'content': 'Keep the unfinished bug and do not deploy.'}] + [imported_tool(i) for i in range(420)]
    archive = tmp_path / 'history.jsonl'
    archive_summary_history(messages, archive)
    restored = [json.loads(line)['message'] for line in archive.read_text().splitlines()]
    assert restored == messages
    projection = compact_summary_messages(messages)
    assert projection[0]['content'] == messages[0]['content']
    assert all(p['status'] == 'completed' and p['input']['file_path'] == '/workspace/source.py' for p in projection[1:])
    assert projection[1]['output'].startswith('HEAD')
    assert 'TAIL' not in projection[1]['output']
    assert '[truncated;' in projection[1]['output']
    prompts = []
    result = summarize_history(messages, request=lambda p: prompts.append(p) or SUMMARY, input_budget_tokens=128000, archive_path=str(archive), continuation_goal='Fix the unfinished bug')
    assert len(prompts) <= 5
    assert str(archive) in result
    assert 'Fix the unfinished bug' in prompts[0]
    assert 'MIDDLE-419' in restored[-1]['content']
    assert max(map(len, prompts)) < 260000


def test_native_tool_ids_error_flag_and_input_path_survive_projection():
    messages = [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'a', 'name': 'Write', 'input': {'file_path': '/workspace/report.md', 'content': 'x' * 40000}}]},
                {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'a', 'is_error': True, 'content': 'HEAD' + 'x' * 100000 + 'TAIL'}]}]
    projection = compact_summary_messages(messages)
    assert projection[0]['content'][0]['id'] == 'a'
    assert projection[0]['content'][0]['input']['file_path'] == '/workspace/report.md'
    assert projection[1]['content'][0]['is_error'] is True
    assert projection[1]['content'][0]['tool_use_id'] == 'a'
    assert projection[0]['content'][0]['input'] == messages[0]['content'][0]['input']
    assert projection[1]['content'][0]['content'] == messages[1]['content'][0]['content']


def test_archive_failure_leaves_existing_archive_untouched(tmp_path):
    archive = tmp_path / 'history.jsonl';archive.write_text('existing')
    with patch('factory.core.session_compaction.os.replace', side_effect=OSError('disk full')):
        with pytest.raises(OSError):
            archive_summary_history([{'role': 'user', 'content': 'important'}], archive)
    assert archive.read_text() == 'existing'
    assert not list(tmp_path.glob('.history-*'))


def test_opencode_serialization_preserves_prose_reasoning_calls_and_errors():
    messages = [
        {'role': 'assistant', 'content': 'prose ' * 2000},
        {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'reason ' * 2000}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'ok', 'content': 'x' * 5000}]},
        imported_tool(1),
    ]
    error = json.loads(messages[-1]['content'].split('\n', 1)[1])
    error['tool_status'] = 'error'
    messages.append({'role': 'assistant', 'content': '[Historical tool evidence]\n' + json.dumps(error)})
    projection = compact_summary_messages(messages)
    assert projection[0]['content'] == messages[0]['content']
    assert projection[1]['content'] == messages[1]['content']
    assert projection[2]['content'][0]['content'].startswith('x' * 2000 + '\n[truncated;')
    assert projection[3]['input'] == error['tool_input']
    assert projection[4]['output'] == error['tool_output']
