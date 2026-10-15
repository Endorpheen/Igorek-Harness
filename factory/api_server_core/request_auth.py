"""Authenticate inference requests before generation or paid context compression."""
from __future__ import annotations

import hmac
import os
from collections.abc import Mapping

KEY_ENV = 'IGOREK_INFERENCE_API_KEY'
MAX_KEY_BYTES = 4096


def paid_upstream_configured(backend: str, env: Mapping[str, str]) -> bool:
    if backend.lower() == 'openrouter':
        return True
    if env.get('CONTEXT_COMPRESSION_ENABLED', '1') == '0':
        return False
    if env.get('CONTEXT_COMPRESSION_API_KEY', '').strip():
        return True
    # A loopback compression endpoint can itself proxy a paid service.
    return bool(env.get('CONTEXT_COMPRESSION_BASE_URL', '').strip())


def authentication_error(
    headers: Mapping[str, str], *, backend: str, env: Mapping[str, str] | None = None,
) -> tuple[int, str] | None:
    source = os.environ if env is None else env
    expected = source.get(KEY_ENV, '').strip().encode('utf-8')
    if not expected:
        if paid_upstream_configured(backend, source):
            return 503, f'Configure {KEY_ENV} before using a remote or paid inference upstream.'
        return None
    if len(expected) > MAX_KEY_BYTES:
        return 503, 'Invalid inference authentication configuration.'
    supplied = []
    if headers.get('x-api-key') is not None:
        supplied.append(headers['x-api-key'])
    authorization = headers.get('authorization')
    if authorization is not None:
        kind, separator, token = authorization.partition(' ')
        if kind.lower() != 'bearer' or not separator or not token:
            return 401, 'Invalid inference credentials.'
        supplied.append(token)
    if not supplied:
        return 401, 'Inference credentials are required.'
    for value in supplied:
        encoded = value.encode('utf-8')
        if len(encoded) > MAX_KEY_BYTES or not hmac.compare_digest(encoded, expected):
            return 401, 'Invalid inference credentials.'
    return None
