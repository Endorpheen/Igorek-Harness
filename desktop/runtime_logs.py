"""Bounded, redacted journal and process states for the owned desktop runtime."""
from collections import deque
from datetime import datetime, timezone
import io
import os
import threading

from factory.core.vault.logging_redaction import redact_text
from factory.core.diagnostics import safe_text


class RuntimeLogs(io.TextIOBase):
    def __init__(self, stream):
        self.stream = stream
        self.rows = deque(maxlen=2000)
        self.pending = {}
        self.mutex = threading.Lock()
        self.since = datetime.now(timezone.utc).isoformat()
        self.health = {'write_failures': 0, 'dropped': 0}

    @property
    def encoding(self):
        return self.stream.encoding

    def write(self, text):
        with self.mutex:
            identity = threading.get_ident()
            pending = self.pending.get(identity, '') + text
            parts = pending.split('\n')
            for line in parts[:-1]:
                if line:
                    if len(self.rows) == self.rows.maxlen:
                        self.health['dropped'] += 1
                    cleaned = safe_text(redact_text(line), 20000)
                    self.rows.append(datetime.now(timezone.utc).isoformat() + ' ' + cleaned)
                    try:
                        self.stream.write(cleaned+'\n')
                    except (OSError, ValueError):
                        self.health['write_failures'] += 1
            if len(parts[-1]) > 20000:
                self.health['dropped'] += 1
                self.pending.pop(identity, None)
                self.rows.append('Слишком длинная незавершённая строка пропущена.')
            elif parts[-1]:
                if identity not in self.pending and len(self.pending) >= 64:
                    self.pending.pop(next(iter(self.pending)))
                    self.health['dropped'] += 1
                self.pending[identity] = parts[-1]
            else:
                self.pending.pop(identity, None)
        return len(text)

    def flush(self):
        try:
            self.stream.flush()
        except (OSError, ValueError):
            self.health['write_failures'] += 1

    def status(self, stop, threads, agent):
        running = agent.status()['running']
        states = [('runtime', 'deactivating' if stop.is_set() else 'active'),
                  ('mobile', 'active' if len(threads) > 0 and threads[0].is_alive() else 'inactive'),
                  ('web', 'active' if len(threads) > 1 and threads[1].is_alive() else 'inactive'),
                  ('worker', 'active' if running else 'inactive')]
        return {'units': [{'key': key, 'unit': f'desktop {key} · PID {os.getpid()} · общий журнал',
                           'active': active, 'since': self.since if key != 'worker' else ''}
                          for key, active in states]}

    def tail(self, key, lines):
        if key not in {'runtime', 'mobile', 'web', 'worker'}:
            raise ValueError('Unknown desktop log source')
        with self.mutex:
            rows = list(self.rows)
        return {'unit': f'desktop {key} · общий журнал текущего запуска',
                'lines': rows[-lines:], 'truncated': len(rows) > lines, 'health': dict(self.health)}
