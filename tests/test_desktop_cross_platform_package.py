"""Release gates for cross-platform candidates and native acceptance evidence."""
import importlib.util
import json
from pathlib import Path
import sys
import tomllib

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / 'desktop/packaging'
sys.path.insert(0, str(PACKAGING))


def load(name):
    spec = importlib.util.spec_from_file_location(name, PACKAGING / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('value', [{}, {'status': 'passed', 'stages': []},
    {'status': 'failed', 'stages': []}])
def test_partial_native_reports_are_not_accepted(tmp_path, value):
    module = load('check-native-report')
    (tmp_path / 'summary.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):
        module.verify(tmp_path)


def test_complete_native_report_is_required(tmp_path):
    module = load('check-native-report')
    value = {'status': 'passed', 'stages': sorted(module.STAGES)}
    (tmp_path / 'summary.json').write_text(json.dumps(value))
    assert module.verify(tmp_path) == value
    value['stages'].pop()
    (tmp_path / 'summary.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):
        module.verify(tmp_path)


def test_staging_refuses_wrong_platform_before_copying(tmp_path):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    version = tomllib.loads((PACKAGING.parents[1] / 'pyproject.toml').read_text())['project']['version']
    (runtime / 'manifest.json').write_text(json.dumps({'schema_version': 1,
        'factory_version': version, 'platform': 'linux-x64', 'python': 'python/bin/python3.14'}))
    output = tmp_path / 'app'
    with pytest.raises(ValueError, match='Windows x64'):
        load('stage-app').stage(runtime, output, 'windows-x64')
    assert not output.exists()


def test_staging_cannot_recurse_into_its_runtime(tmp_path):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    (runtime / 'manifest.json').write_text('{}')
    with pytest.raises(ValueError, match='inside its runtime'):
        load('stage-app').stage(runtime, runtime / 'app', 'windows-x64')


@pytest.mark.parametrize('incomplete',[True,False])
def test_native_search_gate_requires_real_stages_and_clean_visible_ui(tmp_path,incomplete):
    module=load('check-native-report')
    report={'status':'passed','stages':sorted(module.SEARCH_STAGES),
            'pageErrors':[],'layout':{'visible':True,'overflow':False}}
    if incomplete:report['stages'].pop()
    (tmp_path/'summary.json').write_text(json.dumps(report))
    if incomplete:
        with pytest.raises(ValueError):module.verify_search(tmp_path)
    else:assert module.verify_search(tmp_path)==report


def test_reviewed_app_inventory_contains_literal_commonjs_dependencies():
    import re
    files = set(load('bundle_sources').APP_FILES)
    desktop = PACKAGING.parent
    missing = []
    for name in sorted(files):
        if not name.endswith('.cjs'):
            continue
        source = (desktop / name).read_text(encoding='utf-8')
        for dependency in re.findall(r"\brequire\(\s*['\"](\./[^'\"]+\.cjs)['\"]\s*\)", source):
            relative = ((desktop / name).parent / dependency).relative_to(desktop).as_posix()
            if relative not in files:
                missing.append((name, relative))
    assert missing == []
