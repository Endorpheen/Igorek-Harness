"""Skill assets may use registered package links but never outside host files."""
from factory.core.isolation.extensions import collect_task_extensions


def test_nested_skill_link_cannot_export_host_data(tmp_path):
    work = tmp_path / 'work'
    runtime = work / '.skill_runtime/demo'
    runtime.mkdir(parents=True)
    package = tmp_path / 'package'
    (package / 'scripts').mkdir(parents=True)
    private = tmp_path / 'private.txt'
    private.write_text('synthetic-private-canary')
    (package / 'scripts/escape.txt').symlink_to(private)
    (runtime / 'scripts').symlink_to(package / 'scripts', target_is_directory=True)
    result = collect_task_extensions(str(work), skill_sources={'demo': str(package)})
    assert not result.skill_assets
    assert result.statuses


def test_internal_package_links_remain_exportable_but_env_is_private(tmp_path):
    work = tmp_path / 'work'
    runtime = work / '.skill_runtime/demo'
    runtime.mkdir(parents=True)
    package = tmp_path / 'package'
    (package / 'scripts').mkdir(parents=True)
    (package / 'scripts/source.txt').write_text('public fixture')
    (package / 'scripts/alias.txt').symlink_to(package / 'scripts/source.txt')
    (runtime / 'scripts').symlink_to(package / 'scripts', target_is_directory=True)
    result = collect_task_extensions(str(work), skill_sources={'demo': str(package)})
    assert not result.statuses
    assert {item.path for item in result.skill_assets} == {'scripts/source.txt', 'scripts/alias.txt'}
    (package / 'env').mkdir()
    (package / 'env/private.txt').write_text('private fixture')
    (package / 'scripts/secret.txt').symlink_to(package / 'env/private.txt')
    result = collect_task_extensions(str(work), skill_sources={'demo': str(package)})
    assert not result.skill_assets
    assert result.statuses


def test_asset_swap_after_authorization_cannot_read_outside_file(tmp_path, monkeypatch):
    from factory.core.isolation import extensions
    runtime = tmp_path / '.skill_runtime/demo'
    runtime.mkdir(parents=True)
    target = runtime / 'fixture.txt'
    target.write_text('public fixture')
    private = tmp_path / 'private.txt'
    private.write_text('private fixture')
    original = extensions.open_regular_file

    def swap(path):
        target.unlink()
        target.symlink_to(private)
        return original(path)

    monkeypatch.setattr(extensions, 'open_regular_file', swap)
    result = extensions.collect_task_extensions(str(tmp_path))
    assert not result.skill_assets
    assert result.statuses
