"""Bounded browser frames and explicit tab selection for authenticated mobile clients."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from urllib.parse import urlsplit


class DesktopBrowserPreview:
    def __init__(self,pipe,clock=time.monotonic):
        self.pipe=pipe;self.clock=clock;self.lock=threading.Lock();self.cached=None;self.fetched=0.0

    def read(self,after=''):
        if not self.pipe.available():
            self.cached=None
            return {'available':False,'ready':False,'reason':'desktop_unavailable'}
        if not self.lock.acquire(blocking=False):
            return {'available':True,'ready':False,'reason':'busy'}
        try:
            if self.cached is None or self.clock()-self.fetched>=2:
                try:raw=json.loads(self.pipe.request({'action':'preview'},timeout=8))
                except (ValueError,TypeError,OSError):raw={}
                self.cached=self.normalize(raw);self.fetched=self.clock()
            if not self.pipe.available():
                self.cached=None
                return {'available':False,'ready':False,'reason':'desktop_unavailable'}
            result=dict(self.cached)
            if result.get('ready') and after==result.get('frame_id'):
                result.pop('image_base64',None);result['unchanged']=True
            return result
        finally:self.lock.release()

    def select(self, tab_id):
        if not isinstance(tab_id, str) or not re.fullmatch('[-a-zA-Z0-9]{1,64}', tab_id):
            raise ValueError('invalid browser tab')
        if not self.pipe.available():
            self.cached = None
            return {'selected': False, 'reason': 'desktop_unavailable'}
        if not self.lock.acquire(blocking=False):
            return {'selected': False, 'reason': 'busy'}
        try:
            try:
                raw = json.loads(self.pipe.request({'action': 'select_preview_tab', 'tab_id': tab_id}, timeout=8))
            except (ValueError, TypeError, OSError):
                raw = {}
            self.cached = None
            if not self.pipe.available():
                return {'selected': False, 'reason': 'desktop_unavailable'}
            if isinstance(raw, dict) and raw.get('selected') is True and raw.get('tab_id') == tab_id:
                return {'selected': True, 'tab_id': tab_id}
            reason = raw.get('reason') if isinstance(raw, dict) else None
            return {'selected': False, 'reason': reason if isinstance(reason, str) and reason in {'desktop_locked', 'browser_closed', 'browser_hidden', 'browser_changed', 'busy'} else 'preview_failed'}
        finally:
            self.lock.release()

    @staticmethod
    def tabs(raw):
        tabs = []
        for tab in raw.get('tabs', [])[:24] if isinstance(raw.get('tabs'), list) else []:
            if not isinstance(tab, dict) or not isinstance(tab.get('id'), str) or not re.fullmatch('[-a-zA-Z0-9]{1,64}', tab['id']) or any(item['id'] == tab['id'] for item in tabs):
                continue
            url = ''
            try:
                value = urlsplit(tab.get('url', ''))
                if value.scheme == 'https' and value.hostname and not value.username and not value.password:
                    url = (value.scheme + '://' + value.netloc + value.path)[:512]
            except (ValueError, TypeError):
                pass
            tabs.append({'id': tab['id'], 'title': str(tab.get('title', ''))[:120], 'url': url})
        active = raw.get('active_tab_id', '')
        return {'tabs': tabs, 'active_tab_id': active if any(tab['id'] == active for tab in tabs) else ''}

    @staticmethod
    def normalize(raw):
        result={'available':True,'ready':False}
        if not isinstance(raw,dict):return {**result,'reason':'preview_failed'}
        if raw.get('ready') is not True:
            reason=raw.get('reason')
            allowed={'desktop_locked','manual_control','browser_paused','browser_closed','browser_hidden','browser_changed','busy','preview_failed'}
            return {**result,**(DesktopBrowserPreview.tabs(raw) if 'tabs' in raw and reason != 'desktop_locked' else {}),'reason':reason if isinstance(reason,str) and reason in allowed else 'preview_failed'}
        try:
            encoded=raw['image_base64'];identity=raw['frame_id']
            if not isinstance(encoded,str) or len(encoded)>426668 or not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{64}',identity):raise ValueError()
            image=base64.b64decode(encoded,validate=True)
            if len(image)>320000 or not image.startswith(b'\xff\xd8\xff') or hashlib.sha256(image).hexdigest()!=identity:raise ValueError()
            if any(type(raw.get(key)) is not int or not 1<=raw[key]<=960 for key in ('width','height')):raise ValueError()
            if type(raw.get('captured_at')) is not int or raw['captured_at']<=0:raise ValueError()
            if not isinstance(raw.get('url',''),str) or not isinstance(raw.get('title',''),str):raise ValueError()
            url=urlsplit(raw.get('url',''))
            address=(url.scheme+'://'+url.netloc+url.path) if url.scheme=='https' and url.hostname and not url.username and not url.password else ''
            return {**result,**(DesktopBrowserPreview.tabs(raw) if 'tabs' in raw else {}),'ready':True,'url':address[:4096],'title':str(raw.get('title',''))[:160],
                'loading':raw.get('loading') is True,'captured_at':raw['captured_at'],'width':raw['width'],'height':raw['height'],
                'frame_id':identity,'image_base64':encoded}
        except (ValueError,TypeError,KeyError):return {**result,'reason':'preview_failed'}
