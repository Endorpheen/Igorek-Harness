"""Malformed optional provider caches must not prevent a model task from starting."""
import json

import pytest

from factory.core import cc_backend_executor as executor
from factory.core import opencode_models, openrouter_pricing


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(opencode_models, 'get_context_window', lambda *a, **k: None)
    monkeypatch.setattr(openrouter_pricing, 'get_openrouter_context_window', lambda *a, **k: None)
    path = tmp_path / '.cache/opencode/models.json';path.parent.mkdir(parents=True)
    def write(payload):path.write_text(json.dumps(payload));return path
    return write


@pytest.mark.parametrize('provider', ['opencode-go', 'opencode', 'openrouter', 'minimax', 'glm'])
@pytest.mark.parametrize('value', [65536, '65536'])
def test_context_cache_supports_each_known_provider_and_numeric_strings(cache, provider, value):
    cache({provider: {'models': {'fixture': {'limit': {'context': value}}}}})
    assert executor._context_window_from_models_cache('fixture') == 65536


@pytest.mark.parametrize('payload', [
    [], None, 4,
    {'opencode': {'models': []}}, {'opencode': {'models': None}},
    {'opencode': {'models': 'invalid'}},
    {'opencode': {'models': {'fixture': {'limit': []}}}},
    {'opencode': {'models': {'fixture': {'limit': 'invalid'}}}},
    {'opencode': {'models': {'fixture': None}}},
])
def test_invalid_optional_cache_shape_has_no_context(cache, payload):
    cache(payload)
    assert executor._context_window_from_models_cache('fixture') is None


@pytest.mark.parametrize('value', [0, -1, False, None, 'bad'])
def test_invalid_context_limit_is_not_used(cache, value):
    cache({'opencode': {'models': {'fixture': {'limit': {'context': value}}}}})
    assert executor._context_window_from_models_cache('fixture') is None


def test_invalid_provider_does_not_hide_later_valid_provider(cache):
    cache({'opencode-go': [], 'opencode': {'models': {'other': {'limit': {'context': 1}}}}, 'glm': {'models': {'fixture': {'limit': {'context': 131072}}}}})
    assert executor._context_window_from_models_cache('fixture') == 131072


def test_missing_and_corrupt_cache_files_are_optional(cache):
    assert executor._context_window_from_models_cache('fixture') is None
    path = cache({});path.write_text('{not-json')
    assert executor._context_window_from_models_cache('fixture') is None
    path.unlink();path.mkdir()
    assert executor._context_window_from_models_cache('fixture') is None


def test_primary_registry_value_precedes_fallback_file(cache, monkeypatch):
    cache({'opencode': {'models': {'fixture': {'limit': {'context': 1}}}}})
    monkeypatch.setattr(opencode_models, 'get_context_window', lambda *a, **k: 262144)
    monkeypatch.setattr(openrouter_pricing, 'get_openrouter_context_window', lambda *a, **k: pytest.fail('Unneeded fallback'))
    assert executor._context_window_from_models_cache('fixture') == 262144


def test_registry_failure_still_uses_other_provider_metadata(cache, monkeypatch):
    def broken(*a, **k):raise RuntimeError('fixture registry unavailable')
    monkeypatch.setattr(opencode_models, 'get_context_window', broken)
    monkeypatch.setattr(openrouter_pricing, 'get_openrouter_context_window', lambda *a, **k: 131072)
    assert executor._context_window_from_models_cache('fixture') == 131072


@pytest.mark.parametrize('models', [None, [], 'invalid', {'fixture': {'limit': []}}])
def test_malformed_earlier_provider_keeps_later_provider_available(cache, models):
    cache({'opencode-go': {'models': models}, 'glm': {'models': {'fixture': {'limit': {'context': 65536}}}}})
    assert executor._context_window_from_models_cache('fixture') == 65536
