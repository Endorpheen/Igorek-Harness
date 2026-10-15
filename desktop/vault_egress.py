"""Bind the standard KVM auth proxy to one verified desktop Vault profile."""
from copy import deepcopy

from factory.core.isolation.egress_auth import ProviderAuthInjector
from factory.core.isolation.errors import IsolationError
from factory.core.isolation.secret_policy import (
    ProviderRoute, ProviderSecretPolicy, ProxyEndpoint, build_proxy_plan,
    REASON_PROXY_UNAVAILABLE,
)
from provider_setup import profile_config


class DesktopVaultEgress(ProviderAuthInjector):
    def __init__(self, lifecycle, credential_id, config):
        config = deepcopy(config)
        expected = profile_config(config['name'], config['model'], config['base_url'], config['api_format'])
        if config != expected:
            raise ValueError('Invalid desktop provider binding')
        self._lifecycle = lifecycle
        self._credential_id = credential_id
        self._config = config
        anthropic = config['api_format'] == 'anthropic'
        self._route = ProviderRoute(provider='desktop-vault', upstream_base=config['base_url'],
            injectable=True, auth_header='x-api-key' if anthropic else 'Authorization',
            auth_prefix='' if anthropic else 'Bearer ')
        super().__init__(ProviderSecretPolicy(), env={}, token_provider=self._token)

    def _token(self):
        headers = self._lifecycle.provider_runtime_headers(
            self._credential_id, self._config, self._config['api_format'])
        value = headers.get(self._route.auth_header, '')
        if self._route.auth_prefix:
            if not value.startswith(self._route.auth_prefix):
                raise ValueError('Invalid desktop auth header')
            value = value[len(self._route.auth_prefix):]
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError('Invalid desktop auth token')
        return value

    def plan(self, base_url):
        if base_url != self._config['base_url']:
            raise IsolationError(REASON_PROXY_UNAVAILABLE, 'desktop_provider_binding_changed')
        try:
            self._token()
        except Exception as error:
            raise IsolationError(REASON_PROXY_UNAVAILABLE, 'desktop_vault_unavailable') from error
        proxy = self.ensure_proxy(self._route)
        return build_proxy_plan(self._route, base_url, endpoint=ProxyEndpoint(
            bind_host=self._policy.bind_host, port=proxy.port, guest_host=self._policy.guest_host,
            capability=proxy.capability))
