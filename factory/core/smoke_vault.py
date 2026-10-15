#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import sys
import os
import json
import sqlite3
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from factory.core.vault.audit import AuditLogger
from factory.core.vault.broker import Broker
from factory.core.vault.config import AppSettings
from factory.core.vault.models import AgentPolicy, PolicyConfig, VerificationCategory
from factory.core.vault.policy import PolicyEngine
from factory.core.vault.scanner import Scanner
from factory.core.vault.verifier import Verifier
from factory.core.vault.vault import DuplicateCredentialError, Vault


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def smoke_encrypt_and_duplicate_guard(root: Path) -> None:
    vault = Vault(root / "vault.db", root / "salt.bin", "test-passphrase")
    record = vault.add_credential("openrouter", "sk-or-secret-value-1234567890", "api_key")
    _assert(record.encrypted_payload != "sk-or-secret-value-1234567890", "secret stored as plaintext")
    secret = vault.get_secret("openrouter")
    _assert(secret is not None, "secret missing after add")
    _assert(secret.secret == "sk-or-secret-value-1234567890", "decrypted secret mismatch")
    try:
        vault.add_credential("openrouter", "sk-or-other-secret-1234567890", "api_key")
    except DuplicateCredentialError:
        return
    raise AssertionError("duplicate service+alias was accepted")


def smoke_broker_policy(root: Path) -> None:
    vault = Vault(root / "broker.db", root / "broker-salt.bin", "test-passphrase")
    vault.add_credential("tavily", "fixture-tavily-key", "api_key")
    policy = PolicyEngine(
        PolicyConfig(
            agents={
                "cc-backend": AgentPolicy(
                    services=["tavily"],
                    raw_secret_access=False,
                    ephemeral_env_only=True,
                    max_ttl_seconds=60,
                )
            }
        )
    )
    broker = Broker(vault, policy, Verifier(), AuditLogger(root / "broker.db"))
    raw = broker.get_credential("tavily", "test", "cc-backend")
    _assert(raw.allowed is False, "raw secret access was allowed")
    env = broker.get_ephemeral_env("tavily", "cc-backend", ttl=900)
    _assert(env.allowed is True, "ephemeral env was denied")
    _assert(env.ttl_seconds == 60, "ttl was not capped")
    _assert(env.env["TAVILY_API_KEY"] == "fixture-tavily-key", "env map mismatch")


def smoke_verifier_categories() -> None:
    verifier = Verifier()
    invalid = verifier._classify_http_error("openai", 401, "{}")
    _assert(invalid.category is VerificationCategory.invalid_or_expired, "401 not invalid_or_expired")
    scoped = verifier._classify_http_error("github", 403, '{"message":"Resource not accessible"}')
    _assert(scoped.category is VerificationCategory.permission_scope_issue, "403 not permission_scope_issue")
    limited = verifier._classify_http_error("github", 429, "{}")
    _assert(limited.category is VerificationCategory.rate_limit, "429 not rate_limit")


def smoke_scanner(root: Path) -> None:
    managed = root / "managed"
    managed.mkdir()
    secret_file = managed / ".env"
    secret_file.write_text("OPENAI_API_KEY=sk-secretvalue12345678901234567890\n", encoding="utf-8")
    policy = PolicyEngine(
        PolicyConfig(
            managed_paths=[str(managed)],
            deny_plaintext_under_managed_paths=True,
        )
    )
    settings = AppSettings(runtime_home=root / "runtime", default_scan_roots=[managed])
    findings = Scanner(settings, policy=policy).scan()
    _assert(any(item.kind == "plaintext_secret" for item in findings), "plaintext finding missing")
    _assert(any(item.severity.value == "critical" for item in findings), "managed plaintext not critical")


def smoke_worker_cc_backend_materialization(root: Path) -> None:
    os.environ["IGOREK_VAULT_HOME"] = str(root / "worker-vault")
    os.environ["IGOREK_VAULT_PASSPHRASE"] = "test-passphrase"
    os.environ["FACTORY_VAULT_ENABLED"] = "1"
    os.environ["FACTORY_VAULT_SERVICES"] = "tavily"
    os.environ["FACTORY_VAULT_AGENT"] = "cc-backend"
    os.environ["FACTORY_VAULT_TTL_SECONDS"] = "30"

    from factory.core.vault.runtime import build_services
    from factory.core.worker import Worker, _temporary_env

    services = build_services(prompt=False)
    services.vault.add_credential("tavily", "fixture-tavily-key", "api_key")

    class FakeStore:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def log_event(self, **kwargs):
            self.events.append(kwargs)
            return "event-id"

    class FakeSecurityRuntime:
        def __init__(self) -> None:
            self.store = FakeStore()

    worker = Worker.__new__(Worker)
    worker.profile = "cc-local"
    worker.backend = "cc-backend"
    worker.config = {}
    worker.security_runtime = FakeSecurityRuntime()

    env = worker._materialize_vault_env(task_id="task-vault-smoke", task_metadata={})
    _assert(env == {"TAVILY_API_KEY": "fixture-tavily-key"}, "worker vault env mismatch")

    os.environ.pop("TAVILY_API_KEY", None)
    with _temporary_env(env):
        _assert(os.environ.get("TAVILY_API_KEY") == env["TAVILY_API_KEY"], "temporary env not visible")
    _assert("TAVILY_API_KEY" not in os.environ, "temporary env leaked after context")

    rendered_events = repr(worker.security_runtime.store.events)
    _assert("fixture-tavily-key" not in rendered_events, "raw secret leaked into security events")
    _assert("TAVILY_API_KEY" in rendered_events, "env name missing from security events")


def smoke_worker_result_redaction(root: Path) -> None:
    from factory.core.schema import initialize_database, open_db
    from factory.core.worker import Worker

    db_path = initialize_database(str(root / "worker-result.db"))
    with open_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                task_id, parent_id, stage, status, profile, input_text, metadata,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-redaction-smoke",
                None,
                "main",
                "running",
                "cc-local",
                "test",
                "{}",
                int(time.time()),
                int(time.time()),
            ),
        )
        worker = Worker.__new__(Worker)
        worker.profile = "cc-local"
        worker.pid = 123
        worker.backend = "cc-backend"
        worker.model = "local-model"
        worker.config = {}
        secret = "sk-secretvalue12345678901234567890"
        result = {
            "status": "fail",
            "error": f"failed with token {secret}",
            "output": {
                "text": f"OPENAI_API_KEY={secret}",
                "stderr": f"bearer {secret}",
                "files": [],
            },
            "usage": {"prompt": 1, "completion": 2, "lat_ms_p50": 3},
            "mcp_telemetry": [
                {
                    "kind": "mcp_error",
                    "source_event_type": "tool_error",
                    "summary": f"token {secret}",
                    "event": {"text": f"api_key={secret}"},
                }
            ],
        }
        worker.update_task(conn, "task-redaction-smoke", result)
        row = conn.execute(
            "SELECT output_text, error_message FROM tasks WHERE task_id = ?",
            ("task-redaction-smoke",),
        ).fetchone()
        event_rows = conn.execute(
            "SELECT details FROM events WHERE task_id = ? ORDER BY created_at",
            ("task-redaction-smoke",),
        ).fetchall()

    stored_text = "\n".join([str(row[0]), str(row[1]), *[str(item[0]) for item in event_rows]])
    _assert(secret not in stored_text, "raw secret leaked into worker persistence")
    _assert("[REDACTED]" in stored_text, "redaction marker missing from worker persistence")
    for item in event_rows:
        json.loads(item[0])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="igorek-vault-smoke-") as tmp:
        root = Path(tmp)
        smoke_encrypt_and_duplicate_guard(root)
        smoke_broker_policy(root)
        smoke_verifier_categories()
        smoke_scanner(root)
        smoke_worker_cc_backend_materialization(root)
        smoke_worker_result_redaction(root)
    print("smoke_vault: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
