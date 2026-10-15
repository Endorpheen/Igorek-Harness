"""Host-owned APK delivery: scoped files in, fixed Telegram recipient out."""
from __future__ import annotations
import fcntl
import hashlib
import http.client
import json
import os
import re
import secrets
import stat
import struct
import subprocess
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ENV_DELIVERY = 'IGOREK_ISOLATION_APK_DELIVERY'
ENV_ENDPOINT = 'IGOREK_APK_DELIVERY_URL'
MAX_APK = 128 * 1024 * 1024


def telegram_recipient():
    allowed = set()
    for name in ('FACTORY_TELEGRAM_ALLOWED_USER_IDS', 'FACTORY_TELEGRAM_ALLOWED_CHAT_IDS'):
        for value in os.environ.get(name, '').split(','):
            value = value.strip()
            if value and re.fullmatch(r'-?[1-9][0-9]*', value):
                allowed.add(int(value))
    explicit = os.environ.get('IGOREK_APK_DELIVERY_CHAT_ID', '').strip()
    if explicit:
        if not re.fullmatch(r'-?[1-9][0-9]*', explicit) or int(explicit) not in allowed:
            raise ValueError('apk_delivery_recipient_not_allowed')
        return int(explicit)
    if len(allowed) != 1:
        raise ValueError('apk_delivery_recipient_required')
    return next(iter(allowed))


def validate_endpoint(value):
    if not isinstance(value,str): raise ValueError('apk_endpoint_invalid')
    url=urlsplit(value)
    if (url.scheme!='http' or url.hostname!='10.0.2.2' or not url.port or url.username is not None
        or url.password is not None or url.query or url.fragment or not re.fullmatch('/[0-9a-f]{64}/send-apk',url.path)):
        raise ValueError('apk_endpoint_invalid')
    return value


@contextmanager
def guest_delivery_environment(endpoint):
    old=os.environ.get(ENV_ENDPOINT)
    if endpoint: os.environ[ENV_ENDPOINT]=validate_endpoint(endpoint)
    else: os.environ.pop(ENV_ENDPOINT,None)
    try: yield
    finally:
        if old is None: os.environ.pop(ENV_ENDPOINT,None)
        else: os.environ[ENV_ENDPOINT]=old


def apk_metadata(path):
    with zipfile.ZipFile(path) as apk:
        entry=apk.getinfo('AndroidManifest.xml')
        if entry.file_size>8*1024*1024: raise ValueError('apk_manifest_too_large')
        if sum(item.filename=='AndroidManifest.xml' for item in apk.infolist())!=1: raise ValueError('apk_manifest_invalid')
        data=apk.read(entry)
    if len(data)<8 or struct.unpack_from('<H',data)[0]!=3: raise ValueError('apk_manifest_invalid')
    strings=[];offset=8;decoded_bytes=0;seen_pool=False
    def length(pos,utf8,end):
        if utf8:
            if pos>=end: raise ValueError('apk_manifest_invalid')
            first=data[pos]
            if first&128:
                if pos+2>end: raise ValueError('apk_manifest_invalid')
                return (((first&127)<<8)|data[pos+1],pos+2)
            return first,pos+1
        if pos+2>end: raise ValueError('apk_manifest_invalid')
        first=struct.unpack_from('<H',data,pos)[0]
        if first&32768:
            if pos+4>end: raise ValueError('apk_manifest_invalid')
            return (((first&32767)<<16)|struct.unpack_from('<H',data,pos+2)[0],pos+4)
        return first,pos+2
    def string(index):
        if not 0<=index<len(strings): raise ValueError('apk_manifest_invalid')
        return strings[index]
    while offset+8<=len(data):
        kind,header,size=struct.unpack_from('<HHI',data,offset)
        if size<header or size<8 or offset+size>len(data): raise ValueError('apk_manifest_invalid')
        if kind==1:
            if seen_pool or header<28: raise ValueError('apk_manifest_invalid')
            seen_pool=True
            count,styles,flags,start,style_start=struct.unpack_from('<IIIII',data,offset+8)
            if count>100000 or styles>100000 or start<header+4*(count+styles) or start>size:
                raise ValueError('apk_manifest_invalid')
            if style_start and not start<=style_start<=size: raise ValueError('apk_manifest_invalid')
            end=offset+(style_start or size)
            decoded={}
            for i in range(count):
                pos=offset+start+struct.unpack_from('<I',data,offset+header+i*4)[0]
                if pos in decoded:
                    strings.append(decoded[pos]);continue
                key=pos
                if flags&256:
                    _,pos=length(pos,True,end);chars,pos=length(pos,True,end)
                    size_bytes=chars;terminator=b'\0';encoding='utf-8'
                else:
                    chars,pos=length(pos,False,end)
                    size_bytes=chars*2;terminator=b'\0\0';encoding='utf-16le'
                if pos+size_bytes+len(terminator)>end or data[pos+size_bytes:pos+size_bytes+len(terminator)]!=terminator:
                    raise ValueError('apk_manifest_invalid')
                decoded_bytes+=size_bytes
                if decoded_bytes>8*1024*1024: raise ValueError('apk_manifest_too_large')
                value=data[pos:pos+size_bytes].decode(encoding)
                decoded[key]=value;strings.append(value)
        if kind==0x102:
            ext=offset+16
            if header<16 or ext+20>offset+size: raise ValueError('apk_manifest_invalid')
            name=struct.unpack_from('<I',data,ext+4)[0]
            if string(name)=='manifest':
                start,attr_size,count=struct.unpack_from('<HHH',data,ext+8)
                result={}
                if attr_size<20 or start<20 or ext+start+attr_size*count>offset+size: raise ValueError('apk_manifest_invalid')
                for i in range(count):
                    pos=ext+start+i*attr_size
                    namespace,name,raw,_,_,value_type,value=struct.unpack_from('<IIIHBBI',data,pos)
                    key=string(name)
                    expected_namespace = namespace == 0xffffffff if key == 'package' else namespace != 0xffffffff and string(namespace) == 'http://schemas.android.com/apk/res/android'
                    if key in {'package','versionName','versionCode'} and expected_namespace:
                        if key in result: raise ValueError('apk_manifest_invalid')
                        result[key]=string(value) if value_type==3 else value if value_type in {16,17} else None
                if result.get('package')!='ru.igorek.remote' or not isinstance(result.get('versionCode'),int) or not isinstance(result.get('versionName'),str):
                    raise ValueError('apk_application_not_allowed')
                if not 0<result["versionCode"]<2147483648 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}",result["versionName"]): raise ValueError("apk_version_invalid")
                return result
        offset+=size
    raise ValueError('apk_manifest_invalid')


@contextmanager
def snapshot_apk(root, relative, expected_hash):
    path=PurePosixPath(relative)
    if (not isinstance(relative,str) or str(path)!=relative or path.is_absolute() or '..' in path.parts
        or not relative.startswith('android/app/build/outputs/apk/') or path.suffix!='.apk'
        or not re.fullmatch('[0-9a-f]{64}',expected_hash)):
        raise ValueError('apk_path_denied')
    descriptors=[]
    try:
        directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);descriptors.append(directory)
        for part in path.parts[:-1]:
            directory=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory);descriptors.append(directory)
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory);descriptors.append(fd)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=MAX_APK: raise ValueError('apk_file_denied')
        with tempfile.TemporaryDirectory(prefix='igorek-apk-delivery-') as directory:
            target=Path(directory)/"app-debug.apk";digest=hashlib.sha256();size=0
            with target.open('wb') as output:
                while chunk:=os.read(fd,65536):
                    size+=len(chunk)
                    if size>MAX_APK: raise ValueError('apk_file_too_large')
                    digest.update(chunk);output.write(chunk)
            if digest.hexdigest()!=expected_hash: raise ValueError('apk_hash_mismatch')
            metadata=apk_metadata(target)
            yield target,metadata
    finally:
        for fd in reversed(descriptors): os.close(fd)


def telegram_send(path, metadata):
    recipient = telegram_recipient()
    from ..vault.runtime import build_services
    decision=build_services(prompt=False).broker.get_ephemeral_env(service='telegram',agent_id='cc-backend',ttl=300)
    token=decision.env.get('FACTORY_TELEGRAM_BOT_TOKEN') if decision.allowed else None
    if not token: raise ValueError('apk_delivery_credentials_unavailable')
    caption=f"Igorek Remote {metadata['versionName']} (versionCode {metadata['versionCode']})"
    def call(method, arguments):
        config='url = '+json.dumps('https://api.telegram.org/bot'+token+'/'+method)+'\n'
        process=subprocess.run(['curl','--silent','--max-time','180','--config','-',*arguments],input=config,capture_output=True,text=True,timeout=190)
        try: reply=json.loads(process.stdout)
        except ValueError: raise ValueError('apk_delivery_transport_error') from None
        if process.returncode or reply.get('ok') is not True: raise ValueError('apk_delivery_send_failed')
        return reply['result']['message_id']
    call('sendMessage',['-H','Content-Type: application/json','--data-binary',json.dumps({'chat_id':recipient,'text':caption+'\nСборка из изолированной сессии; APK проверен хостовым шлюзом.'},ensure_ascii=False)])
    message=call('sendDocument',['--form',f'chat_id={recipient}','--form',f'caption={caption}',
                                 '--form',f'document=@{path}'])
    return {'ok':True,'curl_exit':0,'message_id':message,'version_name':metadata['versionName'],'version_code':metadata['versionCode']}


class ApkDelivery:
    def __init__(self,root,receipt_dir,*,sender=telegram_send):
        self.root=Path(root);self.receipt_dir=Path(receipt_dir);self.sender=sender
        self.revoked=threading.Event()
        self.nonce=secrets.token_hex(32)
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                if outer.revoked.is_set() or self.path!='/'+outer.nonce+'/send-apk': self.reply(403,{'ok':False,'error':'apk_route_denied'});return
                try:
                    lengths=self.headers.get_all('Content-Length') or []
                    if len(lengths)!=1 or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type')!='application/json': raise ValueError('apk_request_invalid')
                    size=int(lengths[0])
                    if not 0<size<=8192: raise ValueError('apk_request_invalid')
                    self.connection.settimeout(10)
                    payload=json.loads(self.rfile.read(size))
                    if not isinstance(payload,dict) or set(payload)!={'relative_path','sha256'} or not all(isinstance(v,str) for v in payload.values()): raise ValueError('apk_request_invalid')
                    result=outer.deliver(payload['relative_path'],payload['sha256'])
                    self.reply(200,result)
                except (ValueError,OSError,KeyError,IndexError,struct.error,zipfile.BadZipFile,subprocess.TimeoutExpired):
                    self.reply(400,{'ok':False,'error':'apk_delivery_refused'})
            def reply(self,status,result):
                data=json.dumps(result).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.send_header('Connection','close');self.end_headers();self.wfile.write(data);self.close_connection=True
        from .git_gateway import _Server
        self.server=_Server(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    @property
    def port(self): return self.server.server_address[1]
    @property
    def endpoint(self): return f'http://10.0.2.2:{self.port}/{self.nonce}/send-apk'
    def close(self): self.revoked.set();self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
    def deliver(self,relative,digest):
        with snapshot_apk(self.root,relative,digest) as (path,metadata):
            self.receipt_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
            with (self.receipt_dir/'delivery.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                if self.revoked.is_set(): raise ValueError('apk_grant_revoked')
                receipt=self.receipt_dir/(digest+'.json')
                if receipt.exists(): return {**json.loads(receipt.read_text()),'already_sent':True}
                result=self.sender(path,metadata)
                if result.get('ok') is not True or result.get('curl_exit')!=0 or not isinstance(result.get('message_id'),int): raise ValueError('apk_delivery_send_failed')
                fd,tmp=tempfile.mkstemp(dir=self.receipt_dir)
                try:
                    with os.fdopen(fd,'w') as handle: json.dump(result,handle);handle.flush();os.fsync(handle.fileno())
                    os.replace(tmp,receipt)
                finally: Path(tmp).unlink(missing_ok=True)
                return result


def send_apk_from_guest(file_path,workdir):
    endpoint=os.environ.get(ENV_ENDPOINT)
    if not endpoint: return json.dumps({'ok':False,'error':'apk_delivery_unavailable'})
    validate_endpoint(endpoint)
    root=Path(workdir).resolve();path=Path(file_path)
    if not path.is_absolute(): path=root/path
    relative=path.relative_to(root).as_posix()
    if '..' in PurePosixPath(relative).parts or not relative.startswith('android/app/build/outputs/apk/') or path.suffix!='.apk': raise ValueError('apk_path_denied')
    digest=hashlib.sha256();size=0
    with path.open('rb') as handle:
        while chunk:=handle.read(65536):
            size+=len(chunk)
            if size>MAX_APK: raise ValueError('apk_file_too_large')
            digest.update(chunk)
    digest=digest.hexdigest()
    url=urlsplit(endpoint);connection=http.client.HTTPConnection(url.hostname,url.port,timeout=400)
    try:
        connection.request('POST',url.path,body=json.dumps({'relative_path':relative,'sha256':digest}),headers={'Content-Type':'application/json'})
        response=connection.getresponse();payload=response.read(8192)
        return payload.decode()
    finally: connection.close()
