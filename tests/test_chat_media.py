import http.client
import json
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.core.cli_chat import _session_path, _append_entry
from factory.core.mobile_bridge.files import download_range
from factory.core.mobile_bridge.server import BridgeConfig, MobileBridge, build_server
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database


@pytest.fixture
def media(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    initialize_database(get_db_path())
    bridge = MobileBridge(config=BridgeConfig(profile='demo', workdir=str(tmp_path)))
    session = bridge.service.create_session(profile='demo', cwd=str(tmp_path))['session_id']
    audio = tmp_path / 'song.wav'; audio.write_bytes(bytes(range(256)) * 400)
    video = tmp_path / 'clip.mp4'; video.write_bytes(b'video fixture')
    return bridge, session, audio, video


@pytest.mark.parametrize('header,expected', [(None, (0, 99)), ('bytes=0-9', (0, 9)), ('bytes=80-', (80, 99)), ('bytes=-7', (93, 99)), ('bytes=-700', (0, 99)), ('bytes=90-200', (90, 99))])
def test_range_supports_seek_and_suffix(header, expected):
    assert download_range(header, 100) == expected


@pytest.mark.parametrize('header', ['bytes=100-', 'bytes=10-5', 'bytes=-0', 'bytes=-', 'bytes=0-1,4-5', 'items=1-2', 'bytes=' + '1' * 129 + '-'])
def test_invalid_or_unsatisfiable_range_is_rejected(header):
    with pytest.raises(ValueError): download_range(header, 100)
    with pytest.raises(ValueError): download_range('bytes=0-', 0)


def test_old_final_reply_projects_only_existing_authorized_files(media, tmp_path):
    bridge, session, audio, video = media
    private = tmp_path / '.private.wav'; private.write_bytes(b'hidden')
    outside = tmp_path.parent / ('outside-' + tmp_path.name + '.wav'); outside.write_bytes(b'outside')
    try:
        _append_entry(_session_path(session), role='assistant', text=f'Готово: `{audio}` и `{video}`\n{private}\n{outside}', task_id='media-task')
        message = bridge.service.get_session(session)['messages'][-1]
        assert {file['name'] for file in message['files']} == {'song.wav', 'clip.mp4'}
        assert {file['id'] for file in message['files']} <= {file['id'] for file in bridge.service.list_session_files(session)}
        info, handle = bridge.service.open_session_file(session, message['files'][0]['id'])
        with handle: assert handle.read() == audio.read_bytes()
    finally:
        outside.unlink()


def test_finalized_task_persists_attachments_and_emits_one_task_bound_event(media, tmp_path):
    bridge, session, audio, video = media
    task = {'task_id': 'final-media-task', 'profile': 'demo', 'status': 'ok', 'output_text': f'Готово: `{audio}`',
        'metadata': {'source': 'mobile_bridge', 'session': session, 'cwd': str(tmp_path)}}
    with patch.object(bridge.service, '_scan_tasks', return_value=[task]), patch.object(bridge.service, '_merge_task_metadata') as merge:
        assert bridge.service.finalize_turns() == 1
        merge.assert_called_once_with(task['task_id'], {'session_finalized': True})
    message = bridge.service.get_session(session)['messages'][-1]
    assert message['files'][0]['name'] == 'song.wav'
    events = bridge.store.list_events(session_id=session)
    event = next(row for row in events if row['kind'] == 'message.files')
    assert event['task_id'] == task['task_id'] and event['payload']['files'] == message['files']


def test_authenticated_range_download_preserves_bytes_and_boundary_headers(media):
    bridge, session, audio, video = media
    _append_entry(_session_path(session), role='assistant', text=str(audio))
    info = bridge.service.list_session_files(session)[0]
    code = bridge.store.create_pairing_code()['code']
    paired = bridge.store.complete_pairing(code=code, name='Media QA')
    server = build_server(bridge, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def request(range_value=None, token=True):
        client = http.client.HTTPConnection('127.0.0.1', server.server_port)
        headers = {'Authorization': 'Bearer ' + paired['token']} if token else {}
        if range_value: headers['Range'] = range_value
        client.request('GET', f'/mobile/v1/sessions/{session}/files/{info["id"]}', headers=headers)
        reply = client.getresponse(); body = reply.read(); result = reply.status, dict(reply.getheaders()), body; client.close(); return result
    try:
        status, headers, body = request('bytes=7-29')
        assert status == 206 and headers['Content-Range'] == f'bytes 7-29/{audio.stat().st_size}'
        assert headers['Accept-Ranges'] == 'bytes' and body == audio.read_bytes()[7:30]
        assert request('bytes=-8')[2] == audio.read_bytes()[-8:]
        assert request('bytes=999999-')[0] == 416
        assert request('bytes=0-1', token=False)[0] == 401
        assert request()[0] == 200 and request()[2] == audio.read_bytes()
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
