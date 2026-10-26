"""Independent read-evidence boundaries using the actual inference fixture."""
from types import SimpleNamespace

import pytest

from inference_api_support import inference_app as inference_app


def exchange(lines, start=1):
    return SimpleNamespace(result_text='\n'.join(f'{i}→{line}' for i, line in enumerate(lines, start)))


def test_all_evidence_categories_survive_aggregation_and_duplicate_reads(inference_app):
    api = inference_app.api
    item = exchange(['import os', 'MAX_ITEMS = 10', 'def main():', '    pass',
                     '@app.get("/owned")', 'async def endpoint():', '    pass'])
    result = api._summarize_read_evidence('owned.py', [item, item])
    assert result == api._summarize_read_evidence('owned.py', [item])
    for part in ['lines 1-7', 'imports os', 'constants MAX_ITEMS', 'sections main, endpoint',
                 'entrypoints main', 'decorated entrypoints endpoint']:
        assert part in result


def test_aggregated_category_limits_preserve_section_endpoints(inference_app):
    api = inference_app.api
    items = [exchange([f'CONST_{i} = {i}' for i in range(start, start + 10)] +
                      [f'def function_{i}(): pass' for i in range(start, start + 10)], start + 1)
             for start in (0, 10)]
    result = api._summarize_read_evidence('owned.py', items)
    constants = result.split('constants ', 1)[1].split('; ', 1)[0].split(', ')
    sections = result.split('sections ', 1)[1].split('; ', 1)[0].split(', ')
    assert constants == [f'CONST_{i}' for i in range(8)]
    assert len(sections) <= 10 and sections[0] == 'function_0' and sections[-1] == 'function_19'


def test_same_numbered_range_does_not_hide_distinct_read_evidence(inference_app):
    api = inference_app.api
    first = exchange(['import alpha', 'VALUE_A = 1'])
    second = exchange(['import beta', 'VALUE_B = 2'])
    result = api._summarize_read_evidence('owned.py', [first, second, first])
    assert result == 'owned.py: lines 1-2; imports alpha, beta; constants VALUE_A, VALUE_B'


def test_directory_listing_keeps_valid_rows_among_rejected_entries(inference_app):
    def row(name, kind='d'):
        return f'{kind}rwxr-xr-x 2 owner group 4096 Jan 1 10:00 {name}'
    text = '\n'.join(['total 20', 'root:', 'drwx short', row('.', 'd'), row('..', 'd'),
                      row('good'), row('bad(name'), row('not-directory', '-'),
                      row('not-followed-link', 'l'), row('good'), row('tail/')])
    assert inference_app.api._extract_ls_directories(text) == ['good', 'tail']


def test_coverage_evidence_only_counts_distinct_trackable_scans(inference_app):
    api = inference_app.api
    scans = ['ls .', 'ls backend', 'ls backend/src', 'ls backend/other', 'ls frontend/src', 'ls unknown']
    assert api._project_analysis_covered_dirs(scans, ['backend', 'frontend', '.git', '__pycache__']) == ['backend']
    assert api._project_analysis_covered_deep_branches(scans, ['backend', 'frontend']) == ['backend', 'frontend']
    assert api._project_analysis_covered_deep_branches(scans[:2], ['backend']) == []


def test_candidate_dedup_preserves_distinct_case_sensitive_paths(inference_app):
    assert inference_app.api._project_analysis_deep_branch_candidates(
        ['Backend', 'backend', 'Backend', 'misc', 'frontend', 'frontend']) == ['Backend', 'backend', 'frontend']


@pytest.mark.parametrize('target,text,root_enabled', [
    ('notes.txt', 'import local', True),
    ('entry.py', '', True),
    ('entry.py', 'import local', False),
])
def test_related_files_require_python_text_and_project_root(inference_app, tmp_path, target, text, root_enabled):
    (tmp_path / 'local.py').write_text('VALUE = 1\n')
    assert inference_app.api._extract_related_python_files(target, text, str(tmp_path) if root_enabled else None) == []


def test_related_imports_preserve_order_aliases_packages_and_self_exclusion(inference_app, tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    entry, other = root / 'entry.py', root / 'other.py'
    entry.write_text('')
    other.write_text('')
    package = root / 'package'
    package.mkdir()
    initializer = package / '__init__.py'
    initializer.write_text('')
    text = 'import entry, other as alias\nfrom package import child\nimport other\nimport .relative\n'
    result = inference_app.api._extract_related_python_files(str(entry), text, str(root))
    assert result == [str(other.resolve()), str(initializer.resolve())]


def test_related_import_scan_stops_after_line_two_hundred(inference_app, tmp_path):
    before, after, entry = tmp_path / 'before.py', tmp_path / 'after.py', tmp_path / 'entry.py'
    for path in (before, after, entry):
        path.write_text('')
    text = '\n'.join(['# owned filler'] * 199 + ['import before', 'import after'])
    assert inference_app.api._extract_related_python_files(str(entry), text, str(tmp_path)) == [str(before.resolve())]


def test_resolved_escape_is_rejected_without_blocking_a_valid_local_module(inference_app, tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    outside, local, entry = tmp_path / 'outside.py', root / 'local.py', root / 'entry.py'
    for path in (outside, local, entry):
        path.write_text('')
    path_type = type(root)
    original_resolve = path_type.resolve
    def resolve(path, *args, **kwargs):
        if path == root / 'escape_proxy.py':
            return original_resolve(outside)
        return original_resolve(path, *args, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(path_type, 'resolve', resolve)
        assert inference_app.api._resolve_python_module_path('escape_proxy', root) is None
        result = inference_app.api._extract_related_python_files(str(entry), 'import escape_proxy\nimport local', str(root))
    assert result == [str(local.resolve())]
