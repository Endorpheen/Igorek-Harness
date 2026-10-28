"""Versioned, privacy-preserving error observations and local incident episodes."""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import random
import re
import socket
import sqlite3
import ssl
import threading
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'config/error-catalog.json').read_text(encoding='utf-8'))
CODES = CATALOG['codes']
_ID = re.compile(r'[A-Za-z0-9_.:-]{1,160}')
_FIELDS = {'event_id', 'code', 'component', 'operation', 'endpoint_role', 'owner', 'phase', 'delivery',
           'outcome', 'task_id', 'session_id', 'tool_use_id', 'http_request_id', 'client_operation_id',
           'provider_request_id', 'profile', 'model', 'build', 'native_code', 'reason_code'}


def safe_text(value, limit=1000):
    try:
        text = str(value or '')[:16000]
    except Exception:
        return '[unavailable]'
    text = re.sub(r'\bBearer\s+[^\s"\'<>]+', 'Bearer [REDACTED]', text, flags=re.I)
    text = re.sub(r'''(["']?(?:access_token|refresh_token|id_token|api[_-]?key|authorization|cookie|password|token|secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)''', r'\1[REDACTED]', text, flags=re.I)
    text = re.sub(r'\b(?:sk-[\w-]+|gh[pousr]_[\w]+|github_pat_[\w]+|eyJ[\w-]+\.[\w-]+\.[\w-]+)\b', '[REDACTED]', text)
    def url(match):
        try:
            parsed = urlsplit(match[0])
            return urlunsplit((parsed.scheme, parsed.hostname or '', parsed.path, '', ''))
        except ValueError:
            return '[URL]'
    return re.sub(r'https?://[^\s<>"\']+', url, text)[:limit]


def safe_log_value(value, *, depth=0):
    """Sanitize diagnostic copies only; execution and approval inputs stay original."""
    if depth > 12:
        return '[TRUNCATED]'
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:100]:
            normalized = str(key).lower().replace('-', '_')
            secret = normalized in {'authorization', 'cookie', 'set_cookie', 'api_key', 'x_api_key', 'access_token', 'refresh_token', 'id_token', 'password', 'secret', 'token'}
            result[str(key)] = '[REDACTED]' if secret and isinstance(item, str) else safe_log_value(item, depth=depth+1)
        return result
    if isinstance(value, (list, tuple)):
        return [safe_log_value(item, depth=depth+1) for item in value[:100]]
    return safe_text(value, 16000) if isinstance(value, str) else value


def classify_error(error=None, *, native_code='', status=None, phase='unknown'):
    try:
        return _classify_error(error, native_code=native_code, status=status, phase=phase)
    except Exception:
        return 'INTERNAL.UNEXPECTED'


def _exception_chain(error):
    seen = set()
    for _ in range(5):
        if not isinstance(error, BaseException) or id(error) in seen:
            break
        seen.add(id(error))
        yield error
        error = (getattr(error, 'reason', None) or error.__cause__ or
                 (None if error.__suppress_context__ else error.__context__))


def _storage_error(error):
    if isinstance(error, sqlite3.Error):
        number = getattr(error, 'sqlite_errorcode', None)
        name = getattr(error, 'sqlite_errorname', '')
        if type(number) is not int:
            return '', ''
        reason = name if isinstance(name, str) and name.startswith('SQLITE_') and getattr(sqlite3, name, None) == number else ''
        code = {sqlite3.SQLITE_FULL: 'STORAGE.FULL', sqlite3.SQLITE_READONLY: 'STORAGE.READ_ONLY',
                sqlite3.SQLITE_IOERR: 'STORAGE.IO_ERROR', sqlite3.SQLITE_BUSY: 'DATABASE.BUSY',
                sqlite3.SQLITE_LOCKED: 'DATABASE.BUSY', sqlite3.SQLITE_CORRUPT: 'DATABASE.INVALID',
                sqlite3.SQLITE_NOTADB: 'DATABASE.INVALID'}.get(number & 0xff, '')
        return code, reason
    if isinstance(error, OSError) and not isinstance(error, (ssl.SSLError, socket.gaierror, TimeoutError)):
        reason = errno.errorcode.get(error.errno, '')
        return {'ENOSPC': 'STORAGE.FULL', 'EDQUOT': 'STORAGE.QUOTA_EXCEEDED',
                'EROFS': 'STORAGE.READ_ONLY', 'EIO': 'STORAGE.IO_ERROR'}.get(reason, ''), reason
    return '', ''


def exception_details(error):
    """Keep only native enum identifiers, never paths, SQL or exception messages."""
    result = {'native_code': type(error).__name__}
    try:
        for candidate in _exception_chain(error):
            code, reason = _storage_error(candidate)
            if reason:
                if code:
                    result['reason_code'] = reason
                    break
                result.setdefault('reason_code', reason)
    except Exception:
        pass
    return result


def _classify_error(error=None, *, native_code='', status=None, phase='unknown'):
    """Use native types, status and codes; never infer cause from user prose."""
    for candidate in _exception_chain(error):
        storage_code, _ = _storage_error(candidate)
        if storage_code:
            return storage_code
        if isinstance(candidate, ssl.SSLCertVerificationError):
            return 'TLS.CERTIFICATE_REJECTED'
        if isinstance(candidate, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
            return 'TLS.CONNECTION_INTERRUPTED'
        if isinstance(candidate, ssl.SSLError):
            return 'TLS.PROTOCOL_FAILURE'
        if isinstance(candidate, socket.gaierror):
            return 'NET.DNS_FAILURE'
        if isinstance(candidate, TimeoutError):
            return 'NET.CONNECT_TIMEOUT' if phase == 'connect' else 'NET.READ_TIMEOUT'
        if isinstance(candidate, OSError):
            native_code = errno.errorcode.get(candidate.errno, native_code)
        if status is None and isinstance(getattr(candidate, 'code', None), int):
            status = candidate.code
    native = {'EAI_AGAIN': 'NET.DNS_FAILURE', 'ENOTFOUND': 'NET.DNS_FAILURE', 'ECONNREFUSED': 'NET.CONNECTION_REFUSED',
              'ECONNRESET': 'NET.CONNECTION_RESET', 'ECONNABORTED': 'NET.CONNECTION_RESET', 'EPIPE': 'NET.CONNECTION_RESET',
              'ENETUNREACH': 'NET.OFFLINE', 'ENETDOWN': 'NET.OFFLINE', 'ENOSPC': 'STORAGE.FULL',
              'EDQUOT': 'STORAGE.QUOTA_EXCEEDED', 'EROFS': 'STORAGE.READ_ONLY', 'EIO': 'STORAGE.IO_ERROR',
              'SQLITE_FULL': 'STORAGE.FULL', 'SQLITE_READONLY': 'STORAGE.READ_ONLY', 'SQLITE_IOERR': 'STORAGE.IO_ERROR',
              'SQLITE_BUSY': 'DATABASE.BUSY', 'SQLITE_LOCKED': 'DATABASE.BUSY',
              'SQLITE_CORRUPT': 'DATABASE.INVALID', 'SQLITE_NOTADB': 'DATABASE.INVALID',
              'CERTIFICATE_REJECTED': 'TLS.CERTIFICATE_REJECTED', 'INVALID_RESPONSE': 'PROTOCOL.INVALID_RESPONSE',
              'TLS_EOF': 'TLS.CONNECTION_INTERRUPTED', 'TRANSPORT_UNKNOWN': 'NET.TRANSPORT_FAILURE',
              'CANCELLED': 'OPERATION.CANCELLED'}
    if native_code in native:
        return native[native_code]
    if native_code in {'TIMEOUT', 'ETIMEDOUT', 'ESOCKETTIMEDOUT'}:
        return 'NET.CONNECT_TIMEOUT' if phase == 'connect' else 'NET.READ_TIMEOUT'
    if status in {401, 403}:
        return 'AUTH.REJECTED'
    if status == 429:
        return 'HTTP.RATE_LIMITED'
    if status in {408, 504}:
        return 'NET.READ_TIMEOUT'
    if status in {500, 502, 503, 529}:
        return 'HTTP.SERVICE_UNAVAILABLE'
    if isinstance(status, int) and status >= 400:
        return 'HTTP.REQUEST_REJECTED'
    return 'INTERNAL.UNEXPECTED'


def transport_failure(error=None, *, status=None, phase='connect', request_id='', retry_after=None):
    code = classify_error(error, status=status, phase=phase)
    if code == 'INTERNAL.UNEXPECTED' and isinstance(error, OSError):
        code = 'NET.TRANSPORT_FAILURE'
    if isinstance(error, json.JSONDecodeError):
        code = 'PROTOCOL.INVALID_RESPONSE'
    if type(error).__name__ == 'RemoteDisconnected':
        code = 'NET.CONNECTION_RESET'
    details = observation(code, component='model', operation='model_round', endpoint_role='runtime_to_provider',
        phase=phase, http_status=status, provider_request_id=request_id, native_code=type(error).__name__ if error else '')
    try:
        from email.utils import parsedate_to_datetime
        delay = float(retry_after)
    except (TypeError, ValueError):
        try:
            delay = parsedate_to_datetime(retry_after).timestamp()-time.time()
        except (TypeError, ValueError, OverflowError):
            delay = 0
    if 0 < delay <= 900:
        details['retry_after_s'] = delay
    return {'error_code': code, 'diagnostic': details, 'http_status': status}


def safe_reason_detail(value):
    text = safe_text(value, 512)
    return re.sub(r'\b(?:sk[-_][\w-]+|oc_sk_[\w-]+|syt_[\w-]+|gh[pousr]_[\w]+|github_pat_[\w]+)\b', '[REDACTED]', text)


def model_error_reason(body):
    """Preserve the provider's explicit error without interpreting its prose."""
    raw = body.get('error')
    nested = raw if isinstance(raw, dict) else None
    structured_text = isinstance(raw, str) and raw.find('{') >= 0
    if isinstance(raw, str) and len(raw) <= 16000:
        start = raw.find('{')
        if start >= 0:
            try:
                parsed = json.loads(raw[start:])
                nested = parsed if isinstance(parsed, dict) else None
            except ValueError:
                pass
    if isinstance(nested, dict) and isinstance(nested.get('error'), dict):
        nested = nested['error']
    reason = nested.get('message', '') if isinstance(nested, dict) else raw if isinstance(raw, str) and not structured_text else ''
    reason = reason if isinstance(reason, str) else ''
    nested = nested or {}
    from .cc_backend_trace import model_failure_diagnostics
    candidates = [body.get('provider_error_code'), nested.get('code'), nested.get('type')]
    native = next((value for value in candidates if isinstance(value, str)), '')
    accepted = model_failure_diagnostics({'provider_error_code': native, 'error_code': body.get('error_code')})
    native = accepted.get('provider_error_code') or accepted.get('error_code') or ''
    return {'reason_detail': safe_reason_detail(reason), 'reason_code': native}


def model_observation(body, *, profile='', model='', session_id='', task_id=''):
    existing = body.get('diagnostic') if isinstance(body, dict) else None
    legacy = {'responses_network': 'NET.CONNECTION_RESET', 'responses_timeout': 'NET.READ_TIMEOUT',
              'responses_tls': 'TLS.PROTOCOL_FAILURE', 'responses_incomplete_stream': 'STREAM.INTERRUPTED',
              'responses_invalid_response': 'PROTOCOL.INVALID_RESPONSE', 'authentication': 'AUTH.REJECTED',
              'subscription_sharing_usage_limit_exceeded': 'QUOTA.EXHAUSTED'}
    code = existing.get('code') if isinstance(existing, dict) else body.get('error_code')
    code = code if isinstance(code, str) and code in CODES else legacy.get(code if isinstance(code, str) else '', classify_error(status=body.get('http_status')))
    if code == 'INTERNAL.UNEXPECTED' and isinstance(body.get('error_code'), str):
        code = legacy.get(body['error_code'], code)
    if code == 'INTERNAL.UNEXPECTED' and not existing and body.get('error'):
        code = 'ERROR.UNKNOWN'
    if body.get('tls_error_type') == 'SSLCertVerificationError' or body.get('tls_verify_code') is not None:
        code = 'TLS.CERTIFICATE_REJECTED'
    elif body.get('error_code') == 'responses_tls' and (body.get('tls_error_type') in {'SSLEOFError', 'SSLZeroReturnError'} or body.get('tls_reason') in {'UNEXPECTED_EOF_WHILE_READING', 'TLSV1_ALERT_INTERNAL_ERROR'}):
        code = 'TLS.CONNECTION_INTERRUPTED'
    details = {k: body[k] for k in ('http_status', 'retry_after_s') if k in body}
    details.update(model_error_reason(body))
    if isinstance(existing, dict):
        details.update({k: existing[k] for k in ('http_status', 'retry_after_s', 'reason_code', 'reason_detail') if k in existing})
    return observation(code, component='model', operation='model_round', endpoint_role='runtime_to_provider',
        owner='model:'+hashlib.sha256(json.dumps([profile, model, task_id]).encode()).hexdigest()[:24], profile=profile, model=model, session_id=session_id or '', task_id=task_id or '',
        phase=str(body.get('transport_phase') or (existing or {}).get('phase') or 'unknown'),
        provider_request_id=str(body.get('request_id') or (existing or {}).get('provider_request_id') or ''), **details)


def observation(code, *, component, operation, endpoint_role, owner='', delivery='unknown', **details):
    code = code if isinstance(code, str) and code in CODES else 'INTERNAL.UNEXPECTED'
    policy = CODES[code]
    result = {'schema_version': 1, 'event_id': uuid4().hex, 'observed_at_ms': int(time.time()*1000),
              'code': code, 'category': policy['category'], 'severity': policy['severity'],
              'action': policy['action'], 'component': component, 'operation': operation,
              'endpoint_role': endpoint_role, 'owner': owner,
              'delivery': delivery if delivery in {'not_sent', 'accepted', 'rejected', 'unknown'} else 'unknown',
              'outcome': 'cancelled' if code == 'OPERATION.CANCELLED' else 'unknown' if delivery in {'accepted', 'unknown'} and operation in {'create', 'write', 'send'} else 'failed'}
    for key, value in details.items():
        if key == 'reason_detail' and isinstance(value, str):
            result[key] = safe_reason_detail(value)
        elif key in {'profile', 'model'} and isinstance(value, str):
            result[key] = value
        elif key in _FIELDS and isinstance(value, str) and len(value) <= 160 and _ID.fullmatch(value):
            result[key] = value
        elif key in {'http_status', 'retry_after_s', 'attempt', 'next_retry_at_ms'} and type(value) in {int, float} and 0 <= value < 10**15:
            result[key] = value
    return sanitize_observation(result)


def sanitize_observation(value):
    if not isinstance(value, dict):
        return {}
    code = value.get('code') if isinstance(value.get('code'), str) and value['code'] in CODES else 'INTERNAL.UNEXPECTED'
    result = {'schema_version': 1, 'code': code, **{key: CODES[code][key] for key in ('category', 'severity', 'action')}}
    for key in _FIELDS - {'code'}:
        item = value.get(key)
        if key in {'profile', 'model'} and isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9_./:-]{1,160}', item) and '://' not in item:
            result[key] = item
        elif isinstance(item, str) and _ID.fullmatch(item) and not (key == 'reason_code' and re.match(r'^(sk[-_]|syt_|oc_sk_|gh[pousr]_|github_pat_|eyJ)', item)):
            result[key] = item
    for key in ('observed_at_ms', 'http_status', 'retry_after_s', 'attempt', 'next_retry_at_ms'):
        item = value.get(key)
        if type(item) in {int, float} and 0 <= item < 10**15:
            result[key] = item
    if isinstance(value.get('reason_detail'), str) and value['reason_detail']:
        result['reason_detail'] = safe_reason_detail(value['reason_detail'])
    return result


def retry_decision(event, *, attempt, elapsed_s, replay_safe=False, random_value=None):
    code = event.get('code', 'INTERNAL.UNEXPECTED')
    policy = CODES.get(code, CODES['INTERNAL.UNEXPECTED'])
    if event.get('operation') in {'write', 'create', 'send'} and not replay_safe:
        return {'retry': False, 'action': 'reconcile' if event.get('delivery') in {'unknown', 'accepted'} else policy['action']}
    if not replay_safe or not policy['retryable'] or attempt >= CATALOG['retry']['max_attempts']:
        return {'retry': False, 'action': policy['action']}
    ceiling = min(CATALOG['retry']['max_delay_ms'], CATALOG['retry']['base_delay_ms'] * 2**min(max(attempt-1, 0), 20))
    jitter = random.random() if random_value is None else max(0, min(1, random_value))
    delay = max(int(ceiling*(0.5+0.5*jitter)), min(900000, float(event.get('retry_after_s', 0)) * 1000))
    if elapsed_s*1000 + delay >= CATALOG['retry']['deadline_ms']:
        return {'retry': False, 'action': 'check_connection'}
    return {'retry': True, 'action': 'wait', 'attempt': attempt+1, 'delay_ms': int(delay), 'next_retry_at_ms': int(time.time()*1000+delay)}


class IncidentJournal:
    """Bounded atomic local journal; failures remain observable without failing work."""
    def __init__(self, path, *, limit=100):
        self.path = Path(path) if path is not None else None
        self.limit = max(1, min(limit, 100))
        self.lock = threading.RLock()
        self.items = []
        self.health = {'dropped': 0, 'write_failures': 0, 'state': 'ready'}
        if self.path is None:
            return
        try:
            if self.path.stat().st_size > 524288:
                raise ValueError('Journal too large')
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.items = [x for x in data.get('incidents', []) if isinstance(x, dict) and isinstance(x.get('incident_id'), str)][-self.limit:]
            for item in self.items:
                item['error'] = sanitize_observation(item.get('error'))
                item['events'] = [sanitize_observation(x) for x in item.get('events', [])[-8:] if isinstance(x, dict)] if isinstance(item.get('events'), list) else []
                item['observations'] = min(10**9, max(1, item.get('observations', 1))) if type(item.get('observations')) is int else 1
                item['read'] = item.get('read') is True
                item['state'] = item.get('state') if item.get('state') in {'observed','recovering','needs_owner','recovered'} else 'observed'
        except FileNotFoundError:
            pass
        except (ValueError, OSError, TypeError, AttributeError):
            self.health['state'] = 'history_unavailable'

    def _save(self):
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.path.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as handle:
                os.chmod(temporary, 0o600)
                json.dump(self.snapshot(), handle, ensure_ascii=False)
            temporary.replace(self.path)
            self.health['state'] = 'ready'
        except (OSError, ValueError):
            self.health['write_failures'] += 1
            self.health['state'] = 'write_failed'

    def observe(self, event, *, now=None):
        event = sanitize_observation(event)
        stamp = int((time.time() if now is None else now)*1000)
        key = hashlib.sha256(json.dumps([event.get(k, '') for k in ('owner', 'endpoint_role', 'component', 'code')]+[event.get('operation') if event.get('outcome')=='unknown' else 'read']).encode()).hexdigest()[:24]
        with self.lock:
            item = next((x for x in reversed(self.items) if x.get('fingerprint') == key and x.get('state') != 'recovered'), None)
            if item is not None and any(x.get('event_id') == event.get('event_id') for x in item.get('events', [])):
                return json.loads(json.dumps(item))
            if item is None:
                item = {'incident_id': uuid4().hex, 'fingerprint': key, 'first_seen_ms': stamp, 'observations': 0, 'read': False, 'state': 'observed'}
                self.items.append(item)
            history = item.setdefault('events', [])
            history.append(event)
            if len(history) > 8:
                self.health['dropped'] += len(history)-8
                item['events'] = history[-8:]
            item.update(last_seen_ms=stamp, observations=item['observations']+1, error=event)
            item['state'] = 'needs_owner' if event.get('outcome')=='unknown' or not CODES[event['code']]['retryable'] else 'recovering' if event.get('next_retry_at_ms') else 'observed'
            if len(self.items) > self.limit:
                self.health['dropped'] += len(self.items)-self.limit
                self.items = self.items[-self.limit:]
            self._save()
            return json.loads(json.dumps(item))

    def recover(self, *, owner, stable_s=0, now=None, proof='stable_ready'):
        if proof != 'operation_completed' and stable_s*1000 < CATALOG['stable_after_ms']:
            return False
        changed = False
        with self.lock:
            for item in self.items:
                if item.get('error', {}).get('owner') == owner and item.get('state') != 'recovered' and item.get('error', {}).get('outcome') != 'unknown':
                    item.update(state='recovered', recovery_proof=proof, recovered_at_ms=int((time.time() if now is None else now)*1000))
                    changed = True
            if changed:
                self._save()
        return changed

    def acknowledge(self, incident_id):
        with self.lock:
            for item in self.items:
                if item.get('incident_id') == incident_id:
                    item['read'] = True
            self._save()

    def snapshot(self):
        with self.lock:
            items = [{key: value for key, value in item.items() if key in {'incident_id', 'fingerprint', 'first_seen_ms', 'last_seen_ms', 'observations', 'read', 'state', 'recovered_at_ms', 'recovery_proof'}} | {'error': sanitize_observation(item.get('error')), 'events': [sanitize_observation(x) for x in item.get('events', [])[-8:]]} for item in self.items]
            result = {'schema_version': 1, 'captured_at_ms': int(time.time()*1000), 'incidents': json.loads(json.dumps(items)), 'health': dict(self.health)}
            removed = 0
            while len(json.dumps(result).encode()) > 500000 and result['incidents']:
                result['incidents'].pop(0)
                removed += 1
            result['truncated'] = removed > 0
            result['health']['snapshot_omitted'] = removed
            return result


class ActivityTracker:
    """Report observed progress and uncertainty; never stop or restart a task."""
    def __init__(self):
        self.items = {}
        self.lock = threading.RLock()

    def observe(self, event, *, now=None):
        payload = event.get('payload') or {}
        task = event.get('task_id') or payload.get('task_id')
        if not isinstance(task, str) or not _ID.fullmatch(task):
            return
        stamp = time.monotonic() if now is None else now
        kind = event.get('kind')
        if kind not in {'task.start','task.finish','task.status','task.error','model.text_delta','model.reasoning_delta','model.round_response','tool.request_batch','tool.result','approval.required','approval.resolved'}:
            return
        with self.lock:
            item = self.items.setdefault(task, {'started':stamp,'progress':stamp,'phase':'unknown','status':'active'})
            if kind == 'task.start':
                item.update(started=stamp, status='active', phase='unknown')
                item.pop('finished', None)
            item['progress'] = stamp
            if kind in {'task.finish', 'task.status', 'task.error'}:
                status = 'fail' if kind == 'task.error' else str(payload.get('status') or 'unknown')
                if status in {'waiting_approval', 'waiting_owner'}:
                    item.update(status='waiting_owner', phase='approval')
                    item.pop('finished', None)
                elif status in {'running', 'retry', 'queued'}:
                    item.update(status='active', phase='queued' if status != 'running' else 'unknown')
                    item.pop('finished', None)
                else:
                    item.update(status=status, finished=stamp)
            elif kind == 'approval.required':
                item.update(status='waiting_owner', phase='approval')
            elif kind == 'approval.resolved':
                item.update(status='active', phase='model')
            elif kind == 'tool.request_batch':
                item['phase'] = 'tool'
            elif kind.startswith('model.') or kind == 'tool.result':
                item['phase'] = 'model'
            if len(self.items) > 100:
                self.items.pop(next(iter(self.items)))

    def snapshot(self, *, now=None):
        stamp = time.monotonic() if now is None else now
        with self.lock:
            return [{'task_id': task, 'status': item['status'], 'phase': item['phase'],
                     'observed_duration_ms': max(0, int((item.get('finished', stamp)-item['started'])*1000)),
                     'since_progress_ms': max(0, int((item.get('finished', stamp)-item['progress'])*1000)),
                     'progress_state': 'unconfirmed' if item['status']=='active' and stamp-item['progress']>=120 else 'long_running' if item['status']=='active' and stamp-item['started']>=180 else 'observed'}
                    for task, item in self.items.items()]
