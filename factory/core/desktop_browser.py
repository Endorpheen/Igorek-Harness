"""Browser interaction over the desktop parent's private, profile-bound pipe."""
from __future__ import annotations

import json
import base64
import threading
import uuid

from .tool_registry import registry


_IMAGES = {}
_IMAGE_LOCK = threading.Lock()


def take_browser_image(result_text):
    try:
        identity=json.loads(result_text).get('screenshot',{}).get('id')
    except (ValueError,TypeError,AttributeError):
        return None
    if not isinstance(identity,str):return None
    with _IMAGE_LOCK:
        return _IMAGES.pop(identity,None)


SCHEMA = {
    "name": "desktop_browser",
    "description": "Постоянный браузер Игорька внутри desktop. read/open/post возвращают посты, текст и элементы интерфейса с ref и состоянием. elements с query ищет любые кнопки, меню, поля и переключатели по видимому названию/роли/data-testid. click нажимает элемент (button left/right/double, необязательные offset_x/offset_y 0..1 внутри элемента), hover раскрывает меню, fill заполняет обычное поле/contenteditable, press отправляет клавишу, select выбирает option по value или точной надписи, check устанавливает checkbox/switch/toggle, scroll_element прокручивает область. drag рисует или перетаскивает внутри выбранного элемента: target из elements.ref, points — от 2 до 128 точек {x,y} с координатами 0..1 относительно элемента; первая точка — нажатие левой кнопки, следующие — движение с зажатой кнопкой, в конце — отпускание. duration_ms 100..3000, по умолчанию 500. Для эллипса на canvas выбери инструмент фигуры и передай две противоположные точки. Для свободной линии передай её путь. После drag проверь результат через screenshot. ref берётся из последнего результата; stale_target требует перечитать страницу. wait ждёт ready/post/menu/element/idle (timeout_ms до 15000); elements с url ограничивает поиск выбранным постом. read_post url читает полный доступный текст порциями offset/limit и раскрывает Показать ещё; next_offset продолжает текст. comments url/cursor/limit читает следующие публикации ветки с явной relation; unknown нельзя выдавать за ответ. close_menu закрывает меню через Escape и проверяет результат; press Escape можно без target. screenshot сохраняет текущий кадр и возвращает изображение для просмотра. Реакции posts.reactions: true/false/null (неизвестно). Пароли/2FA вводит владелец. performed означает отправку ввода; результат проверяй по новому состоянию элементов. scroll прокручивает страницу, collect/result читают ленту. status показывает браузер.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "open", "read", "scroll", "post", "collect", "result", "click", "hover", "fill", "press", "select", "check", "scroll_element", "drag", "elements", "wait", "read_post", "comments", "close_menu", "screenshot"]},
        "url": {"type": "string", "description": "HTTPS URL для open; URL публикации X/Twitter для post."},
        "target": {"type":"string","description":"Точный elements.ref из последнего результата браузера."},
        "text": {"type":"string","description":"Текст для fill, максимум 16000 символов."},
        "key": {"type":"string","enum":["Enter","Space","Tab","Shift+Tab","Escape","ArrowUp","ArrowDown","ArrowLeft","ArrowRight","Home","End","PageUp","PageDown","Backspace","Delete"]},
        "query": {"type":"string","description":"Поиск элементов, до 200 символов."},
        "condition": {"type":"string","enum":["ready","post","menu","element","idle"]},
        "timeout_ms": {"type":"integer","minimum":100,"maximum":15000},
        "offset": {"type":"integer","minimum":0,"maximum":1000000},
        "limit": {"type":"integer","minimum":1,"maximum":6000},
        "cursor": {"type":"string"},
        "value": {"type":"string","description":"Значение или точная надпись option для select."},
        "checked": {"type":"boolean"},
        "direction": {"type":"string","enum":["up","down","left","right"]},
        "button": {"type":"string","enum":["left","right","double"]},
        "points": {"type":"array","minItems":2,"maxItems":128,"items":{"type":"object","properties":{"x":{"type":"number","minimum":0,"maximum":1},"y":{"type":"number","minimum":0,"maximum":1}},"required":["x","y"],"additionalProperties":False}},
        "duration_ms": {"type":"integer","minimum":100,"maximum":3000},
        "offset_x": {"type":"number","minimum":0,"maximum":1},
        "offset_y": {"type":"number","minimum":0,"maximum":1},
    }, "required": ["action"], "additionalProperties": False},
}


class DesktopBrowserPipe:
    def __init__(self, emit, allowed, timeout=45, channel='browser'):
        self.emit, self.allowed, self.timeout = emit, allowed, timeout
        self.pending = {}
        self.lock = threading.Lock()
        self.closed = False
        self.channel = channel

    def available(self):
        return not self.closed and self.allowed(None)

    def call(self, fields, **context):
        profile = context.get("profile")
        if self.closed or not self.allowed(profile):
            return json.dumps({"error": "desktop_browser_access_denied"})
        if not isinstance(fields, dict) or not isinstance(fields.get("action"), str):
            return json.dumps({"error": "invalid_request"})
        action = fields.get("action")
        expected = {"action", "url"} if action == "post" or (action == "open" and "url" in fields) else {"action"}
        interactions = {'click': set(), 'hover': set(), 'fill': {'text'}, 'press': {'key'},
                        'select': {'value'}, 'check': {'checked'}, 'scroll_element': {'direction'}, 'drag': {'points'}}
        if action in interactions:
            expected = {'action'} | interactions[action] | (set() if action == 'press' else {'target'})
        optional = {'button', 'offset_x', 'offset_y'} if action == 'click' else {'target'} if action == 'press' else {'query', 'url'} if action == 'elements' else {'duration_ms'} if action == 'drag' else set()
        reading = {'wait': ({'action'}, {'condition','query','url','timeout_ms'}), 'read_post': ({'action','url'}, {'offset','limit'}), 'comments': ({'action','url'}, {'cursor','limit'}), 'close_menu': ({'action'}, set()), 'screenshot': ({'action'}, set())}
        if action in reading:
            expected, optional = reading[action]
        if action not in SCHEMA['input_schema']['properties']['action']['enum'] or not expected <= set(fields) or set(fields) - expected - optional:
            return json.dumps({'error': 'invalid_request'})
        if 'url' in fields and (not isinstance(fields['url'], str) or len(fields['url']) > 2048):
            return json.dumps({'error': 'invalid_request'})
        if action in interactions and 'target' in fields:
            import re
            if not isinstance(fields['target'], str) or re.fullmatch(r'r-[a-f0-9]{32}-\d{1,4}', fields['target']) is None:
                return json.dumps({'error': 'invalid_request'})
        for name, maximum in (('text', 16000), ('query', 200), ('value', 1024)):
            if name in fields and (not isinstance(fields[name], str) or len(fields[name]) > maximum):
                return json.dumps({'error': 'invalid_request'})
        if 'key' in fields and fields['key'] not in SCHEMA['input_schema']['properties']['key']['enum']:
            return json.dumps({'error': 'invalid_request'})
        if 'checked' in fields and not isinstance(fields['checked'], bool):
            return json.dumps({'error': 'invalid_request'})
        if 'button' in fields and fields['button'] not in ('left', 'right', 'double'):
            return json.dumps({'error': 'invalid_request'})
        if 'direction' in fields and fields['direction'] not in ('up', 'down', 'left', 'right'):
            return json.dumps({'error': 'invalid_request'})
        import math
        if action == 'drag':
            points = fields.get('points')
            if not isinstance(points, list) or not 2 <= len(points) <= 128 or any(
                not isinstance(point, dict) or set(point) != {'x', 'y'} or any(
                    type(point[key]) not in (int, float) or not math.isfinite(point[key]) or not 0 <= point[key] <= 1
                    for key in ('x', 'y')) for point in points):
                return json.dumps({'error': 'invalid_request'})
        for name in ('offset_x', 'offset_y'):
            if name in fields and (type(fields[name]) not in (int, float) or not math.isfinite(fields[name]) or not 0 <= fields[name] <= 1):
                return json.dumps({'error': 'invalid_request'})
        if action == 'press' and 'target' not in fields and fields.get('key') not in ('Escape','Tab','Shift+Tab'):
            return json.dumps({'error':'invalid_request'})
        for key, minimum, maximum in (('duration_ms',100,3000),('timeout_ms',100,15000),('offset',0,1000000),('limit',1,20 if action=='comments' else 6000)):
            if key in fields and (type(fields[key]) is not int or not minimum <= fields[key] <= maximum):
                return json.dumps({'error':'invalid_request'})
        if 'condition' in fields and fields['condition'] not in ('ready','post','menu','element','idle'):
            return json.dumps({'error':'invalid_request'})
        if 'cursor' in fields and (not isinstance(fields['cursor'],str) or not __import__('re').fullmatch(r'[a-f0-9-]{36}', fields['cursor'])):
            return json.dumps({'error':'invalid_request'})
        return self.request(fields,profile)

    def request(self,fields,profile=None,timeout=None):
        if self.closed or not self.allowed(profile):
            return json.dumps({'error':'desktop_browser_access_denied'})
        identity, done, result = uuid.uuid4().hex, threading.Event(), {}
        with self.lock:
            if self.closed:
                return json.dumps({"error": "desktop_browser_unavailable"})
            result['action']=fields['action']
            self.pending[identity] = (done, result)
        try:
            self.emit({"type": self.channel + "_request", "id": identity, "fields": fields})
            if not done.wait(self.timeout if timeout is None else min(self.timeout,timeout)):
                return json.dumps({"error": "desktop_browser_timeout"})
            if self.closed or not self.allowed(profile):
                return json.dumps({"error": "desktop_browser_access_denied"})
            return json.dumps(result.get("value", {"error": "desktop_browser_unavailable"}), ensure_ascii=False)
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def receive(self, frame):
        if not isinstance(frame, dict) or set(frame) != {"type", "id", "result"} or frame["type"] != self.channel + "_reply":
            return False
        with self.lock:
            item = self.pending.get(frame["id"])
            if item is None:
                return False
            if not isinstance(frame["result"], dict) or len(json.dumps(frame["result"], ensure_ascii=False).encode()) > 2_000_000:
                return False
            done, result = item
            value=dict(frame['result'])
            encoded=value.pop('image_base64',None) if result.get('action')!='preview' else None
            if encoded is not None and result.get('action')=='screenshot':
                try:
                    raw=base64.b64decode(encoded,validate=True)
                    receipt=value['screenshot']['id']
                    if str(uuid.UUID(receipt))!=receipt or not raw.startswith(b'\xff\xd8\xff') or len(raw)>1300000:
                        raise ValueError()
                    with _IMAGE_LOCK:
                        _IMAGES[receipt]={'type':'image','source':{'type':'base64','media_type':'image/jpeg','data':encoded}}
                        while len(_IMAGES)>4:del _IMAGES[next(iter(_IMAGES))]
                except (ValueError,KeyError,TypeError):value={'error':'invalid_screenshot'}
            result["value"] = value
            done.set()
        return True

    def install(self):
        registry.register(name="desktop_browser", toolset="web", schema=SCHEMA,
                          handler=self.call, check_fn=self.available)

    def close(self):
        with self.lock:
            self.closed = True
            for done, _ in self.pending.values():
                done.set()
