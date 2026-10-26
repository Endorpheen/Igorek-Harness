"""Vault scanning of owned fixture files and provider verification without network."""
import io
import json
import socket
from pathlib import Path
from unittest.mock import Mock
import urllib.error

import pytest

from factory.core.vault import scanner as scanning
from factory.core.vault import verifier as verification
from factory.core.vault.config import AppSettings
from factory.core.vault.models import FindingSeverity, PolicyConfig, VerificationCategory
from factory.core.vault.policy import PolicyEngine


@pytest.fixture
def scanner(tmp_path):
    files=tmp_path/'files';files.mkdir()
    settings=AppSettings(runtime_home=tmp_path/'vault',default_scan_roots=[files])
    settings.ensure_runtime_layout()
    policy=PolicyEngine(PolicyConfig(managed_paths=[str(files)],plaintext_exempt_paths=[str(files/'exempt')],plaintext_migration_paths=[str(files/'migration')]))
    return scanning.Scanner(settings,policy),files


def test_actual_scanner_finds_duplicates_without_returning_secret_contents(scanner):
    scan,root=scanner;fake='sk-'+'a'*24
    for filename in ['one.env','two.json']:
        path=root/filename;path.write_text('fixture='+fake+'\n');path.chmod(0o600)
    findings=scan.scan()
    plain=[x for x in findings if x.kind=='plaintext_secret'];duplicates=[x for x in findings if x.kind=='duplicate_secret']
    assert len(plain)==len(duplicates)==2
    assert all(x.severity is FindingSeverity.critical and x.line_number==1 for x in plain)
    assert len({x.fingerprint for x in findings if x.fingerprint})==1
    assert fake not in json.dumps([x.model_dump(mode='json') for x in findings])


@pytest.mark.parametrize('folder,severity',[('normal',FindingSeverity.critical),('exempt',FindingSeverity.medium),('migration',FindingSeverity.medium)])
def test_actual_scan_uses_path_policy_for_classification(scanner,folder,severity):
    scan,root=scanner;path=root/folder/'fixture.env';path.parent.mkdir();path.write_text('key=sk-'+'b'*24);path.chmod(0o600)
    plain=[x for x in scan.scan([path]) if x.kind=='plaintext_secret']
    assert len(plain)==1 and plain[0].severity is severity


@pytest.mark.parametrize('fallback',[False,True])
def test_ignore_file_excludes_matching_paths_in_both_matcher_modes(scanner,monkeypatch,fallback):
    scan,root=scanner
    scan.settings.ignore_path.write_text('# fixture ignore rules\n\n**/ignored/**\n')
    ignored=root/'ignored';ignored.mkdir();(ignored/'fixture.env').write_text('key=sk-'+'c'*24)
    kept=root/'kept.env';kept.write_text('key=sk-'+'d'*24);kept.chmod(0o600)
    if fallback:
        monkeypatch.setattr(scanning,'pathspec',None)
        scan.settings.ignore_path.write_text(str(ignored / '*')+'\n')
    findings=[x for x in scan.scan() if x.kind=='plaintext_secret']
    assert [x.path for x in findings]==[str(kept)]


def test_scanner_skips_comments_non_text_large_and_missing_files(scanner):
    scan,root=scanner;fake='sk-'+'e'*24
    (root/'comments.env').write_text('\n'.join(prefix+' '+fake for prefix in ['#','//',';','--']))
    (root/'image.png').write_text(fake)
    (root/'large.env').write_text('x'*(scanning.MAX_FILE_BYTES+1)+fake)
    (root/'nested').mkdir()
    assert not [x for x in scan.scan([root,root/'missing']) if x.kind=='plaintext_secret']


def test_file_read_failure_does_not_abort_scanning_other_files(scanner,monkeypatch):
    scan,root=scanner;bad=root/'bad.env';good=root/'good.env'
    for path in [bad,good]:path.write_text('key=sk-'+'f'*24);path.chmod(0o600)
    original=Path.read_text
    def read(path,*args,**kwargs):
        if path==bad:raise PermissionError('fixture denied')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    plain=[x for x in scan.scan() if x.kind=='plaintext_secret']
    assert [x.path for x in plain]==[str(good)]


class Reply(io.BytesIO):
    def getcode(self):return 200


@pytest.fixture
def transport(monkeypatch):
    call=Mock(return_value=Reply(b'{}'))
    monkeypatch.setattr(verification.urllib.request,'urlopen',call)
    return call


@pytest.mark.parametrize('service,host,header',[
    ('openai','api.openai.com','Authorization'),('anthropic','api.anthropic.com','X-api-key'),
    ('openrouter','openrouter.ai','Authorization'),('github','api.github.com','Authorization'),
    ('tavily','api.tavily.com','Authorization'),('telegram','api.telegram.org',None),
    ('fal','fal.run','Authorization'),('xsearch','api.twitter.com','Authorization'),('supabase','api.supabase.com','Authorization'),
])
def test_provider_adapter_builds_expected_authenticated_get_without_real_network(transport,service,host,header):
    result=verification.Verifier(timeout_seconds=3).verify(service,'synthetic-fixture')
    assert result.success and result.category is VerificationCategory.valid and result.status_code==200
    request=transport.call_args.args[0]
    assert request.host==host and request.get_method()=='GET' and transport.call_args.kwargs=={'timeout':3}
    if header:assert 'synthetic-fixture' in request.get_header(header)
    if service=='telegram':assert '/botsynthetic-fixture/getMe' in request.full_url


@pytest.mark.parametrize('service,variable',[('minimax','IGOREK_VAULT_MINIMAX_VERIFY_URL'),('glm','IGOREK_VAULT_GLM_VERIFY_URL')])
def test_optional_verifier_requires_configuration_then_uses_explicit_endpoint(transport,monkeypatch,service,variable):
    monkeypatch.delenv(variable,raising=False)
    verifier=verification.Verifier()
    assert verifier.verify(service,'synthetic').category is VerificationCategory.unknown and not transport.called
    monkeypatch.setenv(variable,'https://example.com/fixture')
    assert verifier.verify(service,'synthetic').success
    assert transport.call_args.args[0].full_url=='https://example.com/fixture'


@pytest.mark.parametrize('status,body,headers,category',[
    (400,'bad',{},VerificationCategory.invalid_or_expired),
    (401,'bad',{},VerificationCategory.invalid_or_expired),
    (403,'insufficient scope',{},VerificationCategory.permission_scope_issue),
    (403,'rate limit exceeded',{},VerificationCategory.rate_limit),
    (403,'limited',{'X-RateLimit-Remaining':'0'},VerificationCategory.rate_limit),
    (429,'slow down',{},VerificationCategory.rate_limit),
    (404,'missing',{},VerificationCategory.endpoint_misconfiguration),
    (500,'{"message":"fixture upstream error"}',{},VerificationCategory.unknown),
    (502,'plain fixture upstream error',{},VerificationCategory.unknown),
])
def test_http_failures_keep_invalid_key_distinct_from_quota_and_configuration_errors(transport,status,body,headers,category):
    transport.side_effect=urllib.error.HTTPError('https://example.com',status,'fixture',headers,io.BytesIO(body.encode()))
    result=verification.Verifier().verify('openai','synthetic')
    assert not result.success and result.category is category and result.status_code==status


@pytest.mark.parametrize('failure',[urllib.error.URLError('fixture offline'),socket.timeout('fixture timeout'),RuntimeError('fixture unexpected')])
def test_transport_failure_never_marks_a_credential_as_invalid(transport,failure):
    transport.side_effect=failure
    result=verification.Verifier().verify('openai','synthetic')
    assert not result.success
    assert result.category is (VerificationCategory.unknown if isinstance(failure,RuntimeError) else VerificationCategory.network_failure)


@pytest.mark.parametrize('endpoint',['file:///tmp/fixture','relative-path','https:///missing-host'])
def test_bad_endpoint_is_rejected_before_network(transport,endpoint):
    config=verification.ProviderVerifierConfig('fixture',endpoint,{})
    result=verification.Verifier()._http_verify(config)
    assert result.category is VerificationCategory.endpoint_misconfiguration and not transport.called


def test_unknown_provider_has_no_network_fallback(transport):
    result=verification.Verifier().verify('fixture-unknown','synthetic')
    assert result.category is VerificationCategory.unknown and not transport.called
