"""Launch the native Windows OpenCode service under the Windows process sandbox."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tarfile
import threading
import time
import urllib.request


def extract(archive, output):
    output = Path(output)
    output.mkdir(mode=0o700)
    with tarfile.open(archive, 'r:gz') as package:
        member = package.getmember('package/bin/opencode.exe')
        if not member.isfile() or not 1 <= member.size <= 300 * 1024 * 1024:
            raise ValueError('invalid_opencode_windows_archive')
        with package.extractfile(member) as source, (output / 'opencode.exe').open('xb') as target:
            shutil.copyfileobj(source, target)


def listening_port(pid):
    library = ctypes.WinDLL('iphlpapi', use_last_error=True)
    query = library.GetExtendedTcpTable
    query.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.DWORD), W.BOOL, W.ULONG, ctypes.c_int, W.ULONG]
    query.restype = W.DWORD
    size = W.DWORD()
    query(None, ctypes.byref(size), False, 2, 5, 0)
    storage = ctypes.create_string_buffer(size.value)
    if query(storage, ctypes.byref(size), False, 2, 5, 0):
        return None
    count = W.DWORD.from_buffer(storage).value
    for index in range(count):
        values = (W.DWORD * 6).from_buffer(storage, 4 + index * 24)
        if values[0] == 2 and values[5] == pid and values[1] == 0x0100007F:
            return socket.ntohs(values[2] & 0xFFFF)
    return None


def main(config):
    if os.name != 'nt':
        raise OSError('opencode_windows_requires_windows')
    bundle = Path(config['bundle']).resolve(strict=True)
    sys.path[:0] = [str(bundle), str(bundle / 'packages')]
    from factory.core.platform_support.windows_sandbox import WindowsSandbox

    service = Path(config['root']).resolve(strict=True)
    workspace = Path(config['projects'][0]).resolve(strict=True)
    owned = bool(config.get('owned_workspace'))
    root = workspace.parent if owned else service
    if owned and (service.parent != root or service.name != 'service' or workspace.name != 'workspace'):
        raise ValueError('opencode_windows_workspace_binding')
    for name in ['home', 'cache', 'tmp', 'bin']:
        (service / name).mkdir(exist_ok=True, mode=0o700)
    binary = service / 'bin' / 'opencode.exe'
    source_binary = Path(config['binary_root']) / 'opencode.exe'
    if source_binary.resolve() != binary.resolve():
        shutil.copyfile(source_binary, binary)
    profiles = config.get('profiles')
    if not isinstance(profiles, dict) or type(profiles.get('port')) is not int or not 1 <= profiles['port'] <= 65535:
        raise ValueError('opencode_windows_profiles_required')
    spec = importlib.util.spec_from_file_location('opencode_profile_config', Path(__file__).with_name('opencode-sandbox.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    directory = module.profile_config_directory(profiles, profiles['port'], service / 'home' / 'config', service / 'config', {})
    environment = {'SystemRoot': os.environ['SystemRoot'], 'WINDIR': os.environ['SystemRoot'],
        'COMSPEC': str(Path(os.environ['SystemRoot']) / 'System32' / 'cmd.exe'),
        'PATH': str(Path(os.environ['SystemRoot']) / 'System32'),
        'HOME': str(service / 'home'), 'USERPROFILE': str(service / 'home'),
        'APPDATA': str(service / 'home'), 'LOCALAPPDATA': str(service / 'home'),
        'TEMP': str(service / 'tmp'), 'TMP': str(service / 'tmp'),
        'XDG_CONFIG_HOME': str(service / 'home'), 'XDG_DATA_HOME': str(service / 'home'),
        'XDG_CACHE_HOME': str(service / 'cache'), 'OPENCODE_CONFIG_DIR': directory,
        'OPENCODE_DISABLE_AUTOUPDATE': '1', 'OPENCODE_DISABLE_MODELS_FETCH': '1',
        'OPENCODE_SERVER_PASSWORD': config['password']}
    git = shutil.which('git')
    if git:
        environment['PATH'] += os.pathsep + str(Path(git).parent)
        bash = Path(git).parent.parent / 'bin' / 'bash.exe'
        if bash.is_file():
            environment['PATH'] += os.pathsep + str(bash.parent)
    stopped = threading.Event()

    def owner_input():
        for _line in sys.stdin:
            pass
        stopped.set()

    threading.Thread(target=owner_input, daemon=True).start()
    writable = [service / name for name in ['home', 'cache', 'tmp', 'config']] + ([workspace] if owned else [])
    manual = [] if owned else [Path(item).resolve(strict=True) for item in config['projects']]
    if config.get('diagnostics') is True and git:
        script = "const git=" + json.dumps(git) + ";const cwd=" + json.dumps(str(workspace)) + ";" + r"""
(async()=>{const {spawn,spawnSync}=require('node:child_process');const results=[];
for(const mode of ['pipe','ignore','inherit'])for(const input of ['ignore','inherit']){
 await new Promise(resolve=>{
  const key={mode,input};let output='';
  try{const child=spawn(git,['--version'],{cwd,stdio:[input,mode,mode],windowsHide:true});
   child.stdout?.on('data',part=>output+=part);
   child.on('error',error=>{results.push({...key,error:error.code,errno:error.errno});resolve();});
   child.on('close',code=>{if(!results.some(r=>r.mode===mode&&r.input===input))results.push({...key,code,output:output.slice(0,150)});resolve();});
  }catch(error){results.push({...key,error:error.code,errno:error.errno});resolve();}
 });
}
for(const mode of ['pipe','ignore']){
 try{const child=spawnSync(git,['--version'],{cwd,stdio:['inherit',mode,mode],windowsHide:true});results.push({sync:true,mode,code:child.status,error:child.error?.code});}
 catch(error){results.push({sync:true,mode,error:error.code});}
}
try{const child=Bun.spawn([git,'--version'],{cwd,stdin:'inherit',stdout:'pipe',stderr:'pipe'});results.push({bun:true,code:await child.exited,output:(await child.stdout.text()).slice(0,150)});}
catch(error){results.push({bun:true,error:error.code,errno:error.errno});}
console.log(JSON.stringify({bun:Bun.version,spawn_probes:results}));
})().catch(error=>{console.error(error.code||error.name);process.exitCode=1;});
"""
        probe = WindowsSandbox(root, writable=writable, manual_projects=manual)
        try:
            with open(os.devnull, 'rb') as incoming, (service / 'spawn-probe.log').open('wb') as outgoing:
                probe.start([binary, '-e', script], cwd=workspace, env={**environment, 'BUN_BE_BUN': '1'},
                            stdin=incoming, stdout=outgoing, stderr=outgoing)
                probe.wait(probe.process, 15000)
        finally:
            probe.close()
    sandbox = WindowsSandbox(root, writable=writable, manual_projects=manual)
    try:
        with open(os.devnull, 'rb') as incoming, (service / 'process.log').open('wb') as outgoing:
            command = [binary, 'serve', '--hostname', '127.0.0.1', '--port', '0']
            if config.get('diagnostics') is True:
                command[1:1] = ['--print-logs', '--log-level', 'debug']
            pid = sandbox.start(command, cwd=workspace,
                                env=environment, stdin=incoming, stdout=outgoing, stderr=outgoing)
            authorization = 'Basic ' + base64.b64encode(('opencode:' + config['password']).encode()).decode()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            port = None
            for _attempt in range(150):
                if sandbox.poll() is not None or stopped.is_set():
                    raise RuntimeError('opencode_windows_child_exited')
                port = listening_port(pid)
                if port:
                    try:
                        with opener.open(urllib.request.Request(f'http://127.0.0.1:{port}/api/info', headers={'Authorization': authorization}), timeout=1) as response:
                            if json.load(response).get('version', '').startswith('2.'):
                                break
                    except (OSError, ValueError):
                        pass
                time.sleep(.2)
            else:
                raise RuntimeError('opencode_windows_start_timeout')
            print(json.dumps({'ready': True, 'port': port}), flush=True)
            while not stopped.wait(.2):
                if sandbox.poll() is not None:
                    return 1
    finally:
        sandbox.close()
    return 0


if __name__ == '__main__':
    try:
        if len(sys.argv) == 4 and sys.argv[1] == 'extract':
            extract(sys.argv[2], sys.argv[3])
            raise SystemExit(0)
        raise SystemExit(main(json.loads(sys.stdin.readline(65537))))
    except Exception as error:
        print(json.dumps({'ready': False, 'error': type(error).__name__, 'code': getattr(error, 'winerror', None)}), flush=True)
        raise SystemExit(1)
