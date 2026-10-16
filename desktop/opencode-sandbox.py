"""Private stream bridges; OpenCode is only executed inside bubblewrap/the guest."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import tempfile


def pump(left, right):
    try:
        sockets = [left, right]
        while sockets:
            for source in select.select(sockets, [], [], 1)[0]:
                data = source.recv(65536)
                target = right if source is left else left
                if not data:
                    sockets.remove(source)
                    target.shutdown(socket.SHUT_WR)
                else:
                    target.sendall(data)
    except OSError:
        pass
    finally:
        left.close()
        right.close()


def bridge(listener, connect):
    def accept():
        while True:
            try:
                incoming, _ = listener.accept()
            except OSError:
                return
            def handle(incoming):
                try:
                    outgoing = connect()
                except OSError:
                    incoming.close()
                    return
                pump(incoming, outgoing)
            threading.Thread(target=handle, args=(incoming,), daemon=True).start()
    listener.listen(64)
    threading.Thread(target=accept, daemon=True).start()


def unix_connect(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)
    return sock


def validate_vm(xml, projects):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    if root.tag != 'domain' or root.get('type') != 'kvm':
        raise ValueError('opencode_kvm_required')
    devices = root.find('devices')
    if devices is None or devices.find('hostdev') is not None or devices.find('shmem') is not None:
        raise ValueError('opencode_kvm_host_device_denied')
    for node in devices:
        if node.tag in {'channel', 'redirdev', 'redirfilter', 'smartcard'}:
            raise ValueError('opencode_kvm_host_channel_denied')
        if node.tag in {'serial', 'console', 'parallel'} and node.get('type') != 'pty':
            raise ValueError('opencode_kvm_host_channel_denied')
        if node.tag == 'disk' and node.get('type') not in {'file'}:
            raise ValueError('opencode_kvm_host_disk_denied')
    if any(node.tag.startswith('{') for node in root.iter()):
        raise ValueError('opencode_kvm_extension_denied')
    approved = {str(Path(item).resolve()) for item in projects}
    for share in devices.findall('filesystem'):
        source = share.find('source')
        if source is None or source.get('dir') not in approved or share.get('type') != 'mount':
            raise ValueError('opencode_kvm_share_denied')
    for interface in devices.findall('interface'):
        if interface.get('type') != 'user' or interface.find('portForward') is not None:
            raise ValueError('opencode_kvm_network_denied')
    if devices.find('vsock') is None:
        raise ValueError('opencode_kvm_vsock_missing')
    return int(devices.find('vsock/cid').get('address'))


def profile_providers(grant, port, legacy=False):
    result = {}
    for profile in grant['profiles']:
        api = profile['api_format']
        npm = {'openai': '@ai-sdk/openai-compatible', 'anthropic': '@ai-sdk/anthropic',
               'openai-responses': '@ai-sdk/openai'}[api]
        url = f"http://127.0.0.1:{port}/profiles/{profile['id']}/v1"
        limit = {'context': profile.get('context') or 32768, 'output': 16384}
        if legacy:
            value = {'name': 'Игорёк · ' + profile['name'], 'npm': npm,
                     'options': {'baseURL': url, 'apiKey': grant['token']},
                     'models': {profile['model']: {'name': profile['model'], 'tool_call': True,
                                                  'modalities': {'input': ['text'], 'output': ['text']}, 'limit': limit}}}
        else:
            package = {'openai': '@opencode/ai/providers/openai-compatible',
                       'anthropic': '@opencode/ai/providers/anthropic',
                       'openai-responses': '@opencode/ai/providers/openai/responses'}[api]
            value = {'name': 'Игорёк · ' + profile['name'], 'package': package,
                     'settings': {'apiKey': grant['token'], 'baseURL': url,
                                  'provider': 'igorek-' + profile['name'],
                                  'timeout': 2400000, 'headerTimeout': 2400000, 'chunkTimeout': 600000},
                     'headers': {'X-Igorek-Profile-Grant': grant['token']},
                     'models': {profile['model']: {'name': profile['model'],
                         'capabilities': {'tools': True, 'input': ['text'], 'output': ['text']}, 'limit': limit}}}
        result['igorek-' + profile['name']] = value
    return result


def parse_jsonc(text):
    output, position, quoted = [], 0, False
    while position < len(text):
        char = text[position]
        if quoted:
            output.append(char)
            if char == chr(92) and position + 1 < len(text):
                position += 1
                output.append(text[position])
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            output.append(char)
        elif text.startswith('//', position):
            end = text.find(chr(10), position)
            position = len(text) if end < 0 else end
            continue
        elif text.startswith('/*', position):
            end = text.find('*/', position + 2)
            if end < 0:
                raise ValueError('invalid_opencode_jsonc')
            output.append(' ')
            position = end + 2
            continue
        else:
            output.append(char)
        position += 1
    text = ''.join(output)
    output, position, quoted = [], 0, False
    while position < len(text):
        char = text[position]
        if quoted:
            output.append(char)
            if char == chr(92) and position + 1 < len(text):
                position += 1
                output.append(text[position])
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            output.append(char)
        elif char == ',':
            look = position + 1
            while look < len(text) and text[look].isspace():
                look += 1
            if look >= len(text) or text[look] not in '}]':
                output.append(char)
        else:
            output.append(char)
        position += 1
    return json.loads(''.join(output))


def profile_config_directory(grant, port, original, runtime, extra):
    original, runtime = Path(original), Path(runtime)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    if original.is_dir():
        for source in original.iterdir():
            if source.name != 'opencode.jsonc':
                (runtime / source.name).symlink_to(source.absolute(), target_is_directory=source.is_dir())
    source = original / 'opencode.jsonc'
    try:
        current = parse_jsonc(source.read_text()) if source.is_file() else {}
    except (OSError, ValueError):
        current = {}
    legacy = bool(set(current) & {'logLevel', 'server', 'command', 'reference', 'snapshot', 'plugin', 'autoshare',
                                        'disabled_providers', 'enabled_providers', 'small_model', 'mode',
                                        'agent', 'provider', 'permission', 'tools', 'attachment', 'layout'})
    key = 'provider' if legacy else 'providers'
    current.setdefault(key, {}).update(profile_providers(grant, port, legacy))
    if extra.get('mcp') and not legacy:
        current.setdefault('mcp', {}).setdefault('servers', {}).update(extra['mcp'].get('servers', {}))
    target = runtime / 'opencode.jsonc'
    target.write_text(json.dumps(current, ensure_ascii=False))
    target.chmod(0o600)
    return str(runtime)


def main():
    global socket
    config = json.loads(sys.stdin.readline(65537))
    mode = config['mode']
    if mode == 'kvm':
        bundle = Path(config['bundle'])
        sys.path[:0] = [str(bundle), str(bundle / 'packages')]
        from socket_bootstrap import activate
        activate(bundle)
    import socket
    root = Path(config['bridge'])
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(root / 'api.sock'))
    os.chmod(root / 'api.sock', 0o600)
    child = None
    if mode == 'kvm':
        import libvirt
        connection = libvirt.open('qemu:///system')
        domain = connection.lookupByUUIDString(config['vm'])
        if not domain.isActive():
            raise ValueError('opencode_kvm_stopped')
        cid = validate_vm(domain.XMLDesc(0), config['projects'])
        def connect():
            if not domain.isActive() or validate_vm(domain.XMLDesc(0), config['projects']) != cid:
                raise OSError('opencode_kvm_changed')
            sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((cid, 40521))
            sock.settimeout(None)
            return sock
    else:
        proxy = socket.socket()
        proxy.bind(('127.0.0.1', 0))
        bridge(proxy, lambda: unix_connect(str(root / 'egress.sock')))
        env = dict(os.environ)
        env['OPENCODE_SERVER_PASSWORD'] = config['password']
        env['HTTP_PROXY'] = env['HTTPS_PROXY'] = env['http_proxy'] = env['https_proxy'] = 'http://127.0.0.1:' + str(proxy.getsockname()[1])
        env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
        profiles = config.get('profiles')
        if profiles:
            provider = socket.socket()
            provider.bind(('127.0.0.1', 0))
            bridge(provider, lambda: unix_connect('/run/igorek-profiles.sock'))
            current = json.loads(env.get('OPENCODE_CONFIG_CONTENT', '{}'))
            directory = tempfile.mkdtemp(prefix='igorek-opencode-config-')
            env['OPENCODE_CONFIG_DIR'] = profile_config_directory(profiles, provider.getsockname()[1],
                Path(env['XDG_CONFIG_HOME']) / 'opencode', directory, current)
        child = subprocess.Popen(['/opt/opencode/opencode', 'serve', '--hostname', '127.0.0.1', '--port', '4096'],
                                 env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env.clear()
        connect = lambda: socket.create_connection(('127.0.0.1', 4096), timeout=10)
    bridge(server, connect)
    print('{"ready":true}', flush=True)
    def stop(*_):
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        server.close()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def parent():
        for _line in sys.stdin:
            pass
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=parent, daemon=True).start()
    while True:
        if child is not None:
            code = child.wait()
            raise SystemExit(code or 1)
        signal.pause()


if __name__ == '__main__':
    main()
