from __future__ import annotations

import json
import re
import time
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def model_failure_diagnostics(body) -> dict:
    if not isinstance(body,dict):return {}
    result = {}
    allowed = {
        'error_code': {'responses_failed','responses_network','responses_timeout','responses_tls','responses_invalid_response','responses_incomplete_stream','responses_transport','authentication','subscription_sharing_usage_limit_exceeded','subscription_sharing_usage_unavailable','invalid_api_key','invalid_token','model_not_found','context_length_exceeded'},
        'provider_error_code': {'server_error','internal_server_error','model_error','invalid_request_error','unsupported_parameter','unknown_parameter','invalid_value','model_not_found','context_length_exceeded','rate_limit_exceeded','insufficient_quota','invalid_api_key','invalid_token','access_denied','subscription_sharing_usage_limit_exceeded','subscription_sharing_usage_unavailable'},
        'transport_phase': {'request_build','credentials','connect','http_body','sse_read','sse_parse','response_validation','response_terminal'},
        'terminal_event': {'response.failed','response.incomplete','error'},
        'incomplete_reason': {'max_output_tokens','content_filter'},
        'validation_code': {'responses_invalid_tool_choice','responses_invalid_tool_call','responses_invalid_output','responses_invalid_content_type','responses_event_too_large','responses_invalid_role','responses_invalid_sse_envelope','responses_not_completed','responses_unsupported_output'},
        'response_content_type': {'text/event-stream','application/json','text/html','text/plain','application/octet-stream','other','missing'},
        'error_param': {'tool_choice','tool_choice.namespace','tool_choice.name','tools','input','model','store','stream','include','instructions','reasoning'},
    }
    for key,values in allowed.items():
        if isinstance(body.get(key),str) and body[key] in values:result[key]=body[key]
    if type(body.get('http_status')) is int and 100<=body['http_status']<=599:result['http_status']=body['http_status']
    if isinstance(body.get('request_id'),str) and re.fullmatch(r'req_[A-Za-z0-9]{8,128}',body['request_id']):result['request_id']=body['request_id']
    return result


def browser_tool_diagnostics(tool_input,result_text) -> dict:
    result = {}
    actions={'status','open','read','scroll','post','collect','result','elements','click','hover','fill','press','select','check','scroll_element','wait','read_post','comments','close_menu','screenshot'}
    if isinstance(tool_input,dict) and isinstance(tool_input.get('action'),str) and tool_input['action'] in actions:result['action']=tool_input['action']
    target=tool_input.get('target') if isinstance(tool_input,dict) else None
    if isinstance(target,str) and re.fullmatch(r'r-[a-f0-9]{32}-\d{1,6}',target):result['target_ref']=target
    try:
        if not isinstance(result_text,str) or len(result_text)>2_000_000:return result
        body=json.loads(result_text)
        if not isinstance(body,dict):return result
    except (ValueError,TypeError):return result
    allowed={
        'error':{'browser_needs_owner','desktop_locked','browser_closed','invalid_request','invalid_url','public_page_required','navigation_failed','browser_operation_failed','browser_queue_full','invalid_page','stale_target','target_obscured','protected_input','target_disabled','target_not_found','unsupported_target','invalid_target','target_mismatch','wait_timeout','page_state_unavailable','stale_cursor','unknown_post','no_run','screenshot_failed','menu_not_closed','invalid_value','login_required','challenge'},
        'auth':{'ready','not_checked','unknown','login_required','challenge'},
        'reason':{'manual_login','login_required','challenge','navigation_failed','controls_failed','browser_crashed','interrupted','wait_timeout','page_state_unavailable'},
    }
    for key,values in allowed.items():
        if isinstance(body.get(key),str) and body[key] in values:result[key]=body[key]
    for key in ('paused','manual'):
        if type(body.get(key)) is bool:result[key]=body[key]
    try:
        parsed=urlsplit(body.get('url',''))
        if parsed.scheme=='https' and parsed.hostname and len(parsed.hostname)<=253 and not parsed.username and not parsed.password:result['origin']='https://'+parsed.hostname
    except (ValueError,TypeError,AttributeError):pass
    navigation=body.get('navigation')
    if isinstance(navigation,dict):
        result['navigation']={key:navigation[key] for key in ('committed','document_ready','recovered') if type(navigation.get(key)) is bool}
        if isinstance(navigation.get('phase'),str) and navigation['phase'] in {'loading','document_probe','read','complete','failed'}:result['navigation']['phase']=navigation['phase']
        for key in ('error_code','warning_code'):
            code=navigation.get(key)
            if isinstance(code,str) and (code in {'NAVIGATION_TIMEOUT','PAGE_TIMEOUT','TIME_LIMIT','DOCUMENT_UNAVAILABLE'} or re.fullmatch(r'ERR_[A-Z_]{1,50}',code)):result['navigation'][key]=code
    return result


_AGENT_TRACE = ContextVar("agent_trace", default={})


def agent_trace_context() -> dict[str, Any]:
    return dict(_AGENT_TRACE.get())


def agent_session_id(subagent_id: str) -> str:
    return "agent-" + subagent_id


def scoped_agent_trace(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        inherited = agent_trace_context()
        root = str(kwargs.get("current_session") or inherited.get("root_session_id") or "")
        subagent = str(kwargs.get("subagent_id") or "")
        context = {
            "root_session_id": root,
            "agent_session_id": agent_session_id(subagent) if subagent else root,
            "parent_session_id": inherited.get("agent_session_id", root) if subagent else None,
            "subagent_id": subagent,
        }
        token = _AGENT_TRACE.set(context)
        try:
            return function(*args, **kwargs)
        finally:
            _AGENT_TRACE.reset(token)
    return wrapped


TRACE_FILENAME = "cc_backend_trace.jsonl"


def _trace_path(workdir: str) -> Path:
    return Path(workdir) / TRACE_FILENAME


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def append_cc_backend_trace(workdir: str, event: dict[str, Any]) -> None:
    payload = {
        "ts": int(time.time()),
        **(_AGENT_TRACE.get() if _AGENT_TRACE.get().get("subagent_id") else {}),
        **{str(key): _json_safe(value) for key, value in event.items()},
    }
    path = _trace_path(workdir)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
