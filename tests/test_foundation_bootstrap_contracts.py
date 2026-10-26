"""First-run memory bootstrap and state recovery in an owned data root."""

import pytest

from factory.core import agent_foundation as foundation
from factory.core.builtin_memory_provider import BuiltinMemoryProvider


@pytest.mark.parametrize('raw',['[]','null','42','"string"','true','not JSON'])
def test_nonobject_or_corrupt_state_does_not_crash_bootstrap_startup(tmp_path,raw):
    foundation.ensure_foundation_files(str(tmp_path))
    state=foundation.get_foundation_paths(str(tmp_path)).state
    state.write_text(raw)
    assert isinstance(foundation.load_bootstrap_state(str(tmp_path)),dict)
    assert foundation.bootstrap_active(str(tmp_path))
    assert foundation.bootstrap_intro_message(str(tmp_path))
    assert state.read_text()==raw


def test_five_answers_create_foundation_and_become_context_for_future_sessions(tmp_path):
    root=str(tmp_path)
    assert foundation.bootstrap_active(root) and foundation.foundation_prompt_context(root)==''
    answers=['Fixture User','Fixture Agent','Review fixture code','Concise and practical','Keep all actions local']
    for index,answer in enumerate(answers):
        result=foundation.process_bootstrap_answer(answer,root)
        assert result['done']==(index==4) and result['message']
    paths=foundation.get_foundation_paths(root)
    assert not paths.bootstrap.exists()
    assert foundation.bootstrap_complete(root)
    context=BuiltinMemoryProvider().foundation_context(data_dir=root)
    for answer in answers:assert answer in context
    assert all(label in context for label in ['[IDENTITY]','[USER]','[SOUL]','[MEMORY]','[DAILY_MEMORY]'])
    state=foundation.load_bootstrap_state(root)
    assert state['step']=='done' and state['answers']['user_name']=='Fixture User'
    assert all(value for value in [state['started_at'],state['completed_at']])


def test_interrupted_bootstrap_resumes_at_the_next_question_without_losing_answers(tmp_path):
    root=str(tmp_path);foundation.process_bootstrap_answer('Fixture User',root)
    state=foundation.load_bootstrap_state(root)
    assert state['step']=='agent_name' and state['answers']=={'user_name':'Fixture User'}
    assert foundation.bootstrap_intro_message(root)==foundation.bootstrap_next_prompt(state)
    foundation.process_bootstrap_answer('Fixture Agent',root)
    assert foundation.load_bootstrap_state(root)['step']=='mission'


def test_ensure_preserves_existing_user_text_and_force_is_explicit(tmp_path):
    paths=foundation.get_foundation_paths(str(tmp_path));foundation.ensure_foundation_files(str(tmp_path))
    paths.user.write_text('Fixture user text')
    foundation.ensure_foundation_files(str(tmp_path))
    assert paths.user.read_text()=='Fixture user text'
    foundation.ensure_foundation_files(str(tmp_path),force=True)
    assert 'Fixture user text' not in paths.user.read_text()


@pytest.mark.parametrize('scope',['long','daily'])
def test_fact_write_is_idempotent_and_long_facts_are_separate_from_daily_context(tmp_path,scope):
    root=str(tmp_path)
    first=foundation.write_memory_fact(' Fixture preference stays local ',root,scope=scope)
    second=foundation.write_memory_fact('Fixture preference stays local',root,scope=scope)
    assert first['saved'] and first['daily_saved'] and first['long_saved']==(scope=='long')
    assert not second['saved']
    paths=foundation.get_foundation_paths(root)
    assert ('Fixture preference stays local' in paths.memory_index.read_text())==(scope=='long')
    assert foundation.daily_memory_path(root).read_text().count('Fixture preference stays local')==1


def test_empty_fact_does_not_create_a_saved_facts_bullet(tmp_path):
    result=foundation.write_memory_fact('   ',str(tmp_path))
    assert not result['saved'] and result['paths']==[]
    assert '## Saved facts' not in foundation.get_foundation_paths(str(tmp_path)).memory_index.read_text()


@pytest.mark.parametrize('text,expected',[
    ('/remember Fixture preference',('long','Fixture preference')),
    ('ordinary request',None),('/remember Fixture действует сегодня',('daily','Fixture действует сегодня')),
])
def test_explicit_memory_request_keeps_ordinary_questions_out_of_memory(text,expected):
    assert foundation.extract_explicit_memory_write_request(text)==expected


def test_hidden_memory_blocks_are_extracted_without_leaking_into_visible_answer():
    text='Visible before\n[[MEMORY_WRITE]]\nscope: daily\nfact: Fixture today\n[[/MEMORY_WRITE]]\nVisible after'
    visible,items=foundation.extract_memory_write_blocks(text)
    assert 'MEMORY_WRITE' not in visible and 'Visible before' in visible and 'Visible after' in visible
    assert items==[{'scope':'daily','fact':'Fixture today'}]
