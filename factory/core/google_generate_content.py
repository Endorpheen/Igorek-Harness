from __future__ import annotations

from copy import deepcopy
import http.client
import json
import socket
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4


def build_request(payload):
    contents, call_names = [], {}
    for message in payload.get('messages', []):
        role = message.get('role')
        if role not in {'user', 'assistant'}:
            raise ValueError('google_invalid_role')
        blocks = message.get('content')
        blocks = blocks if isinstance(blocks, list) else [{'type': 'text', 'text': blocks or ''}]
        raw = next((block.get('parts') for block in blocks if block.get('type') == '__google_parts'), None)
        if raw is not None and role == 'assistant':
            for block in blocks:
                if block.get('type') == 'tool_use':
                    call_names[block['id']] = block['name']
            parts = deepcopy(raw)
        else:
            parts = []
            for block in blocks:
                kind = block.get('type')
                if kind == 'text' and block.get('text'):
                    parts.append({'text': block['text']})
                elif kind == 'image' and block.get('source', {}).get('type') == 'base64':
                    source = block['source']
                    parts.append({'inlineData': {'mimeType': source['media_type'], 'data': source['data']}})
                elif kind == 'image_url':
                    source = block.get('image_url', {}).get('url', '')
                    prefix, separator, data = source.partition(';base64,')
                    if not separator or not prefix.startswith('data:image/') or not data:
                        raise ValueError('google_unsupported_image')
                    parts.append({'inlineData': {'mimeType': prefix[5:], 'data': data}})
                elif kind == 'tool_use':
                    call_names[block['id']] = block['name']
                    parts.append({'functionCall': {'name': block['name'], 'args': block.get('input', {})}})
                elif kind == 'tool_result':
                    name = call_names.get(block.get('tool_use_id'))
                    if not name:
                        raise ValueError('google_unknown_tool_result')
                    result = block.get('content', '')
                    parts.append({'functionResponse': {'name': name, 'response': {'result': result}}})
                elif kind not in {'__reasoning', '__google_parts'}:
                    raise ValueError('google_unsupported_content')
        if parts:
            contents.append({'role': 'model' if role == 'assistant' else 'user', 'parts': parts})
    declarations = []
    for tool in payload.get('tools') or []:
        function = tool.get('function', tool)
        declarations.append({'name': function['name'], 'description': function.get('description', ''),
                             'parametersJsonSchema': function.get('parameters', function.get('input_schema', {'type': 'object'}))})
    request = {'contents': contents, 'generationConfig': {'maxOutputTokens': payload.get('max_tokens', 4000)}}
    if payload.get('system'):
        request['systemInstruction'] = {'parts': [{'text': payload['system']}]}
    if declarations:
        request['tools'] = [{'functionDeclarations': declarations}]
        choice = payload.get('tool_choice')
        if isinstance(choice, dict):
            name = choice.get('name') or choice.get('function', {}).get('name')
            if not name or not any(item['name'] == name for item in declarations):
                raise ValueError('google_invalid_tool_choice')
            request['toolConfig'] = {'functionCallingConfig': {'mode': 'ANY', 'allowedFunctionNames': [name]}}
        elif choice in {'required', 'none'}:
            request['toolConfig'] = {'functionCallingConfig': {'mode': 'ANY' if choice == 'required' else 'NONE'}}
    return request


def to_anthropic(response, names):
    candidates = response.get('candidates')
    if not isinstance(candidates, list) or not candidates:
        raise ValueError('google_missing_candidate')
    candidate = candidates[0]
    if not isinstance(candidate, dict) or candidate.get('finishReason') not in {None, 'STOP', 'MAX_TOKENS'}:
        raise ValueError('google_candidate_incomplete')
    parts = candidate.get('content', {}).get('parts')
    if not isinstance(parts, list):
        raise ValueError('google_missing_content')
    content, called = [], False
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError('google_invalid_part')
        if isinstance(part.get('text'), str):
            content.append({'type': '__reasoning' if part.get('thought') else 'text', 'text': part['text']})
        if 'functionCall' in part:
            call = part['functionCall']
            if not isinstance(call, dict) or call.get('name') not in names or not isinstance(call.get('args', {}), dict):
                raise ValueError('google_invalid_tool_call')
            content.append({'type': 'tool_use', 'id': 'call_' + uuid4().hex, 'name': call['name'], 'input': call.get('args', {})})
            called = True
    if called and candidate.get('finishReason') != 'STOP':
        raise ValueError('google_incomplete_tool_call')
    content.append({'type': '__google_parts', 'parts': deepcopy(parts)})
    raw_usage = response.get('usageMetadata')
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}
    usage = {target: value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
             for target, value in [('input_tokens', raw_usage.get('promptTokenCount')),
                                   ('output_tokens', raw_usage.get('candidatesTokenCount'))]}
    stop = 'tool_use' if called else 'max_tokens' if candidate.get('finishReason') == 'MAX_TOKENS' else 'end_turn'
    return {'content': content, 'stop_reason': stop, 'usage': usage}


def post_stream(url, payload, headers, timeout_s, *, trace_dir=None, round_num=0, parent_task_id=None, cancel_check=None):
    from .cc_backend_trace import append_cc_backend_trace
    from .diagnostics import safe_text, transport_failure
    from .trace_taxonomy import CcTraceEvent
    start = time.monotonic()
    done, aborted = threading.Event(), threading.Event()
    parsed = urlsplit(url)
    connection = (http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=timeout_s)
                  if parsed.scheme == 'https' else http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout_s))
    io_socket = None
    def watch():
        while not done.wait(0.1):
            if time.monotonic() - start >= timeout_s or cancel_check and cancel_check():
                aborted.set()
                sock = io_socket or connection.sock
                if sock:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                connection.close()
                return
    watcher = threading.Thread(target=watch, daemon=True)
    try:
        data = json.dumps(build_request(payload), ensure_ascii=False).encode()
        outgoing = {key: value for key, value in headers.items() if key != '__igorek_google_mode__'}
        path = parsed.path.removesuffix(':generateContent') + ':streamGenerateContent?alt=sse'
        watcher.start()
        connection.request('POST', path, data, outgoing)
        reply = connection.getresponse()
        io_socket = getattr(getattr(getattr(reply, 'fp', None), 'raw', None), '_sock', None)
        if reply.status != 200:
            body = reply.read(65536).decode('utf-8', errors='replace')
            return {'error': f'HTTP {reply.status}: {safe_text(body)}', **transport_failure(status=reply.status, phase='http_body')}, int((time.monotonic() - start) * 1000)
        parts, usage, finish, lines = [], {}, None, []
        size, total = 0, 0
        while not aborted.is_set():
            line = reply.readline(32 * 1024 * 1024 + 1)
            if not line:
                if lines:
                    line = b'\n'
                else:
                    break
            size += len(line)
            total += len(line)
            if size > 32 * 1024 * 1024 or total > 32 * 1024 * 1024:
                raise ValueError('google_event_too_large')
            if line.strip():
                if line.startswith(b'data:'):
                    lines.append(line[5:].strip())
                elif not (line.startswith(b':') or line.startswith((b'event:', b'id:', b'retry:'))):
                    raise ValueError('google_invalid_sse')
                continue
            if not lines:
                size = 0
                continue
            raw_event = b'\n'.join(lines)
            lines, size = [], 0
            if raw_event == b'[DONE]':
                break
            event = json.loads(raw_event)
            if not isinstance(event, dict):
                raise ValueError('google_invalid_event')
            if event.get('error'):
                raise ValueError('google_stream_error')
            if isinstance(event.get('usageMetadata'), dict):
                usage = event['usageMetadata']
            for candidate in event.get('candidates', []):
                if candidate.get('index', 0) != 0:
                    continue
                if candidate.get('finishReason'):
                    finish = candidate['finishReason']
                for part in candidate.get('content', {}).get('parts', []):
                    parts.append(part)
                    if trace_dir and isinstance(part.get('text'), str):
                        append_cc_backend_trace(trace_dir, {'kind': CcTraceEvent.MODEL_REASONING_DELTA if part.get('thought') else CcTraceEvent.MODEL_TEXT_DELTA,
                            'round': round_num + 1, 'text': part['text'], 'parent_task_id': parent_task_id or None})
        if aborted.is_set():
            if cancel_check and cancel_check():
                return {'__interrupted__': True}, int((time.monotonic() - start) * 1000)
            raise TimeoutError('google_stream_timeout')
        if finish is None:
            raise ValueError('google_stream_incomplete')
        names = {str(tool.get('name') or tool.get('function', {}).get('name') or '') for tool in payload.get('tools', [])}
        body = to_anthropic({'candidates': [{'content': {'parts': parts}, 'finishReason': finish}], 'usageMetadata': usage}, names)
        return body, int((time.monotonic() - start) * 1000)
    except Exception as exc:
        if aborted.is_set() and cancel_check and cancel_check():
            return {'__interrupted__': True}, int((time.monotonic() - start) * 1000)
        return {'error': 'google_stream_error: ' + safe_text(exc), **transport_failure(exc)}, int((time.monotonic() - start) * 1000)
    finally:
        done.set()
        connection.close()
        if watcher.is_alive():
            watcher.join(timeout=1)
