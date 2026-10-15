"""Keep pytest imports and fixtures away from real user data and credentials."""
import os
from pathlib import Path
import tempfile


_TEST_HOME = tempfile.TemporaryDirectory(prefix="igorek-pytest-")
_ROOT = Path(_TEST_HOME.name)
for _name in list(os.environ):
    if _name.endswith(("_API_KEY", "_TOKEN", "_PASSWORD", "_CLIENT_SECRET", "_SECRET_ACCESS_KEY")):
        os.environ.pop(_name, None)
    elif _name.startswith(("FACTORY_EXTERNAL_", "FACTORY_TELEGRAM_")) or _name in {
        "FACTORY_WALLY_CREDENTIALS", "AGENTFACTORY_LEGACY_SKILLS_PATH", "FACTORY_DB_PATH",
        "IGOREK_VAULT_HOME", "IGOREK_VAULT_PASSPHRASE", "SSH_AUTH_SOCK", "IGOREK_APK_DELIVERY_CHAT_ID",
    }:
        os.environ.pop(_name, None)
for _key, _directory in {
    "HOME": "home", "USERPROFILE": "home", "IGOREK_FACTORY_HOME": "data",
    "FACTORY_DATA_DIR": "data", "FACTORY_ENV_FILES_DIR": "env",
    "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "share", "XDG_CACHE_HOME": "cache",
}.items():
    _path = _ROOT / _directory
    _path.mkdir(exist_ok=True)
    os.environ[_key] = str(_path)
os.environ["FACTORY_VAULT_ENABLED"] = "1"


def pytest_unconfigure(config):
    _TEST_HOME.cleanup()
