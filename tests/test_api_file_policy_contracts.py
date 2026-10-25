"""Exercise API file-policy decisions with isolated files and configuration."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory.api_server_core import file_policy as fp
from factory.api_server_core import policy_engine as pe


@pytest.fixture
def policy(tmp_path, monkeypatch):
    path = tmp_path / 'inference-policy.yaml'
    monkeypatch.setattr(pe, 'POLICY_CONFIG_PATH', path)
    monkeypatch.setattr(pe, '_POLICY_CACHE', None)
    monkeypatch.setattr(pe, '_POLICY_MTIME', None)
    defaults = {
        'file_tools': {'managed_tools': ['Read', 'Edit', 'Update', 'MultiEdit', 'Write'],
                       'enforce_project_root': True, 'allow_absolute_path_override': True,
                       'allow_write_new_files': True, 'additional_allowed_roots': []},
        'bash': {'allow_empty_command': False, 'allow_compound_commands': False,
                 'allow_file_mutation': False, 'allowed_commands': [],
                 'mutation_commands': ['rm', 'mv', 'cp', 'touch'],
                 'nested_shell_commands': ['bash', 'sh'], 'disallow_inline_edit_flags': True},
    }
    def configure(section=None, **values):
        if section:
            defaults[section].update(values)
        path.write_text(yaml.safe_dump({'defaults': defaults}), encoding='utf-8')
        pe._POLICY_CACHE = None
        pe._POLICY_MTIME = None
        return defaults
    configure()
    return configure


def state(root, **values):
    defaults = dict(project_root=str(root), last_successful_read_file=None,
                    last_tool_file=None, current_shell_cwd=None, temporary_allowed_root=None,
                    active_filename=None, explicit_user_file=None, tool_map={},
                    policy_denial=None, missing_file_target=False, current_workflow=None)
    defaults.update(values)
    return SimpleNamespace(**defaults)


def target_from_input(_name, fields):
    return next((fields[k] for k in ('file_path', 'path', 'notebook_path') if fields.get(k)), '')


def decide(context, name, fields):
    return fp.validate_existing_file_policy(context, name, fields, tool_target_from_input=target_from_input)


def test_primary_session_directory_takes_precedence_over_instruction_headers(tmp_path):
    root = tmp_path / 'project'
    req = SimpleNamespace(system=f'Primary working directory: {root}\nOther context',
                          messages=[SimpleNamespace(content='Contents of /other/AGENTS.md')])
    assert fp.extract_project_root(req) == str(root)


@pytest.mark.parametrize('as_blocks', [False, True])
def test_primary_session_directory_preserves_spaces(tmp_path, as_blocks):
    root = tmp_path / 'my project'
    text = f'Primary working directory: {root}\nAdditional instructions'
    req = SimpleNamespace(system=[{'type': 'text', 'text': text}, {'type': 'image'}] if as_blocks else text, messages=[])
    assert fp.extract_project_root(req) == str(root)


@pytest.mark.parametrize('header', ['AGENTS.md', 'CLAUDE.md', '.claude/CLAUDE.md', 'claude/CLAUDE.md'])
def test_instruction_header_fallback_finds_the_external_project(tmp_path, header):
    req = SimpleNamespace(system=None, messages=[SimpleNamespace(content=[{'type': 'image'}, {'type': 'text', 'text': f'Contents of {tmp_path / header}\nInstructions'}])])
    assert fp.extract_project_root(req) == str(tmp_path)


def test_server_instruction_headers_do_not_become_the_active_project(tmp_path):
    req = SimpleNamespace(system=None, messages=[SimpleNamespace(content=f'Contents of {fp.REPO_ROOT / "AGENTS.md"}\nServer rules'), SimpleNamespace(content=f'Contents of {tmp_path / "AGENTS.md"}\nProject rules')])
    assert fp.extract_project_root(req) == str(tmp_path)
    assert fp.extract_project_root(SimpleNamespace(system=42, messages=[])) is None


@pytest.mark.parametrize('base_field', ['project_root', 'current_shell_cwd'])
def test_existing_directory_with_a_suffix_is_not_treated_as_a_file(tmp_path, base_field):
    folder = tmp_path / 'release.v2'
    folder.mkdir()
    fields = dict(last_successful_read_file=None, last_tool_file=None, project_root=None, current_shell_cwd=None)
    fields[base_field] = str(folder)
    assert fp.resolution_base_dir(**fields) == folder


def test_base_resolution_keeps_priority_and_handles_extensionless_files(tmp_path):
    first = tmp_path / 'README'
    first.write_text('fixture')
    assert fp.resolution_base_dir(str(first), '/other/task.py', '/project', '/shell') == tmp_path
    assert fp.resolution_base_dir('relative.txt', None, '/project', '/shell') == Path('/shell')
    assert fp.resolution_base_dir(None, '/other/task.py', '/project', '/shell') == Path('/other')
    assert fp.resolution_base_dir(None, None, None) is None


def test_relative_resolution_requires_a_base_and_normalizes_segments(tmp_path):
    with pytest.raises(ValueError, match='known base_dir'):
        fp.resolve_local_path('notes.md')
    assert fp.resolve_local_path('sub/../notes.md', base_dir=tmp_path) == tmp_path / 'notes.md'
    assert fp.resolve_local_path_if_valid('notes.md') is None
    assert fp.resolve_local_path_if_valid('bad;command', base_dir=tmp_path) is None


@pytest.mark.parametrize('name,key', [('Read','file_path'),('Edit','path'),('Update','file_path'),('MultiEdit','file_path'),('Write','notebook_path')])
def test_existing_project_files_remain_available(policy, tmp_path, name, key):
    target = tmp_path / 'notes.txt'
    target.write_text('fixture')
    context = state(tmp_path)
    assert decide(context, name, {key: str(target)}) == (True, '')
    assert target.read_text() == 'fixture'
    assert not context.missing_file_target


@pytest.mark.parametrize('key', ['file_path', 'path', 'notebook_path'])
def test_missing_target_uses_the_active_file_and_declared_schema_alias(policy, tmp_path, key):
    target = tmp_path / 'active.txt'
    target.write_text('fixture')
    context = state(tmp_path, active_filename=str(target), tool_map={'Read': {'input_schema': {'properties': {key: {}}}}})
    fields = {}
    assert decide(context, 'Read', fields) == (True, '')
    assert fields == {key: str(target)}


def test_unknown_tools_are_not_rewritten_or_denied_by_file_policy(policy, tmp_path):
    fields = {'query': 'some question'}
    context = state(tmp_path)
    assert decide(context, 'WebSearch', fields) == (True, '')
    assert fields == {'query': 'some question'}
    assert not context.missing_file_target


def test_missing_target_has_a_specific_reason_and_state(policy, tmp_path):
    context = state(tmp_path)
    assert decide(context, 'Read', {}) == (False, 'missing_file_target')
    assert context.missing_file_target and context.policy_denial == 'missing_file_target'


@pytest.mark.parametrize('target', ['notes\nother.txt', 'notes;rm.txt', 'https://example.invalid/file'])
def test_invalid_targets_fail_without_operating_files(policy, tmp_path, target):
    context = state(tmp_path)
    assert decide(context, 'Read', {'file_path': target}) == (False, 'invalid_file_target:' + target)
    assert context.missing_file_target


def test_project_boundary_rejects_traversal_prefix_siblings_and_symlink_escapes(policy, tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    outside = tmp_path / 'project-other'
    outside.mkdir()
    target = outside / 'private.txt'
    target.write_text('unchanged')
    link = project / 'link.txt'
    link.symlink_to(target)
    for path in [str(target), '../project-other/private.txt', str(link)]:
        context = state(project)
        assert decide(context, 'Read', {'file_path': path}) == (False, 'path_outside_project_root:' + path)
    assert target.read_text() == 'unchanged'


@pytest.mark.parametrize('root_source', ['temporary', 'configured'])
def test_explicit_additional_root_allows_only_its_own_descendants(policy, tmp_path, root_source):
    project, extra, other = (tmp_path / name for name in ['project', 'extra', 'other'])
    for path in [project, extra, other]: path.mkdir()
    allowed, refused = extra/'ok.txt', other/'outside.txt'
    allowed.write_text('ok');refused.write_text('outside')
    context = state(project)
    if root_source == 'temporary': context.temporary_allowed_root = str(extra)
    else: policy('file_tools', additional_allowed_roots=['', str(extra)])
    assert decide(context, 'Read', {'path': str(allowed)}) == (True, '')
    assert decide(context, 'Read', {'path': str(refused)})[1] == 'path_outside_project_root:' + str(refused)


def test_explicit_policy_can_disable_root_enforcement_without_disabling_existence_check(policy, tmp_path):
    project = tmp_path / 'project';project.mkdir()
    outside = tmp_path / 'existing.txt';outside.write_text('fixture')
    policy('file_tools', enforce_project_root=False)
    assert decide(state(project), 'Read', {'path': str(outside)}) == (True, '')
    missing = tmp_path / 'missing.txt'
    assert decide(state(project), 'Read', {'path': str(missing)}) == (False, 'target_file_not_found:' + str(missing))


@pytest.mark.parametrize('allow_new', [True, False])
def test_write_creation_is_an_explicit_policy_option(policy, tmp_path, allow_new):
    policy('file_tools', allow_write_new_files=allow_new)
    missing = tmp_path / 'new.txt'
    expected = (True, '') if allow_new else (False, 'target_file_not_found:' + str(missing))
    assert decide(state(tmp_path), 'Write', {'file_path': str(missing)}) == expected
    assert not missing.exists()


@pytest.mark.parametrize('name', ['Read', 'Edit', 'Write'])
def test_existing_control_files_are_readable_but_not_mutable(policy, tmp_path, monkeypatch, name):
    control = tmp_path / 'control.yaml';control.write_text('unchanged')
    monkeypatch.setattr(fp, 'PROTECTED_POLICY_PATHS', {control.resolve()})
    expected = (True, '') if name == 'Read' else (False, 'protected_policy_file:' + str(control))
    assert decide(state(tmp_path), name, {'file_path': str(control)}) == expected
    assert control.read_text() == 'unchanged'


def test_missing_explicit_file_has_a_distinct_denial(policy, tmp_path):
    missing = tmp_path / 'requested.txt'
    context = state(tmp_path, explicit_user_file=str(missing))
    assert decide(context, 'Read', {'path': str(missing)}) == (False, 'explicit_file_not_found:' + str(missing))
    assert context.missing_file_target


def test_existing_explicit_file_corrects_the_same_basename_only(policy, tmp_path):
    actual = tmp_path / 'requested.txt';actual.write_text('fixture')
    context = state(tmp_path, explicit_user_file=str(actual))
    fields = {'file_path': str(tmp_path/'wrong'/'requested.txt')}
    assert decide(context, 'Read', fields) == (True, '')
    assert fields['file_path'] == str(actual)
    other = str(tmp_path/'wrong'/'other.txt')
    assert decide(context, 'Read', {'file_path':other}) == (False, 'target_file_not_found:' + other)


@pytest.mark.parametrize('workflow,command,reason', [
    ('local_implementation','echo hello','bash_display_command_not_allowed:echo'),
    ('local_implementation','printf hello','bash_display_command_not_allowed:printf'),
    ('local_implementation',"cat '<html>fixture</html>'",'bash_source_dump_not_allowed'),
    (None,'   ','bash_empty_command'),
    (None,'cat a | cat','bash_compound_command_not_allowed'),
    (None,'cat a > out','bash_compound_command_not_allowed'),
    (None,'touch item','bash_file_mutation_not_allowed:touch'),
    (None,'sed -i s/a/b/ item','bash_file_mutation_not_allowed:sed -i'),
    (None,"perl -pi -e 's/a/b/' item",'bash_file_mutation_not_allowed:perl -pi'),
    (None,'bash script.sh','bash_compound_command_not_allowed:bash'),
    (None,'xdg-open https://example.invalid','bash_gui_open_not_allowed:xdg-open'),
])
def test_bash_policy_returns_the_specific_reason_without_executing(policy, tmp_path, workflow, command, reason):
    context = state(tmp_path, current_workflow=workflow)
    assert decide(context, 'Bash', {'command':command}) == (False, reason)
    assert context.policy_denial == reason


def test_empty_command_can_be_explicitly_permitted(policy, tmp_path):
    policy('bash', allow_empty_command=True)
    assert decide(state(tmp_path), 'Bash', {'command':''}) == (True, '')


def test_bash_allowlist_respects_command_boundaries_and_whitespace(policy, tmp_path):
    policy('bash', allowed_commands=['  git   status  ', 'wc'])
    for command in ['git status','git   status --short','wc -l README.md']:
        assert decide(state(tmp_path), 'Bash', {'command':command}) == (True, '')
    for command in ['git statusx','git log','wcat README.md']:
        assert decide(state(tmp_path), 'Bash', {'command':command}) == (False,'bash_command_not_allowlisted')


def test_bash_target_obeys_file_roots_and_invalid_input_guards(policy, tmp_path):
    project = tmp_path/'project';project.mkdir()
    for target,reason in [('../outside.txt','path_outside_project_root:'), ('bad;path','invalid_file_target:')]:
        fields = {'command':'cat input.txt','file_path':target}
        assert decide(state(project), 'Bash', fields) == (False,reason+target)


def test_protected_file_stays_immutable_even_with_permissive_bash_options(policy, tmp_path, monkeypatch):
    control=tmp_path/'control.yaml';control.write_text('unchanged')
    monkeypatch.setattr(fp,'PROTECTED_POLICY_PATHS',{control.resolve()})
    policy('bash',allow_compound_commands=True,allow_file_mutation=True)
    for command in [f'rm {control}',f'cat {control} > output',f'python3 {control}']:
        assert decide(state(tmp_path),'Bash',{'command':command}) == (False,'protected_policy_file:'+str(control))
    assert decide(state(tmp_path),'Bash',{'command':f'cat {control}'}) == (True,'')
    assert control.read_text() == 'unchanged'


@pytest.mark.parametrize('prefix', ['env -C {root} cat control.yaml','env --chdir={root} cat control.yaml', 'command -- cat {file}', 'nohup cat {file}'])
def test_known_read_wrappers_preserve_control_file_reads(policy, tmp_path, monkeypatch, prefix):
    control=tmp_path/'control.yaml';control.write_text('unchanged')
    monkeypatch.setattr(fp,'PROTECTED_POLICY_PATHS',{control.resolve()})
    command=prefix.format(root=tmp_path,file=control)
    assert decide(state(tmp_path),'Bash',{'command':command}) == (True,'')


@pytest.mark.parametrize('reason,fragment', [
    ('missing_file_target','однозначно'),('bash_empty_command','bash'),
    ('path_outside_project_root:/fixture/private.txt','/fixture/private.txt'),
    ('explicit_file_not_found:/fixture/missing.txt','/fixture/missing.txt'),
    ('target_file_not_found:/fixture/missing.txt','/fixture/missing.txt'),
    ('protected_policy_file:/fixture/control.yaml','/fixture/control.yaml'),
    ('invalid_file_target:bad;path','bad;path'),
    ('user_forbids_subagents','Agent'),('user_forbids_background:Bash','Bash'),
    ('local_implementation_forbids_tool:WebSearch','WebSearch'),
    ('unsupported_tool_in_local_setup:Notebook','Notebook'),
    ('bash_command_not_allowlisted','allowlist'),
    ('bash_gui_open_not_allowed:open','open'),
    ('bash_display_command_not_allowed:echo','echo'),
    ('bash_source_dump_not_allowed','Write'),
    ('bash_compound_command_not_allowed:bash','bash'),
    ('bash_file_mutation_not_allowed:sed -i','sed -i'),
])
def test_rejection_feedback_keeps_the_actionable_cause(reason, fragment):
    assert fragment in fp.build_policy_denial_response(reason)


@pytest.mark.parametrize('values,expected', [
    (('explicit','read','tool'),'explicit_user_file'),
    ((None,'read','tool'),'last_successful_read_file'),
    ((None,None,'tool'),'last_tool_file'),
    ((None,None,None),'none'),
])
def test_resolution_source_reports_the_actual_priority(values,expected):
    assert fp.file_resolution_source(*values) == expected


def test_temporary_override_is_scoped_and_can_be_disabled(policy,tmp_path):
    project=tmp_path/'project';project.mkdir()
    outside=tmp_path/'outside'/'file.md'
    assert fp.temporary_allowed_root_for_path(str(outside),str(project)) == str(outside.parent)
    assert fp.temporary_allowed_root_for_path(str(project/'inside.md'),str(project)) is None
    assert fp.temporary_allowed_root_for_path('relative.md',str(project)) is None
    assert fp.temporary_allowed_root_for_path(None,str(project)) is None
    policy('file_tools',allow_absolute_path_override=False)
    assert fp.temporary_allowed_root_for_path(str(outside),str(project)) is None


def test_explicit_absolute_path_extraction_ignores_urls_and_plain_text():
    assert fp.extract_explicit_absolute_path('Read `/fixture/notes.md` please') == '/fixture/notes.md'
    assert fp.extract_explicit_absolute_path('https://example.invalid/page') is None
    assert fp.extract_explicit_absolute_path('Please read the notes') is None
    assert fp.extract_explicit_absolute_path('') is None


@pytest.mark.parametrize('tool', ['Read', 'Bash'])
def test_actual_relative_file_decision_uses_the_full_dotted_session_directory(policy, tmp_path, tool):
    project=tmp_path/'my project.v2';project.mkdir()
    target=project/'notes.md';target.write_text('fixture')
    req=SimpleNamespace(system=f'Primary working directory: {project}\r\nOther context',messages=[])
    context=state(fp.extract_project_root(req))
    fields={'file_path':'notes.md'}
    if tool=='Bash':fields['command']='cat notes.md'
    assert decide(context,tool,fields)==(True,'')
    assert target.read_text()=='fixture'
