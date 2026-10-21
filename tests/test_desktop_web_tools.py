"""Exercise owner-configured search through Vault, schemas and tool dispatch."""
import io
import json
import os
import sys
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from vault_owner import OwnerVault
from web_tools import DesktopWebTools, _NoRedirect
from factory.core import cc_backend_tools as tools
from factory.core.vault.models import CredentialStatus
from factory.core.web_tools_runtime import active_web_tools, scoped_web_tools

PASSWORD = 'fixture-password'
KEY = 'test-only-search-key'


@pytest.fixture
def setup(tmp_path):
    owner = OwnerVault(tmp_path)
    owner.service.create(PASSWORD)
    profile = owner.providers.save('local-fixture', 'fixture-model', 'http://127.0.0.1:8081/v1', 'openai', '')
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified', 'reason': 'ok'})
    provider = DesktopWebTools(owner.lifecycle, profile['credential_id'], profile['config'])
    yield owner, profile, provider
    owner.close()


def save(owner, key=KEY):
    return owner.lifecycle.save_value('', 'TAVILY_API_KEY', 'secret', key, PASSWORD)


def test_schemas_and_both_real_tools_use_vault_callback_without_export(setup, tmp_path, monkeypatch):
    owner, profile, provider = setup
    monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    assert not provider.available()
    saved = save(owner)
    requests = []
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 60
            assert request.get_header('Authorization') == 'Bearer ' + KEY
            assert KEY not in request.data.decode()
            payload = {'results': [{'title': 'Verified fixture', 'url': 'https://example.com/',
                                    'content': 'Search fixture', 'raw_content': 'Extract fixture'}]}
            return io.BytesIO(json.dumps(payload).encode())
    def build(handler):
        assert isinstance(handler, _NoRedirect)
        return Opener()
    monkeypatch.setattr('web_tools.urllib.request.build_opener', build)
    with scoped_web_tools(provider):
        names = {s['name'] for s in tools.get_tool_schemas()}
        assert {'web_search', 'web_extract'} <= names
        search = tools.execute_tool('web_search', {'query': 'fixture'}, str(tmp_path))
        extract = tools.execute_tool('web_extract', {'urls': ['https://example.com/']}, str(tmp_path))
        assert 'Verified fixture' in search and 'Extract fixture' in extract
        assert KEY not in search + extract + repr(profile) + repr(saved)
        assert 'TAVILY_API_KEY' not in os.environ
    assert [r.full_url for r in requests] == ['https://api.tavily.com/search', 'https://api.tavily.com/extract']
    assert active_web_tools() is None
    assert not tools.web_tools_available()


def test_lock_delete_and_rotation_apply_to_existing_provider_without_env_fallback(setup, monkeypatch):
    owner, _, provider = setup
    saved = save(owner)
    monkeypatch.setenv('TAVILY_API_KEY', 'unrelated-host-key')
    assert tools.web_tools_available()
    with scoped_web_tools(provider):
        assert provider.available()
        owner.service.lock()
        assert not tools.web_tools_available()
        assert 'web_search' not in {s['name'] for s in tools.get_tool_schemas()}
        with pytest.raises(Exception):
            provider.request('search', {'query': 'fixture'})
        owner.service.unlock(PASSWORD)
        assert provider.available()
        owner.lifecycle.save_value(saved['credential_id'], 'TAVILY_API_KEY', 'secret', 'rotated-fixture', PASSWORD)
        assert owner.lifecycle.web_search_runtime_headers(provider.credential_id, provider.config) == {'Authorization': 'Bearer rotated-fixture'}
        owner.lifecycle.delete_value(saved['credential_id'], PASSWORD)
        assert not tools.web_tools_available()
    assert tools.web_tools_available()


@pytest.mark.parametrize('field,value', [('model', 'changed'), ('base_url', 'https://other.invalid/v1'), ('api_format', 'anthropic')])
def test_profile_binding_changes_disable_search(setup, field, value):
    owner, profile, provider = setup
    save(owner)
    assert provider.available()
    changed = {**profile['config'], field: value}
    with pytest.raises(ValueError):
        owner.lifecycle.web_search_runtime_headers(profile['credential_id'], changed)
    owner.providers.update(profile['credential_id'], changed['name'], changed['model'], changed['base_url'], changed['api_format'], 'fixture-provider-key')
    assert not provider.available()


@pytest.mark.parametrize('status', [CredentialStatus.invalid, CredentialStatus.expired])
def test_invalid_or_expired_keys_are_not_advertised(setup, status):
    owner, _, provider = setup
    saved = save(owner)
    owner.lifecycle._require().update_status(saved['credential_id'], status)
    assert not provider.available()


def test_agent_written_secret_does_not_enable_search(setup):
    owner, _, provider = setup
    owner.lifecycle._append_agent_value('TAVILY_API_KEY', 'secret', KEY,
        {'kind': 'agent', 'task_id': 'fixture-task'}, 'fixture-digest')
    assert not provider.available()


def test_owner_env_import_is_supported_but_plain_variables_are_not(setup):
    owner, _, provider = setup
    value = owner.lifecycle.save_value('', 'TAVILY_API_KEY', 'variable', KEY, PASSWORD)
    assert not provider.available()
    owner.lifecycle.delete_value(value['credential_id'], PASSWORD)
    owner.lifecycle.import_owner_values({'TAVILY_API_KEY': ('secret', KEY)}, PASSWORD, 'fixture.env')
    assert provider.available()


@pytest.mark.parametrize('endpoint', ['../search', 'https://other.invalid/', 'search?key=fixture'])
def test_fixed_endpoint_validation_never_sends_secret(setup, monkeypatch, endpoint):
    owner, _, provider = setup
    save(owner)
    monkeypatch.setattr('web_tools.urllib.request.build_opener', lambda *_: pytest.fail('Unexpected network request'))
    with pytest.raises(ValueError):
        provider.request(endpoint, {})


@pytest.mark.parametrize('failure', ['http', 'network', 'json', 'array', 'oversize'])
def test_network_failures_and_response_limits_do_not_expose_credentials(setup, monkeypatch, failure):
    owner, _, provider = setup
    save(owner)
    def open_reply(*args, **kwargs):
        if failure == 'http':
            raise urllib.error.HTTPError('https://api.tavily.com/search', 401, KEY, {}, io.BytesIO(KEY.encode()))
        if failure == 'network':
            raise urllib.error.URLError(KEY)
        raw = {'json': KEY.encode(), 'array': b'[]', 'oversize': b'x' * (4 * 1024 * 1024 + 1)}[failure]
        return io.BytesIO(raw)
    monkeypatch.setattr('web_tools.urllib.request.build_opener', lambda *_: SimpleNamespace(open=open_reply))
    with scoped_web_tools(provider):
        result = json.loads(tools.execute_tool('web_search', {'query': 'fixture'}, str(owner.root)))
    assert result['success'] is False and KEY not in str(result)


def test_redirect_handler_refuses_other_origins():
    assert _NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://other.invalid/') is None


def test_context_is_nested_thread_local_and_restored_on_error(monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    enabled = SimpleNamespace(available=lambda: True)
    disabled = SimpleNamespace(available=lambda: False)
    barrier = threading.Barrier(2)
    def run(provider):
        with scoped_web_tools(provider):
            barrier.wait(timeout=5)
            return tools.web_tools_available()
    with ThreadPoolExecutor(2) as pool:
        first, second = pool.submit(run, enabled), pool.submit(run, disabled)
        assert first.result() is True and second.result() is False
    with scoped_web_tools(enabled):
        with pytest.raises(RuntimeError), scoped_web_tools(disabled):
            assert not tools.web_tools_available()
            raise RuntimeError('fixture')
        assert tools.web_tools_available()
    assert active_web_tools() is None


def test_kvm_does_not_advertise_unimplemented_host_search_bridge(setup):
    owner, profile, _ = setup
    save(owner)
    provider = DesktopWebTools(owner.lifecycle, profile['credential_id'], profile['config'], enabled=False)
    with scoped_web_tools(provider):
        assert not tools.web_tools_available()
        with pytest.raises(RuntimeError, match='isolated'):
            provider.request('search', {})


def test_desktop_packaging_includes_web_adapter():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop/packaging'))
    from bundle_sources import BACKEND_FILES
    assert 'web_tools.py' in BACKEND_FILES


@pytest.mark.parametrize('provider,key_name', [('tavily','TAVILY_API_KEY'),('exa','EXA_API_KEY'),('serper','SERPER_API_KEY')])
def test_owner_setup_checks_then_saves_and_worker_uses_selected_service(setup, monkeypatch, tmp_path, provider, key_name):
    owner, _, runtime = setup
    checked = []
    monkeypatch.setattr('search_setup.search_request', lambda *args: checked.append(args) or {'results': []})
    reply = owner.dispatch({'id':'save-search','action':'save_search','provider':provider,'api_key':KEY,'password':PASSWORD})
    assert not reply.get('error') and reply['result']['search']['provider'] == provider
    assert len(checked) == 1 and KEY not in str(reply)
    captured = []
    def open_reply(request, timeout):
        captured.append(request)
        response = {'organic':[{'title':'Fixture','link':'https://example.com/','snippet':'Found'}]} if provider=='serper' else {'results':[{'title':'Fixture','url':'https://example.com/','highlights':['Found']}]}
        return io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr('web_tools.urllib.request.build_opener', lambda *_: SimpleNamespace(open=open_reply))
    with scoped_web_tools(runtime):
        names = {schema['name'] for schema in tools.get_tool_schemas()}
        assert 'web_search' in names
        assert ('web_extract' in names) == (provider != 'serper')
        result = tools.execute_tool('web_search', {'query':'fixture','limit':3}, str(tmp_path))
        assert 'Fixture' in result and KEY not in result
    request = captured[0]
    assert request.full_url == {'tavily':'https://api.tavily.com/search','exa':'https://api.exa.ai/search','serper':'https://google.serper.dev/search'}[provider]
    assert KEY not in request.data.decode()
    assert any(value == KEY or value == 'Bearer ' + KEY for value in request.headers.values())
    owner.service.lock();owner.service.unlock(PASSWORD)
    assert owner.search_setup.status()['provider'] == provider and runtime.available()


def test_failed_probe_does_not_replace_working_key_or_selection(setup, monkeypatch):
    from web_tools import SearchRequestError
    owner, _, runtime = setup
    save(owner)
    def refuse(*_): raise SearchRequestError('search_auth_failed','fixture-key-must-not-be-reported')
    monkeypatch.setattr('search_setup.search_request', refuse)
    reply=owner.dispatch({'id':'invalid','action':'save_search','provider':'exa','api_key':'bad-fixture','password':PASSWORD})
    assert reply['error']=='search_auth_failed' and 'bad-fixture' not in str(reply)
    assert owner.search_setup.status()['provider']=='tavily' and runtime.available()
    assert not any(value['name']=='EXA_API_KEY' for value in owner.lifecycle.list_values())


def test_switch_off_and_back_reuses_stored_keys_without_export(setup, monkeypatch):
    owner, _, runtime = setup
    monkeypatch.setattr('search_setup.search_request',lambda *_:{'results':[]})
    owner.search_setup.save('exa',KEY,PASSWORD)
    owner.search_setup.save('serper','serper-fixture',PASSWORD)
    owner.search_setup.save('off','',PASSWORD)
    assert not runtime.available()
    owner.search_setup.save('exa','',PASSWORD)
    assert runtime.available()
    assert owner.lifecycle.web_search_runtime_connection(runtime.credential_id,runtime.config)==('exa',{'x-api-key':KEY})
    assert {value['name'] for value in owner.lifecycle.list_values()} >= {'EXA_API_KEY','SERPER_API_KEY','IGOREK_SEARCH_PROVIDER'}


def test_owner_search_protocol_rejects_untrusted_and_locked_changes(setup, monkeypatch):
    owner, _, _ = setup
    monkeypatch.setattr('search_setup.search_request',lambda *_:pytest.fail('Unexpected probe'))
    request={'id':'fixture','action':'save_search','provider':'exa','api_key':KEY,'password':'wrong'}
    assert owner.dispatch(request)['error']=='authentication_failed'
    assert owner.dispatch({**request,'endpoint':'https://other.invalid/'})['error']=='invalid_request'
    owner.interface_locked=True
    assert owner.dispatch({**request,'password':PASSWORD})['error']=='interface_locked'
    owner.interface_locked=False
    owner.service.lock()
    assert owner.dispatch({**request,'password':PASSWORD})['error']=='vault_locked'


def test_lock_during_probe_prevents_late_key_commit(setup, monkeypatch):
    owner, _, _ = setup
    def probe(*_): owner.interface_locked=True;return {'results':[]}
    monkeypatch.setattr('search_setup.search_request',probe)
    reply=owner.dispatch({'id':'late','action':'save_search','provider':'exa','api_key':KEY,'password':PASSWORD})
    assert reply.get('error') and not owner.lifecycle.list_values()


@pytest.mark.parametrize('provider,response', [('tavily',{'error':'secret-fixture'}),('exa',{'results':None}),('serper',{'organic':'invalid'})])
def test_malformed_success_responses_do_not_verify_a_key(monkeypatch, provider, response):
    from web_tools import search_request
    monkeypatch.setattr('web_tools.urllib.request.build_opener',lambda *_:SimpleNamespace(open=lambda *_args,**_kwargs:io.BytesIO(json.dumps(response).encode())))
    with pytest.raises(RuntimeError,match='Invalid web search response'):
        search_request(provider,{},'search',{'query':'fixture'})


def test_search_choice_survives_owner_process_restart(tmp_path, monkeypatch):
    monkeypatch.setattr('search_setup.search_request',lambda *_:{'results':[]})
    owner=OwnerVault(tmp_path);owner.service.create(PASSWORD)
    owner.search_setup.save('exa',KEY,PASSWORD);owner.close()
    reopened=OwnerVault(tmp_path)
    try:
        assert reopened.service.status()['state']=='locked'
        reopened.service.unlock(PASSWORD)
        status=reopened.search_setup.status()
        assert status['provider']=='exa' and next(item for item in status['providers'] if item['id']=='exa')['configured']
        assert KEY not in str(status)
    finally:reopened.close()


def test_concurrent_search_changes_are_not_overwritten_by_a_slow_probe(setup, monkeypatch):
    owner,_,_=setup
    save(owner)
    def probe(*_):
        owner.lifecycle.save_value('', 'IGOREK_SEARCH_PROVIDER', 'variable', 'off', PASSWORD)
        return {'results':[]}
    monkeypatch.setattr('search_setup.search_request',probe)
    reply=owner.dispatch({'id':'late','action':'save_search','provider':'exa','api_key':KEY,'password':PASSWORD})
    assert reply.get('error') and owner.search_setup.status()['provider']=='off'
    assert not any(value['name']=='EXA_API_KEY' for value in owner.lifecycle.list_values())


@pytest.mark.parametrize('style',['full','compact'])
def test_search_only_provider_prompt_does_not_claim_extraction_support(tmp_path,style):
    from factory.core.prompt_assembly import build_cc_backend_system_prompt
    prompt=build_cc_backend_system_prompt(workdir=str(tmp_path),filesystem_mode='workspace',model_name='fixture',
        web_tools_enabled=True,web_extract_enabled=False,prompt_style=style)
    assert 'web_search' in prompt and 'web_extract недоступен' in prompt
    assert 'web_extract для данных URL' not in prompt


@pytest.mark.parametrize('provider,key',[('unknown','fixture'),('exa',''),('exa','fixture\nkey')])
def test_invalid_setup_input_never_probes_or_saves(setup,monkeypatch,provider,key):
    owner,_,_=setup
    monkeypatch.setattr('search_setup.search_request',lambda *_:pytest.fail('Unexpected request'))
    with pytest.raises(ValueError):owner.search_setup.save(provider,key,PASSWORD)
    assert not owner.lifecycle.list_values()


@pytest.mark.parametrize('name',['EXA_API_KEY','IGOREK_SEARCH_PROVIDER'])
def test_agent_created_setup_records_cannot_be_activated(setup,monkeypatch,name):
    owner,_,_=setup
    owner.lifecycle._append_agent_value(name,'secret' if name.endswith('KEY') else 'variable',KEY if name.endswith('KEY') else 'off',{'kind':'agent','task_id':'fixture'},'fixture')
    monkeypatch.setattr('search_setup.search_request',lambda *_:pytest.fail('Unexpected request'))
    with pytest.raises(ValueError):owner.search_setup.save('exa',KEY,PASSWORD)
    assert len(owner.lifecycle.list_values())==1


def test_locked_vault_disables_extract_capability(setup):
    owner,_,provider=setup
    save(owner);assert provider.supports_extract()
    owner.service.lock();assert not provider.supports_extract()


@pytest.mark.parametrize('provider,endpoint',[('unknown','search'),('tavily','../search'),('serper','extract')])
def test_adapter_rejects_unknown_routes_and_unsupported_extract(monkeypatch,provider,endpoint):
    from web_tools import search_request
    monkeypatch.setattr('web_tools.urllib.request.build_opener',lambda *_:pytest.fail('Unexpected request'))
    with pytest.raises(ValueError):search_request(provider,{},endpoint,{})


@pytest.mark.parametrize('code,expected',[(401,'search_auth_failed'),(403,'search_auth_failed'),(402,'search_limit_reached'),(429,'search_limit_reached'),(503,'search_unavailable')])
def test_provider_status_codes_have_stable_safe_errors(monkeypatch,code,expected):
    from web_tools import search_request,SearchRequestError
    def refuse(*args,**kwargs):raise urllib.error.HTTPError('https://api.exa.ai/search',code,KEY,{},io.BytesIO(KEY.encode()))
    monkeypatch.setattr('web_tools.urllib.request.build_opener',lambda *_:SimpleNamespace(open=refuse))
    with pytest.raises(SearchRequestError) as error:search_request('exa',{'x-api-key':KEY},'search',{'query':'fixture'})
    assert error.value.code==expected and KEY not in str(error.value)
