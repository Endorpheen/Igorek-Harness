from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.api_server_core import workflow_engine as flow


def state(**changes):
    values = dict(current_workflow=None, workflow_step=None, workflow_step_count=0,
                  collected_targets=[], collected_directories=[], remaining_directories=[],
                  candidate_deep_branches=[], remaining_deep_branches=[],
                  collected_summaries=[], candidate_related_files=[], tool_map={'Bash': {}, 'Read': {}},
                  explicit_user_absolute_path='', project_root='', resolved_active_path='',
                  active_filename='', active_file_exists=False, latest_user_text='',
                  explicit_runtime_constraints=SimpleNamespace(allow_subagents=False))
    return SimpleNamespace(**(values | changes))


def history(*exchanges):
    messages = []
    for index, (name, tool_input, output, error) in enumerate(exchanges):
        identity = f'call-{index}'
        messages.extend([
            SimpleNamespace(role='assistant', content=[{'type': 'tool_use', 'id': identity, 'name': name, 'input': tool_input}]),
            SimpleNamespace(role='user', content=[{'type': 'tool_result', 'tool_use_id': identity, 'content': output, 'is_error': error}]),
        ])
    return SimpleNamespace(messages=messages)


def listing(name, kind='d'):
    return f'{kind}rwxr-xr-x 2 user group 4096 Oct 23 00:00 {name}'


@pytest.mark.parametrize('name,step', [(None, None), ('unknown', 'unknown'), ('project_analysis', None), ('project_analysis', 'unknown')])
def test_unknown_or_missing_workflow_configuration_is_a_compatible_noop(name, step):
    assert flow.workflow_state_config(name, step) == {}
    assert flow.workflow_allows_tool(name, step, {'name': 'Read', 'input': {}})
    assert flow.workflow_completion_policy(state(current_workflow=name, workflow_step=step), lambda _: False) == (None, None)


@pytest.mark.parametrize('name,step,limit,action', [
    ('project_analysis', 'scan_root', 10, 'summarize'),
    ('file_investigation', 'read_primary', 6, 'summarize'),
    ('failure_recovery', 'retry_once', 1, 'stop'),
    ('local_implementation', 'continue', 20, 'stop'),
    ('tavily_research', 'search', 6, 'stop'),
    ('session_recall', 'search', 3, 'stop'),
])
def test_workflow_step_limits_prevent_unbounded_continuation(name, step, limit, action):
    current = state(current_workflow=name, workflow_step=step, workflow_step_count=limit - 1)
    assert flow.workflow_completion_policy(current, lambda _: False) == (None, None)
    current.workflow_step_count = limit
    assert flow.workflow_completion_policy(current, lambda _: False) == (action, 'max_steps')


@pytest.mark.parametrize('name,step,expected', [
    ('local_implementation', 'blocked', ('stop', 'blocked')),
    ('local_implementation', 'done', ('stop', 'done')),
    ('failure_recovery', 'stop', ('stop', 'recovery_exhausted')),
    ('session_recall', 'summarize', ('summarize', 'workflow_summary_ready')),
    ('session_recall', 'stop', ('stop', 'workflow_stop')),
    ('tavily_research', 'summarize', ('summarize', 'workflow_summary_ready')),
    ('tavily_research', 'stop', ('stop', 'workflow_stop')),
])
def test_terminal_states_do_not_ask_for_more_model_steps(name, step, expected):
    current = state(current_workflow=name, workflow_step=step)
    assert flow.workflow_completion_policy(current, lambda _: False) == expected
    assert not flow.workflow_allows_tool(name, step, {'name': 'Write', 'input': {'file_path': 'out.txt'}})


def test_project_summary_requires_real_evidence_and_all_remaining_targets():
    current = state(current_workflow='project_analysis', workflow_step='scan_subdirs', workflow_step_count=2,
                    collected_summaries=['src: files: main.py'])
    assert flow.project_analysis_has_enough_context(current)
    assert flow.workflow_completion_policy(current, lambda _: False) == ('summarize', 'sufficient_context')
    for changes in [dict(remaining_directories=['docs']),
                    dict(candidate_deep_branches=['src'], remaining_deep_branches=['src']),
                    dict(collected_summaries=[]), dict(workflow_step_count=1), dict(current_workflow='other')]:
        assert not flow.project_analysis_has_enough_context(SimpleNamespace(**(vars(current) | changes)))


def test_file_evidence_and_url_exhaustion_can_end_work_before_the_step_limit():
    assert flow.workflow_completion_policy(state(current_workflow='file_investigation', workflow_step='read_related'), lambda _: True) == ('summarize', 'sufficient_context')
    current = state(current_workflow='tavily_research', workflow_step='fetch_sources',
                    candidate_related_files=['https://example.com/a'], collected_targets=['https://example.com/a'],
                    collected_summaries=['fetched source'])
    assert flow.workflow_completion_policy(current, lambda _: False) == ('summarize', 'exhausted_urls')
    current.candidate_related_files.append('https://example.com/b')
    assert flow.workflow_completion_policy(current, lambda _: False) == (None, None)
    current.workflow_step_count = 6
    assert flow.workflow_completion_policy(current, lambda _: False) == ('summarize', 'max_steps')
    current.collected_summaries = []
    assert flow.workflow_completion_policy(current, lambda _: False) == ('stop', 'max_steps')


@pytest.mark.parametrize('name,step,flag', [
    ('file_investigation', 'read_primary', 'force_file_investigation_followup'),
    ('failure_recovery', 'retry_once', 'force_failure_recovery_followup'),
    ('local_implementation', 'continue', 'force_local_implementation_followup'),
    ('tavily_research', 'search', 'force_tavily_research_followup'),
    ('session_recall', 'search', 'force_session_recall_followup'),
])
def test_followup_requirements_force_only_an_unfinished_workflow(name, step, flag):
    current = state(current_workflow=name, workflow_step=step)
    result = flow.workflow_followup_requirements(current, file_investigation_has_enough_context_fn=lambda _: False)
    assert result[flag] is True
    assert result['completion_action'] is None
    current.workflow_step_count = result['workflow_max_steps']
    result = flow.workflow_followup_requirements(current, file_investigation_has_enough_context_fn=lambda _: False)
    assert result[flag] is False
    assert result['completion_action'] in {'stop', 'summarize'}


@pytest.mark.parametrize('step,changes', [
    ('scan_root', {'collected_directories': ['src']}),
    ('scan_subdirs', {'remaining_directories': ['src']}),
    ('scan_deep', {'remaining_deep_branches': ['src']}),
])
def test_project_scan_followup_depends_on_observed_pending_directories(step, changes):
    current = state(current_workflow='project_analysis', workflow_step=step, **changes)
    result = flow.workflow_followup_requirements(current, file_investigation_has_enough_context_fn=lambda _: False)
    assert result['force_project_scan_followup'] is True


@pytest.mark.parametrize('workflow,tool', [
    ('project_analysis', 'Bash'), ('file_investigation', 'Read'),
    ('failure_recovery', 'Edit'), ('local_implementation', 'Write'),
    ('tavily_research', 'Bash'), ('session_recall', 'SessionSearch'),
])
def test_a_final_answer_after_the_relevant_tool_finishes_the_workflow(workflow, tool):
    req = history((tool, {'file_path': 'a.py'}, 'ok', False))
    assert not flow.workflow_has_completed_assistant_text(req, workflow)
    req.messages.append(SimpleNamespace(role='assistant', content='   '))
    assert not flow.workflow_has_completed_assistant_text(req, workflow)
    req.messages.append(SimpleNamespace(role='assistant', content=[{'type': 'text', 'text': 'Task completed.'}]))
    assert flow.workflow_has_completed_assistant_text(req, workflow)
    req.messages.append(SimpleNamespace(role='assistant', content=[{'type': 'tool_use', 'name': tool, 'id': 'next', 'input': {}}]))
    assert not flow.workflow_has_completed_assistant_text(req, workflow)
    req.messages.append(SimpleNamespace(role='assistant', content='Done again.'))
    assert flow.workflow_has_completed_assistant_text(req, workflow)
    assert not flow.workflow_has_completed_assistant_text(history(), workflow)


@pytest.mark.parametrize('tool,kind', [
    ({'name': 'Bash', 'input': {'command': 'ls -la'}}, 'directory_scan_bash'),
    ({'name': 'Bash', 'input': {'command': 'python script.py'}}, 'bash'),
    ({'name': 'Read'}, 'read_tool'), ({'name': 'Edit'}, 'write_tool'),
    ({'name': 'Write'}, 'write_tool'), ({'name': 'MultiEdit'}, 'write_tool'),
    ({'name': 'SessionSearch'}, 'session_search_tool'), ({'name': 'skill_view'}, 'skills_tool'),
    ({'name': 'skills_list'}, 'skills_tool'), ({'name': 'Custom'}, 'Custom'),
    ({'name': ''}, None), (None, None), ([], None),
])
def test_workflow_tools_are_classified_without_execution(tool, kind):
    assert flow.classify_workflow_tool(tool) == kind


def test_local_implementation_needs_explicit_subagent_authorization():
    call = {'name': 'Agent', 'input': {}}
    current = state(current_workflow='local_implementation', workflow_step='continue')
    assert not flow.workflow_allows_tool('local_implementation', 'continue', call, current)
    current.explicit_runtime_constraints.allow_subagents = True
    assert flow.workflow_allows_tool('local_implementation', 'continue', call, current)
    assert flow.workflow_allows_tool('local_implementation', 'continue', {'name': 'Read', 'input': {}}, current)


def test_project_scans_stay_within_the_selected_root_and_remaining_targets():
    current = state(project_root='/tmp/fixture-project')
    scan = lambda command: {'name': 'Bash', 'input': {'command': command}}
    assert flow.workflow_allows_tool('project_analysis', 'scan_root', scan('ls -la'), current)
    assert flow.workflow_allows_tool('project_analysis', 'scan_root', scan('ls -la /tmp/fixture-project/'), current)
    assert not flow.workflow_allows_tool('project_analysis', 'scan_root', scan('ls -la /tmp/other/'), current)
    current.collected_directories = ['src', 'docs']
    current.remaining_directories = ['docs']
    current.remaining_deep_branches = ['src/core']
    assert flow.workflow_allows_tool('project_analysis', 'scan_root', scan('ls -la src/ docs/'), current)
    assert flow.workflow_allows_tool('project_analysis', 'scan_subdirs', scan('ls -la docs/'), current)
    assert not flow.workflow_allows_tool('project_analysis', 'scan_subdirs', scan('ls -la src/'), current)
    assert flow.workflow_allows_tool('project_analysis', 'scan_deep', scan('ls -la src/core/'), current)
    assert not flow.workflow_allows_tool('project_analysis', 'scan_deep', scan('ls -la docs/'), current)
    assert not flow.is_allowed_subdir_scan_command('cat docs/readme', ['docs'])
    assert not flow.is_allowed_subdir_scan_command('ls -la', ['docs'])


def test_file_reads_must_match_the_active_or_discovered_related_file():
    current = state(resolved_active_path='src/main.py', collected_targets=['src/already.py'], candidate_related_files=['src/util.py'])
    call = lambda path: {'name': 'Read', 'input': {'file_path': path}}
    for path in ['src/main.py', 'src/already.py', 'src/util.py']:
        assert flow.workflow_allows_tool('file_investigation', 'read_related', call(path), current)
    for path in ['', 'other.txt']:
        assert not flow.workflow_allows_tool('file_investigation', 'read_related', call(path), current)


def test_forced_project_scans_use_only_the_requested_stage_and_remove_duplicates():
    current = state(current_workflow='project_analysis', workflow_step='scan_root', explicit_user_absolute_path='/tmp/requested/', project_root='/tmp/other')
    assert flow.project_analysis_forced_scan_tool_use(current)['input'] == {'command': 'ls -la /tmp/requested/'}
    current.explicit_user_absolute_path = current.project_root = ''
    assert flow.project_analysis_forced_scan_tool_use(current)['input'] == {'command': 'ls -la'}
    current.workflow_step = 'scan_subdirs'
    current.remaining_directories = ['src', 'docs/', 'src/', '', '.', '..']
    assert flow.project_analysis_forced_scan_tool_use(current)['input'] == {'command': 'ls -la src/ docs/'}
    current.workflow_step = 'scan_deep'
    current.remaining_deep_branches = ['src/core']
    assert flow.project_analysis_forced_scan_tool_use(current)['input'] == {'command': 'ls -la src/core/'}
    current.remaining_deep_branches = []
    assert flow.project_analysis_forced_scan_tool_use(current) is None
    current.tool_map = {}
    assert flow.project_analysis_forced_scan_tool_use(current) is None
    current.current_workflow = 'other'
    assert flow.project_analysis_forced_scan_tool_use(current) is None


def test_forced_read_requires_an_existing_unread_primary_target():
    current = state(current_workflow='file_investigation', workflow_step='read_primary', resolved_active_path='main.py', active_file_exists=True)
    initial = Mock(return_value={'file_path': 'main.py', 'offset': 1, 'limit': 100})
    call = flow.file_investigation_forced_read_tool_use(current, initial)
    assert call['name'] == 'Read'
    assert call['input'] == {'file_path': 'main.py', 'offset': 1, 'limit': 100}
    initial.assert_called_once_with('main.py')
    for changes in [dict(current_workflow='other'), dict(workflow_step='read_related'), dict(tool_map={}),
                    dict(active_file_exists=False), dict(resolved_active_path=''), dict(collected_targets=['main.py'])]:
        assert flow.file_investigation_forced_read_tool_use(SimpleNamespace(**(vars(current) | changes)), initial) is None


def test_forced_web_fetch_selects_the_next_unread_url_without_running_a_command():
    current = state(current_workflow='tavily_research', workflow_step='fetch_sources',
                    collected_targets=['https://example.com/a'],
                    candidate_related_files=['', 'https://example.com/a', 'https://example.com/b?q=x&y=2'])
    call = flow.tavily_research_forced_tool_use(current)
    assert call['name'] == 'Bash'
    assert call['input']['command'] == "curl -sL -- 'https://example.com/b?q=x&y=2'"
    assert flow.workflow_allows_tool('tavily_research', 'fetch_sources', call, current)
    assert not flow.workflow_allows_tool('tavily_research', 'search', call, current)
    current.collected_targets += current.candidate_related_files
    assert flow.tavily_research_forced_tool_use(current) is None
    current.current_workflow = 'other'
    assert flow.tavily_research_forced_tool_use(current) is None


def test_directory_evidence_ignores_metadata_dots_duplicates_and_invalid_names():
    text = '\n'.join(['', 'src/:', 'total 4', listing('.'), listing('..'), listing('src'), listing('src/'),
                       listing('.git'), listing('docs'), listing('readme.md', '-'), listing('bad$'), 'd malformed'])
    assert flow.extract_ls_directories(text) == ['src', '.git', 'docs']
    assert flow.extract_ls_entry_name(listing('link -> destination', 'l')) == 'link'
    assert flow.extract_ls_entry_name(listing('file name.txt', '-')) == 'file name.txt'
    for line in ['', 'total 4', 'src/:', 'x unsupported', 'd short']:
        assert flow.extract_ls_entry_name(line) == ''
    assert flow.directory_scan_targets_from_command('ls -la ./ src/ docs/') == ['src', 'docs']
    assert flow.directory_scan_targets_from_command('pwd') == []
    assert not flow.is_directory_scan_bash_command('   ')


def test_directory_summaries_keep_multiple_targets_and_bound_long_previews():
    text = 'src/:\n' + '\n'.join(listing('dir'+str(i)) for i in range(8))
    text += '\ndocs/:\n' + '\n'.join(listing('file'+str(i), '-') for i in range(8))
    result = flow.summarize_directory_scan_result('ls -la src/ docs/', text)
    assert 'src: dirs: dir0, dir1' in result
    assert 'docs: files: file0, file1' in result
    assert '...' in result
    assert 'dir7' not in result and 'file7' not in result
    assert 'empty output' in flow.summarize_directory_scan_result('ls -la', '')
    assert 'no visible entries' in flow.summarize_directory_scan_result('ls -la', 'total 0')


def test_project_coverage_counts_each_source_branch_once_and_ignores_cache_dirs():
    dirs = ['src', 'docs', '.git', '__pycache__']
    targets = ['src/main.py', 'src/core/', 'docs/', 'missing']
    assert flow.project_analysis_trackable_dirs(dirs) == ['src', 'docs']
    assert flow.project_analysis_covered_dirs(targets, dirs) == ['src', 'docs']
    assert flow.project_analysis_deep_branch_candidates(['src/', 'src', 'docs', 'src/core', 'backend', '']) == ['src', 'backend']
    assert flow.project_analysis_covered_deep_branches(targets, ['src', 'src/', 'backend']) == ['src']
    assert flow.project_analysis_step(0, ['src'], []) is None
    assert flow.project_analysis_step(1, ['src'], []) == 'scan_root'
    assert flow.project_analysis_step(2, ['src'], []) == 'scan_subdirs'
    assert flow.project_analysis_step(3, [], ['src']) == 'scan_deep'
    assert flow.project_analysis_step(4, [], []) == 'summarize'


def test_project_state_is_derived_from_actual_tool_history_and_preserves_errors():
    root = '\n'.join([listing('src'), listing('docs'), listing('.git')])
    req = history(('Bash', {'command': 'ls -la'}, root, False),
                  ('Read', {'file_path': 'unrelated.py'}, 'ignored', False),
                  ('Bash', {'command': 'ls -la src/'}, listing('main.py', '-'), False),
                  ('Bash', {'command': 'ls -la docs/'}, 'total 0', False))
    result = flow.project_analysis_state(req)
    assert result[:3] == ('project_analysis', 'summarize', 3)
    assert result[3] == ['src', 'docs']
    assert result[5] == ['src', 'docs']
    assert result[6] == []
    assert len(result[11]) == 3
    req.messages.append(SimpleNamespace(role='assistant', content='Project explained.'))
    assert flow.project_analysis_state(req)[0] is None
    failed = history(('Bash', {'command': 'ls -la'}, '<tool_use_error>denied</tool_use_error>', True))
    result = flow.project_analysis_state(failed)
    assert result[0] is None and 'denied' in result[-1]


@pytest.mark.parametrize('name,message,step', [
    ('Read', '<tool_use_error>File has not been read yet</tool_use_error>', 'continue'),
    ('Edit', '<tool_use_error>String to replace not found</tool_use_error>', 'continue'),
    ('Write', '<tool_use_error>required parameter `content` is missing</tool_use_error>', 'continue'),
    ('Read', '<tool_use_error>InputValidationError</tool_use_error>', 'continue'),
    ('Read', '<tool_use_error>missing required field: path</tool_use_error>', 'continue'),
    ('Read', '<tool_use_error>unexpected fields: target</tool_use_error>', 'continue'),
    ('Bash', 'Exit code 127\ncommand not found', 'continue'),
    ('Read', '<tool_use_error>permission denied</tool_use_error>', 'blocked'),
    ('Bash', 'Exit code 1\npermission denied', 'blocked'),
])
def test_local_execution_distinguishes_repairable_inputs_from_real_failures(name, message, step):
    req = history((name, {'file_path': 'src/main.py', 'command': 'missing-command'}, message, True))
    result = flow.local_implementation_state(req)
    assert result[:3] == ('local_implementation', step, 1)
    assert message in result[-1]
    assert result[3] == ['src/main.py']


def test_local_execution_ignores_scanning_and_finishes_after_the_actual_final_answer():
    req = history(('Bash', {'command': 'ls -la'}, 'total 0', False))
    assert flow.local_implementation_state(req)[0] is None
    req = history(('Write', {'file_path': 'a.py'}, 'written', False), ('Bash', {'command': 'python a.py'}, 'ok', False))
    assert flow.local_implementation_state(req)[:3] == ('local_implementation', 'continue', 2)
    req.messages.append(SimpleNamespace(role='assistant', content='Done.'))
    assert flow.local_implementation_state(req)[0] is None


def test_failure_recovery_allows_one_attempt_then_stops_and_clears_on_success():
    first = ('Read', {'file_path': 'a.py'}, '<tool_use_error>denied</tool_use_error>', True)
    assert flow.failure_recovery_state(history(first))[:3] == ('failure_recovery', 'retry_once', 1)
    assert flow.failure_recovery_state(history(first, first))[:3] == ('failure_recovery', 'stop', 2)
    assert flow.failure_recovery_state(history(first, ('Read', {'file_path': 'a.py'}, 'ok', False)))[0] is None
    assert flow.failure_recovery_state(history())[0] is None


def test_session_search_collects_only_successful_search_evidence_and_then_summarizes():
    import json
    payload = {'success': True, 'results': [{'session': 'one', 'excerpt': 'useful result', 'last_active': '2026-10-23', 'entry_kind': 'message'},
                                           {'preview': 'preview without session'}, {'session': 'two'}, None]}
    req = history(('Read', {'file_path': 'ignored.py'}, 'ok', False),
                  ('SessionSearch', {'query': 'example'}, json.dumps(payload), False))
    result = flow.session_recall_state(req, lambda text: text)
    assert result[:3] == ('session_recall', 'summarize', 1)
    assert result[3] == ['one', 'two']
    assert len(result[11]) == 3
    assert 'useful result' in result[11][0]
    req.messages.append(SimpleNamespace(role='assistant', content='Found prior discussion.'))
    assert flow.session_recall_state(req, lambda text: text)[0] is None
    failed = history(('SessionSearch', {'query': 'example'}, '<tool_use_error>denied</tool_use_error>', True))
    result = flow.session_recall_state(failed, lambda text: text)
    assert result[:3] == ('session_recall', 'search', 1) and 'denied' in result[-1]
    assert flow.session_recall_state(history(), lambda text: text)[0] is None


def test_read_summaries_extract_structure_without_running_the_source():
    numbered = ' 1 import os\n 2 import os\n 3 from .core import run\n 4 class Service:\n 5 def work():\n 6 @app.route("/health")\n 7 async def serve():\n'
    assert flow.extract_read_imports(numbered) == ['import os', 'from .core import run']
    assert 'class Service' in flow.extract_read_sections(numbered)
    assert 'def work()' in flow.extract_read_sections(numbered)
    assert flow.extract_decorated_entrypoints(numbered) == ['serve']
    assert flow.extract_main_entrypoints('def main():\n pass\nasync def run():\n pass\nif __name__ == "__main__":\n main()') == ['main', 'run', '__main__']
    assert flow.extract_top_level_constants('MAX_STEPS = 10\n    INNER = 1\nMAX_STEPS = 20\n') == ['MAX_STEPS']
    assert flow.summarize_read_result('a.py', numbered).startswith('a.py: 7 lines read | first: import os')
    assert flow.summarize_read_result('a.py', '') == 'a.py: 0 lines read'
    for sampler in [flow.sample_head_tail, flow.sample_section_names]:
        assert sampler(['a', 'b'], limit=2) == ['a', 'b']
        assert sampler(list('abcdef'), limit=4) == ['a', 'b', '...', 'e', 'f']
