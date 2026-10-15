"""Non-mutating host probes for the first-run wizard."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path


def check_host_readiness(data_dir: str | None = None) -> dict:
    root = Path(data_dir or '~/.igorek-agent-factory').expanduser().absolute()
    checks = []

    def add(check_id, status, message, action=None, **facts):
        checks.append(dict(id=check_id, status=status, message=message, action=action, facts=facts))

    try:
        release = platform.freedesktop_os_release()
        target = release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '26.04'
        add('os', 'ready' if target else 'needs_action',
            'ОС соответствует Linux-пилоту.' if target else 'ОС вне текущего Linux-пилота.',
            None if target else 'Проверить совместимость отдельно; пилот — Ubuntu 26.04.',
            name=release.get('PRETTY_NAME'), version=release.get('VERSION_ID'))
    except OSError:
        add('os', 'unknown', 'Не удалось определить ОС.', 'Проверить сведения об ОС.')
    arch = platform.machine()
    add('architecture', 'ready' if arch in ('x86_64', 'AMD64') else 'needs_action',
        'Архитектура компьютера.', None if arch in ('x86_64', 'AMD64') else 'Проверить поддержку архитектуры.', value=arch)
    supported = sys.version_info >= (3, 13)
    add('python', 'ready' if supported else 'needs_action', 'Python текущего процесса.',
        None if supported else 'Использовать Python 3.13 или новее.', version=platform.python_version())
    ancestor = root
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    writable = ancestor.is_dir() and os.access(ancestor, os.W_OK | os.X_OK)
    add('data_directory', 'ready' if writable else 'needs_action', 'Доступ к каталогу данных.',
        None if writable else 'Выбрать доступный каталог данных.', path=str(root), exists=root.exists(), checked_parent=str(ancestor))
    try:
        add('disk', 'unknown', 'Свободное место измерено; минимальный объём ещё не подтверждён.',
            'Определить требования после сборки десктопа и KVM-образа.', free_bytes=shutil.disk_usage(ancestor).free)
    except OSError:
        add('disk', 'unknown', 'Не удалось измерить свободное место.', 'Проверить доступ к файловой системе.')
    try:
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        add('memory', 'unknown', 'Память измерена; минимальный объём ещё не подтверждён.',
            'Измерить потребление во время пилота.', available_bytes=int(memory['MemAvailable'].split()[0]) * 1024,
            total_bytes=int(memory['MemTotal'].split()[0]) * 1024)
    except (OSError, KeyError, ValueError):
        add('memory', 'unknown', 'Не удалось измерить память.', 'Проверить сведения о памяти.')
    kvm = Path('/dev/kvm')
    accessible = kvm.exists() and os.access(kvm, os.R_OK | os.W_OK)
    add('kvm_access', 'ready' if accessible else 'needs_action', 'Доступ текущего пользователя к /dev/kvm.',
        None if accessible else 'Проверить аппаратную виртуализацию и права группы kvm.', exists=kvm.exists())
    for binary in ('qemu-system-x86_64', 'qemu-img', 'virsh'):
        available = shutil.which(binary) is not None
        add(binary, 'ready' if available else 'needs_action', f'Наличие {binary}.',
            None if available else f'Установить компонент {binary}.')
    add('integration', 'unknown', 'Образ KVM, провайдер, микрофон и десктопная оболочка ещё не проверены.',
        'Проверить на следующих шагах онбординга; агент и ВМ сейчас не запускаются.')
    status = 'needs_action' if any(c['status'] == 'needs_action' for c in checks) else 'unknown'
    return dict(schema_version=1, mode='read_only', target='Ubuntu 26.04 x86_64', status=status, checks=checks)


def format_readiness(report: dict) -> str:
    labels = {'ready': 'Готово', 'needs_action': 'Нужно действие', 'unknown': 'Не проверено полностью'}
    lines = ['Проверка компьютера без изменений — Ubuntu 26.04']
    for check in report['checks']:
        lines.append(f"[{labels[check['status']]}] {check['message']}")
        if check['facts']:
            lines.append('  ' + ', '.join(f'{k}: {v}' for k, v in check['facts'].items()))
        if check['action']:
            lines.append('  Далее: ' + check['action'])
    return '\n'.join(lines)
