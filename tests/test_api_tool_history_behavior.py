from types import SimpleNamespace

import pytest

from factory.api_server_core import tool_history as history


def flatten(blocks, *, role):
    return '\n'.join(str(block.get('content', '')) for block in blocks)


def bash_target(command):
    return ''


OPTIONS = {
    'flatten_block_content': flatten,
    'looks_like_tool_error_payload': lambda text: text.startswith('[tool-error]'),
    'extract_bash_file_target': bash_target,
}


def request(*messages):
    return SimpleNamespace(messages=[SimpleNamespace(role=role, content=content) for role, content in messages])


def use(identity, name, **values):
    return {'type': 'tool_use', 'id': identity, 'name': name, 'input': values}


def reply(identity, content='Done', **values):
    return {'type': 'tool_result', 'tool_use_id': identity, 'content': content, **values}


@pytest.mark.parametrize('key', ['file_path', 'file_name', 'filename', 'path', 'notebook_path'])
def test_file_target_uses_supported_tool_argument_names(key):
    assert history.tool_target_from_input('Read', {key: 'notes.txt'}, extract_bash_file_target=bash_target) == 'notes.txt'


def test_bash_target_uses_injected_extractor_without_running_command():
    commands = []

    def extract(command):
        commands.append(command)
        return 'report.txt'

    assert history.tool_target_from_input('Bash', {'command': 'cat report.txt'}, extract_bash_file_target=extract) == 'report.txt'
    assert commands == ['cat report.txt']
    assert history.tool_target_from_input('Read', {'file_path': None}, extract_bash_file_target=extract) == ''
    assert history.tool_target_from_input('Bash', {'command': None}, extract_bash_file_target=extract) == ''


def test_action_signature_ignores_dictionary_key_order_but_preserves_edit_values():
    options = {'extract_bash_file_target': bash_target}
    first = history.tool_action_signature('Edit', {'path': 'a.txt', 'old_string': 'one', 'new_string': 'two'}, **options)
    same = history.tool_action_signature('Edit', {'new_string': 'two', 'old_string': 'one', 'path': 'a.txt'}, **options)
    changed = history.tool_action_signature('Edit', {'path': 'a.txt', 'old_string': 'one', 'new_string': 'three'}, **options)
    assert first == same
    assert changed != first
    assert history.tool_action_signature('Bash', {'command': ' cat a.txt '}, **options) == ('Bash', '', 'cat a.txt')


def test_parallel_tool_results_are_paired_by_identity_not_return_order():
    req = request(('user', 'Question'),
                  ('assistant', [{'type': 'text', 'text': 'Working'}, use('read', 'Read', path='a.txt'),
                                 use('edit', 'Edit', path='b.txt', old_string='old', new_string='new')]),
                  ('user', [reply('edit', 'Edited'), {'type': 'text', 'text': 'Continue'}, reply('read', 'File text')]))
    exchanges = history.extract_tool_exchanges(req, **OPTIONS)
    assert [(e.tool_name, e.target, e.result_text, e.index) for e in exchanges] == [
        ('Edit', 'b.txt', 'Edited', 0), ('Read', 'a.txt', 'File text', 1)]
    assert exchanges[0].tool_input_obj['new_string'] == 'new'
    assert not any(e.is_error for e in exchanges)


@pytest.mark.parametrize('explicit_error,text', [(True, 'Failure'), (False, '[tool-error] missing')])
def test_failed_tool_result_is_not_considered_a_success(explicit_error, text):
    req = request(('assistant', [use('read', 'Read', path='a.txt')]),
                  ('user', [reply('read', text, is_error=explicit_error)]))
    assert history.extract_tool_exchanges(req, **OPTIONS)[0].is_error is True
    assert history.is_duplicate_of_latest_successful_tool_turn(req, 'Read', {'path': 'a.txt'}, **OPTIONS) is False
    assert history.extract_last_successful_read_file(req, **OPTIONS) is None


def test_duplicate_detection_checks_latest_result_and_actual_input():
    req = request(('assistant', [use('first', 'Read', path='a.txt')]), ('user', [reply('first')]),
                  ('assistant', [use('last', 'Read', path='b.txt')]), ('user', [reply('last')]))
    assert history.is_duplicate_of_latest_successful_tool_turn(req, 'Read', {'path': 'b.txt'}, **OPTIONS) is True
    assert history.is_duplicate_of_latest_successful_tool_turn(req, 'Read', {'path': 'a.txt'}, **OPTIONS) is False
    assert history.is_duplicate_of_latest_successful_tool_turn(request(), 'Read', {'path': 'a.txt'}, **OPTIONS) is False


def test_last_successful_read_ignores_later_edit_and_failed_read():
    req = request(('assistant', [use('read', 'Read', path='good.txt')]), ('user', [reply('read')]),
                  ('assistant', [use('edit', 'Edit', path='edited.txt')]), ('user', [reply('edit')]),
                  ('assistant', [use('failed', 'Read', path='missing.txt')]), ('user', [reply('failed', is_error=True)]))
    assert history.extract_last_successful_read_file(req, **OPTIONS) == 'good.txt'


def test_persisted_output_path_takes_precedence_over_command_target(tmp_path):
    output = tmp_path / 'saved.txt'
    req = request(('assistant', [use('cmd', 'Bash', command='produce-large-output')]),
                  ('user', [reply('cmd', f'Truncated output\nFull output saved to: {output}\nRead it if needed.')]))
    assert history.extract_last_tool_file(req, **OPTIONS) == str(output)


def test_last_tool_file_recovers_target_from_pending_call():
    req = request(('assistant', [use('read', 'Read', path='pending.txt')]))
    assert history.extract_last_tool_file(req, **OPTIONS) == 'pending.txt'
    assert history.extract_last_tool_file(request(), **OPTIONS) is None


def test_shell_directory_follows_only_successful_bash_exchanges(tmp_path):
    req = request(('assistant', [use('first', 'Bash', command='cd project && cd src; pwd')]),
                  ('user', [reply('first')]),
                  ('assistant', [use('failed', 'Bash', command='cd missing')]),
                  ('user', [reply('failed', is_error=True)]),
                  ('assistant', [use('read', 'Read', path='elsewhere.txt')]),
                  ('user', [reply('read')]))
    assert history.extract_last_shell_cwd(req, initial_cwd=str(tmp_path), **OPTIONS) == str(tmp_path / 'project' / 'src')
    assert history.extract_last_shell_cwd(request(), initial_cwd=str(tmp_path), **OPTIONS) is None
    assert history.extract_last_shell_cwd(req, **OPTIONS) is None


def test_shell_directory_can_change_to_an_absolute_path(tmp_path):
    destination = tmp_path / 'destination'
    req = request(('assistant', [use('cmd', 'Bash', command=f'cd {destination}')]), ('user', [reply('cmd')]))
    assert history.extract_last_shell_cwd(req, initial_cwd=str(tmp_path / 'start'), **OPTIONS) == str(destination)


def exchange(name, path, text='', *, error=False, index=0, **values):
    return history.ToolExchange(name, path, values, text, error, index)


def test_latest_read_text_skips_errors_and_strips_numbering_and_system_reminders():
    exchanges = [exchange('Read', '/a/notes.txt', '1→first\n 2→second\n<system-reminder>private\nignored'),
                 exchange('Read', '/a/other.txt', 'Wrong file'),
                 exchange('Read', '/a/notes.txt', 'Failed', error=True),
                 exchange('Edit', '/a/notes.txt', 'Edited')]
    assert history.extract_latest_successful_read_text(exchanges, 'notes.txt') == 'first\nsecond'
    assert history.extract_latest_successful_read_text(exchanges, 'absent.txt') == ''


def test_latest_success_index_uses_matching_file_and_allowed_tools():
    exchanges = [exchange('Read', '/a/notes.txt', index=2), exchange('Edit', '/a/notes.txt', index=5),
                 exchange('Read', '/a/notes.txt', error=True, index=9), exchange('Read', '/a/other.txt', index=10)]
    assert history.latest_success_index(exchanges, 'notes.txt', {'Read'}) == 2
    assert history.latest_success_index(exchanges, 'notes.txt', {'Read', 'Edit'}) == 5
    assert history.latest_success_index(exchanges, 'missing.txt', {'Read'}) == -1


def test_completed_edit_pairs_include_only_successful_edits_of_matching_file():
    exchanges = [exchange('Edit', 'a.txt', old_string=' old ', new_string=' new '),
                 exchange('Update', 'a.txt', old_string='new', new_string='final'),
                 exchange('MultiEdit', 'a.txt', old_string='old', new_string='new'),
                 exchange('Edit', 'other.txt', old_string='one', new_string='two'),
                 exchange('Edit', 'a.txt', error=True, old_string='bad', new_string='badder'),
                 exchange('Read', 'a.txt', old_string='read', new_string='not edit'),
                 exchange('Edit', 'a.txt', old_string='', new_string='empty')]
    assert history.completed_edit_pairs(exchanges, 'a.txt') == {('old', 'new'), ('new', 'final')}
