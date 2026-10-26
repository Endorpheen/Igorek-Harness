"""Context compaction invariants with a synthetic summarizer, no model calls."""
import asyncio
import copy
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from inference_api_support import inference_app as inference_app


@pytest.fixture
def setup(inference_app):
    module=importlib.import_module('factory.api_server_core.context_compactor')
    client=SimpleNamespace(generate_summary=AsyncMock(return_value='Fixture durable summary'))
    def make(**changes):
        config=module.ContextCompactorConfig(**({'protect_first_n':1,'protect_last_n':2,'max_summary_items':3}|changes))
        return module.ContextCompressor(model='fixture',tokenizer=inference_app.generator.tokenizer,context_length=4096,summary_client=client,config=config)
    return module,client,make


def messages():
    result=[{'role':'system','content':'Keep system constraints'}]
    for index in range(12):result += [{'role':'user','content':f'question {index} '+'x'*200},{'role':'assistant','content':f'answer {index} '+'y'*200}]
    return result


def test_compress_keeps_system_and_recent_turns_without_mutating_source(setup):
    module,client,make=setup;compressor=make();source=messages();before=copy.deepcopy(source)
    compacted,metadata=asyncio.run(compressor.compress(source,current_tokens=5000))
    assert source==before and compacted[0]==source[0] and compacted[-2:]==source[-2:]
    summaries=[x for x in compacted if str(x.get('content','')).startswith(module.SUMMARY_PREFIX)]
    assert len(summaries)==1 and 'Fixture durable summary' in summaries[0]['content']
    assert metadata['applied'] and metadata['summary_transport']=='client' and metadata['compression_count']==1
    assert client.generate_summary.await_count==1


@pytest.mark.parametrize('case',['disabled','short','below-threshold'])
def test_noop_compaction_does_not_consume_summarizer_tokens(setup,case):
    _,client,make=setup;compressor=make(enabled=case!='disabled')
    source=messages() if case!='short' else messages()[:3]
    tokens=1 if case=='below-threshold' else 5000
    result,metadata=asyncio.run(compressor.compress(source,current_tokens=tokens))
    assert result is source and metadata is None and client.generate_summary.await_count==0


@pytest.mark.parametrize('failure',[RuntimeError('fixture summarizer unavailable'),''])
def test_summary_failure_uses_deterministic_fallback_and_cooldown(setup,failure):
    module,client,make=setup;compressor=make()
    if isinstance(failure,Exception):client.generate_summary.side_effect=failure
    else:client.generate_summary.return_value=failure
    source=messages()
    first,metadata=asyncio.run(compressor.compress(source,current_tokens=5000))
    assert metadata['summary_transport']=='fallback' and any('Progress Snapshot' in str(x['content']) for x in first)
    assert client.generate_summary.await_count==1
    asyncio.run(compressor.compress(source,current_tokens=5000))
    assert client.generate_summary.await_count==1 and compressor._summary_failure_cooldown_until>0
    assert any('and ' in str(x['content']) for x in first)


@pytest.mark.parametrize('prefix_kind',['current','legacy'])
def test_existing_summary_is_reused_once_instead_of_accumulating_wrappers(setup,prefix_kind):
    module,client,make=setup;compressor=make();source=messages()
    prefix=module.SUMMARY_PREFIX if prefix_kind=='current' else module.LEGACY_SUMMARY_PREFIX
    source.insert(1,{'role':'assistant','content':prefix+'\nOLD_DURABLE_SUMMARY'})
    client.generate_summary.return_value=module.SUMMARY_PREFIX+'\nNEW_DURABLE_SUMMARY'
    compacted,metadata=asyncio.run(compressor.compress(source,current_tokens=5000))
    assert metadata['previous_summary_reused']
    assert 'OLD_DURABLE_SUMMARY' in client.generate_summary.call_args.args[0]
    assert sum(str(x.get('content','')).count(module.SUMMARY_PREFIX) for x in compacted)==1
    assert any('NEW_DURABLE_SUMMARY' in x.get('content','') for x in compacted)


def test_pruning_changes_old_tool_bodies_but_preserves_recent_tool_evidence(setup):
    module,_,make=setup;compressor=make(prune_tool_result_chars=20)
    source=[{'role':'tool','content':'old '*100}]+messages()+[{'role':'tool','content':'recent '*100}]
    before=copy.deepcopy(source)
    pruned,count=compressor._prune_old_tool_results(source)
    assert source==before and count==1
    assert pruned[0]['content']==module.PRUNED_TOOL_PLACEHOLDER and pruned[-1]==source[-1]


def test_sanitization_removes_orphan_result_and_adds_missing_result_without_reordering_calls(setup):
    _,_,make=setup;compressor=make()
    source=[{'role':'tool','tool_call_id':'orphan','content':'unused'},{'role':'assistant','content':'','tool_calls':[{'id':'one','function':{'name':'Read'}},{'id':'two','function':{'name':'Read'}}]},{'role':'tool','tool_call_id':'two','content':'kept'}]
    before=copy.deepcopy(source);result=compressor._sanitize_tool_pairs(source)
    assert source==before and all(x.get('tool_call_id')!='orphan' for x in result)
    assert [x.get('tool_call_id') for x in result if x['role']=='tool']==['one','two']
    assert result[-1]['content']=='kept' and 'earlier conversation' in result[1]['content']


def test_summary_serialization_bounds_large_content_and_preserves_tool_name_and_id(setup):
    _,_,make=setup;compressor=make()
    data=[{'role':'assistant','content':'A'*4000,'tool_calls':[{'id':'one','function':{'name':'Read','arguments':'x'*600}}]},{'role':'tool','tool_call_id':'one','content':'result'}]
    result=compressor._serialize_for_summary(data)
    assert '[ASSISTANT]' in result and 'Read(' in result and '[TOOL RESULT one]: result' in result
    assert 'truncated' in result and len(result)<4000


@pytest.mark.parametrize('raw,expected',[
    ('text','text'),([{'type':'text','text':'one'},{'type':'text','text':'two'}],'one\ntwo'),
    ({'a':1},'{"a": 1}'),(None,''),
])
def test_content_normalization_preserves_supported_message_shapes(setup,raw,expected):
    module,_,_=setup
    assert module._stringify_content(raw)==expected


def test_usage_and_threshold_status_are_based_on_actual_prompt_usage(setup):
    _,_,make=setup;compressor=make()
    compressor.update_from_response({'prompt_tokens':2500,'completion_tokens':20,'total_tokens':2520})
    assert compressor.should_compress() and compressor.should_compress_preflight(messages())
    status=compressor.get_status()
    assert status['last_prompt_tokens']==2500 and status['context_length']==4096
    assert compressor.last_completion_tokens==20 and compressor.last_total_tokens==2520
