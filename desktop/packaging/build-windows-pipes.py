"""Build the restricted Windows x64 process pipe adapter with the pinned sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def build(output: Path):
    if os.name != 'nt':
        raise OSError('windows_pipe_adapter_requires_windows_build_host')
    repo = Path(__file__).resolve().parents[2]
    native = repo / 'desktop/native'
    vendor = native / 'vendor/detours'
    source = json.loads((vendor / 'SOURCE.json').read_text())
    for name, checksum in source['files'].items():
        if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != checksum:
            raise ValueError('windows_pipe_adapter_source_checksum')
    vswhere = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    installation = subprocess.check_output([str(vswhere), '-latest', '-products', '*', '-requires',
                                            'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                                            '-property', 'installationPath'], text=True).strip()
    setup = Path(installation) / 'VC/Auxiliary/Build/vcvars64.bat'
    if not setup.is_file():
        raise FileNotFoundError('windows_pipe_adapter_compiler_missing')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='igorek-pipe-build-') as temporary:
        work = Path(temporary)
        sources = ['detours', 'modules', 'disasm', 'image', 'creatwth', 'disolx86',
                   'disolx64', 'disolia64', 'disolarm', 'disolarm64']
        command = ['cl.exe', '/nologo', '/LD', '/MT', '/O2', '/EHsc', '/std:c++17',
                   '/DWIN32_LEAN_AND_MEAN', '/D_WIN32_WINNT=0x0A00', '/I' + str(vendor),
                   str(native / 'windows-pipes.cpp'), *[str(vendor / (name + '.cpp')) for name in sources],
                   '/link', '/DEF:' + str(native / 'windows-pipes.def'), '/DYNAMICBASE', '/NXCOMPAT',
                   '/OUT:' + str(work / 'igorek-pipes.dll'), 'advapi32.lib', 'user32.lib']
        script = work / 'build.cmd'
        script.write_text('@echo off\r\nchcp 65001 >nul\r\ncall ' + subprocess.list2cmdline([str(setup)]) +
                          ' >nul\r\nif errorlevel 1 exit /b 1\r\n' + subprocess.list2cmdline(command) +
                          '\r\nexit /b %errorlevel%\r\n', encoding='utf-8')
        subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(script)], cwd=work, check=True)
        (output / 'igorek-pipes.dll').write_bytes((work / 'igorek-pipes.dll').read_bytes())
    (output / 'DETOURS-LICENSE.txt').write_bytes((vendor / 'LICENSE.md').read_bytes())
    return output / 'igorek-pipes.dll'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(build(parser.parse_args().output))
