"""Web routes reject malformed state-changing input before bridge calls."""
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from factory.web_ui.server import _Handler


@pytest.fixture
def handler():
    obj = object.__new__(_Handler)
    obj.server = SimpleNamespace(server_port=9911)
    obj._check_origin = Mock(return_value=True)
    obj._json = Mock()
    obj._proxy = Mock()
    obj._body = Mock(return_value={})
    obj._session_journal = Mock()
    obj._events = Mock()
    obj._download = Mock()
    return obj


def dispatch(handler, method, path, body=None):
    handler.path = path
    handler._body.return_value = body
    handler._dispatch(method)


@pytest.mark.parametrize('body', [None, {}, {'profile': 3}, {'profile':'../outside'}, {'profile':'valid','extra':1}])
def test_invalid_profile_change_never_reaches_bridge(handler,body):
    dispatch(handler,'POST','/api/sessions/chat-1/profile',body)
    handler._proxy.assert_not_called()
    if body is not None:
        assert handler._json.call_args.args[0] == 400


def test_valid_profile_change_is_forwarded_once(handler):
    dispatch(handler,'POST','/api/sessions/chat-1/profile',{'profile':'flint-local'})
    handler._proxy.assert_called_once_with('POST','/sessions/chat-1/profile',{'profile':'flint-local'})
    handler._json.assert_not_called()


@pytest.mark.parametrize('body', [{'title':7}, {'pinned':'true'}, {'pinned':1}])
def test_invalid_session_metadata_never_reaches_bridge(handler,body):
    dispatch(handler,'PATCH','/api/sessions/chat-1',body)
    assert handler._json.call_args.args[0] == 400
    handler._proxy.assert_not_called()


def test_valid_session_metadata_is_forwarded_once(handler):
    body={'title':'Fixture','pinned':True}
    dispatch(handler,'PATCH','/api/sessions/chat-1',body)
    handler._proxy.assert_called_once_with('PATCH','/sessions/chat-1',body)


def test_session_events_choose_journal_only_when_limit_present(handler):
    dispatch(handler,'GET','/api/sessions/chat-1/events?limit=20&after=3')
    handler._session_journal.assert_called_once_with('chat-1','limit=20&after=3')
    handler._events.assert_not_called()
    handler._session_journal.reset_mock()
    dispatch(handler,'GET','/api/sessions/chat-1/events?after=3')
    handler._events.assert_called_once_with('chat-1','after=3')
    handler._session_journal.assert_not_called()


@pytest.mark.parametrize('query,expected', [('limit=999&offset=-5','limit=200&offset=0'),('limit=0&offset=999999999','limit=1&offset=1000000'),('limit=invalid','limit=50&offset=0')])
def test_checkpoint_pagination_is_bounded(handler,query,expected):
    dispatch(handler,'GET','/api/sessions/chat-1/checkpoints?'+query)
    handler._proxy.assert_called_once_with('GET','/sessions/chat-1/checkpoints?'+expected)


def test_review_query_forwards_only_expected_fields(handler):
    dispatch(handler,'GET','/api/sessions/chat-1/changes/review?mode=branch&path=dir%2Fa.py&extra=discard')
    method,url=handler._proxy.call_args.args
    assert method == 'GET'
    assert urlsplit(url).path == '/sessions/chat-1/changes/review'
    assert parse_qs(urlsplit(url).query,keep_blank_values=True) == {'mode':['branch'],'base':[''],'path':['dir/a.py'],'revision':[''],'include_untracked':['0']}


@pytest.mark.parametrize('identifier', ['bad!', 'ABCDEF'*5+'AB', 'abc', 'g'*32])
def test_invalid_upload_identifier_is_rejected(handler,identifier):
    dispatch(handler,'GET','/api/sessions/chat-1/uploads/'+identifier)
    assert handler._json.call_args.args[0] == 400
    handler._download.assert_not_called()


def test_valid_upload_opens_preview_in_correct_session(handler):
    identifier='a'*32
    dispatch(handler,'GET','/api/sessions/chat-1/uploads/'+identifier)
    handler._download.assert_called_once_with('chat-1',identifier,preview=True,resource='uploads')


def test_invalid_checkpoint_revision_cannot_request_diff(handler):
    dispatch(handler,'GET','/api/sessions/chat-1/checkpoints/bad!/diff')
    assert handler._json.call_args.args[0] == 400
    handler._proxy.assert_not_called()


def test_denied_origin_prevents_all_route_processing(handler):
    handler._check_origin.return_value=False
    dispatch(handler,'POST','/api/sessions/chat-1/profile',{'profile':'valid'})
    handler._proxy.assert_not_called()
    handler._body.assert_not_called()
