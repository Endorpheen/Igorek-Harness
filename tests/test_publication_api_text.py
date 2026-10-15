"""Generated client commands cannot read host paths or carry ambient secrets."""
import json
import shlex

from factory.api_server_core.api_payload_formatters import normalize_bash_payload_text
from factory.api_server_core.tavily import build_tavily_search_command


def test_result_text_does_not_dereference_a_host_file(tmp_path):
    private = tmp_path / 'private.txt'
    private.write_text('private fixture must not leave the host')
    text = f'Output too large\nFull output saved to: {private}\nPreview (first 2KB):\npublic fixture'
    assert normalize_bash_payload_text(text) == 'public fixture'


def test_generated_command_does_not_export_an_ambient_tavily_key(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'fixture-ambient-secret')
    assert build_tavily_search_command('Search for a fixture') is None


def test_explicit_user_token_and_shell_special_query_remain_one_json_argument():
    query = "look up O'Reilly; $(touch /tmp/fixture); `id` end"
    command = build_tavily_search_command('Authorization: Bearer fixture-token ' + query)
    argv = shlex.split(command)
    assert argv[0] == 'curl'
    payload = json.loads(argv[argv.index('-d') + 1])
    assert payload['query'] == query
    assert 'Authorization: Bearer fixture-token' in argv
