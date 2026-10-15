import json
import threading
import secrets
import logging
import time
from copy import deepcopy
from urllib.error import HTTPError
from urllib.parse import urlsplit

from factory.core.mcp_bridge import MCPClient
from factory.core.vault.lifecycle import is_loopback_provider
from factory.core.prompt_assembly import private_mcp_prompt
from .access import McpAccess
from .transport import post_json, ProbeFailure


class PrivateMcp:
    def __init__(self, root, lifecycle, gmail=None, access=None):
        self.gmail = gmail
        from .calendar import CalendarConnection
        self.calendar = CalendarConnection(lifecycle)
        self.access = access or McpAccess(root, lifecycle)
        self.lifecycle = lifecycle
        from factory.core.vault.lifecycle import VaultLifecycle
        from factory.core.vault.consumers import McpValueAccess
        self.values = McpValueAccess(root, lifecycle, self.access) if isinstance(lifecycle, VaultLifecycle) else None
        self.value_binding = None
        self.context_binding = None
        self.job = None
        self.task_id = None
        self.consumer_check = lambda: True
        self.binding = None
        self.history = []
        self.thread = None
        self.cancel = threading.Event()
        self.mutex = threading.Lock()
        self.approval_event = threading.Event()
        self.approval_id = None
        self.approval_decision = None

    def _approve(self, preview, allowed=lambda: True):
        identity = secrets.token_hex(16)
        with self.mutex:
            if self.cancel.is_set() or self.job is None:
                return False
            self.approval_event.clear()
            self.approval_id = identity
            self.approval_decision = None
            self.job['phase'] = 'Ожидаю подтверждения изменения календаря'
            self.job['approval'] = {'request_id':identity, **preview}
        deadline = time.monotonic() + 180
        while not self.approval_event.wait(.25):
            if not allowed() or time.monotonic() >= deadline:
                break
        with self.mutex:
            approved = self.approval_decision is True and allowed()
            self.approval_id = None
            if self.job is not None:
                self.job.pop('approval',None)
        return approved

    def approve(self, request_id, decision):
        with self.mutex:
            if decision not in {'approve','reject'} or self.cancel.is_set() or not self.approval_id or request_id != self.approval_id or self.approval_event.is_set():
                raise ValueError('Approval expired')
            self.approval_decision = decision == 'approve'
            self.approval_event.set()
        return self.status()

    def start(self, credential_id, server, message, consumer_check=None, continuation=False):
        if self.thread and self.thread.is_alive():
            raise ValueError('private_busy')
        profile = next((p for p in self.lifecycle.list_provider_profiles() if p['credential_id'] == credential_id), None)
        if profile is None or profile['status'] not in {'verified', 'configured'}:
            raise ValueError('profile_not_verified')
        config = deepcopy(profile['config'])
        if config['api_format'] != 'openai':
            raise ValueError('private_openai_format_required')
        if not self.access.authorize(server, config, private=True):
            raise ValueError('mcp_access_denied')
        signature = self.values.snapshot(server, credential_id, config)[1] if self.values and server in self.values._servers() else None
        context_binding = (server, config, signature)
        if not continuation or self.context_binding != context_binding:
            self.history = []
        self.context_binding = context_binding
        self.value_binding = (server, credential_id, config, signature) if signature is not None else None
        self.binding = (server, config)
        self.task_id = secrets.token_hex(16)
        self.consumer_check = consumer_check or (lambda: True)
        self.cancel.clear()
        self.job = {'running': True, 'result': '', 'error': '', 'phase':'Подключение MCP'}
        self.thread = threading.Thread(target=self._run, args=(server, config, credential_id, message, deepcopy(self.history), signature), daemon=True)
        self.thread.start()

    def _run(self, server, config, credential_id, message, history, signature=None):
        client = None
        stage = 'mcp_connect'
        def allowed():
            try:
                return not self.cancel.is_set() and self.consumer_check() and self.access.authorize(server, config, private=True) \
                    and (signature is None or self.values.snapshot(server, credential_id, config)[1] == signature)
            except Exception:
                return False
        try:
            entry = self.access.registry()[server]
            if entry.get('adapter') == 'gmail':
                from .gmail import GmailClient
                client = GmailClient(self.gmail, lambda name: allowed())
            elif entry.get('adapter') == 'openai_docs':
                from .remote import RemoteMcpClient
                client = RemoteMcpClient(allowed)
            elif entry.get('adapter') == 'google_calendar':
                from .calendar import CalendarClient
                client = CalendarClient(self.calendar, allowed, lambda preview:self._approve(preview,allowed))
            else:
                if entry.get('type', 'stdio') != 'stdio' or not isinstance(entry.get('command'), str):
                    raise ValueError()
                value_env = self.values.issue(server, credential_id, config)[0] if signature is not None else {}
                client = MCPClient(server, entry['command'], entry.get('args', []), {**entry.get('env', {}), **value_env}, access_check=lambda name: allowed(), inherit_environment=False)
            client.start()
            schemas = []
            names = set()
            for tool in client.tools:
                name = tool.get('name')
                if not isinstance(name, str) or not 1 <= len(name) <= 64 or not all(c.isascii() and (c.isalnum() or c in '_-') for c in name) or name in names:
                    raise ValueError()
                names.add(name)
                schemas.append({'type': 'function', 'function': {'name': name, 'description': str(tool.get('description', ''))[:4000], 'parameters': tool.get('inputSchema', {'type':'object','properties':{}})}})
            messages = [{'role':'system','content':private_mcp_prompt(server)}, *history, {'role':'user','content':message}]
            target = urlsplit(config['base_url'])
            opencode_go = target.hostname == 'opencode.ai' and target.path.rstrip('/') == '/zen/go/v1'
            disable_thinking = config['model'].rsplit('/', 1)[-1].lower().startswith('deepseek-v4')
            for _ in range(6):
                if not allowed():
                    raise PermissionError()
                with self.mutex:
                    if self.job is not None:
                        self.job['phase'] = 'Модель обрабатывает задачу'
                stage = 'model_auth'
                headers = dict(self.lifecycle.provider_runtime_headers(credential_id, config, 'openai'))
                if opencode_go:
                    headers['x-opencode-session'] = 'igorek-mcp-' + self.task_id
                payload = {'model':config['model'],'messages':messages,'tools':schemas,'max_tokens':2000,'stream':False}
                if disable_thinking:
                    payload['thinking'] = {'type':'disabled'}
                stage = 'model_request'
                response = post_json(config['base_url'].rstrip('/') + '/chat/completions', payload, headers)
                if not allowed():
                    raise PermissionError()
                stage = 'model_response'
                if response.get('error'):
                    raise ProbeFailure('provider_error')
                if response['choices'][0].get('finish_reason') == 'length':
                    raise ProbeFailure('output_limit')
                answer = response['choices'][0]['message']
                calls = answer.get('tool_calls') or []
                if not calls:
                    content = answer.get('content')
                    if not isinstance(content, str) or len(content) > 65536:
                        raise ValueError()
                    with self.mutex:
                        if not allowed():
                            raise PermissionError()
                        self.history = history + [{'role':'user','content':message}, {'role':'assistant','content':content}]
                        while len(self.history) > 12 or sum(len(item['content']) for item in self.history) > 131072:
                            self.history = self.history[2:]
                        self.job = {'running':False,'result':content,'error':''}
                    return
                if len(calls) > 4:
                    raise ValueError()
                assistant = {'role':'assistant','content':answer.get('content'), 'tool_calls':calls}
                if 'reasoning_content' in answer:
                    reasoning = answer['reasoning_content'] if answer['reasoning_content'] is not None else ''
                    if not isinstance(reasoning, str) or len(reasoning) > 65536:
                        raise ValueError()
                    assistant['reasoning_content'] = reasoning
                messages.append(assistant)
                for call in calls:
                    function = call['function']
                    if function['name'] not in names or not allowed():
                        raise PermissionError()
                    arguments = json.loads(function['arguments'])
                    if not isinstance(arguments, dict):
                        raise ValueError()
                    with self.mutex:
                        if self.job is not None:
                            self.job['phase'] = 'Поиск писем' if function['name'] == 'gmail_search' else 'Чтение писем' if function['name'] in {'gmail_read','gmail_latest'} else 'Вызов MCP: ' + function['name']
                    stage = 'mcp_call'
                    result = client.call_tool(function['name'], arguments)
                    if not allowed() or len(result) > 65536:
                        raise PermissionError()
                    messages.append({'role':'tool','tool_call_id':call['id'],'content':result})
            raise ProbeFailure('turn_limit')
        except Exception as error:
            reason = 'internal_error'
            code = None
            if isinstance(error, ProbeFailure):
                reason, code = error.reason, error.http_status
            elif isinstance(error, HTTPError):
                code = error.code
                reason = 'authentication' if code in {401,403} else 'rate_limit' if code == 429 else 'http_error'
            elif isinstance(error, PermissionError):
                reason = 'access_denied'
            elif isinstance(error, TimeoutError):
                reason = 'timeout'
            elif isinstance(error, OSError):
                reason = 'network'
            elif isinstance(error, (ValueError,TypeError,KeyError,IndexError)):
                reason = 'invalid_response'
            if reason not in {'authentication','rate_limit','http_error','access_denied','timeout','network','invalid_response','provider_error','output_limit','turn_limit'}:
                reason = 'internal_error'
            failure = {'stage':stage,'reason':reason}
            if type(code) is int and 100 <= code <= 599:
                failure['http_status'] = code
            logging.getLogger(__name__).warning('MCP task failed: task_id=%s stage=%s reason=%s http_status=%s', self.task_id, stage, reason, failure.get('http_status'))
            with self.mutex:
                self.history = []
                self.job = {'running':False,'result':'','error':'private_request_failed','failure':failure}
        finally:
            if client:
                client.stop()

    def status(self):
        with self.mutex:
            try:
                values_valid = self.value_binding is None or self.values.snapshot(*self.value_binding[:3])[1] == self.value_binding[3]
            except Exception:
                values_valid = False
            if self.binding and (not values_valid or not self.consumer_check() or not self.access.authorize(*self.binding, private=True)):
                self.cancel.set()
                self.history = []
                return {'task_id':self.task_id,'running':False,'result':'','error':'mcp_access_denied'}
            return {'task_id':self.task_id, **dict(self.job or {'running':False,'result':'','error':''})}

    def clear(self):
        self.cancel.set()
        self.approval_event.set()
        with self.mutex:
            self.job = None
            self.binding = None
            self.value_binding = None
            self.context_binding = None
            self.task_id = None
            self.history = []
            self.approval_id = None
            self.approval_decision = None
