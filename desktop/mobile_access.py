"""Owner-enabled HTTPS access to this desktop's existing mobile bridge."""
from __future__ import annotations
import hashlib
import ipaddress
import json
import shutil
import ssl
import subprocess
import threading
from urllib.parse import urlsplit
from pathlib import Path


class MobileAccessError(ValueError):
    def __init__(self, code):
        self.code=code
        super().__init__(code)


def public_address(value):
    if not isinstance(value,str) or len(value)>1024 or any(ord(char)<32 for char in value):
        raise MobileAccessError('mobile_address_invalid')
    value=value.strip()
    if not value:return ''
    if '://' not in value:value='https://'+value
    try:
        parsed=urlsplit(value)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('','/')
                or any(char.isspace() for char in parsed.netloc) or parsed.netloc.endswith(':')
                or '\\' in parsed.netloc or '?' in value or '#' in value
                or not 1<=(443 if parsed.port is None else parsed.port)<=65535):raise ValueError()
    except ValueError:raise MobileAccessError('mobile_address_invalid') from None
    return value.rstrip('/')


def network_addresses():
    try:
        executable=shutil.which('ip',path='/usr/sbin:/sbin:/usr/bin:/bin')
        if executable is None:return []
        result=subprocess.run([executable,'-j','address','show','scope','global'],capture_output=True,text=True,timeout=2,check=True)
        addresses=[]
        for interface in json.loads(result.stdout):
            if 'UP' not in interface.get('flags',[]):continue
            for value in interface.get('addr_info',[]):
                if value.get('family')!='inet':continue
                address=value.get('local','');parsed=ipaddress.ip_address(address)
                if parsed.version==4 and not parsed.is_loopback and not parsed.is_link_local:
                    addresses.append(address)
        return sorted(set(addresses),key=lambda a:(not a.startswith('192.168.'),not a.startswith('100.'),a))
    except (OSError,ValueError,subprocess.SubprocessError):return []


class DesktopMobileAccess:
    def __init__(self,bridge,cert,key,root=None):
        self.bridge,self.cert,self.key=bridge,Path(cert),Path(key)
        self.settings=Path(root or self.cert.parent)/'mobile-access.json'
        self.port=47823;self.public_url='';self.resume_enabled=False;self.restore_error=''
        try:
            saved=json.loads(self.settings.read_text())
            if saved.get('schema_version')==2 and type(saved.get('port')) is int and 1024<=saved['port']<=65535:
                self.public_url=public_address(saved.get('public_url',''));self.port=saved['port']
                self.resume_enabled=saved.get('enabled') is True
        except (OSError,ValueError,KeyError):pass
        self.server=None;self.thread=None;self.lock=threading.RLock()

    def save(self):
        temporary=self.settings.with_suffix('.tmp')
        temporary.write_text(json.dumps({'schema_version':2,'port':self.port,'public_url':self.public_url,'enabled':self.resume_enabled})+'\n')
        temporary.chmod(0o600);temporary.replace(self.settings)

    def configure(self,port,public_url):
        if not isinstance(port,str) or not port.isascii() or not port.isdecimal() or not 1024<=int(port)<=65535:
            raise MobileAccessError('mobile_port_invalid')
        address=public_address(public_url)
        with self.lock:
            if self.server and int(port)!=self.port:self.close()
            self.port=int(port);self.public_url=address;self.save()
            return self.status()

    def status(self):
        with self.lock:
            devices=[d for d in self.bridge.store.list_devices(include_revoked=True) if d.get('platform','').lower() in {'android','ios'}]
            if self.server is None:return {'enabled':False,'devices':devices,'port':self.port,'public_url':self.public_url,'auto_resume':self.resume_enabled,'restore_error':self.restore_error}
            port=self.server.server_address[1]
            der=ssl.PEM_cert_to_DER_cert(self.cert.read_text())
            fingerprint=hashlib.sha256(der).hexdigest().upper()
            urls=['https://'+address+':'+str(port) for address in network_addresses()]
            if self.public_url:urls=[self.public_url]+[url for url in urls if url!=self.public_url]
            return {'enabled':True,'devices':devices,'urls':urls,'public_url':self.public_url,'auto_resume':self.resume_enabled,
                    'port':port,'fingerprint':fingerprint,'profile':self.bridge.service.profile,
                    'profiles':self.bridge.service.available_profiles() if self.bridge.service.available_profiles else []}

    def listen(self):
        with self.lock:
            if self.server is None:
                from factory.core.mobile_bridge.server import build_server
                try:server=build_server(self.bridge,host='0.0.0.0',port=self.port)
                except OSError:raise MobileAccessError('mobile_port_unavailable') from None
                try:
                    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
                    context.load_cert_chain(str(self.cert),str(self.key));server.enable_tls(context)
                except Exception:server.server_close();raise
                self.port=server.server_address[1]
                self.server=server;self.thread=threading.Thread(target=server.serve_forever,daemon=True,name='desktop-mobile-https');self.thread.start()
            self.restore_error=''

    def start(self):
        with self.lock:
            self.listen();self.resume_enabled=True;self.save()
            status=self.status();pairing=self.bridge.store.create_pairing_code(created_by='desktop-mobile-owner',ttl_s=300,replace_previous=True)
            if not status['urls']:return {**status,'pairing':pairing,'qr_error':'network_address_missing'}
            from factory.core.mobile_bridge.qr import pairing_qr
            return {**status,'pairing':pairing,'qr':pairing_qr(status['urls'][0],pairing['code'],status['fingerprint'],pairing['expires_at'])}

    def restore(self):
        with self.lock:
            if self.resume_enabled:
                try:self.listen()
                except MobileAccessError as error:self.restore_error=error.code
                except (OSError,ValueError):self.restore_error='mobile_restore_failed'
            return self.status()

    def disable(self):
        with self.lock:
            self.resume_enabled=False;self.restore_error='';self.save()
            self.close();return self.status()

    def revoke(self,device_id):
        device=self.bridge.store.get_device(device_id)
        if not device or device.get('platform','').lower() not in {'android','ios'}:raise ValueError('Unknown mobile device')
        self.bridge.revoke_device(device_id)
        return self.status()

    def close(self):
        with self.lock:
            if self.server:
                self.server.shutdown();self.thread.join(timeout=3)
                if hasattr(self.server,'terminals'):self.server.terminals.close_all()
                self.server.server_close();self.server=None;self.thread=None
        return {'enabled':False}
