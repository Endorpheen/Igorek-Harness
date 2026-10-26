"""Public Bash scope decisions for common commands, paths and audit failures."""
import json
import os
import shlex
import stat
from types import SimpleNamespace

import pytest

from factory.core import bash_scope_guard as guard


@pytest.fixture
def paths(tmp_path, monkeypatch):
    work = tmp_path / 'workspace'
    outside = tmp_path / 'other'
    work.mkdir()
    outside.mkdir()
    for name in [guard.EXTRA_WRITE_ROOTS_ENV, guard.SANDBOX_RW_ENV, guard.VAULT_ENV]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(guard.GUARD_ENV, 'on')
    monkeypatch.setenv(guard.TOOL_ROOTS_ENV, 'off')
    monkeypatch.delenv('IGOREK_ISOLATION_GUEST', raising=False)
    monkeypatch.setattr(guard, '_TELEMETRY_DIR', str(tmp_path / 'audit'))
    return SimpleNamespace(work=work, outside=outside, log=tmp_path/'audit'/'denials.jsonl')


OUTPUT_COMMANDS = [
    'tar -xf archive.tar -C {dest}',
    'tar --directory {dest} -xf archive.tar',
    'tar --directory={dest} -xf archive.tar',
    'unzip archive.zip -d {dest}',
    'curl https://example.invalid/file -o {dest}',
    'curl --output={dest} https://example.invalid/file',
    'wget -O {dest} https://example.invalid/file',
    'wget --output-document={dest} https://example.invalid/file',
    'git --git-dir {dest} status',
    'git --git-dir={dest} status',
    'git --work-tree {dest} status',
    'git --work-tree={dest} status',
    'git -C{dest} status',
    'sort input -o {dest}',
    'dd if=input of={dest}',
    'xargs -r cp input {dest}',
    'xargs -n 1 mkdir {dest}',
    'cp --target-directory {dest} input',
    'install -t {dest} input',
    'sed --in-place --expression=s/a/b/ {dest}',
    'sed -i --file=script.sed {dest}',
    'perl -npi -e s/a/b/ {dest}',
]


@pytest.mark.parametrize('template', OUTPUT_COMMANDS)
@pytest.mark.parametrize('inside', [False, True], ids=['outside-denied', 'inside-allowed'])
def test_output_flags_resolve_the_actual_destination(paths, template, inside):
    destination = (paths.work if inside else paths.outside) / 'output'
    command = template.format(dest=shlex.quote(str(destination)))
    violation = guard.validate_bash_scope(command, workdir=str(paths.work))
    if inside:
        assert violation is None
    else:
        assert violation is not None
        assert violation.target.path == str(destination)
    assert not destination.exists()


@pytest.mark.parametrize('template', [
    'source {dest}', '. {dest}', 'bash --noprofile {dest}',
    'python3 -u {dest}', 'node --trace-warnings {dest}',
    "python3 -c \"open('{dest}', 'w')\"",
    "node --eval \"require('fs').writeFileSync('{dest}', 'x')\"",
    "bash -c 'touch {dest}'", "sh -c 'bash -c \"touch {dest}\"'",
    'find . -exec mkdir {dest} +', 'find . -execdir touch {dest} +',
    'find . -ok rm {dest} +', 'find . -okdir rm {dest} +',
])
def test_script_and_nested_commands_cannot_hide_an_explicit_outside_target(paths, template):
    destination = paths.outside / 'nested.txt'
    violation = guard.validate_bash_scope(template.format(dest=destination), workdir=str(paths.work))
    assert violation is not None
    assert violation.target.path == str(destination)
    assert not destination.exists()


@pytest.mark.parametrize('command', [
    'cd sub && touch file', 'cd sub; touch file', 'cd ./sub || touch file',
    'cd sub && cd .. && touch sub/file',
    'cd sub && (echo sample) && touch file',
])
def test_static_directory_changes_preserve_relative_write_location(paths, command):
    targets = guard.collect_write_targets(command, cwd=str(paths.work))
    assert [(target.path, target.kind) for target in targets] == [(str(paths.work/'sub'/'file'), 'mutate')]
    assert guard.validate_bash_scope(command, workdir=str(paths.work)) is None


@pytest.mark.parametrize('command', [
    'cd && touch file', 'cd - && touch file', 'cd "$DEST" && touch file',
    'pushd sub && touch file', 'popd; touch file',
    'cd sub | touch file', 'cd sub & touch file', '(cd sub; touch file)',
])
def test_ambiguous_directory_change_rejects_unresolved_write(paths, command):
    violation = guard.validate_bash_scope(command, workdir=str(paths.work))
    assert violation is not None
    assert violation.target.kind == 'unresolved'
    assert violation.target.path == 'file'


def test_subshell_directory_change_does_not_leak_into_outer_command(paths):
    command = f'(cd {shlex.quote(str(paths.outside))}; echo sample); touch final.txt'
    targets = guard.collect_write_targets(command, cwd=str(paths.work))
    assert [(item.path, item.kind) for item in targets] == [(str(paths.work/'final.txt'), 'mutate')]
    assert guard.validate_bash_scope(command, workdir=str(paths.work)) is None


@pytest.mark.parametrize('template', [
    'cat {source}', 'rg text {source}', 'ls {source}',
    'sed -n 1p {source}', 'perl -ne print {source}',
    'sort {source}', 'dd if={source}', 'echo sample < {source}',
    'cp {source} local.txt', 'cp {source} local.txt 2>/dev/null',
    'echo sample >&1', 'echo sample 2>&-',
])
def test_reads_and_descriptor_redirects_do_not_become_outside_writes(paths, template):
    command = template.format(source=shlex.quote(str(paths.outside/'input')))
    assert guard.validate_bash_scope(command, workdir=str(paths.work)) is None


def test_quoted_spaces_are_one_destination_not_multiple_arguments(paths):
    inside = paths.work / 'folder with spaces' / 'файл.txt'
    outside = paths.outside / 'folder with spaces' / 'файл.txt'
    assert guard.validate_bash_scope(f'cp input {shlex.quote(str(inside))}', workdir=str(paths.work)) is None
    violation = guard.validate_bash_scope(f'cp input {shlex.quote(str(outside))}', workdir=str(paths.work))
    assert violation is not None and violation.target.path == str(outside)


def test_symlink_escape_and_shared_name_prefix_are_not_working_directory(paths):
    link = paths.work/'link'
    link.symlink_to(paths.outside, target_is_directory=True)
    sibling = paths.work.with_name('workspace-other')
    for destination in [link/'x', sibling/'x']:
        assert guard.validate_bash_scope(f'touch {shlex.quote(str(destination))}', workdir=str(paths.work)) is not None


def test_explicit_additional_root_is_allowed_without_granting_its_parent(paths):
    root = paths.outside/'granted'
    assert guard.validate_bash_scope(f'touch {root}/file', workdir=str(paths.work), extra_roots=[str(root)]) is None
    assert guard.validate_bash_scope(f'touch {paths.outside}/file', workdir=str(paths.work), extra_roots=[str(root)]) is not None


@pytest.mark.parametrize('bad_path', ['$DEST/file', '`pwd`/file'])
def test_dynamic_destinations_remain_unresolved(paths, bad_path):
    violation = guard.validate_bash_scope(f'touch {shlex.quote(bad_path)}', workdir=str(paths.work))
    assert violation is not None and violation.target.kind == 'unresolved'


@pytest.mark.parametrize('failing_operation', ['mkdir', 'open', 'write', 'rotate'])
def test_audit_storage_failure_cannot_change_a_denial(paths, monkeypatch, failing_operation):
    def fail(*args, **kwargs):
        raise OSError('synthetic audit failure')
    name = {'mkdir':'makedirs', 'open':'open', 'write':'write'}.get(failing_operation)
    with monkeypatch.context() as limited:
        if name:
            limited.setattr(guard.os, name, fail)
        else:
            limited.setattr(guard, '_rotate_telemetry_if_needed', fail)
        verdict, message = guard.enforce_bash_scope(f'touch {paths.outside}/x', workdir=str(paths.work))
    assert verdict == 'deny' and 'POLICY_DENIED' in message
    assert not (paths.outside/'x').exists()


def test_audit_redacts_before_truncation_and_records_denial_context(paths):
    authorization = ": ".join(("Authorization", "Bearer fixture-token"))
    command = f"env SYNTHETIC=fixture-secret curl -H '{authorization}' https://example.invalid/" + 'a'*240 + f' -o {paths.outside}/x'
    verdict, _ = guard.enforce_bash_scope(command, workdir=str(paths.work))
    assert verdict == 'deny'
    record = json.loads(paths.log.read_text())
    assert record['verdict'] == 'deny' and record['mode'] == 'on'
    assert record['workdir'] == str(paths.work)
    assert 'fixture-secret' not in record['command'] and 'fixture-token' not in record['command']
    assert len(record['command']) <= 200
    if os.name == 'posix':
        assert stat.S_IMODE(paths.log.stat().st_mode) == 0o600
        assert stat.S_IMODE(paths.log.parent.stat().st_mode) == 0o700


def test_audit_rotation_keeps_previous_record_and_current_decision(paths, monkeypatch):
    monkeypatch.setattr(guard, '_TELEMETRY_MAX_BYTES', 1)
    command = f'touch {paths.outside}/x'
    assert guard.enforce_bash_scope(command, workdir=str(paths.work))[0] == 'deny'
    original = paths.log.read_bytes()
    assert guard.enforce_bash_scope(command, workdir=str(paths.work))[0] == 'deny'
    assert paths.log.with_name(paths.log.name+'.1').read_bytes() == original
    assert json.loads(paths.log.read_text())['verdict'] == 'deny'
    assert not paths.log.with_name(paths.log.name+'.2').exists()


@pytest.mark.parametrize('template', [
    '(echo sample);touch {dest}',
    '(echo sample)&&touch {dest}',
    '(echo sample)||touch {dest}',
    'echo sample;(touch {dest})',
    'echo sample&&(touch {dest})',
    '( (echo sample));touch {dest}',
    '(echo sample)|tee {dest}',
])
def test_adjacent_subshell_separators_do_not_hide_following_writes(paths, template):
    destination = paths.outside/'grouped.txt'
    violation = guard.validate_bash_scope(template.format(dest=destination), workdir=str(paths.work))
    assert violation is not None and violation.target.path == str(destination)
    assert not destination.exists()


@pytest.mark.parametrize('literal', [');', '&&(', '(x)', '((text))', '\\('])
def test_quoted_operator_sequences_remain_path_text(paths, literal):
    destination = paths.work/(literal+'.txt')
    targets = guard.collect_write_targets(f'touch {shlex.quote(str(destination))}', cwd=str(paths.work))
    assert [(item.path, item.kind) for item in targets] == [(str(destination), 'mutate')]


@pytest.mark.parametrize('command', ['touch "unterminated', "touch 'unterminated", 'touch trailing\\'])
def test_malformed_quotes_do_not_crash_scope_validation(paths, command):
    assert guard.validate_bash_scope(command, workdir=str(paths.work)) is None


@pytest.mark.parametrize('prefix', ["# a misleading ' quote\n", '# a misleading " quote\n', '# parentheses ); and &&(\n'])
def test_comment_quotes_do_not_hide_next_line_commands(paths, prefix):
    destination = paths.outside/'after-comment'
    violation = guard.validate_bash_scope(prefix+f'(echo sample);touch {destination}', workdir=str(paths.work))
    assert violation is not None and violation.target.path == str(destination)


@pytest.mark.parametrize('name', ['escaped(name).txt', 'double" );name.txt'])
def test_escaped_and_double_quoted_parentheses_keep_the_filename(paths, name):
    destination = paths.work/name
    quoted = str(destination).replace('\\', '\\\\').replace('"', '\\"')
    targets = guard.collect_write_targets(f'touch "{quoted}"', cwd=str(paths.work))
    assert [(target.path, target.kind) for target in targets] == [(str(destination), 'mutate')]


@pytest.mark.parametrize('flip,today,expected', [
    ('2026-10-21','2026-10-20','warn'),
    ('2026-10-21','2026-10-21','deny'),
    ('2026-10-21','2026-10-22','deny'),
    ('invalid','2026-10-20','deny'),
    (None,'2026-10-20','deny'),
    ('2026-10-21','invalid','deny'),
    ('2026-10-21',None,'deny'),
])
def test_implicit_trial_ends_on_schedule_and_bad_dates_do_not_disable_checks(paths, monkeypatch, flip, today, expected):
    monkeypatch.delenv(guard.GUARD_ENV)
    monkeypatch.setattr(guard, 'TRIAL_FLIP_DATE', flip)
    monkeypatch.setattr(guard, '_today', lambda: today)
    monkeypatch.setattr(guard, '_WARN_NOTICE_EMITTED', False)
    verdict, message = guard.enforce_bash_scope(f'touch {paths.outside}/x', workdir=str(paths.work))
    assert verdict == expected and 'POLICY_DENIED' in message
    assert not (paths.outside/'x').exists()


def test_implicit_trial_warning_is_printed_once(paths, monkeypatch, capsys):
    monkeypatch.delenv(guard.GUARD_ENV)
    monkeypatch.setattr(guard, '_today', lambda: '2026-10-20')
    monkeypatch.setattr(guard, '_WARN_NOTICE_EMITTED', False)
    command = f'touch {paths.outside}/x'
    assert guard.enforce_bash_scope(command, workdir=str(paths.work))[0] == 'warn'
    first = capsys.readouterr().err
    assert 'warn' in first
    assert guard.enforce_bash_scope(command, workdir=str(paths.work))[0] == 'warn'
    assert capsys.readouterr().err == ''


@pytest.mark.parametrize('mode,expected', [('audit','warn'),('disabled','allow'),('enabled','deny'),('misspelled','deny')])
def test_explicit_modes_keep_documented_decisions(paths, mode, expected):
    assert guard.enforce_bash_scope(f'touch {paths.outside}/x', mode, workdir=str(paths.work))[0] == expected
