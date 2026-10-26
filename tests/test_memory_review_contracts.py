"""Post-turn memory review with temporary facts and fake provider responses."""
import io
import json
from unittest.mock import Mock
import urllib.error

import pytest

from factory.core import memory_review as review
from factory.core import memory_policy as policy
from factory.core.agent_foundation import ensure_foundation_files, get_foundation_paths


@pytest.fixture
def memory(tmp_path,monkeypatch):
    root=tmp_path/'memory-data';ensure_foundation_files(str(root))
    monkeypatch.setenv('IGOREK_MEMORY_REVIEW','on')
    monkeypatch.setenv('IGOREK_MEMORY_POLICY','proactive')
    monkeypatch.setattr(review,'get_profile_config',lambda name:{'backend':'cc-backend','model':'fixture','api_format':'openai','base_url':'https://example.com/v1'})
    return root


@pytest.mark.parametrize('action',['forget','replace'])
def test_review_requires_exact_user_citation_before_mutating_saved_fact(memory,monkeypatch,action):
    paths=get_foundation_paths(str(memory));paths.memory_index.write_text('## Saved facts\n- Fixture preference is green\n\n## Other\n- Untouched\n')
    original=paths.memory_index.read_bytes()
    item=review.MemoryReviewItem(action,'long','Fixture preference is blue',target='Fixture preference is green')
    monkeypatch.setattr(review,'_review_with_profile',lambda **kwargs:[item])
    result=review.review_and_persist_memory(user_text='Update a different preference',assistant_text='Fixture response',profile='fixture',data_dir=str(memory))
    assert result[0]['reason']=='target_not_explicitly_cited' and paths.memory_index.read_bytes()==original


@pytest.mark.parametrize('action',['forget','replace'])
def test_review_edits_only_the_cited_saved_fact_and_retains_other_sections(memory,monkeypatch,action):
    paths=get_foundation_paths(str(memory));paths.memory_index.write_text('Header\n## Saved facts\n- Fixture preference is green\n- Keep another fact\n\n## Other\n- Untouched\n')
    item=review.MemoryReviewItem(action,'long','Fixture preference is blue',target='Fixture preference is green',kind='user_preference',reason='explicit correction')
    monkeypatch.setattr(review,'_review_with_profile',lambda **kwargs:[item])
    result=review.review_and_persist_memory(user_text='Please correct: Fixture preference is green',assistant_text='Fixture response',profile='fixture',data_dir=str(memory))
    assert result[0]['changed'] and result[0]['review_action']==action
    updated=paths.memory_index.read_text()
    assert 'green' not in updated and '- Keep another fact' in updated and '## Other\n- Untouched\n' in updated
    assert ('Fixture preference is blue' in updated)==(action=='replace')


def test_review_rejects_replacement_without_a_new_fact(memory,monkeypatch):
    item=review.MemoryReviewItem('replace','long','',target='Fixture preference')
    monkeypatch.setattr(review,'_review_with_profile',lambda **kwargs:[item])
    result=review.review_and_persist_memory(user_text='Replace Fixture preference',assistant_text='',profile='fixture',data_dir=str(memory))
    assert result[0]['reason']=='replacement_missing' and not result[0]['changed']


def test_review_deduplicates_proposals_and_keeps_different_scopes(memory,monkeypatch):
    fact='Fixture project uses deterministic checks'
    items=[review.MemoryReviewItem('save','long',fact),review.MemoryReviewItem('save','long','  '+fact.upper()+'  '),review.MemoryReviewItem('save','daily','Fixture daily context is available'),review.MemoryReviewItem('discard','long','ignored')]
    monkeypatch.setattr(review,'_review_with_profile',lambda **kwargs:items)
    result=review.review_and_persist_memory(user_text='Fixture user message',assistant_text='Fixture response',profile='fixture',data_dir=str(memory))
    assert len(result)==2 and all(x['saved'] for x in result)
    paths=get_foundation_paths(str(memory))
    assert paths.memory_index.read_text().count(fact)==1
    assert any('Fixture daily context' in p.read_text() for p in paths.memory_dir.glob('*.md'))


def test_provider_failure_does_not_apply_legacy_block_on_cc_backend(memory,monkeypatch):
    monkeypatch.setattr(review,'_review_with_profile',Mock(side_effect=RuntimeError('fixture provider unavailable')))
    logs=[]
    before=get_foundation_paths(str(memory)).memory_index.read_bytes()
    result=review.review_and_persist_memory(user_text='Fixture',assistant_text='Fixture',profile='fixture',data_dir=str(memory),legacy_raw_text='MEMORY_WRITE: unauthorized fixture',log_fn=logs.append)
    assert result==[] and any('provider unavailable' in x for x in logs)
    assert any('legacy MEMORY_WRITE ignored' in x for x in logs)
    assert get_foundation_paths(str(memory)).memory_index.read_bytes()==before


@pytest.mark.parametrize('text,expected',[
    ('{"items":[]}',[]), ('```json\n{"items":[]}\n```',[]),('prefix {bad}',[]), ('no JSON',[]), ('[]',[]),
    ('{"items":"bad"}',[]), ('{"items":[null,{}, {"action":"save"}]}',[]),
    ('{"items":[{"action":"replace","target":"old"}]}',[]),
    ('{"items":[{"action":"forget","target":"old"}]}',[review.MemoryReviewItem('forget','long','',target='old')]),
    ('{"items":[{"action":"save","fact":" x ","scope":"unknown","kind":" stable ","reason":" fixture "}]}',[review.MemoryReviewItem('save','long','x','stable','fixture')]),
])
def test_model_review_json_accepts_only_complete_mutation_proposals(text,expected):
    assert review._parse_review_items(text)==expected


@pytest.mark.parametrize('response,expected',[
    (None,''), ({},''), ({'content':[None,{'type':'image'},{'type':'text','text':8},{'type':'text','text':' first '},{'type':'text','text':'second'}]},'first \nsecond'),
    ({'choices':[{'message':{'content':' fixture '}}]},'fixture'), ({'choices':[None]},''), ({'choices':[]},''),
])
def test_provider_response_text_extraction_ignores_non_text_shapes(response,expected):
    assert review._extract_text(response)==expected


@pytest.mark.parametrize('provider,env_name,format_,header',[
    ('openrouter','OPENROUTER_API_KEY','openai','Authorization'),
    ('openrouter','CODEX_OPENROUTER_API_KEY','anthropic','x-api-key'),
    ('openrouter','FACTORY_OPENROUTER_API_KEY','openai','Authorization'),
    ('minimax','MINIMAX_API_KEY','anthropic','x-api-key'),
    ('glm','GLM_API_KEY','openai','Authorization'),
    ('minimax','ANTHROPIC_AUTH_TOKEN','anthropic','x-api-key'),
    ('opencode-go','OPENCODE_API_KEY','anthropic','x-api-key'),
])
def test_review_auth_headers_follow_provider_and_transport_without_real_secrets(monkeypatch,provider,env_name,format_,header):
    for key in ['OPENROUTER_API_KEY','CODEX_OPENROUTER_API_KEY','FACTORY_OPENROUTER_API_KEY','MINIMAX_API_KEY','GLM_API_KEY','ANTHROPIC_AUTH_TOKEN','OPENCODE_API_KEY']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv(env_name,'synthetic-fixture')
    headers=review._openai_request_headers({'provider':provider},api_format=format_)
    assert headers[header]==('synthetic-fixture' if header=='x-api-key' else 'Bearer synthetic-fixture')
    assert ('x-opencode-session' in headers)==(provider=='opencode-go')


@pytest.mark.parametrize('headers',[None,{}, {'Authorization':'Bearer fixture'}])
def test_actual_review_http_request_preserves_utf8_and_explicit_headers(monkeypatch,headers):
    captured=Mock(return_value=io.BytesIO(b'{"items":[]}'))
    monkeypatch.setattr(review.urllib.request,'urlopen',captured)
    assert review._http_post_json('https://example.com/fixture',{'text':'Привет 🐙'},timeout_s=7,headers=headers)=={'items':[]}
    request=captured.call_args.args[0]
    assert json.loads(request.data)=={'text':'Привет 🐙'} and captured.call_args.kwargs=={'timeout':7}
    assert request.get_header('X-api-key')==('dummy' if headers is None else None)


@pytest.mark.parametrize('failure',[
    urllib.error.HTTPError('https://example.com',429,'quota',{},io.BytesIO(b'fixture limit')),
    urllib.error.URLError('fixture offline'),
])
def test_review_network_failure_is_reported_and_does_not_create_model_success(monkeypatch,failure):
    monkeypatch.setattr(review.urllib.request,'urlopen',Mock(side_effect=failure))
    with pytest.raises(RuntimeError,match='429|offline'):
        review._http_post_json('https://example.com/fixture',{},timeout_s=1)


@pytest.mark.parametrize('scope,policy_name,explicit,reason',[
    ('long','off',False,'policy_off'), ('daily','off',True,'policy_off'),
    ('long','explicit_only',False,'policy_explicit_only'),
])
def test_disabled_memory_policy_leaves_all_files_unchanged(memory,scope,policy_name,explicit,reason):
    before={p:p.read_bytes() for p in memory.rglob('*') if p.is_file()}
    result=policy.persist_memory_write('Fixture project has repeatable checks',data_dir=str(memory),scope=scope,policy=policy_name,explicit=explicit)
    assert result['reason']==reason and not result['saved']
    assert all(p.read_bytes()==value for p,value in before.items())
