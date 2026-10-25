"""Offline MCP catalog, transport and opt-in billing contracts."""

import io
import json
from urllib import error

import pytest

from factory.mcp_servers import openrouter_server as s


def models():
    return [
        {
            "id": "zeta/large",
            "name": "Large",
            "canonical_slug": "zeta/large:stable",
            "description": "Vision fixture",
            "context_length": 32000,
            "created": 20,
            "pricing": {"prompt": "0.000002", "completion": "0.000004"},
            "architecture": {
                "modality": "text+image->text",
                "input_modalities": ["text", "image"],
            },
            "supported_parameters": ["tools"],
        },
        {
            "id": "alpha/small",
            "name": "Small",
            "description": "Fast fixture",
            "context_length": 8000,
            "created": 10,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"modality": "text->text", "input_modalities": ["text"]},
        },
    ]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_CHAT_SEND_ENABLED", raising=False)
    monkeypatch.setattr(s, "__file__", str(tmp_path / "factory/mcp_servers/server.py"))
    monkeypatch.setattr(s, "_MODELS_CACHE", None)
    monkeypatch.setattr(s, "_MODELS_CACHE_TS", 0)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected live HTTP request")

    monkeypatch.setattr(s.urllib_request, "build_opener", forbidden)
    return tmp_path


@pytest.fixture
def catalog(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return {"data": models()}

    monkeypatch.setattr(s, "_http_get", get)
    return calls


def test_catalog_is_cached_and_query_filters_do_not_mutate_original_data(catalog):
    initial = [m["id"] for m in s._models_list()["models"]]
    assert s._models_list(query="fast")["returned"] == 1
    assert s._models_list(sort="price")["models"][0]["id"] == "alpha/small"
    assert [m["id"] for m in s._models_list()["models"]] == initial
    assert len(catalog) == 1


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"query": "VISION"}, ["zeta/large"]),
        ({"modality": "image"}, ["zeta/large"]),
        ({"provider": "alpha"}, ["alpha/small"]),
        ({"family": "large"}, ["zeta/large"]),
        ({"min_context": 10000}, ["zeta/large"]),
        ({"max_prompt_price": 1}, ["alpha/small"]),
        ({"min_prompt_price": 1}, ["zeta/large"]),
        ({"sort": "price_desc"}, ["zeta/large", "alpha/small"]),
        ({"sort": "context"}, ["zeta/large", "alpha/small"]),
        ({"sort": "name"}, ["zeta/large", "alpha/small"]),
        ({"sort": "newest"}, ["zeta/large", "alpha/small"]),
        ({"limit": 1}, ["zeta/large"]),
        ({"limit": "bad"}, ["zeta/large", "alpha/small"]),
        ({"min_context": "bad"}, ["zeta/large", "alpha/small"]),
    ],
)
def test_catalog_filter_sort_and_limits_use_fixture_prices(fields, expected, catalog):
    result = s._models_list(**fields)
    assert [m["id"] for m in result["models"]] == expected
    assert result["returned"] == len(expected)


@pytest.mark.parametrize("identifier", ["zeta/large", "zeta/large:stable", "zeta/"])
def test_single_model_lookup_supports_catalog_identifiers(identifier, catalog):
    assert s._model_get(identifier)["id"] == "zeta/large"
    assert s._benchmarks(identifier)["model_id"] == "zeta/large"


def test_catalog_not_found_and_missing_arguments_are_explicit(catalog):
    assert "not found" in s._model_get("missing")["error"]
    assert "not found" in s._benchmarks("missing")["error"]
    for fn in [s._model_get, s._model_endpoints, s._generation_get, s._docs_search]:
        with pytest.raises(RuntimeError, match="required"):
            fn()
    assert s._benchmarks()["total_models"] == 2
    assert "error" in s._app_rankings()
    assert s._docs_search("fixture")["query"] == "fixture"


@pytest.mark.parametrize(
    "value,expected",
    [("0.000002", "$2.0000"), ("0", "$0.0000"), (None, None), ("bad", None)],
)
def test_price_formatting_uses_per_million_units(value, expected):
    assert s._price_per_m(value) == expected


def test_endpoints_providers_and_rankings_keep_declared_limits(monkeypatch, catalog):
    monkeypatch.setattr(
        s,
        "_http_get",
        lambda *a, **k: {
            "data": {
                "name": "Fixture",
                "endpoints": [
                    {
                        "provider_name": "Fixture provider",
                        "pricing": {"prompt": "0.000001"},
                    }
                ],
            }
        },
    )
    result = s._model_endpoints("author/model")
    assert (
        result["endpoint_count"] == 1
        and result["endpoints"][0]["pricing"]["prompt_per_m"] == "$1.0000"
    )
    monkeypatch.setattr(
        s, "_http_get", lambda *a, **k: {"data": [{"name": "a"}, {"name": "b"}]}
    )
    assert s._providers_list(limit=1)["returned"] == 1
    assert s._providers_list(limit="bad")["returned"] == 2


def test_credits_and_generation_require_a_key_before_transport(monkeypatch):
    assert "error" in s._credits_get() and "error" in s._generation_get("fixture")
    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-token")
    monkeypatch.setattr(
        s, "_http_get", lambda url, **k: calls.append(url) or {"value": 1}
    )
    assert s._credits_get()["credits"]["value"] == 1
    assert s._generation_get("id with space")["generation"] == {"value": 1}
    assert "id%20with%20space" in calls[-1]


def test_billable_chat_is_opt_in_and_fake_transport_keeps_exact_payload(monkeypatch):
    assert "disabled" in s._chat_send(model="fixture", message="Hello")["error"]
    monkeypatch.setenv("OPENROUTER_CHAT_SEND_ENABLED", "true")
    with pytest.raises(RuntimeError, match="model"):
        s._chat_send()
    with pytest.raises(RuntimeError, match="message"):
        s._chat_send(model="fixture")
    assert "not configured" in s._chat_send(model="fixture", message="Hello")["error"]
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-token")
    calls = []
    monkeypatch.setattr(
        s,
        "_http_post",
        lambda url, body, **k: calls.append((url, body, k)) or {"choices": []},
    )
    assert (
        s._chat_send(model="fixture", message="Привет", max_tokens=7)["model"]
        == "fixture"
    )
    assert calls[0][1] == {
        "model": "fixture",
        "messages": [{"role": "user", "content": "Привет"}],
        "max_tokens": 7,
    }


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("failure", ["http", "network", "json"])
def test_http_failures_preserve_status_or_transport_reason(
    monkeypatch, method, failure
):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"not-json"

    def open_request(*args, **kwargs):
        if failure == "http":
            raise error.HTTPError(
                "https://fixture.invalid",
                429,
                "fixture",
                {},
                io.BytesIO(b"fixture rate limit"),
            )
        if failure == "network":
            raise error.URLError("fixture offline")
        return Response()

    monkeypatch.setattr(
        s.urllib_request,
        "build_opener",
        lambda *a: type("Opener", (), {"open": staticmethod(open_request)})(),
    )
    with pytest.raises(
        RuntimeError,
        match={"http": "429", "network": "fixture offline", "json": "bad JSON"}[
            failure
        ],
    ):
        s._http_get("https://fixture.invalid") if method == "GET" else s._http_post(
            "https://fixture.invalid", {"fixture": 1}
        )


def test_http_success_serializes_unicode_and_auth_only_into_fake_request(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"ok":true}'

    def open_request(request, **options):
        calls.append((request, options))
        return Response()

    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setattr(
        s.urllib_request,
        "build_opener",
        lambda *a: type("Opener", (), {"open": staticmethod(open_request)})(),
    )
    assert s._http_get("https://fixture.invalid")["ok"]
    assert s._http_post("https://fixture.invalid", {"text": "Привет"})["ok"]
    assert json.loads(calls[-1][0].data) == {"text": "Привет"}
    assert calls[-1][0].get_header("Authorization") == "Bearer fixture-key"


def test_stdio_mcp_loop_handles_handshake_dispatch_errors_and_notifications(
    monkeypatch, capsys
):
    requests = [
        {"id": 1, "method": "initialize"},
        {"id": 2, "method": "tools/list"},
        {"method": "notifications/initialized"},
        {
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "fixture",
                "arguments": {"value": "Привет", "output_dir": "ignored"},
            },
        },
        {"id": 4, "method": "tools/call", "params": {"name": "unknown"}},
        {
            "id": 5,
            "method": "tools/call",
            "params": {"name": "fixture", "arguments": {"wrong": 1}},
        },
        {"id": 6, "method": "tools/call", "params": {"name": "failure"}},
        {"id": 7, "method": "unknown"},
    ]

    def fail():
        raise RuntimeError("fixture failed")

    monkeypatch.setattr(
        s, "_TOOLS_MAP", {"fixture": lambda value: {"value": value}, "failure": fail}
    )
    monkeypatch.setattr(
        s.sys,
        "stdin",
        io.StringIO("\ninvalid JSON\n" + "\n".join(json.dumps(r) for r in requests)),
    )
    s.main()
    replies = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6, 7]
    assert replies[0]["result"]["serverInfo"]["name"] == "OpenRouter"
    assert json.loads(replies[2]["result"]["content"][0]["text"]) == {"value": "Привет"}
    assert [r["error"]["code"] for r in replies[3:]] == [-32601, -32602, -32000, -32601]
