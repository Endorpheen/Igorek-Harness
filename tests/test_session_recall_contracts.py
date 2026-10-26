"""Owned SQLite recall searches, reindexing and malformed-record isolation."""
import json
import os
from types import SimpleNamespace

import pytest

from factory.core import session_recall_store as recall
from factory.core.schema import open_db


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setenv('FACTORY_SESSION_EMBEDDINGS_ENABLED','off')
    sessions=tmp_path/'sessions';sessions.mkdir()
    monkeypatch.setattr(recall,'get_chat_sessions_dir',lambda:str(sessions))
    monkeypatch.setattr(recall,'get_archive_index',lambda *args:SimpleNamespace(search=lambda *args,**kwargs:[]))
    value=recall.SessionRecallStore(str(tmp_path/'recall.db'),archive_cache_dir=str(tmp_path/'archive'))
    value.ensure_schema()
    return value,sessions


def write_session(path,text='needle fixture '+'x'*80,ts=12):
    path.write_text(json.dumps({'role':'user','text':text,'ts':ts})+'\n'+json.dumps({'role':'assistant','text':'Completed fixture result '+'y'*80,'ts':ts})+'\n')


def test_lexical_search_indexes_real_session_and_excludes_current_one(store):
    value,sessions=store
    write_session(sessions/'first.jsonl');write_session(sessions/'second.jsonl',ts=13)
    results=value.search_lexical('needle',current_session='first')
    assert [x['session'] for x in results]==['second']
    assert results[0]['source']=='recall_entry' and results[0]['matched_terms']==['needle']
    assert 'paired_turn' in results[0]['tags']
    assert value.search_lexical('')==[]
    assert value.sync_recent_sessions()==1
    assert value.sync_recent_sessions()==0


@pytest.mark.parametrize('tags',['bad JSON','{}','null','8','"string"'])
def test_corrupt_tags_cannot_break_lexical_results_for_other_sessions(store,tags):
    value,sessions=store
    write_session(sessions/'first.jsonl');write_session(sessions/'second.jsonl',ts=13)
    value.sync_recent_sessions()
    with open_db(value.db_path) as connection:
        connection.execute('UPDATE session_recall_entries SET tags_json=? WHERE session_id=?',(tags,'first'));connection.commit()
    results=value.search_lexical('needle')
    assert {x['session'] for x in results}=={'first','second'}
    assert next(x for x in results if x['session']=='first')['tags']==[]


@pytest.mark.parametrize('ts',['invalid',{},[],float('nan'),float('inf')])
def test_malformed_timestamp_does_not_discard_otherwise_useful_conversation(store,ts):
    value,sessions=store
    path=sessions/'fixture.jsonl';write_session(path,ts=ts)
    assert value.sync_session_file(path)==1
    assert value.search_lexical('needle')[0]['session']=='fixture'


def test_malformed_jsonl_and_nonconversation_entries_are_ignored_without_modifying_source(store):
    value,sessions=store
    path=sessions/'fixture.jsonl'
    write_session(path)
    original='\n{bad JSON}\n'+json.dumps({'role':'system','text':'ignore fixture'})+'\n'+path.read_text()
    path.write_text(original)
    assert value.sync_session_file(path)==1
    assert path.read_text()==original
    assert value.sync_session_file(sessions/'missing.jsonl')==0


def test_unavailable_match_operator_uses_lexical_fallback_instead_of_dropping_existing_results(store):
    value,sessions=store;write_session(sessions/'fixture.jsonl')
    value.sync_recent_sessions()
    with open_db(value.db_path) as connection:
        connection.execute('DROP TABLE session_recall_entries_fts')
        connection.execute('CREATE TABLE session_recall_entries_fts(entry_id TEXT, session_id TEXT, text TEXT)');connection.commit()
    assert value.search_lexical('needle')[0]['session']=='fixture'


class Embedder:
    available=True
    model='fixture-vector-model'
    def embed_texts(self,texts):return SimpleNamespace(model=self.model,dimensions=2,vectors=[[1.0,0.0] for _ in texts])
    def embed_text(self,text):return self.model,[1.0,0.0]


def test_embeddings_are_indexed_once_and_stale_vectors_are_removed_when_source_is_emptied(store):
    value,sessions=store;value.embedder=Embedder()
    path=sessions/'fixture.jsonl';write_session(path)
    assert value.sync_session_file(path)==1 and value.sync_missing_embeddings()==0
    result=value.search_semantic('needle')
    assert result[0]['session']=='fixture' and result[0]['semantic_score']==1.0
    assert value.search_semantic('needle',current_session='fixture')==[]
    path.write_text('');os.utime(path,(2000000000,2000000000))
    assert value.sync_session_file(path)==0
    with open_db(value.db_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM session_recall_embeddings').fetchone()[0]==0


@pytest.mark.parametrize('tags',['bad JSON','{}','null'])
def test_semantic_search_handles_corrupt_optional_tags_without_losing_matches(store,tags):
    value,sessions=store;value.embedder=Embedder();write_session(sessions/'fixture.jsonl');value.sync_recent_sessions()
    with open_db(value.db_path) as connection:
        connection.execute('UPDATE session_recall_entries SET tags_json=?',(tags,));connection.commit()
    result=value.search_semantic('needle')
    assert len(result)==1 and result[0]['tags']==[]


@pytest.mark.parametrize('vector',['bad JSON','{}','[]'])
def test_invalid_embedding_record_is_ignored_without_creating_a_false_match(store,vector):
    value,sessions=store;value.embedder=Embedder();write_session(sessions/'fixture.jsonl');value.sync_recent_sessions()
    with open_db(value.db_path) as connection:
        connection.execute('UPDATE session_recall_embeddings SET vector_json=?',(vector,));connection.commit()
    assert value.search_semantic('needle')==[]


@pytest.mark.parametrize('vector',[json.dumps(['bad',0]),json.dumps([None,1]),json.dumps([float('nan'),1]),json.dumps([float('inf'),1])])
def test_non_numeric_or_nonfinite_vector_cannot_break_other_semantic_results(store,vector):
    value,sessions=store;value.embedder=Embedder()
    write_session(sessions/'first.jsonl');write_session(sessions/'second.jsonl',ts=13)
    value.sync_recent_sessions()
    with open_db(value.db_path) as connection:
        connection.execute('UPDATE session_recall_embeddings SET vector_json=? WHERE entry_id IN (SELECT entry_id FROM session_recall_entries WHERE session_id=?)',(vector,'first'));connection.commit()
    result=value.search_semantic('needle')
    assert [row['session'] for row in result]==['second']
