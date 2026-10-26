from __future__ import annotations

import getpass
import json
from pathlib import Path
from typing import Any

from factory.core.vault.detectors import detect_matches, guess_from_env_name
from factory.core.vault.runtime import build_services
from factory.core.vault.scanner import Scanner
from factory.core.vault.skillgen import SkillGenerator
from factory.core.vault.vault import DuplicateCredentialError


def status() -> dict[str, Any]:
    services = build_services(prompt=True)
    return {
        "status": "ok",
        "runtime_home": str(services.settings.runtime_home),
        "db_path": str(services.settings.db_path),
        "policy_path": str(services.settings.effective_policy_path),
        "credentials": len(services.vault.list_credentials()),
        "agents": sorted(services.policy.config.agents.keys()),
    }


def scan(paths: list[str] | None = None) -> dict[str, Any]:
    services = build_services(prompt=True)
    scanner = Scanner(services.settings, policy=services.policy)
    findings = scanner.scan(paths=[Path(item).expanduser() for item in paths] if paths else None)
    return {
        "status": "ok",
        "count": len(findings),
        "findings": [item.model_dump(mode="json") for item in findings],
    }


def add(
    *,
    service: str,
    alias: str,
    credential_type: str,
    secret: str | None,
    replace_existing: bool = False,
) -> dict[str, Any]:
    services = build_services(prompt=True)
    secret_value = secret or getpass.getpass("Secret: ")
    try:
        record = services.vault.add_credential(
            service=service,
            alias=alias,
            credential_type=credential_type,
            secret=secret_value,
            replace_existing=replace_existing,
        )
    except DuplicateCredentialError as exc:
        return {"status": "error", "error": str(exc)}
    return {
        "status": "ok",
        "credential": record.model_dump(mode="json", exclude={"encrypted_payload"}),
    }


def import_credentials(
    *,
    from_env: str | None = None,
    from_json: str | None = None,
    redact_source: bool = False,
    replace_existing: bool = False,
) -> dict[str, Any]:
    if not from_env and not from_json:
        return {"status": "error", "error": "Provide --from-env or --from-json"}
    services = build_services(prompt=True)
    source = Path(from_env or from_json or "").expanduser()
    content = source.read_text(encoding="utf-8", errors="ignore")
    imported: list[dict[str, Any]] = []
    imported_lines: set[int] = set()

    if from_env:
        lines = content.splitlines()
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            guessed = guess_from_env_name(name.strip())
            if not guessed:
                continue
            service, credential_type = guessed
            try:
                record = services.vault.add_credential(
                    service=service,
                    secret=value.strip().strip("'\""),
                    credential_type=credential_type,
                    alias=name.strip().lower(),
                    imported_from=str(source),
                    replace_existing=replace_existing,
                )
            except DuplicateCredentialError as exc:
                return {"status": "error", "error": str(exc)}
            imported.append(record.model_dump(mode="json", exclude={"encrypted_payload"}))
            imported_lines.add(index)
        if redact_source and imported_lines:
            redacted_lines = [
                f"# REDACTED by igorek-vault import: {line.split('=', 1)[0].strip()}=[redacted]" if i in imported_lines else line
                for i, line in enumerate(lines)
            ]
            source.write_text("\n".join(redacted_lines) + "\n", encoding="utf-8")
            source.chmod(0o600)

    if from_json:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return {"status": "error", "error": "JSON import source must be an object"}
        for key, value in parsed.items():
            if not isinstance(value, str):
                continue
            matches = detect_matches(value)
            if not matches:
                continue
            detector, secret = matches[0]
            try:
                record = services.vault.add_credential(
                    service=detector.service,
                    secret=secret,
                    credential_type=detector.credential_type,
                    alias=str(key).lower(),
                    imported_from=str(source),
                    replace_existing=replace_existing,
                )
            except DuplicateCredentialError as exc:
                return {"status": "error", "error": str(exc)}
            imported.append(record.model_dump(mode="json", exclude={"encrypted_payload"}))

    return {
        "status": "ok",
        "source": str(source),
        "imported_count": len(imported),
        "credentials": imported,
        "redacted_source": bool(redact_source and imported_lines),
    }


def list_credentials() -> dict[str, Any]:
    services = build_services(prompt=True)
    records = services.vault.list_credentials()
    return {
        "status": "ok",
        "count": len(records),
        "credentials": [
            item.model_dump(mode="json", exclude={"encrypted_payload"}) for item in records
        ],
    }


def show_metadata(service_or_id: str) -> dict[str, Any]:
    services = build_services(prompt=True)
    record = services.vault.get_credential(service_or_id)
    if not record:
        return {"status": "missing", "service_or_id": service_or_id}
    return {
        "status": "ok",
        "credential": record.model_dump(mode="json", exclude={"encrypted_payload"}),
    }


def rotate(service_or_id: str, secret: str | None) -> dict[str, Any]:
    services = build_services(prompt=True)
    secret_value = secret or getpass.getpass("New secret: ")
    record = services.vault.rotate(service_or_id, secret_value)
    return {
        "status": "ok",
        "credential": record.model_dump(mode="json", exclude={"encrypted_payload"}),
    }


def delete(service_or_id: str, yes: bool) -> dict[str, Any]:
    if not yes:
        return {"status": "error", "error": "Deletion requires --yes"}
    services = build_services(prompt=True)
    removed = services.vault.delete(service_or_id)
    return {"status": "ok" if removed else "missing", "service_or_id": service_or_id}


def verify(service: str | None = None, all_services: bool = False) -> dict[str, Any]:
    services = build_services(prompt=True)
    targets = [service] if service else []
    if all_services:
        targets = [record.service for record in services.vault.list_credentials()]
    if not targets:
        return {"status": "error", "error": "Provide --service or --all"}
    results = [services.broker.verify_credential(target).model_dump(mode="json") for target in targets]
    return {"status": "ok", "results": results}


def broker_env(service: str, agent: str, ttl: int) -> dict[str, Any]:
    services = build_services(prompt=True)
    decision = services.broker.get_ephemeral_env(service=service, agent_id=agent, ttl=ttl)
    return decision.model_dump(mode="json")


def broker_list(agent: str) -> dict[str, Any]:
    services = build_services(prompt=True)
    return {
        "status": "ok",
        "agent": agent,
        "credentials": services.broker.list_available_credentials(agent),
    }


def audit(limit: int) -> dict[str, Any]:
    services = build_services(prompt=True)
    return {"status": "ok", "items": services.audit.list_recent(limit=limit)}


def generate_skill(agent: str | None = None, all_agents: bool = False) -> dict[str, Any]:
    services = build_services(prompt=True)
    generator = SkillGenerator(
        policy=services.policy,
        output_dir=services.settings.generated_skills_dir,
    )
    paths = generator.generate_all() if all_agents else [generator.generate_for_agent(agent or "igorek")]
    return {"status": "ok", "paths": [str(path) for path in paths]}
