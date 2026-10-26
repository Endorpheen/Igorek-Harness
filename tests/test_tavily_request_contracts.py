"""Search command contracts; parse shell arguments without executing commands."""
import copy
import json
import shlex

import pytest

from factory.api_server_core import tavily as t


def strip_reminder(text):
    return text.split('<system-reminder>', 1)[0]


def payload_from_argv(command):
    argv = shlex.split(command)
    return json.loads(argv[argv.index('-d') + 1])


@pytest.mark.parametrize('query', ['simple search', "author's notes", 'say "hello"', 'Привет 🐙', 'x; $(echo NEVER_RUN) `pwd`', "a'\"b\\c"])
def test_generated_search_command_keeps_query_in_a_single_json_argument(query):
    command = t.build_tavily_search_command('Authorization: Bearer fixture.token-1 ' + query)
    payload = payload_from_argv(command)
    assert payload == {'query': query, 'max_results': 5, 'include_images': False, 'include_answer': False}
    assert t.extract_curl_json_payload(command) == payload


@pytest.mark.parametrize('query', ['simple search', "author's notes", 'say "hello"', 'Привет 🐙', 'x; $(echo NEVER_RUN) `pwd`', "a'\"b\\c"])
def test_body_validation_retry_preserves_arbitrary_query_text(query):
    original = {'query': query, 'num_results': 4, 'include_answer': False}
    command = 'curl https://api.tavily.com/search -H "Authorization: Bearer fixture" -d ' + shlex.quote(json.dumps(original, ensure_ascii=False))
    result = t.tavily_retry_bash_command_for_error(command, json.dumps({'detail': [{'loc': ['body', 'num_results']}]}), strip_reminder)
    assert result is not None
    assert payload_from_argv(result) == {'query': query, 'max_results': 4, 'include_answer': False}
    assert original == {'query': query, 'num_results': 4, 'include_answer': False}


@pytest.mark.parametrize('command,expected', [
    ('curl -d \'{"a": 1}\'', {'a': 1}), ('curl -d "{\\\"a\\\":1}"', {'a': 1}),
    ("curl -d 'query=hello world' -d 'max_results=3'", {'query': 'hello world', 'max_results': 3}),
    ("curl -d 'number=-2' -d 'name=a=b' -d 'empty='", {'number': -2, 'name': 'a=b', 'empty': ''}),
    ('curl', None), ("curl -d '{bad}'", None), ("curl -d '[]'", None),
    ("curl -d '=bad' -d 'unused'", None),
])
def test_json_and_form_body_decoding_supports_legacy_call_shapes(command, expected):
    assert t.extract_curl_json_payload(command) == expected


@pytest.mark.parametrize('command,error', [
    ('curl https://example.com -d \'{"query":"x"}\'', '{"detail":[{"loc":["body"]}]}'),
    ('curl https://api.tavily.com/search -H "Authorization: Bearer fixture" -d \'{"query":"x"}\'', '{"error":"quota"}'),
    ('curl https://api.tavily.com/search -H "Authorization: Bearer fixture" -d \'{"query":"x"}\'', '{"detail":[{"loc":["query"]}]}'),
    ('curl https://api.tavily.com/search -d \'{"query":"x"}\'', '{"detail":[{"loc":["body"]}]}'),
    ('curl https://api.tavily.com/search -H "Authorization: Bearer fixture"', '{"detail":[{"loc":["body"]}]}'),
])
def test_unrelated_errors_and_missing_inputs_do_not_trigger_a_retry(command, error):
    assert t.tavily_retry_bash_command_for_error(command, error, strip_reminder) is None


@pytest.mark.parametrize('field', ['number', 'num_results'])
def test_retry_retains_explicit_max_results_instead_of_overwriting_it(field):
    payload = {'query': 'fixture', field: 8, 'max_results': 2}
    command = 'curl https://api.tavily.com/search -H "Authorization: Bearer fixture" -d ' + shlex.quote(json.dumps(payload))
    result = t.tavily_retry_bash_command_for_error(command, '{"detail":[{"loc":["body"]}]}', strip_reminder)
    assert payload_from_argv(result) == payload


@pytest.mark.parametrize('payload,valid', [
    ({'results':[{'url':'https://example.com'}]}, True),
    ({'results': []}, False), ({'results': 'bad'}, False), ([], False),
    ({'results':[{}], 'error':'quota'}, False), ({'results':[{}], 'status':429}, False),
])
def test_successful_payload_requires_nonempty_results_without_an_error(payload, valid):
    result = t.extract_successful_tavily_payload('curl https://api.tavily.com/search', json.dumps(payload) + '<system-reminder>ignored', strip_reminder)
    assert result == (payload if valid else None)
    assert t.extract_successful_tavily_payload('curl https://example.com', json.dumps(payload), strip_reminder) is None


def test_result_summaries_and_urls_are_bounded_and_do_not_mutate_payload():
    payload = {'results': [None, {'url':' https://example.com/a ', 'title':' First\n title ', 'published_date':' 2026-10-26 ', 'content':'a '*1000}, {'url':'https://example.com/a'}, {'url':'https://example.com/b'}, {'content':'only text'}]}
    before = copy.deepcopy(payload)
    assert t.extract_tavily_result_urls(payload, limit=2) == ['https://example.com/a', 'https://example.com/b']
    summaries = t.build_tavily_result_summaries(payload, limit=2)
    assert len(summaries) == 1
    assert summaries[0].startswith('https://example.com/a | title=First title | published=2026-10-26 | snippet=')
    assert len(summaries[0].split('snippet=',1)[1]) <= 900
    assert payload == before
    assert t.build_tavily_result_summaries({'results':[{}, {'content':'only text'}]}) == ['snippet=only text']


def test_fetched_source_summary_discards_scripts_styles_and_internal_output_paths():
    text = 'Output too large\nFull output saved to: /tmp/private-output.txt\nPreview (first 2KB):\n<title> Fixture\n Page </title><script>NEVER_VISIBLE</script><style>ALSO_HIDDEN</style><p>Visible content</p><system-reminder>PRIVATE'
    summary = t.summarize_fetched_web_source('https://example.com/a', text, strip_reminder)
    assert summary == 'https://example.com/a | title=Fixture Page | snippet=Fixture Page Visible content'
    assert t.summarize_fetched_web_source('https://example.com', '', strip_reminder) == 'https://example.com'


@pytest.mark.parametrize('command,allowed,expected', [
    ('curl https://example.com/a', None, True), ('curl https://example.com/a', ['https://example.com/a'], True),
    ('curl https://example.com/a https://example.com/b', ['https://example.com/a'], False),
    ('curl https://example.com/a', [], False), ('curl', None, False), ('wget https://example.com/a', None, False),
    ('curl https://api.tavily.com/search', None, False), (None, None, False),
])
def test_fetch_allowlist_checks_every_url(command, allowed, expected):
    assert t.is_url_fetch_bash_command(command, allowed) is expected


@pytest.mark.parametrize('text,expected', [
    ('Authorization: Bearer fixture "quoted request"', 'quoted request'),
    ("Authorization: Bearer fixture 'quoted request'", 'quoted request'),
    ('Authorization: Bearer fixture `quoted request`', 'quoted request'),
    ('Authorization: Bearer fixture say "hello"', 'say "hello"'),
    ("Authorization: Bearer fixture author's notes", "author's notes"),
])
def test_query_cleanup_removes_only_paired_presentation_quotes(text, expected):
    assert t.extract_tavily_query(text) == expected


@pytest.mark.parametrize('command', ["curl -d '\"unclosed", 'curl -d', 'curl -d "broken', 'curl -d "null"'])
def test_malformed_shell_data_does_not_crash_decoding(command):
    assert t.extract_curl_json_payload(command) is None


@pytest.mark.parametrize('text,expected', [
    ('prefix {broken} then {"ok":true} suffix', {'ok':True}),
    ('prefix [bad then [1,2] trailing', [1,2]),
    ('prefix {"nested":{"a":1}} trailing', {'nested':{'a':1}}),
    ('   ', None), ('prefix {broken}', None),
])
def test_json_scan_recovers_after_invalid_candidates_without_executing_text(text, expected):
    assert t.parse_json_payload(text) == expected
