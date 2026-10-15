from unittest.mock import patch

from factory.core.readiness import check_host_readiness, format_readiness


def test_missing_data_directory_is_not_created(tmp_path):
    root = tmp_path / 'new' / 'data'
    with patch.dict('os.environ', {'PRIVATE_TOKEN': 'secret-never-print'}):
        report = check_host_readiness(str(root))
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []
    assert report['mode'] == 'read_only'
    assert 'secret-never-print' not in str(report)
    assert 'secret-never-print' not in format_readiness(report)


def test_supported_host_keeps_unverified_checks_unknown(tmp_path):
    with patch('platform.freedesktop_os_release', return_value={'ID': 'ubuntu', 'VERSION_ID': '26.04'}), \
         patch('platform.machine', return_value='x86_64'), \
         patch('shutil.which', return_value='/usr/bin/tool'), \
         patch('sys.version_info', (3, 13)), \
         patch('os.access', return_value=True), \
         patch('pathlib.Path.exists', return_value=True):
        report = check_host_readiness(str(tmp_path))
    checks = {c['id']: c for c in report['checks']}
    assert checks['os']['status'] == 'ready'
    assert checks['python']['status'] == 'ready'
    assert checks['kvm_access']['status'] == 'ready'
    assert checks['integration']['status'] == 'unknown'
    assert report['status'] == 'unknown'


def test_missing_components_require_action(tmp_path):
    with patch('platform.freedesktop_os_release', side_effect=OSError), \
         patch('shutil.which', return_value=None), \
         patch('sys.version_info', (3, 12)), \
         patch('os.access', return_value=False):
        report = check_host_readiness(str(tmp_path))
    checks = {c['id']: c for c in report['checks']}
    assert checks['os']['status'] == 'unknown'
    for name in ('python', 'data_directory', 'kvm_access', 'virsh'):
        assert checks[name]['status'] == 'needs_action'
        assert checks[name]['action']
    assert report['status'] == 'needs_action'


def test_other_os_is_not_certified(tmp_path):
    with patch('platform.freedesktop_os_release', return_value={'ID': 'ubuntu', 'VERSION_ID': '24.04'}):
        report = check_host_readiness(str(tmp_path))
    assert report['checks'][0]['status'] == 'needs_action'


def test_cli_read_only_bypasses_database_initialization(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = tmp_path / 'untouched'
    env = dict(os.environ, HOME=str(root), FACTORY_DATA_DIR=str(root))
    cli = Path(__file__).resolve().parents[1] / 'factory' / 'cli.py'
    result = subprocess.run([sys.executable, '-c', 'import factory.cli', 'doctor', '--read-only', '--json', '--data-dir', str(root)],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode in (0, 1), result.stderr
    assert json.loads(result.stdout)['mode'] == 'read_only'
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []
