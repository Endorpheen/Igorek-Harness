"""Preserve the official MCP tool-error flag independently of response wording."""
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bridge():
    replies = {'result': {'content': []}, 'status': 200}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if 'id' not in value:
                self.send_response(204);self.end_headers();return
            result = {'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'fixture','version':'1'}} if value['method']=='initialize' else replies['result']
            self.send_response(replies['status'] if value['method']=='tools/call' else 200)
            self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'jsonrpc':'2.0','id':value['id'],'result':result}).encode())
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    node=shutil.which('node')
    assert node, 'Node is required for the MCP error contract'
    proc=subprocess.Popen([node,str(ROOT/'factory/mcp_servers/grep_app_server.mjs')],
        env={**os.environ,'GREP_APP_MCP_URL':f'http://127.0.0.1:{server.server_port}'},
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def call():
        proc.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call',
            'params':{'name':'search_github','arguments':{'query':'fixture'}}})+'\n');proc.stdin.flush()
        return json.loads(proc.stdout.readline())['result']
    try:yield replies,call
    finally:
        proc.terminate();proc.wait(timeout=5)
        for stream in (proc.stdin,proc.stdout,proc.stderr):stream.close()
        server.shutdown();server.server_close();thread.join(2)


def test_captured_official_tool_error_is_not_advertised_as_real_results(bridge):
    replies,call=bridge
    replies['result']=json.loads((ROOT/'tests/fixtures/grep_app_upstream/tool-error.json').read_text())['result']
    result=call()
    assert result.get('isError') is True
    assert result['content'][0]['text']==replies['result']['content'][0]['text']
    assert 'URL_COPY_CONTRACT' not in result['content'][0]['text']


@pytest.mark.parametrize('text',['Repository: fixture\nURL: https://github.com/example/project','The example handles an error response'])
def test_success_keeps_existing_url_contract_even_when_text_mentions_errors(bridge,text):
    replies,call=bridge
    replies['result']={'content':[{'type':'text','text':text}],'isError':False}
    result=call()
    assert not result.get('isError') and 'URL_COPY_CONTRACT' in result['content'][0]['text']
    assert text in result['content'][0]['text']


def test_empty_upstream_tool_error_remains_an_error(bridge):
    replies,call=bridge;replies['result']={'content':[],'isError':True}
    result=call()
    assert result.get('isError') is True
    assert result['content'][0]['text'] and 'URL_COPY_CONTRACT' not in result['content'][0]['text']


def test_http_failure_remains_a_tool_error(bridge):
    replies,call=bridge;replies['status']=429
    result=call()
    assert result.get('isError') is True and 'upstream HTTP 429' in result['content'][0]['text']
