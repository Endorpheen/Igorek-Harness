"""Publication checks use clean sources and never expose configuration values."""
import ast
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("suffix,text", [
    (".py", 'label = "Русский интерфейс"\n# Комментарий\n'),
    (".js", 'const label = "Русский интерфейс"; // Комментарий\n'),
    (".kt", 'val label = "Русский интерфейс" // Комментарий\n'),
    (".sh", 'printf "%s" "Русский интерфейс"\n# Комментарий\n'),
    (".html", '<script>const label="Русский интерфейс"; // Комментарий\n</script>'),
    (".xml", '<root label="Русский интерфейс"><!-- Комментарий --></root>'),
])
def test_language_check_distinguishes_comments_from_translated_interface_text(monkeypatch,suffix,text):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import check_publication
    values = list(check_publication.comments(Path("fixture" + suffix), text))
    assert len(values) == 1
    assert "Комментарий" in values[0][1]
    assert "Русский интерфейс" not in values[0][1]


def test_environment_bootstrap_loads_values_without_printing_them(tmp_path, monkeypatch, capsys, caplog):
    source = ROOT / "factory/core/cc_backend_tools.py"
    tree = ast.parse(source.read_text())
    prefix = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "checkpoint_manager":
            break
        if isinstance(node, ast.ImportFrom) and node.level:
            continue
        prefix.append(node)
    tree.body = prefix
    config = tmp_path / "factory/config"
    config.mkdir(parents=True)
    canary = "fixture-mcp-log-canary"
    (config / "gateway.default.env").write_text("MCP_FIXTURE_PASSWORD=" + canary + "\n")
    monkeypatch.setenv("FACTORY_VAULT_ENABLED", "1")
    monkeypatch.delenv("MCP_FIXTURE_PASSWORD", raising=False)
    import logging
    caplog.set_level(logging.DEBUG, logger="factory.core.cc_backend_tools")
    scope = {"__file__": str(tmp_path / "factory/core/cc_backend_tools.py"),
             "__name__": "factory.core.cc_backend_tools", "__package__": "factory.core"}
    exec(compile(tree, str(source), "exec"), scope)
    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err + caplog.text
    assert scope["os"].environ["MCP_FIXTURE_PASSWORD"] == canary


def test_clean_source_inventory_ignores_stale_build_modules(tmp_path):
    import subprocess
    spec = importlib.util.spec_from_file_location("publication_wheel", ROOT / "tools/build_wheel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("build/\n")
    (tmp_path / "factory").mkdir()
    (tmp_path / "factory/current.py").write_text("VALUE = 1\n")
    (tmp_path / "build/lib/factory").mkdir(parents=True)
    (tmp_path / "build/lib/factory/retired.py").write_text("raise RuntimeError('stale')\n")
    paths = module.source_files(tmp_path)
    assert Path("factory/current.py") in paths
    assert not any(path.parts[0] == "build" for path in paths)


@pytest.mark.parametrize('suffix,prefix,suffix_text,first_line', [
    ('.js', 'const value = 1;\n', '', 2),
    ('.html', '<html>\n<script>\n', '</script>\n</html>', 3),
])
def test_comment_locations_survive_large_trees_and_nested_scripts(monkeypatch, suffix, prefix, suffix_text, first_line):
    monkeypatch.syspath_prepend(str(ROOT / 'tools'))
    import check_publication
    source = prefix + ''.join(f'// marker {index}\n' for index in range(128)) + suffix_text
    expected = [(first_line + index, f'// marker {index}') for index in range(128)]
    for _ in range(32):
        assert list(check_publication.comments(Path('fixture' + suffix), source)) == expected
