"""System dialogs for installing a local Linux snapshot and its menu entry."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location('linux_installer', Path(__file__).with_name('install-linux.py'))
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)
_spec = importlib.util.spec_from_file_location('libvirt_fixer', Path(__file__).with_name('install-libvirt-helper.py'))
libvirt_fixer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(libvirt_fixer)


def desktop_string(value: str):
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def desktop_exec(path: Path):
    value = str(path.absolute())
    if '=' in value or '%' in value or any(ord(char) < 32 for char in value):
        raise ValueError('Unsupported character in launcher path')
    quoted = ''.join('\\' + char if char in '\\"`$' else char for char in value)
    return desktop_string('"' + quoted + '"')


def menu_content(prefix: Path, data_home: Path, management_root: Path):
    return '\n'.join([
        '[Desktop Entry]', 'Type=Application', 'Name=Игорёк',
        'Comment=Агентная рабочая среда', 'Terminal=false', 'Categories=Development;',
        'Exec=' + desktop_exec(prefix / 'igorek'),
        'Icon=' + desktop_string(str(prefix / 'active/current/igorek.svg')),
        'StartupWMClass=igorek-desktop',
        'X-Igorek-Installer=1', 'X-Igorek-InstallRoot=' + desktop_string(str(prefix)),
        'Actions=Manage;', '', '[Desktop Action Manage]', 'Name=Установка и откат',
        'Exec=' + desktop_exec(management_root / 'install-gui.sh') + ' --prefix ' + desktop_exec(prefix)
        + ' --data-home ' + desktop_exec(data_home), '',
    ])


def register_menu(prefix: Path, data_home: Path):
    prefix = prefix.absolute(); data_home = data_home.absolute()
    installed = installer.state(prefix)
    if not installed['current']:
        raise ValueError('No installed snapshot')
    candidates = [prefix / 'releases' / build_id for build_id in [installed['current'], installed['previous']] if build_id]
    management_root = next((root for root in candidates if (root / 'install-gui.sh').is_file()), None)
    if management_root is None:
        raise ValueError('Installed snapshots have no graphical installer')
    content = menu_content(prefix, data_home, management_root)
    applications = data_home / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    target = applications / 'igorek-desktop.desktop'
    if target.is_symlink():
        raise ValueError('Menu entry must not be a symlink')
    if target.exists():
        previous = target.read_text()
        expected_root = 'X-Igorek-InstallRoot=' + desktop_string(str(prefix))
        if 'X-Igorek-Installer=1' not in previous.splitlines() or expected_root not in previous.splitlines():
            raise ValueError('An unrelated menu entry already exists; it will not be overwritten')
    with tempfile.NamedTemporaryFile(mode='w', dir=applications, prefix='.igorek-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.chmod(0o644)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def sandbox_required():
    setting = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
    return setting.is_file() and setting.read_text().strip() == '1'


def sandbox_ready(prefix: Path):
    policy = Path('/etc/apparmor.d/igorek-desktop-installed')
    return policy.is_file() and '"' + str(prefix.resolve()) + '/releases/*/electron/electron"' in policy.read_text()


class Dialogs:
    def __init__(self):
        self.finished = False

    def call(self, *args):
        result = subprocess.run(['zenity', '--title=Установка Игорька', '--no-markup', *args], capture_output=True, text=True)
        if result.returncode not in (0, 1):
            raise RuntimeError('Не удалось открыть системное окно установки.')
        return result

    def choose(self, text, choices):
        rows = []
        for index, (key, label) in enumerate(choices):
            rows.extend(['TRUE' if index == 0 else 'FALSE', key, label])
        result = self.call('--list', '--radiolist', '--width=640', '--height=380', '--text=' + text,
                           '--column=', '--column=Код', '--column=Действие', '--hide-column=2', '--print-column=2',
                           '--ok-label=Выбрать', '--cancel-label=Закрыть', *rows)
        self.finished = result.returncode != 0
        return result.stdout.strip() if result.returncode == 0 else None

    def confirm(self, text):
        return self.call('--question', '--width=620', '--text=' + text, '--ok-label=Продолжить', '--cancel-label=Отмена').returncode == 0

    def info(self, text):
        self.call('--info', '--width=620', '--text=' + text, '--ok-label=Готово')

    def error(self, text):
        self.call('--error', '--width=620', '--text=' + text, '--ok-label=Закрыть')

    @contextlib.contextmanager
    def progress(self, text):
        process = subprocess.Popen(['zenity', '--progress', '--title=Установка Игорька', '--no-markup',
                                    '--text=' + text, '--pulsate', '--no-cancel', '--auto-close', '--width=540'],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        try:
            yield
        finally:
            try:
                process.communicate('100\n', timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate(); process.wait(timeout=5)

    def configure_sandbox(self, prefix):
        helper = prefix / 'active/current/install-sandbox-policy.sh'
        result = subprocess.run(['pkexec', str(helper), '--installation-root', str(prefix)], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError('Настройка песочницы не завершена. Установка сохранена; её можно повторить из этого окна.')
        if not sandbox_ready(prefix):
            raise RuntimeError('Не удалось подтвердить установленный профиль песочницы.')

    def configure_libvirt(self, prefix, *, rollback=False):
        root = prefix / 'active/current'
        args = ['pkexec', '/usr/bin/python3', '-I', str(root / 'install-libvirt-helper.py'),
                'rollback' if rollback else 'install']
        if not rollback: args += ['--payload', str(root / 'libvirt-fix')]
        result = subprocess.run(args, capture_output=True, text=True)
        expected = ('restored', 'already-restored') if rollback else ('installed', 'already-installed')
        try:
            reply = json.loads(result.stdout)
            confirmed = result.returncode == 0 and isinstance(reply, dict) and reply.get('status') in expected
        except (ValueError, TypeError):
            confirmed = False
        if not confirmed:
            raise RuntimeError('Настройка KVM не завершена. Приложение сохранено; действие можно повторить из установщика.')


def run(snapshot: Path, prefix: Path, data_home: Path, dialogs):
    prefix = prefix.absolute(); data_home = data_home.absolute()
    try:
        manifest = installer._verifier.verify(snapshot)
        current = installer.state(prefix)
        snapshot_fix = libvirt_fixer.compatible(snapshot / 'libvirt-fix')
        system_fix = (libvirt_fixer.compatible(prefix / 'active/current/libvirt-fix')
                      if (prefix / 'active/current/install-libvirt-helper.py').is_file() else 'missing')
        choices = [('install', 'Обновить приложение' if current['current'] else 'Установить приложение')]
        if current['previous']:
            choices.append(('rollback', 'Вернуться к предыдущей сборке'))
        if current['current']:
            choices.append(('uninstall', 'Удалить приложение'))
            choices.append(('menu', 'Добавить или восстановить ярлык в меню приложений'))
            if sandbox_required() and not sandbox_ready(prefix):
                choices.append(('sandbox', 'Настроить песочницу Electron'))
            if system_fix == 'ready': choices.append(('libvirt', 'Настроить изоляцию KVM'))
            if system_fix == 'installed': choices.append(('libvirt-rollback', 'Отменить системное исправление KVM'))
        text = (f"Игорёк {manifest['factory_version']} · сборка {manifest['build_id']}\n\n"
                f"Приложение: {prefix}\n"
                'Ключи и история остаются в отдельном каталоге данных.\n'
                'Перед обновлением или откатом закройте Игорька.')
        action = dialogs.choose(text, choices)
        if action is None:
            return 0
        if action not in {key for key, label in choices}:
            raise ValueError('Unknown installation action')
        if action == 'uninstall':
            choice = dialogs.choose('Удаление пользовательской копии Игорька. Выберите, что сделать с данными.', [
                ('keep', 'Сохранить настройки, ключи и историю'),
                ('delete', 'Удалить настройки, ключи, историю и образ desktop'),
            ])
            if choice is None:
                return 0
            if choice not in {'keep', 'delete'}:
                raise ValueError('Unknown removal action')
            config_home = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config').absolute()
            detail = ('Данные останутся для повторной установки.' if choice == 'keep' else
                      'Будут безвозвратно удалены каталоги:\n' + str(config_home / 'igorek-desktop-runtime')
                      + '\n' + str(config_home / 'igorek-desktop-owned'))
            if not dialogs.confirm('Удалить приложение из ' + str(prefix) + '?\n' + detail
                                   + '\nВыбранные внешние рабочие папки и данные по нестандартному пути сохраняются.'):
                return 0
            with dialogs.progress('Удаляю пользовательскую копию приложения.'):
                installer.uninstall(prefix, data_home, delete_data=choice == 'delete', config_home=config_home)
            dialogs.info('Приложение удалено. ' + ('Данные сохранены.' if choice == 'keep' else 'Данные desktop удалены.')
                         + '\nСистемный пакет установщика и общие компоненты Ubuntu остаются установленными.')
            dialogs.finished = True
            return 0
        if action in {'install', 'rollback'}:
            prompt = ('Установить эту сборку и добавить ярлык в меню приложений?' if action == 'install'
                      else 'Вернуться к предыдущей сборке? Ключи и история останутся прежними.')
            if not dialogs.confirm(prompt):
                return 0
            with dialogs.progress('Проверяю и копирую файлы. Дождитесь завершения.'):
                if action == 'install':
                    installer.install(snapshot, prefix)
                else:
                    installer.rollback(prefix)
            register_menu(prefix, data_home)
        elif action == 'menu':
            register_menu(prefix, data_home)
        elif action == 'sandbox':
            if not dialogs.confirm('Ubuntu требует однократную настройку песочницы Electron.\n'
                                   'Будет установлен профиль только для этой папки приложения.\n'
                                   'Система запросит пароль администратора. Продолжить?'):
                return 0
            with installer.installation_lock(prefix):
                dialogs.configure_sandbox(prefix)
        if action in {'install', 'rollback'} and sandbox_required() and not sandbox_ready(prefix):
            if dialogs.confirm('Для первого запуска Ubuntu требует настройку песочницы Electron.\n'
                               'Настроить её сейчас? Система запросит пароль администратора.\n'
                               'При отмене приложение останется установленным; настройку можно повторить позже.'):
                with installer.installation_lock(prefix):
                    dialogs.configure_sandbox(prefix)
        kvm_deferred = False
        if action in ('libvirt', 'libvirt-rollback') or (action == 'install' and snapshot_fix == 'ready'):
            rollback_fix = action == 'libvirt-rollback'
            prompt = ('Вернуть исходный системный компонент KVM?' if rollback_fix else
                      'Настроить изоляцию KVM готовым компонентом этой поставки?\n'
                      'Исходный системный файл будет сохранён для отката.')
            if dialogs.confirm(prompt + '\nUbuntu запросит пароль администратора. Службы не перезапускаются.'):
                with installer.installation_lock(prefix):
                    dialogs.configure_libvirt(prefix, rollback=rollback_fix)
            elif action != 'install':
                return 0
            else:
                kvm_deferred = True
        if action in {'install', 'rollback', 'menu'}:
            message = 'Готово. Игорёк добавлен в меню приложений. Ключи и история сохранены.'
            if kvm_deferred:
                message += '\n\nНастройка KVM отложена. Её можно выполнить кнопкой «Настроить изоляцию KVM» в этом установщике.'
            if sandbox_required() and not sandbox_ready(prefix):
                message += '\n\nПеред первым запуском выберите «Настроить песочницу Electron» в этом установщике.'
            dialogs.info(message)
        elif action == 'sandbox':
            dialogs.info('Песочница настроена. Можно открыть Игорька из меню приложений.')
        else:
            dialogs.info('Системная настройка KVM завершена.' if action == 'libvirt' else 'Исходный системный компонент KVM восстановлен.')
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        message = str(error)
        if isinstance(error, installer.InstallationBusyError):
            message = 'Игорёк или другая установка сейчас открыты. Закройте приложение и повторите действие.'
        dialogs.error('Действие не завершено.\n' + message)
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefix', type=Path, default=Path.home() / '.local/share/igorek-desktop')
    parser.add_argument('--data-home', type=Path, default=Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share'))
    parser.add_argument("--gtk", action="store_true")
    args = parser.parse_args()
    if args.gtk:
        spec = importlib.util.spec_from_file_location('installer_gtk', Path(__file__).with_name('installer-gtk.py'))
        ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(ui)
        raise SystemExit(ui.start(lambda dialogs: run(Path(__file__).resolve().parent, args.prefix, args.data_home, dialogs), Dialogs))
    if not shutil.which('zenity'):
        parser.exit(1, 'Для графического установщика нужен Zenity. На Ubuntu: sudo apt install zenity.\n')
    dialogs = Dialogs()
    while not dialogs.finished:
        result = run(Path(__file__).resolve().parent, args.prefix, args.data_home, dialogs)
        if result:
            raise SystemExit(result)
