"""Responses wire adapter for the existing Igorek tool loop and trace stream."""
from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import threading
import time
from copy import deepcopy
from urllib.parse import urlsplit

from .cc_backend_trace import append_cc_backend_trace, model_failure_diagnostics, TLS_ERROR_REASONS
from .trace_taxonomy import CcTraceEvent

NAMESPACE = "igorek"
MAX_EVENT = 32 * 1024 * 1024
PROVIDER_ERROR_CODES = {
    'server_error', 'internal_server_error', 'model_error',
    'invalid_request_error', 'unsupported_parameter', 'unknown_parameter', 'invalid_value',
    'model_not_found', 'context_length_exceeded', 'rate_limit_exceeded', 'insufficient_quota',
    'invalid_api_key', 'invalid_token', 'access_denied',
    'subscription_sharing_usage_limit_exceeded', 'subscription_sharing_usage_unavailable',
}
ERROR_PARAMS = {'tool_choice', 'tool_choice.namespace', 'tool_choice.name', 'tools', 'input',
                'model', 'store', 'stream', 'include', 'instructions', 'reasoning'}
ERRORS = {
    "subscription_sharing_usage_limit_exceeded": "Лимит подписки ChatGPT исчерпан. Проверьте Usage в ChatGPT.",
    "subscription_sharing_usage_unavailable": "Использование подписки ChatGPT сейчас недоступно.",
    "invalid_api_key": "Вход OpenAI истёк. Войдите снова в настройках подключения.",
    "invalid_token": "Вход OpenAI истёк. Войдите снова в настройках подключения.",
    "model_not_found": "Модель недоступна этому аккаунту. Обновите список моделей.",
    "context_length_exceeded": "context length exceeded: требуется сокращение контекста.",
}


class RenewingHeaders(dict):
    """Resolve host credentials on each request, including long-running turns."""
    def __init__(self, base, provider):
        super().__init__(base)
        self.provider = provider

    def current(self):
        from .chatgpt_usage import ResponseHeaders
        value = self.provider()
        return ResponseHeaders({**self, **value}, getattr(value, 'response_observer', None))


def is_responses_url(url):
    return urlsplit(url).path.rstrip("/").endswith("/responses")


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def build_request(payload, *, generic=False):
    """Translate only supported fields; never forward Chat Completions knobs."""
    inputs, instructions = [], []
    if payload.get("system"):
        instructions.append(_text(payload["system"]))
    for message in payload.get("messages", []):
        role, content = message.get("role", "user"), message.get("content")
        if role in {"system", "developer"}:
            instructions.append(_text(content))
            continue
        if role == "tool":
            inputs.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                           "output": _text(content)})
            continue
        if role not in {"user", "assistant"}:
            raise ValueError("responses_invalid_role")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        raw = message.get("__responses_output")
        for block in blocks:
            if block.get("type") == "__responses_output":
                raw = block["output"]
        if raw:
            inputs.extend(deepcopy(raw))
        parts, results, calls = [], [], []
        raw_call_ids = {x.get("call_id") for x in (raw or []) if x.get("type") == "function_call"}
        for block in blocks:
            kind = block.get("type")
            if kind == "text" and not raw and block.get("text"):
                parts.append({"type": "output_text" if role == "assistant" else "input_text", "text": block["text"]})
            elif kind == "image":
                source = block.get("source", {})
                image = ("data:" + source["media_type"] + ";base64," + source["data"]
                         if source.get("type") == "base64" else source.get("url"))
                if image:
                    parts.append({"type": "input_image", "image_url": image})
            elif kind == "image_url":
                source = block["image_url"]
                parts.append({"type": "input_image", "image_url": source["url"], "detail": source.get("detail", "auto")})
            elif kind == "tool_use" and block["id"] not in raw_call_ids:
                calls.append({"type": "function_call", "call_id": block["id"],
                              **({} if generic else {"namespace": NAMESPACE}), "name": block["name"],
                              "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)})
            elif kind == "tool_result":
                result = block.get("content", "")
                if isinstance(result, list):
                    converted = []
                    for item in result:
                        if item.get("type") == "text":
                            converted.append({"type": "input_text", "text": item["text"]})
                        elif item.get("type") == "image" and item.get("source", {}).get("type") == "base64":
                            source = item["source"]
                            converted.append({"type": "input_image", "image_url": "data:" + source["media_type"] + ";base64," + source["data"]})
                    result = converted or _text(result)
                results.append({"type": "function_call_output", "call_id": block["tool_use_id"], "output": result})
        if parts:
            inputs.append({"role": role, "content": parts})
        for call in message.get("tool_calls") or []:
            if call["id"] not in raw_call_ids:
                calls.append({"type": "function_call", **({} if generic else {"namespace": NAMESPACE}), "call_id": call["id"],
                              "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
        inputs.extend(calls)
        inputs.extend(results)
    functions = []
    for tool in payload.get("tools") or []:
        function = tool.get("function", tool)
        functions.append({"type": "function", "name": function["name"],
                          "description": function.get("description", ""),
                          "parameters": function.get("parameters", function.get("input_schema", {"type": "object"})),
                          "strict": False})
    request = {"model": payload["model"], "input": inputs, "store": False, "stream": True}
    if not generic:
        request['include'] = ['reasoning.encrypted_content']
    if instructions:
        request["instructions"] = "\n\n".join(instructions)
    if functions:
        choice = payload.get("tool_choice")
        if isinstance(choice, dict):
            name = choice.get("name") or choice.get("function", {}).get("name")
            if not name or not any(function['name'] == name for function in functions):
                raise ValueError('responses_invalid_tool_choice')
            functions = [function for function in functions if function['name'] == name]
            # A single available function plus required preserves the forced-call
            # contract without sending the unsupported tool_choice.namespace field.
            request['tool_choice'] = 'required'
        elif choice in {'auto', 'required', 'none'}:
            request['tool_choice'] = choice
        request["tools"] = functions if generic else [{"type": "namespace", "name": NAMESPACE,
                             "description": "Igorek application tools", "tools": functions}]
    return request


def to_anthropic(response, names):
    if response.get("status") != "completed" or not isinstance(response.get("output"), list):
        raise ValueError("responses_not_completed")
    content, ids = [], set()
    for item in response["output"]:
        kind = item.get("type")
        if kind == "message":
            if item.get("role") != "assistant":
                raise ValueError("responses_invalid_output")
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    content.append({"type": "text", "text": part["text"]})
                elif part.get("type") == "refusal":
                    content.append({"type": "text", "text": part["refusal"]})
        elif kind == "reasoning":
            for part in item.get("summary", []):
                if part.get("text"):
                    content.append({"type": "__reasoning", "text": part["text"]})
        elif kind == "function_call":
            arguments = json.loads(item["arguments"])
            identity = item.get("call_id")
            if (item.get("namespace") not in {None, NAMESPACE} or item.get("name") not in names
                    or not isinstance(arguments, dict) or not isinstance(identity, str) or not identity
                    or identity in ids or item.get("status") not in {None, "completed"}):
                raise ValueError("responses_invalid_tool_call")
            ids.add(identity)
            content.append({"type": "tool_use", "id": identity, "name": item["name"], "input": arguments})
        else:
            raise ValueError("responses_unsupported_output")
    content.append({"type": "__responses_output", "output": deepcopy(response["output"])})
    return {"id": response.get("id", ""), "model": response.get("model", ""), "content": content,
            "stop_reason": "tool_use" if ids else "end_turn", "usage": response.get("usage") or {}}


def completed_response(response, output_items):
    """Combine finalized stream items with the terminal response envelope."""
    if not isinstance(response, dict) or response.get('status') not in {None, 'completed'}:
        raise ValueError('responses_not_completed')
    output = response.get('output')
    if output is not None and not isinstance(output, list):
        raise ValueError('responses_invalid_output')
    merged = dict(enumerate(output or []))
    # Finalized items are authoritative, including encrypted reasoning. A compact
    # response.completed event may contain no output items of its own.
    merged.update(output_items)
    return {**response, 'status': 'completed', 'output': [merged[index] for index in sorted(merged)]}


def _failure(error, status=None, request_id=None, *, generic=False):
    code = error.get("code") if isinstance(error, dict) else None
    details = {}
    if isinstance(status, int) and 100 <= status <= 599:
        details['http_status'] = status
    if isinstance(request_id, str) and re.fullmatch(r'req_[A-Za-z0-9]{8,128}', request_id):
        details['request_id'] = request_id
    if isinstance(error, dict):
        provider_code = code or error.get('type')
        if isinstance(provider_code, str) and provider_code in PROVIDER_ERROR_CODES:
            details['provider_error_code'] = provider_code
        if isinstance(error.get('param'), str) and error['param'] in ERROR_PARAMS:
            details['error_param'] = error['param']
    if generic and (status in {401, 403} or isinstance(code, str) and code in {'invalid_api_key', 'invalid_token', 'access_denied'}):
        return {'error': 'OpenCode отклонил запрос. Проверьте ключ и доступ к модели.', 'error_code': 'authentication', **details}
    if isinstance(code, str) and code in ERRORS:
        return {"error": ERRORS[code], "error_code": code, **details}
    if status in {401, 403}:
        return {"error": "Вход OpenAI отклонён. Войдите снова в настройках подключения.", "error_code": "authentication", **details}
    return {"error": ("Запрос OpenCode не завершён" if generic else "Запрос OpenAI не завершён") + (f" (HTTP {status})" if status else "") + ". Повторите попытку.",
            "error_code": "responses_failed", **details}


def _tls_failure_details(error):
    """Preserve safe native TLS codes without certificate subjects or raw messages."""
    kind = next((cls.__name__ for cls in (
        ssl.SSLCertVerificationError, ssl.SSLEOFError, ssl.SSLZeroReturnError,
        ssl.SSLWantReadError, ssl.SSLWantWriteError, ssl.SSLSyscallError,
    ) if isinstance(error, cls)), 'SSLError')
    reason = getattr(error, 'reason', None)
    return model_failure_diagnostics({'tls_error_type': kind,
        'tls_reason': reason if reason in TLS_ERROR_REASONS else 'OTHER',
        'tls_errno': error.errno, 'tls_verify_code': getattr(error, 'verify_code', None)})


def post(url, payload, headers, timeout_s, *, trace_dir=None, round_num=0, parent_task_id=None,
         cancel_check=None, log_fn=None):
    """Read SSE to a completed terminal event before exposing any tool calls."""
    start = time.monotonic()
    done, aborted = threading.Event(), threading.Event()
    target = urlsplit(url)
    connection = (http.client.HTTPSConnection(target.hostname, target.port, timeout=timeout_s)
                  if target.scheme == "https" else http.client.HTTPConnection(target.hostname, target.port, timeout=timeout_s))
    response = None
    io_socket = None
    phase = 'request_build'
    response_details = {}
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
        generic = bool(headers.get('__igorek_generic_responses__'))
        request = build_request(payload, generic=generic)
        phase = 'credentials'
        current = headers.current() if isinstance(headers, RenewingHeaders) else headers
        if generic:
            current = {key: value for key, value in current.items() if key != '__igorek_generic_responses__'}
        if cancel_check and cancel_check():
            return {"__interrupted__": True}, 0
        watcher.start()
        phase = 'connect'
        connection.request("POST", target.path, json.dumps(request, ensure_ascii=False).encode(),
                           {"Content-Type": "application/json", "Accept": "text/event-stream", **current})
        response = connection.getresponse()
        observer = getattr(current, 'response_observer', None)
        if observer:
            try:
                observer(headers={f'x-codex-{name}-{field}': response.getheader(f'x-codex-{name}-{field}')
                    for name in ('primary', 'secondary') for field in ('used-percent', 'window-minutes', 'reset-at')})
            except Exception:
                pass
        phase = 'http_body'
        media_type = (response.getheader('Content-Type') or '').split(';', 1)[0].strip().lower()
        response_details = {'http_status': response.status, 'response_content_type':
            media_type if media_type in {'text/event-stream', 'application/json', 'text/html', 'text/plain',
                                        'application/octet-stream'} else 'other' if media_type else 'missing'}
        request_id=response.getheader('x-request-id')
        from .diagnostics import transport_failure
        retry_details = transport_failure(status=response.status, phase=phase, request_id=request_id or '', retry_after=response.getheader('retry-after'))
        if retry_details['diagnostic'].get('retry_after_s'):
            response_details['retry_after_s'] = retry_details['diagnostic']['retry_after_s']
        if isinstance(request_id,str) and re.fullmatch(r'req_[A-Za-z0-9]{8,128}',request_id):response_details['request_id']=request_id
        io_socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if response.status != 200:
            try:
                error = json.loads(response.read(65536)).get("error")
            except Exception:
                error = None
            return {**_failure(error, response.status, response.getheader('x-request-id'), generic=generic), 'transport_phase': phase,
                **({'retry_after_s':response_details['retry_after_s']} if 'retry_after_s' in response_details else {})}, int((time.monotonic() - start) * 1000)
        # Validate the actual SSE envelope, not the advisory media type. Some
        # gateways preserve SSE events while replacing or omitting this header.
        phase = 'sse_read'
        data, size, seen_sse = [], 0, False
        output_items, output_bytes = {}, 0
        while not aborted.is_set():
            line = response.readline(MAX_EVENT + 1)
            if not line:
                if data:
                    line = b"\n"
                else:
                    break
            size += len(line)
            if size > MAX_EVENT:
                raise ValueError("responses_event_too_large")
            if line.strip():
                if not seen_sse and line.lstrip().startswith(b'{'):
                    phase = 'http_body'
                    raw = line + response.read(max(0, 65537 - len(line)))
                    if len(raw) <= 65536:
                        value = json.loads(raw)
                        if isinstance(value, dict) and isinstance(value.get('error'), dict):
                            return {**_failure(value['error'], response.status, response.getheader('x-request-id'), generic=generic),
                                    **response_details, 'transport_phase': phase}, int((time.monotonic() - start) * 1000)
                    raise ValueError('responses_invalid_content_type')
                if not (line.startswith(b':') or line.split(b':', 1)[0].rstrip(b'\r\n') in
                        {b'data', b'event', b'id', b'retry'}):
                    raise ValueError('responses_invalid_sse_envelope')
                seen_sse = True
                if line.startswith(b"data:"):
                    data.append(line[5:].lstrip().rstrip(b"\r\n"))
                continue
            if not data:
                size = 0
                continue
            phase = 'sse_parse'
            if b'\n'.join(data) == b'[DONE]':
                break
            event = json.loads(b"\n".join(data))
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise ValueError('responses_invalid_sse_envelope')
            event_bytes = size
            data, size = [], 0
            kind = event.get("type")
            if kind == "codex.rate_limits" and observer:
                try:
                    observer(event=event)
                except Exception:
                    pass
            if kind == 'response.output_item.done':
                index, item = event.get('output_index', len(output_items)), event.get('item')
                if (type(index) is not int or index < 0 or not isinstance(item, dict)
                        or index in output_items and output_items[index] != item):
                    raise ValueError('responses_invalid_output')
                if index not in output_items:
                    output_bytes += event_bytes
                    if output_bytes > MAX_EVENT:
                        raise ValueError('responses_event_too_large')
                    output_items[index] = item
            if kind == "response.completed":
                if aborted.is_set() or cancel_check and cancel_check():
                    return {"__interrupted__": True}, int((time.monotonic() - start) * 1000)
                phase = 'response_validation'
                names = ({tool['name'] for tool in request.get('tools', [])} if generic else
                         {f['name'] for tool in request.get('tools', []) for f in tool['tools']})
                body = to_anthropic(completed_response(event.get('response'), output_items), names)
                return body, int((time.monotonic() - start) * 1000)
            if kind in {"response.failed", "response.incomplete", "error"}:
                terminal_details={'terminal_event':kind}
                terminal_response=event.get('response')
                incomplete=terminal_response.get('incomplete_details') if isinstance(terminal_response,dict) else None
                if isinstance(incomplete,dict) and isinstance(incomplete.get('reason'),str) and incomplete['reason'] in {'max_output_tokens','content_filter'}:
                    terminal_details['incomplete_reason']=incomplete['reason']
                return {**_failure(event.get("response", {}).get("error") or event.get("error") or event,
                    request_id=response.getheader('x-request-id'), generic=generic), **response_details,
                    'transport_phase': 'response_terminal',**terminal_details}, int((time.monotonic() - start) * 1000)
            if trace_dir and kind in {"response.output_text.delta", "response.reasoning_summary_text.delta"}:
                text = event.get("delta")
                if isinstance(text, str) and text:
                    append_cc_backend_trace(trace_dir, {"kind": CcTraceEvent.MODEL_TEXT_DELTA if kind == "response.output_text.delta" else CcTraceEvent.MODEL_REASONING_DELTA,
                        "round": round_num + 1, "text": text, "parent_task_id": parent_task_id or None})
            phase = 'sse_read'
        if cancel_check and cancel_check():
            return {"__interrupted__": True}, int((time.monotonic() - start) * 1000)
        return {"error": "Соединение OpenAI прервалось до завершения ответа. Действия из незавершённого ответа не выполнялись.",
                "error_code": "responses_incomplete_stream", **response_details}, int((time.monotonic() - start) * 1000)
    except Exception as error:
        if cancel_check and cancel_check():
            return {"__interrupted__": True}, int((time.monotonic() - start) * 1000)
        code = ('responses_tls' if isinstance(error, ssl.SSLError) else
                'responses_timeout' if isinstance(error, TimeoutError) else
                'responses_network' if isinstance(error, (OSError, http.client.HTTPException)) else
                'responses_invalid_response' if isinstance(error, (ValueError, TypeError, KeyError)) else
                'responses_transport')
        details = {**response_details, 'transport_phase': phase}
        from .diagnostics import transport_failure
        details['diagnostic'] = transport_failure(error, phase=phase)['diagnostic']
        error_message = 'Не удалось завершить запрос OpenAI. Проверьте вход и соединение.'
        if isinstance(error, ssl.SSLError):
            details.update(_tls_failure_details(error))
            error_message = ('Сертификат TLS-соединения с OpenAI не прошёл проверку.'
                if isinstance(error, ssl.SSLCertVerificationError)
                else 'Не удалось установить TLS-соединение с OpenAI.')
        if isinstance(error, ValueError) and error.args and isinstance(error.args[0], str) and error.args[0] in {
            'responses_invalid_tool_choice', 'responses_invalid_tool_call', 'responses_invalid_output',
            'responses_unsupported_output', 'responses_not_completed', 'responses_invalid_content_type',
            'responses_event_too_large', 'responses_invalid_role', 'responses_invalid_sse_envelope'}:
            details['validation_code'] = error.args[0]
        return {"error": error_message,
                "error_code": code, **details}, int((time.monotonic() - start) * 1000)
    finally:
        done.set()
        if response:
            response.close()
        connection.close()
        if watcher.is_alive():
            watcher.join(0.5)
