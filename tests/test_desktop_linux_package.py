import importlib.util
import json
import shutil
import tarfile
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1] / 'desktop'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = load('desktop_linux_package', DESKTOP / 'packaging' / 'package-linux.py')
verifier = load('desktop_package_verify', DESKTOP / 'packaging' / 'verify-package.py')


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / 'repo'; desktop = repo / 'desktop'; desktop.mkdir(parents=True)
    for name in package.LEGAL_FILES:
        destination = repo / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DESKTOP.parent / name, destination)
    (desktop / 'packaging').mkdir()
    (repo / 'pyproject.toml').write_text('[project]\nversion = "0.1.819"\n')
    (desktop / 'package.json').write_text(json.dumps({'devDependencies': {'electron': '44.5.1'}}))
    for name in package.APP_FILES + package.BACKEND_FILES:
        (desktop / name).write_text('fixture source')
    for name in ['verify-package.py', 'portable-launcher.sh', 'install-sandbox-policy.sh', 'install-linux.py', 'install-gui.py', 'installer-gtk.py', 'install-libvirt-helper.py', 'install-gui.sh', 'igorek.svg']:
        shutil.copy2(DESKTOP / name if name == 'igorek.svg' else DESKTOP / 'packaging' / name, desktop / name if name == 'igorek.svg' else desktop / 'packaging' / name)
    (desktop / 'assets').mkdir()
    (desktop / 'assets/release-notes.json').write_text(json.dumps({'version':'0.1.819','changes':['Fixture release']}))
    shutil.copy2(DESKTOP / 'assets/evil-octopus.png', desktop / 'assets/evil-octopus.png')
    shutil.copy2(DESKTOP / 'assets/evil-octopus-wink.png', desktop / 'assets/evil-octopus-wink.png')
    shutil.copy2(DESKTOP / 'assets/igorek.png', desktop / 'assets/igorek.png')
    shutil.copy2(DESKTOP / 'assets/OPENAI-ICON-LICENSE.txt', desktop / 'assets/OPENAI-ICON-LICENSE.txt')
    shutil.copy2(DESKTOP / 'assets/github.svg', desktop / 'assets/github.svg')
    shutil.copy2(DESKTOP / 'assets/GITHUB-ICON-LICENSE.txt', desktop / 'assets/GITHUB-ICON-LICENSE.txt')
    runtime = desktop / 'runtime'; runtime.mkdir()
    python = runtime / 'python'; python.mkdir()
    executable = python / 'python3'
    executable.write_text('#!/usr/bin/env python3\nimport runpy,sys\nsys.argv=sys.argv[1:]\n'
        'while sys.argv and sys.argv[0] in {"-B","-I"}:\n'
        ' if sys.argv.pop(0)=="-B": sys.dont_write_bytecode=True\n'
        'runpy.run_path(sys.argv[0],run_name="__main__")\n')
    executable.chmod(0o755)
    (runtime / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'factory_version': '0.1.819', 'python': 'python/python3',
        'source_commit': 'a' * 40, 'source_modified': False}))
    (runtime / 'alias').symlink_to('python/python3')
    cache = runtime / '__pycache__'; cache.mkdir(); (cache / 'private.pyc').write_bytes(b'cache')
    electron = desktop / 'node_modules/electron/dist'; electron.mkdir(parents=True)
    (electron / 'version').write_text('44.5.1')
    (electron / 'electron').write_text('fixture binary'); (electron / 'electron').chmod(0o755)
    (electron / 'chrome-sandbox').write_text('fixture sandbox'); (electron / 'chrome-sandbox').chmod(0o4755)
    (repo / 'handoff.md').write_text('private notes')
    (desktop / '.env').write_text('PRIVATE_TOKEN=never-package')
    return repo


def test_offline_snapshot_is_relocatable_and_excludes_private_files(source, tmp_path):
    output = tmp_path / 'snapshot'
    package.build(source, output)
    root = output / 'igorek-desktop'
    manifest = verifier.verify(root)
    assert manifest['factory_version'] == '0.1.819'
    assert manifest['verification'] == 'local-integrity-only'
    assert manifest['source_commit'] == 'a' * 40 and manifest['source_modified'] is False
    assert b'Apache License' in (root / 'licenses/LICENSE').read_bytes()
    assert b'OpenAI Codex' in (root / 'licenses/CODEX-NOTICE.txt').read_bytes()
    assert (root / 'app/workspace-browser.cjs').is_file()
    assert (root / 'app/download-history.cjs').is_file()
    assert (root / 'app/completion-sound.cjs').is_file()
    assert (root / 'app/completion-sound-settings.js').is_file()
    assert (root / 'app/about.html').is_file()
    assert (root / 'app/assets/github.svg').is_file()
    assert json.loads((root / 'app/assets/release-notes.json').read_text()) == {'version':'0.1.819','changes':['Fixture release']}
    assert (root / 'app/assets/evil-octopus-wink.png').read_bytes() == (DESKTOP / 'assets/evil-octopus-wink.png').read_bytes()
    assert b'MIT License' in (root / 'app/assets/GITHUB-ICON-LICENSE.txt').read_bytes()
    assert (root / 'app/github-reviews.cjs').is_file()
    assert (root / 'app/github-controller.cjs').is_file()
    assert b'MIT License' in (root / 'app/assets/OPENAI-ICON-LICENSE.txt').read_bytes()
    assert not list(root.rglob('*.pyc'))
    assert not list(root.rglob('.env'))
    assert not list(root.rglob('handoff.md'))
    assert (root / 'electron/chrome-sandbox').stat().st_mode & 0o7777 == 0o755
    moved = tmp_path / 'moved'; root.rename(moved)
    assert verifier.verify(moved)['build_id'] == manifest['build_id']
    archive = next(output.glob('*.tar.gz'))
    with tarfile.open(archive) as tar:
        assert all(name.startswith('igorek-desktop/') or name == 'igorek-desktop' for name in tar.getnames())
    assert (output / (archive.name + '.sha256')).exists()
    with pytest.raises(FileExistsError):
        package.build(source, output)


def test_separate_runtime_does_not_replace_active_bundle(source, tmp_path):
    runtime = source / 'desktop/runtime'
    separate = tmp_path / 'fresh-runtime'
    runtime.rename(separate)
    runtime.mkdir()
    (runtime / 'active-marker').write_text('running bundle remains untouched')
    output = tmp_path / 'separate-snapshot'
    package.build(source, output, runtime_path=separate)
    assert (runtime / 'active-marker').read_text() == 'running bundle remains untouched'
    assert not (output / 'igorek-desktop/app/runtime/active-marker').exists()
    assert verifier.verify(output / 'igorek-desktop')['factory_version'] == '0.1.819'


@pytest.mark.parametrize('kind', ['contents', 'mode', 'missing', 'outside-link'])
def test_integrity_rejects_changed_payload(source, tmp_path, kind):
    output = tmp_path / 'snapshot'; package.build(source, output)
    root = output / 'igorek-desktop'; target = root / 'app/main.cjs'
    if kind == 'contents':
        target.write_text('tampered')
    elif kind == 'mode':
        target.chmod(0o777)
    elif kind == 'missing':
        target.unlink()
    else:
        target.unlink(); target.symlink_to(source / 'desktop/main.cjs')
    with pytest.raises(ValueError):
        verifier.verify(root)


@pytest.mark.parametrize('name', ['.env', 'passphrase.txt', 'history.db', 'trace.jsonl'])
def test_user_data_in_python_bundle_refuses_packaging(source, tmp_path, name):
    (source / 'desktop/runtime' / name).write_text('private')
    with pytest.raises(ValueError, match='User data'):
        package.build(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_external_symlinks_and_input_output_overlap_refuse_packaging(source, tmp_path):
    with pytest.raises(ValueError, match='Output'):
        package.build(source, source / 'desktop/runtime/output')
    (source / 'desktop/runtime/escape').symlink_to(source / 'handoff.md')
    with pytest.raises(ValueError, match='External symlink'):
        package.build(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_stale_source_overlay_refuses_publishing(source, tmp_path):
    path = source / 'desktop/runtime/manifest.json'; manifest = json.loads(path.read_text())
    manifest['prototype_source_overlay'] = {'packages/factory/demo.py': 'wrong-hash'}
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Stale source'):
        package.build(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


installer = load('desktop_installer', DESKTOP / 'packaging' / 'install-linux.py')


def test_install_update_rollback_preserves_data(source, tmp_path):
    package.build(source, tmp_path / 'first')
    first = tmp_path / 'first/igorek-desktop'
    (first / '.env').write_text('unlisted private data')
    prefix = tmp_path / 'installed'
    data = tmp_path / 'user-data'; data.mkdir(); (data / 'history.db').write_bytes(b'keep')
    initial = installer.install(first, prefix)
    assert initial['previous'] is None
    assert not (prefix / 'active/current/.env').exists()
    (source / 'desktop/main.cjs').write_text('next fixture')
    package.build(source, tmp_path / 'second')
    updated = installer.install(tmp_path / 'second/igorek-desktop', prefix)
    assert updated['current'] != initial['current']
    assert updated['previous'] == initial['current']
    assert installer.rollback(prefix) == {'current': initial['current'], 'previous': updated['current']}
    assert (data / 'history.db').read_bytes() == b'keep'
    assert installer.install(first, prefix)['current'] == initial['current']


def test_running_installation_refuses_switch(source, tmp_path):
    import fcntl
    package.build(source, tmp_path / 'first')
    root = tmp_path / 'first/igorek-desktop'; prefix = tmp_path / 'installed'
    initial = installer.install(root, prefix)
    with (prefix / 'run.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='running'):
            installer.install(root, prefix)
        with pytest.raises(RuntimeError, match='running'):
            installer.rollback(prefix)
    assert installer.state(prefix) == initial


def test_failed_activation_keeps_previous_generation(source, tmp_path, monkeypatch):
    package.build(source, tmp_path / 'first'); prefix = tmp_path / 'installed'
    initial = installer.install(tmp_path / 'first/igorek-desktop', prefix)
    (source / 'desktop/main.cjs').write_text('second')
    package.build(source, tmp_path / 'second')
    def fail(*args):
        raise OSError('activation failed')
    monkeypatch.setattr(installer.os, 'replace', fail)
    with pytest.raises(OSError, match='activation failed'):
        installer.install(tmp_path / 'second/igorek-desktop', prefix)
    assert installer.state(prefix) == initial


def test_installer_refuses_unknown_directory_and_links(source, tmp_path):
    package.build(source, tmp_path / 'first'); root = tmp_path / 'first/igorek-desktop'
    prefix = tmp_path / 'unknown'; prefix.mkdir(); (prefix / 'keep').write_text('keep')
    with pytest.raises(ValueError, match='empty'):
        installer.install(root, prefix)
    link = tmp_path / 'linked'; link.symlink_to(prefix)
    with pytest.raises(ValueError, match='symlink'):
        installer.install(root, link)
    assert installer.state(tmp_path / 'absent') == {'current': None, 'previous': None}
    assert not (tmp_path / 'absent').exists()


gui = load('desktop_install_gui', DESKTOP / 'packaging' / 'install-gui.py')


class FakeDialogs:
    def __init__(self, action='install', confirmed=True):
        self.action = action; self.confirmed = confirmed
        self.messages = []; self.errors = []; self.choices = []
        self.authenticated = False

    def choose(self, text, choices):
        self.choices = choices
        return self.action

    def confirm(self, text):
        return self.confirmed

    def progress(self, text):
        import contextlib
        return contextlib.nullcontext()

    def info(self, text):
        self.messages.append(text)

    def error(self, text):
        self.errors.append(text)

    def configure_sandbox(self, prefix):
        self.authenticated = True


def test_graphical_install_and_menu_entry(source, tmp_path, monkeypatch):
    import subprocess
    package.build(source, tmp_path / 'snapshot'); root = tmp_path / 'snapshot/igorek-desktop'
    prefix = tmp_path / 'app with space'; data_home = tmp_path / 'menu data'
    dialogs = FakeDialogs()
    confirmations = iter([True, False])
    dialogs.confirm = lambda text: next(confirmations)
    monkeypatch.setattr(gui, 'sandbox_required', lambda: True)
    monkeypatch.setattr(gui, 'sandbox_ready', lambda prefix: False)
    assert gui.run(root, prefix, data_home, dialogs) == 0
    entry = data_home / 'applications/igorek-desktop.desktop'
    content = entry.read_text()
    assert 'Exec="' + str(prefix / 'igorek') + '"' in content
    assert 'Name=Установка и откат' in content
    assert 'Categories=Development;\n' in content
    assert 'StartupWMClass=igorek-desktop\n' in content
    assert 'Настроить песочницу' in dialogs.messages[0]
    assert not dialogs.authenticated
    if shutil.which('desktop-file-validate'):
        subprocess.run(['desktop-file-validate', str(entry)], check=True)
    assert gui.register_menu(prefix, data_home) == entry
    entry.write_text('[Desktop Entry]\nName=Unrelated\n')
    with pytest.raises(ValueError, match='unrelated'):
        gui.register_menu(prefix, data_home)
    assert 'Unrelated' in entry.read_text()


@pytest.mark.parametrize('action,confirmed', [(None, True), ('install', False)])
def test_graphical_cancel_changes_nothing(source, tmp_path, action, confirmed):
    package.build(source, tmp_path / 'snapshot'); prefix = tmp_path / 'installed'; menu = tmp_path / 'menu'
    dialogs = FakeDialogs(action, confirmed)
    assert gui.run(tmp_path / 'snapshot/igorek-desktop', prefix, menu, dialogs) == 0
    assert not prefix.exists() and not menu.exists()
    assert not dialogs.messages and not dialogs.errors


def test_graphical_failure_has_no_success_or_menu(source, tmp_path):
    package.build(source, tmp_path / 'snapshot'); root = tmp_path / 'snapshot/igorek-desktop'
    (root / 'app/main.cjs').write_text('corrupt')
    dialogs = FakeDialogs(); prefix = tmp_path / 'installed'; menu = tmp_path / 'menu'
    assert gui.run(root, prefix, menu, dialogs) == 1
    assert dialogs.errors and not dialogs.messages
    assert not prefix.exists() and not menu.exists()


def test_graphical_rollback_keeps_management_for_older_snapshot(source, tmp_path):
    package.build(source, tmp_path / 'first'); first = tmp_path / 'first/igorek-desktop'
    (first / 'install-gui.sh').unlink()
    manifest_path = first / 'package-manifest.json'
    manifest = json.loads(manifest_path.read_text()); del manifest['files']['install-gui.sh']
    manifest_path.write_text(json.dumps(manifest))
    prefix = tmp_path / 'installed'; installer.install(first, prefix)
    (source / 'desktop/main.cjs').write_text('next')
    package.build(source, tmp_path / 'second'); second = tmp_path / 'second/igorek-desktop'
    installer.install(second, prefix)
    new_id = installer.state(prefix)['current']
    dialogs = FakeDialogs('rollback')
    assert gui.run(second, prefix, tmp_path / 'menu', dialogs) == 0
    content = (tmp_path / 'menu/applications/igorek-desktop.desktop').read_text()
    assert str(prefix / 'releases' / new_id / 'install-gui.sh') in content
    assert gui.installer.state(prefix)['current'] == manifest['build_id']


def test_sandbox_setup_requires_explicit_confirmation(source, tmp_path, monkeypatch):
    package.build(source, tmp_path / 'snapshot'); root = tmp_path / 'snapshot/igorek-desktop'
    prefix = tmp_path / 'installed'; installer.install(root, prefix)
    monkeypatch.setattr(gui, 'sandbox_required', lambda: True)
    monkeypatch.setattr(gui, 'sandbox_ready', lambda prefix: False)
    cancelled = FakeDialogs('sandbox', False)
    assert gui.run(root, prefix, tmp_path / 'menu', cancelled) == 0
    assert not cancelled.authenticated
    confirmed = FakeDialogs('sandbox', True)
    assert gui.run(root, prefix, tmp_path / 'menu', confirmed) == 0
    assert confirmed.authenticated


def test_menu_exec_escaping_and_refusal():
    assert gui.desktop_exec(Path('/tmp/$data`"\\app')) == '"/tmp/\\\\$data\\\\`\\\\"\\\\\\\\app"'
    for path in ['/tmp/a\nExec=bad', '/tmp/a=b', '/tmp/a%U']:
        with pytest.raises(ValueError):
            gui.desktop_exec(Path(path))


def test_gui_offers_ready_kvm_component_after_install(source, tmp_path, monkeypatch):
    package.build(source, tmp_path / 'snapshot')
    dialogs = FakeDialogs()
    actions = []
    dialogs.configure_libvirt = lambda prefix, rollback=False: actions.append(rollback)
    monkeypatch.setattr(gui.libvirt_fixer, 'compatible', lambda payload: 'ready')
    monkeypatch.setattr(gui, 'sandbox_required', lambda: False)
    assert gui.run(tmp_path / 'snapshot/igorek-desktop', tmp_path / 'app', tmp_path / 'menu', dialogs) == 0
    assert actions == [False]
    assert not dialogs.errors


@pytest.mark.parametrize('status,returncode', [('installed', 0), ('restored', 0), ('installed', 1), ('unknown', 0)])
def test_kvm_gui_requires_confirmed_install_result(tmp_path, monkeypatch, status, returncode):
    import subprocess
    commands = []
    def invoke(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, returncode, json.dumps({'status': status}), '')
    monkeypatch.setattr(gui.subprocess, 'run', invoke)
    dialogs = gui.Dialogs()
    if status == 'installed' and returncode == 0:
        dialogs.configure_libvirt(tmp_path)
    else:
        with pytest.raises(RuntimeError): dialogs.configure_libvirt(tmp_path)
    assert commands[0][:3] == ['pkexec', '/usr/bin/python3', '-I']
    assert commands[0][4] == 'install'


def test_package_refuses_unverified_kvm_payload(source, tmp_path):
    payload = tmp_path / 'payload'; payload.mkdir()
    (payload / 'manifest.json').write_text('{}')
    with pytest.raises(ValueError): package.build(source, tmp_path / 'snapshot', libvirt_fix=payload)
    assert not (tmp_path / 'snapshot').exists()


def test_cancelled_kvm_setup_is_reported_without_authentication(source, tmp_path, monkeypatch):
    package.build(source, tmp_path / 'snapshot')
    dialogs = FakeDialogs(); confirmations = iter([True, False])
    dialogs.confirm = lambda text: next(confirmations)
    dialogs.configure_libvirt = lambda *args, **kwargs: pytest.fail('cancelled setup requested authentication')
    monkeypatch.setattr(gui.libvirt_fixer, 'compatible', lambda payload: 'ready')
    monkeypatch.setattr(gui, 'sandbox_required', lambda: False)
    assert gui.run(tmp_path / 'snapshot/igorek-desktop', tmp_path / 'app', tmp_path / 'menu', dialogs) == 0
    assert 'Настройка KVM отложена' in dialogs.messages[-1]


@pytest.mark.parametrize('required,ready,answer,expected', [
    (True, False, True, True),
    (True, False, False, False),
    (True, True, True, False),
    (False, False, True, False),
])
def test_install_offers_required_sandbox_before_completion(source, tmp_path, monkeypatch, required, ready, answer, expected):
    package.build(source, tmp_path / 'snapshot')
    root = tmp_path / 'snapshot/igorek-desktop'
    dialogs = FakeDialogs()
    confirmations = []
    def confirm(text):
        confirmations.append(text)
        return True if len(confirmations) == 1 else answer
    dialogs.confirm = confirm
    configured = []
    def configure(prefix):
        assert not dialogs.messages
        configured.append(prefix)
    dialogs.configure_sandbox = configure
    monkeypatch.setattr(gui, 'sandbox_required', lambda: required)
    monkeypatch.setattr(gui, 'sandbox_ready', lambda prefix: ready or bool(configured))
    prefix = tmp_path / 'installed'
    assert gui.run(root, prefix, tmp_path / 'menu', dialogs) == 0
    assert bool(configured) is expected
    assert len(confirmations) == (2 if required and not ready else 1)
    assert (prefix / 'igorek').exists()
    assert bool(dialogs.messages)
    assert ('Настроить песочницу' in dialogs.messages[0]) is (required and not ready and not answer)


def test_install_keeps_application_when_sandbox_authentication_fails(source, tmp_path, monkeypatch):
    package.build(source, tmp_path / 'snapshot')
    root = tmp_path / 'snapshot/igorek-desktop'
    dialogs = FakeDialogs()
    monkeypatch.setattr(gui, 'sandbox_required', lambda: True)
    monkeypatch.setattr(gui, 'sandbox_ready', lambda prefix: False)
    def fail(prefix):
        raise RuntimeError('Настройка песочницы не завершена')
    dialogs.configure_sandbox = fail
    prefix = tmp_path / 'installed'
    assert gui.run(root, prefix, tmp_path / 'menu', dialogs) == 1
    assert gui.installer.state(prefix)['current']
    assert not dialogs.messages
    assert dialogs.errors


@pytest.mark.parametrize('delete_data', [False, True])
def test_uninstall_user_copy_with_data_choice(source, tmp_path, delete_data):
    package.build(source, tmp_path / 'snapshot')
    prefix, menu, config = tmp_path / 'installed', tmp_path / 'menu', tmp_path / 'config'
    installer.install(tmp_path / 'snapshot/igorek-desktop', prefix)
    gui.register_menu(prefix, menu)
    for name in ['igorek-desktop-runtime', 'igorek-desktop-owned']:
        directory = config / name
        directory.mkdir(parents=True)
        (directory / 'private').write_text('data')
    outside = tmp_path / 'workspace'; outside.mkdir(); (outside / 'keep').write_text('keep')
    result = installer.uninstall(prefix, menu, delete_data=delete_data, config_home=config)
    assert result == {'removed': True, 'data_deleted': delete_data}
    assert not prefix.exists() and not (menu / 'applications/igorek-desktop.desktop').exists()
    for name in ['igorek-desktop-runtime', 'igorek-desktop-owned']:
        assert (config / name / 'private').exists() is (not delete_data)
    assert (outside / 'keep').read_text() == 'keep'


@pytest.mark.parametrize('failure', ['busy', 'unknown', 'menu', 'menu-link', 'data-link'])
def test_uninstall_refuses_unsafe_or_running_copy(source, tmp_path, failure):
    import fcntl
    package.build(source, tmp_path / 'snapshot')
    prefix, menu, config = tmp_path / 'installed', tmp_path / 'menu', tmp_path / 'config'
    installer.install(tmp_path / 'snapshot/igorek-desktop', prefix)
    gui.register_menu(prefix, menu)
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'keep').write_text('keep')
    config.mkdir()
    if failure == 'unknown': (prefix / 'personal').write_text('keep')
    if failure == 'menu': (menu / 'applications/igorek-desktop.desktop').write_text('unrelated')
    if failure == 'data-link': (config / 'igorek-desktop-runtime').symlink_to(outside)
    if failure == 'menu-link':
        (menu / 'applications').rename(menu / 'original-applications')
        (menu / 'applications').symlink_to(menu / 'original-applications')
    with (prefix / 'run.lock').open('a') as lock:
        if failure == 'busy': fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with pytest.raises((ValueError, installer.InstallationBusyError)):
            installer.uninstall(prefix, menu, delete_data=True, config_home=config)
    assert (prefix / 'igorek').exists()
    assert (outside / 'keep').read_text() == 'keep'


@pytest.mark.parametrize('choice,confirm', [('keep', True), ('delete', True), (None, True), ('delete', False)])
def test_graphical_uninstall_choices(source, tmp_path, monkeypatch, choice, confirm):
    package.build(source, tmp_path / 'snapshot')
    root = tmp_path / 'snapshot/igorek-desktop'
    prefix, menu, config = tmp_path / 'installed', tmp_path / 'menu', tmp_path / 'config'
    gui.installer.install(root, prefix)
    gui.register_menu(prefix, menu)
    directory = config / 'igorek-desktop-runtime'; directory.mkdir(parents=True)
    (directory / 'private').write_text('data')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config))
    dialogs = FakeDialogs(confirmed=confirm)
    actions = iter(['uninstall', choice])
    dialogs.choose = lambda text, choices: next(actions)
    assert gui.run(root, prefix, menu, dialogs) == 0
    removed = choice is not None and confirm
    assert prefix.exists() is (not removed)
    assert (directory / 'private').exists() is (not (removed and choice == 'delete'))
    assert not dialogs.errors
    assert bool(dialogs.messages) is removed


def test_snapshot_refuses_release_notes_from_a_different_version(source,tmp_path):
    (source/'desktop/assets/release-notes.json').write_text(json.dumps({'version':'0.1.818','changes':['Old release']}))
    with pytest.raises(ValueError,match='Release notes'):
        package.build(source,tmp_path/'snapshot')


@pytest.mark.parametrize('legacy', ['directory', 'manifest'])
def test_legacy_codex_executable_cannot_return_via_cached_runtime(source, tmp_path, legacy):
    runtime = source / 'desktop/runtime'
    if legacy == 'directory':
        (runtime / 'codex-quota').mkdir()
        (runtime / 'codex-quota/codex').write_bytes(b'synthetic legacy executable')
    else:
        manifest = json.loads((runtime / 'manifest.json').read_text())
        manifest['codex_quota'] = {'binary': 'codex-quota/codex'}
        (runtime / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Legacy bundled Codex'):
        package.build(source, tmp_path / 'rejected-legacy-counter')
    assert not (tmp_path / 'rejected-legacy-counter').exists()
