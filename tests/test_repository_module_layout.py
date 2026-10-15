"""Packaged API modules and mutable Telegram data retain their own boundaries."""
from pathlib import Path


def test_api_prompt_assembler_is_importable_from_the_package():
    from factory.api_server_core.prompt_builder import build_system_prompt, build_tools_prompt
    assert 'fixture-extra-context' in build_system_prompt(extra_blocks=['fixture-extra-context'])
    assert 'Read' in build_tools_prompt([{'name': 'Read', 'description': 'Read a fixture'}])


def test_inference_policy_is_packaged_and_protected():
    from factory.api_server_core.file_policy import PROTECTED_POLICY_PATHS
    from factory.api_server_core.policy_engine import POLICY_CONFIG_PATH, load_policy_config
    assert POLICY_CONFIG_PATH.is_file()
    assert POLICY_CONFIG_PATH.resolve() in PROTECTED_POLICY_PATHS
    assert load_policy_config()['file_tools']['enforce_project_root'] is True


def test_emoji_registry_uses_runtime_data_even_before_directory_creation(tmp_path, monkeypatch):
    from factory.core import custom_emoji_registry, runtime
    data = tmp_path / 'not-created-yet'
    monkeypatch.setattr(runtime, 'get_data_root', lambda: str(data))
    assert custom_emoji_registry._default_registry_path() == data / 'custom_emoji_registry.json'
    assert not data.exists()
