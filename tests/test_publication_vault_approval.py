"""Approval-required services never materialize a secret without host consent."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.vault.broker import Broker
from factory.core.vault.models import AgentPolicy, PolicyConfig
from factory.core.vault.policy import PolicyEngine


@pytest.mark.parametrize('checker', [None, lambda *args: False, lambda *args: 'yes'])
def test_approval_required_env_is_denied_before_secret_lookup(checker):
    vault = Mock()
    policy = PolicyEngine(PolicyConfig(agents={'fixture': AgentPolicy(
        services=['tavily'], approval_required_services=['tavily'])}))
    broker = Broker(vault, policy, Mock(), Mock(), approval_checker=checker)
    decision = broker.get_ephemeral_env('tavily', 'fixture', 100)
    assert not decision.allowed
    assert not decision.env
    vault.get_secret.assert_not_called()


def test_host_approval_is_bound_to_agent_service_and_action():
    vault = Mock()
    vault.get_secret.return_value = SimpleNamespace(secret='synthetic-secret')
    policy = PolicyEngine(PolicyConfig(agents={'fixture': AgentPolicy(
        services=['tavily'], approval_required_services=['tavily'])}))
    checker = Mock(return_value=True)
    broker = Broker(vault, policy, Mock(), Mock(), approval_checker=checker)
    decision = broker.get_ephemeral_env('tavily', 'fixture', 100)
    assert decision.allowed
    assert decision.env == {'TAVILY_API_KEY': 'synthetic-secret'}
    checker.assert_called_once_with('fixture', 'tavily', 'get_ephemeral_env')


def test_nonapproval_service_retains_normal_ttl_and_environment():
    vault = Mock()
    vault.get_secret.return_value = SimpleNamespace(secret='synthetic-secret')
    policy = PolicyEngine(PolicyConfig(agents={'fixture': AgentPolicy(services=['tavily'], max_ttl_seconds=60)}))
    broker = Broker(vault, policy, Mock(), Mock())
    decision = broker.get_ephemeral_env('tavily', 'fixture', 100)
    assert decision.allowed
    assert decision.ttl_seconds == 60


def test_service_case_does_not_bypass_raw_or_ephemeral_approval():
    vault = Mock()
    policy = PolicyEngine(PolicyConfig(agents={'fixture': AgentPolicy(
        services=['TAVILY'], approval_required_services=['tavily'],
        raw_secret_access=True, ephemeral_env_only=False)}))
    broker = Broker(vault, policy, Mock(), Mock())
    assert not broker.get_ephemeral_env('TAVILY', 'fixture', 100).allowed
    assert not broker.get_credential('TAVILY', 'fixture purpose', 'fixture').allowed
    vault.get_secret.assert_not_called()
    vault.get_credential.assert_not_called()
