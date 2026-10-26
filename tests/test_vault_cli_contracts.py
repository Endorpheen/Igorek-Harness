"""Vault CLI metadata and import contracts over an owned encrypted database."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.vault import cli_support as cli
from factory.core.vault.vault import Vault


@pytest.fixture
def services(tmp_path,monkeypatch):
    vault=Vault(tmp_path/'vault.db',tmp_path/'salt','synthetic fixture passphrase')
    env=SimpleNamespace(vault=vault,settings=SimpleNamespace(runtime_home=tmp_path,db_path=tmp_path/'vault.db',effective_policy_path=tmp_path/'policy.yaml',generated_skills_dir=tmp_path/'generated'),policy=SimpleNamespace(config=SimpleNamespace(agents={'zeta':{},'fixture':{}})),broker=Mock(),audit=Mock())
    monkeypatch.setattr(cli,'build_services',lambda **kwargs:env)
    monkeypatch.setattr(cli.getpass,'getpass',Mock(return_value='synthetic-prompted-secret'))
    return env


def test_add_list_metadata_and_rotate_never_return_encrypted_or_plaintext_payload(services):
    first=cli.add(service='openai',alias='fixture',credential_type='api_key',secret='synthetic-original-secret')
    identity=first['credential']['id']
    for payload in [first,cli.list_credentials(),cli.show_metadata(identity),cli.rotate(identity,'synthetic-replacement-secret')]:
        serialized=json.dumps(payload)
        assert 'encrypted_payload' not in serialized and 'synthetic-original-secret' not in serialized and 'synthetic-replacement-secret' not in serialized
    assert services.vault.get_secret(identity).secret=='synthetic-replacement-secret'
    assert cli.show_metadata('absent')=={'status':'missing','service_or_id':'absent'}


def test_duplicate_add_requires_explicit_replace_and_keeps_record_identity(services):
    args={'service':'openai','alias':'fixture','credential_type':'api_key'}
    first=cli.add(**args,secret='synthetic-first')
    refused=cli.add(**args,secret='synthetic-second')
    assert refused['status']=='error'
    assert services.vault.get_secret(first['credential']['id']).secret=='synthetic-first'
    replaced=cli.add(**args,secret='synthetic-second',replace_existing=True)
    assert replaced['credential']['id']==first['credential']['id']
    assert len(services.vault.list_credentials())==1


def test_interactive_secret_prompt_is_used_only_when_secret_argument_is_absent(services):
    first=cli.add(service='fixture',alias='fixture',credential_type='api_key',secret=None)
    identity=first['credential']['id']
    cli.getpass.getpass.assert_called_once_with('Secret: ')
    assert services.vault.get_secret(identity).secret=='synthetic-prompted-secret'
    cli.rotate(identity,None)
    assert cli.getpass.getpass.call_args.args==('New secret: ',)


def test_deletion_requires_yes_and_does_not_remove_other_credentials(services):
    first=cli.add(service='first',alias='fixture',credential_type='api_key',secret='synthetic-one')['credential']['id']
    second=cli.add(service='second',alias='fixture',credential_type='api_key',secret='synthetic-two')['credential']['id']
    assert cli.delete(first,False)['status']=='error' and len(services.vault.list_credentials())==2
    assert cli.delete(first,True)['status']=='ok' and cli.delete(first,True)['status']=='missing'
    assert services.vault.get_secret(second).secret=='synthetic-two'


@pytest.mark.parametrize('redact',[False,True])
def test_env_import_redaction_really_removes_secret_bytes_and_preserves_unrelated_lines(services,tmp_path,redact):
    source=tmp_path/'fixture.env'
    source.write_text('# keep comment\nOPENAI_API_KEY="synthetic-original-secret"\nPORT=42\nEMPTY_LINE\n\n')
    original=source.read_bytes()
    result=cli.import_credentials(from_env=str(source),redact_source=redact)
    assert result['status']=='ok' and result['imported_count']==1 and result['redacted_source']==redact
    record=services.vault.list_credentials()[0]
    assert record.service=='openai' and services.vault.get_secret(record.id).secret=='synthetic-original-secret'
    text=source.read_text()
    assert '# keep comment' in text and 'PORT=42' in text and 'EMPTY_LINE' in text
    if redact:
        assert 'synthetic-original-secret' not in text and '# REDACTED' in text
        if os.name == "posix":
            assert source.stat().st_mode & 0o777==0o600
    else:
        assert source.read_bytes()==original
    assert 'synthetic-original-secret' not in json.dumps(result) and 'encrypted_payload' not in json.dumps(result)


def test_duplicate_import_does_not_redact_unimported_source(services,tmp_path):
    source=tmp_path/'fixture.env';source.write_text('OPENAI_API_KEY=synthetic-first\n')
    assert cli.import_credentials(from_env=str(source))['status']=='ok'
    source.write_text('OPENAI_API_KEY=synthetic-second\n');original=source.read_bytes()
    assert cli.import_credentials(from_env=str(source),redact_source=True)['status']=='error'
    assert source.read_bytes()==original
    result=cli.import_credentials(from_env=str(source),replace_existing=True)
    assert result['status']=='ok' and len(services.vault.list_credentials())==1


def test_json_import_uses_detected_credential_type_and_ignores_non_credentials(services,tmp_path):
    source=tmp_path/'fixture.json';fake='sk-'+'a'*24
    source.write_text(json.dumps({'OPENAI':fake,'ignored':4,'plain':'ordinary text'}))
    result=cli.import_credentials(from_json=str(source))
    assert result['status']=='ok' and result['imported_count']==1 and not result['redacted_source']
    record=services.vault.list_credentials()[0]
    assert record.service=='openai' and record.alias=='openai' and services.vault.get_secret(record.id).secret==fake
    assert fake not in json.dumps(result)
    assert cli.import_credentials(from_json=str(source))['status']=='error'


def test_import_without_source_or_nonobject_json_has_a_clear_error(services,tmp_path):
    assert cli.import_credentials()['status']=='error'
    source=tmp_path/'fixture.json';source.write_text('[]')
    assert cli.import_credentials(from_json=str(source))['status']=='error'
    assert services.vault.list_credentials()==[]


def test_status_lists_metadata_only_and_sorts_agent_names(services):
    result=cli.status()
    assert result['agents']==['fixture','zeta'] and result['credentials']==0
    assert Path(result['db_path'])==services.vault.db_path


def test_verify_routes_explicit_service_or_all_records_without_real_connections(services):
    for name in ['first','second']:
        cli.add(service=name,alias='fixture',credential_type='api_key',secret='synthetic')
    services.broker.verify_credential.side_effect=lambda target:SimpleNamespace(model_dump=lambda **kwargs:{'service':target,'verified':True})
    assert cli.verify()['status']=='error'
    assert cli.verify(service='first')['results']==[{'service':'first','verified':True}]
    assert cli.verify(all_services=True)['results']==[{'service':name,'verified':True} for name in ['first','second']]


def test_broker_and_audit_forward_only_explicit_cli_fields(services):
    services.broker.get_ephemeral_env.return_value=SimpleNamespace(model_dump=lambda **kwargs:{'allowed':False})
    services.broker.list_available_credentials.return_value=['fixture-metadata']
    services.audit.list_recent.return_value=[{'event':'fixture'}]
    assert cli.broker_env('fixture','agent',42)=={'allowed':False}
    services.broker.get_ephemeral_env.assert_called_once_with(service='fixture',agent_id='agent',ttl=42)
    assert cli.broker_list('agent')['credentials']==['fixture-metadata']
    assert cli.audit(7)['items']==[{'event':'fixture'}]
    services.audit.list_recent.assert_called_once_with(limit=7)
