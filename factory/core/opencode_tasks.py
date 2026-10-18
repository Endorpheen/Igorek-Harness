"""Desktop-only OpenCode delegation with owner-routed input and durable receipts."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .desktop_browser import DesktopBrowserPipe
from .opencode_workspace import git
from .tool_registry import registry


SCHEMA = {
    'name': 'opencode_task',
    'description': 'Передать явно порученную пользователем работу OpenCode в текущем Git-проекте без копирования. Правки сразу видны в проекте; до завершения или отмены основной чат может только читать. Отмена не откатывает сделанные правки. start возвращает run_id сразу после принятия: не жди завершения и не опрашивай в цикле. Вопросы автоматически передаются владельцу; отвечать за него запрещено. Результат приходит автоматически. status читает состояние, cancel останавливает поручение. Обычный OpenCode не изменяется.',
    'input_schema': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['start', 'status', 'cancel']},
        'goal': {'type': 'string'}, 'title': {'type': 'string'}, 'run_id': {'type': 'string'},
    }, 'required': ['action'], 'additionalProperties': False},
}
FINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


class OpenCodeTasks(DesktopBrowserPipe):
    def __init__(self, root, service, emit, allowed, timeout=90):
        super().__init__(emit, allowed, timeout, channel='opencode')
        self.root, self.service = Path(root), service
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == 'nt':
            from .platform_support.windows_security import protect_directory
            protect_directory(self.root)
        self.db = self.root / 'tasks.sqlite3'
        self.state_lock = threading.RLock()
        self.writers = {}
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_key TEXT UNIQUE, state TEXT, document TEXT)')
        for run in self.runs():
            if run['state'] not in FINAL:
                self.clear_inputs(run)
                run.update(state='interrupted', result='OpenCode: приложение было перезапущено. Работа прервана; повторный запуск не выполнялся. Уже сделанные изменения сохранены.', inputs=[])
                self.save(run)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def runs(self):
        with self.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute('SELECT document FROM runs ORDER BY rowid')]

    def save(self, run):
        with self.connect() as conn:
            conn.execute('INSERT INTO runs VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET state=excluded.state, document=excluded.document',
                         (run['id'], run['request_key'], run['state'], json.dumps(run, ensure_ascii=False)))

    def lookup(self, run_id):
        return next((r for r in self.runs() if r['id'] == run_id), None)

    def call(self, fields, **context):
        if not self.allowed(context.get('profile')) or self.closed:
            return json.dumps({'error': 'opencode_access_denied'})
        if not isinstance(fields, dict) or set(fields) - {'action', 'goal', 'title', 'run_id'}:
            return json.dumps({'error': 'invalid_request'})
        session = str(context.get('current_session') or '')
        if not session:
            return json.dumps({'error': 'opencode_requires_desktop_session'})
        action = fields.get('action')
        with self.state_lock:
            if action == 'start':
                goal, title = fields.get('goal'), fields.get('title')
                if not isinstance(goal, str) or not goal.strip() or len(goal) > 24000 or not isinstance(title, str) or not title.strip() or len(title) > 120:
                    return json.dumps({'error': 'goal_and_title_required'})
                key = str(context.get('task_id') or '') + ':' + str(context.get('tool_use_id') or '')
                prior = next((r for r in self.runs() if r['request_key'] == key), None)
                if prior:
                    return json.dumps(self.public(prior), ensure_ascii=False)
                if any(r['state'] not in FINAL for r in self.runs()):
                    return json.dumps({'error': 'opencode_task_already_running'})
                source = Path(context['workdir']).resolve(strict=True)
                if any(self.overlaps(source, session, writer) for writer in self.writers.values()):
                    return json.dumps({'error': 'opencode_project_writer_active'})
                run_id = 'oc_' + uuid.uuid4().hex
                run = {'id': run_id, 'request_key': key, 'session': session, 'profile': context.get('profile'),
                       'source': str(source), 'execution_mode': 'direct', 'goal': goal, 'title': title,
                       'state': 'preparing', 'inputs': [], 'seen': [], 'delivered': False, 'result': ''}
                self.save(run)
                submitted = False
                try:
                    if not source.is_dir() or git(source, 'rev-parse', '--is-inside-work-tree').strip() != b'true':
                        raise ValueError('opencode_git_project_required')
                    submitted = True
                    reply = json.loads(self.request({'action': 'start', 'run_id': run_id, 'source': run['source'],
                        'execution_mode': 'direct', 'title': title, 'goal': goal, 'profile': run['profile'], 'session': session}, context.get('profile')))
                    if not reply.get('error') and reply.get('execution_mode') != 'direct':
                        raise ValueError('opencode_direct_protocol_required')
                    if reply.get('error'):
                        raise ValueError(str(reply['error']))
                    run.update(state='running', external_session=reply['session_id'])
                except Exception as exc:
                    uncertain = submitted and 'timeout' in str(exc)
                    run.update(state='running' if uncertain else 'failed',
                               delivered=not uncertain,
                               result='OpenCode: состояние запуска уточняется.' if uncertain else
                               'OpenCode: поручение не запущено (' + str(exc)[:150] + ').')
                self.save(run)
                return json.dumps(self.public(run), ensure_ascii=False)
            run = self.lookup(str(fields.get('run_id') or ''))
            if not run or run['session'] != session:
                return json.dumps({'error': 'opencode_task_not_found'})
            if action == 'cancel' and run['state'] not in FINAL:
                reply = json.loads(self.request({'action': 'cancel', 'run_id': run['id']}))
                if reply.get('error'):
                    return json.dumps(reply)
                self.clear_inputs(run)
                run.update(state='cancelled', inputs=[], result='OpenCode: поручение остановлено. Уже сделанные изменения остались в исходном проекте.')
                self.save(run)
            elif action not in {'status', 'cancel'}:
                return json.dumps({'error': 'invalid_request'})
            return json.dumps(self.public(run), ensure_ascii=False)

    @staticmethod
    def public(run):
        return {'run_id': run['id'], 'status': run['state'], 'result': run['result']}

    def install(self):
        registry.register(name='opencode_task', toolset='delegation', schema=SCHEMA,
                          handler=self.call, check_fn=self.available)
        registry.add_execution_guard(self)

    @staticmethod
    def overlaps(source, session, context):
        if session and str(context.get('current_session') or '') == session:
            return True
        paths = [context.get('workdir')]
        paths.extend(context.get('targets', []))
        for value in paths:
            if value:
                target = Path(value).resolve()
                if target.is_relative_to(source) or source.is_relative_to(target):
                    return True
        return False

    def policy_error(self, name, args, context):
        if name in {'Read', 'Grep', 'Glob', 'opencode_task'}:
            return None
        context = dict(context)
        if isinstance(args, dict):
            context['targets'] = [str(Path(context.get('workdir') or '.') / args[key])
                                  for key in ('file_path', 'path') if isinstance(args.get(key), str)]
        with self.state_lock:
            for run in self.runs():
                if run['state'] not in FINAL and self.overlaps(Path(run['source']), run['session'], context):
                    return {'error': 'opencode_project_read_only', 'run_id': run['id'],
                            'message': 'OpenCode работает в исходном проекте. Доступны разговор и Read/Grep/Glob; запись возобновится после завершения или отмены.'}
        return None

    @contextmanager
    def lease(self, name, args, context):
        token = None
        context = dict(context)
        if isinstance(args, dict):
            context['targets'] = [str(Path(context.get('workdir') or '.') / args[key])
                                  for key in ('file_path', 'path') if isinstance(args.get(key), str)]
        with self.state_lock:
            error = self.policy_error(name, args, context)
            if not error and name not in {'Read', 'Grep', 'Glob', 'opencode_task'}:
                token = uuid.uuid4().hex
                self.writers[token] = context
        try:
            yield error
        finally:
            if token:
                with self.state_lock:
                    self.writers.pop(token, None)

    def close(self):
        super().close()
        registry.remove_execution_guard(self)

    def approvals(self, session=None):
        return [self.approval(run, item)
                for run in self.runs() if session in (None, run['session']) and run['state'] not in FINAL
                for item in run.get('inputs', [])]

    @staticmethod
    def approval(run, item):
        return {'kind': 'user_input', 'request_id': item['id'], 'task_id': run['id'],
                'session_id': run['session'], 'tool': 'OpenCode',
                'input': {'title': 'OpenCode · ' + item.get('title', 'Вопрос'), 'opencode': item}}

    def resolve(self, run_id, request_id, response, cancelled=False):
        with self.state_lock:
            run = self.lookup(run_id)
            if not run or run['state'] in FINAL or request_id not in {i['id'] for i in run['inputs']}:
                raise ValueError('opencode_input_no_longer_pending')
            result = json.loads(self.request({'action': 'answer', 'run_id': run_id, 'request_id': request_id,
                                             'response': response or {}, 'cancelled': cancelled}))
            if result.get('error'):
                raise ValueError(str(result['error']))
            run['inputs'] = [i for i in run['inputs'] if i['id'] != request_id]
            self.save(run)
            self.event(run, 'approval.resolved', {'task_id': run_id, 'request_id': request_id,
                                                 'decision': 'cancel' if cancelled else 'allow_once'})
            return {'resolved': True}

    def event(self, run, kind, payload):
        seq = self.service.store.append_event(kind=kind, payload=payload, session_id=run['session'], task_id=run['id'])
        self.service._publish_event(kind=kind, payload=payload, session_id=run['session'], task_id=run['id'], seq=seq)

    def clear_inputs(self, run):
        for item in run.get('inputs', []):
            self.event(run, 'approval.resolved', {'task_id': run['id'], 'request_id': item['id'], 'decision': 'cancel'})
        run['inputs'] = []

    def message(self, run, identity, text):
        from .cli_chat import _append_entry, _session_path
        if identity in run['seen']:
            return
        text = 'OpenCode:\n' + text
        _append_entry(_session_path(run['session']), role='assistant', text=text, task_id=run['id'],
                      extra={'external_source': 'opencode', 'history_id': 'opencode-' + run['id'] + '-' + identity})
        self.event(run, 'opencode.message', {'message_id': run['id'] + '-' + identity, 'text': text})
        run['seen'].append(identity)

    def poll(self):
        if not self.allowed(None) or not self.state_lock.acquire(blocking=False):
            return
        try:
            for run in self.runs():
                if run['state'] not in FINAL and run['state'] != 'preparing':
                    snapshot = json.loads(self.request({'action': 'status', 'run_id': run['id'], 'seen': run['seen']}, timeout=8))
                    if snapshot.get('error'):
                        if snapshot['error'] == 'opencode_task_lost':
                            self.clear_inputs(run)
                            run.update(state='interrupted', inputs=[], result='OpenCode: сервис остановился. Автоматического повторного запуска не было.')
                        self.save(run)
                        continue
                    previous = {item['id'] for item in run['inputs']}
                    run['inputs'] = snapshot.get('inputs', [])
                    for item in run['inputs']:
                        if item['id'] not in previous:
                            self.event(run, 'approval.required', self.approval(run, item))
                    for old in previous - {item['id'] for item in run['inputs']}:
                        self.event(run, 'approval.resolved', {'task_id': run['id'], 'request_id': old, 'decision': 'allow_once'})
                    for item in snapshot.get('messages', []):
                        self.message(run, item['id'], item['text'])
                    outcome = snapshot.get('outcome')
                    if outcome in {'succeeded', 'failed', 'interrupted'} and not run['inputs']:
                        run['state'] = {'succeeded': 'completed', 'failed': 'failed', 'interrupted': 'interrupted'}[outcome]
                        run['result'] = 'Состояние OpenCode: ' + outcome + '\n' + (snapshot.get('result') or 'Текстовый ответ отсутствует.')
                        run['result'] += '\n\nИзменения уже находятся в исходном проекте; автоматического отката нет.'
                    self.save(run)
                if run['state'] in FINAL and not run['delivered']:
                    if not self.service._active_task_id_for_session(run['session']):
                        if self.service.deliver_opencode_result(run):
                            run['delivered'] = True
                            self.save(run)
        finally:
            self.state_lock.release()
