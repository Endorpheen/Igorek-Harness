"""Session archive and history recovery use only owned temporary JSONL files."""
import json
from types import SimpleNamespace

import pytest

from factory.core import cli_chat as c


@pytest.mark.parametrize('name',[None,'fixture archive','../../outside','session'])
def test_archive_preserves_exact_bytes_and_resets_only_source_thread(tmp_path,name):
    source=tmp_path/'session.jsonl';content='{"role":"user","text":"Привет 🐙"}\nnot-json\n'
    source.write_text(content,encoding='utf-8');other=tmp_path/'other.jsonl';other.write_text('keep')
    result=c.archive_session_thread(source,archive_name=name)
    assert result is not None and result.parent==tmp_path and result!=source
    assert result.read_text(encoding='utf-8')==content and source.read_text()==''
    assert other.read_text()=='keep'
    assert len(list(tmp_path.iterdir()))==3


def test_archive_collision_never_overwrites_existing_thread(tmp_path,monkeypatch):
    source=tmp_path/'source.jsonl';source.write_text('new content')
    (tmp_path/'fixture.jsonl').write_text('first existing')
    (tmp_path/'fixture-bbbbbb.jsonl').write_text('second existing')
    ids=iter(['aaaaaa','bbbbbb','cccccc'])
    monkeypatch.setattr(c,'uuid',SimpleNamespace(uuid4=lambda:SimpleNamespace(hex=next(ids))))
    result=c.archive_session_thread(source,archive_name='fixture')
    assert result==tmp_path/'fixture-cccccc.jsonl' and result.read_text()=='new content'
    assert (tmp_path/'fixture.jsonl').read_text()=='first existing'
    assert (tmp_path/'fixture-bbbbbb.jsonl').read_text()=='second existing'


@pytest.mark.parametrize('content',[None,'',' \n\t'])
def test_absent_or_empty_session_does_not_create_archive(tmp_path,content):
    source=tmp_path/'session.jsonl'
    if content is not None:source.write_text(content)
    assert c.archive_session_thread(source) is None
    assert list(tmp_path.iterdir())==([] if content is None else [source])
    if content is not None:assert source.read_text()==''


@pytest.mark.parametrize('kind',['absent','directory','malformed'])
def test_history_summary_tolerates_missing_unreadable_or_malformed_records(tmp_path,kind):
    path=tmp_path/'history'
    if kind=='directory':path.mkdir()
    elif kind=='malformed':path.write_text('\nnot-json\n[]\nnull\n{"role":"system"}\n')
    assert c._first_user_message(path)==''
    assert c._session_entry_count(path)==0
    assert c._last_entry_ts(path)==0


def test_history_summary_skips_bad_lines_without_losing_later_user_message(tmp_path):
    path=tmp_path/'history.jsonl'
    rows=[{'type':'meta'}, {'role':'assistant','text':'answer','ts':5},{'role':'user','text':'first question','ts':8},{'role':'user','text':'next question','ts':10}]
    path.write_text('\nnot-json\n[]\n'+'\n'.join(json.dumps(x) for x in rows)+'\n')
    assert c._first_user_message(path)=='first question'
    assert c._session_entry_count(path)==3
    assert c._last_entry_ts(path)==10
