"""Synthetic HTTP metadata and SSE exercise the full backend-to-UI boundary."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from factory.core import cc_backend_executor as executor
from test_cc_stream_wire_contracts import event, chunk
from test_local_history_output_guard import ECHO


@pytest.mark.parametrize('backend',['llamacpp','cloud','unknown'])
def test_task_identity_reaches_live_stream_and_durable_response(tmp_path,monkeypatch,backend):
    requests=[]
    def models():
        return {} if backend=='unknown' else {'data':[{'id':'fixture','owned_by':backend,
            'architecture':{'input_modalities':['text']}}]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            requests.append(('GET',self.path,None))
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps(models() if self.path=='/v1/models' else {}).encode())
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(('POST',self.path,body))
            count=sum(row[0]=='POST' for row in requests)
            text=ECHO if count==1 else 'Current answer'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(event(chunk(text[:12]))+event(chunk(text[12:],finish='stop'))+b'data: [DONE]\n\n')
            self.wfile.flush()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(executor,'_resolve_backend_context_window',lambda **kwargs:(65536,'fixture'))
    monkeypatch.setattr(executor,'is_interrupt_requested',lambda:False)
    monkeypatch.setattr(executor,'task_should_stop',lambda task_id:False)
    work=tmp_path/'work';work.mkdir()
    artifacts=tmp_path/'artifacts';artifacts.mkdir()
    try:
        result=executor.execute_cc_backend_task(base_url=f'http://127.0.0.1:{server.server_port}/v1',
            model='fixture',api_format='openai',system_prompt='Fixture system',user_message='Fixture task',
            workdir=str(work),artifact_workdir=str(artifacts),enabled_tools=[],
            metadata={'model_context_window':65536},timeout_s=3)
    finally:
        server.shutdown();server.server_close();thread.join(2)
    records=[json.loads(row) for row in (artifacts/'cc_backend_trace.jsonl').read_text().splitlines()]
    visible=''.join(row['text'] for row in records if row['kind']=='model.text_delta')
    rounds=[row for row in records if row['kind']=='model.round_response']
    posts=[row for row in requests if row[0]=='POST']
    assert result['status']=='ok' and result['tool_calls_count']==0
    if backend=='llamacpp':
        assert result['output_text']==visible=='Current answer'
        assert [row['text_full'] for row in rounds]==['','Current answer']
        assert len(posts)==2 and all(row['backend_kind']=='llama_cpp' for row in rounds)
    else:
        assert result['output_text']==visible==ECHO
        assert len(posts)==1 and rounds[0]['text_full']==ECHO
        assert rounds[0]['backend_kind']=='unknown'
    assert [row[1] for row in requests if row[0]=='GET']==(
        ['/v1/models','/props?model=fixture'] if backend=='unknown' else ['/v1/models'])
    assert all('backend_capabilities' not in row[2] for row in posts)
