import base64
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
SERVICE = 'desktop-integration:gmail'


def valid_gmail_scopes(value):
    from .calendar import SCOPES
    granted=set(value.split())
    return SCOPE in granted and granted <= SCOPES | {SCOPE}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_json(url, *, data=None, token=None):
    headers = {'Accept':'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if data is not None:
        data = urllib.parse.urlencode(data).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.build_opener(NoRedirect).open(req, timeout=12) as response:
        raw = response.read(1024*1024 + 1)
    if len(raw) > 1024*1024:
        raise ValueError()
    value = json.loads(raw) if raw else {}
    if not isinstance(value, dict):
        raise ValueError()
    return value


class GmailConnection:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle
        self.pending = None
        self.thread = None
        self.cancel = threading.Event()
        self.mutex = threading.RLock()

    def _record(self):
        return next((r for r in self.lifecycle.list_credentials() if r.service == SERVICE and r.imported_from == 'desktop-gmail'), None)

    def _data(self):
        record = self._record()
        if record is None:
            raise ValueError()
        return json.loads(self.lifecycle.get_secret(record.id).secret)

    def _store(self, data):
        self.lifecycle.add_credential(SERVICE, json.dumps(data), 'oauth_token', imported_from='desktop-gmail', scopes=[SCOPE], replace_existing=True)

    def import_host(self, password):
        self.lifecycle.authenticate(password)
        if self.thread and self.thread.is_alive():
            raise ValueError()
        from factory.core.vault.crypto import derive_key, decrypt_secret
        root = Path(os.environ['FACTORY_GMAIL_HOST_VAULT'])
        passfile = root / 'passphrase.txt'
        if passfile.is_symlink() or passfile.stat().st_mode & 0o077 or passfile.stat().st_size > 4096:
            raise ValueError()
        key = derive_key(passfile.read_text().strip(), (root / 'master_key_salt.bin').read_bytes())
        with sqlite3.connect((root / 'vault.db').as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT encrypted_payload FROM credentials WHERE service=? AND imported_from=? AND alias=?', (SERVICE, 'desktop-gmail', 'default')).fetchone()
        if row is None:
            raise ValueError()
        data = json.loads(json.loads(decrypt_secret(row[0], key))['secret'])
        if data.get('scope') != SCOPE or not data.get('refresh_token') or not data.get('client_id','').endswith('.apps.googleusercontent.com'):
            raise ValueError()
        current = self._data() if self._record() else {}
        if current.get('refresh_token') and current != data:
            raise ValueError()
        self._store(data)
        return self.status()

    def status(self):
        with self.mutex:
            record = self._record()
            data = self._data() if record else {}
            return {'configured':bool(data.get('client_id')), 'connected':bool(data.get('refresh_token')),
                    'pending':bool(self.thread and self.thread.is_alive()),
                    'error': self.pending.get('error','') if isinstance(self.pending,dict) else ''}

    def configure(self, source, password):
        self.lifecycle.authenticate(password)
        if self.thread and self.thread.is_alive():
            raise ValueError()
        if self._record() and self._data().get('refresh_token'):
            raise ValueError()
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                raise ValueError()
            value = json.loads(stream.read(16385))
        client = value.get('installed') if isinstance(value,dict) else None
        if not isinstance(client,dict):
            raise ValueError()
        client_id, secret = client.get('client_id'), client.get('client_secret')
        if not isinstance(client_id,str) or not client_id.endswith('.apps.googleusercontent.com') or len(client_id)>512 or not isinstance(secret,str) or not 1<=len(secret)<=512:
            raise ValueError()
        self._store({'client_id':client_id,'client_secret':secret})
        self.pending = None
        return self.status()

    def begin(self, password):
        self.lifecycle.authenticate(password)
        if self.thread and self.thread.is_alive():
            raise ValueError()
        data = self._data()
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        state = secrets.token_urlsafe(32)
        outer = self
        self.cancel.clear()
        self.pending = {}
        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if len(self.path) > 16384:
                    self.send_error(400)
                    return
                parsed = urllib.parse.urlsplit(self.path)
                values = urllib.parse.parse_qs(parsed.query)
                valid = parsed.path == '/callback' and values.get('state') == [state] and len(values.get('code', [])) == 1 and 'error' not in values
                if valid:
                    code = values.get('code', [''])[0]
                    if not code or len(code)>4096:
                        outer.pending = {'error':'gmail_authorization_failed'}
                    else:
                        outer.pending = {'code':code}
                self.send_response(200 if valid else 400)
                self.send_header('Content-Type','text/plain; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                self.wfile.write('Вернитесь в приложение Игорёк.'.encode())
        server = HTTPServer(('127.0.0.1',0),Callback)
        server.timeout = .5
        redirect = 'http://127.0.0.1:' + str(server.server_port) + '/callback'
        def run():
            try:
                deadline=time.monotonic()+180
                while not self.cancel.is_set() and not self.pending and time.monotonic()<deadline:
                    server.handle_request()
                code = self.pending.get('code') if self.pending else None
                if not code or self.cancel.is_set():
                    raise ValueError()
                token = request_json('https://oauth2.googleapis.com/token',data={'client_id':data['client_id'],'client_secret':data['client_secret'],'code':code,'code_verifier':verifier,'redirect_uri':redirect,'grant_type':'authorization_code'})
                if not valid_gmail_scopes(token.get('scope','')) or not token.get('refresh_token') or not token.get('access_token'):
                    raise ValueError()
                with self.mutex:
                    if self.cancel.is_set():
                        raise ValueError()
                    self._store({**data,'refresh_token':token['refresh_token']})
                    self.pending = {}
            except Exception:
                self.pending = {'error':'gmail_authorization_failed'}
            finally:
                server.server_close()
        self.thread=threading.Thread(target=run,daemon=True)
        self.thread.start()
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({'client_id':data['client_id'],'redirect_uri':redirect,'response_type':'code','scope':SCOPE,'state':state,'code_challenge':challenge,'code_challenge_method':'S256','access_type':'offline','prompt':'consent'})

    def disconnect(self, password):
        self.lifecycle.authenticate(password)
        self.cancel.set()
        with self.mutex:
            data=self._data()
            token=data.get('refresh_token')
            if token:
                request_json('https://oauth2.googleapis.com/revoke',data={'token':token})
            self._store({k:v for k,v in data.items() if k in {'client_id','client_secret'}})
            self.pending=None
        return self.status()

    def get(self, path, params=None):
        if path != 'messages' and not (path.startswith('messages/') and path[9:].isalnum() and len(path) <= 137):
            raise ValueError()
        with self.mutex:
            data=self._data()
            token=request_json('https://oauth2.googleapis.com/token',data={'client_id':data['client_id'],'client_secret':data['client_secret'],'refresh_token':data['refresh_token'],'grant_type':'refresh_token'})
            if token.get('scope') and not valid_gmail_scopes(token['scope']):
                raise ValueError()
            url='https://gmail.googleapis.com/gmail/v1/users/me/' + path
            if params:
                url+='?' + urllib.parse.urlencode(params)
            return request_json(url,token=token['access_token'])


class GmailClient:
    def __init__(self, connection, access_check):
        self.connection=connection
        self.check=access_check
        self.tools=[{'name':'gmail_search','description':'Search message IDs and snippets; read only.','inputSchema':{'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False}},
                    {'name':'gmail_read','description':'Read one selected message as plain text; no attachments or actions.','inputSchema':{'type':'object','properties':{'id':{'type':'string'}},'required':['id'],'additionalProperties':False}},
                    {'name':'gmail_latest','description':'Read the latest received messages in Inbox; no attachments or actions.','inputSchema':{'type':'object','properties':{'count':{'type':'integer','minimum':1,'maximum':10}},'required':['count'],'additionalProperties':False}}]

    def start(self):
        if not self.check('gmail') or not self.connection.status()['connected']:
            raise PermissionError()

    def stop(self):
        pass

    def call_tool(self, name, args):
        if not self.check('gmail') or not isinstance(args,dict):
            raise PermissionError()
        if name=='gmail_latest' and set(args)=={'count'} and type(args['count']) is int and 1<=args['count']<=10:
            result=self.connection.get('messages',{'q':'in:inbox','maxResults':args['count'],'includeSpamTrash':'false'})
            messages=[]
            for item in result.get('messages',[])[:args['count']]:
                message=json.loads(self.call_tool('gmail_read',{'id':item['id']}))
                message['text']=message['text'][:4000]
                message['snippet']=message['snippet'][:500]
                messages.append(message)
            value={'messages':messages}
        elif name=='gmail_search' and set(args)=={'query'} and isinstance(args['query'],str) and len(args['query'])<=500:
            result=self.connection.get('messages',{'q':args['query'],'maxResults':10,'includeSpamTrash':'false'})
            value={'messages':result.get('messages',[])}
        elif name=='gmail_read' and set(args)=={'id'} and isinstance(args['id'],str) and 1<=len(args['id'])<=128 and args['id'].isalnum():
            result=self.connection.get('messages/'+args['id'],{'format':'full'})
            payload=result.get('payload',{})
            headers={h['name'].lower():str(h.get('value',''))[:500] for h in payload.get('headers',[]) if isinstance(h,dict) and h.get('name','').lower() in {'from','to','subject','date'}}
            texts=[]
            def collect(part,depth=0):
                if depth>8 or part.get('filename'):
                    return
                if part.get('mimeType')=='text/plain' and isinstance(part.get('body',{}).get('data'),str):
                    encoded=part['body']['data']
                    if len(encoded)<=64000:
                        texts.append(base64.urlsafe_b64decode(encoded+'='*((-len(encoded))%4)).decode('utf-8',errors='replace'))
                for child in part.get('parts',[])[:30]:
                    collect(child,depth+1)
            collect(payload)
            value={'id':args['id'],'headers':headers,'text':'\n'.join(texts)[:48000],'snippet':result.get('snippet','')[:2000]}
        else:
            raise ValueError()
        if not self.check('gmail'):
            raise PermissionError()
        return json.dumps(value,ensure_ascii=False)
