"""Owner-operated Windows terminal implementing the shared terminal backend."""

import codecs
import os
from pathlib import Path
import queue
import threading

from ..isolation.terminal_manager import TerminalError, _clamp_dimension


class WindowsTerminalBackend:
    def __init__(self):
        self.process = None
        self.output = queue.Queue(maxsize=64)
        self.stopped = threading.Event()
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.exit_code = None

    @property
    def pid(self):
        return self.process.pid if self.process else None

    @property
    def closed(self):
        return self.stopped.is_set()

    def open(self, spec):
        if self.process is not None:
            raise TerminalError('backend_already_open')
        try:
            from winpty import PtyProcess
        except ImportError as error:
            raise TerminalError('conpty_unavailable') from error
        system = os.environ.get('SystemRoot', '')
        shell = Path(system) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        if not system or not shell.is_absolute() or not shell.is_file():
            raise TerminalError('shell_not_absolute')
        env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT',
               'PATH', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP') if key in os.environ}
        env.update(TERM='xterm-256color', IGOREK_TERMINAL='1', PYTHONUTF8='1')
        try:
            self.process = PtyProcess.spawn([str(shell), '-NoLogo', '-NoProfile'],
                cwd=spec.workdir or None, env=env,
                dimensions=(_clamp_dimension(spec.rows, 30), _clamp_dimension(spec.cols, 100)))
        except OSError as error:
            raise TerminalError('conpty_spawn_failed') from error
        threading.Thread(target=self._read, name='conpty-output', daemon=True).start()

    def _read(self):
        try:
            while not self.stopped.is_set():
                chunk = self.process.read(65536)
                if not chunk:
                    if not self.process.isalive():
                        break
                    self.stopped.wait(0.02)
                    continue
                data = chunk if isinstance(chunk, bytes) else chunk.encode('utf-8')
                while not self.stopped.is_set():
                    try:
                        self.output.put(data, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except (EOFError, OSError):
            pass
        finally:
            self.exit_code = self.process.exitstatus

    def read(self, timeout_s):
        try:
            return self.output.get(timeout=max(0, timeout_s))
        except queue.Empty:
            return None

    def write(self, data):
        if self.process is None or self.closed:
            raise TerminalError('terminal_closed')
        text = self.decoder.decode(data)
        if text:
            self.process.write(text)

    def resize(self, rows, cols):
        self.process.setwinsize(_clamp_dimension(rows, 30), _clamp_dimension(cols, 100))

    def poll_exit(self):
        if self.process and not self.process.isalive():
            return self.process.exitstatus
        return self.exit_code

    def close(self):
        self.stopped.set()
        if self.process:
            self.process.close(force=True)
