"""Small declarative argument policy shared with Python 3.11 service adapters."""
import math
import re
import threading
import time

RPC_METHODS = {'initialize', 'ping', 'notifications/initialized', 'tools/list', 'tools/call'}


def validate(schema, value, depth=0):
    if depth > 12 or not isinstance(schema, dict):
        raise ValueError('invalid_service_policy')
    kind = schema.get('type')
    good = {'object': isinstance(value, dict), 'array': isinstance(value, list),
            'string': isinstance(value, str), 'integer': type(value) is int,
            'number': type(value) in (int, float), 'boolean': type(value) is bool,
            'null': value is None}.get(kind, False)
    if not good:
        raise ValueError('invalid_argument_type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('argument_not_allowed')
    if 'const' in schema and value != schema['const']:
        raise ValueError('argument_not_allowed')
    if kind == 'object':
        properties = schema.get('properties', {})
        if set(value) - set(properties) or set(schema.get('required', [])) - set(value):
            raise ValueError('invalid_argument_fields')
        for key, item in value.items():
            validate(properties[key], item, depth + 1)
    elif kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 100):
            raise ValueError('argument_out_of_range')
        for item in value:
            validate(schema['items'], item, depth + 1)
    elif kind == 'string':
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 16000):
            raise ValueError('argument_out_of_range')
        if 'pattern' in schema and re.fullmatch(schema['pattern'], value) is None:
            raise ValueError('invalid_argument_value')
    elif kind in ('integer', 'number'):
        if not math.isfinite(value) or value < schema.get('minimum', -1e100) or value > schema.get('maximum', 1e100):
            raise ValueError('argument_out_of_range')


def validate_rpc(message, tools):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or message.get('method') not in RPC_METHODS:
        raise ValueError('service_method_not_allowed')
    if message['method'] == 'tools/call':
        fields = message.get('params') or {}
        if not isinstance(fields, dict) or set(fields) - {'name', 'arguments'} or not isinstance(fields.get('name'), str) or fields['name'] not in tools:
            raise ValueError('service_tool_not_allowed')
        validate(tools[fields['name']]['input_schema'], fields.get('arguments', {}))


class IdleShutdown:
    def __init__(self, seconds, clock=time.monotonic):
        if type(seconds) is not int or not 30 <= seconds <= 3600:
            raise ValueError('invalid_idle_timeout')
        self.seconds = seconds
        self.clock = clock
        self.last_use = clock()
        self.lock = threading.Lock()

    def touch(self):
        with self.lock:
            self.last_use = self.clock()

    def expired(self, busy):
        with self.lock:
            now = self.clock()
            if busy:
                self.last_use = now
            return not busy and now - self.last_use >= self.seconds
