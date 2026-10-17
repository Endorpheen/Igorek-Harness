"""Session-scoped references projected from the durable tool-result journal."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from urllib.parse import parse_qsl, urlsplit, urlunsplit


_URL = re.compile(r'https?://[^\s<>"`\x00-\x1f]+')
_PRIVATE_QUERY = re.compile(r'token|secret|password|signature|authorization|api.?key|x-amz-', re.I)


def safe_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or re.search(r'[\s\x00-\x1f\x7f]', value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password:
            return None
        url.port
        if any(_PRIVATE_QUERY.search(key) for key, _ in parse_qsl(url.query + '&' + url.fragment)):
            return None
        return urlunsplit((url.scheme, url.netloc, url.path, url.query, url.fragment))
    except ValueError:
        return None


def references(payload: dict) -> list[dict]:
    if not isinstance(payload, dict) or payload.get('status') != 'done' or payload.get('is_error') is True:
        return []
    found = OrderedDict()

    def remember(raw, title=''):
        url = safe_url(raw)
        if not url:
            return
        parsed = urlsplit(url)
        pull = re.match(r'^(/[^/]+/[^/]+/pulls?/\d+)(?:/|$)', parsed.path)
        kind = 'pull_request' if pull else 'web'
        if pull:
            url = urlunsplit((parsed.scheme, parsed.netloc, pull[1], '', ''))
        if url in found and not title:
            return
        name = (title if isinstance(title, str) and title else parsed.hostname + parsed.path).strip()[:240]
        found[url] = {'kind': kind, 'id': hashlib.sha256(url.encode()).hexdigest(), 'url': url, 'name': name}

    def visit(value, depth=0):
        if depth > 12:
            return
        if isinstance(value, dict):
            if value.get('isError') is True or value.get('error'):
                return
            for key in ('url', 'href', 'html_url', 'web_url', 'source_url', 'link', 'uri'):
                if isinstance(value.get(key), str):
                    remember(value[key], value.get('title') or value.get('name'))
            for child in list(value.values())[:1000]:
                visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:1000]:
                visit(child, depth + 1)
        elif isinstance(value, str):
            text = value[:1_000_000]
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError, RecursionError):
                parsed = None
            if isinstance(parsed, (dict, list)):
                visit(parsed, depth + 1)
            else:
                for match in _URL.finditer(text):
                    raw = match.group().rstrip('.,;!\'')
                    while raw.endswith(')') and raw.count(')') > raw.count('('):
                        raw = raw[:-1]
                    remember(raw.rstrip(']'))

    visit(payload.get('context_output') or payload.get('result_preview') or '')
    tool_input = payload.get('tool_input')
    if isinstance(tool_input, dict):
        remember(tool_input.get('url'))
    return list(found.values())


class SourceIndex:
    """Cache derived references; rebuilding after restart reads the same journal."""

    def __init__(self, store):
        self.store = store
        self.sessions = OrderedDict()
        self.lock = threading.RLock()

    def projection(self, session_id: str) -> tuple[list[dict], bool, list[str]]:
        with self.lock:
            maximum = self.store.max_seq(session_id=session_id)
            cursor, items, truncated, paths = self.sessions.pop(session_id, (0, OrderedDict(), False, OrderedDict()))
            if cursor > maximum:
                cursor, items, truncated, paths = 0, OrderedDict(), False, OrderedDict()
            while cursor < maximum:
                page = self.store.list_events(session_id=session_id, after_seq=cursor, limit=500, kinds=('tool.result',))
                if not page:
                    cursor = maximum
                    break
                for event in page:
                    cursor = event['seq']
                    payload = event.get('payload') or {}
                    if not isinstance(payload, dict):
                        continue
                    for item in references(payload):
                        items.pop(item['id'], None)
                        items[item['id']] = item
                        if len(items) > 500:
                            items.popitem(last=False)
                            truncated = True
                    if payload.get('status') == 'done' and payload.get('tool_name') in {'Write', 'Edit', 'Bash'}:
                        from ..telegram_bridge import _text_output_file_candidates
                        candidates = _text_output_file_candidates(str(payload.get('context_output') or payload.get('result_preview') or ''), absolute=True)
                        tool_input = payload.get('tool_input')
                        if payload.get('tool_name') in {'Write', 'Edit'} and isinstance(tool_input, dict):
                            candidates.append(str(tool_input.get('file_path') or ''))
                        for candidate in candidates:
                            if not candidate:
                                continue
                            paths.pop(candidate, None)
                            paths[candidate] = None
                            if len(paths) > 500:
                                paths.popitem(last=False)
            self.sessions[session_id] = (cursor, items, truncated, paths)
            while len(self.sessions) > 64:
                self.sessions.popitem(last=False)
            return list(reversed(items.values())), truncated, list(paths)

    def links(self, session_id: str) -> tuple[list[dict], bool]:
        links, truncated, _ = self.projection(session_id)
        return links, truncated

    def file_paths(self, session_id: str) -> list[str]:
        return self.projection(session_id)[2]
