from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Callable

from factory.core.vault.audit import AuditLogger
from factory.core.vault.models import AccessLogRecord, BrokerDecision, CredentialStatus, Decision
from factory.core.vault.policy import PolicyEngine
from factory.core.vault.verifier import Verifier
from factory.core.vault.vault import Vault


SERVICE_ENV_MAP = {
    "openai": {"OPENAI_API_KEY": "{secret}"},
    "anthropic": {"ANTHROPIC_API_KEY": "{secret}"},
    "github": {"GITHUB_TOKEN": "{secret}", "GH_TOKEN": "{secret}"},
    "google": {"GOOGLE_OAUTH_ACCESS_TOKEN": "{secret}"},
    "minimax": {"MINIMAX_API_KEY": "{secret}"},
    "glm": {"GLM_API_KEY": "{secret}", "ANTHROPIC_AUTH_TOKEN": "{secret}"},
    "openrouter": {
        "OPENROUTER_API_KEY": "{secret}",
        "CODEX_OPENROUTER_API_KEY": "{secret}",
        "FACTORY_OPENROUTER_API_KEY": "{secret}",
    },
    "tavily": {"TAVILY_API_KEY": "{secret}"},
    "telegram": {"FACTORY_TELEGRAM_BOT_TOKEN": "{secret}"},
    "factory_api": {"FACTORY_API_KEY": "{secret}"},
    "fal": {"FAL_KEY": "{secret}", "FAL_API_KEY": "{secret}"},
    "xsearch": {"XSEARCH_BEARER_TOKEN": "{secret}"},
    "supabase": {"SUPABASE_ACCESS_TOKEN": "{secret}"},
    "obsidian": {"OBSIDIAN_VAULT_PATH": "{secret}"},
    "coingecko": {"COINGECKO_API_KEY": "{secret}"},
    "fish_audio": {"FISH_API_KEY": "{secret}"},
    "opencode": {
        "OPENCODE_API_KEY": "{secret}",
        "CODEX_OPENCODE_API_KEY": "{secret}",
    },
    "opencode-go": {
        "OPENCODE_API_KEY": "{secret}",
        "CODEX_OPENCODE_API_KEY": "{secret}",
    },
    # RunAnywhere (Wally): Secret is the refresh/access token for the Wally session.
    # `wally_auth` reads `WALLY_ACCESS_TOKEN`/`WALLY_REFRESH_TOKEN` and, if needed,
    # extends the session via `/auth/cli/refresh`.
    "wally": {
        "WALLY_ACCESS_TOKEN": "{secret}",
        "WALLY_REFRESH_TOKEN": "{secret}",
    },
}


class Broker:
    def __init__(
        self,
        vault: Vault,
        policy: PolicyEngine,
        verifier: Verifier,
        audit: AuditLogger,
        *,
        approval_checker: Callable[[str, str, str], bool] | None = None,
    ) -> None:
        self.vault = vault
        self.policy = policy
        self.verifier = verifier
        self.audit = audit
        self._approval_checker = approval_checker

    def _service_approved(self, agent_id: str, service: str, action: str) -> bool:
        agent = self.policy.get_agent_policy(agent_id)
        if agent is None:
            return False
        required = {name.strip().casefold() for name in agent.approval_required_services}
        if service.strip().casefold() not in required:
            return True
        if self._approval_checker is None:
            return False
        try:
            return self._approval_checker(agent_id, service, action) is True
        except Exception:
            return False

    def get_credential(self, service: str, purpose: str, agent_id: str) -> BrokerDecision:
        allowed, reason = self.policy.allow_raw_secret_access(agent_id, service)
        if not allowed:
            return self._deny(agent_id, service, "get_credential", reason)
        if not self._service_approved(agent_id, service, "get_credential"):
            return self._deny(agent_id, service, "get_credential", "owner approval required for service")
        record = self.vault.get_credential(service)
        if not record:
            return self._deny(agent_id, service, "get_credential", "credential not found in vault")
        return self._allow(
            agent_id,
            service,
            "get_credential",
            f"raw secret access allowed for purpose '{purpose}'",
            metadata={
                "credential_id": record.id,
                "service": record.service,
                "alias": record.alias,
                "credential_type": record.credential_type,
            },
        )

    def get_ephemeral_env(self, service: str, agent_id: str, ttl: int) -> BrokerDecision:
        allowed, reason = self.policy.can_access_service(agent_id, service)
        if not allowed:
            return self._deny(agent_id, service, "get_ephemeral_env", reason, ttl_seconds=ttl)
        ttl_ok, ttl_reason, effective_ttl = self.policy.enforce_ttl(agent_id, ttl)
        if not ttl_ok:
            return self._deny(agent_id, service, "get_ephemeral_env", ttl_reason, ttl_seconds=ttl)
        if not self._service_approved(agent_id, service, "get_ephemeral_env"):
            return self._deny(agent_id, service, "get_ephemeral_env", "owner approval required for service", ttl_seconds=effective_ttl)
        secret = self.vault.get_secret(service)
        if not secret:
            return self._deny(agent_id, service, "get_ephemeral_env", "credential not found in vault", ttl_seconds=effective_ttl)
        env_template = SERVICE_ENV_MAP.get(service, {"IGOREK_VAULT_SECRET": "{secret}"})
        env = {key: value.format(secret=secret.secret) for key, value in env_template.items()}
        return self._allow(
            agent_id,
            service,
            "get_ephemeral_env",
            "ephemeral environment materialization approved",
            ttl_seconds=effective_ttl,
            env=env,
        )

    def verify_credential(self, service: str) -> BrokerDecision:
        record = self.vault.get_credential(service)
        if not record:
            return BrokerDecision(
                allowed=False,
                service=service,
                agent_id="igorek-vault",
                reason="credential not found in vault",
            )
        secret = self.vault.get_secret(service)
        assert secret is not None
        result = self.verifier.verify(service, secret.secret)
        status = CredentialStatus.active if result.success else (
            CredentialStatus.invalid if result.category.value == "invalid_or_expired" else CredentialStatus.unknown
        )
        self.vault.update_status(service, status=status, verified_at=result.checked_at.isoformat())
        self.audit.record(
            AccessLogRecord(
                agent_id="igorek-vault",
                service=service,
                action="verify_credential",
                decision=Decision.allow,
                reason=result.reason,
                verification_result=result.category,
            )
        )
        return BrokerDecision(
            allowed=result.success,
            service=service,
            agent_id="igorek-vault",
            reason=result.reason,
            metadata={"verification_result": result.model_dump(mode="json")},
        )

    def list_available_credentials(self, agent_id: str) -> list[dict[str, str]]:
        agent_policy = self.policy.get_agent_policy(agent_id)
        if not agent_policy:
            self._deny(agent_id, "n/a", "list_available_credentials", "agent is not defined in policy")
            return []
        allowed_services = set(agent_policy.services)
        records = self.vault.list_credentials()
        visible = [
            {
                "service": record.service,
                "alias": record.alias,
                "credential_type": record.credential_type,
                "status": record.status.value,
            }
            for record in records
            if record.service in allowed_services
        ]
        self.audit.record(
            AccessLogRecord(
                agent_id=agent_id,
                service="*",
                action="list_available_credentials",
                decision=Decision.allow,
                reason="returned policy-filtered credential metadata",
            )
        )
        return visible

    def _allow(
        self,
        agent_id: str,
        service: str,
        action: str,
        reason: str,
        ttl_seconds: int | None = None,
        env: dict[str, str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> BrokerDecision:
        decision = BrokerDecision(
            allowed=True,
            service=service,
            agent_id=agent_id,
            reason=reason,
            ttl_seconds=ttl_seconds,
            env=env or {},
            metadata=metadata or {},
        )
        self.audit.record(
            AccessLogRecord(
                agent_id=agent_id,
                service=service,
                action=action,
                decision=Decision.allow,
                reason=reason,
                ttl_seconds=ttl_seconds,
            )
        )
        return decision

    def _deny(
        self,
        agent_id: str,
        service: str,
        action: str,
        reason: str,
        ttl_seconds: int | None = None,
    ) -> BrokerDecision:
        self.audit.record(
            AccessLogRecord(
                agent_id=agent_id,
                service=service,
                action=action,
                decision=Decision.deny,
                reason=reason,
                ttl_seconds=ttl_seconds,
            )
        )
        return BrokerDecision(
            allowed=False,
            service=service,
            agent_id=agent_id,
            reason=reason,
            ttl_seconds=ttl_seconds,
        )
