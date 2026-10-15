"""Calendar API adapter with desktop OAuth and explicit write confirmation."""
import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError
from zoneinfo import ZoneInfo
from .gmail import GmailConnection, NoRedirect, request_json

SERVICE = 'desktop-integration:google-calendar'
SCOPES = frozenset('https://www.googleapis.com/auth/'+name for name in (
    'calendar.events.owned', 'calendar.calendarlist.readonly', 'calendar.events.freebusy'))
API = 'https://www.googleapis.com/calendar/v3/'


class CalendarConnectError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def connection_error(error):
    if isinstance(error,CalendarConnectError):return error.code
    if isinstance(error,HTTPError):
        try:
            raw=error.read(65537)
            if len(raw)>65536:return 'calendar_authorization_failed'
            body=json.loads(raw).get('error',{})
            reasons={entry.get('reason') for entry in body.get('errors',[])+body.get('details',[]) if isinstance(entry,dict)} if isinstance(body,dict) else set()
            if error.code==403 and reasons & {'SERVICE_DISABLED','accessNotConfigured'}:
                project=''
                for entry in body.get('details',[]):
                    metadata=entry.get('metadata',{}) if isinstance(entry,dict) else {}
                    if not isinstance(metadata,dict):continue
                    consumer=metadata.get('consumer','')
                    if isinstance(consumer,str) and consumer.startswith('projects/'):project=consumer[9:]
                    if not project and isinstance(metadata.get('activationUrl'),str):
                        url=urllib.parse.urlsplit(metadata['activationUrl'])
                        if url.scheme=='https' and url.hostname in {'console.cloud.google.com','console.developers.google.com'}:
                            project=urllib.parse.parse_qs(url.query).get('project',[''])[0]
                    if project:break
                url='https://console.cloud.google.com/apis/library/calendar-json.googleapis.com'
                if re.fullmatch(r'(?:[0-9]{1,24}|[a-z][a-z0-9-]{4,62})',project):url+='?'+urllib.parse.urlencode({'project':project})
                error.activation_url=url
                return 'calendar_api_disabled'
        except Exception:pass
    return 'calendar_authorization_failed'


def calendar_json(path, *, method='GET', data=None, token='', headers=None):
    if not isinstance(path,str) or path.startswith('/') or '..' in path.split('/'):
        raise ValueError('Invalid Calendar path')
    request = urllib.request.Request(API+path, None if data is None else json.dumps(data).encode(),
        {'Authorization':'Bearer '+token,'Accept':'application/json','Content-Type':'application/json',**(headers or {})}, method=method)
    with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
        raw=response.read(1024*1024+1)
    if len(raw)>1024*1024:
        raise ValueError('Calendar response too large')
    value=json.loads(raw or b'{}')
    if not isinstance(value,dict):raise ValueError('Invalid Calendar response')
    return value


class CalendarConnection(GmailConnection):
    def binding_digest(self):
        data=self._data()
        return hashlib.sha256(json.dumps({k:data.get(k,'') for k in ('client_id','refresh_token','calendar_id')},sort_keys=True).encode()).hexdigest()
    def _record(self):
        return next((r for r in self.lifecycle.list_credentials() if r.service==SERVICE and r.imported_from=='desktop-calendar'),None)

    def _store(self,data):
        self.lifecycle.add_credential(SERVICE,json.dumps(data),'oauth_token',imported_from='desktop-calendar',scopes=sorted(SCOPES),replace_existing=True)

    def status(self):
        value=super().status()
        data=self._data() if self._record() else {}
        return {**value,'calendar_id':data.get('calendar_id','primary'),'calendar_name':data.get('calendar_name',''),'time_zone':data.get('time_zone',''),
                'pending_profile':getattr(self,'connect_profile','') if value['pending'] else '',
                'activation_url':self.pending.get('activation_url','') if isinstance(self.pending,dict) and self.pending.get('error')=='calendar_api_disabled' else ''}

    def begin(self,password):
        self.lifecycle.authenticate(password)
        return self._begin()

    def automatic_begin(self, on_connected, allowed):
        if not allowed():raise CalendarConnectError('vault_locked')
        if self.thread and self.thread.is_alive():raise CalendarConnectError('operation_busy')
        if not self.status()['configured']:
            gmail=GmailConnection(self.lifecycle)
            if not gmail.status()['configured']:raise CalendarConnectError('google_not_configured')
            data=gmail._data();client=data.get('client_id');secret=data.get('client_secret')
            if not isinstance(client,str) or not client.endswith('.apps.googleusercontent.com') or len(client)>512 or not isinstance(secret,str) or not 1<=len(secret)<=512:
                raise CalendarConnectError('google_not_configured')
            self._store({'client_id':client,'client_secret':secret})
        return self._begin(on_connected,allowed)

    def _begin(self,on_connected=None,allowed=lambda:True):
        if self.thread and self.thread.is_alive():raise ValueError('OAuth busy')
        data=self._data();verifier=secrets.token_urlsafe(48);state=secrets.token_urlsafe(32)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        self.cancel.clear();self.pending={};outer=self
        class Callback(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if len(self.path)>16384:self.send_error(400);return
                parsed=urllib.parse.urlsplit(self.path);values=urllib.parse.parse_qs(parsed.query)
                valid=parsed.path=='/callback' and values.get('state')==[state]
                if valid and 'error' in values:outer.pending={'error':'calendar_authorization_failed'}
                elif valid and len(values.get('code',[]))==1 and 0<len(values['code'][0])<=4096:outer.pending={'code':values['code'][0]}
                else:valid=False
                self.send_response(200 if valid else 400);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers()
                self.wfile.write('Вернитесь в приложение Игорёк.'.encode())
        server=HTTPServer(('127.0.0.1',0),Callback);server.timeout=.25
        redirect='http://127.0.0.1:'+str(server.server_port)+'/callback'
        def run():
            try:
                deadline=time.monotonic()+180
                while not self.cancel.is_set() and not self.pending and time.monotonic()<deadline:server.handle_request()
                code=self.pending.get('code') if self.pending else None
                if not code or self.cancel.is_set() or not allowed():raise ValueError()
                token=request_json('https://oauth2.googleapis.com/token',data={'client_id':data['client_id'],'client_secret':data['client_secret'],'code':code,'code_verifier':verifier,'redirect_uri':redirect,'grant_type':'authorization_code'})
                if not SCOPES.issubset(set(token.get('scope','').split())):raise CalendarConnectError('calendar_permissions_missing')
                if not token.get('refresh_token') or not token.get('access_token'):raise ValueError()
                with self.mutex:
                    if self.cancel.is_set() or not allowed():raise ValueError()
                    self._store({**data,'refresh_token':token['refresh_token'],'scope':' '.join(sorted(SCOPES))});self.pending={}
                if on_connected:on_connected()
            except Exception as error:
                self.pending={'error':connection_error(error),'activation_url':getattr(error,'activation_url','')}
            finally:server.server_close()
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
        return 'https://accounts.google.com/o/oauth2/v2/auth?'+urllib.parse.urlencode({'client_id':data['client_id'],'redirect_uri':redirect,'response_type':'code','scope':' '.join(sorted(SCOPES)),'state':state,'code_challenge':challenge,'code_challenge_method':'S256','access_type':'offline','prompt':'consent select_account'})

    def disconnect(self,password):
        self.lifecycle.authenticate(password);self.cancel.set()
        with self.mutex:
            data=self._data()
            gmail=GmailConnection(self.lifecycle)
            shared=gmail.status()['configured'] and gmail._data().get('client_id')==data.get('client_id')
            # Google revocation invalidates the whole project, including Gmail.
            if data.get('refresh_token') and not shared:request_json('https://oauth2.googleapis.com/revoke',data={'token':data['refresh_token']})
            self._store({k:v for k,v in data.items() if k in {'client_id','client_secret'}});self.pending=None
        return self.status()

    def detach(self):
        from ..vault.lifecycle import VaultLockedError
        if self.lifecycle.state!='unlocked':raise VaultLockedError()
        self.cancel.set()
        with self.mutex:
            data=self._data() if self._record() else {}
            if data:self._store({k:v for k,v in data.items() if k in {'client_id','client_secret'}})
            self.pending=None
        return self.status()

    def request(self,path,*,method='GET',data=None,headers=None):
        with self.mutex:
            credentials=self._data()
            token=request_json('https://oauth2.googleapis.com/token',data={'client_id':credentials['client_id'],'client_secret':credentials['client_secret'],'refresh_token':credentials['refresh_token'],'grant_type':'refresh_token'})
            if token.get('scope') and not SCOPES.issubset(set(token['scope'].split())):raise PermissionError()
            if not isinstance(token.get('access_token'),str) or not token['access_token']:raise PermissionError()
            return calendar_json(path,method=method,data=data,token=token['access_token'],headers=headers)

    def calendar(self,identity=None):
        identity=identity or self._data().get('calendar_id','primary')
        value=self.request('users/me/calendarList/'+urllib.parse.quote(identity,safe=''))
        if not isinstance(value.get('id'),str) or not value['id'] or value.get('accessRole')!='owner':raise PermissionError()
        zone=value.get('timeZone');ZoneInfo(zone)
        return {'id':value['id'],'name':str(value.get('summary',''))[:300],'time_zone':zone}

    def check(self,identity=None):
        info=self.calendar(identity);now=datetime.now(ZoneInfo(info['time_zone']))
        events=self.request('calendars/'+urllib.parse.quote(info['id'],safe='')+'/events?'+urllib.parse.urlencode({'timeMin':now.isoformat(),'timeMax':(now+timedelta(days=1)).isoformat(),'singleEvents':'true','maxResults':1}))
        if not isinstance(events.get('items',[]),list):raise ValueError()
        with self.mutex:
            self._store({**self._data(),'calendar_id':info['id'],'calendar_name':info['name'],'time_zone':info['time_zone']})
        return {'ok':True,'time_zone':info['time_zone'],'calendar_name':info['name'],'read_checked':True}

    def calendars(self):
        result=self.request('users/me/calendarList?minAccessRole=owner&maxResults=250')
        return [{'id':v['id'],'name':str(v.get('summary',''))[:300],'time_zone':v.get('timeZone','')} for v in result.get('items',[]) if isinstance(v,dict) and isinstance(v.get('id'),str) and v.get('accessRole')=='owner']

    def select(self,identity,password):
        self.lifecycle.authenticate(password)
        if identity not in {v['id'] for v in self.calendars()}:raise ValueError('Unknown owned calendar')
        return self.check(identity)


def tool(name,description,properties,required=()):
    return {'name':name,'description':description,'inputSchema':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}


def instant(value):
    if not isinstance(value,str) or len(value)>80:raise ValueError('RFC3339 required')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('Explicit UTC offset required')
    return parsed


def event_times(start,end,zone):
    if isinstance(start,str) and isinstance(end,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',start) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',end):
        a,b=date.fromisoformat(start),date.fromisoformat(end)
        if b<=a or (b-a).days>366:raise ValueError('Invalid date range')
        return {'start':{'date':start},'end':{'date':end}}
    a,b=instant(start),instant(end)
    if b<=a or (b-a).total_seconds()>366*86400:raise ValueError('Invalid time range')
    return {'start':{'dateTime':a.isoformat(),'timeZone':zone},'end':{'dateTime':b.isoformat(),'timeZone':zone}}


class CalendarClient:
    def __init__(self,connection,check,approve):
        self.connection=connection;self.check=check;self.approve=approve;self.seen=set();self.observed_etags={};self.observed_events={};self.event_id_factory=lambda:secrets.token_hex(16)
        string={'type':'string'};bounds={'start':string,'end':string}
        self.tools=[tool('calendar_info','Current time, timezone and selected owned calendar. Call before resolving relative dates.',{}),
          tool('calendar_list_events','List events for an explicit RFC3339 interval (inclusive start, exclusive end).',bounds,('start','end')),
          tool('calendar_find_free_time','Find free intervals in the selected calendar. Explicit RFC3339 range.',{**bounds,'duration_minutes':{'type':'integer','minimum':1,'maximum':1440}},('start','end','duration_minutes')),
          tool('calendar_create_event','Create after owner confirmation. No guests unless explicitly requested and allow_invitations=true. All-day end date is exclusive.',{**bounds,'summary':string,'description':string,'location':string,'attendees':{'type':'array','items':string,'maxItems':20},'allow_invitations':{'type':'boolean'}},('summary','start','end')),
          tool('calendar_move_event','Move one event ID returned by calendar_list_events, after owner confirmation. Preserve participants; send no invitations.',{**bounds,'event_id':string},('event_id','start','end'))]

    def start(self):
        if not self.check() or not self.connection.status()['connected']:raise PermissionError()

    def stop(self):pass

    def call_tool(self,name,args):
        if not self.check():raise PermissionError()
        try:return self._call(name,args)
        except (ValueError,KeyError,TypeError):return json.dumps({'error':'invalid_calendar_arguments','message':'Use the declared schema, explicit dates/UTC offsets and an event ID from calendar_list_events. Ask the user to clarify ambiguous dates or events.'})

    def _call(self,name,args):
        schema=next((t['inputSchema'] for t in self.tools if t['name']==name),None)
        if schema is None or not isinstance(args,dict) or set(args)-set(schema['properties']) or not set(schema['required']).issubset(args):raise ValueError()
        info=self.connection.calendar();self.last_calendar_info=info;zone=info['time_zone'];prefix='calendars/'+urllib.parse.quote(info['id'],safe='')+'/events'
        if name=='calendar_info':return json.dumps({**info,'now':datetime.now(ZoneInfo(zone)).isoformat()},ensure_ascii=False)
        if name in {'calendar_list_events','calendar_find_free_time'}:
            a,b=instant(args['start']),instant(args['end'])
            if b<=a or (b-a).total_seconds()>366*86400:raise ValueError()
            if name=='calendar_list_events':
                response=self.connection.request(prefix+'?'+urllib.parse.urlencode({'timeMin':a.isoformat(),'timeMax':b.isoformat(),'singleEvents':'true','orderBy':'startTime','maxResults':100,'timeZone':zone}))
                events=[];size=0;truncated=bool(response.get('nextPageToken'))
                for item in response.get('items',[]):
                    if item.get('status')=='cancelled':continue
                    value={k:item.get(k) for k in ('id','summary','start','end','location','htmlLink')}
                    size+=len(json.dumps(value,ensure_ascii=False).encode())+2
                    if size>60000:truncated=True;break
                    self.seen.add(item['id']);events.append(value)
                    self.observed_events[item['id']]=value
                    if isinstance(item.get('etag'),str):self.observed_etags[item['id']]=item['etag']
                return json.dumps({'time_zone':zone,'events':events,'truncated':truncated},ensure_ascii=False)
            duration=args['duration_minutes']
            if type(duration) is not int or not 1<=duration<=1440:raise ValueError()
            response=self.connection.request('freeBusy',method='POST',data={'timeMin':a.isoformat(),'timeMax':b.isoformat(),'timeZone':zone,'items':[{'id':info['id']}]})
            calendar=response['calendars'][info['id']]
            if calendar.get('errors'):return json.dumps({'error':'calendar_availability_unavailable'})
            cursor=a.astimezone(timezone.utc);end=b.astimezone(timezone.utc);gaps=[];minimum=timedelta(minutes=duration)
            for item in sorted(calendar.get('busy',[]),key=lambda v:instant(v['start'])):
                begin,finish=instant(item['start']).astimezone(timezone.utc),instant(item['end']).astimezone(timezone.utc)
                if begin>cursor and min(begin,end)-cursor>=minimum:gaps.append((cursor,min(begin,end)))
                cursor=max(cursor,finish)
            if end-cursor>=minimum:gaps.append((cursor,end))
            return json.dumps({'time_zone':zone,'slots':[{'start':x.astimezone(ZoneInfo(zone)).isoformat(),'end':y.astimezone(ZoneInfo(zone)).isoformat()} for x,y in gaps[:20]],'truncated':len(gaps)>20})
        times=event_times(args['start'],args['end'],zone)
        if name=='calendar_create_event':
            title=args['summary']
            if not isinstance(title,str) or not title.strip() or len(title)>500:raise ValueError()
            guests=args.get('attendees',[])
            if not isinstance(guests,list) or len(guests)>20 or any(not isinstance(v,str) or not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+',v) or len(v)>254 for v in guests):raise ValueError()
            if type(args.get('allow_invitations',False)) is not bool or (guests and args.get('allow_invitations') is not True):raise ValueError()
            body={'summary':title,**times,'id':self.event_id_factory()}
            for key in ('description','location'):
                if key in args:
                    if not isinstance(args[key],str) or len(args[key])>4000:raise ValueError()
                    body[key]=args[key]
            if guests:body['attendees']=[{'email':v} for v in guests]
            preview={'operation':'create','summary':title,**times,'time_zone':zone,'attendees':guests,'description':body.get('description',''),'location':body.get('location','')}
            if not self.approve(preview):return json.dumps({'error':'owner_declined','changed':False})
            if not self.check():raise PermissionError()
            result=self.connection.request(prefix+'?sendUpdates='+('all' if guests else 'none'),method='POST',data=body)
        else:
            identity=args['event_id']
            if not isinstance(identity,str) or identity not in self.seen:raise ValueError()
            path=prefix+'/'+urllib.parse.quote(identity,safe='');previous=self.connection.request(path)
            etag=previous.get('etag')
            if not isinstance(etag,str) or not etag or any(ord(c)<32 for c in etag):raise ValueError()
            if self.observed_etags.get(identity) and self.observed_etags[identity]!=etag:
                return json.dumps({'error':'calendar_event_changed','changed':False})
            preview={'operation':'move','summary':str(previous.get('summary',''))[:500],**times,'previous_start':previous.get('start'),'previous_end':previous.get('end'),'time_zone':zone,'attendees':[]}
            if not self.approve(preview):return json.dumps({'error':'owner_declined','changed':False})
            if not self.check():raise PermissionError()
            result=self.connection.request(path+'?sendUpdates=none',method='PATCH',data=times,headers={'If-Match':etag})
        return json.dumps({'changed':True,'event':{k:result.get(k) for k in ('id','summary','start','end','htmlLink')}},ensure_ascii=False)
