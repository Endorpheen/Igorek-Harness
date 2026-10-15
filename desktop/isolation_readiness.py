"""Read-only checks for desktop execution readiness."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from factory.core.isolation import config
from factory.core.isolation.base_image import ManifestError, load_manifest, manifest_path_for, resolve_tool_version
from factory.core.readiness import check_host_readiness


def probe_capabilities():
    code = '''import importlib.util,json,sys
from dataclasses import asdict
sys.path.insert(0,sys.argv[1])
sys.path.insert(0,sys.argv[2])
from socket_bootstrap import activate
activate(sys.argv[2])
import socket
from factory.core.isolation.config import check_host_capabilities
result=asdict(check_host_capabilities('qemu:///system'))
result['vsock_present']=hasattr(socket,'AF_VSOCK')
result['bindings_present']=importlib.util.find_spec('libvirt') is not None
try:
 import libvirt
 result['bindings_loadable']=True
except (ImportError,OSError):
 result['bindings_loadable']=False
print(json.dumps(result))
'''
    packages = str(Path(config.__file__).resolve().parents[3])
    try:
        result = subprocess.run([sys.executable, '-I', '-c', code, packages, str(Path(__file__).resolve().parent)],
                                capture_output=True, text=True, timeout=4, check=True)
        payload = json.loads(result.stdout)
        fields = ['kvm_present', 'libvirt_reachable', 'virtiofsd_present', 'qemu_img_present', 'bindings_present', 'bindings_loadable']
        if not isinstance(payload, dict) or any(type(payload.get(key)) is not bool for key in fields):
            raise ValueError('Invalid capability report')
        return payload
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def desktop_readiness(root: Path, *, capability_probe=probe_capabilities, tool_version=None, execution_mode=None):
    root = Path(root).absolute()
    if sys.platform == 'win32':
        return {'status': 'needs_action', 'checks': [
            {'id': 'desktop_execution', 'status': 'needs_action', 'message':
             'Windows preview: защищённый запуск команд и KVM пока недоступны.',
             'action': 'Используйте чат и файловые инструменты; Linux остаётся основной платформой.',
             'facts': {'platform': 'win32', 'command_sandbox': False, 'kvm': False}}]}
    report = check_host_readiness(str(root))
    checks = report['checks']
    checks[:] = [item for item in checks if item['id'] != 'integration']

    def add(check_id, status, message, action=None, **facts):
        checks.append(dict(id=check_id, status=status, message=message, action=action, facts=facts))

    capabilities = capability_probe()
    if capabilities is None:
        add('kvm_management', 'unknown', 'Проверка управления виртуальными машинами не завершилась.',
            'Повторить проверку; работа агента и запуск ВМ не выполнялись.')
    else:
        ready = capabilities['libvirt_reachable']
        message = ('Управление виртуальными машинами доступно из приложения.' if ready else
                   'В комплекте приложения отсутствует компонент управления виртуальными машинами.'
                   if not capabilities['bindings_present'] else
                   'Компонент управления найден, но не может быть загружен.'
                   if not capabilities.get('bindings_loadable', True) else 'Приложение не смогло подключиться к управлению виртуальными машинами.')
        add('kvm_management', 'ready' if ready else 'needs_action', message,
            None if ready else 'Проверить системную библиотеку libvirt, совместимость комплекта и доступ к системному подключению KVM.',
            libvirt_uri='qemu:///system', bindings_present=capabilities['bindings_present'], bindings_loadable=capabilities.get('bindings_loadable'))
        vsock = capabilities.get('vsock_present')
        add('kvm_channel', 'ready' if vsock is True else 'needs_action' if vsock is False else 'unknown',
            'Комплектный Python поддерживает канал связи с гостем.' if vsock is True else
            'В комплектном Python отсутствует поддержка канала связи с гостем.' if vsock is False else
            'Поддержка канала связи с гостем ещё не проверена.',
            None if vsock is True else 'Подготовить комплект Python с поддержкой AF_VSOCK; занятость порта проверяется при запуске исполнителя.',
            vsock_present=vsock)
        add('virtiofsd', 'ready' if capabilities['virtiofsd_present'] else 'needs_action',
            'Компонент передачи выбранной рабочей папки в виртуальную машину.',
            None if capabilities['virtiofsd_present'] else 'Подготовить virtiofsd для Linux-поставки.')

    image = root / 'isolation/images/base.qcow2'
    manifest_file = manifest_path_for(image)
    expected_version = tool_version if tool_version is not None else resolve_tool_version()
    if not image.resolve().is_relative_to(root.resolve()) or not manifest_file.resolve().is_relative_to(root.resolve()):
        add('kvm_image', 'needs_action', 'Путь образа выходит за каталог данных приложения.',
            'Подготовить отдельный образ desktop внутри его каталога данных.', path=str(image))
    elif not image.is_file():
        add('kvm_image', 'needs_action', 'Отдельный образ виртуальной машины для desktop ещё не подготовлен.',
            'На следующем этапе подготовить образ; рабочий образ основной установки не используется.', path=str(image))
    else:
        try:
            manifest = load_manifest(manifest_file)
            if manifest.tool_version != expected_version:
                add('kvm_image', 'needs_action', 'Версия образа не совпадает с версией приложения.',
                    'Подготовить совместимый образ перед включением KVM.', path=str(image),
                    version=manifest.tool_version, expected_version=expected_version)
            elif image.stat().st_size != manifest.size_bytes:
                add('kvm_image', 'needs_action', 'Размер образа не совпадает с его паспортом.',
                    'Повторно подготовить и проверить образ.', path=str(image))
            else:
                add('kvm_image', 'unknown', 'Паспорт образа совместим; полная проверка целостности ещё не выполнена.',
                    'Проверить контрольную сумму перед включением KVM. Быстрая диагностика не читает весь образ.',
                    path=str(image), version=manifest.tool_version, size_bytes=manifest.size_bytes)
        except (ManifestError, OSError):
            add('kvm_image', 'needs_action', 'Паспорт образа отсутствует или повреждён.',
                'Подготовить образ вместе с его паспортом и контрольной суммой.', path=str(image))
    mode = execution_mode if execution_mode is not None else os.environ.get(config.ENV_MODE, config.MODE_OFF)
    add('desktop_execution', 'unknown',
        'Выбран KVM: выполнение подключено к штатному менеджеру; запуск гостя ещё не подтверждён.' if mode == 'kvm' else
        'Выбран запуск на этом компьютере без KVM.',
        'Проверить реальное выполнение задачи в госте; выбор режима и доступность компонентов сами по себе не подтверждают изоляцию.',
        execution_mode=mode)
    add('integration', 'unknown', 'Настоящий провайдер, микрофон и полная desktop-приёмка ещё не проверены.',
        'Проверить на следующих этапах; эта диагностика не вызывает модель и не включает микрофон.')
    report['status'] = 'needs_action' if any(item['status'] == 'needs_action' for item in checks) else 'unknown'
    return report
