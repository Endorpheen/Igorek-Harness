import json
import unittest
from unittest import mock

from factory.core.runtime import (
    _clean_local_model_id,
    profile_display_model_name,
    resolve_local_model_display_name,
    resolve_local_model_id,
)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _fake_payload(model_id: str) -> dict:
    return {"object": "list", "data": [{"id": model_id}]}


class CleanLocalModelIdTest(unittest.TestCase):
    def test_path_basename_strips_gguf_and_shard_suffix(self):
        raw = (
            "/home/example/AI/FrankenMoE-CUDA/models/qwen38/UD-IQ3_XXS/"
            "Qwen3.8-Flash-Next-UD-IQ3_XXS-00001-of-00003.gguf"
        )
        self.assertEqual(
            _clean_local_model_id(raw), "Qwen3.8-Flash-Next-UD-IQ3_XXS"
        )

    def test_plain_alias_is_kept(self):
        self.assertEqual(_clean_local_model_id("ornith-1.5-35b"), "ornith-1.5-35b")

    def test_empty_returns_empty(self):
        self.assertEqual(_clean_local_model_id(""), "")


class ResolveLocalModelDisplayNameTest(unittest.TestCase):
    def test_request_id_keeps_full_path_and_normalizes_v1_once(self):
        raw = "/models/Nemotron-Bolt.gguf"
        for base in ("http://127.0.0.1:8081", "http://127.0.0.1:8081/v1/"):
            with self.subTest(base=base), mock.patch(
                "factory.core.runtime.urllib.request.urlopen",
                return_value=_FakeResponse(_fake_payload(raw)),
            ) as urlopen:
                self.assertEqual(resolve_local_model_id(base, "old-model"), raw)
                self.assertEqual(urlopen.call_args.args[0], "http://127.0.0.1:8081/v1/models")

    def test_reads_served_model_from_models_endpoint(self):
        with mock.patch(
            "factory.core.runtime.urllib.request.urlopen",
            return_value=_FakeResponse(_fake_payload("/models/Qwen3.8-Flash.gguf")),
        ):
            name = resolve_local_model_display_name(
                "http://127.0.0.1:8081/v1", "local"
            )
        self.assertEqual(name, "Qwen3.8-Flash")

    def test_falls_back_when_server_unreachable(self):
        with mock.patch(
            "factory.core.runtime.urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ):
            name = resolve_local_model_display_name(
                "http://127.0.0.1:8081/v1", "local"
            )
        self.assertEqual(name, "local")

    def test_uses_v1_models_for_bare_base_url(self):
        with mock.patch(
            "factory.core.runtime.urllib.request.urlopen",
            return_value=_FakeResponse(_fake_payload("alias-model")),
        ) as urlopen:
            resolve_local_model_display_name("http://127.0.0.1:8081", "local")
        called_url = urlopen.call_args[0][0]
        self.assertEqual(called_url, "http://127.0.0.1:8081/v1/models")


class ProfileDisplayModelNameTest(unittest.TestCase):
    def test_localhost_profile_uses_served_model(self):
        config = {
            "backend": "cc-backend",
            "provider": "local",
            "model": "local",
            "base_url": "http://127.0.0.1:8081/v1",
        }
        with mock.patch(
            "factory.core.runtime.urllib.request.urlopen",
            return_value=_FakeResponse(_fake_payload("qwen3.8-flash-next-iq3")),
        ):
            name = profile_display_model_name(config)
        self.assertEqual(name, "qwen3.8-flash-next-iq3")

    def test_remote_profile_keeps_configured_model(self):
        config = {
            "backend": "cc-backend",
            "provider": "glm",
            "model": "glm-5.3-flash",
            "base_url": "https://api.z.ai/api/anthropic",
        }
        with mock.patch(
            "factory.core.runtime.urllib.request.urlopen"
        ) as urlopen:
            name = profile_display_model_name(config)
        urlopen.assert_not_called()
        self.assertEqual(name, "glm-5.3-flash")


if __name__ == "__main__":
    unittest.main()
