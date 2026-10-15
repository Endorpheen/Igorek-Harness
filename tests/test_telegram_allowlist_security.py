import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def bridge_class():
    source = Path(__file__).parents[1] / 'factory/core/telegram_bridge.py'
    tree = ast.parse(source.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'TelegramBridge')
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                and node.name in {'__init__', '_is_allowed', '_handle_update', '_handle_callback_query'}]
    scope = dict(Any=object, Path=Path, _ensure_text=lambda value: str(value or '').strip())
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), 'exec'), scope)
    return scope['TelegramBridge']


@pytest.mark.parametrize('users,chats', [(set(), set()), ({' ', ''}, set()), (set(), {'42'})])
def test_startup_requires_owner_user_ids_before_external_clients(users, chats):
    with pytest.raises(ValueError, match='allowed user_id'):
        bridge_class()(token='fixture', profile='fixture', runtime_api_base_url='http://127.0.0.1',
                       runtime_api_key='fixture', allowed_user_ids=users, allowed_chat_ids=chats)


def test_empty_runtime_allowlist_fails_closed_for_messages_and_callbacks():
    cls = bridge_class()
    bridge = cls.__new__(cls)
    bridge.allowed_user_ids = set()
    bridge.allowed_chat_ids = set()
    denied = []
    bridge._safe_send = lambda *args: denied.append(args)
    bridge._extract_telegram_attachments = lambda **kwargs: pytest.fail('unauthorized attachment')
    bridge.telegram = SimpleNamespace(answer_callback_query=lambda *args: pytest.fail('unauthorized callback'))
    bridge._handle_update({'message': {'chat': {'id': 42}, 'from': {'id': 7}, 'text': 'run task'}})
    bridge._handle_callback_query({'message': {'chat': {'id': 42}}, 'from': {'id': 7}, 'id': 'cb', 'data': 'mcp:task:once'})
    assert len(denied) == 1
    assert not bridge._is_allowed(user_id='7', chat_id='42')


def test_owner_and_optional_chat_filter_remain_compatible():
    cls = bridge_class()
    bridge = cls.__new__(cls)
    bridge.allowed_user_ids = {'7'}
    bridge.allowed_chat_ids = set()
    assert bridge._is_allowed(user_id='7', chat_id='42')
    assert not bridge._is_allowed(user_id='8', chat_id='42')
    bridge.allowed_chat_ids = {'42'}
    assert bridge._is_allowed(user_id='7', chat_id='42')
    assert not bridge._is_allowed(user_id='7', chat_id='43')
