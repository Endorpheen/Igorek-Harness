"""Check model connectivity with one ordinary text request."""
import http.client
import json
import secrets
import socket
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from factory.core.prompt_assembly import provider_probe_prompt
from provider_setup import profile_config
from factory.core.vault.lifecycle import is_loopback_provider

MAX_RESPONSE = 1024 * 1024
OPENAI_REALTIME_BASE = "https://api.openai.com/v1"


def is_openai_realtime(config):
    """Voice profiles use a realtime model, which has no chat/completions route."""
    base = str(config.get("base_url") or "").rstrip("/")
    model = str(config.get("model") or "").rsplit("/", 1)[-1].lower()
    return config.get("api_format") == "openai" and base == OPENAI_REALTIME_BASE and model.startswith("gpt-realtime")


class ProbeFailure(Exception):
    def __init__(self, reason, http_status=None, details=None):
        self.reason = reason
        self.http_status = http_status
        self.details = details or {}


def post_json(url, payload, headers):
    target = urlsplit(url)
    connection = (http.client.HTTPSConnection(target.hostname, target.port, timeout=10,
                  context=ssl.create_default_context()) if target.scheme == "https"
                  else http.client.HTTPConnection(target.hostname, target.port, timeout=10))
    response = None
    try:
        connection.request("POST", target.path or "/", json.dumps(payload).encode(),
                           {"Content-Type": "application/json", **headers})
        response = connection.getresponse()
        if response.status != 200:
            code = response.status
            reason = "authentication" if code in {401, 403} else "rate_limit" if code == 429 else "http_error"
            raise ProbeFailure(reason, code)
        deadline = time.monotonic() + 10
        chunks, length = [], 0
        while True:
            if time.monotonic() >= deadline:
                raise ProbeFailure("timeout")
            chunk = response.read1(min(65536, MAX_RESPONSE + 1 - length))
            if not chunk:
                break
            chunks.append(chunk); length += len(chunk)
            if length > MAX_RESPONSE:
                raise ProbeFailure("invalid_response")
        result = json.loads(b"".join(chunks))
        if not isinstance(result, dict):
            raise ProbeFailure("invalid_response")
        return result
    except (socket.timeout, TimeoutError):
        raise ProbeFailure("timeout") from None
    except (ssl.SSLError, OSError, http.client.HTTPException):
        raise ProbeFailure("network") from None
    except (ValueError, UnicodeError):
        raise ProbeFailure("invalid_response") from None
    finally:
        if response is not None:
            response.close()
        connection.close()


def check_connection(config, api_key, post=post_json):
    checked_at = datetime.now(timezone.utc).isoformat()
    connected = False
    try:
        profile_config(config["name"], config["model"], config["base_url"], config["api_format"])
        if config['api_format'] == 'openai-responses' and post is post_json:
            from factory.core.openai_responses import post as responses_post
            def responses_probe(url, payload, headers):
                body, _ = responses_post(url, payload, headers, 60)
                if body.get('error'):
                    details = {key: body[key] for key in ('provider_error_code', 'error_param', 'request_id',
                        'transport_phase', 'validation_code', 'response_content_type') if key in body}
                    details['request_phase'] = 'text_response'
                    raise ProbeFailure(body.get('error_code', 'provider_error'), body.get('http_status'), details)
                text = "".join(block['text'] for block in body.get('content', [])
                               if block.get('type') == 'text' and isinstance(block.get('text'), str))
                return {'choices': [{'message': {'role': 'assistant', 'content': text}}]}
            post = responses_probe
        base = config["base_url"].rstrip("/")
        anthropic = config["api_format"] == "anthropic"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if anthropic else {"Authorization": "Bearer " + api_key}
        if not api_key:
            if not is_loopback_provider(config):
                raise ProbeFailure("authentication")
            headers.pop("Authorization", None); headers.pop("x-api-key", None)
        headers["User-Agent"] = "IgorekDesktop/connection-check"
        if is_openai_realtime(config):
            from factory.core.realtime_voice import config as rt_config
            minted = post(
                base + "/realtime/client_secrets",
                {"expires_after": {"anchor": "created_at", "seconds": 60},
                 "session": rt_config.build_session_payload(session_id="connection-check")},
                headers,
            )
            if not str(minted.get("value") or ""):
                raise ProbeFailure("invalid_response")
            return {"status": "verified", "reason": "ok", "connected": True, "checked_at": checked_at}
        target = urlsplit(base)
        if target.hostname == "opencode.ai" and target.path.rstrip("/") == "/zen/go/v1":
            headers["x-opencode-session"] = "igorek-probe-" + secrets.token_hex(16)
        url = (base + "/messages" if base.endswith("/v1") else base + "/v1/messages") if anthropic else base + ("/responses" if config['api_format'] == 'openai-responses' else "/chat/completions")
        payload = {"model": config["model"], "messages": [{"role": "user", "content": provider_probe_prompt()}],
                   "max_tokens": 512, "stream": False}
        if not anthropic and config["model"].rsplit("/", 1)[-1].lower().startswith("deepseek-v4"):
            payload["thinking"] = {"type": "disabled"}
        response = post(url, payload, headers)
        connected = True
        if response.get("error"):
            raise ProbeFailure("provider_error")
        if anthropic:
            if response.get('role', 'assistant') != 'assistant':
                raise ProbeFailure('invalid_response')
            text = "".join(block['text'] for block in response.get('content', [])
                           if isinstance(block, dict) and block.get('type') == 'text'
                           and isinstance(block.get('text'), str))
        else:
            message = response.get('choices', [{}])[0].get('message', {})
            if message.get('role') != 'assistant':
                raise ProbeFailure('invalid_response')
            text = message.get('content')
        if not isinstance(text, str) or not text.strip():
            raise ProbeFailure('invalid_response')
        return {"status": "verified", "reason": "ok", "connected": True, "checked_at": checked_at}
    except ProbeFailure as exc:
        result = {"status": "failed", "reason": exc.reason, "connected": connected, "checked_at": checked_at}
        if exc.http_status is not None:
            result["http_status"] = exc.http_status
        result.update(exc.details)
        return result
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return {"status": "failed", "reason": "invalid_response", "connected": connected, "checked_at": checked_at}
