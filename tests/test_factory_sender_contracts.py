"""Public submission/status helper against an owned authenticated HTTP fixture."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from factory import send_to_factory as client


@pytest.fixture
def api(monkeypatch):
    state=SimpleNamespace(records=[],post_status=201,get_status=200,body={'task_id':'owned-task','status':'ok','profile':'fixture','created_at':1,'result_text':'Fixture completed result','workspace_files':['fixture-report.txt']})
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def handle_request(self):
            raw=self.rfile.read(int(self.headers.get('Content-Length') or 0))
            state.records.append({'method':self.command,'path':self.path,'key':self.headers.get('X-API-Key'),'body':json.loads(raw) if raw else None})
            status=401 if self.headers.get('X-API-Key')!='fixture-api-key' else state.post_status if self.command=='POST' else state.get_status
            body=json.dumps(state.body if status<400 else {'error':'fixture failure'}).encode()
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        do_POST=handle_request
        do_GET=handle_request
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01));thread.start()
    monkeypatch.setenv('FACTORY_API_KEY','fixture-api-key');monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost');monkeypatch.setattr(client,'API_URL',f'http://127.0.0.1:{server.server_port}')
    try:yield state
    finally:server.shutdown();server.server_close();thread.join(3);assert not thread.is_alive()


def test_submission_uses_same_owner_authentication_as_status_lookup(api,capsys):
    assert client.send_task('Привет 🐙','fixture')=='owned-task'
    record=api.records[-1]
    assert record['key']=='fixture-api-key' and record['method']=='POST' and record['path']=='/task'
    assert record['body']=={'text':'Привет 🐙','profile':'fixture','capabilities':[],'metadata':{'source':'matrix','created_via':'matrix_bot'}}
    assert 'fixture-api-key' not in capsys.readouterr().out


def test_status_cli_accepts_printed_check_command_without_submission_text(api,monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['send_to_factory.py','--status','owned-task'])
    client.main()
    assert len(api.records)==1 and api.records[0]['method']=='GET'
    assert 'Fixture completed result' in capsys.readouterr().out


def test_submission_cli_preserves_profile_without_sending_duplicate_requests(api,monkeypatch):
    monkeypatch.setattr(sys,'argv',['send_to_factory.py','fixture question','--profile','fixture'])
    client.main()
    assert len(api.records)==1 and api.records[0]['method']=='POST'
    assert api.records[0]['body']['profile']=='fixture'


def test_empty_cli_still_requires_submission_text(monkeypatch):
    monkeypatch.setattr(sys,'argv',['send_to_factory.py'])
    with pytest.raises(SystemExit) as error:client.main()
    assert error.value.code==2


@pytest.mark.parametrize('status',[400,401,500])
def test_failed_submission_never_invents_task_identifier(api,status):
    api.post_status=status
    assert client.send_task('fixture','fixture') is None
    assert len(api.records)==1


@pytest.mark.parametrize('status',[404,500])
def test_missing_or_failed_status_is_reported_without_fabricated_result(api,status):
    api.get_status=status
    assert client.get_task_status('owned-task') is None
    assert api.records[-1]['key']=='fixture-api-key'


@pytest.mark.parametrize('text',['short fixture','x'*501])
def test_status_preview_is_bounded_but_returned_data_remains_complete(api,capsys,text):
    api.body['result_text']=text
    result=client.get_task_status('owned-task')
    assert result['result_text']==text
    rendered=capsys.readouterr().out
    assert text[:500] in rendered and ('...' in rendered if len(text)>500 else True)
    assert 'fixture-report.txt' in rendered and 'fixture-api-key' not in rendered


@pytest.mark.parametrize('method',['post','get'])
def test_connection_failure_returns_none_without_an_extra_attempt(monkeypatch,method):
    operation=Mock(side_effect=requests.ConnectionError('fixture unavailable'));monkeypatch.setattr(client.requests,method,operation)
    assert (client.send_task('fixture') if method=='post' else client.get_task_status('owned-task')) is None
    operation.assert_called_once()
