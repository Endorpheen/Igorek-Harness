"""Repository boundaries and real smart HTTP push without user repositories."""
import http.client
import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from factory.core.isolation.git_gateway import GitGateway, GitRepositoryGrant, repository_grants, grant_for_workspace, MAX_BODY
from factory.core.isolation.git_gateway_contract import GitGatewayRoute, guest_git_environment
from factory.core.isolation.guest_bwrap import guest_sandbox_env_argv
from factory.core.isolation.history import HostTaskInput, build_wire_task_payload, rehydrate_payload, PayloadError


SAFE_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8', 'GIT_CONFIG_NOSYSTEM': '1'}


def git(*args, cwd=None, env=None):
    result = subprocess.run(['git', *args], cwd=cwd, env=env or SAFE_ENV, capture_output=True, timeout=30)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, ['git', *args], stderr=result.stderr)
    return result.stdout.decode().strip()


@pytest.fixture
def upstream():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_GET(self): self.reply()
        def do_POST(self): self.reply()
        def reply(self):
            size = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(size)
            requests.append((self.command, self.path, dict(self.headers), body))
            if self.server.redirect:
                self.send_response(302);self.send_header('Location', 'http://127.0.0.1:1/private');self.end_headers();return
            service = 'git-receive-pack' if 'receive-pack' in self.path else 'git-upload-pack'
            response = b'0000'
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-' + service + ('-advertisement' if self.command == 'GET' else '-result'))
            self.send_header('Content-Length', str(len(response)));self.end_headers();self.wfile.write(response)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler);server.redirect=False
    thread=threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    try: yield server, requests
    finally: server.shutdown();server.server_close();thread.join()


def request(gateway, method, suffix, *, body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', gateway.port, timeout=5)
    connection.request(method, '/' + gateway.nonce + suffix, body=body, headers=headers or {})
    response = connection.getresponse();result=(response.status, response.read());connection.close();return result


@pytest.mark.parametrize('suffix', [
    '/other.git/info/refs?service=git-upload-pack', '/../../admin', '/%2e%2e/admin',
    '/info/refs?service=git-upload-pack&url=http://127.0.0.1:1', '//info/refs?service=git-upload-pack',
    '/info/refs?service=git-receive-pack&service=git-upload-pack', '/git-receive-pack?x=1', '/issues',
    '/info/refs?service=git-upload-pack#fragment', '/%69nfo/refs?service=git-upload-pack',
])
def test_path_bypasses_do_not_reach_upstream(upstream, suffix):
    server, requests = upstream
    gateway = GitGateway(GitRepositoryGrant(f'http://127.0.0.1:{server.server_port}/owner/repo', True), 'session', authorization=lambda _: '')
    try:
        assert request(gateway, 'GET', suffix)[0] == 403
        assert not requests
    finally: gateway.close()


def test_read_only_and_authentication_boundary(upstream):
    server, requests = upstream
    gateway=GitGateway(GitRepositoryGrant(f'http://127.0.0.1:{server.server_port}/owner/repo'), 'session', authorization=lambda _: 'Basic HOST_SECRET')
    try:
        assert request(gateway, 'GET', '/info/refs?service=git-receive-pack')[0] == 403
        assert request(gateway, 'POST', '/git-receive-pack', body=b'0000', headers={'Content-Type':'application/x-git-receive-pack-request'})[0] == 403
        assert not requests
        assert request(gateway, 'GET', '/info/refs?service=git-upload-pack', headers={'Authorization':'GUEST_SECRET', 'Cookie':'GUEST_COOKIE', 'Host':'other-server', 'Git-Protocol':'version=2'})[0] == 200
        assert requests[0][1] == '/owner/repo.git/info/refs?service=git-upload-pack'
        assert requests[0][2]['Authorization'] == 'Basic HOST_SECRET'
        assert 'Cookie' not in requests[0][2]
        assert requests[0][2]['Git-Protocol'] == 'version=2'
        server.redirect=True
        assert request(gateway, 'GET', '/info/refs?service=git-upload-pack')[0] == 502
        assert len(requests) == 2
    finally: gateway.close()


def test_framing_size_and_capability_are_checked_before_upstream(upstream):
    server, requests = upstream
    gateway=GitGateway(GitRepositoryGrant(f'http://127.0.0.1:{server.server_port}/owner/repo', True), 'session', authorization=lambda _: '')
    try:
        assert request(gateway,'POST','/git-receive-pack',body=b'x',headers={'Content-Type':'text/plain'})[0] == 403
        assert request(gateway,'POST','/git-receive-pack',body=b'',headers={'Content-Type':'application/x-git-receive-pack-request','Content-Length':str(MAX_BODY+1)})[0] == 400
        assert request(gateway,'POST','/git-receive-pack',body=b'',headers={'Content-Type':'application/x-git-receive-pack-request','Content-Length':'0','Transfer-Encoding':'chunked'})[0] == 400
        connection=http.client.HTTPConnection('127.0.0.1',gateway.port)
        connection.request('GET','/'+'0'*64+'/info/refs?service=git-upload-pack')
        assert connection.getresponse().status==403;connection.close()
        assert not requests
        port=gateway.port
    finally: gateway.close()
    with pytest.raises(OSError): socket.create_connection(('127.0.0.1',port),timeout=1)


def test_route_wire_env_and_bwrap_rewrite_are_non_secret_and_restored(monkeypatch):
    route=GitGatewayRoute('http://192.168.50.10:8034/owner/project','http://10.0.2.2:12345/'+'a'*64)
    assert GitGatewayRoute.from_wire(route.to_wire())==route
    with patch.dict(os.environ, {'GIT_CONFIG_COUNT':'7'}, clear=True):
        with guest_git_environment(route):
            args=guest_sandbox_env_argv(dict(os.environ))
            assert 'GIT_CONFIG_VALUE_0' in args and route.repository_url in args
            assert route.gateway_url in os.environ['GIT_CONFIG_KEY_0']
            assert os.environ['GIT_CONFIG_VALUE_1']=='false'
        assert dict(os.environ)=={'GIT_CONFIG_COUNT':'7'}
    for bad in ('http://192.168.50.10:8034/other/repo','http://10.0.2.2:12345/'+('a'*64)+'/extra','http://user@10.0.2.2:12345/'+('a'*64)):
        with pytest.raises(ValueError): GitGatewayRoute.from_wire({**route.to_wire(),'gateway_url':bad})


@pytest.mark.parametrize('url', ['http://example.com:8034/owner/repo','http://user:pass@192.168.50.10:8034/owner/repo','http://192.168.50.10:8034/../repo','http://192.168.50.10:8034/owner/repo?x=1','file:///repo','http://192.168.50.10:8034/owner/%2e%2e'])
def test_operator_urls_cannot_smuggle_credentials_or_dynamic_targets(url):
    with pytest.raises(ValueError): GitRepositoryGrant(url, True)


def test_repository_grant_comes_from_operator_and_matching_local_remote(tmp_path):
    url='http://192.168.50.10:8034/owner/project'
    grants=repository_grants({'IGOREK_ISOLATION_GIT_REPOSITORIES':json.dumps([{'url':url,'push':True}])})
    git('init',str(tmp_path));git('-C',str(tmp_path),'remote','add','backend',url)
    assert grant_for_workspace(grants,str(tmp_path))==grants[0]
    git('-C',str(tmp_path),'remote','set-url','backend',url+'-other')
    assert grant_for_workspace(grants,str(tmp_path)) is None
    with pytest.raises(ValueError): repository_grants({'IGOREK_ISOLATION_GIT_REPOSITORIES':json.dumps([{'url':url,'push':'yes'}])})


def test_real_git_push_fetch_and_chunked_pack_through_scoped_gateway(tmp_path):
    root=tmp_path/'repositories';bare=root/'owner'/'repo.git';bare.parent.mkdir(parents=True)
    git('init','--bare',str(bare));git('--git-dir='+str(bare),'config','http.receivepack','true')
    seen=[]
    backend=git('--exec-path')+'/git-http-backend'
    class Backend(BaseHTTPRequestHandler):
        def log_message(self,*_args): pass
        def do_GET(self): self.forward()
        def do_POST(self): self.forward()
        def forward(self):
            parsed=urlsplit(self.path);body=self.rfile.read(int(self.headers.get('Content-Length',0)))
            seen.append((parsed.path,dict(self.headers)))
            env={**SAFE_ENV,'GIT_PROJECT_ROOT':str(root),'GIT_HTTP_EXPORT_ALL':'1','PATH_INFO':parsed.path,'QUERY_STRING':parsed.query,'REQUEST_METHOD':self.command,'CONTENT_TYPE':self.headers.get('Content-Type',''),'CONTENT_LENGTH':str(len(body))}
            if self.headers.get('Git-Protocol'): env['HTTP_GIT_PROTOCOL']=self.headers['Git-Protocol']
            result=subprocess.run([backend],input=body,env=env,capture_output=True,check=True,timeout=30).stdout
            head,data=result.split(b'\r\n\r\n',1)
            self.send_response(200)
            for line in head.decode().split('\r\n'):
                key,value=line.split(':',1);self.send_header(key,value.strip())
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Backend);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    canonical=f'http://127.0.0.1:{server.server_port}/owner/repo'
    gateway=GitGateway(GitRepositoryGrant(canonical,True),'test-session',authorization=lambda _: '')
    local=tmp_path/'local';local.mkdir();git('init',str(local));git('-C',str(local),'config','user.name','Gateway Test');git('-C',str(local),'config','user.email','gateway@example.invalid')
    (local/'random.bin').write_bytes(os.urandom(2*1024*1024));git('-C',str(local),'add','random.bin');git('-C',str(local),'commit','-m','Test scoped Git push');git('-C',str(local),'remote','add','backend',canonical)
    gateway_url=gateway.route.gateway_url.replace('10.0.2.2','127.0.0.1')
    env={**SAFE_ENV,'GIT_CONFIG_COUNT':'2','GIT_CONFIG_KEY_0':'url.'+gateway_url+'.insteadOf','GIT_CONFIG_VALUE_0':canonical,'GIT_CONFIG_KEY_1':'http.postBuffer','GIT_CONFIG_VALUE_1':'65536','GIT_TERMINAL_PROMPT':'0'}
    try:
        from factory.core.isolation.git_gateway import _Handler
        encodings=[]
        read_body=_Handler._body
        def record_body(handler):
            encodings.append(handler.headers.get('Transfer-Encoding'))
            return read_body(handler)
        with patch.object(_Handler,'_body',record_body):
            git('-C',str(local),'push','backend','HEAD:refs/heads/main',env=env)
        assert 'chunked' in encodings
        assert git('--git-dir='+str(bare),'rev-parse','refs/heads/main')==git('-C',str(local),'rev-parse','HEAD')
        try:
            refs=git('-C',str(local),'ls-remote','backend',env=env)
        except subprocess.CalledProcessError as exc:
            raise AssertionError(exc.stderr.decode()) from None
        assert 'refs/heads/main' in refs
        clone=tmp_path/'clone'
        git('clone','--branch','main',canonical,str(clone),env=env)
        assert (clone/'random.bin').read_bytes()==(local/'random.bin').read_bytes()
        assert all(path.startswith('/owner/repo.git/') for path,_ in seen)
        git('-C',str(local),'remote','set-url','backend',canonical+'-other')
        with pytest.raises(subprocess.CalledProcessError): git('-C',str(local),'ls-remote','backend',env=env)
    finally: gateway.close();server.shutdown();server.server_close();thread.join()


def test_git_route_round_trips_through_guest_payload_and_rejects_invalid_target():
    route=GitGatewayRoute('http://192.168.50.10:8034/owner/project','http://10.0.2.2:12345/'+'b'*64)
    fields=dict(task_id='task',profile='cc-test',model='test',base_url='http://localhost/v1',api_format='openai',system_prompt='test',user_message='test',skills_text='',foundation_context='',filesystem_mode='workspace',guard_mode='on',max_tokens=1024,timeout_s=60,workdir='/workspace',artifact_workdir='/workspace/artifacts')
    wire=build_wire_task_payload(HostTaskInput(**fields,git_gateway=route))
    assert rehydrate_payload(wire).git_gateway==route
    from factory.core.isolation.guest_task import GuestTask
    assert GuestTask.from_wire(wire,guest_roots=('/workspace',)).git_gateway==route
    legacy=build_wire_task_payload(HostTaskInput(**fields))
    assert 'git_gateway' not in legacy and rehydrate_payload(legacy).git_gateway is None
    wire['git_gateway']['gateway_url']='http://192.168.50.10:8034/admin'
    with pytest.raises(PayloadError): rehydrate_payload(wire)


def test_denial_audit_does_not_include_capability_or_guest_secret(upstream, caplog):
    server, requests=upstream
    gateway=GitGateway(GitRepositoryGrant(f'http://127.0.0.1:{server.server_port}/owner/repo'),'audit-session',authorization=lambda _: '')
    try:
        with caplog.at_level('INFO'):
            assert request(gateway,'GET','/forbidden',headers={'Authorization':'PRIVATE_GUEST_TOKEN'})[0]==403
        assert 'audit-session' in caplog.text and 'git_route_denied' in caplog.text
        assert gateway.nonce not in caplog.text and 'PRIVATE_GUEST_TOKEN' not in caplog.text
        assert not requests
    finally: gateway.close()


def test_gateway_port_does_not_open_lan_or_other_host_services():
    from factory.core.isolation.network import build_network_policy, evaluate_egress
    policy=build_network_policy('cc-test',env={},resolver=lambda host: (),host_local_ports=(12345,))
    rules=policy.ruleset()
    assert evaluate_egress(rules,'10.0.2.2',12345).allowed
    assert not evaluate_egress(rules,'10.0.2.2',8034).allowed
    assert not evaluate_egress(rules,'192.168.50.10',8034).allowed
    assert not evaluate_egress(rules,'192.168.50.10',22).allowed


def test_host_credential_helper_receives_only_repository_and_no_model_secrets(monkeypatch):
    from factory.core.isolation.git_gateway import host_git_authorization
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN','MODEL_SECRET')
    completed=subprocess.CompletedProcess([],0,stdout='username=host-user\npassword=HOST_PASSWORD\n',stderr='')
    with patch('factory.core.isolation.git_gateway.subprocess.run',return_value=completed) as run:
        result=host_git_authorization('http://192.168.50.10:8034/owner/project')
    assert result.startswith('Basic ')
    kwargs=run.call_args.kwargs
    assert 'ANTHROPIC_AUTH_TOKEN' not in kwargs['env']
    assert kwargs['cwd']=='/' and kwargs['env']['GIT_TERMINAL_PROMPT']=='0'
    assert kwargs['input']=='url=http://192.168.50.10:8034/owner/project\n\n'
