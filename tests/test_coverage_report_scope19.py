"""Coverage reports must exclude tests without excluding product code."""

import json
import os
from pathlib import Path
import subprocess
import sys


def test_reports_exclude_factory_test_files_but_keep_product_files(tmp_path):
    root = tmp_path / 'fixture'
    product = root / 'factory' / 'product.py'
    testfile = root / 'factory' / 'tests' / 'test_example.py'
    testfile.parent.mkdir(parents=True)
    product.write_text('value = 1\n', encoding='utf-8')
    testfile.write_text('value = 2\n', encoding='utf-8')
    wiki = root / 'tools' / 'wiki'
    wiki.mkdir(parents=True)
    (wiki / 'check.py').write_text('value = 3\n', encoding='utf-8')
    (wiki / 'test_check.py').write_text('value = 4\n', encoding='utf-8')
    output = tmp_path / 'out'
    output.mkdir()
    script = '''
import importlib.util
from pathlib import Path
import runpy
import sys
from coverage import Coverage
root, output, runner = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location('isolated_runner', runner)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
cov = Coverage(data_file=None, branch=True, include=[str(root / '*')])
cov.start()
runpy.run_path(str(root / 'factory' / 'product.py'))
runpy.run_path(str(root / 'factory' / 'tests' / 'test_example.py'))
runpy.run_path(str(root / 'tools' / 'wiki' / 'check.py'))
runpy.run_path(str(root / 'tools' / 'wiki' / 'test_check.py'))
cov.stop()
module.write_coverage_reports(cov, output)
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith('COVERAGE_')}
    runner = Path(__file__).resolve().parents[1] / 'tools' / 'run_tests.py'
    subprocess.run([sys.executable, '-c', script, str(root), str(output), str(runner)],
                   env=env, check=True, capture_output=True, text=True, timeout=20)
    report = json.loads((output / 'coverage.json').read_text(encoding='utf-8'))
    paths = list(report['files'])
    assert any(path.replace('\\', '/').endswith('/factory/product.py') for path in paths)
    assert not any('/factory/tests/' in path.replace('\\', '/') for path in paths)
    assert any(path.replace('\\', '/').endswith('/tools/wiki/check.py') for path in paths)
    assert not any(path.replace('\\', '/').endswith('/tools/wiki/test_check.py') for path in paths)
    xml = (output / 'coverage.xml').read_text(encoding='utf-8')
    assert 'product.py' in xml and 'test_example.py' not in xml
    assert 'check.py' in xml and 'test_check.py' not in xml
