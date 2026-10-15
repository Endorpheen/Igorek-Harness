"""Denied permissions must remain denied at the external protocol boundary."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def resolve_permission(*, approved, remember):
    path = Path(__file__).resolve().parents[1] / 'factory/core/codex_app_server_adapter.py'
    tree = ast.parse(path.read_text())
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                  and node.name == 'resolve_server_request')
    scope = {'Any': Any}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), scope)
    sent = []
    scope['resolve_server_request'](SimpleNamespace(_send=sent.append), request_id='fixture-id',
        method='item/permissions/requestApproval', params={'permissions': {'network': {'enabled': True}}},
        remember_for_session=remember, approved=approved)
    return sent[0]


@pytest.mark.parametrize('remember', [False, True])
def test_rejected_permission_never_grants_or_persists_scope(remember):
    assert resolve_permission(approved=False, remember=remember) == {
        'id': 'fixture-id', 'result': {'permissions': {}, 'scope': 'turn'}
    }


@pytest.mark.parametrize('remember,scope', [(False, 'turn'), (True, 'session')])
def test_approved_permission_keeps_requested_profile_and_scope(remember, scope):
    assert resolve_permission(approved=True, remember=remember) == {
        'id': 'fixture-id', 'result': {'permissions': {'network': {'enabled': True}}, 'scope': scope}
    }
