import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from factory.core.mcp_tasks import remote


@pytest.mark.parametrize('sse',[False,True])
def test_http_mcp_initialize_list_call_session_headers_and_revocation(monkeypatch,sse):
    requests=[];allowed=[True]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append((payload,dict(self.headers)))
            if payload['method']=='notifications/initialized':self.send_response(202);self.end_headers();return
            if payload['method']=='initialize':result={'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
            elif payload['method']=='tools/list':result={'tools':[{'name':'search_openai_docs','inputSchema':{'type':'object'}}]}
            else:result={'content':[{'type':'text','text':json.dumps({'hits':[{'url':'https://developers.openai.com/api/docs'}]})}]}
            value=json.dumps({'jsonrpc':'2.0','id':payload['id'],'result':result}).encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream' if sse else 'application/json');self.send_header('Mcp-Session-Id','fixture-session');self.end_headers()
            self.wfile.write(b': keepalive\n\nevent: message\ndata: '+value+b'\n\n' if sse else value)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(remote,'DOCS_URL',f'http://127.0.0.1:{server.server_port}/mcp')
    try:
        client=remote.RemoteMcpClient(lambda:allowed[0]);client.start()
        assert json.loads(client.call_tool('search_openai_docs',{'query':'fixture'}))['hits']
        assert [item[0]['method'] for item in requests]==['initialize','notifications/initialized','tools/list','tools/call']
        assert requests[-1][1]['Mcp-Session-Id']=='fixture-session'
        allowed[0]=False
        with pytest.raises(PermissionError):client.call_tool('search_openai_docs',{'query':'fixture'})
        assert len(requests)==4
        client.stop();assert not client.tools
    finally:server.shutdown();thread.join();server.server_close()
