import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core import opencode_go_catalog


class OpenCodeGoCatalogTest(unittest.TestCase):
    def test_go_profile_url_is_exact_and_excludes_confusing_origins(self):
        check = opencode_go_catalog.is_opencode_go_url
        self.assertTrue(check('https://opencode.ai/zen/go/v1'))
        for value in ('https://opencode.ai.attacker.invalid/zen/go/v1',
                      'https://opencode.ai:8443/zen/go/v1',
                      'https://opencode.ai/zen/go/v1?other=1',
                      'https://user@opencode.ai/zen/go/v1'):
            self.assertFalse(check(value))

    def test_desktop_worker_route_requires_go_profile_and_known_selected_model(self):
        from factory.core.worker import _desktop_opencode_go_route
        config = {'provider': 'custom', 'base_url': 'https://opencode.ai/zen/go/v1'}
        route = ('anthropic', 'https://opencode.ai/inference/go/anthropic/v1', 'Model')
        with patch.object(opencode_go_catalog, 'active_opencode_go_routes', return_value={'model': route}):
            self.assertEqual(_desktop_opencode_go_route(config, 'model', True), route[:2])
            self.assertEqual(_desktop_opencode_go_route(config, 'model', False), route[:2])
            self.assertIsNone(_desktop_opencode_go_route({**config, 'base_url': 'https://other.invalid/v1'}, 'model', True))
            with self.assertRaises(ValueError):
                _desktop_opencode_go_route(config, 'missing', True)

    def test_go_desktop_keeps_selected_model_instead_of_first_catalog_entry(self):
        from factory.core import worker
        with patch.object(worker, '_resolve_cc_backend_model', return_value='wrong-first-model') as resolver:
            selected = worker._effective_cc_backend_model(provider='custom', configured_model='default',
                task_metadata={'model_override': 'selected'}, base_url='https://opencode.ai/zen/go/v1')
            default = worker._effective_cc_backend_model(provider='custom', configured_model='default',
                task_metadata={}, base_url='https://opencode.ai/zen/go/v1')
        self.assertEqual(selected, 'selected')
        self.assertEqual(default, 'default')
        resolver.assert_not_called()

    def test_catalog_routes_use_official_endpoints_and_reject_untrusted_values(self):
        payload = {'opencode-go': {'npm': '@ai-sdk/openai-compatible',
            'api': 'https://opencode.ai/inference/go/openai/v1', 'models': {
                'chat': {'name': 'Chat'},
                'anthropic': {'provider': {'npm': '@ai-sdk/anthropic', 'api': 'https://opencode.ai/inference/go/anthropic/v1'}},
                'responses': {'provider': {'npm': '@ai-sdk/openai'}},
                'google': {'provider': {'npm': '@ai-sdk/google', 'api': 'https://opencode.ai/inference/go/google/v1beta'}},
                'bad': {'provider': {'api': 'https://attacker.invalid/v1'}},
                'malformed': {'provider': {'npm': []}},
            }}}
        routes = opencode_go_catalog.parse_opencode_go_routes(payload)
        self.assertEqual({key: value[0] for key, value in routes.items()}, {
            'chat': 'openai', 'anthropic': 'anthropic', 'responses': 'opencode-responses', 'google': 'google'})
        self.assertEqual(routes['chat'][2], 'Chat')

    def test_route_cache_returns_a_copy_and_recovers_from_fetch_failure(self):
        route = ('openai', 'https://opencode.ai/inference/go/openai/v1', 'Model')
        with (patch.object(opencode_go_catalog, '_routes_cached', {}),
              patch.object(opencode_go_catalog, '_routes_expire', 0.0),
              patch.object(opencode_go_catalog, 'fetch_opencode_go_routes', side_effect=[{'model': route}, {}]),
              patch.object(opencode_go_catalog.time, 'monotonic', side_effect=[1, 1, 4000, 4000])):
            result = opencode_go_catalog.get_opencode_go_routes()
            result.clear()
            self.assertEqual(opencode_go_catalog.get_opencode_go_routes(), {'model': route})

    def test_official_payload_is_cached_and_keeps_transport_metadata(self):
        payload_ids = ["deepseek-v4-pro", "qwen3.7-max", "minimax-m3", "hy3-preview"]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(os.environ, {"FACTORY_DATA_DIR": tmp}, clear=False),
                patch.object(opencode_go_catalog, "_fetch_model_ids", return_value=payload_ids),
            ):
                models = opencode_go_catalog.list_opencode_go_models()
                cached = json.loads((Path(tmp) / "cache" / "opencode_go_models.json").read_text(encoding="utf-8"))

        self.assertEqual([item.id for item in models], payload_ids)
        self.assertEqual(models[0].transport, "openai")
        self.assertEqual(models[1].transport, "anthropic")
        self.assertEqual(models[2].transport, "anthropic")
        self.assertIsNone(models[3].transport)
        self.assertEqual([item["id"] for item in cached["data"]], payload_ids)

    def test_cached_catalog_is_used_when_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache" / "opencode_go_models.json"
            cache.parent.mkdir(parents=True)
            cache.write_text(json.dumps({"data": [{"id": "qwen3.7-max"}]}), encoding="utf-8")
            with (
                patch.dict(os.environ, {"FACTORY_DATA_DIR": tmp}, clear=False),
                patch.object(opencode_go_catalog, "_fetch_model_ids") as fetch,
            ):
                models = opencode_go_catalog.list_opencode_go_models()

        fetch.assert_not_called()
        self.assertEqual([item.id for item in models], ["qwen3.7-max"])

    def test_failed_catalog_does_not_revive_static_fallback_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(os.environ, {"FACTORY_DATA_DIR": tmp}, clear=False),
                patch.object(opencode_go_catalog, "_fetch_model_ids", return_value=[]),
            ):
                models = opencode_go_catalog.list_opencode_go_models()

        self.assertEqual(models, [])

    def test_minimax_m3_uses_anthropic_transport(self):
        with patch.object(
            opencode_go_catalog,
            "list_opencode_go_models",
            return_value=[opencode_go_catalog.OpenCodeGoModel("minimax-m3", "anthropic")],
        ):
            self.assertEqual(opencode_go_catalog.opencode_go_model_transport("minimax-m3"), "anthropic")

    def test_recommendation_filters_transport_or_accepts_dual_transport_runtime(self):
        models = [
            opencode_go_catalog.OpenCodeGoModel("qwen3.7-max", "anthropic"),
            opencode_go_catalog.OpenCodeGoModel("deepseek-v4-pro", "openai"),
        ]
        with patch.object(opencode_go_catalog, "list_opencode_go_models", return_value=models):
            self.assertEqual(opencode_go_catalog.recommended_opencode_go_model("openai"), "deepseek-v4-pro")
            self.assertEqual(opencode_go_catalog.recommended_opencode_go_model("anthropic"), "qwen3.7-max")
            self.assertEqual(opencode_go_catalog.recommended_opencode_go_model("auto"), "deepseek-v4-pro")


if __name__ == "__main__":
    unittest.main()


def test_live_go_catalog_removal_wins_over_routing_metadata(monkeypatch):
    route = ('openai', 'https://opencode.ai/inference/go/openai/v1', 'Current model')
    monkeypatch.setattr(opencode_go_catalog, 'list_opencode_go_models', lambda: [
        opencode_go_catalog.OpenCodeGoModel('deepseek-v4.1-flash', 'openai')])
    monkeypatch.setattr(opencode_go_catalog, 'get_opencode_go_routes', lambda: {
        'deepseek-v4-flash': route, 'deepseek-v4.1-flash': route, 'another-retired-model': route})
    assert opencode_go_catalog.active_opencode_go_routes() == {'deepseek-v4.1-flash': route}


def test_failed_live_go_catalog_does_not_expose_routing_only_models(monkeypatch):
    monkeypatch.setattr(opencode_go_catalog, 'list_opencode_go_models', lambda: [])
    monkeypatch.setattr(opencode_go_catalog, 'get_opencode_go_routes', lambda: (_ for _ in ()).throw(AssertionError('No active models')))
    assert opencode_go_catalog.active_opencode_go_routes() == {}


def test_stale_go_cache_is_not_revived_when_provider_is_unavailable(tmp_path, monkeypatch):
    cache = tmp_path/'cache/opencode_go_models.json'
    cache.parent.mkdir()
    cache.write_text(json.dumps({'data': [{'id': 'retired-model'}]}))
    os.utime(cache, (0, 0))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(opencode_go_catalog, '_fetch_model_ids', lambda: [])
    assert opencode_go_catalog.list_opencode_go_models() == []
    assert opencode_go_catalog.list_opencode_go_models() == []


def test_confirmed_retired_deepseek_id_is_filtered_even_from_fresh_cache(tmp_path, monkeypatch):
    cache = tmp_path/'cache/opencode_go_models.json';cache.parent.mkdir()
    cache.write_text(json.dumps({'data': [{'id': 'deepseek-v4-flash'}, {'id': 'deepseek-v4-pro'}]}))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(opencode_go_catalog, '_fetch_model_ids', lambda: (_ for _ in ()).throw(AssertionError('Fresh cache')))
    assert [m.id for m in opencode_go_catalog.list_opencode_go_models()] == ['deepseek-v4-pro']


def test_legacy_deepseek_wire_id_changes_only_for_opencode_go():
    from factory.core import worker
    old = 'deepseek-v4-flash'
    args = {'provider': 'custom', 'configured_model': old, 'task_metadata': {}, 'base_url': 'https://opencode.ai/zen/go/v1'}
    assert worker._effective_cc_backend_model(**args) == 'deepseek-v4.1-flash'
    assert worker._effective_cc_backend_model(**{**args, 'task_metadata': {'model_override': old}}) == 'deepseek-v4.1-flash'
    assert worker._effective_cc_backend_model(**{**args, 'provider': 'openrouter', 'base_url': 'https://openrouter.ai/api/v1'}) == old
