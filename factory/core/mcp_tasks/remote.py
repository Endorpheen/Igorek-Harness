"""Small streamable-HTTP bridge for the public OpenAI documentation MCP."""
import json
import time
import urllib.request
from .gmail import NoRedirect

DOCS_URL = 'https://developers.openai.com/mcp'
MAX_BYTES = 1024 * 1024


class RemoteMcpClient:
    def __init__(self, access_check=lambda: True):
        self.access_check = access_check
        self.tools = []
        self.session = None
        self.sequence = 0
        self.protocol = '2025-03-26'

    def _request(self, method, params=None, *, notification=False):
        if not self.access_check():
            raise PermissionError()
        self.sequence += 1
        identity = self.sequence
        body = {'jsonrpc':'2.0','method':method,'params':params or {}}
        if not notification:
            body['id'] = identity
        headers = {'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':self.protocol}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        request = urllib.request.Request(DOCS_URL, json.dumps(body).encode(), headers, method='POST')
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            session = response.headers.get('Mcp-Session-Id')
            if session and len(session) <= 1024 and not any(ord(c)<32 for c in session):
                self.session = session
            if notification:
                return {}
            if response.headers.get('Content-Type','').split(';')[0] == 'text/event-stream':
                value = None; data = []; size = 0; deadline = time.monotonic()+20
                while time.monotonic() < deadline:
                    line = response.readline(MAX_BYTES+1)
                    size += len(line)
                    if size > MAX_BYTES:
                        raise ValueError('MCP response too large')
                    if not line:
                        break
                    if line in (b'\n',b'\r\n'):
                        if data:
                            candidate = json.loads(b'\n'.join(data))
                            if isinstance(candidate,dict) and type(candidate.get('id')) is int and candidate.get('id') == identity:
                                value = candidate; break
                            data = []
                    elif line.startswith(b'data:'):
                        data.append(line[5:].lstrip().rstrip(b'\r\n'))
                if value is None:
                    raise ValueError('Incomplete MCP response')
            else:
                raw = response.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES:
                    raise ValueError('MCP response too large')
                value = json.loads(raw)
        if not self.access_check():
            raise PermissionError()
        if not isinstance(value,dict) or type(value.get('id')) is not int or value.get('id') != identity or 'error' in value or not isinstance(value.get('result'),dict):
            raise ValueError('Invalid MCP response')
        return value['result']

    def start(self):
        reply = self._request('initialize', {'protocolVersion':self.protocol,'capabilities':{},'clientInfo':{'name':'IgorekDesktop','version':'1'}})
        if reply.get('protocolVersion') not in {'2024-11-05','2025-03-26','2025-06-18'}:
            raise ValueError('Unsupported MCP version')
        self.protocol = reply['protocolVersion']
        self._request('notifications/initialized', notification=True)
        cursor = None; tools = []
        for _ in range(10):
            page = self._request('tools/list', {'cursor':cursor} if cursor else {})
            if not isinstance(page.get('tools'),list):
                raise ValueError('Invalid tools')
            tools.extend(page['tools'])
            cursor = page.get('nextCursor')
            if not cursor:
                break
            if not isinstance(cursor,str) or len(cursor)>4096:
                raise ValueError('Invalid tools cursor')
        else:
            raise ValueError('Too many tools')
        if not tools or len(tools)>100:
            raise ValueError('Invalid tools count')
        self.tools = tools

    def call_tool(self, name, arguments):
        if name not in {item.get('name') for item in self.tools} or not isinstance(arguments,dict):
            raise ValueError('Unknown MCP tool')
        result = self._request('tools/call', {'name':name,'arguments':arguments})
        text = '\n'.join(item['text'] for item in result.get('content',[]) if isinstance(item,dict) and item.get('type')=='text' and isinstance(item.get('text'),str))
        if result.get('isError'):
            return json.dumps({'error':'remote_tool_failed'})
        if not text and 'structuredContent' in result:
            text = json.dumps(result['structuredContent'],ensure_ascii=False)
        if len(text)>65536:
            return text[:65000]+'\n[Результат сокращён; уточните запрос.]'
        return text

    def stop(self):
        self.tools = []
        self.session = None
