from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

try:
    from factory.core.runtime import get_data_root
except ImportError:  # pragma: no cover - direct script fallback
    from runtime import get_data_root


def _default_runtime_home() -> Path:
    override = os.environ.get("IGOREK_VAULT_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return get_data_root() / "vault"


class AppSettings(BaseModel):
    app_name: str = "igorek-vault"
    runtime_home: Path = Field(default_factory=_default_runtime_home)
    policy_path: Path | None = Field(
        default_factory=lambda: (
            Path(os.environ["IGOREK_VAULT_POLICY"]).expanduser()
            if os.environ.get("IGOREK_VAULT_POLICY")
            else None
        )
    )
    db_filename: str = "vault.db"
    ignore_filename: str = "scan.ignore"
    salt_filename: str = "master_key_salt.bin"
    default_scan_roots: list[Path] = Field(
        default_factory=lambda: [
            get_data_root(),
            Path("~/.bashrc").expanduser(),
            Path("~/.zshrc").expanduser(),
            Path("~/.profile").expanduser(),
        ]
    )

    @property
    def db_path(self) -> Path:
        return self.runtime_home / self.db_filename

    @property
    def effective_policy_path(self) -> Path:
        return self.policy_path or (self.runtime_home / "policy.yaml")

    @property
    def ignore_path(self) -> Path:
        return self.runtime_home / self.ignore_filename

    @property
    def salt_path(self) -> Path:
        return self.runtime_home / self.salt_filename

    @property
    def generated_skills_dir(self) -> Path:
        return self.runtime_home / "generated-skills"

    def ensure_runtime_layout(self) -> None:
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        self.generated_skills_dir.mkdir(parents=True, exist_ok=True)
        self._secure_directory(self.runtime_home)
        self._secure_directory(self.generated_skills_dir)

    def secure_file(self, path: Path, mode: int = 0o600) -> None:
        if path.exists():
            try:
                os.chmod(path, mode)
            except OSError:
                pass

    def _secure_directory(self, path: Path) -> None:
        if path.exists():
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass


def get_settings() -> AppSettings:
    settings = AppSettings()
    settings.ensure_runtime_layout()
    return settings
