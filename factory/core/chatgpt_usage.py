"""Profile-bound quota snapshots from authenticated Codex response metadata."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

_LOCK = threading.RLock()


class ResponseHeaders(dict):
    def __init__(self, value, response_observer=None):
        super().__init__(value)
        self.response_observer = response_observer


def weekly_window(headers=None, event=None):
    windows = []
    if headers is not None:
        for name in ('primary', 'secondary'):
            windows.append({'used_percent': headers.get(f'x-codex-{name}-used-percent'),
                            'window_minutes': headers.get(f'x-codex-{name}-window-minutes'),
                            'reset_at': headers.get(f'x-codex-{name}-reset-at')})
    if isinstance(event, dict) and event.get('type') == 'codex.rate_limits':
        if event.get('metered_limit_name', event.get('limit_name', 'codex')) not in {None, 'codex'}:
            return None
        limits = event.get('rate_limits')
        if isinstance(limits, dict):
            windows.extend(limits.get(name) for name in ('primary', 'secondary'))
    for window in windows:
        if not isinstance(window, dict):
            continue
        try:
            minutes, percent = float(window['window_minutes']), float(window['used_percent'])
            if minutes != 10080 or not math.isfinite(percent) or not 0 <= percent <= 100:
                continue
            reset = window.get('reset_at')
            resets_at = datetime.fromtimestamp(float(reset), UTC).isoformat() if reset is not None else ''
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        return {'percent': percent, 'status': 'rate-limited' if percent >= 100 else 'ok', 'resetsAt': resets_at}
    return None


class UsageStore:
    def __init__(self, home, credential_id, config):
        binding = json.dumps([credential_id, config], sort_keys=True).encode()
        self.path = Path(home) / 'usage' / (hashlib.sha256(binding).hexdigest() + '.json')

    def observe(self, *, headers=None, event=None):
        window = weekly_window(headers, event)
        if window is None:
            return
        self.save({'usage': {'weekly': window}, 'fetched_at': datetime.now(UTC).isoformat(), 'source': 'response'})

    def save(self, value):
        with _LOCK:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.usage-')
            try:
                with os.fdopen(fd, 'w') as handle:
                    json.dump(value, handle)
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def load(self):
        with _LOCK:
            try:
                value = json.loads(self.path.read_text())
                usage = {}
                for name in ('rolling','weekly'):
                    entry=value['usage'].get(name)
                    if not isinstance(entry,dict):continue
                    normalized = weekly_window(event={'type': 'codex.rate_limits', 'rate_limits': {'primary': {
                        'window_minutes': 10080, 'used_percent': entry['percent'],
                        'reset_at': datetime.fromisoformat(entry['resetsAt']).timestamp() if entry['resetsAt'] else None}}})
                    if normalized is not None:usage[name]=normalized
                if not usage or not isinstance(value['fetched_at'], str):
                    raise ValueError()
                result={'usage': usage, 'fetched_at': datetime.fromisoformat(value['fetched_at']).isoformat(), 'source': value.get('source') if value.get('source') in {'response', 'account_usage', 'codex_account','codex_local'} else 'response'}
                for key in ('account_email','account_key'):
                    if isinstance(value.get(key),str) and len(value[key])<=256:result[key]=value[key]
                return result
            except (OSError, ValueError, KeyError, TypeError):
                return {'usage': None, 'fetched_at': '', 'source': 'response'}


USAGE_URL = 'https://chatgpt.com/backend-api/wham/usage'


class UsageError(Exception):
    def __init__(self, code, http_status=None):
        self.code, self.http_status = code, http_status
        super().__init__(code)


def account_limit_windows(payload):
    """Select supported quota windows by duration, never by their position."""
    if not isinstance(payload, dict) or not isinstance(payload.get('rate_limit'), dict):
        raise UsageError('quota_invalid_response')
    usage = {}
    for name in ('primary_window', 'secondary_window'):
        value = payload['rate_limit'].get(name)
        if not isinstance(value, dict):
            continue
        duration, percent = value.get('limit_window_seconds'), value.get('used_percent')
        if type(duration) not in (int, float) or duration not in (18000, 604800):
            continue
        if type(percent) not in (int, float):
            continue
        entry = weekly_window(event={'type': 'codex.rate_limits', 'rate_limits': {'primary': {
            'window_minutes': 10080, 'used_percent': percent, 'reset_at': value.get('reset_at')}}})
        if entry is not None:
            usage['rolling' if duration == 18000 else 'weekly'] = entry
    if not usage:
        raise UsageError('quota_weekly_missing')
    return usage


def fetch_account_usage(token, *, account_id=None, expected_email=None, include_identity=False, opener=None):
    import urllib.request
    import urllib.error
    from .chatgpt_auth import NoRedirect
    if (not isinstance(token, str) or not token or len(token) > 32768
            or any(not 33 <= ord(char) <= 126 for char in token)):
        raise UsageError('quota_identity_invalid')
    headers = {'Authorization': 'Bearer ' + token,
               'Accept': 'application/json', 'User-Agent': 'IgorekDesktop/quota'}
    if account_id is not None:
        if (not isinstance(account_id, str) or not account_id or len(account_id) > 256
                or any(not 33 <= ord(char) <= 126 for char in account_id)):
            raise UsageError('quota_identity_invalid')
        headers['ChatGPT-Account-Id'] = account_id
    request = urllib.request.Request(USAGE_URL, method='GET', headers=headers)
    try:
        with (opener or urllib.request.build_opener(NoRedirect)).open(request, timeout=8) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise UsageError('quota_invalid_response')
        payload = json.loads(raw)
        identity = None
        if account_id is not None or expected_email is not None or include_identity:
            remote_account = payload.get('account_id') if isinstance(payload, dict) else None
            remote_email = payload.get('email') if isinstance(payload, dict) else None
            if (not isinstance(remote_account, str) or not remote_account or len(remote_account) > 256
                    or not isinstance(remote_email, str) or not remote_email or len(remote_email) > 256
                    or account_id is not None and remote_account != account_id
                    or expected_email is not None and remote_email.casefold() != expected_email.casefold()):
                raise UsageError('quota_account_mismatch')
            identity = {'account_id': remote_account, 'email': remote_email.casefold()}
        result = {'usage': account_limit_windows(payload),
                  'fetched_at': datetime.now(UTC).isoformat(), 'source': 'account_usage'}
        if include_identity:
            result['_account_identity'] = identity
        return result
    except urllib.error.HTTPError as error:
        code = 'quota_access_denied' if error.code in {401, 403} else 'quota_rate_limited' if error.code == 429 else 'quota_http_error'
        error.close()
        raise UsageError(code, error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UsageError('quota_network') from None
    except (ValueError, TypeError):
        raise UsageError('quota_invalid_response') from None
