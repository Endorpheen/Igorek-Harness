"""Read the configured context of a loopback inference server without generation."""
from __future__ import annotations

import http.client
import ipaddress
import json
import ssl
from urllib.parse import urlsplit


def is_loopback_model_url(base_url: str) -> bool:
    try:
        target = urlsplit(base_url)
        if target.scheme not in {'http', 'https'} or not target.hostname or target.username or target.password or target.query or target.fragment:
            return False
        if target.port is not None and not 1 <= target.port <= 65535:
            return False
        return target.hostname == 'localhost' or ipaddress.ip_address(target.hostname).is_loopback
    except ValueError:
        return False


def local_model_context_window(base_url: str) -> int | None:
    if not is_loopback_model_url(base_url):
        return None
    target = urlsplit(base_url)
    path = target.path.rstrip('/')
    if path.endswith('/v1'):
        path = path[:-3].rstrip('/')
    connection = None
    try:
        connection = (http.client.HTTPSConnection(target.hostname, target.port, timeout=.5, context=ssl.create_default_context())
                      if target.scheme == 'https' else http.client.HTTPConnection(target.hostname, target.port, timeout=.5))
        connection.request('GET', path + '/props', headers={'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            return None
        raw = response.read(65537)
        if len(raw) > 65536:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        generation = payload.get('default_generation_settings')
        generation = generation if isinstance(generation, dict) else {}
        for value in (payload.get('n_ctx_slot'), generation.get('n_ctx'), generation.get('n_ctx_slot')):
            if type(value) is int and 0 < value <= 2**31 - 1:
                return value
    except (OSError, http.client.HTTPException, ValueError, UnicodeError):
        return None
    finally:
        if connection is not None:
            connection.close()
    return None
